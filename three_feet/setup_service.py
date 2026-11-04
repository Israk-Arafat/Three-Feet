"""Browser heater setup: frozen images, bounded model jobs, and live geometry updates."""

from __future__ import annotations

import json
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np
from pydantic import BaseModel, Field, FiniteFloat

from .calibration import calibrate_from_heater, heater_floor_corners
from .geometry import FloorCalibration
from .config import CALIBRATION_PATH, DATA_DIR, RUNS_DIR
from .perception.setup import CORNER_NAMES, indicator_roi, locate_heater_base, validate_base_candidate

PREFERENCES_PATH = DATA_DIR / "heater_setup.json"
MAX_JOB_SECONDS = 120.0


class DimensionsBody(BaseModel):
    width_in: FiniteFloat = Field(gt=0, le=120)
    depth_in: FiniteFloat = Field(gt=0, le=120)


class ManualSetupBody(DimensionsBody):
    job_id: str = Field(min_length=1, max_length=64)
    corners_px: list[tuple[FiniteFloat, FiniteFloat]] = Field(min_length=4, max_length=4)


class SetupError(ValueError):
    def __init__(self, message: str, status_code: int = 409) -> None:
        super().__init__(message)
        self.status_code = status_code


class SetupService:
    def __init__(self, engine, *, preferences_path: Path | None = None,
                 calibration_path: Path | None = None, artifacts_dir: Path | None = None) -> None:
        self.engine = engine
        self.preferences_path = preferences_path or PREFERENCES_PATH
        self.calibration_path = calibration_path or CALIBRATION_PATH
        self.artifacts_dir = artifacts_dir or RUNS_DIR / "setup"
        self._lock = threading.RLock()
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="heater-setup")
        self._future: Future | None = None
        self._closed = False
        self._started = 0.0
        self._image: np.ndarray | None = None
        self._directory: Path | None = None
        self._candidate: dict | None = None
        heater = engine.calib.heater if engine.calib else None
        width, depth = (heater.width_in, heater.depth_in) if heater else (4.5, 4.35)
        try:
            saved = DimensionsBody.model_validate_json(self.preferences_path.read_text(encoding="utf-8"))
            width, depth = saved.width_in, saved.depth_in
        except (OSError, ValueError):
            pass
        self._state = {
            "width_in": width, "depth_in": depth,
            "status": "ready" if engine.calib and engine.cmap else "idle",
            "message": "Heater base is calibrated." if engine.calib and engine.cmap
                       else "Enter the base measurements, then find the base in the camera view.",
            "job_id": None, "image_url": None, "overlay_url": None,
            "corners_px": None, "image_size": None, "quality": None,
        }

    def close(self) -> None:
        with self._lock:
            self._closed = True
        self._pool.shutdown(wait=False, cancel_futures=True)

    def state(self) -> dict:
        with self._lock:
            if self._state["status"] == "running" and time.monotonic() - self._started > MAX_JOB_SECONDS:
                self._state.update(status="error", message="Finding the base took too long. Try again in a moment.")
            result = dict(self._state)
            result["calibrated"] = bool(self.engine.calib and self.engine.cmap)
            result["method"] = self.engine.calib.method if self.engine.calib else None
            return result

    def _idle(self) -> None:
        if self._closed:
            raise SetupError("Setup is shutting down.")
        if self._future is not None and not self._future.done():
            raise SetupError("The base is already being analyzed. Wait for this attempt to finish.")

    def _save_dimensions(self, width: float, depth: float) -> None:
        body = DimensionsBody(width_in=width, depth_in=depth)
        self.preferences_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.preferences_path.with_suffix(".tmp")
        temporary.write_text(body.model_dump_json(indent=2), encoding="utf-8")
        temporary.replace(self.preferences_path)
        self._state.update(width_in=body.width_in, depth_in=body.depth_in)

    def dimensions(self, width: float, depth: float) -> dict:
        with self._lock:
            self._idle()
            self._save_dimensions(width, depth)
            self._state["message"] = "Measurements saved. Find the base to apply them to the camera view."
            return self.state()

    def _fresh_image(self) -> np.ndarray:
        engine = self.engine
        obs = engine._last_observation
        if (engine.latest_raw is None or not engine.source.healthy or engine.fault_camera
                or obs is None or not obs.camera_ok or not 0 <= engine.clock() - obs.t <= 1.5):
            raise SetupError("A fresh camera view is needed. Reconnect the camera and try again.")
        return engine.latest_raw.copy()

    def _new_capture(self, width: float, depth: float) -> str:
        self._idle()
        image = self._fresh_image()
        self._save_dimensions(width, depth)
        job_id = uuid.uuid4().hex
        directory = self.artifacts_dir / job_id
        directory.mkdir(parents=True, exist_ok=False)
        cv2.imwrite(str(directory / "snapshot.jpg"), image)
        self._image, self._directory, self._candidate = image, directory, None
        self._state.update(
            status="needs_review", message="Select the four base corners where the heater meets the floor.",
            job_id=job_id, image_url=f"/api/setup/image?job_id={job_id}", overlay_url=None,
            corners_px=None, image_size=[image.shape[1], image.shape[0]], quality=None,
        )
        return job_id

    def capture(self, width: float, depth: float) -> dict:
        with self._lock:
            self._new_capture(width, depth)
            return self.state()

    def auto(self, width: float, depth: float) -> dict:
        with self._lock:
            if self.engine.meter.over_budget():
                raise SetupError("The hourly model budget has been reached. Choose the corners yourself or try later.", 429)
            job_id = self._new_capture(width, depth)
            self._state.update(status="running", message="Meta is finding the base and checking its floor-contact corners.")
            self._started = time.monotonic()
            self._future = self._pool.submit(self._analyze, job_id, self._image.copy(), width, depth)
            return self.state()

    def image_path(self, job_id: str | None, overlay: bool = False) -> Path:
        with self._lock:
            if self._directory is None or (job_id is not None and job_id != self._state["job_id"]):
                raise SetupError("This setup photo has expired. Capture a new camera view.", 404)
            path = self._directory / ("candidate_overlay.jpg" if overlay else "snapshot.jpg")
            if not path.exists():
                raise SetupError("The base preview is not ready yet.", 404)
            return path

    @staticmethod
    def _points(candidate: dict | None) -> list[list[float]] | None:
        if not candidate:
            return None
        try:
            points = np.array([[candidate["corners"][name]["x"], candidate["corners"][name]["y"]]
                               for name in CORNER_NAMES], dtype=float)
            return points.tolist() if points.shape == (4, 2) and np.isfinite(points).all() else None
        except (KeyError, TypeError, ValueError):
            return None

    def _geometry(self, image: np.ndarray, corners, width: float, depth: float, *, allow_uncertain: bool = False):
        # Human picks establish visibility; numerical checks still apply to their four corners.
        candidate = {
            "heater_found": True, "base_on_floor": True, "front_identified": True,
            "selected_surface": "floor_contact_base", "confidence": 1.0,
            "reason": "The four floor-contact base corners were selected in the setup image.",
            "corners": {name: {"x": xy[0], "y": xy[1], "visible": True,
                               "confidence": 1.0, "evidence": "User-selected floor-contact corner."}
                        for name, xy in zip(CORNER_NAMES, corners)},
        }
        size = (image.shape[1], image.shape[0])
        points = validate_base_candidate(candidate, size, min_area=4, min_edge=1)
        cal = calibrate_from_heater(size, points, width, depth, samples=0)
        if not np.isfinite(cal.floor.H).all() or not np.isfinite(cal.floor.rms_in) or not np.isfinite(cal.distance_err_frac):
            raise ValueError("Those points do not define usable floor geometry. Select the base corners again.")
        self._state["quality"] = {**(self._state.get("quality") or {}),
                                  "error_pct": round(cal.distance_err_frac * 100, 1)}
        reprojected = cal.floor.floor_to_image(heater_floor_corners(width, depth))
        error = np.max(np.linalg.norm(reprojected - points, axis=1))
        if not np.isfinite(error) or error > 3:
            if not allow_uncertain:
                raise ValueError("Those points do not fit the measured base rectangle. Check the measurements and corner order.")
            # Preserve a person's four-point selection as an explicitly untrusted preview.
            # A free homography fits the selected corners but is not a validated floor scale.
            cal.floor = FloorCalibration.fit(points, heater_floor_corners(width, depth), size)
            if not np.isfinite(cal.floor.H).all():
                raise ValueError("Those points do not define usable floor geometry. Select the base corners again.")
            cal.distance_err_frac = max(cal.distance_err_frac, 0.3)
            self._state["quality"]["error_pct"] = round(cal.distance_err_frac * 100, 1)
        return cal

    def _same_view(self, image: np.ndarray, corners) -> None:
        latest = self._fresh_image()
        if latest.shape != image.shape:
            raise SetupError("The camera resolution changed. Capture a new setup photo.")
        before = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        after = cv2.cvtColor(latest, cv2.COLOR_BGR2GRAY)
        features = cv2.goodFeaturesToTrack(before, 250, 0.03, 12)
        if features is not None and len(features) >= 12:
            moved, status, _ = cv2.calcOpticalFlowPyrLK(before, after, features, None)
            if moved is not None and status is not None:
                good = status.ravel().astype(bool)
                if good.sum() >= 12:
                    motion = np.linalg.norm(moved[good] - features[good], axis=2).ravel()
                    if float(np.median(motion)) > 4:
                        raise SetupError("The camera moved after this photo. Capture a new setup view.")
        points = np.asarray(corners, float)
        x0, y0 = np.floor(points.min(axis=0) - 12).astype(int)
        x1, y1 = np.ceil(points.max(axis=0) + 12).astype(int)
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(before.shape[1], x1), min(before.shape[0], y1)
        if x1 > x0 and y1 > y0:
            difference = cv2.absdiff(before[y0:y1, x0:x1], after[y0:y1, x0:x1])
            if float(difference.mean()) > 35:
                raise SetupError("The heater or lighting changed after this photo. Capture a new setup view.")

    def _save_calibration(self, cal, width: float, depth: float) -> None:
        if self.calibration_path.exists():
            (self._directory / "previous_calibration.json").write_bytes(self.calibration_path.read_bytes())
        temporary = self.calibration_path.with_suffix(".tmp")
        cal.save(temporary)
        self.engine.set_calibration(cal)
        temporary.replace(self.calibration_path)
        self._save_dimensions(width, depth)
        self._state.update(status="ready", message="Heater base saved. The danger zone now uses your measurements.")

    def _write_overlay(self, image: np.ndarray, corners, accepted: bool) -> None:
        overlay = image.copy()
        points = np.asarray(corners, dtype=float) if corners is not None else np.empty((0, 2))
        color = (70, 180, 80) if accepted else (0, 185, 245)
        if points.shape == (4, 2) and np.isfinite(points).all():
            pts = np.rint(points).astype(np.int32)
            cv2.polylines(overlay, [pts], True, color, 2, cv2.LINE_AA)
            for label, xy in zip(("FL", "FR", "BR", "BL"), pts):
                cv2.circle(overlay, tuple(xy), 5, color, -1, cv2.LINE_AA)
                cv2.putText(overlay, label, tuple(xy + [7, -5]), cv2.FONT_HERSHEY_SIMPLEX,
                            .5, color, 2, cv2.LINE_AA)
        cv2.imwrite(str(self._directory / "candidate_overlay.jpg"), overlay)
        self._state["overlay_url"] = f"/api/setup/overlay?job_id={self._state['job_id']}"
        diagnostic = {**self.state(), "candidate": self._candidate}
        (self._directory / "result.json").write_text(json.dumps(diagnostic, indent=2, default=str), encoding="utf-8")

    def _analyze(self, job_id: str, image: np.ndarray, width: float, depth: float) -> None:
        candidate, cal = None, None
        failure = None
        try:
            candidate = locate_heater_base(image, width, depth, meter=self.engine.meter, refine=False)
            corners = self._points(candidate)
            if not candidate.get("heater_found") or corners is None:
                raise ValueError("The heater base could not be located in this image.")
            with self._lock:
                if not self._current(job_id):
                    return
                cal = self._geometry(image, corners, width, depth, allow_uncertain=True)
        except Exception as exc:
            failure = exc
        with self._lock:
            if not self._current(job_id):
                return
            self._candidate = candidate
            corners = self._points(candidate)
            self._state["corners_px"] = corners
            confidence = candidate.get("confidence") if candidate else None
            if isinstance(confidence, (int, float)) and np.isfinite(confidence):
                self._state["quality"] = {**(self._state.get("quality") or {}), "confidence": confidence}
            if failure is None:
                try:
                    self._same_view(image, corners)
                    cal.indicator_roi = indicator_roi(candidate, (image.shape[1], image.shape[0]))
                    cal.indicator_threshold = None
                    self._save_calibration(cal, width, depth)
                except Exception as exc:
                    failure = exc
            if failure is not None:
                if candidate:
                    reason = candidate.get("reason", "")
                    self._state.update(status="needs_review", message=f"{failure} {reason}".strip())
                else:
                    self._state.update(status="error", message="Meta could not analyze this view. Try again, or select the base corners yourself.")
                    self.engine.log("setup", f"Automatic base setup failed: {type(failure).__name__}")
            self._write_overlay(image, corners, failure is None)

    def _current(self, job_id: str) -> bool:
        return (not self._closed and self._state["job_id"] == job_id
                and self._state["status"] == "running"
                and time.monotonic() - self._started <= MAX_JOB_SECONDS)

    def manual(self, job_id: str, corners, width: float, depth: float) -> dict:
        with self._lock:
            self._idle()
            if job_id != self._state["job_id"] or self._image is None:
                raise SetupError("This setup photo has expired. Capture a new view before selecting corners.")
            try:
                self._same_view(self._image, corners)
                cal = self._geometry(self._image, corners, width, depth, allow_uncertain=True)
                cal.indicator_roi = None
                cal.indicator_threshold = None
                self._save_calibration(cal, width, depth)
            except (ValueError, cv2.error) as exc:
                if isinstance(exc, SetupError):
                    raise
                raise SetupError(str(exc), 422) from exc
            self._state["corners_px"] = [list(xy) for xy in corners]
            self._candidate = None
            self._write_overlay(self._image, corners, True)
            return self.state()
