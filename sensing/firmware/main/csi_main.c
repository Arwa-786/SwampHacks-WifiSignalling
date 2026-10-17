/*
 * WiFind CSI link: one ESP32 sends packets, the other measures how the radio
 * channel looks for every packet (Channel State Information, CSI) and prints it
 * over USB serial for the Python code in sensing/host.
 *
 *   TX board (CSI_ROLE_TX=1): broadcasts a small ESP-NOW packet every 10 ms
 *                             (100 Hz) from a fixed, made-up MAC address.
 *   RX board (CSI_ROLE_TX=0): keeps only packets from that MAC and prints one
 *                             CSI line per packet.
 *
 * Why ESP-NOW and not a router: we don't depend on venue Wi-Fi, the channel is
 * fixed, and we control the packet rate. Same approach as espressif/esp-csi
 * "get-started" examples.
 *
 * Serial output (921600 baud), one line each:
 *   BOOT,<role>,<mac>,<channel>          once at startup
 *   CSI,<seq>,<rssi>,<noise>,<ts_us>,<len>,<hex>
 *       seq    802.11 sequence number (gaps = lost packets)
 *       rssi   signal strength in dBm
 *       noise  noise floor in dBm
 *       ts_us  ESP32 receive timestamp in microseconds
 *       len    number of CSI bytes
 *       hex    CSI bytes as hex, 2 chars per byte. Bytes come in pairs
 *              (imaginary, real) per subcarrier, each a signed int8.
 *              Hex instead of decimal keeps the line short enough for 100 Hz.
 *   STAT,<role>,<packets_last_second>,<dropped_last_second>   once per second
 */

#include <inttypes.h>
#include <stdio.h>
#include <string.h>

#include "esp_event.h"
#include "esp_mac.h"
#include "esp_netif.h"
#include "esp_now.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "nvs_flash.h"

#ifndef CSI_ROLE_TX
#error "Build with -DCSI_ROLE_TX=1 (sender) or -DCSI_ROLE_TX=0 (receiver); see platformio.ini"
#endif

/* Both boards must agree on these. Channel 11 is often less crowded than 1/6,
 * but if the venue is noisy on 11, try 13 or 3 (change here and reflash both). */
#define CSI_CHANNEL 11
#define SEND_INTERVAL_MS 10

/* Locally administered MAC (first byte 0x1a) so it can't clash with a real
 * device. The receiver ignores CSI from every other transmitter in the room. */
static const uint8_t TX_MAC[6] = {0x1a, 0x00, 0x00, 0x00, 0x00, 0x01};

/* CSI_HTLTF=1 (env rx_both) also captures the HT-LTF block, appended after
 * the LLTF one: 2 x 64 subcarriers x 2 bytes. ESPectre uses HT-LTF on these
 * chips; this build lets us compare both on the same packets. */
#ifndef CSI_HTLTF
#define CSI_HTLTF 0
#endif

/* ESP32 / ESP32-S3 LLTF CSI = 64 subcarriers x 2 bytes (x2 with HT-LTF). */
#define MAX_CSI_BYTES (CSI_HTLTF ? 256 : 128)

static volatile uint32_t s_packets = 0;
static volatile uint32_t s_dropped = 0;

static void wifi_init(void)
{
    ESP_ERROR_CHECK(nvs_flash_init());
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));
    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
#if CSI_ROLE_TX
    ESP_ERROR_CHECK(esp_wifi_set_mac(WIFI_IF_STA, TX_MAC));
#endif
    ESP_ERROR_CHECK(esp_wifi_set_bandwidth(WIFI_IF_STA, WIFI_BW20));
    ESP_ERROR_CHECK(esp_wifi_start());
    /* Power save would make the radio sleep and miss packets. */
    ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));
    ESP_ERROR_CHECK(esp_wifi_set_channel(CSI_CHANNEL, WIFI_SECOND_CHAN_NONE));
}

static void print_boot_line(const char *role)
{
    uint8_t mac[6];
    esp_wifi_get_mac(WIFI_IF_STA, mac);
    printf("BOOT,%s," MACSTR ",%d\n", role, MAC2STR(mac), CSI_CHANNEL);
    fflush(stdout);
}

#if CSI_ROLE_TX

static const uint8_t BROADCAST_MAC[6] = {0xff, 0xff, 0xff, 0xff, 0xff, 0xff};

static void sender_task(void *arg)
{
    /* Payload content doesn't matter for sensing; the CSI comes from the
     * packet's preamble. A tag makes it recognisable in a sniffer. */
    uint8_t payload[16] = "WIFIND";
    uint32_t counter = 0;
    TickType_t last_wake = xTaskGetTickCount();
    for (;;) {
        memcpy(&payload[8], &counter, sizeof(counter));
        if (esp_now_send(BROADCAST_MAC, payload, sizeof(payload)) == ESP_OK) {
            s_packets++;
        } else {
            s_dropped++;
        }
        counter++;
        vTaskDelayUntil(&last_wake, pdMS_TO_TICKS(SEND_INTERVAL_MS));
    }
}

