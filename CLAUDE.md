# Project context: Wi-Fi sensing game for SwampHacks

This file is read by Claude Code. It holds the decisions made while planning this project.
Treat it as the plan of record; update it as decisions change.

## The idea in one line
A real person hidden behind a curtain plays Red Light, Green Light, and **Wi-Fi signals** (ESP32 CSI)
decide whether they moved. The same sensing powers a **Disaster mode** (the hidden person is a
"trapped survivor" the system finds) and a **Guardian mode** (fall detection at home without cameras).
A **Roblox** world mirrors the real person as an avatar, and online players can call "red light".

Pitch line: "Same Wi-Fi, three uses: a game, a disaster rescue, and keeping grandparents safe at home
without cameras. If Wi-Fi can catch you holding your breath behind a curtain, it can find a survivor
behind a wall after a hurricane."

## Why this project (the goal)
Beat past winners like Farsight (rover + VR + Gaussian splatting) and Permanence (3D VLM + object
tracking + AR "x-ray"). What made them win: an "impossible" moment visible in a 3-minute demo, recent
research, real hardware, measured numbers, and judges taking part. Judges score: technical difficulty,
usefulness, wow/creativity, completeness (works live), presentation. Line count does not matter.

## Hardware we have / plan to buy
- ESP32 dev boards (classic ESP32, e.g. 3-pack ESP-WROOM-32 with CP2102). Classic ESP32 supports CSI.
  Start with 2 (one TX, one RX). 4-5 ESP32-S3 boards only if we attempt pose/location.
- MG90S servos, motors (one becomes the booth fan), wires, sensors. **No 3D printer** (use cardboard/foam board).
- Laptop does all processing. Phones available. Speaker. LED strip.
- Optional: MAX30102 fingertip pulse sensor (~$5) for ground truth; MQ-2 / ENS160 gas sensors;
  vibration motor; small travel router (do not rely on venue Wi-Fi); phone hotspot.
- **Plan B sensors:** HLK-LD2410 24 GHz radar (~$5-10, motion + still presence),
  Seeed MR60BHA2 60 GHz radar (~$30, breathing + heart rate).

## The three modes
### 1. Play mode (pulls the crowd in)
- **Red Light, Green Light** (main game): move during green fills a progress bar; any motion on red
  eliminates. Leaderboard (name/nickname, survival time, stillness score). AI voice callouts
  ("You're holding your breath. Nice try.").
- **Wi-Fi Heist**: grab the gator plushie without tripping the invisible Wi-Fi "beam"; on-screen
  sensitivity meter. Uses only motion detection, so it's the most reliable game.
- **Zen Master** (stretch, needs breathing): calmest, slowest steady breathing wins.

### 2. Disaster mode (the surprise twist, mostly physical + dashboard)
- After a game round: "Now you're trapped." A disaster hits.
- **Booth 4D effects** (cheap, big impact): fan blows wind (hurricane), LEDs flicker (lightning /
  power outage), speaker plays thunder, rumble, sirens; optional vibration motor shakes the "rubble".
- The **web dashboard** finds the hidden person: "Survivor detected, breathing, not moving, Priority 1"
  (+ gas warning if the gas sensor is built).
- Roblox here is limited to optional visuals (see Roblox scope).

### 3. Guardian mode (the real-world use)
- **Fall detection, rule-based first** (no ML needed): big sudden motion spike -> stillness for several
  seconds -> person still present / breathing => "Person fell, not moving." Show a mock alert to family.
  A trained classifier can come later if time allows.
- Demo falls: a teammate falls onto a mattress/crash pad. **Never ask a judge to fall.**
- Privacy angle: works without cameras in the home.

## Roblox scope (decided: keep it simple)
- **In scope:** the real person's avatar moves when they move and freezes/gets knocked out based on
  Wi-Fi; Roblox players can press "red light" (triggers the booth sound/LEDs, optionally a servo).
- **Optional stretch:** simple disaster visuals in Roblox (camera shake, falling parts, rising water)
  timed with Disaster mode.
- **Cut / too difficult for now:** a full Roblox rescue operation (multiplayer digging, rescue
  gameplay). Rescue lives on the web dashboard instead.
- Original art only; do not copy the Squid Game doll or other copyrighted assets.

## The cue problem (important)
The hidden player cannot see the screen or anything on the audience side, so "red light / green light"
must reach them inside the hiding spot:
1. **Speaker** announcing green/red + sound effects (primary; also carries the AI gator voice).
2. **LED strip on the player's side** of the curtain (green/red flood; glow is visible to the audience too).
3. Optional: small tablet/old phone inside the booth showing red/green or their Roblox avatar.
4. Optional: vibration motor the player holds.
A physical servo gator is optional. If built, place it inside the booth facing the player (the
audience sees it via webcam or its Roblox twin). Cut it first if short on time.

