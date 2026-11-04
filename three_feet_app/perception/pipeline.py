"""Local mask tracking with scene-triggered and periodic Meta model verification.

SAM initializes masks; local optical flow carries them between model checks. New
scene content or uncertain tracking requests an early check. Model verification
and camera/tracking freshness have separate deadlines, and failures stay unhealthy.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

import cv2
import numpy as np

from ..sources import Frame
from .local_tracking import LocalMaskTracker
from .meta_client import CostMeter
from .sam import SamClient
from .spark import ALWAYS_ON_CONCEPTS, SparkClient
from .tracking import PerceivedObject, Tracker, dedupe_detections, is_person_label


class MetaPerception:
    def __init__(self, sam: SamClient, spark: SparkClient, meter: CostMeter,
                 sam_period_s: float = 1.0, inventory_period_s: float = 60.0, idle_refresh_s: float = 30.0,
                 max_result_age_s: float = 5.0, clock: Callable[[], float] = time.time,
                 verification_max_age_s: float = 45.0) -> None:
        self.sam, self.spark, self.meter = sam, spark, meter
        self.sam_period_s = sam_period_s
        self.inventory_period_s = inventory_period_s
        self.idle_refresh_s = idle_refresh_s
        self.max_result_age_s = max_result_age_s
        self.verification_max_age_s = verification_max_age_s
        self.clock = clock
        self.last_success_at: float | None = None
        self.last_frame_ts: float | None = None
        self.last_frame_index: int | None = None
        self._segmentation_error: str | None = None
        self.tracker = Tracker()
        self._local = LocalMaskTracker()
        self._last_verification_ts: float | None = None
        self._scene_changed = False
        self.local_tracking_active = False
        self.concepts: list[str] = list(ALWAYS_ON_CONCEPTS)
        self.fault_api = False  # demo fault injection
        self.last_sam_latency_s: float | None = None
        self.last_error: str | None = None
        self._lock = threading.Lock()
        self._busy = False
        self._generation = 0
        self._last_cycle = float("-inf")
        self._last_inventory = float("-inf")
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
    def verification_age_s(self) -> float | None:
        with self._lock:
            stamp = self._last_verification_ts
        return None if stamp is None else max(0.0, self.clock() - stamp)

    @property
    def health_reason(self) -> str | None:
        if self.fault_api:
            return "API outage simulated"
        if self.meter.over_budget():
            return "API budget exhausted; model monitoring paused"
        with self._lock:
            stamp, error = self.last_frame_ts, self._segmentation_error
            verified, changed = self._last_verification_ts, self._scene_changed
        if error:
            return error
        if stamp is None:
            return "Waiting for the first successful object detection"
        if not 0 <= self.clock() - stamp <= self.max_result_age_s:
            return "Object detections are stale; waiting for a fresh result"
        if verified is None or not 0 <= self.clock() - verified <= self.verification_max_age_s:
            return "Model verification is stale; waiting for a fresh result"
        if changed:
            return "Scene changed or local tracking uncertain; waiting for model verification"
        return None

    def on_frame(self, frame: Frame, t: float, motion: bool, zone_view: np.ndarray | None = None) -> None:
        """Track locally even during API work; queue model work only when needed.

        The engine's motion flag includes a hold period. Raw motion alone must not
        restart six API calls when local masks already explain the movement.
        """
        if self.fault_api:
            return
        with self._lock:
            # Duplicate, out-of-order, or old input cannot refresh tracking health.
            if (not 0 <= self.clock() - frame.ts <= self.max_result_age_s
                    or (self.last_frame_ts is not None and frame.ts < self.last_frame_ts)):
                return
            if self.last_frame_index == frame.index:
                return
            if self._last_verification_ts is not None:
                gap = self.last_frame_ts is None or frame.ts - self.last_frame_ts > self.max_result_age_s
                changed = self._local.update(frame.image, self.tracker)
                self._scene_changed = self._scene_changed or gap or changed
                if not self._scene_changed and self._segmentation_error is None:
                    self.last_frame_ts, self.last_frame_index = frame.ts, frame.index
                    self.local_tracking_active = True
            if self._busy:
                return
            # Keep cycling at full rate until every track is confirmed or aged out; otherwise an object that was
            # just removed lingers in the tracker for several idle refreshes and keeps blocking the reset.
            settling = any(o.misses > 0 for o in self.tracker.objects())
            needs_check = (self._last_verification_ts is None or self._scene_changed or settling
                           or self._segmentation_error is not None)
            if not needs_check and t - self._last_cycle < self.idle_refresh_s:
                return
            period = self.sam_period_s
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
            self._local = LocalMaskTracker()
            self._last_verification_ts = None
            self._scene_changed = False
            self.local_tracking_active = False
            self.concepts = list(ALWAYS_ON_CONCEPTS)
            self.last_success_at = self.last_frame_ts = self.last_frame_index = None
            self._segmentation_error = None
            self.last_error = None
            self._last_cycle = float("-inf")
            self._last_inventory = float("-inf")
            self._inventory_busy = False
            self._busy = False

    def request_check(self) -> None:
        """An unexplained zone change needs a fresh inventory and SAM response."""
        with self._lock:
            self._scene_changed = True
            self._last_inventory = float("-inf")

    def current(self) -> list[PerceivedObject]:
        with self._lock:
            return self.tracker.objects()

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
        self._assess_pool.shutdown(wait=False, cancel_futures=True)
        self._inventory_pool.shutdown(wait=False, cancel_futures=True)

    def _refresh_inventory(self, view: np.ndarray, generation: int) -> None:
        try:
            concepts = self.spark.inventory(view)
            with self._lock:
                if generation == self._generation:
                    self.concepts = concepts
        except Exception as exc:
            with self._lock:
                if generation == self._generation:
                    self.last_error = f"inventory: {type(exc).__name__}: {exc}"
        finally:
            with self._lock:
                if generation == self._generation:
                    self._inventory_busy = False

    def _cycle(self, image: np.ndarray, t: float, zone_view: np.ndarray | None,
               frame_ts: float, frame_index: int, generation: int = 0) -> None:
        try:
            # The concept list refreshes in the background so a slow LLM call never delays segmentation.
            with self._lock:
                if generation != self._generation:
                    return
                due = not self._inventory_busy and (
                    t - self._last_inventory >= self.inventory_period_s
                    or self._scene_changed and t - self._last_inventory >= 5
                )
                if due:
                    self._inventory_busy = True
                    self._last_inventory = t
                concepts = list(self.concepts)
                if "person" not in concepts:
                    concepts.insert(0, "person")
            if due:
                self._inventory_pool.submit(self._refresh_inventory, zone_view if zone_view is not None else image,
                                            generation)
            results = self.sam.segment_many(image, concepts)
            self.last_sam_latency_s = max(r.latency_s for r in results) if results else None
            raw = [d for r in results for d in r.detections]
            # A clothing box must not replace the person detection during generic IoU deduplication.
            people = dedupe_detections([d for d in raw if is_person_label(d.concept)])
            detections = [*people, *dedupe_detections([d for d in raw if not is_person_label(d.concept)])]
            person_mask = np.zeros(image.shape[:2], bool)
            for person in people:
                person_mask |= person.mask
            with self._lock:
                if generation != self._generation:
                    return
                created = self.tracker.update(detections)
                for track in self.tracker.objects():
                    area = int(track.mask.sum())
                    track.extra["on_person"] = bool(area and not is_person_label(track.label)
                        and int((track.mask & person_mask).sum()) / area >= .8)
                self.last_success_at = self.clock()
                self.last_frame_ts = frame_ts
                self.last_frame_index = frame_index
                self._last_verification_ts = frame_ts
                self._local.seed(image, self.tracker)
                self._scene_changed = False
                self.local_tracking_active = False
                self._segmentation_error = None
                assessments = [(track, track.mask.copy()) for track in created
                               if not is_person_label(track.label) and not track.extra.get("on_person")]
                for track in self.tracker.objects():
                    if is_person_label(track.label) or track.extra.get("on_person"):
                        track.assessing = False
                        track.why = "Person or clothing being worn; not treated as an object left in the zone."
            for track, mask in assessments:
                self._assess_pool.submit(self._assess, track, image, mask)
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

    def _assess(self, track: PerceivedObject, image: np.ndarray, mask: np.ndarray) -> None:
        try:
            # Local tracking may already have moved the live mask; crop the frame
            # using the mask captured when this SAM result was accepted.
            ys, xs = np.nonzero(mask)
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
