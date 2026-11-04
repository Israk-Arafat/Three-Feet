"""Runtime settings loaded from environment variables and `.env`."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
RUNS_DIR = REPO_ROOT / "runs"
RULES_PATH = REPO_ROOT / "rules" / "heater_rules.json"
CALIBRATION_PATH = DATA_DIR / "calibration.json"
MANUALS_DIR = DATA_DIR / "manuals"
CYNC_TOKEN_PATH = DATA_DIR / "cync_token.json"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    model_api_key: str = ""
    meta_api_base: str = "https://api.meta.ai/v1"
    sam_model: str = "sam-3.1"
    spark_model: str = "muse-spark-1.3"
    wonder_model: str = "muse-wonder-1.0"
    api_timeout_s: float = 30.0
    perception_max_age_s: float = Field(default=5.0, gt=0)
    perception_idle_refresh_s: float = Field(default=2.0, gt=0)
    display_timezone: str = "America/New_York"

    @field_validator("display_timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (KeyError, ValueError) as exc:
            raise ValueError("DISPLAY_TIMEZONE must be an IANA timezone") from exc
        return value

    camera_url: str = ""
    camera_user: str = ""
    camera_password: str = ""
    camera_verify_tls: bool = False

    plug_kind: str = "dryrun"
    plug_name: str = ""  # Cync: the plug's name in the Cync app (blank = the only plug on the account)

    default_clearance_in: float = 36.0
    watch_margin_in: float = 6.0
    dwell_high_s: float = 10.0
    dwell_medium_s: float = 20.0
    dwell_low_s: float = 60.0
    failsafe_enabled: bool = True
    failsafe_after_s: float = 30.0
    off_power_w: float = 5.0
    verify_timeout_s: float = 5.0
    max_off_retries: int = 2

    mask_edge_px: float = 2.0
    parallax_in: float = 0.5

    budget_usd_per_hour: float = 5.0


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
