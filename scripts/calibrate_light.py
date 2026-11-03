"""Teach Three Feet what the heater's power light looks like on and off (the independent shutoff check).

    python scripts/calibrate_light.py --source phone

Run after scripts/calibrate.py has saved the light's position (drag a box over it there). With the heater
(or stand-in lamp) ON, press Enter; switch it OFF, press Enter again. The threshold is saved to
data/calibration.json.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from three_feet.calibration import CalibrationFile  # noqa: E402
from three_feet.config import CALIBRATION_PATH  # noqa: E402
from three_feet.safety.verifier import IndicatorProbe, roi_brightness  # noqa: E402
from three_feet.sources import MjpegHttpSource, open_source  # noqa: E402


def grab(src, n: int = 8) -> list:
    frames, last = [], -1
    deadline = time.time() + 10
    while len(frames) < n and time.time() < deadline:
        f = src.read()
        if f is not None and f.index != last:
            frames.append(f.image)
            last = f.index
        time.sleep(0.1)
    if not frames:
        raise SystemExit("No frames from the camera")
    return frames


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="phone")
    ap.add_argument("--calibration", default=str(CALIBRATION_PATH))
    args = ap.parse_args()

    cal = CalibrationFile.load(Path(args.calibration))
    if not cal.indicator_roi:
        raise SystemExit("No power-light box in the calibration. Run scripts/calibrate.py and drag a box over the light.")
    src = open_source(args.source)
    if isinstance(src, MjpegHttpSource) and src.wait_first(10) is None:
        raise SystemExit(f"No frames from the camera: {src.last_error}")
    try:
        input("Turn the heater ON (power light lit) and press Enter ")
        on = grab(src)
        input("Now turn the heater OFF (light dark) and press Enter ")
        time.sleep(1.0)
        off = grab(src)
    finally:
        src.close()

    print(f"brightness on:  {[round(roi_brightness(i, cal.indicator_roi)) for i in on]}")
    print(f"brightness off: {[round(roi_brightness(i, cal.indicator_roi)) for i in off]}")
    try:
        probe = IndicatorProbe.calibrate(cal.indicator_roi, on, off)
    except ValueError as exc:
        raise SystemExit(f"{exc}") from exc
    cal.indicator_threshold = probe.threshold
    cal.save(Path(args.calibration))
    print(f"Saved threshold {probe.threshold:.0f} to {args.calibration}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
