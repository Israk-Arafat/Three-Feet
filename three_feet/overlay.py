"""Drawing helpers shared by the CLI tools and the dashboard stream."""

from __future__ import annotations

import cv2
import numpy as np

from .geometry import ClearanceMap, Measurement, Status

STATUS_BGR: dict[str, tuple[int, int, int]] = {
    "clear": (90, 200, 90),
    "watch": (0, 190, 255),
    "violation": (40, 40, 230),
}


def put_label(img: np.ndarray, text: str, org: tuple[int, int], color=(255, 255, 255), scale: float = 0.6) -> None:
    (tw, th), base = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, 2)
    x = min(max(org[0], 0), max(img.shape[1] - tw - 8, 0))
    y = max(org[1], th + 4)
    cv2.rectangle(img, (x, y - th - 6), (x + tw + 6, y + base), (0, 0, 0), -1)
    cv2.putText(img, text, (x + 3, y - 3), cv2.FONT_HERSHEY_SIMPLEX, scale, color, 2, cv2.LINE_AA)


def draw_zone(img: np.ndarray, cmap: ClearanceMap, watch_margin_in: float) -> None:
    cv2.drawContours(img, cmap.zone_contours(watch_margin_in), -1, STATUS_BGR["watch"], 1, cv2.LINE_AA)
    cv2.drawContours(img, cmap.zone_contours(0.0), -1, STATUS_BGR["violation"], 2, cv2.LINE_AA)
    cv2.polylines(img, [cmap.heater_polygon_px()], True, (255, 255, 255), 2, cv2.LINE_AA)


def draw_mask(img: np.ndarray, mask: np.ndarray, color, alpha: float = 0.45) -> None:
    tint = np.zeros_like(img)
    tint[mask] = color
    img[mask] = cv2.addWeighted(img, 1 - alpha, tint, alpha, 0)[mask]
    contours, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cv2.drawContours(img, contours, -1, color, 2, cv2.LINE_AA)


def draw_measured(img: np.ndarray, mask: np.ndarray, label: str, m: Measurement | None, status: Status) -> None:
    color = STATUS_BGR[status]
    draw_mask(img, mask, color)
    ys, xs = np.nonzero(mask)
    anchor = (int(xs.min()), int(ys.min()) - 4) if len(xs) else (10, 30)
    if m is None:
        put_label(img, f"{label}: off floor plane", anchor, color)
        return
    cv2.line(img, m.closest_px, m.heater_px, color, 2, cv2.LINE_AA)
    cv2.circle(img, m.closest_px, 5, color, -1, cv2.LINE_AA)
    put_label(img, f"{label} {m.distance_in:.1f} in +/-{m.err_in:.1f} ({m.side}, needs {m.required_in:.0f})",
              anchor, color, scale=0.75)
