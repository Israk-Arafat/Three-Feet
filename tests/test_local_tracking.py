import cv2
import numpy as np

from three_feet.perception.local_tracking import LocalMaskTracker
from three_feet.perception.sam import Detection
from three_feet.perception.tracking import Tracker


def scene(dx=0, dy=0):
    image = np.full((180, 240, 3), 80, np.uint8)
    texture = np.random.default_rng(42).integers(100, 255, (60, 60, 3), dtype=np.uint8)
    image[50 + dy:110 + dy, 70 + dx:130 + dx] = texture
    mask = np.zeros(image.shape[:2], bool)
    mask[50 + dy:110 + dy, 70 + dx:130 + dx] = True
    return image, mask


def seeded():
    image, mask = scene()
    tracker = Tracker()
    tracker.update([Detection("blanket", "1", (70, 50, 130, 110), mask, 0.9)])
    local = LocalMaskTracker()
    local.seed(image, tracker)
    return image, tracker, local


def test_static_objects_keep_identity_and_hazard_without_model_work():
    image, tracker, local = seeded()
    obj = tracker.objects()[0]
    obj.hazard, obj.why = "medium", "Existing assessment"
    for _ in range(10):
        assert not local.update(image, tracker)
    assert tracker.objects()[0] is obj
    assert obj.hazard == "medium" and obj.misses == 0


def test_textured_object_moves_locally_and_updates_measurement_mask_and_matching_box():
    _, tracker, local = seeded()
    obj = tracker.objects()[0]
    for dx in (3, 6, 9):
        image, expected = scene(dx, 2)
        assert not local.update(image, tracker)
        assert np.count_nonzero(obj.mask & expected) / np.count_nonzero(expected) > 0.95
    assert obj.extra["tracking"] == "local"
    assert tracker._boxes[obj.track_id][0] >= 78
    # The next SAM verification must retain the same ID after local movement.
    assert tracker.update([Detection("blanket", "1", (79, 52, 139, 112), expected)]) == []
    assert tracker.objects()[0] is obj


def test_new_object_outside_known_masks_requests_verification():
    image, tracker, local = seeded()
    image[130:150, 170:195] = 240
    assert local.update(image, tracker)
    # A stationary newcomer remains unexplained until the SAM baseline changes.
    assert local.update(image, tracker)


def test_gradual_background_change_is_compared_with_verified_anchor():
    image, tracker, local = seeded()
    for brightness in (90, 100):
        changed = image.copy()
        changed[130:150, 170:195] = brightness
        assert not local.update(changed, tracker)
    changed[130:150, 170:195] = 120
    assert local.update(changed, tracker)


def test_disappeared_or_occluded_object_keeps_its_mask_and_requests_sam():
    _, tracker, local = seeded()
    original = tracker.objects()[0].mask.copy()
    assert local.update(np.full((180, 240, 3), 80, np.uint8), tracker)
    assert np.array_equal(tracker.objects()[0].mask, original)
    assert tracker.objects()[0].misses == 0


def test_partial_occlusion_requests_verification_even_when_other_features_still_match():
    image, tracker, local = seeded()
    image[98:110, 70:130] = 80
    assert local.update(image, tracker)


def test_camera_resolution_change_requires_model_verification():
    image, tracker, local = seeded()
    assert local.update(cv2.resize(image, (120, 90)), tracker)


def test_local_motion_on_large_frames_keeps_masks_in_source_coordinates():
    image, mask = scene()
    image = cv2.resize(image, (960, 720))
    mask = cv2.resize(mask.astype(np.uint8), (960, 720), interpolation=cv2.INTER_NEAREST).astype(bool)
    tracker = Tracker()
    tracker.update([Detection("blanket", "1", (280, 200, 520, 440), mask)])
    local = LocalMaskTracker()
    local.seed(image, tracker)
    moved = cv2.resize(scene(4, 2)[0], (960, 720))
    assert not local.update(moved, tracker)
    box = tracker._boxes["t1"]
    assert abs(box[0] - 296) <= 2 and abs(box[1] - 208) <= 2
