"""Simulated scene for building and demoing the dashboard without camera, plug, or API key.

Renders an oblique "overhead camera" view of a floor with real ArUco markers, a heater whose power light
and glow follow a DryRunPlug, and objects that move on a script. Ground-truth masks stand in for SAM, so
everything downstream (calibration, geometry, state machine, verification, UI) is the real code.
Everything produced here is labeled SIMULATED in the UI.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable

import cv2
import numpy as np

from .calibration import ARUCO_DICT_ID, CalibrationFile, calibrate_from_markers
from .geometry import FloorCalibration, HeaterFootprint
from .perception.tracking import PerceivedObject
from .safety.actuator import DryRunPlug
from .safety.verifier import IndicatorProbe
from .sources import Frame

PX_PER_IN = 8.0
FLOOR_W_IN, FLOOR_H_IN = 160.0, 90.0
IMG_W, IMG_H = 1280, 720
WARP_DST = np.float32([(150, 30), (1130, 30), (1280, 720), (0, 720)])
MARKERS_IN = {0: (6.0, 6.0), 1: (154.0, 6.0), 2: (154.0, 84.0), 3: (6.0, 84.0)}
MARKER_SIZE_IN = 5.0
HEATER_CORNERS_IN = ((73.0, 46.5), (87.0, 46.5), (87.0, 37.5), (73.0, 37.5))  # FL, FR, BR, BL
LED_IN = (80.0, 39.5)


@dataclass
class SimObject:
    key: str
    label: str
    size_in: tuple[float, float]
    color: tuple[int, int, int]
    hazard: str
    why: str
    home: tuple[float, float]
    spots: dict[str, tuple[float, float]]
    source: str = "sim"
    visible: bool = True
    pos: tuple[float, float] = (0.0, 0.0)
    target: tuple[float, float] = (0.0, 0.0)

    def __post_init__(self) -> None:
        self.pos = self.target = self.home

    def corners_in(self) -> np.ndarray:
        (x, y), (w, h) = self.pos, self.size_in
        return np.array([(x - w / 2, y - h / 2), (x + w / 2, y - h / 2), (x + w / 2, y + h / 2), (x - w / 2, y + h / 2)])


def default_objects() -> list[SimObject]:
    return [
        SimObject("blanket", "fleece blanket", (30, 18), (170, 100, 50), "high",
                  "Polyester fleece ignites and melts readily.", home=(22, 80),
                  spots={"near": (80, 67.5)}),
        SimObject("box", "cardboard box", (16, 12), (70, 125, 175), "high",
                  "Corrugated cardboard ignites easily.", home=(140, 80),
                  spots={"watch": (134, 42), "near": (112, 42)}),
        SimObject("shoe", "unknown object", (11, 4.5), (45, 45, 45), "high",
                  "Not recognized — treated as high hazard.", home=(60, 16),
                  spots={"near": (60, 16)}, source="catchall", visible=False),
    ]


class SimWorld:
    def __init__(self, plug: DryRunPlug, clock: Callable[[], float] = time.time, speed_in_s: float = 12.0) -> None:
        self.plug = plug
        self.clock = clock
        self.speed_in_s = speed_in_s
        self.objects = {o.key: o for o in default_objects()}
        self.camera_cut = False
        self.bumped = False
        self._last_t = clock()
        self._lock = threading.Lock()
        S = np.diag([PX_PER_IN, PX_PER_IN, 1.0])
        W = cv2.getPerspectiveTransform(
            np.float32([(0, 0), (IMG_W - 1, 0), (IMG_W - 1, IMG_H - 1), (0, IMG_H - 1)]), WARP_DST)
        self.floor_to_img = W @ S
        self.heater = HeaterFootprint.from_corners(*HEATER_CORNERS_IN)
        self._background = self._render_background()

    # -- scene control --------------------------------------------------------------
    def place(self, key: str, spot: str) -> None:
        with self._lock:
            obj = self.objects[key]
            if spot == "home":
                obj.target = obj.home
                if obj.source == "catchall":
                    obj.visible = False
            else:
                obj.target = obj.spots[spot]
                if not obj.visible:
                    obj.pos, obj.visible = obj.target, True

    def at_target(self, key: str) -> bool:
        o = self.objects[key]
        return np.hypot(o.pos[0] - o.target[0], o.pos[1] - o.target[1]) < 0.5

    def step(self) -> None:
        t = self.clock()
        dt = max(0.0, t - self._last_t)
        self._last_t = t
        with self._lock:
            for o in self.objects.values():
                d = np.subtract(o.target, o.pos)
                dist = float(np.hypot(*d))
                move = min(dist, self.speed_in_s * dt)
                if dist > 1e-6:
                    o.pos = tuple(np.add(o.pos, d / dist * move))  # type: ignore[assignment]

    @property
    def heater_on(self) -> bool:
        st = self.plug.status()
        return bool(st.power_w and st.power_w > 5)

    # -- rendering ------------------------------------------------------------------
    def to_img(self, pts_in) -> np.ndarray:
        pts = np.asarray(pts_in, dtype=np.float64).reshape(-1, 1, 2)
        return cv2.perspectiveTransform(pts, self.floor_to_img).reshape(-1, 2)

    def _poly(self, pts_in) -> np.ndarray:
        return self.to_img(pts_in).round().astype(np.int32)

    def _render_background(self) -> np.ndarray:
        floor = np.zeros((int(FLOOR_H_IN * PX_PER_IN), int(FLOOR_W_IN * PX_PER_IN), 3), np.uint8)
        floor[:] = (95, 135, 170)
        rng = np.random.default_rng(7)
        for y in range(0, floor.shape[0], int(6 * PX_PER_IN)):
            cv2.line(floor, (0, y), (floor.shape[1], y), (70, 105, 140), 2)
            for x in rng.integers(0, floor.shape[1], 3):
                cv2.line(floor, (int(x), y), (int(x), y + int(6 * PX_PER_IN)), (70, 105, 140), 2)
        noise = rng.normal(0, 6, floor.shape[:2])[..., None]
        floor = np.clip(floor + noise, 0, 255).astype(np.uint8)
        dictionary = cv2.aruco.getPredefinedDictionary(ARUCO_DICT_ID)
        side = int(MARKER_SIZE_IN * PX_PER_IN)
        for mid, (x, y) in MARKERS_IN.items():
            marker = cv2.cvtColor(cv2.aruco.generateImageMarker(dictionary, mid, side), cv2.COLOR_GRAY2BGR)
            x0, y0 = int(x * PX_PER_IN - side / 2), int(y * PX_PER_IN - side / 2)
            q = int(PX_PER_IN)
            floor[y0 - q : y0 + side + q, x0 - q : x0 + side + q] = 255
            floor[y0 : y0 + side, x0 : x0 + side] = marker
        S = np.diag([1 / PX_PER_IN, 1 / PX_PER_IN, 1.0])
        return cv2.warpPerspective(floor, self.floor_to_img @ S, (IMG_W, IMG_H),
                                   flags=cv2.INTER_LINEAR, borderValue=(60, 60, 66))

    def render(self) -> np.ndarray:
        self.step()
        img = self._background.copy()
        on = self.heater_on
        cv2.fillPoly(img, [self._poly(self.heater.corners())], (55, 55, 60), cv2.LINE_AA)
        for k in np.linspace(-0.3, 0.25, 6):
            a, b = (73 + 1.5, 42 + k * 9), (87 - 1.5, 42 + k * 9)
            cv2.line(img, *map(tuple, self._poly([a, b])), (80, 80, 85), 2, cv2.LINE_AA)
        glow = [(74, 44.5), (86, 44.5), (86, 46.2), (74, 46.2)]
        cv2.fillPoly(img, [self._poly(glow)], (40, 150, 255) if on else (45, 45, 50), cv2.LINE_AA)
        led = tuple(self._poly([LED_IN])[0])
        cv2.circle(img, led, 6, (40, 40, 255) if on else (35, 35, 35), -1, cv2.LINE_AA)
        with self._lock:
            for o in self.objects.values():
                if not o.visible:
                    continue
                poly = self._poly(o.corners_in())
                cv2.fillPoly(img, [poly], o.color, cv2.LINE_AA)
                cv2.polylines(img, [poly], True, tuple(int(c * 0.7) for c in o.color), 2, cv2.LINE_AA)
        if self.bumped:
            img = cv2.warpAffine(img, np.float32([[1, 0, 14], [0, 1, 8]]), (IMG_W, IMG_H), borderValue=(60, 60, 66))
        return img

    def masks(self) -> list[tuple[SimObject, np.ndarray]]:
        out = []
        with self._lock:
            for o in self.objects.values():
                if not o.visible:
                    continue
                mask = np.zeros((IMG_H, IMG_W), np.uint8)
                cv2.fillPoly(mask, [self._poly(o.corners_in())], 1)
                out.append((o, mask.astype(bool)))
        return out

    # -- setup helpers ----------------------------------------------------------------
    def calibration(self) -> CalibrationFile:
        image = self._background
        try:
            cal = calibrate_from_markers(image, MARKERS_IN)
        except ValueError:
            pts = list(MARKERS_IN.values())
            cal = CalibrationFile(floor=FloorCalibration.fit(self.to_img(pts), pts, (IMG_W, IMG_H)))
        cal.heater = self.heater
        x, y = self._poly([LED_IN])[0]
        cal.indicator_roi = (int(x) - 10, int(y) - 10, 20, 20)
        cal.indicator_threshold = 120.0
        return cal

    def indicator_probe(self, cal: CalibrationFile) -> IndicatorProbe:
        return IndicatorProbe(cal.indicator_roi, cal.indicator_threshold)  # type: ignore[arg-type]


class SimSource:
    def __init__(self, world: SimWorld) -> None:
        self.world = world
        self._index = 0
        self._last: Frame | None = None

    @property
    def healthy(self) -> bool:
        return not self.world.camera_cut

    def read(self) -> Frame | None:
        if self.world.camera_cut:
            return self._last
        self._index += 1
        self._last = Frame(self.world.render(), self.world.clock(), self._index, "sim")
        return self._last

    def close(self) -> None:
        pass


class SimPerception:
    """Ground-truth masks in place of SAM. With the API 'down', objects degrade to unknown/high."""

    concepts = ["fleece blanket", "cardboard box"]
    last_sam_latency_s = 0.8
    last_error = None

    def __init__(self, world: SimWorld) -> None:
        self.world = world
        self.fault_api = False

    @property
    def api_ok(self) -> bool:
        return not self.fault_api

    def on_frame(self, frame, t, motion, zone_view=None) -> None:
        pass

    def current(self) -> list[PerceivedObject]:
        out = []
        for o, mask in self.world.masks():
            if self.fault_api and o.source != "catchall":
                out.append(PerceivedObject(f"u-{o.key}", "unknown object", mask, "high",
                                           "Models unavailable — treated as high hazard.", "catchall"))
            else:
                out.append(PerceivedObject(o.key, o.label, mask, o.hazard, o.why, o.source, 0.9))
        return out

    def shutdown(self) -> None:
        pass


class Autoplay:
    """Loops a scripted demo: blanket shutoff, near miss, unknown object. Pausable from the dashboard."""

    def __init__(self, world: SimWorld, get_state: Callable[[], str], reset: Callable[[], bool],
                 log: Callable[[str], None]) -> None:
        self.world, self.get_state, self.reset, self.log = world, get_state, reset, log
        self.enabled = threading.Event()
        self.enabled.set()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="sim-autoplay", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.enabled.set()

    def _wait(self, seconds: float) -> None:
        end = time.time() + seconds
        while time.time() < end and not self._stop.is_set():
            self.enabled.wait()
            time.sleep(0.1)

    def _wait_for(self, cond: Callable[[], bool], timeout: float) -> None:
        end = time.time() + timeout
        while not cond() and time.time() < end and not self._stop.is_set():
            self.enabled.wait()
            time.sleep(0.1)

    def _human_reset(self) -> None:
        self._wait_for(lambda: self.reset(), 10)
        self.log("SIMULATED: a person reset the system and restored heater power")

    def _run(self) -> None:
        off = lambda: self.get_state().startswith("OFF_")  # noqa: E731
        while not self._stop.is_set():
            self._wait(4)
            self.world.place("blanket", "near")
            self._wait_for(off, 45)
            self._wait(5)
            self.world.place("blanket", "home")
            self._wait_for(lambda: self.world.at_target("blanket"), 15)
            self._wait(2)
            if off():
                self._human_reset()
            self._wait(4)
            self.world.place("box", "watch")
            self._wait(8)
            self.world.place("box", "home")
            self._wait(5)
            self.world.place("shoe", "near")
            self._wait_for(off, 45)
            self._wait(5)
            self.world.place("shoe", "home")
            self._wait(2)
            if off():
                self._human_reset()
            self._wait(6)
