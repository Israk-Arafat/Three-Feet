"""End-to-end: simulated scene -> real calibration, geometry, state machine, plug, verification, incidents."""

import threading

import pytest
import numpy as np

from three_feet.config import Settings
from three_feet.engine import Engine
from three_feet.perception.wonder import ClearanceRule
from three_feet.safety.actuator import DryRunPlug
from three_feet.sim import SimPerception, SimSource, SimWorld
from three_feet.engine import is_ignored_item
from three_feet.perception.tracking import PerceivedObject


class FakeClock:
    def __init__(self) -> None:
        self.t = 1_000_000.0

    def __call__(self) -> float:
        return self.t


class SyncExecutor:
    def submit(self, fn, *args, **kwargs):
        fn(*args, **kwargs)

    def shutdown(self, **kwargs):
        pass


@pytest.fixture
def rig():
    clock = FakeClock()
    plug = DryRunPlug(settle_s=0)
    world = SimWorld(plug, clock=clock, speed_in_s=1000)
    calib = world.calibration()
    settings = Settings(_env_file=None, failsafe_after_s=30, dwell_high_s=10)
    engine = Engine(SimSource(world), SimPerception(world), plug, settings, calib=calib,
                    rule=ClearanceRule(36, 36, 36, 36, "default"), probe=world.indicator_probe(calib),
                    mode="sim", clock=clock, events_dir=None)
    engine._workers.shutdown()
    engine._workers = SyncExecutor()
    engine.plug.stop()

    def tick(seconds=0.1):
        clock.t += seconds
        engine.plug.refresh()
        return engine.tick()

    yield engine, world, plug, tick
    engine.stop()


def kinds(engine):
    return [e["kind"] for e in engine.events_since(0)]


def test_cords_and_room_fixtures_are_never_measured(rig, monkeypatch):
    engine, _, plug, tick = rig
    mask = np.zeros(engine.cmap.slack.shape, bool)
    mask[10:20, 10:20] = True
    for label in ("power cord", "Extension-Cord", "white baseboard", "wall outlet"):
        assert is_ignored_item(PerceivedObject("x", label, mask, source="sam")), label
    for label in ("blanket", "towel", "fleece blanket", "cardboard box", "person"):
        assert not is_ignored_item(PerceivedObject("x", label, mask, source="sam")), label
    assert not is_ignored_item(PerceivedObject("u1", "unknown object", mask, source="catchall"))
    assert not is_ignored_item(PerceivedObject("c", "power cord", mask, source="catchall"))
    cord = PerceivedObject("cord", "power cord", np.ones_like(mask), source="sam")
    monkeypatch.setattr(engine.perception, "current", lambda: [cord])
    assert tick().state.value == "CLEAR"
    assert tick(15).state.value == "CLEAR" and plug.off_commands == 0


def test_blanket_shutoff_confirmed_by_two_signals(rig):
    engine, world, plug, tick = rig
    assert tick().state.value == "CLEAR"
    world.place("blanket", "near")
    d = tick()
    assert d.state.value == "WARN"
    obj = engine.state_message["objects"][0]
    assert obj["label"] == "fleece blanket" and obj["status"] == "violation"
    assert obj["distance_in"] == pytest.approx(12, abs=0.5) and obj["side"] == "front"
    d = tick(10)
    assert d.state.value == "VERIFYING" and plug.off_commands == 1
    d = tick()
    assert d.state.value == "OFF_CONFIRMED"
    assert d.signals == {"power_off": True, "indicator_off": True}
    assert {"warn", "shutoff", "confirmed", "report"} <= set(kinds(engine))
    inc = next(iter(engine.incidents.values()))
    assert inc.outcome == "confirmed" and inc.report_source == "template"
    assert "came within 12." in inc.report and "front" in inc.report

    assert engine.reset() is False  # blanket still in the zone
    world.place("blanket", "home")
    tick()
    assert engine.state_message["can_reset"] is True
    assert engine.reset() is True
    assert tick().state.value == "CLEAR"
    assert plug.status().power_w == 1500
    assert plug.on_commands == 1
    assert engine.reset() is False  # repeated requests must not send another ON command
    assert plug.on_commands == 1


def shutoff_then_clear(rig):
    engine, world, plug, tick = rig
    world.place("blanket", "near")
    assert tick().state.value == "WARN"
    assert tick(10).state.value == "VERIFYING"
    assert tick().state.value == "OFF_CONFIRMED"
    world.place("blanket", "home")
    tick()
    assert engine.state_message["can_reset"] is True
    return engine, world, plug, tick


