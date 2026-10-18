# Project context: Wi-Fi sensing game for SwampHacks

This file is read by Claude Code. It holds the decisions made while planning this project.
Treat it as the plan of record; update it as decisions change.

## The idea in one line
A real person hidden behind a curtain plays Red Light, Green Light, and **Wi-Fi signals** (ESP32 CSI)
decide whether they moved. The same sensing powers a **Fire rescue mode** (the hidden person is
trapped in a smoke-filled room and the system finds them) and a **Guardian mode** (fall detection at home without cameras).
A **Roblox** world mirrors the real person as an avatar, and online players can call "red light".

Pitch line: "Same Wi-Fi, three uses: a game, a fire rescue, and keeping grandparents safe at home
without cameras. If Wi-Fi can catch you holding your breath behind a curtain, it can tell firefighters
which smoke-filled room someone is still in, using the routers the building already has."

## Why this project (the goal)
Beat past winners like Farsight (rover + VR + Gaussian splatting) and Permanence (3D VLM + object
tracking + AR "x-ray"). What made them win: an "impossible" moment visible in a 3-minute demo, recent
research, real hardware, measured numbers, and judges taking part. Judges score: technical difficulty,
usefulness, wow/creativity, completeness (works live), presentation. Line count does not matter.

## Hardware we have / plan to buy
**Reality check (2026-10-17): we only have 4 ESP32s (1 TX + 3 RX), the laptop, a hub, a battery and
a phone. No LED strip, fan, motors, servos, gas or pulse sensors.** So: booth cues = laptop speech
(`server/app.py --speak`), fire / smart-light effects live on screen and in Roblox, and breathing
ground truth comes from the camera or paced breathing. The list below is the original wish list.
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

