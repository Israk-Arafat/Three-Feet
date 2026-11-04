"""The recorder behind the static Pages demo: a short run must produce a replayable recording."""

import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "record_demo.py"


def load_recorder():
    spec = importlib.util.spec_from_file_location("record_demo", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_short_recording_is_replayable(tmp_path):
    rec = load_recorder().record(tmp_path, max_seconds=8)
    saved = json.loads((tmp_path / "recording.json").read_text(encoding="utf-8"))
    assert saved == json.loads(json.dumps(rec))
    assert 7.5 <= saved["duration_s"] <= 8.5
    assert len(saved["frames"]) >= 20 and all((tmp_path / f).is_file() for f in saved["frames"])
    assert saved["states"][0]["t"] <= 0.25 and saved["states"][0]["msg"]["type"] == "state"
    times = [s["t"] for s in saved["states"]]
    assert times == sorted(times)
    # The blanket enters the zone at 4 s in the script, so the opening events are already there.
    kinds = [e["event"]["kind"] for e in saved["events"]]
    assert "system" in kinds and "warn" in kinds
    assert all(0 <= e["t"] <= saved["duration_s"] for e in saved["events"])


def test_pages_workflow_builds_the_demo():
    workflow = (SCRIPT.parent.parent / ".github" / "workflows" / "pages.yml").read_text(encoding="utf-8")
    assert "npm run build:demo" in workflow and "deploy-pages" in workflow
