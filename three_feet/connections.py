"""Camera and plug choices made in the dashboard: checked, applied to the running engine, and remembered.

Saved choices live in data/connections.json (git-ignored, includes any camera password). The command line wins
when it names a camera or plug explicitly; otherwise the saved choice is used, then `.env`.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .config import CONNECTIONS_PATH, CYNC_TOKEN_PATH
from .engine import ConnectionChangeBlocked, Engine
from .safety.actuator import DryRunPlug, make_switch
from .sources import FrameSource, MjpegHttpSource, NoCameraSource, OpenCVSource

PLUG_KINDS = ("dryrun", "cync")
CAMERA_HELP = "Enter the camera's stream address, like https://192.168.0.63:4444/video/mjpeg, or a webcam number such as 0."
MAX_WEBCAM_INDEX = 20


@dataclass(frozen=True)
class CameraConfig:
    address: str
    user: str = ""
    password: str = ""
    verify_tls: bool = False


@dataclass(frozen=True)
class PlugConfig:
    kind: str
    name: str = ""


class ConnectionStore:
    def __init__(self, path: Path = CONNECTIONS_PATH) -> None:
        self.path = path

    def load(self) -> tuple[CameraConfig | None, PlugConfig | None]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            camera = CameraConfig(**data["camera"]) if isinstance(data.get("camera"), dict) else None
            plug = PlugConfig(**data["plug"]) if isinstance(data.get("plug"), dict) else None
        except (OSError, ValueError, TypeError, KeyError):
            return None, None
        return camera, plug

    def save(self, camera: CameraConfig | None = None, plug: PlugConfig | None = None) -> None:
        """Updates only what is given and keeps the other saved choice."""
        old_camera, old_plug = self.load()
        data = {"camera": asdict(camera or old_camera) if (camera or old_camera) else None,
                "plug": asdict(plug or old_plug) if (plug or old_plug) else None}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)


def parse_camera_address(address: str) -> tuple[str, str | int]:
    """("webcam", index) or ("url", normalized http(s) address). Folders and files are not accepted here."""
    text = (address or "").strip()
    if text.isdigit() and int(text) <= MAX_WEBCAM_INDEX:
        return "webcam", int(text)
    parts = urlsplit(text)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError(CAMERA_HELP)
    try:
        parts.port  # noqa: B018 - raises ValueError for a bad port
    except ValueError as exc:
        raise ValueError(CAMERA_HELP) from exc
    return "url", text


def display_address(address: str) -> str:
    """The address without any user:password@ part, safe to show and log."""
    parts = urlsplit(address)
    if not parts.hostname or "@" not in parts.netloc:
        return address
    host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
    return urlunsplit(parts._replace(netloc=f"{host}:{parts.port}" if parts.port else host))


def build_camera(config: CameraConfig) -> FrameSource:
    kind, target = parse_camera_address(config.address)
    if kind == "webcam":
        return OpenCVSource(int(target))
    user = config.user.strip()
    return MjpegHttpSource(str(target), user, config.password if user else "", config.verify_tls)


def await_first_frame(source: FrameSource, timeout_s: float):
    """The first frame from a freshly opened source, or None if nothing arrives in time."""
    import time

    wait = getattr(source, "wait_first", None)
    if callable(wait):
        return wait(timeout_s)
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        frame = source.read()
        if frame is not None:
            return frame
        time.sleep(0.1)
    return None


def camera_summary(source: FrameSource, label: str) -> dict:
    if isinstance(source, MjpegHttpSource):
        auth = source.auth
        return {"kind": "url", "address": display_address(source.url), "user": auth[0] if auth else "",
                "has_password": bool(auth and auth[1]), "verify_tls": source.verify_tls}
    if isinstance(source, OpenCVSource):
        return {"kind": "webcam", "address": str(source.target), "user": "", "has_password": False,
                "verify_tls": False}
    if isinstance(source, NoCameraSource):
        return {"kind": "none", "address": "", "user": "", "has_password": False, "verify_tls": False}
    return {"kind": "other", "address": label, "user": "", "has_password": False, "verify_tls": False}


def plug_kind(switch) -> str:
    return "cync" if getattr(switch, "name", "") == "cync" else getattr(switch, "name", "unknown")


class ConnectionService:
    def __init__(self, engine: Engine, store: ConnectionStore | None = None, *,
                 camera_factory=build_camera, plug_factory=make_switch, login_factory=None,
                 token_path: Path = CYNC_TOKEN_PATH, camera_timeout_s: float = 10.0) -> None:
        self.engine = engine
        self.store = store or ConnectionStore()
        self._camera_factory, self._plug_factory = camera_factory, plug_factory
        self._login_factory = login_factory
        self._token_path = token_path
        self._camera_timeout_s = camera_timeout_s
        self._lock = threading.Lock()  # one change at a time
        self._login = None
        self._reconnect_cync = False  # fresh sign-in: the running Cync connection holds the old tokens

    # -- reading ------------------------------------------------------------------------------------
    @property
    def editable(self) -> bool:
        return self.engine.mode != "sim"

    def snapshot(self) -> dict:
        e = self.engine
        status = e.plug.status
        switch = e.switch
        kind = plug_kind(switch)
        devices = []
        if kind == "cync":
            try:
                devices = [d for d in _describe(switch) if d["is_plug"] or d["name"] == getattr(switch, "device_name", "")]
            except Exception:
                devices = []
        return {
            "editable": self.editable,
            "mode": e.mode,
            "heater_on": status.on if status.reachable else None,
            "camera": {**camera_summary(e.source, e.source_label), "healthy": bool(e.source.healthy)},
            "plug": {"kind": kind, "name": _device_name(switch),
                     "connected": status.reachable, "devices": devices,
                     "signed_in": self._token_path.exists()},
        }

    # -- camera -------------------------------------------------------------------------------------
    def _camera_config(self, address: str, user: str, password: str | None, verify_tls: bool) -> CameraConfig:
        parse_camera_address(address)
        user = (user or "").strip()
        if user and not password:
            current = camera_summary(self.engine.source, "")
            if current["user"] == user and isinstance(self.engine.source, MjpegHttpSource) and self.engine.source.auth:
                password = self.engine.source.auth[1]  # keep the password the camera is already using
        return CameraConfig(address.strip(), user, (password or "") if user else "", bool(verify_tls))

    def test_camera(self, address: str, user: str = "", password: str | None = None,
                    verify_tls: bool = False) -> dict:
        """Open the camera briefly without touching the running one."""
        config = self._camera_config(address, user, password, verify_tls)
        source = self._camera_factory(config)
        try:
            frame = await_first_frame(source, self._camera_timeout_s)
            if frame is None:
                raise ConnectionError(_no_frame_message(source))
            h, w = frame.image.shape[:2]
            return {"ok": True, "width": int(w), "height": int(h)}
        finally:
            source.close()

    def apply_camera(self, address: str, user: str = "", password: str | None = None,
                     verify_tls: bool = False) -> dict:
        if not self.editable:
            raise ConnectionChangeBlocked("The simulated scene has no real camera to change.")
        with self._lock:
            config = self._camera_config(address, user, password, verify_tls)
            source = self._camera_factory(config)
            try:
                frame = await_first_frame(source, self._camera_timeout_s)
                if frame is None:
                    raise ConnectionError(_no_frame_message(source))
                before = self.engine.plug.status.on
                self.engine.replace_source(source, display_address(config.address))
            except BaseException:
                source.close()
                raise
            self._remember(camera=config)
            self.engine.log("setup", f"Camera changed to {display_address(config.address)}. Check the danger zone "
                                     "still sits on the heater" + _heater_note(before),
                            {"camera": display_address(config.address), "heater_on": before})
        return self.snapshot()

    def _remember(self, camera: CameraConfig | None = None, plug: PlugConfig | None = None) -> None:
        """The change is already live; failing to save it only means it will not survive a restart."""
        try:
            self.store.save(camera=camera, plug=plug)
        except OSError as exc:
            self.engine.log("error", f"Could not save the connection settings: {exc}")

    # -- plug ---------------------------------------------------------------------------------------
    def apply_plug(self, kind: str, name: str = "") -> dict:
        if not self.editable:
            raise ConnectionChangeBlocked("The simulated scene has no real plug to change.")
        if kind not in PLUG_KINDS:
            raise ValueError(f"Choose a plug type: {', '.join(PLUG_KINDS)}.")
        name = (name or "").strip()[:80]
        with self._lock:
            e = self.engine
            before = e.plug.status.on
            current = e.switch
            if kind == "cync" and not self._token_path.exists():
                raise ValueError("Sign in to your Cync account first.")
            if kind == "cync" and plug_kind(current) == "cync" and not self._reconnect_cync:
                # Same account: pick another plug on the open connection instead of opening a second one.
                e.check_plug_change()
                current.select_device(name)
            else:
                new = DryRunPlug() if kind == "dryrun" else self._plug_factory(kind, name)
                try:
                    e.replace_switch(new)
                except BaseException:
                    close = getattr(new, "close", None)
                    if callable(close):
                        close()
                    raise
                if kind == "cync":
                    self._reconnect_cync = False
            self._remember(plug=PlugConfig(kind, name))
            label = "Test plug (simulated, no real power control)" if kind == "dryrun" else f"Cync plug {name or '(only plug)'}"
            e.log("setup", f"Plug changed to {label}. Check the shutoff works" + _heater_note(before),
                  {"plug": kind, "name": name, "heater_on": before})
        return self.snapshot()

    # -- Cync sign-in ---------------------------------------------------------------------------------
    def _new_login(self):
        if self._login_factory is not None:
            return self._login_factory()
        from .safety.cync_plug import CyncLogin

        return CyncLogin(self._token_path)

    def cync_login(self, email: str, password: str) -> dict:
        if not email.strip() or not password:
            raise ValueError("Enter your Cync email and password.")
        with self._lock:
            self._close_login()
            self._login = self._new_login()
            try:
                status = self._login.start(email.strip(), password)
            except BaseException:
                self._close_login()
                raise
            if status == "signed_in":
                self._finish_login()
        return {"status": status}

    def cync_code(self, code: str) -> dict:
        if not code.strip():
            raise ValueError("Enter the code Cync emailed you.")
        with self._lock:
            if self._login is None:
                raise ValueError("Sign in with your email and password first.")
            self._login.submit_code(code.strip())
            self._finish_login()
        return {"status": "signed_in"}

    def _finish_login(self) -> None:
        self._close_login()
        self._reconnect_cync = True
        self.engine.log("setup", "Signed in to Cync from the dashboard")

    def _close_login(self) -> None:
        if self._login is not None:
            try:
                self._login.close()
            finally:
                self._login = None

    def close(self) -> None:
        self._close_login()


def _describe(switch) -> list[dict]:
    from .safety.cync_plug import describe_devices

    return describe_devices(switch.devices())


def _device_name(switch) -> str:
    """The configured device name, or the name of the one plug the driver picked."""
    return (getattr(switch, "device_name", "") or getattr(getattr(switch, "_device", None), "name", "") or "")


def _heater_note(was_on: bool | None) -> str:
    if was_on is True:
        return " (the heater was on)."
    if was_on is None:
        return " (heater state unknown)."
    return "."


def _no_frame_message(source: FrameSource) -> str:
    detail = getattr(source, "last_error", None)
    return "No picture arrived from that camera." + (f" {detail}" if detail else " Check the address and that the camera app is streaming.")


def startup_connections(source_spec: str, plug_kind_cli: str, plug_explicit: bool,
                        store: ConnectionStore | None = None) -> tuple[CameraConfig | None, PlugConfig | None]:
    """Saved choices that apply to this launch: the camera only for `--source phone`, the plug unless a flag names another kind."""
    saved_camera, saved_plug = (store or ConnectionStore()).load()
    camera = saved_camera if source_spec == "phone" else None
    plug = None
    if saved_plug is not None and (not plug_explicit or saved_plug.kind == plug_kind_cli):
        plug = saved_plug
    return camera, plug
