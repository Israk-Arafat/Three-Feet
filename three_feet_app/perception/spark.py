"""Muse Spark: open-vocabulary scene inventory and per-object hazard judgment."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
from openai import OpenAI

from ..config import get_settings
from .meta_client import CostMeter, chat_json, image_data_url

HazardLevel = Literal["high", "medium", "low"]

# Always segmented in addition to what Muse Spark proposes.
ALWAYS_ON_CONCEPTS = ["person", "blanket", "curtain", "cardboard box", "towel", "clothing", "paper", "shoe", "bag"]


def safety_concepts(proposed: list[str], max_scene_concepts: int = 3) -> list[str]:
    """Keep every baseline hazard, with a bounded number of scene-specific additions."""
    out = list(ALWAYS_ON_CONCEPTS)
    for phrase in proposed:
        phrase = " ".join(phrase.lower().split())
        if phrase and phrase not in out and "," not in phrase:
            out.append(phrase)
        if len(out) >= len(ALWAYS_ON_CONCEPTS) + max_scene_concepts:
            break
    return out

INVENTORY_SCHEMA = {
    "type": "object",
    "properties": {
        "objects": {
            "type": "array",
            "maxItems": 8,
            "items": {
                "type": "object",
                "properties": {
                    "phrase": {"type": "string", "description": "1-3 word concrete noun phrase"},
                    "in_or_near_zone": {"type": "boolean"},
                },
                "required": ["phrase", "in_or_near_zone"],
            },
        }
    },
    "required": ["objects"],
}

HAZARD_SCHEMA = {
    "type": "object",
    "properties": {
        "label": {"type": "string"},
        "material_guess": {"type": "string"},
        "flammability": {"type": "string", "enum": ["high", "medium", "low", "unknown"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "why": {"type": "string", "description": "One short sentence a homeowner can understand"},
        "seen_clearly": {"type": "boolean"},
    },
    "required": ["label", "material_guess", "flammability", "confidence", "why", "seen_clearly"],
}

INVENTORY_PROMPT = (
    "This is an overhead camera view of a space heater. The red outline is its clearance zone. "
    "List the physical objects that are inside or near the red outline, excluding the heater itself, "
    "its power cord, any other cords or cables, people, the floor, walls, baseboards, trim, outlets, "
    "switches, doors and windows. Each phrase must be a short concrete noun phrase that an image segmenter "
    "can find, such as 'fleece blanket' or 'cardboard box'. Do not use positions, negation, or lists "
    "inside a phrase."
)

HAZARD_PROMPT = (
    "You are helping a space-heater safety system. The first image is a close crop of one object; the "
    "second is the whole scene. The object was detected as '{concept}'. Judge how easily it could ignite "
    "if left near a running space heater. If you are unsure what it is or what it is made of, answer "
    "'unknown'. Be conservative: never call textiles, paper, cardboard, or plastics 'low'."
)


@dataclass(frozen=True)
class HazardAssessment:
    label: str
    material_guess: str
    flammability: str  # high | medium | low | unknown
    confidence: float
    why: str
    seen_clearly: bool

    @property
    def level(self) -> HazardLevel:
        return conservative_level(self)


def conservative_level(a: HazardAssessment | None, min_confidence: float = 0.6) -> HazardLevel:
    """The model can only make Three Feet more cautious: anything uncertain is treated as high."""
    if a is None or a.flammability == "unknown" or not a.seen_clearly or a.confidence < min_confidence:
        return "high"
    if a.flammability in ("high", "medium", "low"):
        return a.flammability  # type: ignore[return-value]
    return "high"


class SparkClient:
    def __init__(self, client: OpenAI, meter: CostMeter | None = None, model: str | None = None) -> None:
        self.client = client
        self.meter = meter
        self.model = model or get_settings().spark_model

    def inventory(self, scene_with_zone: np.ndarray, max_concepts: int = 7) -> list[str]:
        """Ask Muse Spark which concepts SAM should look for, merged with the always-on list."""
        url, _ = image_data_url(scene_with_zone, max_side=1024)
        data = chat_json(
            self.client,
            self.model,
            [{"role": "user", "content": [
                {"type": "text", "text": INVENTORY_PROMPT},
                {"type": "image_url", "image_url": {"url": url}},
            ]}],
            "SceneInventory",
            INVENTORY_SCHEMA,
            self.meter,
        )
        proposed = [o["phrase"].strip().lower() for o in data.get("objects", []) if o.get("in_or_near_zone")]
        return safety_concepts(proposed[:max_concepts])

    def assess(self, crop: np.ndarray, scene: np.ndarray, concept: str) -> HazardAssessment:
        crop_url, _ = image_data_url(crop, max_side=512)
        scene_url, _ = image_data_url(scene, max_side=768)
        data = chat_json(
            self.client,
            self.model,
            [{"role": "user", "content": [
                {"type": "text", "text": HAZARD_PROMPT.format(concept=concept)},
                {"type": "image_url", "image_url": {"url": crop_url}},
                {"type": "image_url", "image_url": {"url": scene_url}},
            ]}],
            "HazardAssessment",
            HAZARD_SCHEMA,
            self.meter,
        )
        return HazardAssessment(
            label=str(data.get("label", concept)),
            material_guess=str(data.get("material_guess", "")),
            flammability=str(data.get("flammability", "unknown")),
            confidence=float(data.get("confidence", 0.0)),
            why=str(data.get("why", "")),
            seen_clearly=bool(data.get("seen_clearly", False)),
        )


def merge_concepts(proposed: list[str], always_on: list[str], limit: int) -> list[str]:
    """Model proposals first (they describe this scene), then the safety list; de-duplicated and capped."""
    out: list[str] = []
    for phrase in [*proposed, *always_on]:
        phrase = " ".join(phrase.split())
        if phrase and phrase not in out and "," not in phrase:
            out.append(phrase)
        if len(out) >= limit:
            break
    return out
