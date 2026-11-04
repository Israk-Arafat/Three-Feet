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
    failsafe_enabled=True, failsafe_after_s=30, off_power_w=5,
)
ON = 1500.0


def blanket(slack, hazard="high", err=1.0, tid="1", person=False):
    return ZoneObject(tid, "person" if person else "blanket", slack, err, hazard, person)


def obs(t, *objects, power=ON, camera=True, calib=True, api=True):
    return Observation(t, tuple(objects), camera, calib, api, power)


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


def test_outside_error_overlap_is_amber_without_shutoff():
    sm = SafetyStateMachine(SafetyConfig(watch_margin_in=2))
    for t in (0, 10, 60):
        d = sm.step(obs(t, blanket(.5, err=11)))
        assert d.state is State.WATCH and d.countdown_s is None
        assert Action.TURN_OFF not in d.actions


def test_drill_outside_two_inch_band_never_triggers_shutoff():
    sm = SafetyStateMachine(SafetyConfig(watch_margin_in=2))
    for t in (0, 10, 60):
        d = sm.step(obs(t, blanket(20.2 - 15, err=11)))
        assert d.state is State.CLEAR and d.countdown_s is None
        assert Action.TURN_OFF not in d.actions


def test_leaving_red_zone_cancels_countdown_even_if_error_overlaps():
    sm = SafetyStateMachine(SafetyConfig(watch_margin_in=2))
    assert sm.step(obs(0, blanket(-1))).state is State.WARN
    assert sm.step(obs(9, blanket(1, err=11))).state is State.WATCH
    d = sm.step(obs(60, blanket(1, err=11)))
    assert d.state is State.WATCH and Action.TURN_OFF not in d.actions


def test_warn_then_shutoff_after_dwell():
    sm = SafetyStateMachine(CFG)
    d = sm.step(obs(0, blanket(-10)))
    assert d.state is State.WARN and Action.WARN_ALERT in d.actions and d.countdown_s == 10
    d = sm.step(obs(9.9, blanket(-10)))
    assert d.state is State.WARN and Action.TURN_OFF not in d.actions
    d = sm.step(obs(10, blanket(-10)))
    assert d.state is State.OFF_REQUESTED and Action.TURN_OFF in d.actions


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
    assert sm.step(obs(60, blanket(-10, "low"))).state is State.OFF_REQUESTED


def test_api_not_ready_waits_for_model_before_starting_dwell():
    sm = SafetyStateMachine(CFG)
    sm.step(obs(0, blanket(-10, "low"), api=False))
    d = sm.step(obs(10, blanket(-10, "low"), api=False))
    assert d.state is State.WATCH and "API_DOWN" in d.degraded
    assert d.countdown_s is None and Action.TURN_OFF not in d.actions


def test_person_in_zone_warns_but_never_cuts_power():
    sm = SafetyStateMachine(CFG)
    for t in range(0, 120, 10):
        d = sm.step(obs(t, blanket(-20, person=True)))
        assert d.state is State.WATCH and Action.TURN_OFF not in d.actions


def test_unclassified_movement_waits_for_sam_then_starts_full_dwell():
    sm = SafetyStateMachine(CFG)
    pending = ZoneObject("motion", "unclassified movement", -10, 1, awaiting_model=True)
    for t in (0, 10, 60):
        d = sm.step(obs(t, pending))
        assert d.state is State.WATCH and d.countdown_s is None
        assert Action.TURN_OFF not in d.actions
    d = sm.step(obs(61, blanket(-10, tid="sam-blanket")))
    assert d.state is State.WARN and d.countdown_s == 10
    assert sm.step(obs(70, blanket(-10, tid="sam-blanket"))).state is State.WARN
    assert sm.step(obs(71, blanket(-10, tid="sam-blanket"))).state is State.OFF_REQUESTED


def test_different_object_does_not_inherit_previous_countdown():
    sm = SafetyStateMachine(CFG)
    sm.step(obs(0, blanket(-10, tid="first")))
    d = sm.step(obs(9, blanket(-10, tid="second")))
    assert d.countdown_s == 10
    assert sm.step(obs(10, blanket(-10, tid="second"))).state is State.WARN
    assert sm.step(obs(19, blanket(-10, tid="second"))).state is State.OFF_REQUESTED


def test_pending_model_response_resets_elapsed_dwell():
    sm = SafetyStateMachine(CFG)
    sm.step(obs(0, blanket(-10)))
    d = sm.step(obs(9, blanket(-10), api=False))
    assert d.state is State.WATCH and d.countdown_s is None
    assert sm.step(obs(10, blanket(-10))).countdown_s == 10
    assert sm.step(obs(19, blanket(-10))).state is State.WARN