def test_clear_zone_does_not_automatically_restore_power(rig):
    engine, _, plug, tick = shutoff_then_clear(rig)
    for _ in range(3):
        assert tick(1).state.value == "OFF_CONFIRMED"
    assert plug.status().on is False
    assert plug.on_commands == 0


@pytest.mark.parametrize("fault", ["occupied", "camera", "calibration", "drift", "api", "plug"])
def test_reset_never_energizes_without_a_reliable_clear_zone(rig, fault):
    engine, world, plug, tick = shutoff_then_clear(rig)
    if fault == "occupied":
        world.place("blanket", "near")
    elif fault == "camera":
        world.camera_cut = True
    elif fault == "calibration":
        engine.calib = None
        engine._build_geometry()
    elif fault == "drift":
        world.bumped = True
    elif fault == "api":
        engine.perception.fault_api = True
    elif fault == "plug":
        plug.reachable = False
    tick(5)
    assert engine.state_message["can_reset"] is False
    assert engine.reset() is False
    assert engine.sm.state.value == "OFF_CONFIRMED"
    assert plug.on_commands == 0


def test_reset_refuses_a_stale_observation_even_before_next_tick(rig):
    engine, _, plug, _ = shutoff_then_clear(rig)
    engine.clock.t += 5
    assert engine.reset() is False
    assert engine.sm.state.value == "OFF_CONFIRMED"
    assert plug.on_commands == 0


def test_reset_preserves_shutoff_latch_when_on_command_fails(rig, monkeypatch):
    engine, _, plug, tick = shutoff_then_clear(rig)

    def fail_on():
        raise ConnectionError("plug command timed out")

    monkeypatch.setattr(plug, "turn_on", fail_on)
    with pytest.raises(RuntimeError):
        engine.reset()
    assert engine.sm.state.value in ("VERIFYING", "OFF_CONFIRMED")
    assert plug.status().on is False
    assert "reset" not in kinds(engine)
    assert tick().state.value == "OFF_CONFIRMED"


def test_uncertain_on_and_failed_compensating_off_retries_then_alarms(rig, monkeypatch):
    engine, _, plug, tick = shutoff_then_clear(rig)
    original_on = plug.turn_on
    off_attempts = []

    def uncertain_on():
        original_on()  # a timeout does not prove the relay stayed off
        raise TimeoutError("ON acknowledgement lost")

    def failed_off():
        off_attempts.append(True)
        raise ConnectionError("OFF command failed")

    monkeypatch.setattr(plug, "turn_on", uncertain_on)
    monkeypatch.setattr(plug, "turn_off", failed_off)
    with pytest.raises(RuntimeError):
        engine.reset()
    assert engine.sm.state.value == "VERIFYING"
    assert plug.status().on is True
    for _ in range(3):
        assert tick(5).state.value != "CLEAR"
    assert engine.sm.state.value == "OFF_UNCONFIRMED"
    assert len(off_attempts) >= engine.sm.cfg.max_off_retries + 1
    assert plug.on_commands == 1
    assert "alarm" in kinds(engine)
    assert "reset" not in kinds(engine)


