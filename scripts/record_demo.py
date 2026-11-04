"""Record the simulated scenario for the static GitHub Pages demo.

    python scripts/record_demo.py                 # writes web/demo-data/

Runs the real engine (geometry, state machine, plug, verification, incident report) against the simulated scene on a
fake clock, so the recording is fast and repeatable. The dashboard replays it in `npm run build:demo`: state messages,
events, incidents, and the annotated camera frames. Nothing here touches a camera, a plug, or the network.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from three_feet.config import REPO_ROOT, Settings  # noqa: E402
from three_feet.engine import Engine  # noqa: E402
from three_feet.perception.wonder import ClearanceRule  # noqa: E402
from three_feet.safety.actuator import DryRunPlug  # noqa: E402
from three_feet.sim import SimPerception, SimSource, SimWorld  # noqa: E402

DEFAULT_OUT = REPO_ROOT / "web" / "demo-data"
TICK_S = 0.1
FRAME_S = 1 / 3
STATE_S = 0.25
FRAME_WIDTH = 640
JPEG_QUALITY = 72


class FakeClock:
    """Starts at noon local time, so every clock in the recording (frame stamps, log, report) agrees."""

    def __init__(self) -> None:
        self.t = datetime(2026, 11, 4, 12, 0, 0).timestamp()

    def __call__(self) -> float:
        return self.t


class SyncExecutor:
    def submit(self, fn, *args, **kwargs):
        fn(*args, **kwargs)

    def shutdown(self, **kwargs) -> None:
        pass


class Done(Exception):
    """The requested recording length was reached."""


class Recorder:
    def __init__(self, out_dir: Path, max_seconds: float | None = None) -> None:
        self.out_dir, self.max_seconds = out_dir, max_seconds
        self.clock = FakeClock()
        plug = DryRunPlug(settle_s=0.6)
        self.plug = plug
        self.world = SimWorld(plug, clock=self.clock)
        calib = self.world.calibration()
        self.engine = Engine(
            SimSource(self.world), SimPerception(self.world), plug, Settings(_env_file=None), calib=calib,
            rule=ClearanceRule(36, 36, 36, 36, "default"), probe=self.world.indicator_probe(calib), mode="sim",
            clock=self.clock, events_dir=None)
        self.engine._workers.shutdown()
        self.engine._workers = SyncExecutor()
        self.engine.plug.stop()
        self.frames: list[str] = []
        self.states: list[dict] = []
        self.t0 = 0.0
        self._next_frame = 0.0
        self._next_state = 0.0

    # -- stepping ---------------------------------------------------------------------------------
    def _tick(self) -> None:
        self.clock.t += TICK_S
        self.engine.plug.refresh()
        self.engine.tick()
        elapsed = self.clock.t - self.t0
        if elapsed + 1e-9 >= self._next_state:
            self._next_state += STATE_S
            self.states.append({"t": round(elapsed, 2), "msg": json.loads(json.dumps(self.engine.state_message, default=str))})
        if elapsed + 1e-9 >= self._next_frame:
            self._next_frame += FRAME_S
            self._save_frame()
        if self.max_seconds is not None and elapsed >= self.max_seconds:
            raise Done

    def _save_frame(self) -> None:
        image = cv2.imdecode(np.frombuffer(self.engine.latest_jpeg, np.uint8), cv2.IMREAD_COLOR)
        h, w = image.shape[:2]
        image = cv2.resize(image, (FRAME_WIDTH, round(h * FRAME_WIDTH / w)), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
        assert ok
        name = f"frames/{len(self.frames):04d}.jpg"
        (self.out_dir / name).write_bytes(buf.tobytes())
        self.frames.append(name)

    def wait(self, seconds: float) -> None:
        for _ in range(round(seconds / TICK_S)):
            self._tick()

    def wait_for(self, cond, timeout: float) -> None:
        for _ in range(round(timeout / TICK_S)):
            if cond():
                return
            self._tick()

    def _off(self) -> bool:
        return self.engine.sm.state.value.startswith("OFF_")

    def human_reset(self) -> None:
        self.wait_for(lambda: self.engine.reset(), 10)
        self.engine.log("sim", "SIMULATED: a person reset the system and restored heater power")

    # -- the scenario: blanket shutoff, near miss, unknown object ------------------------------------
    def script(self) -> None:
        w = self.world
        self.wait(4)
        w.place("blanket", "near")
        self.wait_for(self._off, 45)
        self.wait(5)
        w.place("blanket", "home")
        self.wait_for(lambda: w.at_target("blanket"), 15)
        self.wait(2)
        if self._off():
            self.human_reset()
        self.wait(4)
        w.place("box", "watch")
        self.wait(8)
        w.place("box", "home")
        self.wait(5)
        w.place("shoe", "near")
        self.wait_for(self._off, 45)
        self.wait(5)
        w.place("shoe", "home")
        self.wait(2)
        if self._off():
            self.human_reset()
        self.wait(6)

    def record(self) -> dict:
        shutil.rmtree(self.out_dir / "frames", ignore_errors=True)
        (self.out_dir / "frames").mkdir(parents=True, exist_ok=True)
        self.engine.tick()  # first tick: the opening state and events exist at t = 0
        self.t0 = self.clock.t
        try:
            self.script()
        except Done:
            pass
        duration = round(self.clock.t - self.t0, 2)
        events = [{"t": round(e["t"] - self.t0, 2), "event": {k: v for k, v in e.items() if k != "t"}}
                  for e in self.engine.events_since(0, 500)]
        incidents = []
        for inc in self.engine.incidents.values():
            d = inc.to_dict()
            d["started_t"] = round(d["started_t"] - self.t0, 2)
            d["ended_t"] = None if d["ended_t"] is None else round(d["ended_t"] - self.t0, 2)
            d["events"] = []
            incidents.append(d)
        self.engine.stop()
        return {"version": 1, "start_t": self.t0, "duration_s": duration, "frame_interval_s": round(FRAME_S, 4),
                "frames": self.frames, "states": self.states, "events": events, "incidents": incidents}


def record(out_dir: Path = DEFAULT_OUT, max_seconds: float | None = None) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    recording = Recorder(out_dir, max_seconds).record()
    (out_dir / "recording.json").write_text(json.dumps(recording, separators=(",", ":")), encoding="utf-8")
    return recording


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--seconds", type=float, default=None, help="stop early (for testing)")
    args = ap.parse_args()
    rec = record(args.out, args.seconds)
    size = sum(p.stat().st_size for p in args.out.rglob("*") if p.is_file())
    print(f"Recorded {rec['duration_s']} s: {len(rec['frames'])} frames, {len(rec['states'])} states, "
          f"{len(rec['events'])} events, {len(rec['incidents'])} incidents -> {args.out} ({size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