def test_off_request_is_immediate_and_has_no_confirmation_countdown():
    sm = SafetyStateMachine(CFG)
    d = drive_to_shutoff(sm)
    assert d.state is State.OFF_REQUESTED and d.countdown_s is None
    for t in (11,15,20,100):
        d = sm.step(obs(t))
        assert d.state is State.OFF_REQUESTED
        assert Action.TURN_OFF not in d.actions





def test_unreachable_plug_keeps_request_latched_and_reports_degraded():
    sm = SafetyStateMachine(CFG)
    drive_to_shutoff(sm)
    d = sm.step(obs(11, power=None))
    assert d.state is State.OFF_REQUESTED and 'PLUG_UNREACHABLE' in d.degraded



def test_latched_off_until_reset_and_reset_requires_clear_zone():
    sm = SafetyStateMachine(CFG)
    drive_to_shutoff(sm)
    sm.step(obs(11, power=0.0))
    assert sm.step(obs(100, power=0.0)).state is State.OFF_REQUESTED
    assert sm.reset(zone_clear=False) is False
    assert sm.reset(zone_clear=True) is True
    assert sm.step(obs(101)).state is State.CLEAR


def test_power_back_on_while_latched_resumes_monitoring():
    sm = SafetyStateMachine(CFG)
    drive_to_shutoff(sm)
    sm.step(obs(11, blanket(-10), power=0.0))
    assert sm.state is State.OFF_REQUESTED
    # Someone switches the heater on with the blanket still there: never keep saying "off".
    d = sm.step(obs(20, blanket(-10), power=ON))
    assert Action.POWER_RESTORED in d.actions
    assert d.state is State.WARN and Action.WARN_ALERT in d.actions
    assert sm.step(obs(30, blanket(-10))).state is State.OFF_REQUESTED


def test_camera_lost_is_unknown_and_failsafe_cuts_power():
    sm = SafetyStateMachine(CFG)
    d = sm.step(obs(0, camera=False))
    assert d.banner == "UNKNOWN — not safe" and Action.DEGRADED_ALERT in d.actions
    assert sm.step(obs(29, camera=False)).state is State.CLEAR
    d = sm.step(obs(30, camera=False))
    assert d.state is State.OFF_REQUESTED and Action.TURN_OFF in d.actions


def test_failsafe_skipped_when_heater_already_off():
    sm = SafetyStateMachine(CFG)
    sm.step(obs(0, camera=False, power=0.0))
    assert sm.step(obs(60, camera=False, power=0.0)).state is State.CLEAR


def test_camera_lost_during_warn_keeps_countdown():
    sm = SafetyStateMachine(CFG)
    sm.step(obs(0, blanket(-10)))
    assert sm.step(obs(5, camera=False)).state is State.WARN
    assert sm.step(obs(10, camera=False)).state is State.OFF_REQUESTED


def test_calibration_drift_counts_as_blind():
    sm = SafetyStateMachine(CFG)
    d = sm.step(obs(0, calib=False))
    assert "CALIBRATION_DRIFT" in d.degraded and d.banner.startswith("UNKNOWN")


# Plugs without a power meter (e.g. GE Cync) report only their relay state.
def relay_obs(t, *objects, plug_on=True, camera=True):
    return Observation(t, tuple(objects), camera, True, True, None, plug_on)


def test_relay_only_plug_does_not_need_light_evidence():
    sm = SafetyStateMachine(CFG)
    assert sm.step(relay_obs(0, blanket(-10))).state is State.WARN
    assert sm.step(relay_obs(10, blanket(-10))).state is State.OFF_REQUESTED
    d = sm.step(relay_obs(11, plug_on=False))
    assert d.state is State.OFF_REQUESTED and not d.actions



def test_relay_only_missing_status_is_degraded():
    sm = SafetyStateMachine(CFG)
    sm.step(relay_obs(0, blanket(-10)))
    sm.step(relay_obs(10, blanket(-10)))
    d = sm.step(relay_obs(11, plug_on=None))
    assert d.state is State.OFF_REQUESTED and 'PLUG_UNREACHABLE' in d.degraded



def test_relay_only_failsafe_skipped_when_plug_reports_off():
    sm = SafetyStateMachine(CFG)
    sm.step(relay_obs(0, camera=False, plug_on=False))
    assert sm.step(relay_obs(60, camera=False, plug_on=False)).state is State.CLEAR
    d = sm.step(relay_obs(61, camera=False, plug_on=True))
    assert d.state is State.OFF_REQUESTED and Action.TURN_OFF in d.actions


def test_relay_only_power_restored_while_latched():
    sm = SafetyStateMachine(CFG)
    sm.step(relay_obs(0, blanket(-10)))
    sm.step(relay_obs(10, blanket(-10)))
    assert sm.step(relay_obs(11, blanket(-10), plug_on=False)).state is State.OFF_REQUESTED
    d = sm.step(relay_obs(20, blanket(-10), plug_on=True))
    assert Action.POWER_RESTORED in d.actions and d.state is State.WARN
