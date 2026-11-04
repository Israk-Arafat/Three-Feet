"""Changing the camera and plug from the dashboard: validation, live swap, persistence, and safety limits."""

import numpy as np
import pytest
from fastapi.testclient import TestClient

from three_feet.config import Settings
from three_feet.connections import (
    CameraConfig,
    ConnectionService,
    ConnectionStore,
    PlugConfig,
    display_address,
    parse_camera_address,
    startup_connections,
)
from three_feet.engine import ConnectionChangeBlocked, Engine
from three_feet.perception.wonder import ClearanceRule
from three_feet.safety.actuator import DryRunPlug
from three_feet.safety.cync_plug import DeviceChoiceError, pick_device
from three_feet.safety.state_machine import State
from three_feet.sim import SimPerception, SimSource, SimWorld
from three_feet.sources import Frame


class FakeCamera:
    def __init__(self, frame=True, last_error=None):
        self._frame = Frame(np.zeros((12, 16, 3), np.uint8), 0.0, 1) if frame else None
        self.healthy = frame
        self.last_error = last_error
        self.closed = False

    def read(self):
        return self._frame

    def close(self):
        self.closed = True


class FakeCyncPlug:
    name = "cync"

    def __init__(self, device_name=""):
        self.device_name = device_name
        self.closed = False
        self.selected = []

    def turn_off(self): ...

    def turn_on(self): ...

    def status(self):
        from three_feet.safety.actuator import PlugStatus

        return PlugStatus(True, False, None, metering=False)

    def select_device(self, name):
        self.selected.append(name)
        self.device_name = name

    def devices(self):
        return []

    def close(self):
        self.closed = True


@pytest.fixture
def rig(tmp_path):
    plug = DryRunPlug(settle_s=0)
    world = SimWorld(plug)
    calib = world.calibration()
    engine = Engine(SimSource(world), SimPerception(world), plug, Settings(_env_file=None), calib=calib,
                    rule=ClearanceRule(36, 36, 36, 36, "default"), mode="live", events_dir=None)
    engine.plug.stop()
    cameras, plugs = [], []

    def make_camera(config):
        cameras.append((config, FakeCamera()))
        return cameras[-1][1]

    def make_plug(kind, name):
        plugs.append((kind, name))
        return FakeCyncPlug(name)

    token = tmp_path / "cync_token.json"
    service = ConnectionService(engine, ConnectionStore(tmp_path / "connections.json"), camera_factory=make_camera,
                                plug_factory=make_plug, token_path=token, camera_timeout_s=0.2)
    yield engine, service, cameras, plugs, token, plug
    engine.stop()


def test_camera_address_accepts_streams_and_webcams_only():
    assert parse_camera_address(" https://192.168.0.63:4444/video/mjpeg ") == ("url", "https://192.168.0.63:4444/video/mjpeg")
    assert parse_camera_address("0") == ("webcam", 0)
    for bad in ("", "phone", "192.168.0.63:4444", r"C:\photos", "ftp://cam/stream", "http://", "https://host:notaport/x", "999"):
        with pytest.raises(ValueError):
            parse_camera_address(bad)


def test_display_address_hides_credentials():
    assert display_address("https://me:secret@192.168.0.9:4444/video") == "https://192.168.0.9:4444/video"
    assert display_address("http://cam.local/video") == "http://cam.local/video"


def test_store_merges_and_survives_garbage(tmp_path):
    store = ConnectionStore(tmp_path / "c.json")
    assert store.load() == (None, None)
    store.save(camera=CameraConfig("http://cam/video", "u", "p", True))
    store.save(plug=PlugConfig("cync", "Heater"))
    assert store.load() == (CameraConfig("http://cam/video", "u", "p", True), PlugConfig("cync", "Heater"))
    (tmp_path / "c.json").write_text("not json")
    assert store.load() == (None, None)


