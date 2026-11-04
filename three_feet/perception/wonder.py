"""Muse Wonder: read the heater manual for clearance rules, and explain incidents."""

from __future__ import annotations

import base64
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from openai import OpenAI

from ..config import RULES_PATH, get_settings
from .meta_client import CostMeter, chat_json, token_cost

CLEARANCE_SCHEMA = {
    "type": "object",
    "properties": {
        "found": {"type": "boolean", "description": "True only if the manual states a clearance distance"},
        "front_in": {"type": ["number", "null"]},
        "back_in": {"type": ["number", "null"]},
        "sides_in": {"type": ["number", "null"]},
        "top_in": {"type": ["number", "null"]},
        "quote": {"type": "string", "description": "Exact sentence(s) from the manual"},
        "page": {"type": ["integer", "null"]},
        "heater_model": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "notes": {"type": "string"},
    },
    "required": ["found", "front_in", "back_in", "sides_in", "top_in", "quote", "page",
                 "heater_model", "confidence", "notes"],
}

EXTRACT_PROMPT = (
    "This is a space heater's owner's manual. Find the required clearance between the heater and "
    "combustible materials (furniture, bedding, curtains, paper, etc.). Convert every distance to inches "
    "(1 ft = 12 in, 1 m = 39.37 in). If one distance applies to all sides, use it for front, back, and "
    "sides. Quote the exact sentence and give the PDF page number where it appears. If the manual gives "
    "no clearance distance, set found=false and leave the distances null. Do not guess."
)


@dataclass
class ClearanceRule:
    front_in: float
    back_in: float
    left_in: float
    right_in: float
    source: str  # "manual" | "default" | "user" (distances typed in by the user)
    quote: str = ""
    page: int | None = None
    heater_model: str = ""
    confidence: float = 0.0
    approved: bool = False
    approved_at: str | None = None
    manual_url: str = ""  # where the manual was found on the web, if it was not uploaded

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)

    @classmethod
    def load(cls, path: Path | None = None) -> "ClearanceRule":
        return cls(**json.loads(Path(path or RULES_PATH).read_text(encoding="utf-8")))

    def save(self, path: Path | None = None) -> None:
        path = Path(path or RULES_PATH)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json(), encoding="utf-8")

    def approve(self) -> None:
        self.approved = True
        self.approved_at = datetime.now(timezone.utc).isoformat()


def rule_from_extraction(data: dict, minimum_in: float) -> ClearanceRule:
    """Apply the stricter of the manual and `minimum_in` on every side. Missing sides use the minimum."""

    def side(value) -> float:
        return max(float(value), minimum_in) if isinstance(value, (int, float)) else minimum_in

    found = bool(data.get("found"))
    sides = data.get("sides_in") if found else None
    return ClearanceRule(
        front_in=side(data.get("front_in") if found else None),
        back_in=side(data.get("back_in") if found else None),
        left_in=side(sides),
        right_in=side(sides),
        source="manual" if found else "default",
        quote=str(data.get("quote", "")),
        page=data.get("page"),
        heater_model=str(data.get("heater_model", "")),
        confidence=float(data.get("confidence", 0.0)),
    )


class WonderClient:
    def __init__(self, client: OpenAI, meter: CostMeter | None = None, model: str | None = None) -> None:
        self.client = client
        self.meter = meter
        self.model = model or get_settings().wonder_model

    def extract_clearance(self, pdf_path: str | Path) -> dict:
        pdf = Path(pdf_path)
        encoded = base64.b64encode(pdf.read_bytes()).decode("ascii")
        return chat_json(
            self.client,
            self.model,
            [{"role": "user", "content": [
                {"type": "text", "text": EXTRACT_PROMPT},
                {"type": "file", "file": {
                    "filename": pdf.name,
                    "file_data": f"data:application/pdf;base64,{encoded}",
                }},
            ]}],
            "ClearanceExtraction",
            CLEARANCE_SCHEMA,
            self.meter,
        )

    def incident_report(self, events: list[dict], rule: ClearanceRule) -> str:
        """Plain-language explanation of one shutoff incident from the event log."""
        zone = get_settings().display_timezone
        local_events = [
            {**{k: v for k, v in event.items() if k != "t"},
             "time_local": datetime.fromtimestamp(event["t"], ZoneInfo(zone)).isoformat()}
            for event in events
        ]
        prompt = (
            "You write short incident reports for Three Feet, a space-heater clearance monitor. Using only "
            "the events below, explain in 3-5 sentences what was detected, how close it was versus the "
            "rule (cite the manual page if present), what action was taken, and whether shutoff was "
            "independently confirmed. Confirmation requires BOTH power_off=true and indicator_off=true "
            "on a confirmed event; a plug report alone never proves independent confirmation. "
            f"Use the provided time_local values in {zone}, including their UTC offset; do not convert "
            "or infer a timezone. Do not invent facts.\n\n"
            f"RULE: {rule.to_json()}\n\nEVENTS (JSONL):\n" + "\n".join(json.dumps(e) for e in local_events)
        )
        response = self.client.chat.completions.create(
            model=self.model, messages=[{"role": "user", "content": prompt}]
        )
        if self.meter and response.usage:
            self.meter.add(self.model, token_cost(
                self.model, response.usage.prompt_tokens, response.usage.completion_tokens))
        return response.choices[0].message.content or ""
