# Three Feet — Build & Win Plan

> **Goal:** Submit a working, measured, and memorable demo by **Wed Nov 4, 2026, 9:00 p.m. ET**. Target **8:00 p.m.** so there is a buffer.
> **Time available:** about 55 hours from Mon Nov 2, 2 p.m. ET. Plan for about 30 hours of actual work.
> **One-line pitch:** *Turn an old Android phone and a $20 smart plug into an AI guardian that keeps flammable things three feet from a space heater and proves it turned the heater off.*

This plan replaces parts of the README's original concept where a better hackathon choice exists. These changes are listed in [§2](#2-changes-from-the-original-idea-and-why).

---

## 0. TL;DR — the winning formula

| Rubric (weight) | How we score high |
| --- | --- |
| **Impact (30%)** | A real, deadly, seasonal problem with NFPA data. Needs **no special hardware**: an old Android phone and a common smart plug. Serves renters, older adults, and people who rely on space heaters. |
| **Meta AI tooling (25%)** | **Four Meta models working as a team**, not a single API call. Muse Spark discovers objects in the scene → SAM 3.1 segments them with pixel-accurate masks → Muse Spark judges flammability with structured output → Muse Wonder reads the heater's PDF manual for the clearance rule (with page citations) and writes the evening watch report. Stretch goals add Muse Voice Transcribe and Muse Glimmer offline. |
| **Technical depth (20%)** | Real-world **inches** from a single camera using floor-plane homography with ArUco markers. Includes an error bar for each measurement, a deterministic safety state machine, **two-signal shutdown verification**, fail-safe handling for camera, API, and plug failures, a cost governor, a replay harness, and a tape-measure validation table. |
| **Demo (25%)** | A tight three-minute video: the blanket slides in → the overlay turns red → "14.2 in ± 1.1 — manual p.4 requires 36 in" → countdown → plug clicks off → **"CONFIRMED OFF (2/2 signals)"**. Then the camera feed is unplugged and the screen says **"UNKNOWN — not safe,"** not "all clear." It closes with measured results. |

**Design principle (say this in the video):** *"The AI can only make Three Feet more cautious, never less. Models perceive and explain; deterministic code decides when to cut power, and power stays off until a human resets it."*

---

## 1. Can I use my Android phone as the camera? **Yes.**

