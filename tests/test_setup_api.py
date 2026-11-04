import copy
import threading
import time

import pytest
import numpy as np
from fastapi.testclient import TestClient

from three_feet import app as app_module, setup_service


def test_small_user_selection_produces_untrusted_preview():
    service = setup_service.SetupService.__new__(setup_service.SetupService)
    service._state = {"quality": None}
    # Approximate points from a distant heater image, including a point on the body.
    corners = [(304, 753), (377, 727), (329, 643), (268, 700)]
    cal = service._geometry(np.zeros((960, 720, 3), np.uint8), corners, 4.5, 4.35,
                            allow_uncertain=True)
    assert cal.distance_err_frac >= .3
    assert np.isfinite(cal.floor.H).all()


@pytest.fixture
def setup_client(tmp_path, monkeypatch):
    monkeypatch.setenv("TF_SOURCE", "sim")
    monkeypatch.setenv("TF_AUTOPLAY", "0")
    monkeypatch.setenv("MODEL_API_KEY", "")
    monkeypatch.setattr(setup_service, "PREFERENCES_PATH", tmp_path / "dimensions.json")
    monkeypatch.setattr(setup_service, "CALIBRATION_PATH", tmp_path / "calibration.json")
    monkeypatch.setattr(setup_service, "RUNS_DIR", tmp_path)
    app_module.get_settings.cache_clear()
    with TestClient(app_module.create_app()) as c:
        for _ in range(50):
            if c.get("/api/state").json().get("camera", {}).get("healthy"):
                break
            time.sleep(.1)
        yield c
    app_module.get_settings.cache_clear()


def test_dimensions_and_frozen_snapshot(setup_client):
    c = setup_client
    assert c.post("/api/setup/dimensions", json={"width_in": 0, "depth_in": 4}).status_code == 422
    body = {"width_in": 4.5, "depth_in": 4.35}
    assert c.post("/api/setup/dimensions", json=body).status_code == 200
    state = c.post("/api/setup/capture", json=body).json()
    assert state["job_id"] and state["image_size"] == [1280, 720]
    image = c.get(state["image_url"])
    assert image.status_code == 200 and image.content.startswith(b"\xff\xd8")
    assert c.get("/api/setup/image?job_id=expired").status_code == 404


def test_manual_rejects_expired_and_degenerate_points_preserving_geometry(setup_client):
    c = setup_client
    engine = c.app.state.engine
    original = engine.calib
    dims = {"width_in": 4.5, "depth_in": 4.35}
    state = c.post("/api/setup/capture", json=dims).json()
    body = {**dims, "job_id": "expired", "corners_px": [[20, 20]] * 4}
    assert c.post("/api/setup/manual", json=body).status_code == 409
    body["job_id"] = state["job_id"]
    assert c.post("/api/setup/manual", json=body).status_code == 422
    assert engine.calib is original and not c.app.state.setup.calibration_path.exists()


def test_manual_applies_live_without_power_command(setup_client, monkeypatch):
    c = setup_client
    engine = c.app.state.engine
    service = c.app.state.setup
    dims = {"width_in": 14, "depth_in": 9}
    state = c.post("/api/setup/capture", json=dims).json()
    calibration = copy.deepcopy(engine.calib)
    calibration.method = "heater"
    monkeypatch.setattr(service, "_geometry", lambda *args, **kwargs: calibration)
    response = c.post("/api/setup/manual", json={**dims, "job_id": state["job_id"],
                     "corners_px": [[500, 400], [600, 400], [600, 300], [500, 300]]})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "ready" and engine.calib is calibration
    assert service.calibration_path.exists() and engine.switch.on_commands == 0
    assert engine.probe is None


def test_base_can_be_used_without_uncertainty_blocking_monitoring(setup_client, monkeypatch):
    c = setup_client
    engine = c.app.state.engine
    service = c.app.state.setup
    dims = {"width_in": 14, "depth_in": 9}
    state = c.post("/api/setup/capture", json=dims).json()
    calibration = copy.deepcopy(engine.calib)
    calibration.method, calibration.distance_err_frac = "heater", .7
    calibration.marker_ref_px = {}
    monkeypatch.setattr(service, "_geometry", lambda *args, **kwargs: calibration)
    response = c.post("/api/setup/manual", json={**dims, "job_id": state["job_id"],
                     "corners_px": [[500, 400], [600, 400], [600, 300], [500, 300]]})
    assert response.status_code == 200 and response.json()["status"] == "ready"
    for _ in range(50):
        current = c.get("/api/state").json()
        if current.get("calibration", {}).get("method") == "heater":
            break
        time.sleep(.05)
    assert "CALIBRATION_DRIFT" not in current["degraded"]


def test_auto_busy_and_uncertain_result_preserve_calibration(setup_client, monkeypatch):
    c = setup_client
    original = c.app.state.engine.calib
    entered, release = threading.Event(), threading.Event()
    def uncertain(*args, **kwargs):
        entered.set()
        release.wait(3)
        return {"heater_found": False, "confidence": .1, "reason": "Base hidden"}
    monkeypatch.setattr(setup_service, "locate_heater_base", uncertain)
    dims = {"width_in": 4.5, "depth_in": 4.35}
    try:
        assert c.post("/api/setup/auto", json=dims).status_code == 202
        assert entered.wait(2)
        assert c.post("/api/setup/capture", json=dims).status_code == 409
    finally:
        release.set()
    for _ in range(50):
        state = c.get("/api/setup").json()
        if state["status"] != "running":
            break
        time.sleep(.05)
    assert state["status"] == "needs_review"
    assert c.app.state.engine.calib is original
    assert c.get(state["overlay_url"]).status_code == 200
