# Wi-Fi sensing (ESP32 CSI)

Two ESP32 boards: **TX** = classic ESP32-WROOM-32, **RX** = ESP32-S3 (either
role can use either chip: swap the `board` lines in `firmware/platformio.ini`).
**TX** broadcasts 100 packets/s; **RX** measures the
channel (CSI) for every packet and streams it to the laptop over USB. Python
turns that into EMPTY / STILL / MOVING and a breathing rate.

```
firmware/   ESP-IDF code (PlatformIO). One file, two roles: env "tx" and env "rx"
host/       Python: check_link, live_motion, go_no_go, breathing, record + tests
```

## 0. One-time setup

```bash
# PlatformIO CLI (skip if `pio` already works, e.g. from the VS Code extension)
pip install platformio            # or use ~/.platformio/penv/bin/pio
cd sensing/host && pip install -r requirements.txt && python -m pytest -q
```

Plug a board in and run `ls /dev/cu.*`. The ESP32-S3 shows up as
`/dev/cu.usbmodemXXXX`; the WROOM (CP2102/CH340 chip) as `/dev/cu.usbserial-XXXX`
(or `/dev/cu.SLAB_USBtoUART`). macOS first asks "Allow accessory to connect?":
click **Allow**, or the board stays invisible. Light on but nothing new appears ->
the cable is charge-only, use another one. Label the boards **TX** and **RX** with tape.

## 1. Flash and check the boards work

```bash
cd sensing/firmware
pio run -e tx -t upload --upload-port /dev/cu.usbserial-AAAA   # WROOM, labelled TX
pio run -e rx -t upload --upload-port /dev/cu.usbmodemBBBB     # S3, labelled RX
```

If upload hangs at `Connecting...`, hold the **BOOT** button until it starts writing.

Power TX from any USB port/charger, keep RX plugged into the laptop:

```bash
cd sensing/host
python check_link.py
```

Expect `PASS` with ~100 packets/s, RSSI between -30 and -70 dBm and 51/51
subcarriers. The script prints what to fix otherwise. To see the raw lines:
`pio device monitor -e rx --port /dev/cu.usbmodemBBBB` (you should see
`BOOT,RX,...` then a stream of `CSI,...` and one `STAT,RX,<pkts>,<drops>` per second).

> **Build fails with `git-data/head-ref ... No such file`?** That happens when a
> folder above the project (e.g. your home folder) is an empty git repo.
> Run builds as `GIT_CEILING_DIRECTORIES=$HOME pio run ...`.

## 2. MVP test: motion

```bash
python live_motion.py               # calibrates on the empty spot, then a live meter
python go_no_go.py --trials 10      # the go/no-go test from CLAUDE.md, scored
python record.py venue_empty.log --seconds 60 --note "corner table, empty"
python live_motion.py --replay venue_empty.log   # re-run anything on a recording
```

Too many false `MOVING`: `--margin 1.5`. Misses motion: `--margin 0.8`.
MOVING needs both a big enough change *and* a smooth one (the `smooth` number
on screen, > 0.5): bodies move smoothly, radio noise jumps randomly.

## 3. Breathing (and the heart-rate question)

```bash
python breathing.py                 # breaths/min every 2 s, 30 s window
python breathing.py --band heart    # EXPERIMENTAL
```

Check it against counting your own breaths for 30 s. Heart rate from one
ESP32 antenna is expected to fail (a heartbeat moves the chest ~0.1-0.5 mm,
breathing ~5 mm). `--band heart` exists so we can measure that against a
MAX30102 or phone pulse app. If a heart number is needed in the demo, take it
from the MAX30102 or the 60 GHz MR60BHA2 radar, and say so to judges.

## Where to put and point the boards

The ESP32 antenna is the wavy copper trace at the end of the module (the end
away from the USB port). Wi-Fi sensing works best on the **straight line
between the two antennas** and in a fat, cigar-shaped zone around it (the
first Fresnel zone, ~20 cm radius at the middle for a 1.5 m link).

```
  side view                                  top view

  TX ][                          ][ RX       TX ]=====( person )=====[ RX
     ||   <------ 1-1.5 m ------> ||             \_____ sensing zone ____/
  ~~~~~~~ box/cardboard, not metal ~~~~~~
```

- **Distance:** start at 1.5 m apart (1-2 m works). Closer than ~30 cm saturates the receiver.
- **Height:** both at the same height, about the height of the hidden person's
  chest when crouched/sitting (roughly 40-70 cm). Prop them on cardboard boxes.
- **Orientation:** stand both boards **upright, antenna end up, flat sides facing
  each other**. What matters most is that both are oriented the same way
  (same polarization); don't have one vertical and one horizontal.
- **Person:** in the middle of the line between them. Their body should cut the line.
- **Keep clear:** no metal (table legs, laptop, phone) right next to the antennas,
  no hands or cables over the antenna end. Put the boards on cardboard, not
  directly on a metal table.
- **Crowd:** put the link along a wall/corner so the audience walks *beside*
  the zone, not through it; people crossing the line will look like motion.
- **Tape them down.** Any move of a board = recalibrate.

For **breathing**, sit beside the line (about 20-40 cm off it) with the chest
facing the line, not with the back to it. Breathing moves the chest
front-to-back; that displacement changes the signal most when it pushes across
the sensing zone. Breathing sensing has "blind spots": if the reading is weak,
shift 5-10 cm closer or farther and try again.

If you can't decide, run `live_motion.py` and try placements: the best one is
where the empty-room motion baseline is low and a small arm wave jumps far past
the `|` threshold.
