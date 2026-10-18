# WiFind: the swamp can feel you move

*Draft for Devpost. Every number below was measured on our own recordings; the
conditions are stated next to it. Update the numbers after the booth sessions.*

## Inspiration
Wi-Fi signals bounce off people. Research labs (MIT's Wi-Vi, RF-Pose; Gu et al.'s
WiFi breathing monitor) have shown that those reflections reveal movement and even
breathing, but with special radios or lab Wi-Fi cards. We wanted to know how much of
that a $15 set of ESP32 boards can do, and to make it something people *play*.

## What it does
- **Red Light, Green Light, judged by Wi-Fi.** A real person hides behind a curtain.
  No camera points at them: three ESP32 receivers listen to a fourth board's Wi-Fi
  packets, and the way the signal changes tells whether the hider moved. Their
  avatar runs in a **Roblox** game; move on red and the Wi-Fi catches you.
- **A live swamp, in 16-bit pixel art.** The judges' screen shows what the Wi-Fi
  feels: each receiver is a fishing bobber that dips with movement, the transmitter
  is a lantern, and a gator watches the water on red light.
- **Rescue Watch.** The same sensing, reframed for a fire: smoke blinds cameras but
  not Wi-Fi. A burning-cabin view shows whether someone is in the room, whether
  they're moving, whether they're breathing, and a rescue priority.
- **An AI host.** After each round a gator (DeepSeek on Vultr, with tool calls for
  the round's facts) says one line about what happened, checked so it can't invent
  numbers.

## How we built it
- **Hardware:** 4 ESP32 boards (1 transmitter on a battery, 3 receivers on the
  laptop), sending ESP-NOW packets 100 times a second on a fixed channel. Our own
  firmware prints each packet's Channel State Information (CSI).
- **Sensing (Python):** per-board features every 0.1 s (motion size, smoothness,
  presence, RSSI, all relative to a scan of the empty room), a small neural network
  per task trained on labelled recordings, and a frequency analysis for breathing.
- **Labels without a camera:** behind a curtain a camera can't see, so our recorders
  *speak a script* ("freeze", "move", "walk away") and label the data with what they
  asked for. For breathing we used a phone on the belly (phyphox) as ground truth,
  as Gu et al. used an accelerometer.
- **Roblox (Luau):** the game runs the booth step by step (step out, room scan,
  hide, 3-2-1, lights, results card) and drives the hider's avatar from the Wi-Fi.
- **Vultr:** a small server is the public address for Roblox and the judges'
  pages; the laptop connects *out* to it, so no tunnel. Vultr Serverless Inference
  runs the gator's lines and its voice.
- **Tiger Data (TimescaleDB):** every reading, recording, labelled window and test
  result, so every number on this page can be traced back to the data.

## Measured results
| What | Result | Conditions |
|---|---|---|
| Moving vs still vs empty (motion model) | **96.6%** accuracy; 100% of movement caught; **0 false eliminations** | 2 sessions, 1 person, standing 1.5 m, no curtain; each session tested by a model trained on the other |
| Same, old hand-tuned rules | 48% | same windows |
| Pretrained Wi-Fi model (CSI-BERT2) + our head | 54.9% | same test |
| Moves caught behind a curtain | **100%** of move turns, within ~3 s | live voice-guided test, transmitter behind the hider |
| Breathing person vs empty room | **100% found, 0% empty-room false alarms** | 2 sessions behind the curtain (paced + natural), each tested on the other |
| Same, old frequency rule | 19-20% found | same windows |
| Breathing rate vs phone on the belly | **2.3 breaths/min** median error (41% within ±2) | 3 min of natural breathing behind the curtain |

## Challenges
- **Freezing isn't silent.** With the transmitter 50 cm behind the hider, standing
  still (sway, breathing) read as movement. Placement matters as much as the model,
  so we record training data in the real booth layout.
- **The most sensitive receiver changes.** One board tracked breathing within ~2/min
  while the others didn't; Gu et al. report the same. Choosing it is a learning problem.
- **Slow body sway masked breathing** until we limited the rate to 12-36 per minute.

## What's next
Train on more people and positions; learn which receiver to trust for breathing;
fall detection for at-home safety without cameras; two hiders judged separately
(our zone experiment is ready to record).

## Honest limits
One person and a handful of sessions so far; ESP32s are not lab radios; Wi-Fi heart
rate is out of reach for us; Rescue Watch is a prototype, and a quiet signal does
not prove an empty room.

## Built with
ESP32, ESP-NOW, Python, NumPy/SciPy, FastAPI, Roblox Studio (Luau), Vultr (cloud
server + Serverless Inference), Tiger Data / TimescaleDB, phyphox, HTML canvas.

## Research we built on
MIT Wi-Vi (2013), RF-Pose (2018); Gu, Zhang, Liu, Ren, "WiFi-based Real-time Breathing
and Heart Rate Monitoring during Sleep" (2019); Espressif ESP-CSI; CSI-BERT2 (Zhao et
al., 2025); Awesome-WiFi-CSI-Sensing.