static void start_role(void)
{
    ESP_ERROR_CHECK(esp_now_init());
    esp_now_peer_info_t peer = {
        .channel = CSI_CHANNEL,
        .ifidx = WIFI_IF_STA,
        .encrypt = false,
    };
    memcpy(peer.peer_addr, BROADCAST_MAC, 6);
    ESP_ERROR_CHECK(esp_now_add_peer(&peer));

    /* ESP-NOW defaults to 1 Mbps 802.11b, which has no OFDM training field,
     * so the receiver would get no CSI. Force an 802.11n (OFDM) rate. */
    esp_now_rate_config_t rate = {
        .phymode = WIFI_PHY_MODE_HT20,
        .rate = WIFI_PHY_RATE_MCS0_LGI,
    };
    ESP_ERROR_CHECK(esp_now_set_peer_rate_config(BROADCAST_MAC, &rate));

    print_boot_line("TX");
    xTaskCreate(sender_task, "sender", 4096, NULL, 5, NULL);
}

#else /* receiver */

typedef struct {
    uint16_t seq;
    int8_t rssi;
    int8_t noise_floor;
    uint32_t timestamp_us;
    uint16_t len;
    int8_t buf[MAX_CSI_BYTES];
} csi_record_t;

static QueueHandle_t s_csi_queue;

/* Runs inside the Wi-Fi driver task, so it must be quick: copy and hand off.
 * Printing here (~3 ms per line) would stall the radio. */
static void csi_rx_cb(void *ctx, wifi_csi_info_t *info)
{
    if (!info || !info->buf || memcmp(info->mac, TX_MAC, 6) != 0) {
        return;
    }
    csi_record_t rec;
    rec.seq = info->rx_seq;
    rec.rssi = info->rx_ctrl.rssi;
    rec.noise_floor = info->rx_ctrl.noise_floor;
    rec.timestamp_us = info->rx_ctrl.timestamp;
    rec.len = info->len > MAX_CSI_BYTES ? MAX_CSI_BYTES : info->len;
    memcpy(rec.buf, info->buf, rec.len);
    if (xQueueSend(s_csi_queue, &rec, 0) != pdTRUE) {
        s_dropped++; /* printer can't keep up; Python will see a seq gap */
    }
}

static void printer_task(void *arg)
{
    static const char HEX[] = "0123456789abcdef";
    static char line[64 + 2 * MAX_CSI_BYTES + 2];
    csi_record_t rec;
    for (;;) {
        if (xQueueReceive(s_csi_queue, &rec, portMAX_DELAY) != pdTRUE) {
            continue;
        }
        int n = snprintf(line, sizeof(line), "CSI,%u,%d,%d,%" PRIu32 ",%u,",
                         rec.seq, rec.rssi, rec.noise_floor, rec.timestamp_us, rec.len);
        for (int i = 0; i < rec.len; i++) {
            uint8_t b = (uint8_t)rec.buf[i];
            line[n++] = HEX[b >> 4];
            line[n++] = HEX[b & 0x0f];
        }
        line[n++] = '\n';
        fwrite(line, 1, n, stdout);
        s_packets++;
    }
}

static void start_role(void)
{
    s_csi_queue = xQueueCreate(32, sizeof(csi_record_t));

    /* Promiscuous mode lets the driver hand us CSI for packets that aren't
     * addressed to this board. */
    ESP_ERROR_CHECK(esp_wifi_set_promiscuous(true));

    wifi_csi_config_t csi_cfg = {
        .lltf_en = true,           /* legacy LTF: 64 subcarriers, present in every OFDM packet */
        .htltf_en = CSI_HTLTF,     /* second block; only in the rx_both build */
        .stbc_htltf2_en = false,
        .ltf_merge_en = !CSI_HTLTF, /* off when comparing, so HT-LTF stays its own measurement */
        .channel_filter_en = false, /* keep subcarriers independent (no smoothing) */
        .manu_scale = false,
        .shift = 0,
    };
    ESP_ERROR_CHECK(esp_wifi_set_csi_config(&csi_cfg));
    ESP_ERROR_CHECK(esp_wifi_set_csi_rx_cb(csi_rx_cb, NULL));
    ESP_ERROR_CHECK(esp_wifi_set_csi(true));

    print_boot_line("RX");
    xTaskCreate(printer_task, "printer", 4096, NULL, 4, NULL);
}

#endif /* CSI_ROLE_TX */

void app_main(void)
{
    wifi_init();
    start_role();

    const char *role = CSI_ROLE_TX ? "TX" : "RX";
    for (;;) {
        vTaskDelay(pdMS_TO_TICKS(1000));
        printf("STAT,%s,%" PRIu32 ",%" PRIu32 "\n", role, s_packets, s_dropped);
        fflush(stdout);
        s_packets = 0;
        s_dropped = 0;
    }
}
