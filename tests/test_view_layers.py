"""Viewer overlays may change pixels, but must never alter clearance decisions or plug power."""

from copy import deepcopy
import asyncio

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

import three_feet_app.app as app_module
from three_feet_app.config import Settings
from three_feet_app.engine import Engine
from three_feet_app.perception.wonder import ClearanceRule
from three_feet_app.safety.actuator import DryRunPlug
from three_feet_app.sim import SimPerception, SimSource, SimWorld


@pytest.fixture
def engine():
    plug = DryRunPlug(settle_s=0)
    world = SimWorld(plug, speed_in_s=1000)
    calibration = world.calibration()
    engine = Engine(SimSource(world), SimPerception(world), plug, Settings(_env_file=None),
                    calib=calibration, rule=ClearanceRule(36, 36, 36, 36, "default"),
                    mode="sim", events_dir=None)
    world.place("blanket", "near")
    world.objects["blanket"].pos = world.objects["blanket"].target
    engine.tick()
    yield engine
    engine.stop()


def decode(jpeg):
    assert jpeg is not None
    image = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    assert image is not None
    return image


def test_viewer_can_isolate_each_layer_without_changing_safety(engine):
    before_state = deepcopy(engine.state_message)
    before_raw = engine.latest_raw.copy()
    before_objects = [(item.obj.track_id, item.m, item.status) for item in engine._measured]
    hidden = decode(engine.render_view(zone=False, base=False, objects=False))
    zone = decode(engine.render_view(zone=True, base=False, objects=False))
    base = decode(engine.render_view(zone=False, base=True, objects=False))
    objects = decode(engine.render_view(zone=False, base=False, objects=True))

    assert hidden.shape == before_raw.shape
    # Every requested layer adds actual visible information to the camera view.
    for shown in (zone, base, objects):
        assert np.count_nonzero(np.max(np.abs(shown.astype(int) - hidden.astype(int)), axis=2) > 12) > 100
    # With all overlays hidden, the body of the image is simply the camera image (JPEG tolerance).
    assert np.mean(np.abs(hidden[70:].astype(float) - before_raw[70:].astype(float))) < 4
    np.testing.assert_array_equal(engine.latest_raw, before_raw)
    assert engine.state_message == before_state
    assert [(item.obj.track_id, item.m, item.status) for item in engine._measured] == before_objects
    assert engine.sm.state.value == "WARN"
    assert engine.switch.on_commands == 0 and engine.switch.off_commands == 0


def test_geometry_has_camera_coordinates_and_never_invents_missing_calibration(engine):
    geometry = engine.view_geometry()
    assert geometry["configured"] is True
    assert geometry["image_size"] == [1280, 720]
    assert np.asarray(geometry["base_corners_px"]).shape == (4, 2)
    assert geometry["base_width_in"] == pytest.approx(14)
    assert geometry["base_depth_in"] == pytest.approx(9)
    engine.calib = None
    engine._build_geometry()
    geometry = engine.view_geometry()
    assert geometry["configured"] is False
    assert geometry["base_corners_px"] is None
    assert geometry["base_width_in"] is None and geometry["base_depth_in"] is None


def test_geometry_endpoint_and_stream_layer_parameters(engine):
    # Inspect the actual route contract without starting another hardware source or engine loop.
    app = app_module.create_app()
    app.state.engine = engine
    client = TestClient(app)
    response = client.get("/api/view/geometry")
    assert response.status_code == 200 and response.json() == engine.view_geometry()
    parameters = client.get("/openapi.json").json()["paths"]["/stream.mjpg"]["get"]["parameters"]
    assert {item["name"] for item in parameters} == {"zone", "base", "objects"}
    assert all(item["schema"]["type"] == "boolean" and item["schema"]["default"] is True
               for item in parameters)

    async def first_frame():
        route = next(route for route in app.routes if route.path == "/stream.mjpg")
        stream = await route.endpoint(zone=False, base=True, objects=False)
        try:
            return await stream.body_iterator.__anext__()
        finally:
            await stream.body_iterator.aclose()

    chunk = asyncio.run(first_frame())
    assert b"Content-Type: image/jpeg" in chunk
    assert engine.render_view(zone=False, base=True, objects=False) in chunk

