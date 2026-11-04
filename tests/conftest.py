"""Tests use explicit defaults, never a developer's .env, credentials, or saved rule."""

import pytest

from three_feet.config import Settings, get_settings


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch, tmp_path):
    import three_feet.app as app_module
    import three_feet.perception.wonder as wonder_module

    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    for name in ("TF_SOURCE", "TF_PLUG", "TF_DEMO", "TF_AUTOPLAY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    rules = tmp_path / "heater_rules.json"
    monkeypatch.setattr(app_module, "RULES_PATH", rules)
    monkeypatch.setattr(wonder_module, "RULES_PATH", rules)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
