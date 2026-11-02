"""Generate printable ArUco markers (DICT_4X4_50), one per letter page at 300 DPI.

    python tools/print_markers.py --ids 0 1 2 3 --size-in 4

Print at 100% scale ("actual size"), then measure one printed marker to confirm its size.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np

DPI = 300
PAGE_W_IN, PAGE_H_IN = 8.5, 11.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", type=int, nargs="+", default=[0, 1, 2, 3])
    ap.add_argument("--size-in", type=float, default=4.0)
    ap.add_argument("--out", default="data/markers")
    args = ap.parse_args()

    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    side = int(args.size_in * DPI)
    for marker_id in args.ids:
        page = np.full((int(PAGE_H_IN * DPI), int(PAGE_W_IN * DPI)), 255, np.uint8)
        marker = cv2.aruco.generateImageMarker(dictionary, marker_id, side)
        y0 = (page.shape[0] - side) // 2
        x0 = (page.shape[1] - side) // 2
        page[y0 : y0 + side, x0 : x0 + side] = marker
        cv2.putText(page, f"Three Feet marker id={marker_id}  ({args.size_in:g} in)  print at 100%",
                    (150, y0 + side + 150), cv2.FONT_HERSHEY_SIMPLEX, 2.0, 0, 4, cv2.LINE_AA)
        path = out / f"aruco_{marker_id}.png"
        cv2.imwrite(str(path), page, [cv2.IMWRITE_PNG_COMPRESSION, 9])
        print("wrote", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
