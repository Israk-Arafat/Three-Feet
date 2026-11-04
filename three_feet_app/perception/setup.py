"""Model-assisted floor-contact corner selection for marker-free heater setup."""

from __future__ import annotations

import cv2
import numpy as np

from ..config import get_settings
from .meta_client import CostMeter, chat_json, image_data_url, make_client

CORNER_NAMES = ("front_left", "front_right", "back_right", "back_left")

POINT_SCHEMA = {
    "type": "object",
    "properties": {
        "x": {"type": "number"},
        "y": {"type": "number"},
        "visible": {"type": "boolean"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "evidence": {"type": "string"},
    },
    "required": ["x", "y", "visible", "confidence", "evidence"],
}

BASE_SCHEMA = {
    "type": "object",
    "properties": {
        "heater_found": {"type": "boolean"},
        "base_on_floor": {"type": "boolean"},
        "front_identified": {"type": "boolean"},
        "selected_surface": {
            "type": "string", "enum": ["floor_contact_base", "body_front", "top", "unclear"],
        },
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reason": {"type": "string"},
        "corners": {
            "type": "object",
            "properties": {name: POINT_SCHEMA for name in CORNER_NAMES},
            "required": list(CORNER_NAMES),
        },
        "indicator": {
            "type": "object",
            "properties": {
                "visible": {"type": "boolean"},
                "x": {"type": "number"}, "y": {"type": "number"},
                "width": {"type": "number"}, "height": {"type": "number"},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": ["visible", "x", "y", "width", "height", "confidence"],
        },
    },
    "required": ["heater_found", "base_on_floor", "front_identified", "selected_surface",
                 "confidence", "reason", "corners", "indicator"],
}

BASE_PROMPT = """Find the space heater's rectangular BASE FOOTPRINT where it contacts the FLOOR.
The heater's measured base is {width:g} inches wide by {depth:g} inches deep. Do not estimate inches.
The image is {w} pixels wide and {h} pixels high. Return image coordinates with x right, y down.
Identify FRONT-LEFT, FRONT-RIGHT, BACK-RIGHT, BACK-LEFT, with left/right as a person standing in
front of the heater and facing it would see them. The front is the face that emits heat.

CRITICAL: Select the small horizontal floor-contact rectangle, NOT the tall front grille, entire
heater bounding box, control panel, or top surface. An upright tower often hides its rear base corners.
Set each corner's visible=false when the exact floor-contact corner is occluded, rounded, ambiguous,
or only inferred from symmetry/perspective. Do not pretend hidden corners are directly visible.
Explain the floor-contact evidence for each point and any ambiguity in reason. Coordinates for hidden
corners may be approximate candidates for an overlay, but must be marked invisible and low confidence.
If the base is not a clear rectangle, say selected_surface=unclear. A correct failure is useful.
Optionally locate a clearly identifiable power-indicator light; return visible=false and zeros if absent.
"""


def _query_base(image: np.ndarray, width_in: float, depth_in: float, *, client,
                meter: CostMeter | None = None) -> dict:
    s = get_settings()
    url, scale = image_data_url(image, max_side=1280, quality=92)
    w, h = round(image.shape[1] * scale), round(image.shape[0] * scale)
    candidate = chat_json(
        client, s.spark_model,
        [{"role": "user", "content": [
            {"type": "text", "text": BASE_PROMPT.format(width=width_in, depth=depth_in, w=w, h=h)},
            {"type": "image_url", "image_url": {"url": url}},
        ]}],
        "HeaterBaseSetup", BASE_SCHEMA, meter,
    )
    # All public/result coordinates refer to the original image even if encoding resized it.
    for point in candidate.get("corners", {}).values():
        point["x"], point["y"] = float(point["x"]) / scale, float(point["y"]) / scale
    indicator = candidate.get("indicator", {})
    for key in ("x", "y", "width", "height"):
        if key in indicator:
            indicator[key] = float(indicator[key]) / scale
    return candidate


def locate_heater_base(image: np.ndarray, width_in: float, depth_in: float, *, client=None,
                       meter: CostMeter | None = None, refine: bool = True) -> dict:
    """Find corners, then inspect one focused crop if the full view was uncertain.

    Enlarging a crop assists localization; it does not make hidden corners visible or create detail.
    Every returned coordinate is mapped back to the untouched original camera frame.
    """
    client = client or make_client()
    initial = _query_base(image, width_in, depth_in, client=client, meter=meter)
    if not refine:
        return initial
    points = np.array([[p.get("x", np.nan), p.get("y", np.nan)]
                       for p in initial.get("corners", {}).values()], dtype=float)
    if (not initial.get("heater_found") or points.shape != (4, 2) or not np.isfinite(points).all()
            or (float(initial.get("confidence", 0)) >= 0.8
                and all(p.get("visible") for p in initial["corners"].values()))):
        return initial
    cx, cy = points.mean(axis=0)
    radius = max(96.0, 4 * float(np.ptp(points, axis=0).max()))
    h, w = image.shape[:2]
    x0, y0 = max(0, int(cx - radius)), max(0, int(cy - 2 * radius))
    x1, y1 = min(w, int(cx + radius)), min(h, int(cy + radius))
    if x1 - x0 < 32 or y1 - y0 < 32:
        return initial
    crop = image[y0:y1, x0:x1]
    scale = min(3.0, 960 / max(crop.shape[:2]))
    enlarged = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    try:
        refined = _query_base(enlarged, width_in, depth_in, client=client, meter=meter)
    except Exception as exc:
        initial["refinement_error"] = f"{type(exc).__name__}: {exc}"
        return initial
    # cv2 rounds output dimensions; derive the actual x/y scales for exact inverse mapping.
    sx, sy = enlarged.shape[1] / crop.shape[1], enlarged.shape[0] / crop.shape[0]
    for point in refined.get("corners", {}).values():
        point["x"], point["y"] = point["x"] / sx + x0, point["y"] / sy + y0
    indicator = refined.get("indicator", {})
    if indicator:
        indicator["x"], indicator["y"] = indicator["x"] / sx + x0, indicator["y"] / sy + y0
        indicator["width"], indicator["height"] = indicator["width"] / sx, indicator["height"] / sy
    refined["initial_selection"] = initial
    refined["focused_crop_px"] = [x0, y0, x1 - x0, y1 - y0]
    return refined


def validate_base_candidate(candidate: dict, image_size: tuple[int, int],
                            min_confidence: float = 0.8, min_area: float = 128,
                            min_edge: float = 8) -> np.ndarray:
    """Reject occluded/model-uncertain corners and numerically unusable quadrilaterals."""
    if not candidate.get("heater_found") or not candidate.get("base_on_floor"):
        raise ValueError("The model could not identify a heater base on the floor")
    if candidate.get("selected_surface") != "floor_contact_base":
        raise ValueError("The selected surface is not an identifiable floor-contact base")
    if not candidate.get("front_identified"):
        raise ValueError("The heater's front direction is unclear")
    confidence = float(candidate.get("confidence", 0))
    if not np.isfinite(confidence) or confidence < min_confidence:
        raise ValueError(f"Base selection confidence {confidence:.2f} is below {min_confidence:.2f}")
    if not str(candidate.get("reason", "")).strip():
        raise ValueError("The model did not explain its floor-contact selection")
    points = []
    for name in CORNER_NAMES:
        point = candidate.get("corners", {}).get(name, {})
        if not point.get("visible"):
            raise ValueError(f"{name.replace('_', '-')} floor-contact corner is hidden or ambiguous")
        conf = float(point.get("confidence", 0))
        if not np.isfinite(conf) or conf < min_confidence:
            raise ValueError(f"{name.replace('_', '-')} corner confidence is too low ({conf:.2f})")
        if not str(point.get("evidence", "")).strip():
            raise ValueError(f"No floor-contact evidence for {name.replace('_', '-')}")
        points.append((float(point.get("x", np.nan)), float(point.get("y", np.nan))))
    corners = np.asarray(points, dtype=np.float64)
    if not np.isfinite(corners).all():
        raise ValueError("Corner coordinates must be finite")
    w, h = image_size
    if (corners < 0).any() or (corners[:, 0] >= w).any() or (corners[:, 1] >= h).any():
        raise ValueError("Base corners extend outside the camera image")
    contour = corners.astype(np.float32)
    if not cv2.isContourConvex(contour) or cv2.contourArea(contour) < min_area:
        raise ValueError("Base corners must form a sufficiently large, ordered convex quadrilateral")
    edge_lengths = np.linalg.norm(np.roll(corners, -1, axis=0) - corners, axis=1)
    if edge_lengths.min() < min_edge:
        raise ValueError("The base is too small or edge-on for reliable automatic calibration")
    return corners


def indicator_roi(candidate: dict, image_size: tuple[int, int]) -> tuple[int, int, int, int] | None:
    """A visible indicator box is optional; an on/off brightness threshold still needs calibration."""
    roi = candidate.get("indicator", {})
    if not roi.get("visible") or float(roi.get("confidence", 0)) < 0.8:
        return None
    values = np.array([roi.get(k, np.nan) for k in ("x", "y", "width", "height")], dtype=float)
    if not np.isfinite(values).all():
        return None
    x, y, w, h = np.rint(values).astype(int)
    if min(x, y) < 0 or min(w, h) < 2 or x + w > image_size[0] or y + h > image_size[1]:
        return None
    return int(x), int(y), int(w), int(h)
