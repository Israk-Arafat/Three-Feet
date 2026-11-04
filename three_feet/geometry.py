"""Floor-plane geometry: image pixels -> floor inches, per-side clearance zone, distance with error bar."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

import cv2
import numpy as np

Side = Literal["front", "back", "left", "right", "inside"]
SIDES: tuple[Side, ...] = ("front", "back", "left", "right", "inside")
Status = Literal["clear", "watch", "violation"]


@dataclass
class FloorCalibration:
    """Homography from image pixels to floor coordinates in inches."""

    H: np.ndarray  # 3x3, image px -> floor in
    rms_in: float  # reprojection error of the calibration points, inches
    image_size: tuple[int, int]  # (width, height)

    @classmethod
    def fit(cls, image_pts, floor_pts, image_size: tuple[int, int]) -> "FloorCalibration":
        img = np.asarray(image_pts, dtype=np.float64).reshape(-1, 2)
        flr = np.asarray(floor_pts, dtype=np.float64).reshape(-1, 2)
        if len(img) < 4:
            raise ValueError("Need at least 4 point correspondences")
        H, _ = cv2.findHomography(img, flr, 0)
        if H is None:
            raise ValueError("Homography fit failed; are the points collinear?")
        cal = cls(H=H, rms_in=0.0, image_size=image_size)
        err = np.linalg.norm(cal.image_to_floor(img) - flr, axis=1)
        cal.rms_in = float(np.sqrt(np.mean(err**2)))
        return cal

    @property
    def H_inv(self) -> np.ndarray:
        return np.linalg.inv(self.H)

    def image_to_floor(self, pts) -> np.ndarray:
        pts = np.asarray(pts, dtype=np.float64).reshape(-1, 1, 2)
        return cv2.perspectiveTransform(pts, self.H).reshape(-1, 2)

    def floor_to_image(self, pts) -> np.ndarray:
        pts = np.asarray(pts, dtype=np.float64).reshape(-1, 1, 2)
        return cv2.perspectiveTransform(pts, self.H_inv).reshape(-1, 2)

    def inches_per_px(self, x: float, y: float) -> float:
        p = self.image_to_floor([[x, y], [x + 1, y], [x, y + 1]])
        return float((np.linalg.norm(p[1] - p[0]) + np.linalg.norm(p[2] - p[0])) / 2)

    def to_dict(self) -> dict:
        return {"H": self.H.tolist(), "rms_in": self.rms_in, "image_size": list(self.image_size)}

    @classmethod
    def from_dict(cls, d: dict) -> "FloorCalibration":
        return cls(H=np.array(d["H"], dtype=np.float64), rms_in=float(d["rms_in"]),
                   image_size=tuple(d["image_size"]))  # type: ignore[arg-type]


@dataclass
class HeaterFootprint:
    """Heater base as an oriented rectangle on the floor (inches)."""

    center: tuple[float, float]
    width_in: float  # side to side
    depth_in: float  # front to back
    front: tuple[float, float]  # unit vector the heater faces
    right: tuple[float, float]  # unit vector to the right as seen by a person standing in front, facing the heater

    @classmethod
    def from_corners(cls, front_left, front_right, back_right, back_left) -> "HeaterFootprint":
        fl, fr, br, bl = (np.asarray(p, dtype=np.float64) for p in (front_left, front_right, back_right, back_left))
        center = (fl + fr + br + bl) / 4
        front_vec = (fl + fr) / 2 - (bl + br) / 2
        front = front_vec / np.linalg.norm(front_vec)
        right_raw = (fr - fl + br - bl) / 2
        right = right_raw - front * np.dot(right_raw, front)
        right /= np.linalg.norm(right)
        width = (np.linalg.norm(fr - fl) + np.linalg.norm(br - bl)) / 2
        depth = (np.linalg.norm(fl - bl) + np.linalg.norm(fr - br)) / 2
        return cls(tuple(center), float(width), float(depth), tuple(front), tuple(right))  # type: ignore[arg-type]

    def to_local(self, floor_pts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """(u, v): u toward the viewer's right (facing the heater), v toward its front, origin at its center."""
        d = floor_pts - np.asarray(self.center)
        return d @ np.asarray(self.right), d @ np.asarray(self.front)

    def from_local(self, u: np.ndarray, v: np.ndarray) -> np.ndarray:
        return (np.asarray(self.center) + np.outer(u, self.right) + np.outer(v, self.front))

    def corners(self) -> np.ndarray:
        hw, hd = self.width_in / 2, self.depth_in / 2
        return self.from_local(np.array([-hw, hw, hw, -hw]), np.array([hd, hd, -hd, -hd]))

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "HeaterFootprint":
        return cls(tuple(d["center"]), d["width_in"], d["depth_in"], tuple(d["front"]), tuple(d["right"]))  # type: ignore[arg-type]


@dataclass(frozen=True)
class Clearances:
    front_in: float = 36.0
    back_in: float = 36.0
    left_in: float = 36.0
    right_in: float = 36.0

    @classmethod
    def from_rule(cls, rule) -> "Clearances":
        return cls(rule.front_in, rule.back_in, rule.left_in, rule.right_in)


