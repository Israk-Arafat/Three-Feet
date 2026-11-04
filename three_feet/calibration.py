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
    method: str = "markers"  # markers | points | heater
    distance_err_frac: float = 0.0  # extra error per inch of distance from the heater (heater method)

    def save(self, path: Path = CALIBRATION_PATH) -> None:
        data = {
            "floor": self.floor.to_dict(),
            "heater": self.heater.to_dict() if self.heater else None,
            "marker_floor_in": {str(k): list(v) for k, v in self.marker_floor_in.items()},
            "marker_ref_px": {str(k): list(v) for k, v in self.marker_ref_px.items()},
            "indicator_roi": list(self.indicator_roi) if self.indicator_roi else None,
            "indicator_threshold": self.indicator_threshold,
            "method": self.method,
            "distance_err_frac": self.distance_err_frac,
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
            method=d.get("method", "markers"),
            distance_err_frac=float(d.get("distance_err_frac", 0.0)),
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
    return CalibrationFile(floor=FloorCalibration.fit(image_pts, floor_pts_in, image_size), method="points")


def heater_floor_corners(width_in: float, depth_in: float) -> list[tuple[float, float]]:
    """Front-left, front-right, back-right, back-left in heater-centred floor inches (front faces +y)."""
    return [(0.0, depth_in), (width_in, depth_in), (width_in, 0.0), (0.0, 0.0)]


def camera_matrix(image_size: tuple[int, int], hfov_deg: float) -> np.ndarray:
    """Pinhole intrinsics from a horizontal field of view, principal point at the image center."""
    w, h = image_size
    f = w / 2 / np.tan(np.radians(hfov_deg) / 2)
    return np.array([[f, 0, w / 2], [0, f, h / 2], [0, 0, 1.0]])


def _heater_pose_homography(corners_px: np.ndarray, floor_pts, K: np.ndarray) -> np.ndarray | None:
    """Image -> floor homography from the camera pose that fits the heater rectangle (planar PnP).

    With the intrinsics fixed only the 6-DOF pose is solved, instead of a free 8-DOF homography whose
    projective terms blow small click errors up into large errors a few feet from the heater.
    """
    obj = np.c_[np.asarray(floor_pts, dtype=np.float64), np.zeros(4)]
    ok, rvec, tvec = cv2.solvePnP(obj, corners_px, K, None, flags=cv2.SOLVEPNP_IPPE)
    if not ok:
        return None
    R, _ = cv2.Rodrigues(rvec)
    floor_to_img = K @ np.c_[R[:, :2], tvec]
    H = np.linalg.inv(floor_to_img)
    return H / H[2, 2] if abs(H[2, 2]) > 1e-12 else H


def calibrate_from_heater(
    image_size: tuple[int, int],
    corners_px,
    width_in: float,
    depth_in: float,
    hfov_deg: float = 66.0,
    fov_err_frac: float = 0.1,
    click_px: float = 3.0,
    size_err_in: float = 0.25,
    reach_in: float = 36.0,
    samples: int = 400,
    seed: int = 0,
) -> CalibrationFile:
    """Quick setup without printed markers: the heater's own base is the known-size reference.

    `corners_px` are the clicked base corners in order front-left, front-right, back-right, back-left
    (as seen standing in front of the heater). The camera pose is solved from them assuming a typical phone
    field of view (`hfov_deg`). Because everything is extrapolated from a small rectangle, error grows with
    distance; a Monte Carlo run jitters the clicks (`click_px`), the measured size (`size_err_in`) and the
    field of view (`fov_err_frac`) and records the 95th-percentile position error at `reach_in` from the
    heater as a fraction of that distance. ClearanceMap adds that fraction of each object's distance to its
    error bar.
    """
    if width_in <= 0 or depth_in <= 0:
        raise ValueError("Heater width and depth must be positive inches")
    corners_px = np.asarray(corners_px, dtype=np.float64).reshape(4, 2)
    floor_pts = np.asarray(heater_floor_corners(width_in, depth_in))
    H = _heater_pose_homography(corners_px, floor_pts, camera_matrix(image_size, hfov_deg))
    if H is None:
        raise ValueError("Could not solve the camera pose; click the 4 base corners in order")
    floor = FloorCalibration(H=H, rms_in=0.0, image_size=image_size)
    floor.rms_in = float(np.sqrt(np.mean(np.sum((floor.image_to_floor(corners_px) - floor_pts) ** 2, axis=1))))

    cx, cy = width_in / 2, depth_in / 2
    d = reach_in / np.sqrt(2)
    probes = np.array([
        (cx, depth_in + reach_in), (cx, -reach_in), (-reach_in, cy), (width_in + reach_in, cy),
        (width_in + d, depth_in + d), (-d, depth_in + d), (width_in + d, -d), (-d, -d),
    ])
    probes_px = floor.floor_to_image(probes).reshape(-1, 1, 2)
    rng = np.random.default_rng(seed)
    errors = []
    for _ in range(samples):
        jittered = corners_px + rng.normal(0.0, click_px, corners_px.shape)
        w = max(width_in + rng.normal(0.0, size_err_in), 0.5)
        dd = max(depth_in + rng.normal(0.0, size_err_in), 0.5)
        fov = float(np.clip(hfov_deg * (1 + rng.normal(0.0, fov_err_frac)), 20.0, 150.0))
        Hj = _heater_pose_homography(jittered, heater_floor_corners(w, dd), camera_matrix(image_size, fov))
        if Hj is None:
            continue
        est = cv2.perspectiveTransform(probes_px, Hj).reshape(-1, 2)
        errors.extend(np.linalg.norm(est - probes, axis=1))
    frac = float(np.percentile(errors, 95)) / reach_in if errors else 0.5
    cal = CalibrationFile(floor=floor, method="heater", distance_err_frac=max(frac, 0.03))
    cal.heater = HeaterFootprint.from_corners(*floor_pts)
    return cal
