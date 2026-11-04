"""Power switches. ON is used only by an explicit, guarded user reset."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class PlugStatus:
    reachable: bool
    on: bool | None
    power_w: float | None
    metering: bool = True  # False: the plug reports its relay state but cannot measure watts


class PowerSwitch(Protocol):
    name: str

    def turn_off(self) -> None: ...

    def turn_on(self) -> None: ...

    def status(self) -> PlugStatus: ...


class DryRunPlug:
    """Simulated metering plug for development, tests, and replay. Never present it as a real shutoff."""

    name = "dryrun"

    def __init__(self, load_w: float = 1500.0, settle_s: float = 0.5) -> None:
        self.load_w = load_w
        self.settle_s = settle_s
        self._on = True
        self._off_at: float | None = None
        self.reachable = True
        self.stuck = False  # simulate a welded relay
        self.off_commands = 0
        self.on_commands = 0

    def turn_on(self) -> None:
        if not self.reachable:
            raise ConnectionError("dry-run plug unreachable")
        self.on_commands += 1
        self._on, self._off_at = True, None

    def simulate_power_on(self) -> None:
        """Test/demo helper standing in for a human switching the heater back on."""
        self._on, self._off_at = True, None

    def turn_off(self) -> None:
        if not self.reachable:
            raise ConnectionError("dry-run plug unreachable")
        self.off_commands += 1
        if not self.stuck:
            self._on = False
            self._off_at = time.monotonic()

    def status(self) -> PlugStatus:
        if not self.reachable:
            return PlugStatus(False, None, None)
        settling = self._off_at is not None and time.monotonic() - self._off_at < self.settle_s
        power = self.load_w if (self._on or settling) else 0.0
        return PlugStatus(True, self._on, power)


def make_switch(kind: str, name: str = "") -> PowerSwitch:
    if kind == "dryrun":
        return DryRunPlug()
    if kind == "cync":
        from .cync_plug import CyncPlug

        return CyncPlug(device_name=name)
    raise ValueError(f"Unknown PLUG_KIND {kind!r} (dryrun | cync)")
