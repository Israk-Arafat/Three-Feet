from datetime import datetime, timezone
from types import SimpleNamespace

from three_feet.perception.wonder import ClearanceRule, WonderClient


def test_report_uses_local_times_and_command_outcomes():
    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(usage=None, choices=[SimpleNamespace(message=SimpleNamespace(content="Report"))])

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    stamp = datetime(2026, 11, 4, 18, 24, 40, tzinfo=timezone.utc).timestamp()
    WonderClient(client).incident_report(
        [{"t": stamp, "kind": "alarm", "text": "OFF command failed"}],
        ClearanceRule(36, 36, 36, 36, "default"),
    )
    prompt = captured["messages"][0]["content"]
    assert "2026-11-04T13:24:40-05:00" in prompt
    assert "America/New_York" in prompt
    assert "Describe only command outcomes" in prompt
