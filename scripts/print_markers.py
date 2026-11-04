"""Generate printable ArUco markers (DICT_4X4_50): one letter page per marker, as PNGs and one PDF.

    python scripts/print_markers.py --ids 0 1 2 3 --size-in 4

Print data/markers/aruco_markers.pdf at "Actual size" (100%, not "Fit"), then measure the 1-inch bar on
each page to confirm the printer did not scale it.
"""

from __future__ import annotations

import argparse
import zlib
from pathlib import Path

import cv2
import numpy as np

DPI = 300
PAGE_W_IN, PAGE_H_IN = 8.5, 11.0


def render_page(dictionary, marker_id: int, size_in: float) -> np.ndarray:
    page = np.full((int(PAGE_H_IN * DPI), int(PAGE_W_IN * DPI)), 255, np.uint8)
    side = int(round(size_in * DPI))
    marker = cv2.aruco.generateImageMarker(dictionary, marker_id, side)
    y0 = (page.shape[0] - side) // 2
    x0 = (page.shape[1] - side) // 2
    page[y0 : y0 + side, x0 : x0 + side] = marker

    font = cv2.FONT_HERSHEY_SIMPLEX
    cv2.putText(page, f"Three Feet marker {marker_id}", (x0, y0 - 220), font, 2.2, 0, 5, cv2.LINE_AA)
    cv2.putText(page, f"{size_in:g} in square. Print at Actual size (100%).", (x0, y0 - 120), font, 1.3, 0, 3,
                cv2.LINE_AA)

    # 1-inch scale bar under the marker, to check the printout.
    bar_y = y0 + side + 180
    cv2.rectangle(page, (x0, bar_y), (x0 + DPI, bar_y + 24), 0, -1)
    for x in (x0, x0 + DPI):
        cv2.line(page, (x, bar_y - 30), (x, bar_y + 54), 0, 4)
    cv2.putText(page, "this bar must measure 1 in", (x0 + DPI + 40, bar_y + 24), font, 1.2, 0, 3, cv2.LINE_AA)
    cv2.putText(page, "Tape the page flat. Measure the floor position of the marker's CENTER.",
                (x0 - 260, bar_y + 160), font, 1.1, 0, 3, cv2.LINE_AA)
    return page


def write_pdf(pages: list[np.ndarray], path: Path, dpi: int = DPI) -> None:
    """Minimal PDF: each page is one lossless grayscale image placed at its true physical size."""
    objects: list[bytes] = []

    def add(obj: bytes) -> int:
        objects.append(obj)
        return len(objects)

    catalog = add(b"")  # filled in once the page tree exists
    pages_id = add(b"")
    page_ids = []
    for img in pages:
        h, w = img.shape
        w_pt, h_pt = w * 72 / dpi, h * 72 / dpi
        data = zlib.compress(img.tobytes(), 9)
        image_id = add(b"<< /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace /DeviceGray "
                       b"/BitsPerComponent 8 /Interpolate false /Filter /FlateDecode /Length %d >>\nstream\n"
                       % (w, h, len(data)) + data + b"\nendstream")
        content = b"q %.4f 0 0 %.4f 0 0 cm /Im0 Do Q" % (w_pt, h_pt)
        content_id = add(b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream")
        page_ids.append(add(b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 %.4f %.4f] "
                            b"/Resources << /XObject << /Im0 %d 0 R >> >> /Contents %d 0 R >>"
                            % (pages_id, w_pt, h_pt, image_id, content_id)))
    objects[catalog - 1] = b"<< /Type /Catalog /Pages %d 0 R >>" % pages_id
    kids = b" ".join(b"%d 0 R" % i for i in page_ids)
    objects[pages_id - 1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (kids, len(page_ids))

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, obj in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + obj + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % off for off in offsets)
    out += b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, catalog, xref)
    path.write_bytes(bytes(out))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", type=int, nargs="+", default=[0, 1, 2, 3])
    ap.add_argument("--size-in", type=float, default=4.0)
    ap.add_argument("--out", default="data/markers")
    args = ap.parse_args()
    if not 1.0 <= args.size_in <= 6.5:
        raise SystemExit("--size-in must be between 1 and 6.5 inches to fit a letter page")

    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    pages = []
    for marker_id in args.ids:
        page = render_page(dictionary, marker_id, args.size_in)
        pages.append(page)
        path = out / f"aruco_{marker_id}.png"
        cv2.imwrite(str(path), page, [cv2.IMWRITE_PNG_COMPRESSION, 9])
        print("wrote", path)
    pdf = out / "aruco_markers.pdf"
    write_pdf(pages, pdf)
    print("wrote", pdf, f"({len(pages)} pages). Print at Actual size (100%).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
