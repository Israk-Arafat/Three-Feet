"""Power switches. Deliberately no `turn_on`: Three Feet never turns a heater on."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol

import httpx


@dataclass(frozen=True)
class PlugStatus:
    reachable: bool
    on: bool | None
    power_w: float | None
    metering: bool = True  # False: the plug reports its relay state but cannot measure watts


class PowerSwitch(Protocol):
    name: str

    def turn_off(self) -> None: ...

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


class ShellyPlug:
    """Shelly Gen2+ plug (e.g. Shelly Plug US) via its local RPC API."""

    name = "shelly"

    def __init__(self, host: str, switch_id: int = 0, timeout_s: float = 2.0) -> None:
        self.base = host if host.startswith("http") else f"http://{host}"
        self.switch_id = switch_id
        self._client = httpx.Client(timeout=timeout_s)

    def turn_off(self) -> None:
        r = self._client.get(f"{self.base}/rpc/Switch.Set", params={"id": self.switch_id, "on": "false"})
        r.raise_for_status()

    def status(self) -> PlugStatus:
        try:
            r = self._client.get(f"{self.base}/rpc/Switch.GetStatus", params={"id": self.switch_id})
            r.raise_for_status()
            d = r.json()
            return PlugStatus(True, bool(d.get("output")), float(d.get("apower", 0.0)))
        except (httpx.HTTPError, ValueError):
            return PlugStatus(False, None, None)


def make_switch(kind: str, host: str = "", name: str = "") -> PowerSwitch:
    if kind == "dryrun":
        return DryRunPlug()
    if kind == "shelly":
        if not host:
            raise ValueError("Set PLUG_HOST for the Shelly plug")
        return ShellyPlug(host)
    if kind == "cync":
        from .cync_plug import CyncPlug

        return CyncPlug(device_name=name)
    raise ValueError(f"Unknown PLUG_KIND {kind!r} (dryrun | shelly | cync)")
