"""Live perception with Meta models, run off the safety loop's thread.

Muse Spark proposes concepts (every few seconds) -> SAM 3.1 segments each concept in parallel (<= ~1 Hz)
-> IoU tracker keeps identities -> Muse Spark judges each new track's hazard (async; "high" until judged).
API calls only happen while the motion gate is active, plus a slow idle refresh, and stop at the budget cap.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

import cv2
import numpy as np

from ..sources import Frame
from .meta_client import CostMeter
from .sam import SamClient
from .spark import ALWAYS_ON_CONCEPTS, SparkClient
from .tracking import PerceivedObject, Tracker, dedupe_detections


class MetaPerception:
    def __init__(self, sam: SamClient, spark: SparkClient, meter: CostMeter,
                 sam_period_s: float = 1.0, inventory_period_s: float = 5.0, idle_refresh_s: float = 2.0,
                 max_result_age_s: float = 5.0, clock: Callable[[], float] = time.time) -> None:
        self.sam, self.spark, self.meter = sam, spark, meter
        self.sam_period_s = sam_period_s
        self.inventory_period_s = inventory_period_s
        self.idle_refresh_s = idle_refresh_s
        self.max_result_age_s = max_result_age_s
        self.clock = clock
        self.last_success_at: float | None = None
        self.last_frame_ts: float | None = None
        self.last_frame_index: int | None = None
        self._segmentation_error: str | None = None
        self.tracker = Tracker()
        self.concepts: list[str] = list(ALWAYS_ON_CONCEPTS)
        self.fault_api = False  # demo fault injection
        self.last_sam_latency_s: float | None = None
        self.last_error: str | None = None
        self._lock = threading.Lock()
        self._busy = False
        self._generation = 0
        self._last_cycle = 0.0
        self._last_inventory = 0.0
        self._inventory_busy = False
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="perception")
        self._inventory_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="inventory")
        self._assess_pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="hazard")

    @property
    def api_ok(self) -> bool:
        return self.health_reason is None

    @property
    def result_age_s(self) -> float | None:
        with self._lock:
            stamp = self.last_frame_ts
        return None if stamp is None else max(0.0, self.clock() - stamp)

    @property
    def health_reason(self) -> str | None:
        if self.fault_api:
            return "API outage simulated"
        if self.meter.over_budget():
            return "API budget exhausted; model monitoring paused"
        with self._lock:
            stamp, error = self.last_frame_ts, self._segmentation_error
        if error:
            return error
        if stamp is None:
            return "Waiting for the first successful object detection"
        if not 0 <= self.clock() - stamp <= self.max_result_age_s:
            return "Object detections are stale; waiting for a fresh result"
        return None

    def on_frame(self, frame: Frame, t: float, motion: bool, zone_view: np.ndarray | None = None) -> None:
        """Non-blocking: maybe start a perception cycle on this frame."""
        if self.fault_api:
            return
        with self._lock:
            if self._busy:
                return
            # Keep cycling at full rate until every track is confirmed or aged out; otherwise an object that was
            # just removed lingers in the tracker for several idle refreshes and keeps blocking the reset.
            settling = any(o.misses > 0 for o in self.tracker.objects())
            period = self.sam_period_s if (motion or settling) else min(self.idle_refresh_s, self.max_result_age_s / 2)
            if t - self._last_cycle < period or self.meter.over_budget():
                return
            self._busy = True
            self._last_cycle = t
            generation = self._generation
        self._pool.submit(self._cycle, frame.image.copy(), t, zone_view, frame.ts, frame.index, generation)

    def reset(self) -> None:
        """Forget every detection, e.g. after the camera changed. Results of cycles already running are dropped."""
        with self._lock:
            self._generation += 1
            self.tracker = Tracker()
            self.last_success_at = self.last_frame_ts = self.last_frame_index = None
            self._segmentation_error = None
            self.last_error = None
            self._last_cycle = 0.0
            self._busy = False

    def current(self) -> list[PerceivedObject]:
        with self._lock:
            return self.tracker.objects()

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
        self._assess_pool.shutdown(wait=False, cancel_futures=True)
        self._inventory_pool.shutdown(wait=False, cancel_futures=True)

    def _refresh_inventory(self, view: np.ndarray) -> None:
        try:
            self.concepts = self.spark.inventory(view)
        except Exception as exc:
            self.last_error = f"inventory: {type(exc).__name__}: {exc}"
        finally:
            with self._lock:
                self._inventory_busy = False

    def _cycle(self, image: np.ndarray, t: float, zone_view: np.ndarray | None,
               frame_ts: float, frame_index: int, generation: int = 0) -> None:
        try:
            # The concept list refreshes in the background so a slow LLM call never delays segmentation.
            with self._lock:
                due = t - self._last_inventory >= self.inventory_period_s and not self._inventory_busy
                if due:
                    self._inventory_busy = True
                    self._last_inventory = t
            if due:
                self._inventory_pool.submit(self._refresh_inventory, zone_view if zone_view is not None else image)
            results = self.sam.segment_many(image, self.concepts)
            self.last_sam_latency_s = max(r.latency_s for r in results) if results else None
            detections = dedupe_detections([d for r in results for d in r.detections])
            with self._lock:
                if generation != self._generation:
                    return
                created = self.tracker.update(detections)
                self.last_success_at = self.clock()
                self.last_frame_ts = frame_ts
                self.last_frame_index = frame_index
                self._segmentation_error = None
            for track in created:
                self._assess_pool.submit(self._assess, track, image)
            self.last_error = None
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            with self._lock:
                if generation == self._generation:
                    self.last_error = error
                    self._segmentation_error = error
        finally:
            with self._lock:
                if generation == self._generation:
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
            self.last_error = f"hazard: {type(exc).__name__}: {exc}"
            track.why = "Could not assess — treated as high hazard"
        finally:
            track.assessing = False