def test_object_entering_during_slow_reset_cuts_power_without_stalling_ticks(rig, monkeypatch):
    engine, world, plug, tick = shutoff_then_clear(rig)
    started, release, tick_finished = threading.Event(), threading.Event(), threading.Event()
    original_on = plug.turn_on
    outcomes, errors = [], []

    def slow_on():
        original_on()  # relay energizes before its delayed command acknowledgement
        started.set()
        if not release.wait(5):
            raise TimeoutError("test did not release the ON acknowledgement")
        plug.simulate_power_on()  # a delayed ON delivery can override the first compensating OFF

    def request_reset():
        try:
            outcomes.append(engine.reset())
        except Exception as exc:
            errors.append(exc)

    def observe_new_object():
        try:
            tick()
        finally:
            tick_finished.set()

    monkeypatch.setattr(plug, "turn_on", slow_on)
    reset_thread = threading.Thread(target=request_reset, daemon=True)
    tick_thread = threading.Thread(target=observe_new_object, daemon=True)
    reset_thread.start()
    try:
        assert started.wait(2), "reset did not reach the ON command"
        world.place("blanket", "near")
        tick_thread.start()
        assert tick_finished.wait(2), "a slow ON command blocked the safety loop"
        assert engine.sm.state.value in ("VERIFYING", "OFF_CONFIRMED")
        assert engine.state_message["can_reset"] is False
        assert engine.state_message["reset_pending"] is True
        assert engine.state_message["reset_cancelled"] is True
        assert plug.status().on is False, "new obstruction must cut power before the ON acknowledgement"
        assert plug.off_commands >= 2
        assert engine.reset() is False  # do not submit a second ON while one is pending
    finally:
        release.set()
        reset_thread.join(timeout=2)
        if tick_thread.ident is not None:
            tick_thread.join(timeout=2)
    assert not reset_thread.is_alive()
    assert True not in outcomes
    assert outcomes == [False] or (len(errors) == 1 and isinstance(errors[0], RuntimeError))
    assert engine.sm.state.value in ("VERIFYING", "OFF_CONFIRMED")
    assert plug.status().on is False
    assert plug.on_commands == 1
    assert plug.off_commands >= 3  # original shutoff, immediate cancellation, and late-ON compensation
    assert "reset" not in kinds(engine)
    assert tick().state.value == "OFF_CONFIRMED"
    assert engine.state_message["reset_pending"] is False


def test_watch_for_near_miss(rig):
    engine, world, _, tick = rig
    world.place("box", "watch")
    assert tick().state.value == "WATCH"
    assert engine.state_message["objects"][0]["status"] == "watch"


def test_camera_cut_is_unknown_then_failsafe(rig):
    engine, world, plug, tick = rig
    tick()
    world.camera_cut = True
    d = tick()
    assert d.banner.startswith("UNKNOWN") and "CAMERA_LOST" in d.degraded
    assert engine.state_message["camera"]["healthy"] is False
    d = tick(30)
    assert d.state.value == "VERIFYING" and plug.off_commands == 1


def test_stuck_relay_alarms(rig):
    engine, world, plug, tick = rig
    plug.stuck = True
    world.place("blanket", "near")
    tick()
    tick(10)
    for _ in range(3):
        d = tick(5)
    assert d.state.value == "OFF_UNCONFIRMED" and "alarm" in kinds(engine)


def test_api_outage_degrades_to_unknown_high_hazard(rig):
    engine, world, _, tick = rig
    engine.perception.fault_api = True
    world.place("box", "near")
    d = tick()
    assert "API_DOWN" in d.degraded
    assert engine.state_message["objects"][0]["label"] == "unknown object"
    assert engine.state_message["objects"][0]["hazard"] == "high"


def test_calibration_drift_detected(rig):
    engine, world, _, tick = rig
    tick()
    world.bumped = True
    d = tick(5)
    assert "CALIBRATION_DRIFT" in d.degraded


def test_relay_only_plug_end_to_end():
    """A plug without a meter (Cync): shutoff confirmed by the relay report plus the camera's power light."""
    from three_feet.safety.actuator import PlugStatus

    class RelayOnlyPlug(DryRunPlug):
        def status(self):
            st = super().status()
            return PlugStatus(st.reachable, st.on, None, metering=False)

    clock = FakeClock()
    plug = RelayOnlyPlug(settle_s=0)
    world = SimWorld(plug, clock=clock, speed_in_s=1000)
    calib = world.calibration()
    engine = Engine(SimSource(world), SimPerception(world), plug, Settings(_env_file=None), calib=calib,
                    rule=ClearanceRule(36, 36, 36, 36, "default"), probe=world.indicator_probe(calib),
                    mode="sim", clock=clock, events_dir=None)
    engine._workers.shutdown()
    engine._workers = SyncExecutor()
    engine.plug.stop()

    def tick(seconds=0.1):
        clock.t += seconds
        engine.plug.refresh()
        return engine.tick()

    try:
        world.place("blanket", "near")
        assert tick().state.value == "WARN"
        assert engine.state_message["power"]["metering"] is False
        assert engine.state_message["power"]["watts"] is None
        assert "PLUG_UNREACHABLE" not in engine.state_message["degraded"]
        assert tick(10).state.value == "VERIFYING"
        d = tick()
        assert d.state.value == "OFF_CONFIRMED"
        assert any("plug reports off" in e["text"] for e in engine.events_since(0))
    finally:
        engine.stop()
