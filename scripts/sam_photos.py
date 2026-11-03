"""Run SAM 3.1 on photos, draw masks, and (if calibrated) label each object with inches to the heater.

    python scripts/sam_photos.py data/photos --concept blanket --concept "cardboard box"
    python scripts/sam_photos.py data/photos --spark          # let Muse Spark choose concepts

Outputs annotated images and a CSV to runs/sam_<timestamp>/.
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2  # noqa: E402

from three_feet.calibration import CalibrationFile  # noqa: E402
from three_feet.config import CALIBRATION_PATH, RULES_PATH, RUNS_DIR, get_settings  # noqa: E402
from three_feet.geometry import ClearanceMap, Clearances  # noqa: E402
from three_feet.overlay import STATUS_BGR, draw_mask, draw_measured, draw_zone, put_label  # noqa: E402
from three_feet.perception.meta_client import CostMeter, make_client  # noqa: E402
from three_feet.perception.sam import SamClient  # noqa: E402
from three_feet.perception.spark import ALWAYS_ON_CONCEPTS, SparkClient  # noqa: E402
from three_feet.perception.wonder import ClearanceRule  # noqa: E402
from three_feet.sources import FolderSource  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--concept", action="append", default=[])
    ap.add_argument("--spark", action="store_true", help="ask Muse Spark which concepts to segment")
    ap.add_argument("--calibration", default=str(CALIBRATION_PATH))
    args = ap.parse_args()

    s = get_settings()
    meter = CostMeter(s.budget_usd_per_hour)
    client = make_client(s)
    sam = SamClient(client, meter)
    spark = SparkClient(client, meter) if args.spark else None

    cmap = None
    if Path(args.calibration).exists():
        cal = CalibrationFile.load(Path(args.calibration))
        if cal.heater:
            clear = Clearances.from_rule(ClearanceRule.load()) if RULES_PATH.exists() else Clearances(
                *([s.default_clearance_in] * 4))
            cmap = ClearanceMap(cal.floor, cal.heater, clear, s.mask_edge_px, s.parallax_in, cal.distance_err_frac)
            print(f"Using calibration (RMS {cal.floor.rms_in:.2f} in)")

    out = RUNS_DIR / f"sam_{datetime.now():%Y%m%d_%H%M%S}"
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for frame in FolderSource(args.folder):
        image = frame.image
        if spark:
            concepts = spark.inventory(image)
        else:
            concepts = args.concept or ALWAYS_ON_CONCEPTS
        view = image.copy()
        size_ok = cmap is not None and image.shape[:2] == cmap.slack.shape
        if size_ok:
            draw_zone(view, cmap, s.watch_margin_in)
        for result in sam.segment_many(image, concepts):
            print(f"{frame.name:<28} {result.concept:<18} {len(result.detections)} match(es)  {result.latency_s:.2f}s")
            for d in result.detections:
                m = cmap.measure(d.mask) if size_ok else None
                if m is not None:
                    status = m.status(s.watch_margin_in)
                    draw_measured(view, d.mask, d.concept, m, status)
                else:
                    draw_mask(view, d.mask, STATUS_BGR["watch"])
                rows.append({
                    "photo": frame.name, "concept": d.concept, "object_id": d.object_id,
                    "confidence": d.confidence, "area_px": d.area, "latency_s": round(result.latency_s, 3),
                    "distance_in": round(m.distance_in, 2) if m else "",
                    "err_in": round(m.err_in, 2) if m else "", "side": m.side if m else "",
                    "required_in": m.required_in if m else "",
                })
        put_label(view, f"spent ${meter.total_usd:.3f}", (10, 30))
        cv2.imwrite(str(out / frame.name), view)

    if rows:
        with open(out / "detections.csv", "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    print(f"\nWrote {out}  |  cost so far ${meter.total_usd:.3f}  |  calls {meter.calls}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
