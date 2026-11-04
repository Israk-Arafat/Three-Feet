"""Local, model-free perception: a motion gate (saves API spend) and a zone-change catch-all.

The catch-all flags anything in the clearance zone that changed versus an empty-zone reference and
is not explained by a SAM mask. Unrecognized objects therefore still trigger alerts.
"""

from __future__ import annotations

import cv2
import numpy as np

from .sam import Detection


def _prep(image: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    return cv2.GaussianBlur(gray, (7, 7), 0)


class MotionGate:
    """Cheap frame differencing; `active` tells the engine whether to spend on API calls."""

    def __init__(self, region: np.ndarray | None = None, threshold: int = 25,
                 min_fraction: float = 0.002, hold_s: float = 3.0) -> None:
        self.region = region
        self.threshold = threshold
        self.min_fraction = min_fraction
        self.hold_s = hold_s
        self._prev: np.ndarray | None = None
        self._last_motion = -1e9
        self.fraction = 0.0

    def update(self, image: np.ndarray, t: float) -> bool:
        cur = _prep(image)
        if self._prev is None or self._prev.shape != cur.shape:
            self._prev = cur
            self._last_motion = t
            return True
        diff = cv2.absdiff(cur, self._prev) > self.threshold
        self._prev = cur
        if self.region is not None:
            denom = max(int(self.region.sum()), 1)
            self.fraction = float((diff & self.region).sum()) / denom
        else:
            self.fraction = float(diff.mean())
        if self.fraction >= self.min_fraction:
            self._last_motion = t
        return t - self._last_motion <= self.hold_s


class ZoneChangeDetector:
    def __init__(self, zone_mask: np.ndarray, ignore_mask: np.ndarray | None = None,
                 diff_threshold: int = 35, min_area_px: int = 600) -> None:
        self.zone = zone_mask & ~ignore_mask if ignore_mask is not None else zone_mask.copy()
        self.diff_threshold = diff_threshold
        self.min_area_px = min_area_px
        self.reference: np.ndarray | None = None

    def set_reference(self, image: np.ndarray) -> None:
        """Call while the zone is confirmed empty (setup, or after a human reset)."""
        self.reference = _prep(image)

    def unknown_blobs(self, image: np.ndarray, explained: np.ndarray | None = None) -> list[Detection]:
        if self.reference is None:
            return []
        cur = _prep(image)
        # Compensate global exposure drift so lighting changes don't look like objects.
        ref = self.reference.astype(np.float32)
        gain = (np.median(cur[self.zone]) + 1) / (np.median(ref[self.zone]) + 1) if self.zone.any() else 1.0
        diff = np.abs(cur.astype(np.float32) - ref * np.float32(gain)) > self.diff_threshold
        changed = diff & self.zone
        if explained is not None:
            changed &= ~cv2.dilate(explained.astype(np.uint8), np.ones((15, 15), np.uint8)).astype(bool)
        changed = cv2.morphologyEx(changed.astype(np.uint8), cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
        n, labels, stats, _ = cv2.connectedComponentsWithStats(changed, connectivity=8)
        blobs: list[Detection] = []
        for i in range(1, n):
            x, y, w, h, area = stats[i]
            if area < self.min_area_px:
                continue
            blobs.append(Detection(
                concept="unknown object", object_id=f"u{i}",
                box=(float(x), float(y), float(x + w), float(y + h)),
                mask=labels == i, confidence=None, source="catchall",
            ))
        return blobs
