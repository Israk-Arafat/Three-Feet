"""FastAPI server: WebSocket state, annotated MJPEG stream, REST commands, and the built dashboard.

    python -m three_feet.app --source sim --demo                  # no hardware, no API key needed
    python -m three_feet.app --source phone --plug cync --demo    # live
    python -m three_feet.app --source data/photos --demo          # replay photos (real API calls)
"""

from __future__ import annotations

import argparse
import asyncio
import os
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .calibration import CalibrationFile
from .config import CALIBRATION_PATH, REPO_ROOT, RULES_PATH, get_settings
from .engine import Engine, NullPerception
from .perception.meta_client import CostMeter, make_client
from .perception.wonder import ClearanceRule, WonderClient, rule_from_extraction
from .safety.actuator import DryRunPlug, make_switch
from .setup_service import DimensionsBody, ManualSetupBody, SetupError, SetupService
from .sources import FolderSource, open_source

WEB_DIST = REPO_ROOT / "web" / "dist"
SIM_EXTRACTION = {
    "found": True, "front_in": 36, "back_in": 36, "sides_in": 36, "top_in": None,
    "quote": "Keep combustible materials, such as furniture, pillows, bedding, papers, clothes, and curtains "
             "at least 3 feet (0.9 m) from the front, sides, and rear of the heater.",
    "page": 4, "heater_model": "SIMULATED heater", "confidence": 0.95,
    "notes": "SIMULATED extraction — no API key configured",
}


# Request bodies live at module level: with postponed annotations, FastAPI can't resolve classes
# defined inside create_app() and would silently treat them as query parameters.
class ApproveBody(BaseModel):
    front_in: float
    back_in: float
    left_in: float
    right_in: float
    source: str
    quote: str = ""
    page: int | None = None
    heater_model: str = ""
    confidence: float = 0.0


class Toggle(BaseModel):
    on: bool


class PlugAction(BaseModel):
    action: str  # stuck | unstuck | unreachable | reachable | power_on


class SimBody(BaseModel):
    autoplay: bool | None = None
    object: str | None = None
    spot: str | None = None


def default_rule(minimum_in: float) -> ClearanceRule:
    return ClearanceRule(minimum_in, minimum_in, minimum_in, minimum_in, source="default")


def build_engine(source_spec: str, plug_kind: str, demo: bool) -> tuple[Engine, object | None]:
    """Returns the engine and, in sim mode, the autoplay controller."""
    s = get_settings()
    meter = CostMeter(s.budget_usd_per_hour)
    try:
        client = make_client(s)
    except RuntimeError:
        client = None
    wonder = WonderClient(client, meter) if client else None
    rule = ClearanceRule.load() if RULES_PATH.exists() else default_rule(s.default_clearance_in)

    if source_spec == "sim":
        from .sim import Autoplay, SimPerception, SimSource, SimWorld

        plug = DryRunPlug(settle_s=0.6)
        world = SimWorld(plug)
        calib = world.calibration()
        engine = Engine(SimSource(world), SimPerception(world), plug, s, calib=calib, rule=rule,
                        probe=world.indicator_probe(calib), wonder=wonder, meter=meter, mode="sim",
                        demo=demo, source_label="simulated scene")
        engine.sim_world = world  # type: ignore[attr-defined]
        autoplay = Autoplay(world, lambda: engine.sm.state.value, engine.reset,
                            lambda text: engine.log("sim", text))
        return engine, autoplay

    from .perception.pipeline import MetaPerception
    from .perception.sam import SamClient
    from .perception.spark import SparkClient
    from .safety.verifier import IndicatorProbe

    path = Path(source_spec)
    source = FolderSource(path, loop=True, hold_s=4.0) if path.is_dir() else open_source(source_spec)
    calib = CalibrationFile.load() if CALIBRATION_PATH.exists() else None
    probe = (IndicatorProbe(calib.indicator_roi, calib.indicator_threshold)
             if calib and calib.indicator_roi and calib.indicator_threshold is not None else None)
    perception = (MetaPerception(SamClient(client, meter), SparkClient(client, meter), meter)
                  if client else NullPerception())
    engine = Engine(source, perception, make_switch(plug_kind, s.plug_name), s, calib=calib, rule=rule,
                    probe=probe, wonder=wonder, meter=meter, mode="replay" if path.is_dir() else "live",
                    demo=demo, source_label=source_spec)
    if calib is None:
        engine.log("setup", "Open Heater setup, enter the base measurements, then find or mark its footprint.")
    return engine, None


