"""End-to-end: simulated scene -> real calibration, geometry, state machine, plug, incidents."""

import threading
from dataclasses import replace

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
                    rule=ClearanceRule(36, 36, 36, 36, "default"),
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


def test_blanket_sends_off_request_and_keeps_guarded_reset(rig):
    engine, world, plug, tick = rig
    assert tick().state.value == 'CLEAR'
    world.place('blanket', 'near')
    assert tick().state.value == 'WARN'
    assert tick(10).state.value == 'OFF_REQUESTED'
    assert plug.off_commands == 1
    assert {'warn', 'shutoff', 'off_request', 'report'} <= set(kinds(engine))
    incident = next(iter(engine.incidents.values()))
    assert incident.outcome == 'requested'
    assert 'An OFF command was sent' in incident.report
    assert 'signals' not in engine.state_message and 'indicator_configured' not in engine.state_message
    for _ in range(4):
        assert tick(10).state.value == 'OFF_REQUESTED'
    assert plug.off_commands == 1  # no follow-up confirmation or telemetry retries
    assert not engine.reset()
    world.place('blanket', 'home')
    tick()
    assert engine.reset()
    assert tick().state.value == 'CLEAR' and plug.on_commands == 1
    assert not engine.reset()



def shutoff_then_clear(rig):
    engine, world, plug, tick = rig
    world.place("blanket", "near")
    assert tick().state.value == "WARN"
    assert tick(10).state.value == "OFF_REQUESTED"
    assert tick().state.value == "OFF_REQUESTED"
    world.place("blanket", "home")
    tick()
    assert engine.state_message["can_reset"] is True
    return engine, world, plug, tick


def test_clear_zone_does_not_automatically_restore_power(rig):
    engine, _, plug, tick = shutoff_then_clear(rig)
    for _ in range(3):
        assert tick(1).state.value == "OFF_REQUESTED"
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
    assert engine.sm.state.value == "OFF_REQUESTED"
    assert plug.on_commands == 0


def test_reset_refuses_a_stale_observation_even_before_next_tick(rig):
    engine, _, plug, _ = shutoff_then_clear(rig)
    engine.clock.t += 5
    assert engine.reset() is False
    assert engine.sm.state.value == "OFF_REQUESTED"
    assert plug.on_commands == 0


def test_reset_preserves_shutoff_latch_when_on_command_fails(rig, monkeypatch):
    engine, _, plug, tick = shutoff_then_clear(rig)

    def fail_on():
        raise ConnectionError("plug command timed out")

    monkeypatch.setattr(plug, "turn_on", fail_on)
    with pytest.raises(RuntimeError):
        engine.reset()
    assert engine.sm.state.value == "OFF_REQUESTED"
    assert plug.status().on is False
    assert "reset" not in kinds(engine)
    assert tick().state.value == "OFF_REQUESTED"


def test_uncertain_on_retries_failed_off_commands_and_reports_error(rig, monkeypatch):
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
    assert engine.sm.state.value == "OFF_REQUESTED"
    assert plug.status().on is True
    for _ in range(3):
        assert tick(5).state.value != "CLEAR"
    assert engine.sm.state.value == "OFF_REQUESTED"
    assert len(off_attempts) >= engine.settings.off_command_retries + 1
    assert plug.on_commands == 1
    assert "error" in kinds(engine)
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
        assert engine.sm.state.value == "OFF_REQUESTED"
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
    assert engine.sm.state.value == "OFF_REQUESTED"
    assert plug.status().on is False
    assert plug.on_commands == 1
    assert plug.off_commands >= 3  # original shutoff, immediate cancellation, and late-ON compensation
    assert "reset" not in kinds(engine)
    assert tick().state.value == "OFF_REQUESTED"
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
    assert d.state.value == "OFF_REQUESTED" and plug.off_commands == 1


def test_stuck_relay_does_not_start_a_confirmation_workflow(rig):
    engine, world, plug, tick = rig
    plug.stuck = True
    world.place('blanket', 'near')
    tick()
    assert tick(10).state.value == 'OFF_REQUESTED'
    for _ in range(3):
        assert tick(10).state.value == 'OFF_REQUESTED'
    assert plug.off_commands == 1 and plug.status().on is True
    assert next(iter(engine.incidents.values())).outcome == 'requested'
    assert 'confirmed' not in kinds(engine)


def test_failed_off_request_retries_command_and_records_failure(rig, monkeypatch):
    engine, world, plug, tick = rig
    attempts = []

    def fail_off():
        attempts.append(True)
        raise ConnectionError("plug unavailable")

    monkeypatch.setattr(plug, "turn_off", fail_off)
    world.place("blanket", "near")
    tick()
    assert tick(10).state.value == "OFF_REQUESTED"
    assert len(attempts) == engine.settings.off_command_retries + 1
    assert engine.state_message["off_pending"] is False
    assert "plug unavailable" in engine.state_message["off_error"]
    incident = next(iter(engine.incidents.values()))
    assert incident.outcome == "failed" and "OFF command failed" in incident.report



def test_api_outage_degrades_to_unknown_high_hazard(rig):
    engine, world, _, tick = rig
    engine.perception.fault_api = True
    world.place("box", "near")
    d = tick()
    assert "API_DOWN" in d.degraded
    assert engine.state_message["objects"][0]["label"] == "unknown object"
    assert engine.state_message["objects"][0]["hazard"] == "high"


