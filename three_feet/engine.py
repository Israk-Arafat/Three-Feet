"""The live loop: frames -> perception -> inches -> safety state machine -> plug -> verification -> UI state.

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
from .perception.tracking import PerceivedObject
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
from .safety.verifier import IndicatorProbe
from .sources import FrameSource, MjpegHttpSource

PERSON_LABELS = {"person", "people", "man", "woman", "child", "human"}


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
    outcome: str | None = None  # confirmed | unconfirmed
    ended_t: float | None = None
    report: str | None = None
    report_source: str | None = None  # muse-wonder | template

    def to_dict(self) -> dict:
        return {
            "id": self.id, "started_t": self.started_t, "ended_t": self.ended_t, "reason": self.reason,
            "outcome": self.outcome, "report": self.report, "report_source": self.report_source,
            "events": self.events,
        }


@dataclass
class MeasuredObject:
    obj: PerceivedObject
    m: Measurement
    status: str


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
        probe: IndicatorProbe | None = None,
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
        self.settings, self.calib, self.rule, self.probe = settings, calib, rule, probe
        self.wonder, self.meter = wonder, meter or CostMeter(settings.budget_usd_per_hour)
        self.mode, self.demo, self.source_label = mode, demo, source_label
        self.clock, self.tick_hz = clock, tick_hz
        self.sm = SafetyStateMachine(replace(SafetyConfig.from_settings(settings), require_indicator=probe is not None))
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
        if probe is None:
            self.log("system", "Heater power-light check not configured: shutoff confirmation uses watts only (1/2)")

    # -- lifecycle ----------------------------------------------------------------------
    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="engine", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        self.plug.stop()
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
                            s.mask_edge_px, s.parallax_in)
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
                explained = np.zeros(cmap.slack.shape, bool)
                for o in perceived:
                    explained |= o.mask
                self._catchall_objs = [
                    PerceivedObject(f"u{i}", "unknown object", d.mask, "high",
                                    "Not recognized — treated as high hazard.", "catchall")
                    for i, d in enumerate(catchall.unknown_blobs(frame.image, explained), 1)
                ]
            measured = []
            for o in [*perceived, *self._catchall_objs]:
                m = cmap.measure(o.mask)
                if m is not None:
                    measured.append(MeasuredObject(o, m, m.status(self.settings.watch_margin_in)))
            self._measured = measured
        elif not camera_ok or not calibration_ok:
            self._measured = []

        status = self.plug.status
        power_w = status.power_w if status.reachable else None
        if power_w is not None and t - self._last_power_sample >= 0.25:
            self._power_history.append(round(power_w, 1))
            self._last_power_sample = t
        indicator = self.probe.is_on(frame.image) if (self.probe and camera_ok and frame is not None) else None

        zone_objects = tuple(
            ZoneObject(mo.obj.track_id, mo.obj.label, mo.m.slack_in, mo.m.err_in,
                       mo.obj.hazard if mo.obj.hazard in ("high", "medium", "low") else "high",  # type: ignore[arg-type]
                       mo.obj.label.lower() in PERSON_LABELS)
            for mo in self._measured
        )
        obs = Observation(t, zone_objects, camera_ok, calibration_ok, self.perception.api_ok, power_w, indicator)
        decision = self.sm.step(obs)
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
                    where = (f"{mo.m.distance_in:.1f} ± {mo.m.err_in:.1f} in from {mo.m.side}, at the zone edge"
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
                                      if d.t - e["t"] <= 120 and e["kind"] in ("warn", "watch", "degraded", "rule")]
                    self._open_incident = inc
                    self.incidents[inc.id] = inc
                closest = self._closest
                extra = (f"; closest {closest['distance_in']:.1f} in from {closest['side']} "
                         f"(needs {closest['required_in']:.0f} in)" if closest else "")
                self.log("shutoff", f"Power cut sent to plug ({self.switch.name}): {d.reason}{extra}",
                         {"incident": self._open_incident.id, "closest": closest})
                self._workers.submit(self._turn_off)
            elif action is Action.CONFIRMED:
                n = sum(1 for v in d.signals.values() if v)
                self.log("confirmed", f"Heater OFF — confirmed by {n}/2 independent signals", {"signals": d.signals})
                self._close_incident("confirmed")
            elif action is Action.ALARM:
                self.log("alarm", "Shutoff NOT confirmed after retries — check the heater now", {"signals": d.signals})
                self._close_incident("unconfirmed")
            elif action is Action.DEGRADED_ALERT:
                self.log("degraded", "Degraded: " + ", ".join(d.degraded).replace("_", " ").lower(),
                         {"degraded": list(d.degraded)})
            elif action is Action.POWER_RESTORED:
                self.log("alarm", "Heater switched back on without a reset; monitoring resumed",
                         {"signals": d.signals})

    def _track_closest(self, d: Decision) -> None:
        """Closest approach while warning, so the shutoff log and report cite the worst moment, not the first."""
        if d.state in (State.CLEAR, State.WATCH) and not d.actions:
            self._closest = None
            return
        if d.state is not State.WARN and Action.TURN_OFF not in d.actions:
            return
        violating = [mo for mo in self._measured if mo.status == "violation"]
        if not violating:
            return
        mo = min(violating, key=lambda x: x.m.slack_in)
        if self._closest is None or mo.m.slack_in < self._closest["slack_in"]:
            self._closest = {"label": mo.obj.label, "distance_in": round(mo.m.distance_in, 1),
                             "err_in": round(mo.m.err_in, 1), "side": mo.m.side,
                             "required_in": mo.m.required_in, "slack_in": round(mo.m.slack_in, 1)}

    def _turn_off(self) -> None:
        try:
            self.switch.turn_off()
            self.plug.refresh()
        except Exception as exc:
            self.log("error", f"Plug command failed: {type(exc).__name__}: {exc}")

    def _close_incident(self, outcome: str) -> None:
        inc = self._open_incident
        if inc is None:
            return
        inc.outcome, inc.ended_t = outcome, self.clock()
        self._open_incident = None
        self._workers.submit(self._write_report, inc)

    def _write_report(self, inc: Incident) -> None:
        if self.wonder is not None:
            try:
                inc.report = self.wonder.incident_report(inc.events, self.rule)
                inc.report_source = "muse-wonder"
            except Exception as exc:
                self.log("error", f"Incident report failed: {type(exc).__name__}: {exc}")
        if inc.report is None:
            inc.report, inc.report_source = self._template_report(inc), "template"
        self.log("report", f"Incident report ready ({inc.report_source})", {"incident": inc.id})

    def _template_report(self, inc: Incident) -> str:
        when = datetime.fromtimestamp(inc.started_t).strftime("%I:%M:%S %p").lstrip("0")
        shutoff = next((e for e in inc.events if e["kind"] == "shutoff"), None)
        closest = (shutoff or {}).get("data", {}).get("closest")
        if closest:
            what = (f"a {closest['label']} came within {closest['distance_in']:.1f} ± {closest['err_in']:.1f} in of "
                    f"the heater's {closest['side']}, where the rule requires {closest['required_in']:.0f} in")
        else:
            what = inc.reason
        outcome = ("Shutoff was confirmed by independent signals." if inc.outcome == "confirmed"
                   else "Shutoff could NOT be confirmed; a person was alerted to check the heater.")
        return f"At {when}, {what}. Power was cut ({inc.reason}). {outcome}"

    def reset(self) -> bool:
        zone_clear = all(mo.status != "violation" for mo in self._measured) and self.state_message.get(
            "camera", {}).get("healthy", False)
        ok = self.sm.reset(zone_clear)
        if ok:
            self._prev_state = self.sm.state
            self.log("reset", "Reset by user — monitoring resumed. Three Feet never turns the heater on; "
                              "switch it on yourself when the zone is clear.")
            if self.on_reset:
                self.on_reset()
        return ok

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

    def _fetch_camera_info(self) -> None:
        s = self.settings
        try:
            r = httpx.get(f"{self._phone_base()}/info.json", verify=s.camera_verify_tls, timeout=3,
                          auth=(s.camera_user, s.camera_password) if s.camera_user else None)
            data = r.json()
            self.camera_info = {"battery_pct": _find_number(data, ("battery",)),
                                "wifi_dbm": _find_number(data, ("rssi", "signal", "wifi"))}
        except Exception:
            pass

    def phone_streaming(self, on: bool) -> bool:
        base, s = self._phone_base(), self.settings
        if base is None:
            return False
        try:
            r = httpx.post(f"{base}/control/{'start' if on else 'stop'}", verify=s.camera_verify_tls, timeout=3,
                           auth=(s.camera_user, s.camera_password) if s.camera_user else None)
            return r.status_code < 400
        except httpx.HTTPError:
            return False

    # -- rendering + UI state ---------------------------------------------------------------------
    def _render(self, frame, d: Decision, camera_ok: bool, art) -> None:
        if frame is None:
            canvas = np.full((720, 1280, 3), 30, np.uint8)
        else:
            canvas = frame.image.copy()
        if art is not None:
            watch_c, zone_c, heater_poly = art
            cv2.drawContours(canvas, watch_c, -1, STATUS_BGR["watch"], 2, cv2.LINE_AA)
            cv2.drawContours(canvas, zone_c, -1, STATUS_BGR["violation"], 3, cv2.LINE_AA)
            cv2.polylines(canvas, [heater_poly], True, PAPER_BGR, 2, cv2.LINE_AA)
        for mo in self._measured:
            label = mo.obj.label + ("" if mo.obj.source != "catchall" else " (catch-all)")
            draw_measured(canvas, mo.obj.mask, label, mo.m, mo.status)  # type: ignore[arg-type]
        if not camera_ok:
            canvas = (canvas * 0.35).astype(np.uint8)
            put_label(canvas, "No camera feed: status unknown", (40, canvas.shape[0] // 2), PAPER_BGR, 1.2,
                      bg=STATUS_BGR["violation"])
        stamp = datetime.fromtimestamp(d.t).strftime("%H:%M:%S")
        text = f"{stamp}  {d.banner}" + ("  [SIMULATED]" if self.mode == "sim" else "")
        (tw, _), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.7, 2)
        put_label(canvas, text, (canvas.shape[1] - tw - 20, 34), scale=0.7)
        ok, buf = cv2.imencode(".jpg", canvas, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if ok:
            self.latest_jpeg = buf.tobytes()

    def _message(self, d: Decision, camera_ok: bool, power_w: float | None, status: PlugStatus) -> dict:
        times = list(self._frame_times)
        fps = (len(times) - 1) / (times[-1] - times[0]) if len(times) > 1 and times[-1] > times[0] else 0.0
        objects = sorted(self._measured, key=lambda mo: mo.m.slack_in - mo.m.err_in)
        rule = self.rule
        zone_clear = all(mo.status != "violation" for mo in self._measured)
        return {
            "type": "state",
            "t": d.t,
            "mode": self.mode,
            "state": d.state.value,
            "banner": d.banner,
            "reason": d.reason,
            "countdown_s": None if d.countdown_s is None else round(d.countdown_s, 1),
            "degraded": list(d.degraded),
            "can_reset": d.state in (State.OFF_CONFIRMED, State.OFF_UNCONFIRMED) and zone_clear and camera_ok,
            "objects": [{
                "track_id": mo.obj.track_id, "label": mo.obj.label, "source": mo.obj.source,
                "distance_in": round(mo.m.distance_in, 1), "err_in": round(mo.m.err_in, 1),
                "required_in": mo.m.required_in, "side": mo.m.side, "status": mo.status,
                "hazard": mo.obj.hazard, "why": mo.obj.why, "assessing": mo.obj.assessing,
                "material": mo.obj.extra.get("material"),
            } for mo in objects],
            "rule": {
                "front_in": rule.front_in, "back_in": rule.back_in, "left_in": rule.left_in,
                "right_in": rule.right_in, "source": rule.source, "quote": rule.quote, "page": rule.page,
                "heater_model": rule.heater_model, "approved": rule.approved, "approved_at": rule.approved_at,
            },
            "power": {"reachable": status.reachable, "on": status.on, "watts": power_w,
                      "history": list(self._power_history), "plug": getattr(self.switch, "name", "?")},
            "signals": d.signals,
            "indicator_configured": self.probe is not None,
            "camera": {"fps": round(fps, 1), "healthy": camera_ok, **self.camera_info},
            "calibration": {"configured": self.cmap is not None,
                            "rms_in": round(self.calib.floor.rms_in, 2) if self.calib else None,
                            "drift_px": None if not np.isfinite(self.drift) else round(self.drift, 1),
                            "reference_set": bool(self.catchall and self.catchall.reference is not None)},
            "api": {"ok": self.perception.api_ok, "sam_latency_s": self.perception.last_sam_latency_s,
                    "last_error": self.perception.last_error, "concepts": list(self.perception.concepts),
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
