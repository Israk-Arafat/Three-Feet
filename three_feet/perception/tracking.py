"""Perceived objects and a minimal IoU tracker that keeps IDs (and hazard judgments) stable across SAM runs."""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field

import numpy as np

from .sam import Detection


@dataclass
class PerceivedObject:
    track_id: str
    label: str
    mask: np.ndarray  # bool, full frame
    hazard: str = "high"  # high | medium | low (already conservative)
    why: str | None = None  # Muse Spark's one-line reason; None while assessing
    source: str = "sam"  # sam | catchall | sim
    confidence: float | None = None
    misses: int = 0
    assessing: bool = False
    extra: dict = field(default_factory=dict)


def box_iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


class Tracker:
    """Greedy IoU matching. A track is dropped after `max_misses` SAM cycles without a match.

    Tracks are only aged when a SAM cycle runs, so a static scene keeps its objects between cycles.
    """

    def __init__(self, iou_threshold: float = 0.25, max_misses: int = 2) -> None:
        self.iou_threshold = iou_threshold
        self.max_misses = max_misses
        self.tracks: dict[str, PerceivedObject] = {}
        self._boxes: dict[str, tuple[float, float, float, float]] = {}
        self._ids = itertools.count(1)

    def update(self, detections: list[Detection]) -> list[PerceivedObject]:
        """Returns newly created tracks (callers typically request a hazard judgment for them)."""
        pairs = sorted(
            ((box_iou(self._boxes[tid], d.box), tid, i) for tid in self.tracks for i, d in enumerate(detections)),
            reverse=True,
        )
        used_tracks: set[str] = set()
        used_dets: set[int] = set()
        for iou, tid, i in pairs:
            if iou < self.iou_threshold or tid in used_tracks or i in used_dets:
                continue
            used_tracks.add(tid)
            used_dets.add(i)
            track, det = self.tracks[tid], detections[i]
            track.mask, track.confidence, track.misses = det.mask, det.confidence, 0
            self._boxes[tid] = det.box

        for tid in list(self.tracks):
            if tid not in used_tracks:
                self.tracks[tid].misses += 1
                if self.tracks[tid].misses > self.max_misses:
                    del self.tracks[tid], self._boxes[tid]

        created = []
        for i, det in enumerate(detections):
            if i in used_dets:
                continue
            tid = f"t{next(self._ids)}"
            track = PerceivedObject(tid, det.concept, det.mask, confidence=det.confidence, assessing=True)
            self.tracks[tid] = track
            self._boxes[tid] = det.box
            created.append(track)
        return created

    def objects(self) -> list[PerceivedObject]:
        return list(self.tracks.values())


def dedupe_detections(detections: list[Detection], iou_threshold: float = 0.6) -> list[Detection]:
    """Different concepts often match the same object ('blanket' and 'clothing'); keep the most confident."""
    ordered = sorted(detections, key=lambda d: -(d.confidence or 0.0))
    kept: list[Detection] = []
    for d in ordered:
        if all(box_iou(d.box, k.box) < iou_threshold for k in kept):
            kept.append(d)
    return kept
