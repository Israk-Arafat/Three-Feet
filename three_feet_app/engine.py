"""The live loop: frames -> perception -> inches -> safety state machine -> plug -> UI state.

Runs on its own thread at `tick_hz`. Slow work (model calls, plug HTTP, incident reports) happens on other
threads so a stalled API or plug can never stall the safety loop.
"""

from __future__ import annotations

import itertools
import json
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Any, Callable, Protocol

import cv2
import httpx
import numpy as np

from .calibration import CalibrationFile, detect_markers, drift_px, marker_centers
from .config import RUNS_DIR, Settings
from .geometry import ClearanceMap, Clearances, Measurement
from .overlay import PAPER_BGR, STATUS_BGR, draw_measured, put_label
from .perception.catchall import MotionGate, ZoneChangeDetector
from .perception.meta_client import CostMeter
from .perception.tracking import PerceivedObject, is_person_label
from .perception.wonder import ClearanceRule, WonderClient
from .safety.actuator import PlugStatus, PowerSwitch
from .safety.state_machine import (
    Action,
    Decision,
    Observation,
    SafetyConfig,
    SafetyStateMachine,
    State,
    ZoneObject,
)
from .sources import FrameSource, MjpegHttpSource

# A heater always has a cord, and walls and trim are part of the room: neither is something placed near the heater.
CORD_WORDS = {"cord", "cords", "cable", "cables", "wire", "wires"}
FIXTURE_WORDS = {"wall", "walls", "drywall", "baseboard", "baseboards", "skirting", "molding", "moulding", "trim",
                 "outlet", "outlets", "socket", "sockets", "switch", "door", "doors", "doorframe", "window",
                 "windows", "windowsill", "ceiling"}


def is_ignored_item(obj: PerceivedObject) -> bool:
    """Cords and fixed room fixtures are never measured. Only named SAM detections qualify, never catch-all blobs."""
    if obj.source != "sam":
        return False
    return bool(set(obj.label.lower().replace("-", " ").split()) & (CORD_WORDS | FIXTURE_WORDS))


class Perception(Protocol):
    fault_api: bool
    last_sam_latency_s: float | None
    last_error: str | None
    concepts: list[str]

    @property
    def api_ok(self) -> bool: ...

    def on_frame(self, frame, t: float, motion: bool, zone_view: np.ndarray | None = None) -> None: ...

    def current(self) -> list[PerceivedObject]: ...

    def shutdown(self) -> None: ...


class NullPerception:
    """Used when no API key is configured: geometry and the catch-all still run; the API is reported down."""

    fault_api = False
    last_sam_latency_s = None
    last_error = "MODEL_API_KEY not set"
    concepts: list[str] = []
    api_ok = False

    def on_frame(self, *args, **kwargs) -> None:
        pass

    def current(self) -> list[PerceivedObject]:
        return []

    def shutdown(self) -> None:
        pass


class PlugMonitor:
    """Polls plug status on its own thread so a slow plug never blocks the loop."""

    def __init__(self, switch: PowerSwitch, period_s: float = 0.5) -> None:
        self.switch = switch
        self.period_s = period_s
        self.status = PlugStatus(False, None, None)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="plug-monitor", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.status = self.switch.status()
            except Exception:
                self.status = PlugStatus(False, None, None)
            self._stop.wait(self.period_s)

    def refresh(self) -> None:
        try:
            self.status = self.switch.status()
        except Exception:
            self.status = PlugStatus(False, None, None)

    def stop(self) -> None:
        self._stop.set()


@dataclass
class Incident:
    id: str
    started_t: float
    reason: str
    events: list[dict] = field(default_factory=list)
    outcome: str | None = None  # requested | failed
    ended_t: float | None = None
    report: str | None = None
    report_source: str | None = None  # muse-wonder | template
    report_generation: int = 0
    trigger: dict | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id, "started_t": self.started_t, "ended_t": self.ended_t, "reason": self.reason,
            "outcome": self.outcome, "report": self.report, "report_source": self.report_source,
            "events": self.events,
            "trigger": self.trigger,
        }


@dataclass
class MeasuredObject:
    obj: PerceivedObject
    m: Measurement
    status: str


class ConnectionChangeBlocked(RuntimeError):
    """The camera or plug cannot be swapped right now."""


