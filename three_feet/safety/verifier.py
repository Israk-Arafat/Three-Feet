"""Second, independent shutoff signal: the heater's power light / glowing element as seen by the camera."""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


def roi_brightness(image: np.ndarray, roi: tuple[int, int, int, int]) -> float:
    """95th-percentile HSV value in the ROI; robust to a small bright LED in a dark area."""
    x, y, w, h = roi
    patch = image[y : y + h, x : x + w]
    if patch.size == 0:
        raise ValueError("Indicator ROI is outside the frame")
    v = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)[..., 2]
    return float(np.percentile(v, 95))


@dataclass
class IndicatorProbe:
    roi: tuple[int, int, int, int]
    threshold: float

    @classmethod
    def calibrate(cls, roi, on_images: list[np.ndarray], off_images: list[np.ndarray],
                  min_separation: float = 20.0) -> "IndicatorProbe":
        on = [roi_brightness(i, roi) for i in on_images]
        off = [roi_brightness(i, roi) for i in off_images]
        if min(on) - max(off) < min_separation:
            raise ValueError(
                f"Indicator not distinguishable (on min {min(on):.0f}, off max {max(off):.0f}); "
                "pick a tighter ROI on the light or reduce exposure"
            )
        return cls(tuple(roi), (min(on) + max(off)) / 2)  # type: ignore[arg-type]

    def is_on(self, image: np.ndarray | None) -> bool | None:
        if image is None:
            return None
        try:
            return roi_brightness(image, self.roi) > self.threshold
        except ValueError:
            return None
