"""Automatically select a visible heater base with Muse Spark and calibrate without markers.

    python scripts/calibrate_auto.py --source phone --heater 4.5 4.35
    python scripts/calibrate_auto.py --image setup.jpg --heater 4.5 4.35

Writes source image, candidate overlay, and a JSON explanation to runs/setup/. Hidden/ambiguous corners
or poor geometry produce diagnostics without replacing the active calibration. No plug commands run.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from three_feet_app.calibration import calibrate_from_heater, heater_floor_corners  # noqa: E402
from three_feet_app.config import CALIBRATION_PATH, RUNS_DIR  # noqa: E402
from three_feet_app.perception.meta_client import CostMeter  # noqa: E402
from three_feet_app.perception.setup import (  # noqa: E402
    CORNER_NAMES, indicator_roi, locate_heater_base, validate_base_candidate,
)
from three_feet_app.sources import MjpegHttpSource, open_source  # noqa: E402


def capture(image_path: str | None, source: str) -> np.ndarray:
    if image_path:
        image = cv2.imread(image_path)
        if image is None:
            raise ValueError(f"Cannot read {image_path}")
        return image
    src = open_source(source)
    try:
        frame = src.wait_first(12) if isinstance(src, MjpegHttpSource) else src.read()
        if frame is None or not src.healthy:
            raise ValueError("No fresh frame from the camera")
        return frame.image.copy()
    finally:
        src.close()


def candidate_overlay(image: np.ndarray, candidate: dict, accepted: bool) -> np.ndarray:
    overlay = image.copy()
    points = []
    for name in CORNER_NAMES:
        p = candidate.get("corners", {}).get(name, {})
        x, y = float(p.get("x", np.nan)), float(p.get("y", np.nan))
        if not np.isfinite([x, y]).all():
            continue
        if not (0 <= x < image.shape[1] and 0 <= y < image.shape[0]):
            continue
        xy = int(round(x)), int(round(y))
        points.append(xy)
        visible = p.get("visible", False)
        color = (0, 230, 0) if visible else (0, 140, 255)
        cv2.circle(overlay, xy, 5, color, -1)
        label = "".join(part[0].upper() for part in name.split("_")) + ("" if visible else " hidden")
        cv2.putText(overlay, label, (xy[0] + 7, xy[1] - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                    color, 1, cv2.LINE_AA)
    if len(points) == 4:
        cv2.polylines(overlay, [np.array(points, np.int32)], True,
                      (0, 230, 0) if accepted else (0, 140, 255), 2, cv2.LINE_AA)
    cv2.rectangle(overlay, (0, 0), (image.shape[1], 36), (20, 20, 20), -1)
    cv2.putText(overlay, "AUTO CALIBRATION SAVED" if accepted else "CANDIDATE ONLY - NOT CALIBRATED",
                (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1, cv2.LINE_AA)
    return overlay


def json_safe(value):
    """Keep rejected nonfinite coordinates readable as null in the diagnostic JSON."""
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    source = ap.add_mutually_exclusive_group()
    source.add_argument("--image")
    source.add_argument("--source", default="phone")
    ap.add_argument("--heater", nargs=2, type=float, required=True, metavar=("WIDTH_IN", "DEPTH_IN"))
    ap.add_argument("--fov", type=float, default=66.0)
    ap.add_argument("--max-error-pct", type=float, default=30.0,
                    help="reject wider modelled distance uncertainty (default 30 percent)")
    ap.add_argument("--out", type=Path, default=CALIBRATION_PATH)
    ap.add_argument("--artifacts", type=Path, help="diagnostic output directory (default runs/setup/timestamp)")
    args = ap.parse_args()
    if not np.isfinite(args.heater).all() or min(args.heater) <= 0:
        ap.error("Heater width and depth must be finite, positive measured inches")
    if not np.isfinite(args.max_error_pct) or not 0 < args.max_error_pct <= 50:
        ap.error("--max-error-pct must be greater than 0 and at most 50")
    if not np.isfinite(args.fov) or not 20 <= args.fov <= 150:
        ap.error("--fov must be between 20 and 150 degrees")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    artifacts = args.artifacts or RUNS_DIR / "setup" / stamp
    artifacts.mkdir(parents=True, exist_ok=True)
    meter = CostMeter(5.0)
    result = {"accepted": False, "measured_base_in": args.heater, "model": "Muse Spark",
              "artifacts": str(artifacts.resolve()), "candidate": None}
    image, candidate, cal = None, {}, None
    try:
        image = capture(args.image, args.source)
        cv2.imwrite(str(artifacts / "snapshot.jpg"), image)
        result["image_size"] = [image.shape[1], image.shape[0]]
        candidate = locate_heater_base(image, *args.heater, meter=meter)
        result["candidate"] = candidate
        corners = validate_base_candidate(candidate, tuple(result["image_size"]))
        cal = calibrate_from_heater(tuple(result["image_size"]), corners, *args.heater, hfov_deg=args.fov)
        if not np.isfinite(cal.floor.H).all() or not np.isfinite(cal.floor.rms_in):
            raise ValueError("The solved floor geometry is nonfinite")
        result["rms_in"] = cal.floor.rms_in
        result["distance_error_pct"] = cal.distance_err_frac * 100
        if cal.distance_err_frac * 100 >= args.max_error_pct:
            raise ValueError(f"Modelled uncertainty is {cal.distance_err_frac * 100:.1f}% of distance; "
                             f"the automatic setup limit is {args.max_error_pct:g}%")
        reprojection = cal.floor.floor_to_image(heater_floor_corners(*args.heater))
        error_px = float(np.max(np.linalg.norm(reprojection - corners, axis=1)))
        result["max_corner_reprojection_px"] = error_px
        if not np.isfinite(error_px) or error_px > 3.0:
            raise ValueError(f"Base corners do not fit the measured rectangle (pixel error {error_px:.1f})")
        # The heater/camera has been repositioned: never reuse the previous light threshold or ROI.
        cal.indicator_roi = indicator_roi(candidate, tuple(result["image_size"]))
        cal.indicator_threshold = None
        if args.out.exists():
            shutil.copy2(args.out, artifacts / "previous_calibration.json")
        cal.save(args.out)
        result["accepted"] = True
        result["saved_calibration"] = str(args.out.resolve())
        result["reason"] = "Visible floor-contact corners passed geometry and confidence checks; " \
                           "distance accuracy still needs tape-measure validation."
    except Exception as exc:
        result["reason"] = f"{type(exc).__name__}: {exc}"
    finally:
        result["spend_estimate"] = meter.snapshot()
        if image is not None:
            overlay = candidate_overlay(image, candidate, bool(result["accepted"]))
            cv2.imwrite(str(artifacts / "candidate_overlay.jpg"), overlay)
            if candidate.get("focused_crop_px"):
                x, y, w, h = candidate["focused_crop_px"]
                detail = overlay[y:y + h, x:x + w]
                scale = min(3.0, 960 / max(detail.shape[:2]))
                detail = cv2.resize(detail, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
                cv2.imwrite(str(artifacts / "candidate_overlay_detail.jpg"), detail)
        (artifacts / "candidate.json").write_text(json.dumps(json_safe(result), indent=2, allow_nan=False),
                                                  encoding="utf-8")
    print(json.dumps(json_safe({k: v for k, v in result.items() if k != "candidate"}), indent=2))
    return 0 if result["accepted"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
