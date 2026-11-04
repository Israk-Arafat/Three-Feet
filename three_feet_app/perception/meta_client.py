"""Shared Meta Model API client, image encoding, and a spend meter."""

from __future__ import annotations

import base64
import threading
import time
from collections import deque
from dataclasses import dataclass, field

import cv2
import numpy as np
from openai import OpenAI

from ..config import Settings, get_settings

# USD, from https://dev.meta.ai/docs/pricing-rate-limits (Standard mode).
SAM_IMAGE_USD = 2.50 / 1000
SAM_VIDEO_FRAME_USD = 0.20 / 1000
SAM_VIDEO_MIN_FRAMES = 50
TOKEN_PRICES_PER_M = {
    "muse-spark": (1.25, 4.25),
    "muse-wonder": (4.00, 20.00),
}


def make_client(settings: Settings | None = None) -> OpenAI:
    settings = settings or get_settings()
    if not settings.model_api_key:
        raise RuntimeError("MODEL_API_KEY is not set. Copy .env.example to .env and add your key.")
    return OpenAI(
        base_url=settings.meta_api_base,
        api_key=settings.model_api_key,
        timeout=settings.api_timeout_s,
        max_retries=2,
    )


def encode_jpeg(image: np.ndarray, max_side: int = 1280, quality: int = 85) -> tuple[bytes, float]:
    """JPEG-encode a BGR image, downscaling so the longest side <= max_side.

    Returns the bytes and the scale factor applied (encoded_px = source_px * scale).
    """
    h, w = image.shape[:2]
    scale = min(1.0, max_side / max(h, w))
    if scale < 1.0:
        image = cv2.resize(image, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise ValueError("JPEG encoding failed")
    return buf.tobytes(), scale


def image_data_url(image: np.ndarray, max_side: int = 1280, quality: int = 85) -> tuple[str, float]:
    jpeg, scale = encode_jpeg(image, max_side, quality)
    return "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii"), scale


def token_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    for prefix, (in_price, out_price) in TOKEN_PRICES_PER_M.items():
        if model.startswith(prefix):
            return (input_tokens * in_price + output_tokens * out_price) / 1_000_000
    return 0.0


def chat_json(
    client: OpenAI,
    model: str,
    messages: list[dict],
    schema_name: str,
    schema: dict,
    meter: CostMeter | None = None,
    **kwargs,
) -> dict:
    """Chat Completions call with schema-constrained JSON output (Meta structured output)."""
    import json

    response = client.chat.completions.create(
        model=model,
        messages=messages,
        response_format={"type": "json_schema", "json_schema": {"name": schema_name, "schema": schema}},
        **kwargs,
    )
    if meter and response.usage:
        meter.add(model, token_cost(model, response.usage.prompt_tokens, response.usage.completion_tokens))
    content = response.choices[0].message.content or "{}"
    return json.loads(content)


@dataclass
class CostMeter:
    """Thread-safe running total of estimated API spend, with a rolling one-hour rate."""

    budget_usd_per_hour: float = 5.0
    total_usd: float = 0.0
    calls: dict[str, int] = field(default_factory=dict)
    _window: deque = field(default_factory=deque, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(self, kind: str, usd: float) -> None:
        now = time.time()
        with self._lock:
            self.total_usd += usd
            self.calls[kind] = self.calls.get(kind, 0) + 1
            self._window.append((now, usd))
            self._trim(now)

    def last_hour_usd(self) -> float:
        with self._lock:
            self._trim(time.time())
            return sum(usd for _, usd in self._window)

    def over_budget(self) -> bool:
        return self.last_hour_usd() >= self.budget_usd_per_hour

    def snapshot(self) -> dict:
        return {
            "total_usd": round(self.total_usd, 4),
            "last_hour_usd": round(self.last_hour_usd(), 4),
            "budget_usd_per_hour": self.budget_usd_per_hour,
            "calls": dict(self.calls),
        }

    def _trim(self, now: float) -> None:
        while self._window and now - self._window[0][0] > 3600:
            self._window.popleft()