def test_startup_uses_saved_choices_unless_flags_say_otherwise(tmp_path):
    store = ConnectionStore(tmp_path / "c.json")
    store.save(camera=CameraConfig("http://cam/video"), plug=PlugConfig("cync", "Heater"))
    assert startup_connections("phone", "cync", True, store) == (CameraConfig("http://cam/video"), PlugConfig("cync", "Heater"))
    camera, plug = startup_connections("http://other/video", "dryrun", True, store)
    assert camera is None and plug is None  # an explicit address and a different explicit plug win
    assert startup_connections("phone", "dryrun", False, store)[1] == PlugConfig("cync", "Heater")


def test_apply_camera_swaps_live_and_remembers_it(rig):
    engine, service, cameras, _, _, _ = rig
    old = engine.source
    result = service.apply_camera("https://192.168.0.9:4444/video/mjpeg", "me", "pw", True)
    assert engine.source is cameras[0][1] and engine.source is not old
    assert engine.source_label == "https://192.168.0.9:4444/video/mjpeg"
    assert result["camera"]["address"] == "https://192.168.0.9:4444/video/mjpeg"
    assert service.store.load()[0] == CameraConfig("https://192.168.0.9:4444/video/mjpeg", "me", "pw", True)
    assert engine.catchall is None or engine.catchall.reference is None
    assert "Camera changed" in engine.events_since(0)[-1]["text"]


def test_failed_camera_leaves_the_running_one_alone(rig):
    engine, service, cameras, _, _, _ = rig
    service._camera_factory = lambda config: FakeCamera(frame=False, last_error="ConnectError: refused")
    old = engine.source
    with pytest.raises(ConnectionError, match="No picture"):
        service.apply_camera("http://192.168.0.9/video")
    assert engine.source is old
    assert service.store.load()[0] is None


def test_testing_a_camera_never_touches_the_engine(rig):
    engine, service, cameras, _, _, _ = rig
    old = engine.source
    assert service.test_camera("http://192.168.0.9/video") == {"ok": True, "width": 16, "height": 12}
    assert engine.source is old and cameras[0][1].closed


def test_blank_password_keeps_the_one_in_use(rig):
    engine, service, cameras, _, _, _ = rig
    import threading

    from three_feet.sources import MjpegHttpSource

    streaming = MjpegHttpSource.__new__(MjpegHttpSource)  # skip the network reader thread
    streaming.url, streaming._auth, streaming._verify = "http://cam/video", ("me", "pw"), False
    streaming._lock, streaming._latest, streaming._stale_after_s = threading.Lock(), None, 3.0
    streaming.close = lambda: None
    engine.source = streaming
    service.apply_camera("http://cam/video", "me", None)
    assert cameras[0][0].password == "pw"
    service.apply_camera("http://cam/video", "", "ignored")
    assert cameras[1][0] == CameraConfig("http://cam/video", "", "", False)


def test_heater_on_is_noted_but_never_blocks(rig):
    engine, service, cameras, _, _, plug = rig
    engine.plug.refresh()
    assert engine.plug.status.on is True
    result = service.apply_camera("http://192.168.0.9/video")
    assert result["heater_on"] is True
    assert "heater was on" in engine.events_since(0)[-1]["text"]


def test_plug_change_to_dryrun_closes_the_old_plug(rig):
    engine, service, _, _, _, plug = rig
    cync = FakeCyncPlug()
    engine.switch = cync
    service.apply_plug("dryrun")
    assert cync.closed and isinstance(engine.switch, DryRunPlug) and engine.switch is not plug
    assert service.store.load()[1] == PlugConfig("dryrun", "")


def test_cync_needs_a_sign_in_first(rig):
    _, service, _, plugs, _, _ = rig
    with pytest.raises(ValueError, match="Sign in"):
        service.apply_plug("cync", "Heater")
    assert plugs == []


