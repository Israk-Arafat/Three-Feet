"""End-to-end: simulated scene -> real calibration, geometry, state machine, plug, verification, incidents."""

import pytest

from three_feet.config import Settings
from three_feet.engine import Engine
from three_feet.perception.wonder import ClearanceRule
from three_feet.safety.actuator import DryRunPlug
from three_feet.sim import SimPerception, SimSource, SimWorld


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
    assert plug.status().power_w == 0  # Three Feet never turns the heater back on


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
