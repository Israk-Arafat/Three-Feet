import threading
import time
from types import SimpleNamespace

import numpy as np

from three_feet_app.perception.meta_client import CostMeter
from three_feet_app.perception.pipeline import MetaPerception
from three_feet_app.perception.sam import Detection, SamResult
from three_feet_app.perception.spark import ALWAYS_ON_CONCEPTS
from three_feet_app.sources import Frame


class FakeSam:
    def __init__(self):
        self.present = True
        self.calls = 0

    def segment_many(self, image, concepts):
        self.calls += 1
        self.last_concepts = list(concepts)
        if not self.present:
            return []
        mask = np.zeros((20, 20), bool)
        mask[5:15, 5:15] = True
        return [SamResult("cloth", [Detection("cloth", "1", (5, 5, 15, 15), mask, 0.9)], 0.01)]


class SlowSpark:
    def __init__(self):
        self.release = threading.Event()

    def inventory(self, view):
        self.release.wait(5)
        return ["cloth"]

    def assess(self, *args, **kwargs):
        raise RuntimeError("not needed")


def _frame(i):
    return Frame(np.zeros((20, 20, 3), np.uint8), time.time(), i)


def _wait(cond, timeout=2.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def _perception(sam, spark):
    return MetaPerception(sam, spark, CostMeter(), sam_period_s=0.0, idle_refresh_s=1.0)


def test_slow_inventory_does_not_block_segmentation():
    sam, spark = FakeSam(), SlowSpark()
    p = _perception(sam, spark)
    try:
        p.on_frame(_frame(1), 1.0, True)
        assert _wait(lambda: p.current()), "segmentation waited on the inventory call"
    finally:
        spark.release.set()
        p.shutdown()


def test_removed_object_ages_out_without_motion():
    sam, spark = FakeSam(), SlowSpark()
    spark.release.set()
    p = _perception(sam, spark)
    try:
        p.on_frame(_frame(1), 1.0, True)
        assert _wait(lambda: p.current())
        assert _wait(lambda: not p._busy)
        sam.present = False
        t = 2.0
        # Motion only starts the first cycle after removal; the settling rule must carry the rest.
        while p.current() and t < 20.0:
            p.on_frame(_frame(int(t)), t, t == 2.0)
            time.sleep(0.05)
            t += 1.0
        assert p.current() == []
    finally:
        p.shutdown()


class QuietSpark:
    def inventory(self, view):
        return ["cloth"]

    def assess(self, *args):
        return SimpleNamespace(level="high", why="Test fabric", material_guess="fabric")


def test_person_is_requested_even_if_inventory_returns_only_cloth():
    sam = FakeSam()
    sam.present = False
    p = _perception(sam, QuietSpark())
    try:
        p.concepts = ["cloth"]
        p.on_frame(_frame(1), 1, True)
        assert _wait(lambda: not p._busy)
        assert "person" in sam.last_concepts
    finally:
        p.shutdown()


def test_person_and_overlapping_worn_clothes_survive_deduplication():
    class PeopleSam:
        def segment_many(self, image, concepts):
            mask = np.zeros(image.shape[:2], bool)
            mask[5:15, 5:15] = True
            return [SamResult("people", [
                Detection("person", "1", (5, 5, 15, 15), mask, .8),
                Detection("clothing", "2", (5, 5, 15, 15), mask.copy(), .99),
            ], .01)]

    p = _perception(PeopleSam(), QuietSpark())
    try:
        p.on_frame(_frame(1), 1, True)
        assert _wait(lambda: not p._busy)
        by_label = {o.label: o for o in p.current()}
        assert set(by_label) == {"person", "clothing"}
        assert by_label["clothing"].extra["on_person"] is True
        assert not by_label["person"].assessing
    finally:
        p.shutdown()


def test_health_requires_a_successful_fresh_frame_and_exposes_its_identity():
    now = [100.0]
    sam = FakeSam()
    sam.present = False
    p = MetaPerception(sam, QuietSpark(), CostMeter(), clock=lambda: now[0])
    try:
        assert not p.api_ok
        p.on_frame(Frame(_frame(1).image, 100, 7), 100, True)
        assert _wait(lambda: not p._busy)
        assert p.api_ok and p.last_frame_index == 7 and p.result_age_s == 0
        now[0] = 106
        assert not p.api_ok and "stale" in p.health_reason
        # A slow call completing now cannot make its old input frame fresh.
        p.on_frame(Frame(_frame(1).image, 100, 8), 106, True)
        assert _wait(lambda: not p._busy)
        assert not p.api_ok
        now[0] = 107
        p.on_frame(Frame(_frame(1).image, 107, 9), 107, True)
        assert _wait(lambda: not p._busy)
        assert p.api_ok and p.last_frame_index == 9
    finally:
        p.shutdown()


def test_segmentation_failure_never_recovers_just_because_time_passed():
    now = [100.0]
    sam = FakeSam()
    sam.present = False
    original = sam.segment_many
    p = MetaPerception(sam, QuietSpark(), CostMeter(), idle_refresh_s=1, clock=lambda: now[0])
    try:
        p.on_frame(Frame(_frame(1).image, 100, 1), 100, True)
        assert _wait(lambda: not p._busy) and p.api_ok

        def fail(*args):
            raise ConnectionError("segmentation unavailable")

        sam.segment_many = fail
        now[0] = 101
        p.on_frame(Frame(_frame(1).image, 101, 2), 101, True)
        assert _wait(lambda: not p._busy)
        now[0] = 120
        assert not p.api_ok and "segmentation unavailable" in p.health_reason
        sam.segment_many = original
        p.on_frame(Frame(_frame(1).image, 120, 3), 120, True)
        assert _wait(lambda: not p._busy) and p.api_ok
    finally:
        p.shutdown()


def test_budget_pause_is_unhealthy_even_with_a_recent_success():
    sam = FakeSam()
    sam.present = False
    meter = CostMeter(budget_usd_per_hour=1)
    p = MetaPerception(sam, QuietSpark(), meter, clock=lambda: 100)
    try:
        p.on_frame(Frame(_frame(1).image, 100, 1), 100, True)
        assert _wait(lambda: not p._busy) and p.api_ok
        meter.add("test", 1)
        p.on_frame(Frame(_frame(1).image, 101, 2), 101, True)
        assert sam.calls == 1 and not p.api_ok
        assert "budget exhausted" in p.health_reason
    finally:
        p.shutdown()


def test_stalled_call_expires_the_previous_success():
    now = [100.0]
    sam = FakeSam()
    sam.present = False
    p = MetaPerception(sam, QuietSpark(), CostMeter(), idle_refresh_s=1, clock=lambda: now[0])
    entered, release = threading.Event(), threading.Event()
    original = sam.segment_many
    try:
        p.on_frame(Frame(_frame(1).image, 100, 1), 100, True)
        assert _wait(lambda: not p._busy) and p.api_ok

        def slow(*args):
            entered.set()
            release.wait(3)
            return original(*args)

        sam.segment_many = slow
        now[0] = 101
        p.on_frame(Frame(_frame(1).image, 101, 2), 101, True)
        assert entered.wait(1)
        now[0] = 107
        assert p._busy and not p.api_ok
        release.set()
        assert _wait(lambda: not p._busy)
        assert not p.api_ok  # completion time does not replace the input frame timestamp
    finally:
        release.set()
        p.shutdown()


def test_reset_forgets_the_old_camera_and_ignores_a_cycle_still_running():
    sam = FakeSam()
    now = [100.0]
    p = MetaPerception(sam, QuietSpark(), CostMeter(), clock=lambda: now[0])
    entered, release = threading.Event(), threading.Event()
    original = sam.segment_many
    try:
        p.on_frame(Frame(_frame(1).image, 100, 1), 100, True)
        assert _wait(lambda: p.current() and not p._busy)
        p.reset()
        assert p.current() == [] and not p.api_ok and p.last_frame_index is None

        def slow(*args):
            entered.set()
            release.wait(3)
            return original(*args)

        sam.segment_many = slow
        now[0] = 101
        p.on_frame(Frame(_frame(1).image, 101, 2), 101, True)
        assert entered.wait(1)
        p.reset()  # the camera changed while this cycle was looking at the old view
        release.set()
        time.sleep(0.2)  # let the stale cycle finish; its result must be dropped
        assert p.current() == [] and not p.api_ok
    finally:
        release.set()
        p.shutdown()


def test_one_minute_of_held_motion_uses_two_sam_cycles_with_fresh_local_tracking():
    now = [100.0]
    meter = CostMeter()

    class CountingSam(FakeSam):
        def segment_many(self, image, concepts):
            for _ in concepts:
                meter.add("sam_image", 0.0025)
            return super().segment_many(image, concepts)

    class CountingSpark(QuietSpark):
        calls = 0

        def inventory(self, view):
            self.calls += 1
            return list(ALWAYS_ON_CONCEPTS)

    sam, spark = CountingSam(), CountingSpark()
    sam.present = False
    p = MetaPerception(sam, spark, meter, clock=lambda: now[0])
    try:
        for second in range(60):
            now[0] = 100 + second
            p.on_frame(Frame(_frame(0).image, now[0], second), now[0], True)
            assert _wait(lambda: not p._busy)
            assert p.api_ok
        assert sam.calls == 2
        expected_calls = 2 * len(ALWAYS_ON_CONCEPTS)
        assert meter.calls["sam_image"] == expected_calls
        assert abs(meter.total_usd - expected_calls * .0025) < 1e-9
        assert spark.calls == 1
        assert p.last_frame_index == 59 and p.result_age_s == 0
        assert p.verification_age_s == 29 and p.local_tracking_active
    finally:
        p.shutdown()


def test_new_scene_content_triggers_early_check_even_without_motion_flag():
    now = [100.0]
    sam = FakeSam()
    sam.present = False
    p = MetaPerception(sam, QuietSpark(), CostMeter(), clock=lambda: now[0])
    try:
        image = np.zeros((100, 100, 3), np.uint8)
        p.on_frame(Frame(image, 100, 1), 100, False)
        assert _wait(lambda: not p._busy)
        now[0] = 101
        image = image.copy()
        image[40:60, 40:60] = 255
        p.on_frame(Frame(image, 101, 2), 101, False)
        assert _wait(lambda: not p._busy)
        assert sam.calls == 2 and p.api_ok and p.verification_age_s == 0
    finally:
        p.shutdown()


def test_ongoing_local_frames_cannot_extend_stalled_periodic_verification_deadline():
    now = [100.0]
    sam = FakeSam()
    sam.present = False
    p = MetaPerception(sam, QuietSpark(), CostMeter(), clock=lambda: now[0])
    entered, release = threading.Event(), threading.Event()
    original = sam.segment_many
    try:
        image = _frame(0).image
        p.on_frame(Frame(image, 100, 0), 100, False)
        assert _wait(lambda: not p._busy)

        def slow(*args):
            entered.set()
            release.wait(5)
            return original(*args)

        sam.segment_many = slow
        for second in range(1, 47):
            now[0] = 100 + second
            p.on_frame(Frame(image, now[0], second), now[0], False)
            if second == 30:
                assert entered.wait(1)
        assert p._busy and p.result_age_s == 0
        assert not p.api_ok and "verification is stale" in p.health_reason
        release.set()
        assert _wait(lambda: not p._busy)
        assert not p.api_ok  # the old request's completion cannot refresh camera health
    finally:
        release.set()
        p.shutdown()


def test_failed_verification_is_not_hidden_by_more_local_frames():
    now = [100.0]
    sam = FakeSam()
    sam.present = False
    p = MetaPerception(sam, QuietSpark(), CostMeter(), idle_refresh_s=2, clock=lambda: now[0])
    try:
        image = _frame(0).image
        p.on_frame(Frame(image, 100, 0), 100, False)
        assert _wait(lambda: not p._busy)

        def fail(*args):
            raise ConnectionError("offline")

        sam.segment_many = fail
        for second in range(1, 5):
            now[0] = 100 + second
            p.on_frame(Frame(image, now[0], second), now[0], False)
            assert _wait(lambda: not p._busy)
        assert not p.api_ok and "offline" in p.health_reason
    finally:
        p.shutdown()


def test_duplicate_old_and_out_of_order_frames_cannot_refresh_local_health():
    now = [100.0]
    sam = FakeSam()
    sam.present = False
    p = MetaPerception(sam, QuietSpark(), CostMeter(), clock=lambda: now[0])
    try:
        image = _frame(0).image
        p.on_frame(Frame(image, 100, 1), 100, False)
        assert _wait(lambda: not p._busy)
        for second in range(1, 7):
            now[0] = 100 + second
            p.on_frame(Frame(image, now[0], 1), now[0], False)  # duplicate identity
            p.on_frame(Frame(image, 99, second + 10), now[0], False)
        assert p.last_frame_ts == 100 and sam.calls == 1
        assert not p.api_ok and "stale" in p.health_reason
    finally:
        p.shutdown()


def test_new_scene_stays_unhealthy_until_verification_completes():
    now = [100.0]
    sam = FakeSam()
    sam.present = False
    p = MetaPerception(sam, QuietSpark(), CostMeter(), clock=lambda: now[0])
    entered, release = threading.Event(), threading.Event()
    original = sam.segment_many
    try:
        image = np.zeros((100, 100, 3), np.uint8)
        p.on_frame(Frame(image, 100, 0), 100, False)
        assert _wait(lambda: not p._busy)

        def slow(*args):
            entered.set()
            release.wait(5)
            return original(*args)

        sam.segment_many = slow
        now[0] = 101
        changed = image.copy()
        changed[40:60, 40:60] = 255
        p.on_frame(Frame(changed, 101, 1), 101, False)
        assert entered.wait(1)
        assert not p.api_ok and "Scene changed" in p.health_reason
        now[0] = 102
        p.on_frame(Frame(changed, 102, 2), 102, False)
        assert not p.api_ok
        release.set()
        assert _wait(lambda: not p._busy)
        assert p.api_ok
    finally:
        release.set()
        p.shutdown()
