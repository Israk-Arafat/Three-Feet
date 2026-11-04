"""GE Cync smart plug driver (cloud) via the reverse-engineered `pycync` library.

The plug has no power meter, so its status reports the relay state only (`metering=False`); the camera's
power-light check is the independent confirmation. Log in once with `scripts/cync_login.py`, which saves
tokens (never the password) to data/cync_token.json. Only one connection per Cync account is allowed:
opening the Cync phone app disconnects this driver for about 10 seconds.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path
from typing import Any

from ..config import CYNC_TOKEN_PATH
from .actuator import PlugStatus

REFRESH_MARGIN_S = 15 * 60
STATE_POLL_S = 5.0


def save_tokens(user, path: Path = CYNC_TOKEN_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "access_token": user.access_token, "refresh_token": user.refresh_token, "authorize": user.authorize,
        "user_id": user.user_id, "expires_at": user.expires_at,
    }, indent=2), encoding="utf-8")


def load_user(path: Path = CYNC_TOKEN_PATH):
    from pycync.user import User

    if not path.exists():
        raise RuntimeError(f"No Cync login found at {path}. Run: python scripts/cync_login.py")
    d = json.loads(path.read_text(encoding="utf-8"))
    return User(d["access_token"], d["refresh_token"], d["authorize"], d["user_id"], expires_at=d["expires_at"])


def device_is_on(device: Any) -> bool | None:
    try:
        value = device.is_on
    except Exception:  # unsupported capability or unknown device type
        return None
    return value if isinstance(value, bool) else None


def pick_device(devices: list, name: str = ""):
    """The device named `name` (case-insensitive), or the only plug on the account."""
    if name:
        matches = [d for d in devices if (getattr(d, "name", "") or "").strip().lower() == name.strip().lower()]
        if not matches:
            raise RuntimeError(f"No Cync device named {name!r}; found: {[getattr(d, 'name', '?') for d in devices]}")
        return matches[0]
    plugs = [d for d in devices if type(d).__name__ == "CyncPlug"]
    if len(plugs) == 1:
        return plugs[0]
    raise RuntimeError("Set PLUG_NAME to the plug's name in the Cync app; found: "
                       f"{[(getattr(d, 'name', '?'), type(d).__name__) for d in devices]}")


class CyncPlug:
    name = "cync"

    def __init__(self, device_name: str = "", token_path: Path = CYNC_TOKEN_PATH, timeout_s: float = 8.0,
                 connect_timeout_s: float = 40.0) -> None:
        self.device_name = device_name
        self.token_path = token_path
        self.timeout_s = timeout_s
        self._cync = None
        self._session = None
        self._device = None
        self._is_on: bool | None = None
        self._last_poll = 0.0
        self.last_error: str | None = None
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, name="cync-loop", daemon=True)
        self._thread.start()
        try:
            asyncio.run_coroutine_threadsafe(self._connect(), self._loop).result(timeout=connect_timeout_s)
        except Exception:
            self.close()
            raise

    # -- PowerSwitch ----------------------------------------------------------------------------
    def turn_off(self) -> None:
        asyncio.run_coroutine_threadsafe(asyncio.wait_for(self._off(), self.timeout_s), self._loop).result(
            timeout=self.timeout_s + 1)

    def turn_on(self) -> None:
        """Restore plug power after the engine accepts an explicit user reset."""
        asyncio.run_coroutine_threadsafe(asyncio.wait_for(self._on(), self.timeout_s), self._loop).result(
            timeout=self.timeout_s + 1)

    def turn_on_for_test(self) -> None:
        """Compatibility alias for the manual lamp-test utility."""
        self.turn_on()

    def status(self) -> PlugStatus:
        if self._device is None:
            return PlugStatus(False, None, None, metering=False)
        now = time.monotonic()
        if now - self._last_poll >= STATE_POLL_S:
            self._last_poll = now
            self._loop.call_soon_threadsafe(self._request_states)
        on = self._is_on if self._is_on is not None else device_is_on(self._device)
        reachable = self.connected and bool(getattr(self._device, "is_online", True))
        return PlugStatus(reachable, on if reachable else None, None, metering=False)

    @property
    def connected(self) -> bool:
        tcp = getattr(getattr(self._cync, "_command_client", None), "_tcp_manager", None)
        return bool(getattr(tcp, "_login_acknowledged", False))

    def devices(self) -> list:
        return list(self._cync.get_devices()) if self._cync else []

    def close(self) -> None:
        async def _shutdown():
            try:
                if self._cync:
                    await asyncio.wait_for(self._cync.shut_down(), 3)
            finally:
                if self._session:
                    await self._session.close()

        try:
            asyncio.run_coroutine_threadsafe(_shutdown(), self._loop).result(timeout=5)
        except Exception:
            pass
        self._loop.call_soon_threadsafe(self._loop.stop)

    # -- internals ----------------------------------------------------------------------------------
    async def _connect(self) -> None:
        import aiohttp
        from pycync import Auth, Cync

        self._session = aiohttp.ClientSession()
        user = load_user(self.token_path)
        auth = Auth(self._session, user=user)
        if user.expires_at - time.time() < REFRESH_MARGIN_S:
            await auth.async_refresh_user_token()
            save_tokens(auth.user, self.token_path)
        self._cync = await Cync.create(auth)
        self._cync.set_update_callback(self._on_update)
        self._device = pick_device(self.devices(), self.device_name)
        self._is_on = device_is_on(self._device)

    def _on_update(self, data: dict) -> None:
        for device in (data or {}).values():
            if getattr(device, "device_id", None) == getattr(self._device, "device_id", object()):
                state = device_is_on(device)
                if state is not None:
                    self._is_on = state

    def _request_states(self) -> None:
        if self._cync is None:
            return
        try:
            self._cync.update_device_states()
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"

    async def _off(self) -> None:
        device = self._device
        if device is None:
            raise ConnectionError("Cync plug not connected")
        try:
            await device.turn_off()
        except (AttributeError, NotImplementedError) + _unsupported():
            # Device type pycync does not know yet: send the same power command it uses for plugs.
            await self._cync._command_client.set_power_state(device, False)
        self._request_states()

    async def _on(self) -> None:
        device = self._device
        if device is None:
            raise ConnectionError("Cync plug not connected")
        try:
            await device.turn_on()
        except (AttributeError, NotImplementedError) + _unsupported():
            await self._cync._command_client.set_power_state(device, True)
        self._request_states()


def _unsupported() -> tuple[type[BaseException], ...]:
    try:
        from pycync.exceptions import UnsupportedCapabilityError

        return (UnsupportedCapabilityError,)
    except ImportError:
        return ()