def clearance_fields(heater: HeaterFootprint, clear: Clearances, floor_pts: np.ndarray):
    """Vectorized: distance to heater, required clearance, slack (= distance - required), side index.

    Near a corner both adjacent sides apply and the larger requirement wins (conservative).
    Points on or inside the footprint get distance 0 and the largest requirement.
    """
    u, v = heater.to_local(floor_pts)
    hw, hd = heater.width_in / 2, heater.depth_in / 2
    du = np.maximum(np.abs(u) - hw, 0.0)
    dv = np.maximum(np.abs(v) - hd, 0.0)
    distance = np.hypot(du, dv)

    reqs = np.stack([
        np.where(v > hd, clear.front_in, 0.0),
        np.where(v < -hd, clear.back_in, 0.0),
        np.where(u < -hw, clear.left_in, 0.0),
        np.where(u > hw, clear.right_in, 0.0),
    ])
    required = reqs.max(axis=0)
    side_idx = reqs.argmax(axis=0)
    inside = required == 0.0
    required = np.where(inside, max(clear.front_in, clear.back_in, clear.left_in, clear.right_in), required)
    side_idx = np.where(inside, 4, side_idx)
    return distance, required, distance - required, side_idx


@dataclass(frozen=True)
class Measurement:
    distance_in: float
    required_in: float
    slack_in: float  # distance - required; negative means inside the zone
    err_in: float
    side: Side
    closest_px: tuple[int, int]  # point on the object closest (by slack) to violating
    heater_px: tuple[int, int]  # nearest point on the heater footprint, for drawing a line

    def status(self, watch_margin_in: float) -> Status:
        return classify(self.slack_in, self.err_in, watch_margin_in)


def classify(slack_in: float, err_in: float, watch_margin_in: float) -> Status:
    """Conservative: use the pessimistic end of the error bar."""
    if slack_in - err_in < 0:
        return "violation"
    if slack_in - err_in < watch_margin_in:
        return "watch"
    return "clear"


class ClearanceMap:
    """Per-pixel slack (inches) precomputed once per calibration so measuring a mask is a lookup."""

    def __init__(self, calib: FloorCalibration, heater: HeaterFootprint, clear: Clearances,
                 mask_edge_px: float = 2.0, parallax_in: float = 0.5, distance_err_frac: float = 0.0) -> None:
        """`distance_err_frac` adds error that grows with distance from the heater (heater-only calibration)."""
        self.calib, self.heater, self.clear = calib, heater, clear
        self.mask_edge_px, self.parallax_in = mask_edge_px, parallax_in
        self.distance_err_frac = distance_err_frac
        w, h = calib.image_size
        xs, ys = np.meshgrid(np.arange(w, dtype=np.float64), np.arange(h, dtype=np.float64))
        pix = np.stack([xs.ravel(), ys.ravel(), np.ones(w * h)])
        proj = calib.H @ pix
        heater_px = calib.floor_to_image([heater.center])[0]
        ref_sign = np.sign((calib.H @ np.array([heater_px[0], heater_px[1], 1.0]))[2]) or 1.0
        valid = np.sign(proj[2]) == ref_sign
        floor = (proj[:2] / np.where(proj[2] == 0, 1e-12, proj[2])).T
        distance, required, slack, side_idx = clearance_fields(heater, clear, floor)
        self.valid = valid.reshape(h, w)
        self.distance = np.where(self.valid, distance.reshape(h, w), np.inf).astype(np.float32)
        self.required = required.reshape(h, w).astype(np.float32)
        self.slack = np.where(self.valid, slack.reshape(h, w), np.inf).astype(np.float32)
        self.side_idx = side_idx.reshape(h, w).astype(np.uint8)

    def zone_mask(self, margin_in: float = 0.0) -> np.ndarray:
        return self.slack < margin_in

    def zone_contours(self, margin_in: float = 0.0) -> list[np.ndarray]:
        contours, _ = cv2.findContours(self.zone_mask(margin_in).astype(np.uint8),
                                       cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        return list(contours)

    def heater_polygon_px(self) -> np.ndarray:
        return self.calib.floor_to_image(self.heater.corners()).round().astype(np.int32)

    def measure(self, mask: np.ndarray) -> Measurement | None:
        if mask.shape != self.slack.shape:
            raise ValueError(f"mask shape {mask.shape} != calibration {self.slack.shape}")
        sel = mask & self.valid
        if not sel.any():
            return None
        ys, xs = np.nonzero(sel)
        vals = self.slack[ys, xs]
        i = int(np.argmin(vals))
        x, y = int(xs[i]), int(ys[i])
        err = (self.calib.rms_in + self.mask_edge_px * self.calib.inches_per_px(x, y) + self.parallax_in
               + self.distance_err_frac * float(self.distance[y, x]))
        floor_pt = self.calib.image_to_floor([[x, y]])
        u, v = self.heater.to_local(floor_pt)
        hw, hd = self.heater.width_in / 2, self.heater.depth_in / 2
        nearest = self.heater.from_local(np.clip(u, -hw, hw), np.clip(v, -hd, hd))
        hx, hy = self.calib.floor_to_image(nearest)[0]
        return Measurement(
            distance_in=float(self.distance[y, x]),
            required_in=float(self.required[y, x]),
            slack_in=float(vals[i]),
            err_in=float(err),
            side=SIDES[int(self.side_idx[y, x])],
            closest_px=(x, y),
            heater_px=(int(round(hx)), int(round(hy))),
        )
