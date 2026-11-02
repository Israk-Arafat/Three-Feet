import numpy as np
import pytest

from three_feet.geometry import ClearanceMap, Clearances, FloorCalibration, HeaterFootprint, classify

# Synthetic overhead camera: 10 px per inch, floor origin at pixel (100, 100).
PX_PER_IN = 10.0
IMG = (1000, 800)  # width, height -> 90 x 70 in of floor


def px(x_in, y_in):
    return (100 + x_in * PX_PER_IN, 100 + y_in * PX_PER_IN)


def make_calib():
    floor = [(0, 0), (48, 0), (48, 48), (0, 48)]
    return FloorCalibration.fit([px(*p) for p in floor], floor, IMG)


def make_heater():
    # 12 in wide, 8 in deep, centered at (40, 30), facing +y (down in the image)
    return HeaterFootprint.from_corners((34, 34), (46, 34), (46, 26), (34, 26))


def test_homography_roundtrip_and_scale():
    cal = make_calib()
    assert cal.rms_in < 1e-6
    np.testing.assert_allclose(cal.image_to_floor([px(10, 20)]), [[10, 20]], atol=1e-6)
    np.testing.assert_allclose(cal.floor_to_image([[10, 20]]), [px(10, 20)], atol=1e-6)
    assert cal.inches_per_px(500, 400) == pytest.approx(0.1)


def test_heater_from_corners():
    h = make_heater()
    assert h.width_in == pytest.approx(12)
    assert h.depth_in == pytest.approx(8)
    np.testing.assert_allclose(h.front, [0, 1], atol=1e-9)
    np.testing.assert_allclose(h.center, [40, 30])


def test_per_side_clearance_and_measurement():
    cal = make_calib()
    cmap = ClearanceMap(cal, make_heater(), Clearances(front_in=36, back_in=12, left_in=24, right_in=24),
                        mask_edge_px=2, parallax_in=0)
    # Object 10 in in front of the heater's front face (y = 34 + 10 = 44)
    mask = np.zeros((IMG[1], IMG[0]), bool)
    x0, y0 = map(int, px(38, 44))
    mask[y0 : y0 + 20, x0 : x0 + 40] = True
    m = cmap.measure(mask)
    assert m.side == "front"
    assert m.distance_in == pytest.approx(10, abs=0.15)
    assert m.required_in == 36
    assert m.slack_in == pytest.approx(-26, abs=0.15)
    assert m.err_in == pytest.approx(0.2, abs=0.01)  # 2 px * 0.1 in/px
    assert m.status(6) == "violation"

    # Object 16 in behind the heater (back rule 12 in) -> clear with 6 in watch margin? 16-12=4 -> watch
    mask[:] = False
    x0, y0 = map(int, px(38, 26 - 16))
    mask[y0 - 20 : y0 + 1, x0 : x0 + 40] = True
    m = cmap.measure(mask)
    assert m.side == "back" and m.status(6) == "watch"


def test_corner_uses_stricter_side():
    cal = make_calib()
    cmap = ClearanceMap(cal, make_heater(), Clearances(36, 12, 24, 24), parallax_in=0)
    mask = np.zeros((IMG[1], IMG[0]), bool)
    x, y = map(int, px(46 + 5, 34 + 5))  # diagonal off the front-right corner
    mask[y, x] = True
    m = cmap.measure(mask)
    assert m.required_in == 36
    assert m.distance_in == pytest.approx(np.hypot(5, 5), abs=0.15)


def test_mask_outside_view_returns_none_and_shape_checked():
    cmap = ClearanceMap(make_calib(), make_heater(), Clearances())
    assert cmap.measure(np.zeros((IMG[1], IMG[0]), bool)) is None
    with pytest.raises(ValueError):
        cmap.measure(np.zeros((10, 10), bool))


def test_classify():
    assert classify(-1, 0.5, 6) == "violation"
    assert classify(0.4, 0.5, 6) == "violation"
    assert classify(5, 0.5, 6) == "watch"
    assert classify(10, 0.5, 6) == "clear"
