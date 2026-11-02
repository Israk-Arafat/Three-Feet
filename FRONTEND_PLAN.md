# Three Feet — Frontend Plan

> **Purpose:** The dashboard is what judges see for most of the three-minute video. Its job is to make the
> *perceive → reason → act → verify* loop visible and believable at a glance, and to make each Meta model's
> contribution clear.
> **Budget:** About 7 focused hours on Tue Nov 3. Build the backend API contract in parallel ([§4](#4-backend-contract-build-first)).

**Status (Nov 2):** The dashboard, backend engine, and API are implemented and tested against the simulator (`python -m three_feet.app --source sim --demo`):
- **Built:** live view, banner states with countdown and UNKNOWN, object, rule, and power cards, the Muse Wonder rule upload-and-approve dialog, timeline, incident report, health bar with spend, sound/voice/browser alerts, and demo fault-injection controls.
- **Still open:** setup wizard steps 1–3 and 5 (use `scripts/calibrate.py` meanwhile), browser-side SAM rendering, ntfy push, and a response cache for photo replay.

---

## 1. Decisions

| Decision | Choice | Why |
| --- | --- | --- |
| Framework | **Vite + React + TypeScript** in `web/` | Fast rebuilds and a static build that FastAPI can serve. No Next.js: server-side rendering and routing add moving parts and nothing for one local dashboard. Node 24 is already installed. |
| Styling | **Tailwind CSS** | Build a dark, high-contrast UI quickly without design work. |
| Live video | **Annotated MJPEG from the backend** (`<img src="/stream.mjpg">`) | The Python code already draws masks, the zone, distance lines, and labels (`three_feet/overlay.py`). One source of truth, with no mask decoding in the browser. |
| Live state | **One WebSocket** (`/ws`) pushing a `StateMessage` at about 5 Hz, plus `event` messages | Simple and real-time. Reconnect with backoff, and show **"Backend disconnected — UNKNOWN"** when it drops. |
| Commands | Small REST endpoints (`/api/...`) | Reset, calibration steps, rule extraction, and demo fault injection. |
| State library | None. Use React `useReducer` and context. | One state stream doesn't need Redux or Zustand. |
| Charts | A hand-rolled SVG sparkline for watts (about 30 lines) | Avoids a charting dependency for one small chart. |
| Served from | FastAPI `StaticFiles` mount of `web/dist` at `/` | One process and one URL for the demo: `http://localhost:8000`. |

**Stretch:** Render SAM masks in the browser with Meta's `@meta-sam/react` / `@meta-sam/graphics`. This adds an extra Meta-tooling point, but only do it after the MVP works.

---

## 2. Screen layout (designed for 1920×1080 screen recording)

```text
┌──────────────────────────────────────────────────────────────────────────────────────────────┐
│ ▲ THREE FEET     [ WARN · Fleece blanket 14.2 in from front · shutting off in 7 s ◔ ]  12:04:31 │  ← state banner
├───────────────────────────────────────────────────────────┬──────────────────────────────────┤
│                                                           │ CLOSEST OBJECT        [SAM 3.1]  │
│                                                           │  Fleece blanket                  │
│                LIVE VIEW (annotated MJPEG)                │  14.2 in ± 1.1   needs 36 (front)│
│     red zone outline · masks · distance line · labels     │  Hazard: HIGH        [Muse Spark]│
│                                                           │  "Polyester fleece ignites and   │
│                                                           │   melts readily."                │
│                                                           ├──────────────────────────────────┤
│                                                           │ CLEARANCE RULE      [Muse Wonder]│
│                                                           │  Front 36 · Back 36 · Sides 36 in│
│                                                           │  Lasko 754200 manual, p.4        │
│                                                           │  "Keep combustible materials..." │
│                                                           │  ✓ Approved by you 8:12 PM       │
│                                                           ├──────────────────────────────────┤
│                                                           │ HEATER POWER                     │
│                                                           │  1,482 W  ▁▂▇▇▇▇▇▇  plug: ON     │
│                                                           │  Verify: ☐ watts < 5  ☐ light off│
├───────────────────────────────────────────────────────────┴──────────────────────────────────┤
│ TIMELINE  12:04:24 WARN blanket entered zone · 12:04:21 WATCH towel near · 12:03:10 Reset   │
│ INCIDENT REPORT [Muse Wonder]  "At 12:04:24 a fleece blanket was 14 in from the front..."   │
├──────────────────────────────────────────────────────────────────────────────────────────────┤
│ Camera 9.8 fps · battery 84% · Wi-Fi −52 dBm │ Calib RMS 0.6 in │ SAM 0.9 s │ $0.41 / $5 per hr  │  ← health bar
└──────────────────────────────────────────────────────────────────────────────────────────────┘
```

### Banner states (use color **plus** text and icon; never color alone)

| State | Look | Text |
| --- | --- | --- |
| `CLEAR` | Green | "Zone clear" |
| `WATCH` | Amber | "Towel 4 in outside the zone" |
| `WARN` | Red, slow pulse, **countdown ring** | "Blanket 14.2 in from front · shutting off in 7 s" |
| `VERIFYING` | Red with a spinner | "Power cut sent · verifying (watts, light)" |
| `OFF_CONFIRMED` | Blue, **latched** | "Heater OFF — confirmed 2/2 signals · Reset when clear" |
| `OFF_UNCONFIRMED` | Flashing red and white, alarm | "Shutoff NOT confirmed — CHECK THE HEATER NOW" |
| Any state with `degraded` items | Gray diagonal stripes over the banner | "UNKNOWN — not safe · camera lost · fail-safe in 22 s" |

### Model attribution chips

Put a small chip such as **[SAM 3.1]**, **[Muse Spark]**, or **[Muse Wonder]** next to every output a model produced. This puts the "Use of Meta AI tooling" category (25% of the score) on screen without narration.

---

## 3. Pages and panels

1. **Dashboard** (`/`): the layout above.
2. **Setup wizard** (`/setup`): six steps, each with a ✓. Every step also has a CLI fallback that already exists.

   | Step | UI | Backend | CLI fallback |
   | --- | --- | --- | --- |
   | 1 Camera | Live preview, fps, battery, and Wi‑Fi from `/info.json` | `GET /api/camera` | `scripts/view_stream.py` |
   | 2 Floor | Detected marker IDs, RMS in inches, **Recalibrate** button | `POST /api/calibration/markers` | `scripts/calibrate.py` |
   | 3 Heater | Drag a box on the snapshot → **SAM 3.1 box prompt** returns the mask → confirm 4 base corners and the front | `POST /api/calibration/heater-box`, `POST /api/calibration/heater-corners` | `scripts/calibrate.py` (click corners) |
   | 4 Rule | Upload the manual PDF → Muse Wonder extraction → show **quote + page** → **Approve** | `POST /api/rules/extract`, `POST /api/rules/approve` | `scripts/extract_rules.py` |
   | 5 Power light | Drag the ROI → capture "on" → turn the heater off → capture "off" → shows the threshold | `POST /api/calibration/indicator` | — |
   | 6 Empty zone | "Clear the zone, then click" → saves the catch-all reference | `POST /api/calibration/reference` | — |

3. **Demo controls** (hidden; only shown with `?demo=1`). These are clearly labeled **FAULT INJECTION**:
   - **Cut camera / restore:** phone `POST /control/stop` and `/control/start`
   - **Simulate API outage:** the engine treats the API as down
   - **DryRun plug:** stuck relay, unreachable, or back on (only when `PLUG_KIND=dryrun`)

---

## 4. Backend contract (build first)

New modules: `three_feet/engine.py` (the loop) and `three_feet/app.py` (FastAPI). The engine runs on its own thread. The frame reader is already threaded. Perception runs on a worker pool, so a slow SAM call never blocks the safety loop.

```text
engine tick (5 Hz):
  frame = source.read()                    → camera_ok = source.healthy and frame is fresh
  drift check every 5 s                    → calibration_ok
  motion = MotionGate.update(...)
  if motion and perception idle and not over budget:  submit perception job (Spark inventory ≤ every 5 s, SAM fan-out ≤ 1 Hz)
  objects = latest SAM detections + catch-all blobs (local, every tick) → ClearanceMap.measure → ZoneObject list
  plug = switch.status(); indicator = IndicatorProbe.is_on(frame)
  decision = state_machine.step(Observation(...))
  for action in decision.actions: TURN_OFF → switch.turn_off(); alerts → event log
  publish StateMessage to WebSocket clients; render overlay frame for /stream.mjpg
```

### Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/stream.mjpg` | Annotated live view (multipart MJPEG, about 10 fps, JPEG quality 80) |
| GET | `/api/snapshot.jpg` | Latest raw frame, used for setup clicks |
| WS | `/ws` | `StateMessage` at 5 Hz; `EventMessage` on every event |
| GET | `/api/state` | Same as one `StateMessage`, for debugging |
| POST | `/api/reset` | Human reset; returns 409 unless the state is `OFF_*` and the zone is clear |
| GET | `/api/events?limit=50` | Recent event log entries (JSONL-backed) |
| POST | `/api/incidents/{id}/report` | Generate or fetch the Muse Wonder incident report |
| GET, POST | `/api/rules`, `/api/rules/extract` (multipart PDF), `/api/rules/approve` | Read the rule, extract one from a manual, approve it |
| GET, POST | `/api/calibration/*` | Setup wizard steps |
| POST | `/api/demo/{camera,api,plug}` | Fault injection; returns 404 unless the server runs with `--demo` |

### `StateMessage` (mirror it in `web/src/types.ts`)

```ts
type State = "CLEAR" | "WATCH" | "WARN" | "VERIFYING" | "OFF_CONFIRMED" | "OFF_UNCONFIRMED";

interface StateMessage {
  type: "state";
  t: number;                       // epoch seconds
  state: State;
  banner: string;                  // "UNKNOWN — not safe" when degraded
  reason: string;
  countdown_s: number | null;
  degraded: ("CAMERA_LOST" | "CALIBRATION_DRIFT" | "API_DOWN" | "PLUG_UNREACHABLE")[];
  can_reset: boolean;
  objects: {
    track_id: string; label: string; source: "sam" | "catchall";
    distance_in: number; err_in: number; required_in: number; side: string;
    status: "clear" | "watch" | "violation";
    hazard: "high" | "medium" | "low"; why: string | null;      // from Muse Spark
  }[];
  rule: { front_in: number; back_in: number; left_in: number; right_in: number;
          source: "manual" | "default"; quote: string; page: number | null;
          heater_model: string; approved: boolean; approved_at: string | null };
  power: { reachable: boolean; on: boolean | null; watts: number | null; history: number[] };
  signals: { power_off: boolean | null; indicator_off: boolean | null };
  camera: { fps: number; healthy: boolean; battery_pct: number | null; wifi_dbm: number | null };
  calibration: { rms_in: number; drift_px: number };
  api: { ok: boolean; sam_latency_s: number | null; spend: { total_usd: number; last_hour_usd: number; budget_usd_per_hour: number } };
}

interface EventMessage { type: "event"; id: string; t: number; kind: string; text: string; data?: unknown }
```

---

## 5. Build without hardware: **demo mode first**

Run `python -m three_feet.app --source data/photos --plug dryrun --demo`.

- `FolderSource(loop=True)` plays your test photos as a slow "video", and the `DryRunPlug` simulates watts.
- **Cache SAM and Spark responses by frame hash** (`runs/cache/`), so replaying the same photos costs nothing after the first pass. The same cache powers a guaranteed-to-work **backup demo recording**.
- The whole UI can be built and styled before the phone is mounted or the smart plug arrives.

---

## 6. Alerts (the parts the video needs to hear)

- **Sound:** Use the WebAudio beep pattern: a soft chirp for WATCH, a repeating triple beep for WARN, and a continuous siren for `OFF_UNCONFIRMED`.
  - Browsers block audio until the user interacts, so show an **"Enable alerts"** button on first load.
- **Speech:** Use `speechSynthesis`, for example: *"Fleece blanket is fourteen inches from the heater. Turning off in ten seconds."* Then: *"Heater is off. Confirmed by power and light."*
- **Browser notifications:** Use the Notification API for WARN, shutoff, and degraded states. These are useful when the dashboard tab is in the background.
- **Optional:** Send an ntfy push to a second phone. Add this only if time allows.

---

## 7. Project setup steps

```powershell
# from the repo root
npm create vite@latest web -- --template react-ts
cd web
npm install
npm install -D tailwindcss @tailwindcss/vite
```

- **`web/vite.config.ts`:** Add the Tailwind plugin and a dev proxy:
  `server.proxy = { "/api": "http://localhost:8000", "/ws": { target: "ws://localhost:8000", ws: true }, "/stream.mjpg": "http://localhost:8000" }`
- **`.gitignore`:** Add `web/node_modules/` and `web/dist/`. The root `/node_modules` rule doesn't cover `web/`.
- **FastAPI:** `app.mount("/", StaticFiles(directory="web/dist", html=True))`, registered *after* the API routes.
- **Dev loop:** Run `uvicorn three_feet.app:app --reload` in one terminal and `npm run dev` in `web/` in another. Open the Vite URL.
- **Demo or record:** Run `npm run build`, then open only `http://localhost:8000`.

### Component tree

```text
web/src/
  main.tsx, App.tsx              # routes: "/" dashboard, "/setup" wizard
  types.ts                       # StateMessage / EventMessage
  hooks/useLiveState.ts          # WebSocket + reconnect + "backend lost" → UNKNOWN
  hooks/useAlerts.ts             # sound, TTS, notifications keyed on state transitions
  components/
    StateBanner.tsx              # colors, icons, countdown ring
    LiveView.tsx                 # <img src="/stream.mjpg">, onError → "camera feed lost" overlay
    ClosestObjectCard.tsx        # distance ± err, required, side, hazard chip, Spark "why"
    RuleCard.tsx                 # values, quote, page, approved badge
    PowerCard.tsx                # watts, sparkline, plug state, 2-signal checklist
    Timeline.tsx                 # event log
    IncidentReport.tsx           # Muse Wonder text
    HealthBar.tsx                # camera / calibration / API latency / spend
    ModelChip.tsx                # [SAM 3.1] [Muse Spark] [Muse Wonder]
    ResetButton.tsx              # enabled only when can_reset
    DemoControls.tsx             # ?demo=1 only
  setup/ CameraStep.tsx FloorStep.tsx HeaterStep.tsx RuleStep.tsx IndicatorStep.tsx ReferenceStep.tsx
```

---

## 8. Schedule (Tue Nov 3)

| Time | Task | Done when |
| --- | --- | --- |
| 8:00–10:00 | **Backend:** `engine.py` + `app.py` with `/ws`, `/stream.mjpg`, `/api/state`, `/api/reset`, and demo mode with photo folder, DryRun plug, and response cache | `curl /api/state` works, and the stream shows overlays from the photos |
| 10:00–10:30 | Vite + Tailwind scaffold, proxy, `types.ts`, `useLiveState` | The page shows the raw state JSON |
| 10:30–12:00 | `StateBanner` (all states + countdown), `LiveView`, `ClosestObjectCard`, `ModelChip` | A blanket photo shows WARN → countdown → VERIFYING → OFF_CONFIRMED with the DryRun plug |
| 12:00–1:00 | Physical plug + indicator verifier wired in (separate track) | Real watts show in `PowerCard` |
| 1:00–2:00 | `RuleCard`, `PowerCard` + sparkline + 2-signal checklist, `ResetButton` | The full happy path is visible on one screen |
| 2:00–3:00 | `Timeline`, `IncidentReport` (Wonder), `HealthBar` with spend | Incident text appears about 5 s after confirmation |
| 3:00–4:00 | `useAlerts` (sound, TTS, notifications), degraded/UNKNOWN styling, `DemoControls` | "Cut camera" → gray UNKNOWN banner → fail-safe countdown |
| 4:00–5:00 | Setup wizard steps 3 (heater box → SAM) and 4 (manual → Wonder → Approve). Use the CLI for the other steps. | Both show their model chips |
| 5:00–5:30 | Video polish: font sizes ≥ 20 px, 1080p layout check, remove debug UI | Screenshot looks good at 1920×1080 |

**Cut line** (drop these from the bottom if behind schedule): the setup wizard (the CLI scripts already cover setup), TTS, sparkline, and the client-side SAM rendering stretch goal.

**Never cut:** the live view, the banner with countdown, the closest-object card with ± error, the rule card with quote and page, the power card with 2/2 verification, the UNKNOWN degraded state, and the Reset button.

---

## 9. Done checklist

- [ ] Every state in [§2](#banner-states-use-color-plus-text-and-icon-never-color-alone) renders correctly. Force each one with demo mode.
- [ ] Killing the backend → UI shows **UNKNOWN**, never a stale green "CLEAR".
- [ ] Losing the stream image → the live view shows "camera feed lost" over the last frame, dimmed.
- [ ] Reset is disabled until the zone is clear, and the server also enforces it (409).
- [ ] Every model output has its model chip.
- [ ] Spend meter is visible. No API calls happen while the scene is idle (motion gate).
- [ ] Readable in a 1080p screen recording, viewed at 50% size.
- [ ] Works when served from FastAPI after `npm run build`, without the Vite dev server.
