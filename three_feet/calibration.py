"""Camera-to-floor calibration from ArUco markers (or clicked tape marks), saved as JSON."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from .config import CALIBRATION_PATH
from .geometry import FloorCalibration, HeaterFootprint

ARUCO_DICT_ID = cv2.aruco.DICT_4X4_50


def _detector() -> cv2.aruco.ArucoDetector:
    dictionary = cv2.aruco.getPredefinedDictionary(ARUCO_DICT_ID)
    return cv2.aruco.ArucoDetector(dictionary, cv2.aruco.DetectorParameters())


def detect_markers(image: np.ndarray) -> dict[int, np.ndarray]:
    """Marker id -> 4x2 corner pixels."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    corners, ids, _ = _detector().detectMarkers(gray)
    if ids is None:
        return {}
    return {int(i): c.reshape(4, 2) for i, c in zip(ids.ravel(), corners)}


def marker_centers(markers: dict[int, np.ndarray]) -> dict[int, tuple[float, float]]:
    return {i: tuple(c.mean(axis=0).tolist()) for i, c in markers.items()}  # type: ignore[misc]


def drift_px(reference: dict[int, tuple[float, float]], current: dict[int, tuple[float, float]]) -> float:
    """Largest movement of any reference marker; inf if fewer than 3 are still visible."""
    common = [i for i in reference if i in current]
    if len(common) < 3:
        return float("inf")
    return max(float(np.hypot(*(np.subtract(current[i], reference[i])))) for i in common)


@dataclass
class CalibrationFile:
    floor: FloorCalibration
    heater: HeaterFootprint | None = None
    marker_floor_in: dict[int, tuple[float, float]] = field(default_factory=dict)
    marker_ref_px: dict[int, tuple[float, float]] = field(default_factory=dict)
    indicator_roi: tuple[int, int, int, int] | None = None  # x, y, w, h of heater power light
    indicator_threshold: float | None = None

    def save(self, path: Path = CALIBRATION_PATH) -> None:
        data = {
            "floor": self.floor.to_dict(),
            "heater": self.heater.to_dict() if self.heater else None,
            "marker_floor_in": {str(k): list(v) for k, v in self.marker_floor_in.items()},
            "marker_ref_px": {str(k): list(v) for k, v in self.marker_ref_px.items()},
            "indicator_roi": list(self.indicator_roi) if self.indicator_roi else None,
            "indicator_threshold": self.indicator_threshold,
        }
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(data, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path = CALIBRATION_PATH) -> "CalibrationFile":
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            floor=FloorCalibration.from_dict(d["floor"]),
            heater=HeaterFootprint.from_dict(d["heater"]) if d.get("heater") else None,
            marker_floor_in={int(k): tuple(v) for k, v in d.get("marker_floor_in", {}).items()},  # type: ignore[misc]
            marker_ref_px={int(k): tuple(v) for k, v in d.get("marker_ref_px", {}).items()},  # type: ignore[misc]
            indicator_roi=tuple(d["indicator_roi"]) if d.get("indicator_roi") else None,  # type: ignore[arg-type]
            indicator_threshold=d.get("indicator_threshold"),
        )


def calibrate_from_markers(
    image: np.ndarray, marker_floor_in: dict[int, tuple[float, float]]
) -> CalibrationFile:
    """Fit the floor homography from marker centers whose floor positions (inches) were tape-measured."""
    centers = marker_centers(detect_markers(image))
    ids = [i for i in marker_floor_in if i in centers]
    if len(ids) < 4:
        raise ValueError(f"Need >= 4 known markers in view; saw {sorted(centers)}")
    h, w = image.shape[:2]
    floor = FloorCalibration.fit([centers[i] for i in ids], [marker_floor_in[i] for i in ids], (w, h))
    return CalibrationFile(floor=floor, marker_floor_in=dict(marker_floor_in),
                           marker_ref_px={i: centers[i] for i in ids})


def calibrate_from_points(image_size: tuple[int, int], image_pts, floor_pts_in) -> CalibrationFile:
    """Fallback without a printer: click tape marks whose floor positions you measured."""
    return CalibrationFile(floor=FloorCalibration.fit(image_pts, floor_pts_in, image_size))