def create_app() -> FastAPI:
    source = os.environ.get("TF_SOURCE", "sim")
    plug = os.environ.get("TF_PLUG", "dryrun")
    demo = os.environ.get("TF_DEMO", "1") == "1"
    autoplay_on = os.environ.get("TF_AUTOPLAY", "1") == "1"

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine, autoplay = build_engine(source, plug, demo)
        app.state.engine, app.state.autoplay = engine, autoplay
        app.state.setup = SetupService(engine)
        engine.start()
        if autoplay is not None:
            if not autoplay_on:
                autoplay.enabled.clear()
            autoplay.start()
        yield
        if autoplay is not None:
            autoplay.stop()
        app.state.setup.close()
        engine.stop()

    app = FastAPI(title="Three Feet", lifespan=lifespan)

    def eng() -> Engine:
        return app.state.engine

    def require_demo() -> None:
        if not eng().demo:
            raise HTTPException(404, "Demo controls are disabled (start with --demo)")

    @app.get("/api/state")
    def state() -> dict:
        return eng().state_message

    @app.get("/api/config")
    def config() -> dict:
        e = eng()
        return {"mode": e.mode, "demo": e.demo, "source": e.source_label, "plug": e.switch.name,
                "autoplay": None if app.state.autoplay is None else app.state.autoplay.enabled.is_set(),
                "wonder": e.wonder is not None}

    @app.get("/api/events")
    def events(limit: int = 100) -> list[dict]:
        return eng().events_since(0, limit)

    @app.get("/api/incidents")
    def incidents() -> list[dict]:
        return [i.to_dict() for i in reversed(list(eng().incidents.values()))]

    @app.get("/api/incidents/{incident_id}")
    def incident(incident_id: str) -> dict:
        inc = eng().incidents.get(incident_id)
        if inc is None:
            raise HTTPException(404, "No such incident")
        return inc.to_dict()

    @app.post("/api/reset")
    def reset() -> dict:
        if not eng().reset():
            raise HTTPException(409, "Reset refused: only after a shutoff, with the camera working and the zone clear")
        return {"ok": True}

    @app.get("/api/snapshot.jpg")
    def snapshot() -> Response:
        import cv2

        raw = eng().latest_raw
        if raw is None:
            raise HTTPException(503, "No frame yet")
        ok, buf = cv2.imencode(".jpg", raw, [cv2.IMWRITE_JPEG_QUALITY, 90])
        return Response(buf.tobytes(), media_type="image/jpeg")

    @app.get("/stream.mjpg")
    async def stream() -> StreamingResponse:
        async def frames():
            last = None
            while True:
                jpg = eng().latest_jpeg
                if jpg is not None and jpg is not last:
                    last = jpg
                    yield (b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                           + str(len(jpg)).encode() + b"\r\n\r\n" + jpg + b"\r\n")
                await asyncio.sleep(0.08)

        return StreamingResponse(frames(), media_type="multipart/x-mixed-replace; boundary=frame",
                                 headers={"Cache-Control": "no-store"})

    @app.websocket("/ws")
    async def ws(socket: WebSocket) -> None:
        await socket.accept()
        e = eng()
        last_event = 0
        backlog = e.events_since(0, 50)
        if backlog:
            await socket.send_json({"type": "events", "events": backlog})
            last_event = backlog[-1]["id"]
        last_t = None
        try:
            while True:
                msg = e.state_message
                if msg and msg.get("t") != last_t:
                    last_t = msg.get("t")
                    await socket.send_json(msg)
                new = e.events_since(last_event)
                if new:
                    last_event = new[-1]["id"]
                    await socket.send_json({"type": "events", "events": new})
                await asyncio.sleep(0.2)
        except (WebSocketDisconnect, RuntimeError):
            return

    # -- rules (Muse Wonder) ------------------------------------------------------------------
    @app.get("/api/rules")
    def get_rule() -> dict:
        return eng().state_message.get("rule", {})

    @app.post("/api/rules/extract")
    async def extract_rule(file: UploadFile) -> dict:
        e = eng()
        if not (file.filename or "").lower().endswith(".pdf"):
            raise HTTPException(400, "Upload a PDF manual")
        data = await file.read()
        if e.wonder is None:
            if e.mode != "sim":
                raise HTTPException(503, "Muse Wonder unavailable: set MODEL_API_KEY")
            extraction, simulated = dict(SIM_EXTRACTION), True
        else:
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                tmp.write(data)
            try:
                extraction = await asyncio.to_thread(e.wonder.extract_clearance, tmp.name)
            except Exception as exc:
                raise HTTPException(502, f"Muse Wonder extraction failed: {exc}") from exc
            finally:
                os.unlink(tmp.name)
            simulated = False
        rule = rule_from_extraction(extraction, get_settings().default_clearance_in)
        e.log("rule", f"Muse Wonder read {file.filename}: "
                      + (f"p.{rule.page} — awaiting approval" if extraction.get("found") else "no clearance found"),
              {"simulated": simulated})
        return {"extraction": extraction, "rule": rule.__dict__, "simulated": simulated,
                "minimum_in": get_settings().default_clearance_in}

    @app.post("/api/rules/approve")
    def approve_rule(body: ApproveBody) -> dict:
        minimum = get_settings().default_clearance_in
        rule = ClearanceRule(**body.model_dump())
        # The approved rule can never be looser than the configured minimum.
        rule.front_in, rule.back_in = max(rule.front_in, minimum), max(rule.back_in, minimum)
        rule.left_in, rule.right_in = max(rule.left_in, minimum), max(rule.right_in, minimum)
        rule.approve()
        rule.save()
        eng().set_rule(rule)
        return {"ok": True, "rule": rule.__dict__}

    # -- setup ------------------------------------------------------------------------------------
    @app.post("/api/calibration/reference")
    def set_reference() -> dict:
        if not eng().set_reference():
            raise HTTPException(409, "Need a live frame and a calibrated zone first")
        return {"ok": True}

    # -- demo / fault injection --------------------------------------------------------------------
    @app.post("/api/demo/camera")
    def demo_camera(body: Toggle) -> dict:
        """on=True cuts the camera. Uses the phone's real /control/stop when available."""
        require_demo()
        e = eng()
        if getattr(e, "sim_world", None) is not None:
            e.sim_world.camera_cut = body.on  # type: ignore[attr-defined]
            via = "simulated"
        elif e.phone_streaming(not body.on):
            via = "phone /control/" + ("stop" if body.on else "start")
            e.fault_camera = False
        else:
            e.fault_camera = body.on
            via = "engine flag"
        e.log("fault", f"FAULT INJECTION: camera {'cut' if body.on else 'restored'} ({via})")
        return {"ok": True, "via": via}

    @app.post("/api/demo/api")
    def demo_api(body: Toggle) -> dict:
        require_demo()
        e = eng()
        e.perception.fault_api = body.on
        e.log("fault", f"FAULT INJECTION: Meta API {'outage simulated' if body.on else 'restored'}")
        return {"ok": True}

    @app.post("/api/demo/bump")
    def demo_bump(body: Toggle) -> dict:
        require_demo()
        e = eng()
        world = getattr(e, "sim_world", None)
        if world is None:
            raise HTTPException(400, "Bump the real phone instead")
        world.bumped = body.on
        e.log("fault", f"FAULT INJECTION: camera {'bumped' if body.on else 'restored'} (simulated)")
        return {"ok": True}

    @app.post("/api/demo/plug")
    def demo_plug(body: PlugAction) -> dict:
        require_demo()
        e = eng()
        plug = e.switch
        if not isinstance(plug, DryRunPlug):
            raise HTTPException(400, "Plug faults are only available with the dry-run plug")
        if body.action == "stuck":
            plug.stuck = True
        elif body.action == "unstuck":
            plug.stuck = False
        elif body.action == "unreachable":
            plug.reachable = False
        elif body.action == "reachable":
            plug.reachable = True
        elif body.action == "power_on":
            plug.simulate_power_on()
        else:
            raise HTTPException(400, "Unknown action")
        e.log("fault", f"FAULT INJECTION: dry-run plug {body.action.replace('_', ' ')}")
        return {"ok": True}

    @app.post("/api/demo/sim")
    def demo_sim(body: SimBody) -> dict:
        require_demo()
        e, auto = eng(), app.state.autoplay
        world = getattr(e, "sim_world", None)
        if world is None or auto is None:
            raise HTTPException(400, "Not running the simulator")
        if body.autoplay is not None:
            (auto.enabled.set if body.autoplay else auto.enabled.clear)()
            e.log("sim", f"Autoplay {'resumed' if body.autoplay else 'paused'}")
        if body.object and body.spot:
            if body.object not in world.objects or (body.spot != "home" and body.spot not in world.objects[body.object].spots):
                raise HTTPException(400, "Unknown object or spot")
            world.place(body.object, body.spot)
        return {"ok": True, "autoplay": auto.enabled.is_set(),
                "objects": {k: {"spots": ["home", *o.spots]} for k, o in world.objects.items()}}

    # -- dashboard ---------------------------------------------------------------------------------
    if WEB_DIST.exists():
        app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            candidate = WEB_DIST / path
            if path and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(WEB_DIST / "index.html")
    else:
        @app.get("/", include_in_schema=False)
        def no_build():
            return JSONResponse({"message": "Dashboard not built. Run `npm run build` in web/, "
                                            "or use the Vite dev server (npm run dev)."})

    return app


def main() -> None:
    import uvicorn

    ap = argparse.ArgumentParser(description="Three Feet server")
    ap.add_argument("--source", default="sim", help="sim | phone | <mjpeg url> | <webcam index> | <photo folder>")
    ap.add_argument("--plug", default=None, help="dryrun | cync (default: PLUG_KIND from .env)")
    ap.add_argument("--demo", action="store_true", help="enable fault-injection controls")
    ap.add_argument("--no-autoplay", action="store_true", help="sim: start with the scripted demo paused")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--reload", action="store_true")
    args = ap.parse_args()
    os.environ["TF_SOURCE"] = args.source
    os.environ["TF_PLUG"] = args.plug or get_settings().plug_kind
    os.environ["TF_DEMO"] = "1" if args.demo else "0"
    os.environ["TF_AUTOPLAY"] = "0" if args.no_autoplay else "1"
    uvicorn.run("three_feet.app:create_app", factory=True, host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
