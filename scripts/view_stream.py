"""Live preview of a frame source with FPS; press S to save a snapshot, Q to quit.

    python scripts/view_stream.py --source phone
    python scripts/view_stream.py --source 1            # DroidCam / webcam index
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2  # noqa: E402

from three_feet_app.config import DATA_DIR  # noqa: E402
from three_feet_app.overlay import put_label  # noqa: E402
from three_feet_app.sources import MjpegHttpSource, open_source  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="phone")
    ap.add_argument("--out", default=str(DATA_DIR / "photos"))
    args = ap.parse_args()

    src = open_source(args.source)
    if isinstance(src, MjpegHttpSource):
        print(f"Connecting to {src.url} ...")
        if src.wait_first(10) is None:
            print(f"No frames after 10 s. Last error: {src.last_error}")
            return 1
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    last_index, times = -1, []
    try:
        while True:
            frame = src.read()
            if frame is None:
                if not isinstance(src, MjpegHttpSource):
                    break
                time.sleep(0.01)
                continue
            if frame.index != last_index:
                last_index = frame.index
                times = [t for t in [*times, time.time()] if time.time() - t < 2]
            view = frame.image.copy()
            h, w = view.shape[:2]
            put_label(view, f"{w}x{h}  {len(times) / 2:.1f} fps  healthy={src.healthy}", (10, 30))
            cv2.imshow("Three Feet - camera", view)
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break
            if key == ord("s"):
                path = out / f"{datetime.now():%Y%m%d_%H%M%S}.jpg"
                cv2.imwrite(str(path), frame.image)
                print("saved", path)
    finally:
        src.close()
        cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
