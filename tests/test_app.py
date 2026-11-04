"""API contract tests against the simulated engine (no hardware, no API key)."""

import time

import pytest
from fastapi.testclient import TestClient

import three_feet.app as app_module
import three_feet.perception.wonder as wonder_module
from three_feet.safety.state_machine import State


@pytest.fixture
def client(tmp_path, monkeypatch):
    rules = tmp_path / "heater_rules.json"
    monkeypatch.setattr(app_module, "RULES_PATH", rules)
    monkeypatch.setattr(wonder_module, "RULES_PATH", rules)
    monkeypatch.setenv("TF_SOURCE", "sim")
    monkeypatch.setenv("TF_DEMO", "1")
    monkeypatch.setenv("TF_AUTOPLAY", "0")
    monkeypatch.setenv("MODEL_API_KEY", "")
    app_module.get_settings.cache_clear()
    with TestClient(app_module.create_app()) as c:
        deadline = time.time() + 10
        while not c.get("/api/state").json() and time.time() < deadline:
            time.sleep(0.1)
        c.rules_path = rules
        yield c
    app_module.get_settings.cache_clear()


def wait_for(client, predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = client.get("/api/state").json()
        if predicate(s):
            return s
        time.sleep(0.1)
    raise AssertionError(f"condition not met; last state {s.get('state')} {s.get('degraded')}")


def test_state_shape_and_config(client):
    s = client.get("/api/state").json()
    for key in ("state", "banner", "objects", "rule", "power", "off_pending", "off_error", "camera", "calibration", "api",
                "reset_pending", "reset_cancelled"):
        assert key in s
    cfg = client.get("/api/config").json()
    assert cfg == {"mode": "sim", "demo": True, "source": "simulated scene", "plug": "dryrun",
                   "autoplay": False, "wonder": False}


def test_rule_extract_and_approve(client):
    r = client.post("/api/rules/extract", files={"file": ("manual.pdf", b"%PDF-1.4\n%%EOF", "application/pdf")})
    assert r.status_code == 200
    draft = r.json()
    assert draft["simulated"] is True and draft["rule"]["page"] == 4
    r = client.post("/api/rules/approve", json=draft["rule"])
    assert r.status_code == 200
    assert r.json()["rule"]["front_in"] == 36
    assert client.rules_path.exists()
    s = wait_for(client, lambda s: s["rule"]["approved"])
    assert s["rule"]["source"] == "manual" and s["rule"]["default_in"] == 36


def test_user_can_set_any_positive_clearance(client):
    rule = {"front_in": 24, "back_in": 12, "left_in": 48, "right_in": 48, "source": "user"}
    r = client.post("/api/rules/approve", json=rule)
    assert r.status_code == 200
    assert (r.json()["rule"]["front_in"], r.json()["rule"]["back_in"]) == (24, 12)  # no floor on user edits
    s = wait_for(client, lambda s: s["rule"]["source"] == "user")
    assert s["rule"]["left_in"] == 48 and s["rule"]["approved"]
    events = client.get("/api/events", params={"limit": 1000}).json()
    assert any("below the 36 in default on the front, back" in e["text"] for e in events)


@pytest.mark.parametrize("bad", [{"front_in": 0}, {"front_in": -5}, {"front_in": 500}, {"source": "magic"}])
def test_approve_rejects_invalid_rules(client, bad):
    rule = {"front_in": 36, "back_in": 36, "left_in": 36, "right_in": 36, "source": "user", **bad}
    assert client.post("/api/rules/approve", json=rule).status_code == 422


def test_find_manual_by_name_simulated(client):
    r = client.post("/api/rules/find", json={"heater_name": "  Lasko   754200 "})
    assert r.status_code == 200
    draft = r.json()
    assert draft["simulated"] is True
    assert draft["manual"]["heater_model"] == "Lasko 754200"
    assert draft["rule"]["page"] == 4 and draft["rule"]["source"] == "manual"
    assert client.post("/api/rules/find", json={"heater_name": "x"}).status_code == 422


def test_find_manual_reports_links_it_could_not_download(client, monkeypatch):
    from three_feet.perception.manual_finder import ManualNotFound

    class FakeWonder:
        client = meter = None

    class FailingFinder:
        def __init__(self, *args):
            pass

        def find(self, name):
            raise ManualNotFound("couldn't download", [{"url": "https://example.com/m.pdf", "error": "HTTP 403"}])

    monkeypatch.setattr(client.app.state.engine, "wonder", FakeWonder())
    monkeypatch.setattr(app_module, "ManualFinder", FailingFinder)
    r = client.post("/api/rules/find", json={"heater_name": "Lasko 754200"})
    assert r.status_code == 404
    assert r.json()["detail"]["candidates"][0]["error"] == "HTTP 403"


def test_rejects_non_pdf(client):
    r = client.post("/api/rules/extract", files={"file": ("x.txt", b"hi", "text/plain")})
    assert r.status_code == 400


def test_reset_refused_when_not_shut_off(client):
    assert client.post("/api/reset").status_code == 409


def latch_simulated_plug_off(client):
    engine = client.app.state.engine
    engine.switch.settle_s = 0
    engine.switch.turn_off()
    engine.plug.refresh()
    engine.sm.state = State.OFF_REQUESTED
    wait_for(client, lambda s: s["state"] == "OFF_REQUESTED" and s["can_reset"])
    return engine


def test_reset_restores_power_and_monitoring_once(client):
    engine = latch_simulated_plug_off(client)
    response = client.post("/api/reset")
    assert response.status_code == 200
    assert response.json()["ok"] is True
    state = wait_for(client, lambda s: s["state"] == "CLEAR" and s["power"]["on"] is True)
    assert state["can_reset"] is False
    assert engine.switch.on_commands == 1
    assert client.post("/api/reset").status_code == 409
    assert engine.switch.on_commands == 1


def test_reset_on_failure_returns_error_and_keeps_heater_latched_off(client, monkeypatch):
    engine = latch_simulated_plug_off(client)

    def fail_on():
        raise RuntimeError("plug command failed")

    monkeypatch.setattr(engine.switch, "turn_on", fail_on)
    response = client.post("/api/reset")
    assert response.status_code == 502
    assert "detail" in response.json()
    assert engine.sm.state is State.OFF_REQUESTED
    assert engine.switch.status().on is False
    wait_for(client, lambda s: s["state"] == "OFF_REQUESTED")


def test_reset_refuses_missing_calibration_after_shutoff(client):
    engine = latch_simulated_plug_off(client)
    engine.calib = None
    engine._build_geometry()
    wait_for(client, lambda s: not s["calibration"]["configured"] and not s["can_reset"])
    assert client.post("/api/reset").status_code == 409
    assert engine.switch.on_commands == 0
    assert engine.switch.status().on is False


def test_demo_controls_drive_states(client):
    assert client.post("/api/demo/sim", json={"object": "blanket", "spot": "near"}).status_code == 200
    wait_for(client, lambda s: s["state"] == "WARN")
    assert client.post("/api/demo/sim", json={"object": "blanket", "spot": "home"}).status_code == 200
    wait_for(client, lambda s: s["state"] == "CLEAR", timeout=10)

    assert client.post("/api/demo/camera", json={"on": True}).json()["via"] == "simulated"
    s = wait_for(client, lambda s: "CAMERA_LOST" in s["degraded"])
    assert s["banner"].startswith("UNKNOWN")
    client.post("/api/demo/camera", json={"on": False})

    assert client.post("/api/demo/api", json={"on": True}).status_code == 200
    wait_for(client, lambda s: "API_DOWN" in s["degraded"])
    assert client.post("/api/demo/plug", json={"action": "unreachable"}).status_code == 200
    wait_for(client, lambda s: "PLUG_UNREACHABLE" in s["degraded"])
    assert client.post("/api/demo/plug", json={"action": "bogus"}).status_code == 400


def test_snapshot_and_events(client):
    r = client.get("/api/snapshot.jpg")
    assert r.status_code == 200 and r.content[:2] == b"\xff\xd8"
    events = client.get("/api/events").json()
    assert events and events[0]["kind"] == "system"


def test_websocket_sends_events_then_state(client):
    with client.websocket_connect("/ws") as ws:
        first = ws.receive_json()
        assert first["type"] == "events"
        assert ws.receive_json()["type"] == "state"
