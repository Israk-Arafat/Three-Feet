"""SAM 3.1 segmentation over Meta Model API, parsed with Meta's `meta_sam_parser`."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import cv2
import numpy as np
from meta_sam_parser import (
    CompletedOutcome,
    FramePrompt,
    PromptBox,
    PromptObject,
    build_object_prompt,
    decode_mask_to_raster,
    image_segmentation_format,
)
from openai import OpenAI

from ..config import get_settings
from .meta_client import SAM_IMAGE_USD, CostMeter, image_data_url


@dataclass
class Detection:
    concept: str
    object_id: str
    box: tuple[float, float, float, float]  # x1, y1, x2, y2 (half-open), source pixels
    mask: np.ndarray  # bool, full source-frame size
    confidence: float | None = None
    source: str = "sam"  # "sam" | "catchall"

    @property
    def area(self) -> int:
        return int(self.mask.sum())


@dataclass
class SamResult:
    concept: str
    detections: list[Detection]
    latency_s: float
    raw_output: str = ""
    diagnostics: list[str] = field(default_factory=list)


def parse_sam_output(
    text: str, frame_size: tuple[int, int], concept: str = "", scale: float = 1.0
) -> tuple[list[Detection], list[str]]:
    """Parse SAM output text into full-frame boolean masks.

    `frame_size` is the *source* (width, height). `scale` is encoded_px / source_px, used when
    the image was downscaled before upload.
    """
    src_w, src_h = frame_size
    parser = image_segmentation_format().create_parser()
    if text:
        parser.push(text if text.endswith("\n") else text + "\n")
    result = parser.finish(CompletedOutcome()).result

    boxes: dict[str, tuple[tuple[float, float, float, float], float | None]] = {}
    masks: dict[str, tuple[np.ndarray, float | None]] = {}
    enc_w, enc_h = round(src_w * scale), round(src_h * scale)
    for record in result.records:
        if record.kind == "box":
            boxes[record.object_id] = (
                (record.left, record.top, record.right, record.bottom),
                record.confidence,
            )
        elif record.kind == "mask":
            masks[record.object_id] = (
                _paste_mask(record, enc_w, enc_h),
                record.confidence,
            )

    detections: list[Detection] = []
    for object_id, (mask_enc, mask_conf) in masks.items():
        box_enc, box_conf = boxes.get(object_id, (_mask_bbox(mask_enc), None))
        mask = mask_enc
        if scale != 1.0:
            mask = cv2.resize(mask_enc.astype(np.uint8), (src_w, src_h), interpolation=cv2.INTER_NEAREST) > 0
        box = tuple(v / scale for v in box_enc)
        detections.append(
            Detection(
                concept=concept,
                object_id=object_id,
                box=box,  # type: ignore[arg-type]
                mask=mask,
                confidence=box_conf if box_conf is not None else mask_conf,
            )
        )
    diagnostics = [f"{d.severity}:{d.code}:{d.message}" for d in result.diagnostics]
    return detections, diagnostics


def _paste_mask(record, frame_w: int, frame_h: int) -> np.ndarray:
    """Place a box-local mask raster into a full-frame boolean array."""
    raster = np.frombuffer(decode_mask_to_raster(record.mask), dtype=np.uint8).reshape(
        record.mask.height, record.mask.width
    )
    b = record.bounds
    left, top = int(round(b.left)), int(round(b.top))
    right, bottom = int(round(b.right)), int(round(b.bottom))
    bw, bh = max(right - left, 1), max(bottom - top, 1)
    if (bw, bh) != (record.mask.width, record.mask.height):
        raster = cv2.resize(raster, (bw, bh), interpolation=cv2.INTER_NEAREST)
    full = np.zeros((frame_h, frame_w), dtype=bool)
    x0, y0 = max(left, 0), max(top, 0)
    x1, y1 = min(left + bw, frame_w), min(top + bh, frame_h)
    if x1 > x0 and y1 > y0:
        full[y0:y1, x0:x1] = raster[y0 - top : y1 - top, x0 - left : x1 - left] > 0
    return full


def _mask_bbox(mask: np.ndarray) -> tuple[float, float, float, float]:
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        return (0.0, 0.0, 0.0, 0.0)
    return (float(xs.min()), float(ys.min()), float(xs.max() + 1), float(ys.max() + 1))


class SamClient:
    def __init__(self, client: OpenAI, meter: CostMeter | None = None, model: str | None = None,
                 max_side: int = 1280) -> None:
        self.client = client
        self.meter = meter
        self.model = model or get_settings().sam_model
        self.max_side = max_side

    def segment(self, image: np.ndarray, concept: str) -> SamResult:
        """Segment every instance of one short noun phrase (one concept per request)."""
        return self._request(image, concept, concept)

    def segment_box(self, image: np.ndarray, box: tuple[int, int, int, int], label: str = "heater") -> SamResult:
        """Segment the single object inside a user-drawn box (x1, y1, x2, y2 half-open, source px)."""
        h, w = image.shape[:2]
        scale = min(1.0, self.max_side / max(h, w))  # must match encode_jpeg()
        ew, eh = round(w * scale), round(h * scale)
        x1, y1, x2, y2 = (int(round(v * scale)) for v in box)
        prompt = build_object_prompt(
            size=(ew, eh),
            objects=[PromptObject(id=1, prompts=[FramePrompt(box=PromptBox(
                max(x1, 0), max(y1, 0), min(x2, ew), min(y2, eh)))])],
        )
        return self._request(image, prompt, label)

    def segment_many(self, image: np.ndarray, concepts: list[str], max_workers: int = 6) -> list[SamResult]:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            return list(pool.map(lambda c: self.segment(image, c), concepts))

    def _request(self, image: np.ndarray, prompt_text: str, concept: str) -> SamResult:
        h, w = image.shape[:2]
        data_url, scale = image_data_url(image, self.max_side)
        start = time.perf_counter()
        stream = self.client.responses.create(
            model=self.model,
            input=[{
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt_text},
                    {"type": "input_image", "image_url": data_url},
                ],
            }],
            metadata={"include_confidence": "true", "mask_encoding": "one_bit"},
            stream=True,
        )
        parts: list[str] = []
        for event in stream:
            etype = getattr(event, "type", "")
            if etype == "response.output_text.delta":
                parts.append(event.delta)
            elif etype in ("response.failed", "error"):
                raise RuntimeError(f"SAM request failed: {event}")
        latency = time.perf_counter() - start
        if self.meter:
            self.meter.add("sam_image", SAM_IMAGE_USD)
        text = "".join(parts)
        detections, diagnostics = parse_sam_output(text, (w, h), concept, scale)
        return SamResult(concept, detections, latency, text, diagnostics)
