"""Calibrate the floor plane and mark the heater, from a photo or the live camera.

Quick setup (no printing): measure the heater's base width and depth in inches, then click its 4 base
corners. The heater itself sets the scale; error bars widen with distance from it.
    python scripts/calibrate.py --source phone --heater 14 9

ArUco mode (printed markers, floor positions measured with a tape):
    python scripts/calibrate.py --image data/photos/setup.jpg --layout data/markers_layout.example.json

Click mode (no printer): click each tape mark in the same order as `points` in the layout file:
    python scripts/calibrate.py --image data/photos/setup.jpg --layout data/tape_layout.json --click

Heater corners are clicked in this order: FRONT-LEFT, FRONT-RIGHT, BACK-RIGHT, BACK-LEFT ("left/right" as
seen standing in front of the heater, facing it), where the base meets the floor. Optionally drag a box
over the heater's power light (Enter to accept, Esc to skip).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from three_feet_app.calibration import calibrate_from_heater, calibrate_from_markers, calibrate_from_points  # noqa: E402
from three_feet_app.config import CALIBRATION_PATH, RULES_PATH, get_settings  # noqa: E402
from three_feet_app.geometry import ClearanceMap, Clearances, HeaterFootprint  # noqa: E402
from three_feet_app.overlay import draw_zone, put_label  # noqa: E402
from three_feet_app.perception.wonder import ClearanceRule  # noqa: E402
from three_feet_app.sources import MjpegHttpSource, open_source  # noqa: E402


def click_points(image: np.ndarray, n: int, title: str, prompts: list[str]) -> list[tuple[int, int]]:
    pts: list[tuple[int, int]] = []

    def on_mouse(event, x, y, *_):
        if event == cv2.EVENT_LBUTTONDOWN and len(pts) < n:
            pts.append((x, y))

    cv2.namedWindow(title, cv2.WINDOW_NORMAL)
    cv2.setMouseCallback(title, on_mouse)
    while len(pts) < n:
        view = image.copy()
        for i, p in enumerate(pts):
            cv2.circle(view, p, 6, (0, 255, 255), -1)
            put_label(view, str(i + 1), (p[0] + 8, p[1]))
        put_label(view, f"Click {prompts[len(pts)]}  ({len(pts) + 1}/{n})  Backspace=undo", (10, 30))
        cv2.imshow(title, view)
        key = cv2.waitKey(20) & 0xFF
        if key == 8 and pts:
            pts.pop()
        if key == 27:
            raise SystemExit("cancelled")
    cv2.destroyWindow(title)
    return pts


def grab_image(args) -> np.ndarray:
    if args.image:
        img = cv2.imread(args.image)
        if img is None:
            raise SystemExit(f"cannot read {args.image}")
        return img
    src = open_source(args.source)
    frame = src.wait_first(10) if isinstance(src, MjpegHttpSource) else src.read()
    src.close()
    if frame is None:
        raise SystemExit("no frame from source")
    return frame.image


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--image")
    ap.add_argument("--source", default="phone")
    ap.add_argument("--layout", help="JSON with marker or tape-point floor positions (inches)")
    ap.add_argument("--click", action="store_true", help="click tape marks instead of detecting ArUco")
    ap.add_argument("--heater", nargs=2, type=float, metavar=("WIDTH_IN", "DEPTH_IN"),
                    help="quick setup: the heater base's width and depth in inches; no markers needed")
    ap.add_argument("--fov", type=float, default=66.0,
                    help="quick setup: the phone camera's horizontal field of view in degrees (default 66)")
    ap.add_argument("--out", default=str(CALIBRATION_PATH))
    args = ap.parse_args()
    if not args.heater and not args.layout:
        ap.error("give --heater WIDTH DEPTH for the quick setup, or --layout for markers / tape marks")

    image = grab_image(args)
    h, w = image.shape[:2]
    corner_prompts = ["heater FRONT-LEFT", "FRONT-RIGHT", "BACK-RIGHT", "BACK-LEFT"]

    if args.heater:
        corners_px = click_points(image, 4, "heater base corners", corner_prompts)
        cal = calibrate_from_heater((w, h), corners_px, *args.heater, hfov_deg=args.fov)
        pct = cal.distance_err_frac * 100
        print(f"Quick setup from a {args.heater[0]:g} x {args.heater[1]:g} in heater: error grows about "
              f"{pct:.0f}% of distance (about +/-{cal.distance_err_frac * 36:.1f} in at 36 in).")
        if pct >= 30:
            print("That's wide. Move the phone closer (heater 200+ px wide) or use printed markers.")
    else:
        layout = json.loads(Path(args.layout).read_text(encoding="utf-8"))
        if args.click:
            points = layout["points"]
            clicked = click_points(image, len(points), "tape marks", [p["name"] for p in points])
            cal = calibrate_from_points((w, h), clicked, [p["floor_in"] for p in points])
        else:
            markers = {int(k): tuple(v) for k, v in layout["markers"].items()}
            cal = calibrate_from_markers(image, markers)
        print(f"Floor homography fitted; reprojection RMS = {cal.floor.rms_in:.2f} in")
        corners_px = click_points(image, 4, "heater base", corner_prompts)
        corners_in = cal.floor.image_to_floor(corners_px)
        cal.heater = HeaterFootprint.from_corners(*corners_in)
    print(f"Heater footprint: {cal.heater.width_in:.1f} in wide x {cal.heater.depth_in:.1f} in deep")

    roi = cv2.selectROI("Drag a box over the heater power light (Esc to skip)", image, showCrosshair=False)
    cv2.destroyAllWindows()
    if roi[2] > 0 and roi[3] > 0:
        cal.indicator_roi = tuple(int(v) for v in roi)  # type: ignore[assignment]

    cal.save(Path(args.out))
    print("Saved", args.out)

    s = get_settings()
    clear = Clearances.from_rule(ClearanceRule.load()) if RULES_PATH.exists() else Clearances(
        *([s.default_clearance_in] * 4))
    cmap = ClearanceMap(cal.floor, cal.heater, clear, s.mask_edge_px, s.parallax_in, cal.distance_err_frac)
    preview = image.copy()
    draw_zone(preview, cmap, s.watch_margin_in)
    accuracy = (f"quick setup, +/-{cal.distance_err_frac * 100:.0f}% of distance" if cal.method == "heater"
                else f"RMS {cal.floor.rms_in:.2f} in")
    put_label(preview, f"{accuracy}  |  red = clearance zone, amber = +{s.watch_margin_in:.0f} in", (10, 30))
    cv2.imshow("zone preview (any key to close)", preview)
    cv2.waitKey(0)
    cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
