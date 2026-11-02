"""Deterministic safety state machine.

Principles:
- Models can only make the system more cautious (unknown/uncertain hazard -> "high").
- Missing information is never treated as safe.
- Shutoff is confirmed only by independent signals; power stays off until a human resets.
- Three Feet never turns a heater on.
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
    VERIFYING = "VERIFYING"
    OFF_CONFIRMED = "OFF_CONFIRMED"
    OFF_UNCONFIRMED = "OFF_UNCONFIRMED"


class Action(StrEnum):
    WATCH_ALERT = "WATCH_ALERT"
    WARN_ALERT = "WARN_ALERT"
    TURN_OFF = "TURN_OFF"
    CONFIRMED = "CONFIRMED"
    ALARM = "ALARM"  # shutoff could not be confirmed: a person must check the heater now
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


@dataclass(frozen=True)
class Observation:
    t: float
    objects: tuple[ZoneObject, ...] = ()
    camera_ok: bool = True
    calibration_ok: bool = True
    api_ok: bool = True
    power_w: float | None = None  # None = plug unreachable / unknown
    indicator_on: bool | None = None  # None = no indicator ROI or unreadable


@dataclass(frozen=True)
class SafetyConfig:
    watch_margin_in: float = 6.0
    dwell_s: dict = field(default_factory=lambda: {"high": 10.0, "medium": 20.0, "low": 60.0})
    failsafe_enabled: bool = True
    failsafe_after_s: float = 30.0
    off_power_w: float = 5.0
    verify_timeout_s: float = 5.0
    max_off_retries: int = 2
    require_indicator: bool = True  # need both watts AND indicator light to confirm

    @classmethod
    def from_settings(cls, s) -> "SafetyConfig":
        return cls(
            watch_margin_in=s.watch_margin_in,
            dwell_s={"high": s.dwell_high_s, "medium": s.dwell_medium_s, "low": s.dwell_low_s},
            failsafe_enabled=s.failsafe_enabled,
            failsafe_after_s=s.failsafe_after_s,
            off_power_w=s.off_power_w,
            verify_timeout_s=s.verify_timeout_s,
            max_off_retries=s.max_off_retries,
        )


@dataclass(frozen=True)
class Decision:
    t: float
    state: State
    reason: str
    actions: tuple[Action, ...]
    countdown_s: float | None
    degraded: tuple[str, ...]
    signals: dict  # {"power_off": bool|None, "indicator_off": bool|None}
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
        self._blind_since: float | None = None
        self._verify_started: float | None = None
        self._retries = 0
        self._prev_degraded: set[str] = set()
        self._reason = "Zone clear"

    # -- public API ---------------------------------------------------------------
    def step(self, obs: Observation) -> Decision:
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

        signals = self._signals(obs)
        countdown: float | None = None
        worst: ZoneObject | None = None

        if self.state is State.VERIFYING:
            countdown = self._step_verifying(obs, signals, actions)
        elif self.state is State.OFF_UNCONFIRMED:
            if self._confirmed(signals):
                self._enter(State.OFF_CONFIRMED, "Shutoff confirmed (late)")
                actions.append(Action.CONFIRMED)
        elif self.state is State.OFF_CONFIRMED and signals["power_off"] is not False:
            pass  # latched until a human resets
        else:
            if self.state is State.OFF_CONFIRMED:
                # The plug meter shows power again without a reset: never keep claiming "off".
                self._enter(State.CLEAR, "Heater switched back on without a reset")
                self._warn_since = self._verify_started = None
                self._retries = 0
                actions.append(Action.POWER_RESTORED)
            countdown, worst = self._step_monitoring(obs, blind, actions)

        return Decision(obs.t, self.state, self._reason, tuple(actions), countdown,
                        tuple(degraded), signals, worst)

    def reset(self, zone_clear: bool) -> bool:
        """Human reset after shutoff. Refused unless the zone is currently clear."""
        if self.state not in (State.OFF_CONFIRMED, State.OFF_UNCONFIRMED) or not zone_clear:
            return False
        self._enter(State.CLEAR, "Reset by user")
        self._warn_since = self._verify_started = None
        self._retries = 0
        return True

    # -- internals ----------------------------------------------------------------
    def _step_monitoring(self, obs: Observation, blind: bool, actions: list[Action]):
        cfg = self.cfg
        heater_maybe_on = obs.power_w is None or obs.power_w >= cfg.off_power_w

        if blind:
            # Can't see: keep any running countdown (assume the object is still there).
            if self.state is State.WARN and self._warn_since is not None:
                remaining = self._warn_deadline - obs.t
                if remaining <= 0:
                    self._start_shutoff(obs.t, "Object was in zone and camera view was lost", actions)
                    return cfg.verify_timeout_s, None
                return remaining, None
            if cfg.failsafe_enabled and heater_maybe_on:
                remaining = self._blind_since + cfg.failsafe_after_s - obs.t  # type: ignore[operator]
                if remaining <= 0:
                    self._start_shutoff(obs.t, f"Fail-safe: no reliable view for {cfg.failsafe_after_s:.0f}s", actions)
                    return cfg.verify_timeout_s, None
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
        watching = [o for o in objects if statuses[o.track_id] == "watch"]
        candidates = [o for o in violating if not o.is_person]
        worst = min(objects, key=lambda o: o.slack_in - o.err_in, default=None)

        if candidates:
            if self.state is not State.WARN:
                self._enter(State.WARN, "")
                self._warn_since = obs.t
                actions.append(Action.WARN_ALERT)
            dwell = min(cfg.dwell_s[o.hazard] for o in candidates)
            self._warn_deadline = self._warn_since + dwell  # type: ignore[operator]
            target = min(candidates, key=lambda o: o.slack_in - o.err_in)
            self._reason = f"{target.label} inside clearance zone ({target.hazard} hazard)"
            remaining = self._warn_deadline - obs.t
            if remaining <= 0:
                self._start_shutoff(obs.t, f"{target.label} stayed in zone for {dwell:.0f}s", actions)
                return cfg.verify_timeout_s, target
            return remaining, target

        self._warn_since = None
        if violating or watching:
            if self.state is not State.WATCH:
                actions.append(Action.WATCH_ALERT)
            self._enter(State.WATCH, "Object near clearance zone" if watching else "Person in zone")
        else:
            self._enter(State.CLEAR, "Zone clear")
        return None, worst

    def _step_verifying(self, obs: Observation, signals: dict, actions: list[Action]) -> float | None:
        if self._confirmed(signals):
            n = sum(1 for v in signals.values() if v)
            self._enter(State.OFF_CONFIRMED, f"Shutoff confirmed ({n}/2 signals)")
            actions.append(Action.CONFIRMED)
            return None
        elapsed = obs.t - self._verify_started  # type: ignore[operator]
        if elapsed < self.cfg.verify_timeout_s:
            return self.cfg.verify_timeout_s - elapsed
        if self._retries < self.cfg.max_off_retries:
            self._retries += 1
            self._verify_started = obs.t
            actions.append(Action.TURN_OFF)
            self._reason = f"Not confirmed; retry {self._retries}/{self.cfg.max_off_retries}"
            return self.cfg.verify_timeout_s
        self._enter(State.OFF_UNCONFIRMED, "Shutoff NOT confirmed — check the heater now")
        actions.append(Action.ALARM)
        return None

    def _start_shutoff(self, t: float, reason: str, actions: list[Action]) -> None:
        self._enter(State.VERIFYING, reason)
        self._verify_started = t
        self._retries = 0
        actions.append(Action.TURN_OFF)

    def _signals(self, obs: Observation) -> dict:
        return {
            "power_off": None if obs.power_w is None else obs.power_w < self.cfg.off_power_w,
            "indicator_off": None if obs.indicator_on is None else not obs.indicator_on,
        }

    def _confirmed(self, signals: dict) -> bool:
        if signals["power_off"] is not True:
            return False
        return signals["indicator_off"] is True or not self.cfg.require_indicator

    @staticmethod
    def _degraded(obs: Observation) -> list[str]:
        flags = []
        if not obs.camera_ok:
            flags.append("CAMERA_LOST")
        if not obs.calibration_ok:
            flags.append("CALIBRATION_DRIFT")
        if not obs.api_ok:
            flags.append("API_DOWN")
        if obs.power_w is None:
            flags.append("PLUG_UNREACHABLE")
        return flags

    def _enter(self, state: State, reason: str) -> None:
        self.state = state
        if reason:
            self._reason = reason
