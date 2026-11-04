"""Deterministic safety state machine.

Principles:
- Models can only make the system more cautious (unknown/uncertain hazard -> "high").
- Missing information is never treated as safe.
- An OFF request latches monitoring until a human resets or the plug reports a later restart.
- Restoring power requires an explicit, guarded user reset in the engine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

from ..geometry import classify

HazardLevel = Literal["high", "medium", "low"]


class State(StrEnum):
    CLEAR = "CLEAR"
    WATCH = "WATCH"
    WARN = "WARN"
    OFF_REQUESTED = "OFF_REQUESTED"


class Action(StrEnum):
    WATCH_ALERT = "WATCH_ALERT"
    WARN_ALERT = "WARN_ALERT"
    TURN_OFF = "TURN_OFF"
    DEGRADED_ALERT = "DEGRADED_ALERT"
    POWER_RESTORED = "POWER_RESTORED"  # heater drew power again while latched off; monitoring resumed


@dataclass(frozen=True)
class ZoneObject:
    track_id: str
    label: str
    slack_in: float  # distance minus required clearance; negative = inside zone
    err_in: float
    hazard: HazardLevel = "high"
    is_person: bool = False
    awaiting_model: bool = False


@dataclass(frozen=True)
class Observation:
    t: float
    objects: tuple[ZoneObject, ...] = ()
    camera_ok: bool = True
    calibration_ok: bool = True
    api_ok: bool = True
    power_w: float | None = None  # None = no meter reading (unreachable, or a plug without metering)
    plug_on: bool | None = None  # relay state the plug reports; used when it has no power meter


@dataclass(frozen=True)
class SafetyConfig:
    watch_margin_in: float = 2.0
    dwell_s: dict = field(default_factory=lambda: {"high": 10.0, "medium": 20.0, "low": 60.0})
    failsafe_enabled: bool = True
    failsafe_after_s: float = 30.0
    off_power_w: float = 5.0

    @classmethod
    def from_settings(cls, s) -> "SafetyConfig":
        return cls(
            watch_margin_in=s.watch_margin_in,
            dwell_s={"high": s.dwell_high_s, "medium": s.dwell_medium_s, "low": s.dwell_low_s},
            failsafe_enabled=s.failsafe_enabled,
            failsafe_after_s=s.failsafe_after_s,
            off_power_w=s.off_power_w,
        )


@dataclass(frozen=True)
class Decision:
    t: float
    state: State
    reason: str
    actions: tuple[Action, ...]
    countdown_s: float | None
    degraded: tuple[str, ...]
    worst: ZoneObject | None

    @property
    def banner(self) -> str:
        if self.degraded and self.state in (State.CLEAR, State.WATCH, State.WARN):
            return "UNKNOWN — not safe"
        return self.state.value


class SafetyStateMachine:
    def __init__(self, config: SafetyConfig | None = None) -> None:
        self.cfg = config or SafetyConfig()
        self.state = State.CLEAR
        self._warn_since: float | None = None
        self._warn_deadline = 0.0
        self._violation_since: dict[str, float] = {}
        self._warn_track: str | None = None
        self._blind_since: float | None = None
        self._plug_was_off = False
        self._prev_degraded: set[str] = set()
        self._reason = "Zone clear"

    # -- public API ---------------------------------------------------------------
    def step(self, obs: Observation, *, allow_power_restored: bool = True) -> Decision:
        actions: list[Action] = []
        degraded = self._degraded(obs)
        if set(degraded) - self._prev_degraded:
            actions.append(Action.DEGRADED_ALERT)
        self._prev_degraded = set(degraded)

        blind = not obs.camera_ok or not obs.calibration_ok
        if blind:
            self._blind_since = obs.t if self._blind_since is None else self._blind_since
        else:
            self._blind_since = None

        countdown: float | None = None
        worst: ZoneObject | None = None

        if self.state is State.OFF_REQUESTED:
            reported_off = self._reported_off(obs)
            if reported_off is True:
                self._plug_was_off = True
            elif reported_off is False and self._plug_was_off and allow_power_restored:
                self._enter(State.CLEAR, "Plug reports power on again without a reset")
                self._warn_since = None
                self._plug_was_off = False
                actions.append(Action.POWER_RESTORED)
                countdown, worst = self._step_monitoring(obs, blind, actions)
        else:
            countdown, worst = self._step_monitoring(obs, blind, actions)

        return Decision(obs.t, self.state, self._reason, tuple(actions), countdown,
                        tuple(degraded), worst)

    def reset(self, zone_clear: bool) -> bool:
        """Human reset after shutoff. Refused unless the zone is currently clear."""
        if self.state is not State.OFF_REQUESTED or not zone_clear:
            return False
        self._enter(State.CLEAR, "Reset by user")
        self._warn_since = None
        self._violation_since.clear()
        self._warn_track = None
        self._plug_was_off = False
        return True

    # -- internals ----------------------------------------------------------------
    def request_shutoff(self, t: float, reason: str) -> None:
        """Latch the OFF request after a violation or cancelled power restoration."""
        self._enter(State.OFF_REQUESTED, reason)
        self._plug_was_off = False
        self._violation_since.clear()
        self._warn_track = None

    def _step_monitoring(self, obs: Observation, blind: bool, actions: list[Action]):
        cfg = self.cfg
        heater_maybe_on = self._reported_off(obs) is not True

        if blind:
            # Can't see: keep any running countdown (assume the object is still there).
            if self.state is State.WARN and self._warn_since is not None:
                remaining = self._warn_deadline - obs.t
                if remaining <= 0:
                    self._start_shutoff(obs.t, "Object was in zone and camera view was lost", actions)
                    return None, None
                return remaining, None
            if cfg.failsafe_enabled and heater_maybe_on:
                remaining = self._blind_since + cfg.failsafe_after_s - obs.t  # type: ignore[operator]
                if remaining <= 0:
                    self._start_shutoff(obs.t, f"Fail-safe: no reliable view for {cfg.failsafe_after_s:.0f}s", actions)
                    return None, None
                self._reason = "Camera view unavailable — fail-safe countdown"
                return remaining, None
            self._reason = "Camera view unavailable"
            return None, None

        objects = [
            o if obs.api_ok else ZoneObject(o.track_id, o.label, o.slack_in, o.err_in, "high", o.is_person)
            for o in obs.objects
        ]
        statuses = {o.track_id: classify(o.slack_in, o.err_in, cfg.watch_margin_in) for o in objects}
        violating = [o for o in objects if statuses[o.track_id] == "violation"]
        watching = [o for o in objects if statuses[o.track_id] in ("watch", "uncertain")]
        pending = [o for o in violating if not o.is_person and (o.awaiting_model or not obs.api_ok)]
        candidates = [o for o in violating if not o.is_person and not o.awaiting_model and obs.api_ok]
        worst = min(objects, key=lambda o: (statuses[o.track_id] != "violation", o.slack_in), default=None)
        eligible = {o.track_id for o in candidates}
        self._violation_since = {key: since for key, since in self._violation_since.items() if key in eligible}
        for obj in candidates:
            self._violation_since.setdefault(obj.track_id, obs.t)

        if candidates:
            target = min(candidates, key=lambda o: self._violation_since[o.track_id] + cfg.dwell_s[o.hazard])
            if self.state is not State.WARN or self._warn_track != target.track_id:
                self._enter(State.WARN, "")
                actions.append(Action.WARN_ALERT)
            self._warn_track = target.track_id
            self._warn_since = self._violation_since[target.track_id]
            dwell = cfg.dwell_s[target.hazard]
            self._warn_deadline = self._warn_since + dwell
            self._reason = f"{target.label} inside clearance zone ({target.hazard} hazard)"
            remaining = self._warn_deadline - obs.t
            if remaining <= 0:
                self._start_shutoff(obs.t, f"{target.label} stayed in zone for {dwell:.0f}s", actions)
                return None, target
            return remaining, target

        self._warn_since = None
        self._warn_track = None
        if violating or watching:
            if self.state is not State.WATCH:
                actions.append(Action.WATCH_ALERT)
            uncertain = any(statuses[o.track_id] == "uncertain" for o in watching)
            self._enter(State.WATCH, "Movement in zone; waiting for SAM identification" if pending
                        else "Outside zone; distance uncertain" if uncertain
                        else "Object near clearance zone" if watching else "Person in zone")
        else:
            self._enter(State.CLEAR, "Zone clear")
        return None, worst

    def _start_shutoff(self, t: float, reason: str, actions: list[Action]) -> None:
        self.request_shutoff(t, reason)
        actions.append(Action.TURN_OFF)

    def _reported_off(self, obs: Observation) -> bool | None:
        """Basic plug telemetry, used for fail-safe timing and detecting an external restart."""
        if obs.power_w is not None:
            return obs.power_w < self.cfg.off_power_w
        return None if obs.plug_on is None else not obs.plug_on

    @staticmethod
    def _degraded(obs: Observation) -> list[str]:
        flags = []
        if not obs.camera_ok:
            flags.append("CAMERA_LOST")
        if not obs.calibration_ok:
            flags.append("CALIBRATION_DRIFT")
        if not obs.api_ok:
            flags.append("API_DOWN")
        if obs.power_w is None and obs.plug_on is None:
            flags.append("PLUG_UNREACHABLE")
        return flags

    def _enter(self, state: State, reason: str) -> None:
        self.state = state
        if reason:
            self._reason = reason
