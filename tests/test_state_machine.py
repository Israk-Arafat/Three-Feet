from three_feet.safety.state_machine import (
    Action,
    Observation,
    SafetyConfig,
    SafetyStateMachine,
    State,
    ZoneObject,
)

CFG = SafetyConfig(
    watch_margin_in=6, dwell_s={"high": 10, "medium": 20, "low": 60},
    failsafe_enabled=True, failsafe_after_s=30, off_power_w=5, verify_timeout_s=5, max_off_retries=2,
)
ON = 1500.0


def blanket(slack, hazard="high", err=1.0, tid="1", person=False):
    return ZoneObject(tid, "person" if person else "blanket", slack, err, hazard, person)


def obs(t, *objects, power=ON, indicator=True, camera=True, calib=True, api=True):
    return Observation(t, tuple(objects), camera, calib, api, power, indicator)


def drive_to_shutoff(sm, t0=0.0):
    sm.step(obs(t0, blanket(-10)))
    return sm.step(obs(t0 + 10, blanket(-10)))


def test_clear_when_empty():
    d = SafetyStateMachine(CFG).step(obs(0))
    assert d.state is State.CLEAR and d.actions == ()


def test_watch_near_boundary_alerts_once():
    sm = SafetyStateMachine(CFG)
    d1 = sm.step(obs(0, blanket(4)))
    d2 = sm.step(obs(1, blanket(4)))
    assert d1.state is State.WATCH and Action.WATCH_ALERT in d1.actions
    assert d2.state is State.WATCH and d2.actions == ()


def test_error_bar_is_conservative():
    # 0.5 in outside the rule but error is 1 in -> counts as a violation
    d = SafetyStateMachine(CFG).step(obs(0, blanket(0.5, err=1.0)))
    assert d.state is State.WARN


def test_warn_then_shutoff_after_dwell():
    sm = SafetyStateMachine(CFG)
    d = sm.step(obs(0, blanket(-10)))
    assert d.state is State.WARN and Action.WARN_ALERT in d.actions and d.countdown_s == 10
    d = sm.step(obs(9.9, blanket(-10)))
    assert d.state is State.WARN and Action.TURN_OFF not in d.actions
    d = sm.step(obs(10, blanket(-10)))
    assert d.state is State.VERIFYING and Action.TURN_OFF in d.actions


def test_leaving_zone_cancels_countdown():
    sm = SafetyStateMachine(CFG)
    sm.step(obs(0, blanket(-10)))
    assert sm.step(obs(5)).state is State.CLEAR
    d = sm.step(obs(6, blanket(-10)))
    assert d.state is State.WARN and d.countdown_s == 10


def test_low_hazard_waits_longer_but_still_shuts_off():
    sm = SafetyStateMachine(CFG)
    sm.step(obs(0, blanket(-10, "low")))
    assert sm.step(obs(30, blanket(-10, "low"))).state is State.WARN
    assert sm.step(obs(60, blanket(-10, "low"))).state is State.VERIFYING


def test_api_down_forces_high_hazard():
    sm = SafetyStateMachine(CFG)
    sm.step(obs(0, blanket(-10, "low"), api=False))
    d = sm.step(obs(10, blanket(-10, "low"), api=False))
    assert d.state is State.VERIFYING and "API_DOWN" in d.degraded


def test_person_in_zone_warns_but_never_cuts_power():
    sm = SafetyStateMachine(CFG)
    for t in range(0, 120, 10):
        d = sm.step(obs(t, blanket(-20, person=True)))
        assert d.state is State.WATCH and Action.TURN_OFF not in d.actions


def test_confirmed_requires_both_signals():
    sm = SafetyStateMachine(CFG)
    drive_to_shutoff(sm)
    d = sm.step(obs(11, power=0.0, indicator=True))  # watts dropped but light still on
    assert d.state is State.VERIFYING
    d = sm.step(obs(12, power=0.0, indicator=False))
    assert d.state is State.OFF_CONFIRMED and Action.CONFIRMED in d.actions
    assert d.signals == {"power_off": True, "indicator_off": True}


def test_unconfirmed_retries_then_alarms():
    sm = SafetyStateMachine(CFG)
    drive_to_shutoff(sm)  # t=10, TURN_OFF
    assert Action.TURN_OFF in sm.step(obs(15)).actions  # retry 1
    assert Action.TURN_OFF in sm.step(obs(20)).actions  # retry 2
    d = sm.step(obs(25))
    assert d.state is State.OFF_UNCONFIRMED and Action.ALARM in d.actions


def test_plug_unreachable_cannot_confirm():
    sm = SafetyStateMachine(CFG)
    drive_to_shutoff(sm)
    d = sm.step(obs(11, power=None, indicator=False))
    assert d.state is State.VERIFYING and "PLUG_UNREACHABLE" in d.degraded


def test_latched_off_until_reset_and_reset_requires_clear_zone():
    sm = SafetyStateMachine(CFG)
    drive_to_shutoff(sm)
    sm.step(obs(11, power=0.0, indicator=False))
    assert sm.step(obs(100, power=0.0, indicator=False)).state is State.OFF_CONFIRMED
    assert sm.reset(zone_clear=False) is False
    assert sm.reset(zone_clear=True) is True
    assert sm.step(obs(101)).state is State.CLEAR


def test_camera_lost_is_unknown_and_failsafe_cuts_power():
    sm = SafetyStateMachine(CFG)
    d = sm.step(obs(0, camera=False))
    assert d.banner == "UNKNOWN — not safe" and Action.DEGRADED_ALERT in d.actions
    assert sm.step(obs(29, camera=False)).state is State.CLEAR
    d = sm.step(obs(30, camera=False))
    assert d.state is State.VERIFYING and Action.TURN_OFF in d.actions


def test_failsafe_skipped_when_heater_already_off():
    sm = SafetyStateMachine(CFG)
    sm.step(obs(0, camera=False, power=0.0))
    assert sm.step(obs(60, camera=False, power=0.0)).state is State.CLEAR


def test_camera_lost_during_warn_keeps_countdown():
    sm = SafetyStateMachine(CFG)
    sm.step(obs(0, blanket(-10)))
    assert sm.step(obs(5, camera=False)).state is State.WARN
    assert sm.step(obs(10, camera=False)).state is State.VERIFYING


def test_calibration_drift_counts_as_blind():
    sm = SafetyStateMachine(CFG)
    d = sm.step(obs(0, calib=False))
    assert "CALIBRATION_DRIFT" in d.degraded and d.banner.startswith("UNKNOWN")