One phone is enough. The plan switches from **two cameras with stereo** to **one camera with a calibrated floor plane**. This approach is more accurate, easier to validate, and much faster to build. See [§5](#5-measuring-inches-with-one-phone).

### Connection options, ranked

| # | Option | How | Pros | Cons |
| --- | --- | --- | --- | --- |
| **A (chosen)** | **[Android IP Camera](https://github.com/DigitallyRefined/android-ip-camera)** (F-Droid, open source) | Default port **4444**. The stream is at `https://PHONE_IP:4444/video/mjpeg` (`multipart/x-mixed-replace; boundary=frame`), and a single frame is at `/video/snapshot?camera=back`. Phone health (battery, Wi‑Fi signal) is at `/info.json`. Supports a username and password and a **self-signed TLS** certificate. | The **laptop pulls** from the phone. The web panel controls resolution, zoom, exposure, and contrast. **`POST /control/stop` and `/control/start`** let a script simulate "camera lost" for fault-injection tests and the demo. `/record/start` can record raw footage on the phone. | OpenCV's `VideoCapture` often fails with HTTPS, self-signed certificates, and Basic Auth. Read the MJPEG stream yourself with `httpx` (`verify=False`, `auth=(user, pw)`): split on the `--frame` boundary and decode each JPEG with `cv2.imdecode`. Keep credentials in `.env`. |
| A′ | **IP Webcam** app (Play Store, by Pavel Khlebovich) | `http://PHONE_IP:8080/video` (MJPEG), `/shot.jpg` | Plain HTTP; `cv2.VideoCapture` works directly | Closed source; Play Store only |
| B | **DroidCam** (phone app plus Windows client) | Use Wi‑Fi or **USB** (USB debugging). It appears as a normal webcam, such as `cv2.VideoCapture(1)`. | **USB works when Wi‑Fi is blocked.** | Requires installing the Windows client, which may need administrator rights. |
| C | **Windows 11 Phone Link — "Connected camera"** | Use Link to Windows on Android 9+ and Windows 11. | Built in, with no third-party app. | Managed or corporate PCs often disable it. |
| D (stretch / product story) | **Browser page on the phone** (`/phone`) | Use `getUserMedia({video:{facingMode:"environment"}})` to send JPEG frames over a WebSocket. | No app install. The phone becomes part of the product. | Needs **HTTPS** on the LAN (a self-signed certificate or tunnel) and inbound connections to the laptop. |

**Recommendation:** Use **A** (Android IP Camera) for the build and demo, with **B over USB** as the fallback. Put both behind one `FrameSource` interface so either can be selected with a CLI flag. Also support **folder of JPEGs** and **video file** sources so most of the code can be built and tested before the camera is mounted.

### Phone setup checklist

- [ ] Use the **main (1×) lens**, not the ultra-wide lens. Ultra-wide distortion bends the floor-plane math.
- [ ] Use landscape at **1280×720**. Set JPEG quality to about 80.
- [ ] **Fix zoom and exposure** in the web panel and never change them after calibration. If the image scale changes, the calibration is invalid. The app detects this through ArUco drift ([§5](#5-measuring-inches-with-one-phone)).
- [ ] Keep it **plugged into a charger**. Set **Settings → Apps → Android IP Camera → Battery → Unrestricted**. Enable the app's display-off option to reduce heat. Use medium quality or frame rate if the phone gets hot; 5–10 fps is enough.
- [ ] Turn on **username and password** protection in the app.
- [ ] **Mount it high and look down** (45°–90°), for example from a bookshelf, door top, or a $10 clamp or tripod. A near-overhead view makes the floor-plane measurement accurate. This matters more than resolution.
- [ ] Network: put the phone and laptop on the same Wi‑Fi. If the Wi‑Fi isolates clients (guest or corporate networks), **connect the laptop to the phone's hotspot**, or use DroidCam over USB.
- [ ] Your only phone is now the sensor. Use the **laptop webcam** (or a borrowed phone) to film the physical scene for the demo video.

---

## 2. Changes from the original idea and why

| Original | New plan | Why |
| --- | --- | --- |
| Two cheap cameras with stereo | **One Android phone, overhead, with floor-plane homography from ArUco markers** | Stereo calibration, synchronization, and matching are risky with two days left. A homography gives true inches on the floor plane with a measurable error and works with the hardware you already have. |
| ESP32 with the heater's IR remote | **Tier A: a GE Cync smart plug** (15 A; no power meter). **Tier B:** ESP32 IR (only if you already have the parts and the heater has a remote). | The plug can cut power and report that it is off; the camera seeing the power light go dark is the independent check. IR remotes usually **toggle** power, so sending "off" to a heater that is already off can **turn it on**. Tier B must check state before and after every IR command. |
| Confirms off | **Two-signal confirmation:** (1) plug power draw is below 5 W, and (2) the camera sees the heater's indicator light or element go dark | The README requires independent confirmation. Two different physical signals justify the claim. |
| Muse Wonder "keeps watch through the evening" | **Muse Wonder** extracts manual rules from the PDF and writes a **watch report and incident explanation** using tools. A **deterministic loop** keeps watch. | Do not use an LLM as the real-time safety loop. Use it where reasoning helps. |
| Hard-coded object list | **Open-vocabulary loop:** Muse Spark names what it sees → SAM 3.1 segments those concepts → a **change-detection catch-all** flags anything unexplained in the zone as `UNKNOWN` | This uses the Muse Spark + SAM pairing that Meta's docs recommend. The catch-all means an unrecognized object still triggers an alert. |

---

## 3. System architecture

```text
 ┌──────────────┐   JPEG/MJPEG   ┌──────────────────────────── Laptop (Python) ────────────────────────────┐
 │ Android phone│ ─────────────▶ │ FrameSource ─▶ Calibration (ArUco→homography, drift check)             │
 │ (IP Camera)  │                │      │                                                                  │
 └──────────────┘                │      ▼                                                                  │
                                 │ MotionGate (local OpenCV, free) ── idle? ──▶ skip API calls             │
                                 │      │ activity in/near zone                                            │
                                 │      ▼                                                                  │
                                 │ Perception ──▶ Muse Spark: "list objects near the heater" (every ~5 s)  │
                                 │      │        SAM 3.1: one request per concept, parallel (≤1 Hz)        │
                                 │      │        Catch-all: background-difference blobs inside the zone    │
                                 │      ▼                                                                  │
                                 │ Geometry: mask → floor inches → min distance to heater footprint ± err  │
                                 │      ▼                                                                  │
                                 │ Hazard: Muse Spark structured JSON per new object (cached per track)    │
                                 │      ▼                                                                  │
                                 │ SafetyStateMachine (deterministic) ─▶ Actuator (plug/IR) ─▶ Verifier    │
                                 │      │                                     (watts + camera LED ROI)     │
                                 │      ▼                                                                  │
                                 │ EventLog (JSONL) ─▶ Muse Wonder: incident explanation + evening report  │
                                 │      ▼                                                                  │
                                 │ FastAPI + WebSocket ─▶ Dashboard (browser; also viewable on any phone)  │
                                 └─────────────────────────────────────────────────────────────────────────┘
   Setup-time only: heater manual PDF ─▶ Muse Wonder ─▶ clearance rules + page/quote ─▶ human review ─▶ rules.json
```

### Tech stack (optimized for speed)

- **Backend:** Python 3.11+, `opencv-contrib-python` (ArUco), `numpy`, `fastapi`, `uvicorn`, `openai` (Meta Model API is OpenAI-compatible at `https://api.meta.ai/v1`), `httpx`, `pydantic`, and Meta's SAM parser (`meta_sam_parser` in Python, or `@meta-sam/parser` in TypeScript).
  - ⚠️ **Do not write your own mask decoder.** The base85 payload uses a specific alphabet. Use Meta's parser.
- **Plug:** GE Cync through its cloud (`pycync`): a one-time login saves tokens; the plug reports on/off but not watts.
- **Frontend:** A Vite + React + TypeScript + Tailwind dashboard in `web/`, built and served by FastAPI. It shows the annotated MJPEG stream and a WebSocket state feed. **Skip Next.js.** See [FRONTEND_PLAN.md](./FRONTEND_PLAN.md).
- **Alerts:** Browser notification, alarm sound, and spoken TTS (`speechSynthesis`) on the dashboard. Optional phone push through [ntfy](https://ntfy.sh).

### Proposed repository layout

```text
three_feet/
  app.py                 # FastAPI, WebSocket, MJPEG overlay stream, main loop
  config.py              # thresholds, dwell times, budget caps (pydantic settings, .env)
  sources.py             # FrameSource: ip_webcam | opencv_index | video_file | replay_dir
  calibration.py         # ArUco detection, homography, inches-per-pixel map, drift check
  geometry.py            # zone polygon (per-side clearances), distance map, closest point, error bar
  perception/
    meta_client.py       # OpenAI client → api.meta.ai, retries, timeouts, cost meter
    sam.py               # SAM 3.1 concept + box prompts, parse via meta_sam_parser
    spark.py             # scene inventory + hazard JSON (structured output)
    wonder.py            # manual extraction (PDF) + incident/evening reports (tool calling)
    catchall.py          # background model + zone-change blobs → UNKNOWN objects
    tracker.py           # IoU track IDs, temporal smoothing
  safety/
    state_machine.py     # CLEAR/WATCH/WARN/SHUTOFF_PENDING/OFF_UNCONFIRMED/OFF_CONFIRMED/DEGRADED
    actuator.py          # DryRun (simulated) | cync_plug.py: GE Cync
    verifier.py          # watts + LED-ROI brightness (+ optional Spark check)
  eval/
    record.py            # save frames + events for replay
    trials.csv           # tape-measure ground truth vs estimates
    report.py            # MAE, boundary accuracy, confusion, shutoff stats → RESULTS.md
  static/index.html      # dashboard
tools/print_markers.py   # generate printable ArUco sheet
rules/heater_rules.json  # reviewed clearance rule with source page + quote
```

---

## 4. Meta model usage (the 25% category)

Every call has a clear purpose. All model calls use **Standard mode**, not Contributor mode, because frames from inside a home should not become training data. Mention this privacy decision in the video.

### 4.1 Muse Spark (`muse-spark-1.3`) — scene inventory ("what should SAM look for?")

- **When:** At startup, then every ~5 s while there is motion near the zone.
- **Input:** The full frame with the zone overlay drawn on it.
- **Output:** Structured JSON listing up to 6 short noun phrases for objects **in or near the outlined zone**, such as `["fleece blanket", "cardboard box", "curtain"]`.
- The prompt asks for phrases that follow SAM's rules: one concrete noun phrase each, with no spatial words or negation.
- Merge the result with an always-on safety list: `blanket`, `curtain`, `cardboard box`, `towel`, `clothing`, `paper`. Cap the total at 6 concepts per cycle to control cost.

### 4.2 SAM 3.1 (`sam-3.1`) — pixel-accurate geometry

- **Heater (setup):** Send a **box prompt** from one user drag on the dashboard. This is more reliable than the text "space heater." Convert the mask to the heater footprint polygon, and let the user adjust the corners. Re-check every 60 s; if the heater moves, show `CALIBRATION_DRIFT`.
- **Objects (live):** Send **one concept prompt per request**, in parallel, as base64 JPEG `input_image` data URLs. Set `include_confidence: "true"` and `mask_encoding: one_bit`.
- **Tracking stretch:** On a motion burst, send a short MP4 clip (5 s × 10 fps = 50 frames ≈ $0.01) with `input_video`. SAM's stable object IDs then provide tracks.
- **Constraints from the docs:**
  - Send only one concept per request; lists return nothing.
  - Use a short noun phrase.
  - Zero matches is a valid result, not an error.
  - Key records by object ID, not by position.
  - Frame indices can skip.

### 4.3 Muse Spark — hazard judgment (structured output, cached per track)

```json
{ "label": "fleece blanket", "material_guess": "polyester fleece",
  "flammability": "high | medium | low | unknown", "confidence": 0.0,
  "why": "synthetic textile; ignites and melts readily", "seen_clearly": true }
```

- Send a crop around the mask and a small scene thumbnail.
- **Policy:** `unknown`, low confidence, or `seen_clearly=false` → treat as **high**.
  - `low`, such as a metal stand → **still warn**, but use a longer dwell time.
  - The model can **shorten** the time to shutoff, but it can **never** dismiss an object in the zone.
- Show the `why` text on the dashboard. Judges should see the explanation.

### 4.4 Muse Wonder (`muse-wonder-1.0`) — manual reader and watch-report agent

1. **Manual → rules (setup):**
   - Upload the heater manual PDF. Most manuals are public on the manufacturer's site, such as Lasko or Vornado.
   - Muse Wonder returns structured output: `{front_in, sides_in, back_in, top_in, quote, page, confidence}`.
   - The dashboard shows the **quoted text and page number** next to the extracted number. The user must click **Approve** before the rule becomes active.
   - If the manual has no rule, default to **36 in on all sides**. Use the stricter value whenever the manual and the default disagree.
   - Keep a **second manual** for a test case: a different heater with different clearances. It shows that the rule comes from the manual, not hard-coded logic.
2. **Incident explanation:**
   - After each shutoff, Wonder receives the event log through tool calls: `get_events`, `get_snapshot`, `get_rule`, and `get_power_trace`.
   - It writes a short report, such as: *"At 9:14:03 PM a fleece blanket was 14 in from the heater's front (rule: 36 in, manual p.4). After 10 s, power was cut; draw fell from 1,480 W to 0 W in 0.8 s and the element went dark. Confirmed off."*
3. **Evening watch summary (stretch):** Wonder reviews the night's events, such as near misses, repeated objects, and degraded periods. It suggests changes, such as *"The laundry basket keeps drifting near the left side; move it."*

### 4.5 Stretch Meta uses (only after the MVP works)

- **Muse Voice Transcribe:** A push-to-talk voice query, such as *"Three Feet, what's near the heater?"* Wonder or Spark answers through TTS. This is an accessibility story for older adults.
- **Muse Glimmer (open weights, local):** Run hazard judgment **offline** when the API is unreachable. This keeps the system usable without a network and keeps frames local. Try it only if you have a GPU.
- **Muse Code:** Use it as the coding agent while building. Mention it if you use it.

---

## 5. Measuring inches with one phone

**Idea:** Most clutter rests on the floor. A homography maps image pixels to **floor coordinates in inches**. With a near-overhead camera, parallax from object height is small and has a bound.

### Calibration (2 minutes, one time)

**Quick setup (no printing, chosen for the first demo):** Measure the heater base's width and depth, then click its 4 base corners (`scripts/calibrate.py --heater W D`).
- With a typical phone field of view assumed (66°, `--fov` to change), the camera pose is solved from the heater rectangle (`cv2.solvePnP`, IPPE). This is about 5× steadier than a free 4-point homography, which turns a 3 px click slip into ±35 in at 3 ft.
- A Monte Carlo pass jitters the clicks (3 px), the tape measurement (¼ in), and the field of view (10%). Its 95th-percentile error at 36 in becomes `distance_err_frac`, and every distance's error bar grows by that fraction of the distance.
- With the phone about 6 ft away, this gives about ±20% of distance (±7 in at 36 in). That's fine for "the blanket is touching the heater," but it is wide at the 3 ft boundary. If it reads 30% or more, move the phone closer or use markers.
- Limitation: there are no markers to watch, so drift isn't detected. Rerun the setup if the phone moves.

**Markers (tighter error bars):**

1. Print 4 **ArUco markers** (`DICT_4X4_50`, 4 in squares) with `tools/print_markers.py`. If there is no printer, place 4 pieces of bright tape and click them in the UI.
2. Put the markers at the corners of a **48 × 48 in square** centered on the heater. Measure their exact positions with a tape measure, and enter the coordinates in the UI or in `calibration.json`.
3. Compute `H = cv2.findHomography(image_pts, floor_pts_in)`. Show the **reprojection RMS in inches** in the UI.
4. Precompute an **image-space distance map**:
   - For each pixel: floor coordinate → signed distance in inches to the heater footprint.
   - The **zone** uses per-side clearances (front, sides, and back) from the manual. The user marks the heater's front with one click.
   - Live distance = `min(distance_map[mask])` plus the closest point. This takes microseconds per mask.
5. **Drift check:** Re-detect markers every 5 s. If marker corners move by more than 3 px, show `CALIBRATION_DRIFT`, which is a degraded state.

### Error bar shown with every number

```text
err_in = calib_rms_in + mask_edge_px * inches_per_px(at closest point) + parallax_term
         + distance_err_frac * distance_in        # quick setup only; 0 with markers
```

- `inches_per_px` comes from the local Jacobian of `H`.
- Use a mask edge of about 2 px.
- **Use the conservative bound:**
  - `distance - err < clearance` → **in zone**.
  - `distance - err < clearance + 6 in` → **WATCH**.
- Fit the parallax term from the validation trials ([§8](#8-validation-protocol-run-it-wed-morning)) instead of guessing it.

### Known limitations (state them openly)

- Measures horizontal clearance on the floor plane only.
- Hanging fabric, such as curtains, is still flagged because the overhead view puts it inside the zone. The measured distance can be off by about the fabric's height times tan(view angle).
- Occlusions behind the heater are not visible and are reported as such.
- One heater, one room, and fixed calibration.

---

## 6. Safety state machine (deterministic, tested)

```text
            ┌──────── everything visible, nothing within clearance+6" ────────┐
            ▼                                                                 │
  ┌───────────────┐  object within clearance+6"  ┌──────────┐  within clearance  ┌──────────┐
  │    CLEAR      │ ───────────────────────────▶ │  WATCH   │ ─────────────────▶ │   WARN   │
  └───────────────┘ ◀─────────────────────────── └──────────┘ ◀───────────────── └────┬─────┘
          ▲                                                    cleared               │ dwell ≥ T(hazard)
          │ human reset                                                              ▼
  ┌───────┴───────┐   watts<5W AND LED dark   ┌──────────────────┐  send OFF   ┌──────────────┐
  │ OFF_CONFIRMED │ ◀──────────────────────── │  VERIFYING (≤5s) │ ◀────────── │ SHUTOFF_PEND │
  └───────────────┘                           └────────┬─────────┘             └──────────────┘
                                                       │ timeout / 1 signal only
                                                       ▼
                                              ┌────────────────────┐ retry ×2, then loud alarm
                                              │ OFF_UNCONFIRMED    │ + push "CHECK HEATER NOW"
                                              └────────────────────┘
  Overlay states (any time): DEGRADED_CAMERA_LOST | CAMERA_FROZEN | API_DOWN | CALIBRATION_DRIFT | PLUG_UNREACHABLE
```

- **Dwell T:**
  - high or unknown: **10 s** for the demo; configure 30 s for real use.
  - medium: 20 s.
  - low: 60 s, with a warning.
  - A **person or pet** should only warn and should never cut power. Use a SAM `person` concept; it is a stretch goal.
- **Latched off:** Power stays off until a human presses **Reset and turn on**. This explicitly restores plug power and resumes monitoring only after a fresh, calibrated view establishes a clear zone and the API and plug are available. Removing an object never automatically restores power.
- **Degraded policy:**
  - If the camera has been lost or frozen for more than 30 s while the heater draws power, show `UNKNOWN — not safe`.
  - The **fail-safe option** cuts power. Default it **on** for the demo so it is visible.
  - Detect a frozen camera with a perceptual hash. If it is unchanged for more than 10 s, treat the camera as frozen. Also poll `/info.json` for phone battery and Wi‑Fi health.
- **API down:** Geometry and the catch-all still run locally. Every object becomes `UNKNOWN`, which is treated as high hazard. Safety degrades conservatively.
- **Unit tests:** About 15 pytest cases cover every transition, including "one verification signal only → OFF_UNCONFIRMED" and "API timeout during WARN → still shuts off."

---

## 7. Actuation and independent verification

### Tier A — GE Cync smart plug (chosen; in hand)

- **GE Cync Indoor Plug** (rated **15 A**), controlled through Cync's cloud with the unofficial `pycync` library. It has **no power meter**.
- **Signal 1:** the plug reports its relay is off after the OFF command (the plug's own report, labelled as such).
- **Signal 2 (independent physical signal):**
  - The camera watches a small ROI on the heater's power LED, display, or glowing element.
  - Before calibration, record the ROI's brightness while on and while off. "Off" requires it to go dark.
  - Optionally, Muse Spark can confirm with a yes/no check: *"Is the heater's indicator light on?"*
- **Both signals → `OFF_CONFIRMED (2/2)`**. One signal → `OFF_UNCONFIRMED`.
- ⚠️ Many heater manuals say to plug **directly into a wall outlet** (no extension cords or power strips). A plug-in smart plug conflicts with that guidance.
  - For the demo, use a **stand-in load**, such as a desk lamp or the heater's **fan-only** mode, for staged object trials.
  - Show the real heater shutoff **separately under supervision**, as the README already requires.
  - State this in the video. Honesty here helps credibility.

### Tier B — ESP32 IR (only if you already own an ESP32, IR LED, and IR receiver, and the heater has a remote)

- Learn the power code with `IRremoteESP8266` and expose `POST /ir/power` from ESP32 firmware.
- Because **IR power buttons usually toggle**, follow this sequence: **read state (watts/LED) → send only if ON → verify OFF → if it is still ON, retry once → if it turned ON, send again and alarm.**
- Verification still needs watts (a metering plug or clamp) or the LED ROI.

### Tier C — DryRun

- Use only for development and the replay harness. **Never present it as real shutdown in the video.**

---

## 8. Validation protocol (run it Wed morning)

All results are logged to `eval/trials.csv`. `eval/report.py` generates `RESULTS.md` with tables and a scatter plot of estimated vs. tape distance.

| Test | Design | Metrics |
| --- | --- | --- |
| **Distance accuracy** | 3 objects (blanket, box, towel) × 8 distances (12, 24, 30, 33, **36**, 39, 42, 48 in) × 2 lighting conditions = **48 trials**, measured with a tape measure | MAE, max error, error vs. distance, and **boundary classification accuracy** at 36 in |
| **Detection** | Same trials, plus 6 "unusual" objects (a shoe, a plastic bag, a stuffed toy, etc.) | Missed detections, false alarms, and how often the catch-all caught misses |
| **Shutoff** | **20 OFF commands** on the stand-in load, plus **3 real-heater shutoffs**, supervised | Attempted, succeeded, confirmed 2/2, and latency (p50/p95) |
| **Fault injection** | `POST /control/stop` on the phone (camera lost), freeze the frame, use an invalid API key, unplug or disconnect the plug, move a marker | Correct degraded state shown? Correct fail-safe action? Time to detect |
| **Cost and latency** | Log every API call | $/active-hour, SAM latency p50/p95, and end-to-end alert latency |

Report the trial count, setup, and limitations. **Do not claim real-world safety from a small demo.**

---

## 9. Cost governor (the $150 credit is plenty if gated)

| Item | Price | Policy | Est. |
| --- | --- | --- | --- |
| SAM 3.1 image | $2.50 / 1k images | Only on local motion near the zone, ≤ 6 concepts, ≤ 1 Hz | ≈ $0.015/s while active, $0 idle |
| Muse Spark | $1.25 in / $4.25 out per 1M tokens | Inventory every 5 s while active; hazard JSON once per new track | Pennies |
| Muse Wonder | $4 in / $20 out per 1M tokens | Setup plus once per incident | < $1 total |

- Enforce a **budget cap** in `config.py`, such as `$5/hour`. Show a live **$ meter** on the dashboard; judges like seeing that cost was considered.
- If the cap is reached, fall back to catch-all-only mode, which is conservative.
- Claim the **$150 credit now** and confirm it appears in billing. Credits are applied in batches, and a payment method is required.

---

## 10. Schedule (ET) — hard cutlines

### Mon Nov 2 (now → ~11 p.m.) — "Pixels to inches"

1. **(30 min)** Claim API credits and get an API key. Run `client.models.list()` and confirm that `sam-3.1`, `muse-spark-1.3`, and `muse-wonder-1.0` are listed.
2. **(30 min)** **Get the smart plug** (done: GE Cync Indoor Plug). Download the heater's PDF manual.
3. **(45 min)** Set up Android IP Camera, write `sources.py` (custom MJPEG reader), and show live frames in an OpenCV window. Mount the phone overhead.
4. **(1.5 h)** Send one SAM 3.1 request on a frame ("blanket"), parse it with Meta's parser, and draw the mask. **Measure the latency.**
5. **(2 h)** Calibrate with ArUco or tape clicks, build the distance map, and print *"blanket: 22.4 in ± 1.2"*. Check it with a tape measure.
6. **(30 min)** Commit and push.

**✅ Gate 1:** A live phone feed produces a SAM mask with a distance in inches that is within about 2 in of the tape measurement. If not, simplify by using an overhead view, a larger marker square, or manual corner clicks.

### Tue Nov 3 — "The full loop" (last full day for Discord or office-hours help; ask questions early)

| Time | Task |
| --- | --- |
| 8–10 | State machine + pytest; `DryRun` actuator; event log |
| 10–12 | Spark inventory → SAM fan-out; Spark hazard JSON; catch-all blobs; simple IoU tracker |
| 12–1 | Plug actuator + watts verifier + LED ROI verifier |
| 1–4 | FastAPI dashboard: overlay stream (masks, zone, closest-point line, label "14.2 in ± 1.1"), state banner, countdown, rule card with manual quote/page, $ meter, event log, Reset button, alarm + TTS |
| 4–6 | Muse Wonder manual extraction + **Approve** UI; incident report |
| 6–8 | Degraded states + fail-safe; frame recorder + replay mode |
| 8–10 | End-to-end rehearsal ×5; fix the top 3 bugs; push |

**✅ Gate 2 (by 10 p.m.):** Unattended, the loop goes from blanket in → WARN → shutoff → `OFF_CONFIRMED 2/2` → Wonder report, and it handles one failure case visibly. **Stop adding features after this gate.**

### Wed Nov 4 — "Prove it, film it, ship it"

| Time | Task |
| --- | --- |
| 8–11 | Validation trials ([§8](#8-validation-protocol-run-it-wed-morning)) → `RESULTS.md` |
| 11–2 | Record demo footage: screen capture plus laptop-webcam B-roll of the scene. Record **several takes**. Keep a replay-mode backup run. |
| 2–4 | Edit the **≤ 3:00** video, add captions, and upload it unlisted. Check that it plays in a private window. |
| 4–6 | README rewrite: what was built during the hackathon, architecture, how to run, results, limitations, safety disclaimer, and the list of team members |
| 6–7 | Make the repository **public**, then check every link while logged out |
| **7–8** | **Submit on Cerebral Valley.** Use the Indie track and the **Push the Frontier** theme. |
| 8–9 | Buffer only. Do not change code. |

---

## 11. Three-minute demo video script

| Time | Shot | Voiceover (summary) |
| --- | --- | --- |
| 0:00–0:15 | Heater beside a blanket; NFPA statistic on screen | "Space heaters and heating stoves cause about 30% of U.S. home heating fires and 73% of the deaths. Most of these fires start with something too close." |
| 0:15–0:35 | Old Android phone taped up high; $20 plug | "Three Feet turns an old phone and a smart plug into a heater guardian, using four Meta models." |
| 0:35–1:00 | Manual PDF → Muse Wonder extracts **"36 in, p.4"** with quote → **Approve** | "It reads *your* heater's manual for the clearance rule, cites the page, and waits for you to approve it." |
| 1:00–1:45 | **Hero shot.** Split screen: B-roll + dashboard. Blanket slides in. Muse Spark names it, SAM 3.1 masks it, label shows "14.2 in ± 1.1", Spark says "polyester fleece, high", 10 s countdown, *click*, watts 1,480 → 0, LED dark, **CONFIRMED OFF 2/2** | Narrate each model's role in one sentence. |
| 1:45–2:05 | Unplug or stop the camera → **"UNKNOWN — not safe"** → fail-safe | "Missing information never counts as safe." |
| 2:05–2:20 | Unusual object (shoe or plastic bag) caught by the catch-all as UNKNOWN | "If the AI can't recognize something, Three Feet treats it as dangerous." |
| 2:20–2:40 | Wonder incident report + `RESULTS.md` table and scatter plot | "48 tape-measure trials: mean error X in; 20/20 shutoffs confirmed…" (use real numbers) |
| 2:40–3:00 | Architecture diagram + code glimpse + disclaimer | "AI makes it more cautious, never less. A research prototype, not a certified safety device." |

**Recording tips:** Use OBS for the screen. Use the laptop webcam for B-roll. Put large text on the overlays and use high-contrast masks. Show a visible timer on the dashboard. Record the voiceover separately. Add captions.

---

## 12. Risk register

| Risk | Likelihood | Mitigation |
| --- | --- | --- |
| SAM latency is too high for "live" use | Med | Measure it on day 0. Use motion gating, parallel concept calls, and lower JPEG resolution (960×540). Use the local catch-all for instant reaction while SAM refines the result. |
| Corporate or guest Wi‑Fi blocks phone ↔ laptop traffic | Med | Use the phone hotspot or DroidCam over USB. |
| API key or credits are not active | Low–Med | Claim them **tonight**. Ask in Discord `#questions` before Nov 3 ends. |
| Smart plug does not arrive | Med | Buy one locally today. If that fails, use Tier B (ESP32) or show DryRun **clearly labeled**, and emphasize the camera verifier. |
| Calibration drifts (phone bumped) | Med | Run the ArUco drift check every 5 s, show the degraded state, and allow one-click recalibration. |
| Mask quality is poor on dark fabric or under low light | Med | Add lighting, use attribute prompts ("gray blanket"), and rely on the catch-all fallback. |
| Live demo fails during recording | Med | Use **replay mode** with recorded frames and real API calls, and keep several takes. |
| Scope creep | **High** | Respect Gate 2. Stretch goals start only after the README and video are done. |
| Disqualification for unclear hackathon work | Low | List in the README that all code was written Oct 27–Nov 4. Commit often with meaningful messages. |

---

## 13. MVP vs. stretch (cut from the bottom)

**Must-have (wins the "it works" test):**
- [ ] Phone feed → SAM 3.1 masks → inches with error bars
- [ ] Clearance zone from a Muse Wonder-reviewed manual rule
- [ ] Muse Spark hazard JSON shown with reasoning
- [ ] Deterministic state machine → plug OFF → **2-signal confirmation**
- [ ] Visible degraded state when the camera is lost (fail-safe)
- [ ] Validation results table + three-minute video + public repository

**Should-have:**
- [ ] Muse Spark open-vocabulary inventory → SAM fan-out
- [ ] Catch-all UNKNOWN blobs
- [ ] Wonder incident report
- [ ] Cost meter
- [ ] Replay mode

**Stretch:**
- [ ] SAM video tracking
- [ ] Voice queries (Muse Voice Transcribe)
- [ ] Glimmer offline mode
- [ ] Browser-based phone camera page
- [ ] ntfy push
- [ ] Evening summary
- [ ] Person and pet awareness

---

## 14. Submission checklist (copy into the Cerebral Valley form)

- [ ] Track: **Indie**. Theme: **Push the Frontier** (*"understand and control a physical system"*).
- [ ] Public repository, accessible while logged out, with the README listing **what was built during the hackathon**.
- [ ] Three-minute video, accessible without login, that highlights the features built during the event.
- [ ] Meta models used: SAM 3.1, Muse Spark 1.3, Muse Wonder 1.0 (+ any stretch models).
- [ ] Team members are listed. Only assets you have rights to are used; use your own manual PDF link or a public link, not a copied file, if the license is unclear.
- [ ] Safety disclaimer is visible in the README and video.
- [ ] Submitted by 8 p.m. ET Nov 4. Confirmation screenshot saved.

---

## 15. Decisions to make tonight

1. **Actuation tier:** Do you have an ESP32 with IR parts, and does your heater have a remote? If not, use **Tier A (smart plug)**, which is recommended either way.
2. **Heater model:** Find its PDF manual now. This affects the clearance rule and the LED ROI.
3. **Phone mount spot:** Choose a place that gives an overhead view of the heater plus about 4 ft around it.
4. **Network:** Use home Wi‑Fi, the phone hotspot, or USB (DroidCam). Test this first.
