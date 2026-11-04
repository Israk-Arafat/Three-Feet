"""Automatic setup must reject occluded/invalid geometry and preserve an existing calibration."""

from copy import deepcopy
import json

import numpy as np
import pytest

from scripts import calibrate_auto
from three_feet.perception import setup


def visible_base():
    return {
        "heater_found": True, "base_on_floor": True, "front_identified": True,
        "selected_surface": "floor_contact_base", "confidence": 0.95,
        "reason": "Four distinct corners are visible where the rectangular base contacts the floor.",
        "corners": {name: {"x": x, "y": y, "visible": True, "confidence": 0.95,
                           "evidence": "Clear base edge meets the floor at this corner."}
                    for name, (x, y) in zip(setup.CORNER_NAMES,
                                            [(200, 600), (360, 600), (340, 490), (220, 490)])},
        "indicator": {"visible": False, "confidence": 0, "x": 0, "y": 0, "width": 0, "height": 0},
    }


def test_visible_floor_rectangle_preserves_corner_order():
    corners = setup.validate_base_candidate(visible_base(), (720, 960))
    np.testing.assert_array_equal(corners, [(200, 600), (360, 600), (340, 490), (220, 490)])


@pytest.mark.parametrize("problem", ["hidden", "body", "uncertain", "crossed", "nonfinite", "outside", "tiny"])
def test_rejects_corners_that_cannot_define_floor_scale(problem):
    candidate = visible_base()
    points = candidate["corners"]
    if problem == "hidden":
        points["back_left"]["visible"] = False
    elif problem == "body":
        candidate["selected_surface"] = "body_front"
    elif problem == "uncertain":
        points["front_left"]["confidence"] = 0.5
    elif problem == "crossed":
        points["back_left"], points["back_right"] = points["back_right"], points["back_left"]
    elif problem == "nonfinite":
        points["front_left"]["x"] = float("nan")
    elif problem == "outside":
        points["front_left"]["x"] = 800
    elif problem == "tiny":
        for p in points.values():
            p["x"], p["y"] = p["x"] / 100, p["y"] / 100
    with pytest.raises(ValueError):
        setup.validate_base_candidate(candidate, (720, 960))


def test_refusal_preserves_existing_calibration_and_writes_evidence(tmp_path, monkeypatch):
    old = tmp_path / "calibration.json"
    old.write_text("previous calibration", encoding="utf-8")
    artifacts = tmp_path / "diagnostics"
    candidate = visible_base()
    candidate["corners"]["back_left"]["visible"] = False
    monkeypatch.setattr(calibrate_auto, "capture", lambda *args: np.zeros((960, 720, 3), np.uint8))
    monkeypatch.setattr(calibrate_auto, "locate_heater_base", lambda *args, **kw: deepcopy(candidate))
    monkeypatch.setattr("sys.argv", ["calibrate_auto.py", "--heater", "4.5", "4.35", "--out", str(old),
                                     "--artifacts", str(artifacts)])
    assert calibrate_auto.main() == 2
    assert old.read_text(encoding="utf-8") == "previous calibration"
    result = json.loads((artifacts / "candidate.json").read_text(encoding="utf-8"))
    assert result["accepted"] is False and "hidden" in result["reason"]
    assert (artifacts / "snapshot.jpg").exists() and (artifacts / "candidate_overlay.jpg").exists()


def test_focused_crop_coordinates_map_back_to_original_frame(monkeypatch):
    initial = visible_base()
    initial["confidence"] = 0.1
    calls = []

    def query(image, *args, **kwargs):
        calls.append(image.shape)
        if len(calls) == 1:
            return deepcopy(initial)
        refined = visible_base()
        for point in refined["corners"].values():
            point["x"], point["y"] = image.shape[1] / 2, image.shape[0] / 2
        return refined

    monkeypatch.setattr(setup, "_query_base", query)
    result = setup.locate_heater_base(np.zeros((960, 720, 3), np.uint8), 4.5, 4.35, client=object())
    x, y, w, h = result["focused_crop_px"]
    for point in result["corners"].values():
        assert point["x"] == pytest.approx(x + w / 2)
        assert point["y"] == pytest.approx(y + h / 2)
    assert len(calls) == 2