## Feature list (locked scope)
| Priority | Feature | Est. effort (person-hours) |
|---|---|---|
| Core | Wi-Fi motion detection + calibration | 4-8 |
| Core | Red Light/Green Light + speaker/LED cue | 6-10 |
| Core | Wi-Fi Heist | 2-4 |
| Core | Live "Wi-Fi vision" waterfall screen (proves it's real) | 2-4 |
| Surprise | Roblox avatar moves/freezes; Roblox players trigger red light | 6-10 |
| Surprise | Booth 4D disaster effects (fan, lightning LEDs, thunder) | 2-4 |
| Stretch 1 | Fall detection (rule-based) + Guardian/Rescue dashboard | 7-14 |
| Stretch 1 | Breathing (breath-holding callout, Zen Master) | 6-12 |
| Stretch 2 | "How did it catch me?" replay; accuracy scoreboard (Wi-Fi vs hidden camera); AI gator trash talk | 8-14 |
| Stretch 2 | Roblox disaster visuals | 6-12 |
| Stretch 3 | Gas sensor in Disaster mode; QR "see your round" page (no names/emails stored) | 5-9 |
| Cut | Roblox rescue operation, Wi-Fi heart rate, Hide and Seek with location, full skeleton pose, 3D printed parts | - |

Core + Surprise is ~22-40 person-hours. With a team of 3-4 (~110 person-hours budget), Stretch 1 and
some of Stretch 2 are realistic. Always keep one working demo; cut from the bottom up.

## Demo flow (about 3 minutes)
1. Play: judge plays Red Light, Green Light behind the curtain, gets caught; replay shows the signal spike.
2. Twist: "Now you're trapped." Fan, lightning LEDs, thunder.
3. Rescue: dashboard shows "Survivor detected, breathing, not moving."
4. Real use: Guardian mode; teammate falls onto the mat -> "Fall detected."
5. Pitch line (above). Show measured accuracy numbers.

## Honest confidence
- High: Roblox <-> laptop, ESP32 LEDs/servo/fan, game logic, booth effects.
- Medium-high: Wi-Fi presence/motion behind a curtain at 1-2 m.
- Medium: reliably telling "frozen" from small movements in a crowded hall (core risk).
- Medium: rule-based fall detection (big spike then stillness is a strong pattern).
- Medium-low: breathing in a crowd. Low: activity recognition / pose on single-antenna ESP32.
- Wi-Fi heart rate is not reliable on ESP32; use camera rPPG (pyVHR) or MAX30102 if needed.
- Wi-Fi does NOT go through meters of rubble/concrete. Realistic: fabric, drywall, wood, one interior
  wall, a few meters. Frame rescue use as light debris / wood-frame hurricane damage / room sweeps.
  Deep rubble needs radar like NASA JPL's FINDER. Do not overclaim to judges.

## Go/no-go test (do before the hackathon)
Busy place (dining hall/library), person behind a curtain 1-1.5 m from the ESP32 link:
1. Empty vs person present: >= 9/10 correct.
2. Moving vs frozen: >= 9/10 correct.
3. No false eliminations for 30 s while still, with people walking 2-3 m away.
Fail -> switch core sensing to HLK-LD2410 radar (keep Wi-Fi as the research piece / comparison).

## Demo setup at the venue
- Ask organizers for an edge/corner table, ideally next to a real wall or doorway.
- Hiding spot: tablecloth over the table or cardboard boxes; person crouches/sits behind it.
- ESP32s low, 1-2 m apart, person inside the link line. Private ESP32-to-ESP32 link on a fixed channel.
- Recalibrate the empty zone before every player (show it as a visible "Scanning the room..." step).
- Record training data AT the venue spot, with crowd moving in the background. Tape down ESP32
  positions; any move means recalibrate/retrain.
- State machine with named states: EMPTY / MOVING / STILL_BREATHING / FALLEN. Change state only after
  3 consistent readings; keep last known state when a person goes quiet (Permanence-style object
  permanence, see notes below).
- Keep an offline local game screen as backup (Roblox needs internet). Bring a phone hotspot.
  Record a clean backup demo video as soon as the core works.

## Roblox integration details (verified against Roblox docs, Oct 2026)
- Roblox server script polls our laptop with HttpService (GetAsync/PostAsync) ~4x per second.
- Enable "Allow HTTP Requests" in Studio: File > Experience Settings > Security.
- HTTPS only, must be publicly reachable (Roblox servers can't reach localhost): use Cloudflare Tunnel or ngrok.
- Limit: ~500 HTTP requests/minute per game server. Expect ~0.2-0.5 s delay; add a short grace period after "red light".
- Open Cloud Messaging (push into game) is limited to ~120 messages/min per topic with 1 server and
  1 KB messages, so polling is better for live state.
- Send only game state to Roblox (moving/still/out). No names or biometric data (young audience).
- Docs: https://create.roblox.com/docs/en-us/cloud-services/http-service
        https://create.roblox.com/docs/en-us/cloud/guides/usage-messaging

## Architecture
```
ESP32 TX --Wi-Fi packets--> ESP32 RX --USB serial (CSI)--> Python on laptop
  Python: parse CSI -> clean/baseline -> motion (+ breathing, fall rule) -> state machine -> game logic
  FastAPI: /state (polled by Roblox + local screens), /command (red light from Roblox)
  WebSocket -> local web screens: game, Wi-Fi vision waterfall, leaderboard, Rescue/Guardian dashboard
  Laptop --USB serial--> ESP32 for LEDs / fan / optional servo / vibration
  Cloudflare Tunnel -> public HTTPS for Roblox
```

## Open-source code to build on (all verified to exist)
Wi-Fi CSI:
- https://github.com/espressif/esp-csi (official, start here)
- https://github.com/StevenMHernandez/ESP32-CSI-Tool (most used community tool)
- https://github.com/heyfinal/wifi-ghost (ESP32-WROOM through-wall presence, motion, breathing; closest to our setup)
- https://github.com/The-Masked-Bear/wifisense-pi (motion/breathing + live browser dashboard; ESP32-S3)
- https://github.com/jussi-modernpath/wisent (ESP32-S3 through-wall localization; documents failures)
- https://github.com/ruvnet/RuView (claims pose/vitals; unverified, previously accused of fake data; S3 boards)
- https://github.com/Marsrocky/Awesome-WiFi-CSI-Sensing (papers, datasets; cite in Devpost)
Camera heart rate: https://github.com/phuselab/pyVHR (simpler), https://github.com/ubicomplab/rPPG-Toolbox
Sensors/servos: SparkFun MAX3010x, SparkFun ENS160, Bosch BSEC2 (BME688), madhephaestus/ESP32Servo.
MQ gas sensors: 5 V heater, analog out needs a voltage divider before the ESP32 (3.3 V max); burn in
24-48 h before first use; show Normal/Warning/Danger, not fake ppm. Demo with hand sanitizer/isopropyl
on a swab; never real toxic gas or lighters.
Check licenses; credit everything used in README and Devpost.

## Research to cite
MIT Wi-Vi (2013), WiTrack (2014), Vital-Radio (2015), RF-Pose (2018); UW WiSee (2013);
CMU Person-in-WiFi (2019), DensePose from WiFi (2022); IEEE 802.11bf Wi-Fi sensing standard;
Espressif ESP-CSI; Wi-Fi fall-detection literature (see Awesome-WiFi-CSI-Sensing).
Note: the big results used special radios / multi-antenna routers, not ESP32s.

## Notes from reviewing past projects
- Permanence (github.com/billpquach/permanence) "memory" = tracking with rules, not AI memory:
  3D scan for a fixed world frame; YOLOE detections -> 3D via depth; Hungarian matching with distance
  gates; alpha-beta filter; when unseen, keep last position and label OUT_OF_VIEW / OCCLUDED / LOST;
  state changes need 3 frames agreeing; shell game rule: ball vanishes next to a resting cup -> INSIDE,
  rides with the cup. Borrow: keep state when a person goes quiet; 3-reading confirmation; named states.
  Permanence did not use Wi-Fi sensing, ESP32, or vitals.
- Farsight (github.com/azizu06/farsight): ~115k lines, but much was AI-agent generated (Devin),
  duplicated Python+Rust, ~35k tests. Size is not why it won.
- Our team's KYU-BOT (github.com/mbarbosa1/knightHacks): disaster triage agents + Databricks scorecard.
  Reuse map/dashboard experience for the Rescue/Guardian dashboard.
- A sign-language learning game ("Sign Hero") was done at a previous SwampHacks; avoid sign-language ideas.

## Build order
1. Before event: flash esp-csi to 2 ESP32s, stream CSI to laptop, see motion; run go/no-go test.
2. Motion detection + calibration at the venue.
3. Red Light/Green Light on local screen (offline backup) + speaker/LED cue inside the booth.
4. Heist + Wi-Fi vision screen.
5. Roblox avatar moving/freezing, then Roblox players triggering red light.
6. Booth 4D disaster effects.
7. Fall detection (rule-based) + Rescue/Guardian dashboard; breathing.
8. Stretch items in priority order.
Lock features; always keep one working demo; record backup video as soon as core works.

## Suggested team roles (adjust to team size)
- Signal lead: ESP32s, CSI, motion, breathing, fall rule, calibration.
- Game + screens: Red Light/Green Light, Heist, leaderboard, Wi-Fi vision, dashboards.
- Roblox: Studio world, Luau scripts, HttpService polling, tunnel.
- Hardware + story: LEDs, fan, speaker, booth build, gas sensor, Devpost, pitch, backup video.

## Working style
Team prioritizes understanding the code, testing, and in-person communication. Prefer clear, readable
code with tests over volume. Explain non-obvious signal-processing steps in comments.