class Engine:
    def __init__(
        self,
        source: FrameSource,
        perception: Perception,
        switch: PowerSwitch,
        settings: Settings,
        *,
        calib: CalibrationFile | None,
        rule: ClearanceRule,
        wonder: WonderClient | None = None,
        meter: CostMeter | None = None,
        mode: str = "live",  # live | sim | replay
        demo: bool = False,
        source_label: str = "",
        clock: Callable[[], float] = time.time,
        tick_hz: float = 10.0,
        events_dir: Path | None = RUNS_DIR / "events",
    ) -> None:
        self.source, self.perception, self.switch = source, perception, switch
        self.settings, self.calib, self.rule = settings, calib, rule
        self.wonder, self.meter = wonder, meter or CostMeter(settings.budget_usd_per_hour)
        self.mode, self.demo, self.source_label = mode, demo, source_label
        self.clock, self.tick_hz = clock, tick_hz
        self.sm = SafetyStateMachine(SafetyConfig.from_settings(settings))
        self._state_lock = threading.Lock()
        self._reset_lock = threading.Lock()
        self._reset_pending = False
        self._reset_cancelled = False
        self._off_status_lock = threading.Lock()
        self._off_io_lock = threading.Lock()
        self._off_pending = 0
        self._off_error: str | None = None
        self._last_observation: Observation | None = None
        self.plug = PlugMonitor(switch)

        self.fault_camera = False
        self.latest_jpeg: bytes | None = None
        self.latest_raw: np.ndarray | None = None
        self.state_message: dict = {}
        self.drift = 0.0
        self.camera_info: dict = {"battery_pct": None, "wifi_dbm": None}

        self._events: deque[dict] = deque(maxlen=500)
        self._event_ids = itertools.count(1)
        self._events_lock = threading.Lock()
        self._events_path = None
        if events_dir is not None:
            events_dir.mkdir(parents=True, exist_ok=True)
            self._events_path = events_dir / f"{datetime.now():%Y%m%d}.jsonl"
        self.incidents: dict[str, Incident] = {}
        self._open_incident: Incident | None = None
        self._incident_ids = itertools.count(1)

        self._geom_lock = threading.Lock()
        self.cmap: ClearanceMap | None = None
        self._zone_art: tuple | None = None
        self.motion_gate: MotionGate | None = None
        self.catchall: ZoneChangeDetector | None = None
        self._build_geometry()

        self._last_index = -1
        self._frame_times: deque[float] = deque(maxlen=30)
        self._last_drift_check = -1e9
        self._calibration_ok = True
        self._measured: list[MeasuredObject] = []
        self._closest: dict | None = None
        self._catchall_objs: list[PerceivedObject] = []
        self._prev_state = self.sm.state
        self._power_history: deque[float] = deque(maxlen=120)
        self._last_power_sample = 0.0
        self._last_info_poll = -1e9
        self._workers = ThreadPoolExecutor(max_workers=3, thread_name_prefix="engine-io")
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.on_reset: Callable[[], None] | None = None  # sim hook

        self.log("system", f"Three Feet started ({mode}{', demo controls on' if demo else ''})",
                 {"source": source_label, "plug": getattr(switch, "name", "?")})

    # -- lifecycle ----------------------------------------------------------------------
    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="engine", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        self.plug.stop()
        close = getattr(self.switch, "close", None)
        if callable(close):
            close()
        self.perception.shutdown()
        self.source.close()
        self._workers.shutdown(wait=False, cancel_futures=True)

    def _run(self) -> None:
        period = 1.0 / self.tick_hz
        while not self._stop.is_set():
            start = time.perf_counter()
            try:
                self.tick()
            except Exception as exc:  # keep the loop alive; surface the error
                self.log("error", f"Engine tick failed: {type(exc).__name__}: {exc}")
                time.sleep(0.5)
            self._stop.wait(max(0.0, period - (time.perf_counter() - start)))

    # -- geometry -------------------------------------------------------------------------
    def _build_geometry(self) -> None:
        s = self.settings
        if not (self.calib and self.calib.heater):
            with self._geom_lock:
                self.cmap = None
            return
        cmap = ClearanceMap(self.calib.floor, self.calib.heater, Clearances.from_rule(self.rule),
                            s.mask_edge_px, s.parallax_in, self.calib.distance_err_frac)
        heater_px = np.zeros(cmap.slack.shape, np.uint8)
        cv2.fillPoly(heater_px, [cmap.heater_polygon_px()], 1)
        heater_px = cv2.dilate(heater_px, np.ones((25, 25), np.uint8)).astype(bool)
        art = (cmap.zone_contours(s.watch_margin_in), cmap.zone_contours(0.0), cmap.heater_polygon_px())
        old_ref = self.catchall.reference if self.catchall else None
        with self._geom_lock:
            self.cmap = cmap
            self._zone_art = art
            self.motion_gate = MotionGate(region=cmap.zone_mask(s.watch_margin_in + 12))
            self.catchall = ZoneChangeDetector(cmap.zone_mask(s.watch_margin_in), ignore_mask=heater_px)
            self.catchall.reference = old_ref

    def set_calibration(self, calib: CalibrationFile) -> None:
        """Apply a reviewed setup without restarting the camera or energizing the plug."""
        with self._state_lock:
            if self._reset_pending:
                raise RuntimeError("Wait for power restoration to finish before changing the heater setup")
            self.calib = calib
            self.catchall = None  # A reference from the previous view cannot explain this floor plane.
            self._build_geometry()
            self._calibration_ok = True
            self._last_drift_check = -1e9
            self.drift = 0.0
            self._last_observation = None
            self._measured = []
            self._catchall_objs = []
        self.log("setup", "Heater base calibration applied from the dashboard",
                 {"width_in": calib.heater.width_in, "depth_in": calib.heater.depth_in,
                  "method": calib.method, "distance_error_pct": round(calib.distance_err_frac * 100, 1)})

    def set_rule(self, rule: ClearanceRule) -> None:
        self.rule = rule
        self._build_geometry()
        self.log("rule", f"Clearance rule set: front {rule.front_in:.0f}, back {rule.back_in:.0f}, "
                         f"left {rule.left_in:.0f}, right {rule.right_in:.0f} in ({rule.source})",
                 {"page": rule.page, "approved": rule.approved})

    def set_reference(self) -> bool:
        """Save the current frame as the empty-zone reference for the catch-all detector."""
        if self.latest_raw is None or self.catchall is None:
            return False
        self.catchall.set_reference(self.latest_raw)
        self.log("setup", "Empty-zone reference captured for the catch-all detector")
        return True

    # -- connections -----------------------------------------------------------------------------
    def replace_source(self, source: FrameSource, label: str) -> None:
        """Swap the camera live. The new view starts with no detections and no catch-all reference."""
        with self._state_lock:
            if self._reset_pending:
                raise ConnectionChangeBlocked("Wait for power restoration to finish before changing the camera")
            old, self.source, self.source_label = self.source, source, label
            if self.mode == "replay":
                self.mode = "live"
            self._last_index = -1
            self.latest_raw = None
            self._frame_times.clear()
            self.catchall = None  # A reference from the previous camera cannot explain the new view.
            self._build_geometry()
            self._calibration_ok = True
            self._last_drift_check = -1e9
            self.drift = 0.0
            self._last_observation = None
            self._measured = []
            self._catchall_objs = []
            self.camera_info = {"battery_pct": None, "wifi_dbm": None}
            self._last_info_poll = -1e9
            reset_perception = getattr(self.perception, "reset", None)
            if callable(reset_perception):
                reset_perception()
        old.close()

    def check_plug_change(self) -> None:
        """Keep queued power commands tied to the plug they were requested for."""
        if self._reset_pending or self._off_pending:
            raise ConnectionChangeBlocked("Wait for the current power command to finish before changing the plug.")

    def replace_switch(self, switch: PowerSwitch) -> None:
        """Swap the power plug live."""
        with self._state_lock:
            self.check_plug_change()
            old_switch, old_monitor = self.switch, self.plug
            self.switch, self.plug = switch, PlugMonitor(switch)
            self._power_history.clear()
        old_monitor.stop()
        close = getattr(old_switch, "close", None)
        if callable(close) and old_switch is not switch:
            close()

    # -- events -----------------------------------------------------------------------------
    def log(self, kind: str, text: str, data: dict | None = None) -> dict:
        event = {"type": "event", "id": next(self._event_ids), "t": self.clock(), "kind": kind, "text": text}
        if data:
            event["data"] = data
        with self._events_lock:
            self._events.append(event)
            if self._open_incident is not None:
                self._open_incident.events.append(event)
        if self._events_path is not None:
            try:
                with open(self._events_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(event, default=str) + "\n")
            except OSError:
                pass
        return event

    def events_since(self, last_id: int, limit: int = 200) -> list[dict]:
        with self._events_lock:
            return [e for e in self._events if e["id"] > last_id][-limit:]

    # -- main tick ----------------------------------------------------------------------------
    def tick(self) -> Decision:
        t = self.clock()
        frame = self.source.read()
        camera_ok = frame is not None and self.source.healthy and not self.fault_camera
        new_frame = camera_ok and frame.index != self._last_index
        with self._geom_lock:
            cmap, art, gate, catchall = self.cmap, self._zone_art, self.motion_gate, self.catchall

        if new_frame:
            self._last_index = frame.index
            self._frame_times.append(t)
            self.latest_raw = frame.image
        self._poll_camera_info(t)

        calibration_ok = cmap is not None
        if cmap is not None and frame is not None and frame.image.shape[:2] != cmap.slack.shape:
            calibration_ok = False
        if calibration_ok and new_frame and self.calib and self.calib.marker_ref_px and t - self._last_drift_check >= 5:
            self._last_drift_check = t
            self.drift = drift_px(self.calib.marker_ref_px, marker_centers(detect_markers(frame.image)))
            self._calibration_ok = self.drift <= 4.0
        if self.calib and self.calib.marker_ref_px:
            calibration_ok = calibration_ok and self._calibration_ok

        if new_frame and calibration_ok:
            motion = gate.update(frame.image, t) if gate else True
            zone_view = frame.image.copy()
            cv2.drawContours(zone_view, art[1], -1, (0, 0, 255), 3)
            self.perception.on_frame(frame, t, motion, zone_view)
            perceived = self.perception.current()
            if catchall is not None and catchall.reference is not None:
                had_pending = bool(self._catchall_objs)
                explained = np.zeros(cmap.slack.shape, bool)
                for o in perceived if self.perception.api_ok else []:
                    explained |= o.mask
                self._catchall_objs = [
                    PerceivedObject(f"u{i}", "unclassified movement", d.mask, "high",
                                    "Waiting for SAM to identify this change before starting a shutoff countdown.", "catchall")
                    for i, d in enumerate(catchall.unknown_blobs(frame.image, explained), 1)
                ]
                if self._catchall_objs and not had_pending:
                    request_check = getattr(self.perception, "request_check", None)
                    if callable(request_check):
                        request_check()
            measured = []
            for o in [*perceived, *self._catchall_objs]:
                if is_ignored_item(o):
                    continue
                m = cmap.measure(o.mask)
                if m is not None:
                    status = m.status(self.settings.watch_margin_in)
                    if status == "violation":
                        if is_person_label(o.label) or o.extra.get("on_person"):
                            status = "watch"
                        elif o.source == "catchall" or o.misses > 0 or not self.perception.api_ok:
                            status = "pending"
                    measured.append(MeasuredObject(o, m, status))
            self._measured = measured
        elif not camera_ok or not calibration_ok:
            self._measured = []

        status = self.plug.status
        power_w = status.power_w if (status.reachable and status.metering) else None
        plug_on = status.on if status.reachable else None
        if power_w is not None and t - self._last_power_sample >= 0.25:
            self._power_history.append(round(power_w, 1))
            self._last_power_sample = t

        zone_objects = tuple(
            ZoneObject(mo.obj.track_id, mo.obj.label, mo.m.slack_in, mo.m.err_in,
                       mo.obj.hazard if mo.obj.hazard in ("high", "medium", "low") else "high",  # type: ignore[arg-type]
                       is_person_label(mo.obj.label) or bool(mo.obj.extra.get("on_person")),
                       awaiting_model=mo.obj.source == "catchall" or mo.obj.misses > 0)
            for mo in self._measured
        )
        obs = Observation(t, zone_objects, camera_ok, calibration_ok, self.perception.api_ok, power_w,
                          plug_on)
        with self._state_lock:
            self._last_observation = obs
            if self._reset_pending and not self._reset_cancelled and not self._reset_view_ok():
                self._reset_cancelled = True
                self.sm.request_shutoff(t, "Power restoration cancelled: the clearance zone or monitoring changed")
                self.log("reset_cancelled", "Monitoring changed during power restoration; requesting OFF immediately")
                self._queue_off()
            decision = self.sm.step(obs, allow_power_restored=not self._reset_pending and not self._off_pending)
            self._handle(decision)
        self._render(frame, decision, camera_ok, art)
        self.state_message = self._message(decision, camera_ok, power_w, status)
        return decision

    # -- actions --------------------------------------------------------------------------------
    def _handle(self, d: Decision) -> None:
        if d.state is not self._prev_state:
            self.log("state", f"{self._prev_state.value} → {d.state.value}: {d.reason}",
                     {"from": self._prev_state.value, "to": d.state.value})
            self._prev_state = d.state
        worst = d.worst
        self._track_closest(d)
        for action in d.actions:
            if action is Action.WARN_ALERT and worst:
                mo = next((x for x in self._measured if x.obj.track_id == worst.track_id), None)
                detail = ""
                if mo:
                    where = (f"{mo.m.distance_in:.1f} in from {mo.m.side}, at the zone edge"
                             if mo.m.distance_in >= mo.m.required_in else f"{mo.m.distance_in:.1f} in from {mo.m.side}")
                    detail = f" — {where} (needs {mo.m.required_in:.0f} in)"
                self.log("warn", f"{worst.label.capitalize()} inside clearance zone{detail}",
                         {"track_id": worst.track_id, "hazard": worst.hazard,
                          **({"distance_in": round(mo.m.distance_in, 1), "err_in": round(mo.m.err_in, 1),
                              "required_in": mo.m.required_in, "side": mo.m.side} if mo else {})})
            elif action is Action.WATCH_ALERT:
                self.log("watch", "Object near the clearance zone")
            elif action is Action.TURN_OFF:
                if self._open_incident is None:
                    inc = Incident(f"i{next(self._incident_ids)}", d.t, d.reason)
                    with self._events_lock:
                        inc.events = [e for e in self._events
                                      if d.t - e["t"] <= 120 and (
                                          e["kind"] in ("degraded", "rule")
                                          or e["kind"] == "warn" and worst is not None
                                          and e.get("data", {}).get("track_id") == worst.track_id)]
                    inc.trigger = dict(self._closest) if self._closest else None
                    self._open_incident = inc
                    self.incidents[inc.id] = inc
                closest = self._closest
                extra = (f"; closest {closest['distance_in']:.1f} in from {closest['side']} "
                         f"(needs {closest['required_in']:.0f} in)" if closest else "")
                self.log("shutoff", f"Power off requested ({self.switch.name}): {d.reason}{extra}",
                         {"incident": self._open_incident.id, "closest": closest})
                self._queue_off(self._open_incident)
            elif action is Action.DEGRADED_ALERT:
                self.log("degraded", "Degraded: " + ", ".join(d.degraded).replace("_", " ").lower(),
                         {"degraded": list(d.degraded)})
            elif action is Action.POWER_RESTORED:
                self.log("alarm", "Plug reports power on again without a reset; monitoring resumed")

    def _track_closest(self, d: Decision) -> None:
        """Closest approach while warning, so the shutoff log and report cite the worst moment, not the first."""
        if d.state in (State.CLEAR, State.WATCH):
            self._closest = None
            return
        if d.state is not State.WARN and Action.TURN_OFF not in d.actions:
            return
        violating = [mo for mo in self._measured if mo.status == "violation"
                     and d.worst is not None and mo.obj.track_id == d.worst.track_id]
        if not violating:
            return
        mo = min(violating, key=lambda x: x.m.slack_in)
        if (self._closest is None or self._closest.get("track_id") != mo.obj.track_id
                or mo.m.slack_in < self._closest["slack_in"]):
            self._closest = {"track_id": mo.obj.track_id, "label": mo.obj.label, "source": mo.obj.source,
                             "distance_in": round(mo.m.distance_in, 1),
                             "err_in": round(mo.m.err_in, 1), "side": mo.m.side,
                             "required_in": mo.m.required_in, "slack_in": round(mo.m.slack_in, 1)}

    def _queue_off(self, incident: Incident | None = None) -> None:
        with self._off_status_lock:
            self._off_pending += 1
        self._workers.submit(self._turn_off, incident, True)

    def _turn_off(self, incident: Incident | None = None, queued: bool = False) -> None:
        if not queued:
            with self._off_status_lock:
                self._off_pending += 1
        outcome = "failed"
        try:
            with self._off_io_lock:
                for attempt in range(self.settings.off_command_retries + 1):
                    try:
                        self.switch.turn_off()
                        self.plug.refresh()
                        self._off_error = None
                        outcome = "requested"
                        self.log("off_request", "OFF command sent to plug", {"attempt": attempt + 1})
                        break
                    except Exception as exc:
                        self._off_error = f"{type(exc).__name__}: {exc}"
                        self.log("error", f"OFF command failed (attempt {attempt + 1}): {self._off_error}")
            if incident is not None:
                self._close_incident(incident, outcome)
        finally:
            with self._off_status_lock:
                self._off_pending -= 1

    def _close_incident(self, inc: Incident, outcome: str) -> None:
        with self._events_lock:
            inc.outcome, inc.ended_t = outcome, self.clock()
            inc.report_generation += 1
            inc.report = inc.report_source = None
        self._open_incident = None
        self._workers.submit(self._write_report, inc)

    def _write_report(self, inc: Incident) -> None:
        with self._events_lock:
            snapshot = replace(inc, events=list(inc.events))
        report, source = None, None
        if self.wonder is not None:
            try:
                report = self.wonder.incident_report(snapshot.events, self.rule, trigger=snapshot.trigger)
                source = "muse-wonder"
            except Exception as exc:
                self.log("error", f"Incident report failed: {type(exc).__name__}: {exc}")
        if not report:
            report, source = self._template_report(snapshot), "template"
        with self._events_lock:
            if inc.report_generation != snapshot.report_generation:
                return
            inc.report, inc.report_source = report, source
        self.log("report", f"Incident report ready ({source})", {"incident": inc.id})

    def _template_report(self, inc: Incident) -> str:
        when = datetime.fromtimestamp(inc.started_t, ZoneInfo(self.settings.display_timezone)).strftime("%I:%M:%S %p %Z").lstrip("0")
        shutoff = next((e for e in inc.events if e["kind"] == "shutoff"), None)
        closest = inc.trigger or (shutoff or {}).get("data", {}).get("closest")
        if closest:
            article = "an" if closest["label"][:1].lower() in tuple("aeiou") else "a"
            what = (f"{article} {closest['label']} came within {closest['distance_in']:.1f} in of "
                    f"the heater's {closest['side']}, where the rule requires {closest['required_in']:.0f} in")
        else:
            what = inc.reason
        outcome = ("An OFF command was sent to the plug." if inc.outcome == "requested"
                   else "The OFF command failed after retries; check the heater now.")
        return f"At {when}, {what}. {outcome}"

    def _reset_view_ok(self) -> bool:
        """A missing or stale observation never establishes a clear zone."""
        obs = self._last_observation
        return bool(
            obs is not None
            and 0 <= self.clock() - obs.t <= 1.0
            and obs.camera_ok and self.source.healthy and not self.fault_camera
            and obs.calibration_ok and self.calib is not None and self.cmap is not None
            and obs.api_ok and self.perception.api_ok
            and self.plug.status.reachable
            and all(mo.status not in ("violation", "pending") for mo in self._measured)
        )

    def _can_reset(self, *, allow_pending: bool = False) -> bool:
        return bool(
            (allow_pending or not self._reset_pending)
            and not self._off_pending
            and not (self._reset_pending and self._reset_cancelled)
            and self.sm.state is State.OFF_REQUESTED
            and self._reset_view_ok()
        )

    def reset(self) -> bool:
        """Restore power once on user request, then resume monitoring after a fresh clear-zone check."""
        if not self._reset_lock.acquire(blocking=False):
            return False
        try:
            with self._state_lock:
                if not self._can_reset():
                    return False
                self._reset_cancelled = False
                self._reset_pending = True
            try:
                # Network IO stays off the safety-loop thread; never retry an ON command.
                self.switch.turn_on()
                self.plug.refresh()
                with self._state_lock:
                    if not self._can_reset(allow_pending=True):
                        raise RuntimeError("The camera, calibration, plug, API, or clearance zone changed while restoring power")
                    self.sm.reset(True)
                    self._reset_pending = False
                    self._prev_state = self.sm.state
                    self.log("reset", "Reset by user — plug ON command sent; monitoring resumed")
                if self.on_reset:
                    self.on_reset()
                return True
            except Exception as exc:
                # An ON timeout can mean power was restored: request OFF rather than retry ON.
                with self._state_lock:
                    self._reset_cancelled = True
                    self.sm.request_shutoff(self.clock(), "Power restoration failed; requesting OFF")
                self._turn_off()
                self.log("error", f"Reset failed while restoring power: {type(exc).__name__}: {exc}")
                raise RuntimeError(f"Could not restore heater power: {exc}") from exc
            finally:
                self._reset_pending = False
        finally:
            self._reset_lock.release()

    # -- phone camera ------------------------------------------------------------------------------
    def _phone_base(self) -> str | None:
        if isinstance(self.source, MjpegHttpSource):
            url = httpx.URL(self.source.url)
            return f"{url.scheme}://{url.host}:{url.port}" if url.port else f"{url.scheme}://{url.host}"
        return None

    def _poll_camera_info(self, t: float) -> None:
        if t - self._last_info_poll < 10 or self._phone_base() is None:
            return
        self._last_info_poll = t
        self._workers.submit(self._fetch_camera_info)

    def _camera_http(self) -> dict:
        """TLS and credentials of the camera currently in use, so a camera changed in the app keeps working."""
        source = self.source
        auth = getattr(source, "auth", None)
        return {"verify": getattr(source, "verify_tls", self.settings.camera_verify_tls), "auth": auth}

    def _fetch_camera_info(self) -> None:
        try:
            base = self._phone_base()
            if base is None:
                return
            r = httpx.get(f"{base}/info.json", timeout=3, **self._camera_http())
            data = r.json()
            self.camera_info = {"battery_pct": _find_number(data, ("battery",)),
                                "wifi_dbm": _find_number(data, ("rssi", "signal", "wifi"))}
        except Exception:
            pass

    def phone_streaming(self, on: bool) -> bool:
        base = self._phone_base()
        if base is None:
            return False
        try:
            r = httpx.post(f"{base}/control/{'start' if on else 'stop'}", timeout=3, **self._camera_http())
            return r.status_code < 400
        except httpx.HTTPError:
            return False

    # -- rendering + UI state ---------------------------------------------------------------------
    def _render(self, frame, d: Decision, camera_ok: bool, art) -> None:
        # One immutable bundle per tick keeps each viewer's overlays consistent without touching
        # detection, calibration, or safety state. Readers can retain the old bundle while a new
        # frame is published.
        measured = tuple((mo.obj.mask, mo.obj.label, mo.obj.source, mo.m, mo.status) for mo in self._measured)
        snapshot = (frame.image if frame is not None else None, d, camera_ok, art, measured)
        self._view_snapshot = snapshot
        self._view_cache = {}
        self.latest_jpeg = self._compose_view(snapshot, zone=True, base=True, objects=True)

    def render_view(self, *, zone: bool = True, base: bool = True, objects: bool = True) -> bytes | None:
        """Render a viewer's chosen layers; visibility never changes safety observations."""
        if zone and base and objects:
            return self.latest_jpeg
        snapshot = getattr(self, "_view_snapshot", None)
        if snapshot is None:
            return None
        key = (zone, base, objects)
        cached = getattr(self, "_view_cache", {}).get(key)
        if cached is not None and cached[0] is snapshot:
            return cached[1]
        jpg = self._compose_view(snapshot, zone=zone, base=base, objects=objects)
        self._view_cache[key] = (snapshot, jpg)
        return jpg

    def view_geometry(self) -> dict:
        """Camera-space footprint for optional browser zoom, without refitting geometry."""
        with self._geom_lock:
            cmap = self.cmap
        image_size = ([self.latest_raw.shape[1], self.latest_raw.shape[0]]
                      if self.latest_raw is not None else None)
        result = {"configured": False, "image_size": image_size, "base_corners_px": None,
                  "base_width_in": None, "base_depth_in": None}
        if cmap is None:
            return result
        corners = cmap.calib.floor_to_image(cmap.heater.corners())
        if not np.isfinite(corners).all():
            return result
        return {"configured": True, "image_size": list(cmap.calib.image_size),
                "base_corners_px": corners.tolist(), "base_width_in": cmap.heater.width_in,
                "base_depth_in": cmap.heater.depth_in}

    def _compose_view(self, snapshot, *, zone: bool, base: bool, objects: bool) -> bytes | None:
        image, d, camera_ok, art, measured = snapshot
        if image is None:
            canvas = np.full((720, 1280, 3), 30, np.uint8)
        else:
            canvas = image.copy()
        if art is not None:
            watch_c, zone_c, heater_poly = art
            if zone:
                tint = canvas.copy()
                cv2.drawContours(tint, zone_c, -1, STATUS_BGR["violation"], cv2.FILLED)
                canvas = cv2.addWeighted(canvas, 0.88, tint, 0.12, 0)
                cv2.drawContours(canvas, zone_c, -1, STATUS_BGR["violation"], 3, cv2.LINE_AA)
            if base:
                base_color = (228, 205, 75)  # cyan, kept distinct from the red danger zone
                cv2.polylines(canvas, [heater_poly], True, PAPER_BGR, 6, cv2.LINE_AA)
                cv2.polylines(canvas, [heater_poly], True, base_color, 3, cv2.LINE_AA)
                points = heater_poly.reshape(-1, 2)
                for name, point in zip(("FL", "FR", "BR", "BL"), points):
                    xy = tuple(int(v) for v in point)
                    cv2.circle(canvas, xy, 5, PAPER_BGR, -1, cv2.LINE_AA)
                    put_label(canvas, name, (xy[0] + 7, xy[1] - 8), scale=0.4)
                if len(points) == 4:
                    front = (points[0] + points[1]) / 2
                    center = points.mean(axis=0)
                    direction = front - center
                    norm = np.linalg.norm(direction)
                    if norm > 0:
                        end = front + direction / norm * 24
                        cv2.arrowedLine(canvas, tuple(front.astype(int)), tuple(end.astype(int)),
                                        base_color, 3, cv2.LINE_AA, tipLength=0.35)
                        put_label(canvas, "Front", tuple(end.astype(int)), scale=0.4)
        if objects:
            for mask, label, source, measurement, status in measured:
                label = label + ("" if source != "catchall" else " (catch-all)")
                draw_measured(canvas, mask, label, measurement, status)
        if not camera_ok:
            canvas = (canvas * 0.35).astype(np.uint8)
            put_label(canvas, "No camera feed: status unknown", (40, canvas.shape[0] // 2), PAPER_BGR, 1.2,
                      bg=STATUS_BGR["violation"])
        stamp = datetime.fromtimestamp(d.t, ZoneInfo(self.settings.display_timezone)).strftime("%H:%M:%S %Z")
        text = f"{stamp}  {d.banner.replace('_', ' ')}" + ("  [SIMULATED]" if self.mode == "sim" else "")
        (tw, _), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.7, 2)
        put_label(canvas, text, (canvas.shape[1] - tw - 20, 34), scale=0.7)
        ok, buf = cv2.imencode(".jpg", canvas, [cv2.IMWRITE_JPEG_QUALITY, 80])
        return buf.tobytes() if ok else None

    def _message(self, d: Decision, camera_ok: bool, power_w: float | None, status: PlugStatus) -> dict:
        times = list(self._frame_times)
        fps = (len(times) - 1) / (times[-1] - times[0]) if len(times) > 1 and times[-1] > times[0] else 0.0
        priority = {"violation": 0, "pending": 1, "uncertain": 2, "watch": 2, "clear": 3}
        objects = sorted(self._measured, key=lambda mo: (priority[mo.status], mo.m.slack_in))
        rule = self.rule
        return {
            "type": "state",
            "t": d.t,
            "time_zone": self.settings.display_timezone,
            "watch_margin_in": self.settings.watch_margin_in,
            "mode": self.mode,
            "state": d.state.value,
            "banner": d.banner,
            "reason": d.reason,
            "countdown_s": None if d.countdown_s is None else round(d.countdown_s, 1),
            "degraded": list(d.degraded),
            "can_reset": self._can_reset(),
            "reset_pending": self._reset_pending,
            "reset_cancelled": self._reset_pending and self._reset_cancelled,
            "objects": [{
                "track_id": mo.obj.track_id, "label": mo.obj.label, "source": mo.obj.source,
                "distance_in": round(mo.m.distance_in, 1), "err_in": round(mo.m.err_in, 1),
                "required_in": mo.m.required_in, "side": mo.m.side, "status": mo.status,
                "hazard": mo.obj.hazard, "why": mo.obj.why, "assessing": mo.obj.assessing,
                "material": mo.obj.extra.get("material"),
                "is_person": is_person_label(mo.obj.label) or bool(mo.obj.extra.get("on_person")),
            } for mo in objects],
            "rule": {
                "front_in": rule.front_in, "back_in": rule.back_in, "left_in": rule.left_in,
                "right_in": rule.right_in, "source": rule.source, "quote": rule.quote, "page": rule.page,
                "heater_model": rule.heater_model, "approved": rule.approved, "approved_at": rule.approved_at,
                "confidence": rule.confidence, "manual_url": rule.manual_url,
                "default_in": self.settings.default_clearance_in,
            },
            "power": {"reachable": status.reachable, "on": status.on, "watts": power_w, "metering": status.metering,
                      "history": list(self._power_history), "plug": getattr(self.switch, "name", "?")},
            "off_pending": bool(self._off_pending),
            "off_error": self._off_error,
            "camera": {"fps": round(fps, 1), "healthy": camera_ok, **self.camera_info},
            "calibration": {"configured": self.cmap is not None,
                            "method": self.calib.method if self.calib else None,
                            "distance_err_pct": round(self.calib.distance_err_frac * 100) if self.calib else None,
                            "rms_in": round(self.calib.floor.rms_in, 2) if self.calib else None,
                            "drift_px": None if not np.isfinite(self.drift) else round(self.drift, 1),
                            "reference_set": bool(self.catchall and self.catchall.reference is not None)},
            "api": {"ok": self.perception.api_ok, "sam_latency_s": self.perception.last_sam_latency_s,
                    "last_error": self.perception.last_error, "concepts": list(self.perception.concepts),
                    "health_reason": getattr(self.perception, "health_reason", None),
                    "result_age_s": getattr(self.perception, "result_age_s", None),
                    "verification_age_s": getattr(self.perception, "verification_age_s", None),
                    "local_tracking": getattr(self.perception, "local_tracking_active", False),
                    "frame_index": getattr(self.perception, "last_frame_index", None),
                    "spend": self.meter.snapshot()},
            "open_incident": self._open_incident.id if self._open_incident else None,
        }


def _find_number(data: Any, keys: tuple[str, ...]) -> float | None:
    """Best-effort search of a nested JSON object for the first numeric value whose key contains any of `keys`."""
    if isinstance(data, dict):
        for k, v in data.items():
            if any(key in str(k).lower() for key in keys) and isinstance(v, (int, float)) and not isinstance(v, bool):
                return float(v)
        for v in data.values():
            found = _find_number(v, keys)
            if found is not None:
                return found
    elif isinstance(data, list):
        for v in data:
            found = _find_number(v, keys)
            if found is not None:
                return found
    return None
