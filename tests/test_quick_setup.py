import json

import cv2
import numpy as np
import pytest

from three_feet_app.calibration import CalibrationFile, calibrate_from_heater, camera_matrix, heater_floor_corners
from three_feet_app.geometry import ClearanceMap, Clearances

W_IN, D_IN = 14.0, 9.0
IMG = (1280, 720)


class PhoneCamera:
    """Pinhole camera standing in front of the heater, a bit to the viewer's right.

    Heater frame (same as `heater_floor_corners` and the sim): back-left corner at 0, +x toward the viewer's
    right, +y toward the front. Seen from above with z up that frame is mirrored, so world = (-x, y).
    """

    def __init__(self, dist_in=72.0, height_in=40.0, hfov_deg=66.0):
        self.K = camera_matrix(IMG, hfov_deg)
        c = np.array([-W_IN / 2 - 10, D_IN + dist_in, height_in])
        z = np.array([-W_IN / 2, D_IN / 2, 0.0]) - c
        z /= np.linalg.norm(z)
        x = np.cross(z, [0, 0, 1.0])
        x /= np.linalg.norm(x)
        self.R = np.stack([x, np.cross(z, x), z])
        self.t = -self.R @ c

    def project(self, floor_pts) -> np.ndarray:
        f = np.asarray(floor_pts, float) * [-1.0, 1.0]
        p = np.c_[f, np.zeros(len(f))] @ self.R.T + self.t
        p = p @ self.K.T
        return p[:, :2] / p[:, 2:]

    def mask(self, floor_poly) -> np.ndarray:
        m = np.zeros((IMG[1], IMG[0]), np.uint8)
        cv2.fillPoly(m, [self.project(floor_poly).round().astype(np.int32)], 1)
        return m.astype(bool)


def box_in_front(gap_in):
    x0, y0 = 2.0, D_IN + gap_in
    return [(x0, y0), (x0 + 10, y0), (x0 + 10, y0 + 9), (x0, y0 + 9)]


def quick_setup(cam, **kw):
    return calibrate_from_heater(IMG, cam.project(heater_floor_corners(W_IN, D_IN)), W_IN, D_IN, **kw)


def measure(cal, mask):
    cmap = ClearanceMap(cal.floor, cal.heater, Clearances(), mask_edge_px=0, parallax_in=0,
                        distance_err_frac=cal.distance_err_frac)
    return cmap.measure(mask)


def test_heater_quick_setup_measures_distance():
    cam = PhoneCamera()
    fl, fr, br, bl = cam.project(heater_floor_corners(W_IN, D_IN))
    assert fl[0] < fr[0] and fl[1] > bl[1]  # front-left is on the image left, front is nearer the camera
    cal = quick_setup(cam)
    assert cal.method == "heater"
    assert cal.heater.width_in == pytest.approx(W_IN)
    assert cal.heater.depth_in == pytest.approx(D_IN)
    assert cal.floor.rms_in < 0.05
    assert 0.03 <= cal.distance_err_frac <= 0.3  # about +/-6 in at 36 in for a phone 6 ft away

    m = measure(cal, cam.mask(box_in_front(12)))
    assert m.side == "front"
    assert m.distance_in == pytest.approx(12, abs=0.3)
    assert m.err_in == pytest.approx(cal.floor.rms_in + cal.distance_err_frac * m.distance_in, abs=0.05)
    assert m.status(6) == "violation"

    right = measure(cal, cam.mask([(W_IN + 8, 1), (W_IN + 16, 1), (W_IN + 16, 8), (W_IN + 8, 8)]))
    assert right.side == "right" and right.distance_in == pytest.approx(8, abs=0.3)


def test_dashboard_setup_computes_error_instead_of_using_fifty_percent_fallback():
    from three_feet_app.setup_service import SetupService

    service = SetupService.__new__(SetupService)
    service._state = {"quality": None}
    cam = PhoneCamera()
    corners = cam.project(heater_floor_corners(W_IN, D_IN))
    cal = service._geometry(np.zeros((IMG[1], IMG[0], 3), np.uint8), corners, W_IN, D_IN,
                            allow_uncertain=True)
    assert .03 <= cal.distance_err_frac < .3


@pytest.mark.parametrize("true_fov", [58.0, 74.0])
def test_wrong_fov_guess_stays_inside_the_error_bar(true_fov):
    cam = PhoneCamera(hfov_deg=true_fov)
    cal = quick_setup(cam)  # still assumes 66 degrees
    for gap in (6, 18, 30):
        m = measure(cal, cam.mask(box_in_front(gap)))
        assert abs(m.distance_in - gap) <= m.err_in + 0.5


def test_error_grows_with_distance_and_with_a_far_camera():
    cam = PhoneCamera()
    cal = quick_setup(cam)
    near, far = measure(cal, cam.mask(box_in_front(6))), measure(cal, cam.mask(box_in_front(30)))
    assert far.err_in > near.err_in * 3
    assert quick_setup(PhoneCamera(dist_in=120, height_in=60)).distance_err_frac > cal.distance_err_frac


def test_quick_setup_rejects_bad_size():
    with pytest.raises(ValueError):
        calibrate_from_heater(IMG, PhoneCamera().project(heater_floor_corners(W_IN, D_IN)), 0, D_IN)


def test_calibration_file_round_trip_keeps_method(tmp_path):
    cal = quick_setup(PhoneCamera())
    cal.indicator_roi = (10, 20, 30, 40)
    path = tmp_path / "calibration.json"
    cal.save(path)
    loaded = CalibrationFile.load(path)
    assert loaded.method == "heater"
    assert loaded.distance_err_frac == pytest.approx(cal.distance_err_frac)
    assert loaded.indicator_roi == (10, 20, 30, 40)
    np.testing.assert_allclose(loaded.floor.H, cal.floor.H)


def test_old_calibration_files_default_to_markers(tmp_path):
    path = tmp_path / "old.json"
    quick_setup(PhoneCamera()).save(path)
    data = json.loads(path.read_text())
    del data["method"], data["distance_err_frac"]
    path.write_text(json.dumps(data))
    loaded = CalibrationFile.load(path)
    assert loaded.method == "markers" and loaded.distance_err_frac == 0.0
