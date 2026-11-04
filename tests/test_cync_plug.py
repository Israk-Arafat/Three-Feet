"""GE Cync driver: helpers, status mapping and the off-command fallback, without a Cync account."""

import asyncio
import json
import threading
import time

import pytest

from three_feet_app.safety import cync_plug
from three_feet_app.safety.cync_plug import CyncPlug, device_is_on, load_user, pick_device, save_tokens


class FakePlug:  # named like pycync's class so pick_device recognizes it
    def __init__(self, name, is_on=True, online=True, device_id=1):
        self.name, self._on, self.is_online, self.device_id = name, is_on, online, device_id
        self.off_calls = 0
        self.on_calls = 0

    @property
    def is_on(self):
        return self._on

    async def turn_on(self):
        self.on_calls += 1
        self._on = True

    async def turn_off(self):
        self.off_calls += 1
        self._on = False


FakePlug.__name__ = "CyncPlug"


class UnknownDevice:
    """A device type pycync does not recognize: no turn_off, and is_on raises."""

    name, is_online, device_id = "Heater plug", True, 7

    @property
    def is_on(self):
        raise RuntimeError("unsupported")


class FakeCommandClient:
    def __init__(self):
        self.calls = []
        self._tcp_manager = type("T", (), {"_login_acknowledged": True})()

    async def set_power_state(self, device, is_on):
        self.calls.append((device, is_on))


class FakeCync:
    def __init__(self):
        self._command_client = FakeCommandClient()
        self.state_requests = 0

    def update_device_states(self):
        self.state_requests += 1


def bare_plug(device, cync=None):
    """A CyncPlug wired to fakes, skipping the network connect."""
    p = CyncPlug.__new__(CyncPlug)
    p.device_name, p.timeout_s, p.last_error = "", 2.0, None
    p._cync, p._session, p._device = cync or FakeCync(), None, device
    p._is_on, p._last_poll = device_is_on(device), 0.0
    p._loop = asyncio.new_event_loop()
    threading.Thread(target=p._loop.run_forever, daemon=True).start()
    return p


def test_device_is_on_handles_unsupported():
    assert device_is_on(FakePlug("a", is_on=False)) is False
    assert device_is_on(UnknownDevice()) is None


def test_pick_device_by_name_or_single_plug():
    a, b = FakePlug("Heater"), FakePlug("Lamp")
    assert pick_device([a, b], "heater") is a
    assert pick_device([a, UnknownDevice()]) is a
    with pytest.raises(RuntimeError):
        pick_device([a, b])
    with pytest.raises(RuntimeError):
        pick_device([a], "nope")


def test_tokens_roundtrip_without_password(tmp_path):
    pytest.importorskip("pycync")
    from pycync.user import User

    path = tmp_path / "tok.json"
    save_tokens(User("acc", "ref", "authz", 42, expires_at=time.time() + 3600), path)
    saved = json.loads(path.read_text())
    assert "password" not in json.dumps(saved).lower()
    user = load_user(path)
    assert (user.access_token, user.refresh_token, user.authorize, user.user_id) == ("acc", "ref", "authz", 42)


def test_missing_login_has_clear_error(tmp_path):
    pytest.importorskip("pycync")
    with pytest.raises(RuntimeError, match="cync_login.py"):
        load_user(tmp_path / "missing.json")


def test_connect_failure_closes_session_and_loop(monkeypatch):
    created = {}

    class FakeSession:
        closed = False

        async def close(self):
            self.closed = True

    async def fail_connect(self):
        self._session = FakeSession()
        created["session"] = self._session
        created["thread"] = self._thread
        raise RuntimeError("login missing")

    monkeypatch.setattr(CyncPlug, "_connect", fail_connect)
    with pytest.raises(RuntimeError, match="login missing"):
        CyncPlug(connect_timeout_s=1)

    created["thread"].join(timeout=1)
    assert created["session"].closed
    assert not created["thread"].is_alive()


def test_status_reports_relay_without_meter():
    p = bare_plug(FakePlug("Heater", is_on=True))
    st = p.status()
    assert st.reachable and st.on is True and st.power_w is None and st.metering is False
    p._loop.call_soon_threadsafe(p._loop.stop)


def test_turn_off_uses_plug_api_then_requests_state():
    dev = FakePlug("Heater")
    p = bare_plug(dev)
    p.turn_off()
    assert dev.off_calls == 1
    p._on_update({"x": dev})
    assert p.status().on is False
    time.sleep(0.05)
    assert p._cync.state_requests >= 1
    p._loop.call_soon_threadsafe(p._loop.stop)


def test_turn_on_for_test_uses_plug_api_then_requests_state():
    dev = FakePlug("Lamp", is_on=False)
    p = bare_plug(dev)
    p.turn_on_for_test()
    assert dev.on_calls == 1
    p._on_update({"x": dev})
    assert p.status().on is True
    time.sleep(0.05)
    assert p._cync.state_requests >= 1
    p._loop.call_soon_threadsafe(p._loop.stop)


def test_turn_on_uses_plug_api_then_requests_state():
    dev = FakePlug("Heater", is_on=False)
    p = bare_plug(dev)
    try:
        p.turn_on()
        assert dev.on_calls == 1
        p._on_update({"x": dev})
        assert p.status().on is True
        time.sleep(0.05)
        assert p._cync.state_requests >= 1
    finally:
        p._loop.call_soon_threadsafe(p._loop.stop)


def test_turn_off_falls_back_for_unknown_device_type():
    dev = UnknownDevice()
    p = bare_plug(dev)
    p.turn_off()
    assert p._cync._command_client.calls == [(dev, False)]
    assert p.status().on is None  # state unknown: the safety logic will not count it as off
    p._loop.call_soon_threadsafe(p._loop.stop)


def test_turn_on_for_test_falls_back_for_unknown_device_type():
    dev = UnknownDevice()
    p = bare_plug(dev)
    p.turn_on_for_test()
    assert p._cync._command_client.calls == [(dev, True)]
    assert p.status().on is None
    p._loop.call_soon_threadsafe(p._loop.stop)


def test_turn_on_falls_back_for_unknown_device_type():
    dev = UnknownDevice()
    p = bare_plug(dev)
    try:
        p.turn_on()
        assert p._cync._command_client.calls == [(dev, True)]
        assert p.status().on is None  # sending ON does not fabricate a confirmed relay state
    finally:
        p._loop.call_soon_threadsafe(p._loop.stop)


def test_disconnected_account_is_unreachable():
    cync = FakeCync()
    cync._command_client._tcp_manager._login_acknowledged = False
    p = bare_plug(FakePlug("Heater"), cync)
    assert p.status().reachable is False and p.status().on is None
    p._loop.call_soon_threadsafe(p._loop.stop)


def test_make_switch_knows_cync(monkeypatch):
    from three_feet_app.safety import actuator

    created = {}

    def fake_init(self, device_name=""):
        created["name"] = device_name

    monkeypatch.setattr(cync_plug.CyncPlug, "__init__", fake_init)
    actuator.make_switch("cync", name="Heater")
    assert created["name"] == "Heater"