def test_local_motion_waits_for_sam_and_report_names_actual_trigger(rig, monkeypatch):
    engine, world, plug, tick = rig
    tick()
    assert engine.set_reference()
    original = engine.perception.current
    requested = []
    monkeypatch.setattr(engine.perception, "current", lambda: [])
    monkeypatch.setattr(engine.perception, "request_check", lambda: requested.append(True), raising=False)
    world.place("blanket", "near")
    for seconds in (.1, 10, 60):
        d = tick(seconds)
        assert d.state.value == "WATCH" and d.countdown_s is None
        assert plug.off_commands == 0
        assert any(o["status"] == "pending" for o in engine.state_message["objects"])
    assert requested
    engine.log("warn", "Unknown object was seen earlier", {"track_id": "unrelated-motion"})
    monkeypatch.setattr(engine.perception, "current", original)
    assert tick().countdown_s == 10
    assert tick(9).state.value == "WARN"
    assert tick(1).state.value == "OFF_REQUESTED"
    incident = next(iter(engine.incidents.values()))
    assert incident.trigger["label"] == "fleece blanket"
    assert "fleece blanket" in incident.report
    assert not any(e.get("data", {}).get("track_id") == "unrelated-motion" for e in incident.events)


def test_person_and_worn_clothing_never_start_object_shutoff(rig, monkeypatch):
    engine, _, plug, tick = rig
    mask = engine.cmap.zone_mask()
    person = PerceivedObject("p", "person", mask, source="sam")
    clothes = PerceivedObject("c", "clothing", mask, source="sam", extra={"on_person": True})
    monkeypatch.setattr(engine.perception, "current", lambda: [person, clothes])
    for seconds in (.1, 10, 60):
        d = tick(seconds)
        assert d.state.value == "WATCH" and d.countdown_s is None
        assert plug.off_commands == 0
        assert all(o["is_person"] for o in engine.state_message["objects"])


def test_calibration_drift_detected(rig):
    engine, world, _, tick = rig
    tick()
    world.bumped = True
    d = tick(5)
    assert "CALIBRATION_DRIFT" in d.degraded


def test_relay_only_plug_sends_off_without_camera_light_setup():
    from three_feet.safety.actuator import PlugStatus
    class RelayOnlyPlug(DryRunPlug):
        def status(self):
            value = super().status()
            return PlugStatus(value.reachable, value.on, None, metering=False)
    clock = FakeClock()
    plug = RelayOnlyPlug(settle_s=0)
    world = SimWorld(plug, clock=clock, speed_in_s=1000)
    engine = Engine(SimSource(world), SimPerception(world), plug, Settings(_env_file=None),
                    calib=world.calibration(), rule=ClearanceRule(36,36,36,36,'default'),
                    mode='sim', clock=clock, events_dir=None)
    engine._workers.shutdown()
    engine._workers = SyncExecutor()
    engine.plug.stop()
    try:
        world.place('blanket','near')
        clock.t += .1
        engine.plug.refresh()
        assert engine.tick().state.value == 'WARN'
        clock.t += 10
        assert engine.tick().state.value == 'OFF_REQUESTED'
        assert plug.off_commands == 1 and plug.status().on is False
        assert next(iter(engine.incidents.values())).outcome == 'requested'
    finally:
        engine.stop()







def test_live_outside_measurement_is_uncertain_without_starting_shutoff(rig, monkeypatch):
    engine, world, _, tick = rig
    original = engine.cmap.measure

    def boundary_measurement(mask):
        m = original(mask)
        return replace(m, distance_in=m.required_in + .5, slack_in=.5, err_in=1)

    monkeypatch.setattr(engine.cmap, "measure", boundary_measurement)
    world.place("blanket", "near")
    assert tick().state.value == "WATCH"
    obj = engine.state_message["objects"][0]
    assert obj["err_in"] == 1 and obj["status"] == "uncertain"
    assert obj["distance_in"] > obj["required_in"]
    assert tick(60).state.value == "WATCH"


def test_actual_overlap_has_priority_over_uncertain_outside_object(rig, monkeypatch):
    from three_feet.geometry import Measurement
    from three_feet.engine import MeasuredObject

    engine, _, _, tick = rig
    decision = tick()
    mask = np.zeros(engine.cmap.slack.shape, bool)
    outside = MeasuredObject(PerceivedObject("drill", "power drill", mask),
        Measurement(20.2, 15, 5.2, 11, "back", (0,0), (0,0)), "clear")
    inside = MeasuredObject(PerceivedObject("cloth", "blanket", mask),
        Measurement(14, 15, -1, .2, "front", (0,0), (0,0)), "violation")
    engine._measured = [outside, inside]
    result = engine._message(decision, True, 1500, engine.plug.status)
    assert result["objects"][0]["label"] == "blanket"


@pytest.mark.parametrize("reason", ["stale", "budget", "failed"])
def test_reset_refuses_unhealthy_model_results_with_a_fresh_camera(rig, reason):
    from three_feet.perception.meta_client import CostMeter
    from three_feet.perception.pipeline import MetaPerception

    engine, _, plug, _ = shutoff_then_clear(rig)
    meter = CostMeter(budget_usd_per_hour=1)
    perception = MetaPerception(None, None, meter, clock=engine.clock)
    perception.last_frame_ts = engine.clock() - (6 if reason == "stale" else 0)
    perception.last_success_at = engine.clock()
    if reason == "budget":
        meter.add("test", 1)
    elif reason == "failed":
        perception._segmentation_error = "API call failed"
    engine.perception = perception
    assert not engine.reset()
    assert plug.on_commands == 0
