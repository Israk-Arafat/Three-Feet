"""API contract tests against the simulated engine (no hardware, no API key)."""

import time

import pytest
from fastapi.testclient import TestClient

import three_feet.app as app_module
import three_feet.perception.wonder as wonder_module


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
    for key in ("state", "banner", "objects", "rule", "power", "signals", "camera", "calibration", "api"):
        assert key in s
    cfg = client.get("/api/config").json()
    assert cfg == {"mode": "sim", "demo": True, "source": "simulated scene", "plug": "dryrun",
                   "autoplay": False, "wonder": False}


def test_rule_extract_and_approve(client):
    r = client.post("/api/rules/extract", files={"file": ("manual.pdf", b"%PDF-1.4\n%%EOF", "application/pdf")})
    assert r.status_code == 200
    draft = r.json()
    assert draft["simulated"] is True and draft["rule"]["page"] == 4
    r = client.post("/api/rules/approve", json={**draft["rule"], "front_in": 12})
    assert r.status_code == 200
    assert r.json()["rule"]["front_in"] == 36  # never looser than the minimum
    assert client.rules_path.exists()
    s = wait_for(client, lambda s: s["rule"]["approved"])
    assert s["rule"]["source"] == "manual"


def test_rejects_non_pdf(client):
    r = client.post("/api/rules/extract", files={"file": ("x.txt", b"hi", "text/plain")})
    assert r.status_code == 400


def test_reset_refused_when_not_shut_off(client):
    assert client.post("/api/reset").status_code == 409


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
