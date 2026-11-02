"""Live perception with Meta models, run off the safety loop's thread.

Muse Spark proposes concepts (every few seconds) -> SAM 3.1 segments each concept in parallel (<= ~1 Hz)
-> IoU tracker keeps identities -> Muse Spark judges each new track's hazard (async; "high" until judged).
API calls only happen while the motion gate is active, plus a slow idle refresh, and stop at the budget cap.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import cv2
import numpy as np

from ..sources import Frame
from .meta_client import CostMeter
from .sam import SamClient
from .spark import ALWAYS_ON_CONCEPTS, SparkClient
from .tracking import PerceivedObject, Tracker, dedupe_detections


class MetaPerception:
    def __init__(self, sam: SamClient, spark: SparkClient, meter: CostMeter,
                 sam_period_s: float = 1.0, inventory_period_s: float = 5.0, idle_refresh_s: float = 30.0,
                 api_error_hold_s: float = 10.0) -> None:
        self.sam, self.spark, self.meter = sam, spark, meter
        self.sam_period_s = sam_period_s
        self.inventory_period_s = inventory_period_s
        self.idle_refresh_s = idle_refresh_s
        self.api_error_hold_s = api_error_hold_s
        self.tracker = Tracker()
        self.concepts: list[str] = list(ALWAYS_ON_CONCEPTS)
        self.fault_api = False  # demo fault injection
        self.last_sam_latency_s: float | None = None
        self.last_error: str | None = None
        self._lock = threading.Lock()
        self._busy = False
        self._last_cycle = 0.0
        self._last_inventory = 0.0
        self._last_error_t = -1e9
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="perception")
        self._assess_pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="hazard")

    @property
    def api_ok(self) -> bool:
        return not self.fault_api and time.time() - self._last_error_t > self.api_error_hold_s

    def on_frame(self, frame: Frame, t: float, motion: bool, zone_view: np.ndarray | None = None) -> None:
        """Non-blocking: maybe start a perception cycle on this frame."""
        if self.fault_api:
            return
        with self._lock:
            if self._busy:
                return
            period = self.sam_period_s if motion else self.idle_refresh_s
            if t - self._last_cycle < period or self.meter.over_budget():
                return
            self._busy = True
            self._last_cycle = t
        self._pool.submit(self._cycle, frame.image.copy(), t, zone_view)

    def current(self) -> list[PerceivedObject]:
        with self._lock:
            return self.tracker.objects()

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
        self._assess_pool.shutdown(wait=False, cancel_futures=True)

    def _cycle(self, image: np.ndarray, t: float, zone_view: np.ndarray | None) -> None:
        try:
            if t - self._last_inventory >= self.inventory_period_s:
                self.concepts = self.spark.inventory(zone_view if zone_view is not None else image)
                self._last_inventory = t
            results = self.sam.segment_many(image, self.concepts)
            self.last_sam_latency_s = max(r.latency_s for r in results) if results else None
            detections = dedupe_detections([d for r in results for d in r.detections])
            with self._lock:
                created = self.tracker.update(detections)
            for track in created:
                self._assess_pool.submit(self._assess, track, image)
            self.last_error = None
        except Exception as exc:
            self._last_error_t = time.time()
            self.last_error = f"{type(exc).__name__}: {exc}"
        finally:
            with self._lock:
                self._busy = False

    def _assess(self, track: PerceivedObject, image: np.ndarray) -> None:
        try:
            ys, xs = np.nonzero(track.mask)
            if len(xs) == 0:
                return
            pad = 20
            x0, x1 = max(xs.min() - pad, 0), min(xs.max() + pad, image.shape[1])
            y0, y1 = max(ys.min() - pad, 0), min(ys.max() + pad, image.shape[0])
            scene = cv2.resize(image, (image.shape[1] // 2, image.shape[0] // 2))
            assessment = self.spark.assess(image[y0:y1, x0:x1], scene, track.label)
            track.hazard, track.why = assessment.level, assessment.why
            track.extra["material"] = assessment.material_guess
        except Exception as exc:
            self._last_error_t = time.time()
            self.last_error = f"hazard: {type(exc).__name__}: {exc}"
            track.why = "Could not assess — treated as high hazard"
        finally:
            track.assessing = False