### 2. Fire rescue mode (the surprise twist, mostly physical + dashboard)
Decided 2026-10-17 on a mentor's suggestion: fire instead of earthquake/hurricane.
- After a game round: "Now there's a fire. You're trapped." The building fills with smoke.
- **Why fire is the strong story:** smoke blinds normal cameras but does not block Wi-Fi; buildings
  already have Wi-Fi routers, so no new hardware; firefighters could know which rooms still have
  people (and whether they're moving or breathing) *before* going in.
- **Honest limit:** thermal cameras also see through smoke, but not through walls or closed doors.
  Wi-Fi can (a few meters, interior walls). Pitch that edge, not "replaces thermal cameras".
- **Booth 4D effects** (cheap, big impact): LEDs flicker orange/red (flames), speaker plays crackling
  fire + smoke alarm + sirens, fan blows "heat". **No real smoke, haze/fog machine or flames** at the
  venue (sets off fire alarms); fake it with light and sound.
- **MQ-2 gas sensor fits directly:** it detects smoke. Demo with a swab of isopropyl/hand sanitizer
  (see MQ notes below), shown as Normal/Warning/Danger.
- The **web dashboard** finds the hidden person: "Person detected in Room 2, not moving, breathing,
  Priority 1" (+ smoke level if the gas sensor is built).
- Roblox here is limited to optional visuals (see Roblox scope).

### 3. Guardian mode (the real-world use)
- **Fall detection, rule-based first** (no ML needed): big sudden motion spike -> stillness for several
  seconds -> person still present / breathing => "Person fell, not moving." Show a mock alert to family.
  A trained classifier can come later if time allows.
- Demo falls: a teammate falls onto a mattress/crash pad. **Never ask a judge to fall.**
- Privacy angle: works without cameras in the home.

## Roblox scope (decided: keep it simple)
- **In scope:** the real person's avatar moves when they move and freezes/gets knocked out based on
  Wi-Fi. The game calls every light itself; online players do NOT get a "red light" button
  (removed 2026-10-17 at Arwa's request).
- **Optional stretch:** simple fire visuals in Roblox (Fire/Smoke effects, orange lighting, the
  building filling with smoke) timed with Fire rescue mode.
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
| Surprise | Booth 4D fire effects (flicker LEDs, crackle + alarm sound, fan) | 2-4 |
| Stretch 1 | Fall detection (rule-based) + Guardian/Rescue dashboard | 7-14 |
| Stretch 1 | Breathing (breath-holding callout, Zen Master) | 6-12 |
| Stretch 2 | "How did it catch me?" replay; accuracy scoreboard (Wi-Fi vs hidden camera); AI gator trash talk | 8-14 |
| Stretch 2 | Roblox fire visuals | 6-12 |
| Stretch 3 | MQ-2 smoke sensor in Fire rescue mode; QR "see your round" page (no names/emails stored) | 5-9 |
| Cut | Roblox rescue operation, Wi-Fi heart rate, Hide and Seek with location, full skeleton pose, 3D printed parts | - |

Core + Surprise is ~22-40 person-hours. With a team of 3-4 (~110 person-hours budget), Stretch 1 and
some of Stretch 2 are realistic. Always keep one working demo; cut from the bottom up.

## Demo flow (about 3 minutes)
1. Play: judge plays Red Light, Green Light behind the curtain, gets caught; replay shows the signal spike.
2. Twist: "Now there's a fire. You're trapped." Orange flicker LEDs, crackling + smoke alarm, fan.
3. Rescue: dashboard shows "Person detected, not moving, breathing" (+ smoke level).
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
  wall, a few meters. Frame fire rescue as room-by-room sweeps through smoke, walls and closed doors
  using existing building Wi-Fi. Deep rubble needs radar like NASA JPL's FINDER. Do not overclaim.

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
- HTTPS only, must be publicly reachable (Roblox servers can't reach localhost): we use the Vultr hub (see Architecture).
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
  Laptop dials OUT (WebSocket) -> Vultr hub https://64-177-42-5.sslip.io (server/hub.py, Caddy HTTPS)
     Roblox + judges' dashboard talk to the hub; it answers /api/sense from memory and
     forwards everything else to the laptop. No tunnel. Deploy: server/deploy/deploy_hub.sh
```
Decided 2026-10-17: Vultr hosts the public side; sensing + the red-light decision stay on the
laptop (boards are USB; a network hiccup must never cause a false catch).

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

## Polish list for the end (requested 2026-10-17, do after the core features)
- Web "Wi-Fi vision" UI: redesign so it looks hand-made and polished, not generic/"AI-looking".
- Roblox game controls: skip the round, pause, start early (host/booth buttons).
- Spoken cues: "Room scanned. Next player, go in." sounds bad; rewrite the lines and pick a
  better voice (macOS `say -v` premium voices, or a TTS service).
- Possibly: elimination needs MOVING for ~0.75 s in a row during red (fewer twitch/glitch catches).
- More hidden players: **team mode** (2-3 people in one spot; anyone moves on red -> whole team out)
  and a **player queue** (next judge's name queued, rounds back to back with auto-scan between).
- **Individual players at once = zone localization (decided 2026-10-17).** Keep 1 TX + 3 RX, no
  hardware changes (no second TX, no foil divider). Treat the 3 receivers as a spatial array:
  the model outputs {movement, location 0=zone A..1=zone B, confidence}; a player is out only
  if movement is located in their zone for ~0.8 s; ambiguous -> nobody out. Generalises to N zones.
  Experiment first: `record_zones.py` (spoken script: A moves / B moves / both / both freeze,
  `--mode solo` for one person, `--swapped` second session so it learns places, not people),
  then `eval_zones.py` (signatures per board, held-out who-moved + location AUC, wrong-player
  blamed must be 0). Only build game/Roblox changes if it passes.

## Tiger Data showcase ideas (decided 2026-10-17)
1. Stillness percentile: "you were stiller than 87% of SwampHacks" (toolkit percentile over every
   player's red-light motion), shown after each round.
2. Wi-Fi time machine in Roblox: on a catch, Roblox fetches the 8 s around it from Tiger
   (/api/replay) and replays a ghost avatar + the 3 boards' signal graph in-game.
3. Wi-Fi fingerprints: CSI-BERT2 embeddings (128-d per second) in pgvector; "this movement looks
   most like..." via nearest-neighbour search.
Also: Guardian "room memory" day strip (state_agg), and forks to re-score old games with a new model.

## AI gator on Vultr Serverless Inference (decided 2026-10-17)
- `server/gator.py`: one line after each catch/win + rescue sentence; `app.py --speak --gator`.
  Off the red-light path, 3 s deadline, checks (length, kind words, no made-up numbers incl.
  spelled-out ones, no "let me look up..." narration), fixed fallback lines otherwise.
- Pick models with `server/gator_eval.py` (usable / judge-faithful / p50 / p95). Chosen:
  `deepseek-v4.1-flash`, tool calling, `reasoning_effort: none` (reasoning on -> empty lines).
  kimi-k2-instruct from the docs isn't offered; tool calling works on deepseek/mimo/qwen flash.
- Fixed cues pre-rendered with `qwen3-tts-12hz-1.7b` voice `aiden` into `server/voice_cache/`
  (returns MP3, not WAV). Config lives in `server/.env` (VULTR_*).
- Vultr has no model for CSI: breathing, presence, posture stay our own signal processing / models.

## Curtain tests and the ML decision (2026-10-17)
- Live voice-guided tests behind a curtain: the hider must be BETWEEN the TX and the 3 RX.
  TX in front of the curtain -> moves not seen (2%). TX 50 cm behind the hider -> moves 100%
  caught but freeze read as moving (too close to TX). Next: TX ~1-1.2 m behind the hider.
- Force a room scan after moving any board (the auto-scan "spoiled scan" check can keep an old one).
- **Decision: ML for everything** (move/freeze, presence, breathing, falls, zones): record
  labelled data in the real booth layout with a spoken-script recorder, train, report held-out
  numbers; rules only as a baseline.

## Experiments still to run (need the boards)
- Paced breathing (`record_breathing.py`, then `eval_breathing.py`): true vs Wi-Fi rate + empty-room
  control. Breathing rate is signal processing (FFT); train a "breathing yes/no" model only if
  the empty-room control shows too many false detections.
- More game sessions for `evaluate.py`: sitting/crouching, ~2 m away, a second person.
- Curtain test (hidden under the tablecloth) and through-wall test, labelled by a spoken
  freeze/move script instead of the camera (the camera can't see behind the curtain).
- Zone test (see polish list).
- Optional: receivers listening to a normal router (own hotspot/travel router, never the venue's)
  for the "uses the building's existing Wi-Fi" pitch.

## Build order
1. Before event: flash esp-csi to 2 ESP32s, stream CSI to laptop, see motion; run go/no-go test.
2. Motion detection + calibration at the venue.
3. Red Light/Green Light on local screen (offline backup) + speaker/LED cue inside the booth.
4. Heist + Wi-Fi vision screen.
5. Roblox avatar moving/freezing, then Roblox players triggering red light.
6. Booth 4D fire effects.
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
