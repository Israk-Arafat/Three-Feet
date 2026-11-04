"""Frame sources: phone MJPEG stream, webcam/video via OpenCV, or a folder of photos.

All sources return the *latest* frame so the safety loop never works on a stale backlog.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Protocol

import cv2
import httpx
import numpy as np

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
_SOI = b"\xff\xd8"
_EOI = b"\xff\xd9"


@dataclass(frozen=True)
class Frame:
    image: np.ndarray  # BGR
    ts: float  # time.time() when received
    index: int  # increments for every new frame
    name: str = ""  # file name for folder sources


class FrameSource(Protocol):
    def read(self) -> Frame | None: ...

    def close(self) -> None: ...

    @property
    def healthy(self) -> bool: ...


def split_jpegs(buffer: bytearray) -> list[bytes]:
    """Remove and return every complete JPEG in `buffer`; keep any partial tail.

    MJPEG frames are delimited by SOI/EOI markers regardless of the multipart boundary,
    and 0xFFD9 cannot appear inside entropy-coded data because 0xFF bytes are stuffed.
    """
    frames: list[bytes] = []
    while True:
        start = buffer.find(_SOI)
        if start < 0:
            buffer.clear()
            return frames
        end = buffer.find(_EOI, start + 2)
        if end < 0:
            if start > 0:
                del buffer[:start]
            return frames
        frames.append(bytes(buffer[start : end + 2]))
        del buffer[: end + 2]


class MjpegHttpSource:
    """Reads an MJPEG stream (e.g. Android IP Camera `/video/mjpeg`) on a background thread."""

    def __init__(
        self,
        url: str,
        user: str = "",
        password: str = "",
        verify_tls: bool = False,
        stale_after_s: float = 3.0,
    ) -> None:
        self.url = url
        self._auth = (user, password) if user else None
        self._verify = verify_tls
        self._stale_after_s = stale_after_s
        self._lock = threading.Lock()
        self._latest: Frame | None = None
        self._index = 0
        self._stop = threading.Event()
        self.last_error: str | None = None
        self._thread = threading.Thread(target=self._run, name="mjpeg-reader", daemon=True)
        self._thread.start()

    @property
    def auth(self) -> tuple[str, str] | None:
        return self._auth

    @property
    def verify_tls(self) -> bool:
        return self._verify

    @property
    def healthy(self) -> bool:
        with self._lock:
            latest = self._latest
        return latest is not None and time.time() - latest.ts < self._stale_after_s

    def read(self) -> Frame | None:
        with self._lock:
            return self._latest

    def wait_first(self, timeout_s: float = 10.0) -> Frame | None:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            frame = self.read()
            if frame is not None:
                return frame
            time.sleep(0.05)
        return None

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)

    def _run(self) -> None:
        backoff = 0.5
        while not self._stop.is_set():
            try:
                with httpx.Client(auth=self._auth, verify=self._verify, timeout=10.0) as client:
                    with client.stream("GET", self.url) as response:
                        response.raise_for_status()
                        self.last_error = None
                        backoff = 0.5
                        buffer = bytearray()
                        for chunk in response.iter_bytes():
                            if self._stop.is_set():
                                return
                            buffer.extend(chunk)
                            jpegs = split_jpegs(buffer)
                            if jpegs:
                                self._publish(jpegs[-1])
            except Exception as exc:  # network errors must never kill the reader
                self.last_error = f"{type(exc).__name__}: {exc}"
            self._stop.wait(backoff)
            backoff = min(backoff * 2, 5.0)

    def _publish(self, jpeg: bytes) -> None:
        image = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            return
        with self._lock:
            self._index += 1
            self._latest = Frame(image=image, ts=time.time(), index=self._index)


class OpenCVSource:
    """Webcam index (e.g. DroidCam over USB), video file, or any URL OpenCV can open."""

    def __init__(self, target: int | str) -> None:
        self.target = target
        self._cap = cv2.VideoCapture(target)
        self._index = 0
        self._last_ok = 0.0

    @property
    def healthy(self) -> bool:
        return self._cap.isOpened() and time.time() - self._last_ok < 3.0

    def read(self) -> Frame | None:
        ok, image = self._cap.read()
        if not ok or image is None:
            return None
        self._index += 1
        self._last_ok = time.time()
        return Frame(image=image, ts=self._last_ok, index=self._index)

    def close(self) -> None:
        self._cap.release()


class NoCameraSource:
    """Placeholder until a camera is added in the dashboard: never has a picture, so monitoring reports it as lost."""

    healthy = False

    def read(self) -> Frame | None:
        return None

    def close(self) -> None:
        pass


class FolderSource:
    """Iterates the images in a folder in name order. Useful before the camera is mounted."""

    def __init__(self, folder: str | Path, loop: bool = False, hold_s: float = 0.0) -> None:
        self.paths = sorted(p for p in Path(folder).iterdir() if p.suffix.lower() in IMAGE_EXTS)
        if not self.paths:
            raise FileNotFoundError(f"No images in {folder}")
        self._loop = loop
        self._hold_s = hold_s
        self._held: Frame | None = None
        self._i = 0
        self._index = 0

    @property
    def healthy(self) -> bool:
        return True

    def read(self) -> Frame | None:
        """Next image; with `hold_s` > 0, the same frame is repeated until it has been shown that long."""
        if self._held is not None and time.time() - self._held.ts < self._hold_s:
            return self._held
        frame = self._next()
        if self._hold_s > 0:
            self._held = frame
        return frame

    def _next(self) -> Frame | None:
        while True:
            if self._i >= len(self.paths):
                if not self._loop:
                    return None
                self._i = 0
            path = self.paths[self._i]
            self._i += 1
            image = cv2.imread(str(path), cv2.IMREAD_COLOR)
            if image is not None:
                self._index += 1
                return Frame(image=image, ts=time.time(), index=self._index, name=path.name)

    def __iter__(self) -> Iterator[Frame]:
        while (frame := self.read()) is not None:
            yield frame

    def close(self) -> None:
        pass


def open_source(spec: str) -> FrameSource:
    """`phone` (uses CAMERA_* settings), an http(s) MJPEG URL, a webcam index, a folder, or a video file."""
    from .config import get_settings

    settings = get_settings()
    if spec == "phone":
        if not settings.camera_url:
            raise ValueError("Set CAMERA_URL in .env (e.g. https://PHONE_IP:4444/video/mjpeg)")
        return MjpegHttpSource(
            settings.camera_url,
            settings.camera_user,
            settings.camera_password,
            settings.camera_verify_tls,
        )
    if spec.startswith(("http://", "https://")):
        return MjpegHttpSource(
            spec, settings.camera_user, settings.camera_password, settings.camera_verify_tls
        )
    if spec.isdigit():
        return OpenCVSource(int(spec))
    path = Path(spec)
    if path.is_dir():
        return FolderSource(path)
    if path.is_file():
        return OpenCVSource(str(path))
    raise ValueError(f"Unknown source: {spec}")