def test_cync_plug_is_connected_then_device_switch_reuses_the_connection(rig):
    engine, service, _, plugs, token, _ = rig
    token.write_text("{}")
    service.apply_plug("cync", "Heater")
    assert plugs == [("cync", "Heater")] and engine.switch.name == "cync"
    service.apply_plug("cync", "Fan")
    assert plugs == [("cync", "Heater")] and engine.switch.selected == ["Fan"]  # no second account connection


def test_new_cync_sign_in_forces_a_fresh_connection(rig):
    engine, service, _, plugs, token, _ = rig
    token.write_text("{}")
    service.apply_plug("cync", "Heater")
    first = engine.switch

    class Login:
        def start(self, email, password):
            return "code_required"

        def submit_code(self, code):
            pass

        def close(self):
            pass

    service._login_factory = Login
    assert service.cync_login("a@b.co", "pw") == {"status": "code_required"}
    assert service.cync_code("123456") == {"status": "signed_in"}
    service.apply_plug("cync", "Heater")
    assert len(plugs) == 2 and engine.switch is not first and first.closed


def test_plug_cannot_change_while_an_off_command_is_pending(rig):
    engine, service, _, _, _, plug = rig
    engine._off_pending = 1
    with pytest.raises(ConnectionChangeBlocked):
        service.apply_plug('dryrun')
    assert engine.switch is plug
    assert service.store.load()[1] is None



def test_simulated_scene_cannot_be_edited(rig):
    engine, service, *_ = rig
    engine.mode = "sim"
    assert service.snapshot()["editable"] is False
    with pytest.raises(ConnectionChangeBlocked):
        service.apply_camera("http://cam/video")
    with pytest.raises(ConnectionChangeBlocked):
        service.apply_plug("dryrun")


def test_device_choice_error_lists_what_the_account_has():
    class CyncPlug:
        def __init__(self, name):
            self.name, self.is_online = name, True

    with pytest.raises(DeviceChoiceError) as info:
        pick_device([CyncPlug("Heater"), CyncPlug("Lamp")])
    assert [d["name"] for d in info.value.devices] == ["Heater", "Lamp"]


def test_connection_api_in_the_simulator(monkeypatch):
    import three_feet.app as app_module

    monkeypatch.setenv("TF_SOURCE", "sim")
    monkeypatch.setenv("TF_AUTOPLAY", "0")
    monkeypatch.setenv("MODEL_API_KEY", "")
    with TestClient(app_module.create_app()) as client:
        snap = client.get("/api/connections").json()
        assert snap["editable"] is False and snap["plug"]["kind"] == "dryrun"
        assert client.put("/api/connections/plug", json={"kind": "dryrun"}).status_code == 409
        assert client.put("/api/connections/camera", json={"address": "nonsense"}).status_code in (400, 409)
        assert client.post("/api/connections/camera/test", json={"address": "nonsense"}).status_code == 400
        assert client.put("/api/connections/plug", json={"kind": "shelly"}).status_code == 422


def test_plain_launch_starts_live_with_no_camera_until_one_is_added(monkeypatch, tmp_path):
    import three_feet.app as app_module
    from three_feet.sources import NoCameraSource

    monkeypatch.setattr(app_module, "startup_connections", lambda *args: (None, None))
    engine, _ = app_module.build_engine("phone", "dryrun", False, plug_explicit=False)
    try:
        service = ConnectionService(engine, ConnectionStore(tmp_path / "c.json"),
                                    camera_factory=lambda config: FakeCamera(), camera_timeout_s=0.2)
        snap = service.snapshot()
        assert isinstance(engine.source, NoCameraSource) and engine.mode == "live"
        assert snap["editable"] is True and snap["camera"]["kind"] == "none" and snap["camera"]["address"] == ""
        assert not engine.source.healthy
        service.apply_camera("http://192.168.0.9/video")
        assert engine.source_label == "http://192.168.0.9/video"
    finally:
        engine.stop()
