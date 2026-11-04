"""Track SAM masks locally; ask for verification when appearance or background changes."""

from __future__ import annotations

import cv2
import numpy as np

from .tracking import Tracker


class LocalMaskTracker:
    """Conservative translation tracking on a small image, anchored to the last SAM frame.

    Optical flow must agree in both directions and pass an appearance check. Unknown
    changes are compared with the SAM frame, so slow changes cannot evade detection
    by staying below the threshold between consecutive camera frames. Failed tracks
    retain their masks until SAM confirms removal; local tracking never deletes them.
    """

    def __init__(self, max_side: int = 480, diff_threshold: int = 25,
                 min_change_fraction: float = 0.002) -> None:
        self.max_side = max_side
        self.diff_threshold = diff_threshold
        self.min_change_fraction = min_change_fraction
        self._anchor: np.ndarray | None = None
        self._previous: np.ndarray | None = None
        self._shape: tuple[int, int] | None = None
        self._masks: dict[str, np.ndarray] = {}
        self._offsets: dict[str, np.ndarray] = {}

    def _prepare(self, image: np.ndarray) -> np.ndarray:
        h, w = image.shape[:2]
        scale = min(1.0, self.max_side / max(h, w))
        small = cv2.resize(image, (max(1, round(w * scale)), max(1, round(h * scale))),
                           interpolation=cv2.INTER_AREA) if scale < 1 else image
        return cv2.GaussianBlur(small, (3, 3), 0)

    @staticmethod
    def _resize_mask(mask: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
        return cv2.resize(mask.astype(np.uint8), (shape[1], shape[0]),
                          interpolation=cv2.INTER_NEAREST).astype(bool)

    def seed(self, image: np.ndarray, tracker: Tracker) -> None:
        self._anchor = self._prepare(image)
        self._previous = self._anchor
        self._shape = image.shape[:2]
        self._masks = {o.track_id: o.mask.copy() for o in tracker.objects()}
        self._offsets = {tid: np.zeros(2, np.float32) for tid in self._masks}

    def update(self, image: np.ndarray, tracker: Tracker) -> bool:
        """Move confidently tracked masks. Return True if SAM must inspect the scene."""
        if self._anchor is None or self._previous is None or image.shape[:2] != self._shape:
            return True
        current = self._prepare(image)
        previous = self._previous
        shape = current.shape[:2]
        gray_before = cv2.cvtColor(previous, cv2.COLOR_BGR2GRAY)
        gray_now = cv2.cvtColor(current, cv2.COLOR_BGR2GRAY)
        delta = np.max(cv2.absdiff(current, previous), axis=2) > self.diff_threshold
        explained = np.zeros(shape, bool)
        uncertain = False
        for obj in tracker.objects():
            original = self._masks.get(obj.track_id)
            if original is None or obj.mask.shape != self._shape:
                uncertain = True
                continue
            mask = self._resize_mask(obj.mask, shape)
            region = cv2.dilate(mask.astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool)
            # Static and textureless objects require no flow estimation.
            moved = False
            if region.any() and float(delta[region].mean()) > 0.01:
                shift = self._translation(gray_before, gray_now, mask)
                if shift is None:
                    uncertain = True
                else:
                    transform = np.float32([[1, 0, shift[0]], [0, 1, shift[1]]])
                    warped = cv2.warpAffine(previous, transform, (shape[1], shape[0]))
                    moved_mask = cv2.warpAffine(mask.astype(np.uint8), transform,
                                               (shape[1], shape[0])).astype(bool)
                    mismatch = np.max(cv2.absdiff(current, warped), axis=2) > self.diff_threshold
                    # Clipped, occluded, deformed, and poorly matched masks need SAM.
                    if (moved_mask.sum() < mask.sum() * 0.9 or not moved_mask.any()
                            or float(mismatch[moved_mask].mean()) > 0.05):
                        uncertain = True
                    else:
                        sx, sy = image.shape[1] / shape[1], image.shape[0] / shape[0]
                        self._offsets[obj.track_id] += shift * np.array([sx, sy], np.float32)
                        dx, dy = self._offsets[obj.track_id]
                        moved_full = cv2.warpAffine(original.astype(np.uint8),
                                                   np.float32([[1, 0, dx], [0, 1, dy]]),
                                                   (image.shape[1], image.shape[0])).astype(bool)
                        tracker.move(obj.track_id, moved_full)
                        mask, moved = moved_mask, True
            # Exclude both the original footprint and its current location. Any
            # unexplained change elsewhere remains anchored to the last SAM image.
            explained |= self._resize_mask(original, shape) | mask
            if moved:
                obj.extra["tracking"] = "local"
        difference = np.max(cv2.absdiff(current, self._anchor), axis=2) > self.diff_threshold
        explained = cv2.dilate(explained.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
        unknown = (difference & ~explained).astype(np.uint8)
        # Ignore isolated sensor noise, retaining small coherent objects.
        unknown = cv2.morphologyEx(unknown, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        n, _, stats, _ = cv2.connectedComponentsWithStats(unknown, connectivity=8)
        minimum = max(4, round(unknown.size * self.min_change_fraction))
        changed = any(stats[i, cv2.CC_STAT_AREA] >= minimum for i in range(1, n))
        self._previous = current
        return uncertain or changed

    @staticmethod
    def _translation(before: np.ndarray, after: np.ndarray, mask: np.ndarray) -> np.ndarray | None:
        points = cv2.goodFeaturesToTrack(before, maxCorners=80, qualityLevel=0.01,
                                        minDistance=3, mask=mask.astype(np.uint8) * 255)
        if points is None or len(points) < 4:
            return None
        moved, valid, _ = cv2.calcOpticalFlowPyrLK(before, after, points, None,
                                                 winSize=(21, 21), maxLevel=3)
        if moved is None or valid is None:
            return None
        returned, back_valid, _ = cv2.calcOpticalFlowPyrLK(after, before, moved, None,
                                                         winSize=(21, 21), maxLevel=3)
        if returned is None or back_valid is None:
            return None
        good = (valid.ravel() > 0) & (back_valid.ravel() > 0)
        good &= np.linalg.norm(returned[:, 0] - points[:, 0], axis=1) < 1.0
        if good.sum() < max(4, len(points) * 0.6):
            return None
        shifts = moved[good, 0] - points[good, 0]
        shift = np.median(shifts, axis=0)
        if (not np.isfinite(shift).all() or np.linalg.norm(shift) > 40
                or np.mean(np.linalg.norm(shifts - shift, axis=1) < 2) < 0.8):
            return None
        return shift
