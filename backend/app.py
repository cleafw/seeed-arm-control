"""FastAPI app: REST + WebSocket + static frontend.

Runs the Controller in a dedicated thread; REST handlers acquire the
Controller lock briefly to issue commands. WS clients receive snapshots
pushed by the controller via an asyncio.Queue (cross-thread safe).
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import Config
from .controller import Controller, ControllerError
from .models import (
    ActionMeta,
    ActionPatch,
    HealthResponse,
    MotorMapRequest,
    PlayRequest,
    RecordStartRequest,
    SafetyRequest,
    StateSnapshot,
)
from .storage import ActionLibrary

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Snapshot pub-sub: control thread pushes, async WS task fans out
# ---------------------------------------------------------------------------

class SnapshotHub:
    def __init__(self):
        self._loop: asyncio.AbstractEventLoop | None = None
        self._latest: dict | None = None
        self._listeners: set[asyncio.Queue] = set()

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def push_from_thread(self, snap: dict) -> None:
        """Called from the control thread."""
        self._latest = snap
        if self._loop is None:
            return
        self._loop.call_soon_threadsafe(self._fanout, snap)

    def _fanout(self, snap: dict) -> None:
        for q in list(self._listeners):
            try:
                q.put_nowait(snap)
            except asyncio.QueueFull:
                # Slow client — drop the update rather than block.
                pass

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=8)
        if self._latest is not None:
            try:
                q.put_nowait(self._latest)
            except asyncio.QueueFull:
                pass
        self._listeners.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._listeners.discard(q)


# ---------------------------------------------------------------------------
# Lifespan: start controller thread, install SIGTERM handler
# ---------------------------------------------------------------------------

def build_app() -> FastAPI:
    cfg = Config.from_env()
    library = ActionLibrary(cfg.recordings_dir)
    library.migrate_legacy_slots()
    controller = Controller(cfg, library)
    hub = SnapshotHub()
    controller.add_listener(hub.push_from_thread)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        loop = asyncio.get_running_loop()
        hub.attach_loop(loop)

        # Hardware setup + reconnect live in the control thread. Serial I/O
        # faults no longer kill the loop — only unexpected crashes exit, and
        # then we SIGTERM so Docker can restart the container.
        def thread_target():
            try:
                controller.setup_hardware()
                controller.run()
            except Exception:  # noqa: BLE001
                log.exception("Control thread crashed")
            finally:
                log.info("Control thread done; signaling shutdown")
                os.kill(os.getpid(), signal.SIGTERM)

        thread = threading.Thread(target=thread_target, daemon=True, name="controller")
        thread.start()

        try:
            yield {"controller": controller, "library": library, "hub": hub, "cfg": cfg}
        finally:
            controller.request_shutdown()
            thread.join(timeout=5.0)

    app = FastAPI(title="rebot-record", version="0.2.0", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ----------------------------------------------------------------------
    # REST routes
    # ----------------------------------------------------------------------

    @app.get("/api/health", response_model=HealthResponse)
    async def health():
        return HealthResponse(
            ok=controller.running,
            mode=controller.mode,
            master_connected=controller.arm_connected("master"),
            slave_connected=controller.arm_connected("slave"),
        )

    @app.get("/api/state", response_model=StateSnapshot)
    async def state():
        return StateSnapshot(**controller.snapshot())

    @app.get("/api/actions", response_model=list[ActionMeta])
    async def list_actions():
        return [ActionMeta(**a.meta_dict()) for a in library.list()]

    @app.post("/api/actions/record/start", response_model=StateSnapshot)
    async def record_start(req: RecordStartRequest):
        try:
            controller.start_record(req.name)
        except ControllerError as e:
            raise HTTPException(status_code=409, detail=str(e))
        return StateSnapshot(**controller.snapshot())

    @app.post("/api/actions/record/stop", response_model=ActionMeta)
    async def record_stop():
        try:
            action = controller.stop_record()
        except ControllerError as e:
            raise HTTPException(status_code=409, detail=str(e))
        return ActionMeta(**action.meta_dict())

    @app.post("/api/actions/{action_id}/play", response_model=StateSnapshot)
    async def play(action_id: str, req: PlayRequest):
        if not library.exists(action_id):
            raise HTTPException(status_code=404, detail="Action not found")
        try:
            controller.start_playback(action_id, req.mode)
        except ControllerError as e:
            raise HTTPException(status_code=409, detail=str(e))
        return StateSnapshot(**controller.snapshot())

    @app.post("/api/actions/stop", response_model=StateSnapshot)
    async def stop_play():
        controller.stop_playback()
        return StateSnapshot(**controller.snapshot())

    @app.post("/api/follow", response_model=StateSnapshot)
    async def force_follow():
        controller.force_follow()
        return StateSnapshot(**controller.snapshot())

    @app.post("/api/pause", response_model=StateSnapshot)
    async def pause():
        controller.pause()
        return StateSnapshot(**controller.snapshot())

    @app.post("/api/free_move", response_model=StateSnapshot)
    async def free_move():
        try:
            await run_in_threadpool(controller.start_free_move)
        except ControllerError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        return StateSnapshot(**controller.snapshot())

    @app.post("/api/resume", response_model=StateSnapshot)
    async def resume():
        try:
            await run_in_threadpool(controller.resume)
        except ControllerError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        return StateSnapshot(**controller.snapshot())

    @app.post("/api/safety", response_model=StateSnapshot)
    async def set_safety(req: SafetyRequest):
        controller.set_safety(req.enabled)
        return StateSnapshot(**controller.snapshot())

    @app.post("/api/recover", response_model=StateSnapshot)
    async def recover():
        # Serial I/O across 7 motors is ~hundreds of ms; offload from the
        # event loop so we don't stall WS pushes / other handlers.
        snap = await run_in_threadpool(controller.recover)
        return StateSnapshot(**snap)

    @app.post("/api/calibrate/start", response_model=StateSnapshot)
    async def calibrate_start():
        try:
            await run_in_threadpool(controller.start_calibrate)
        except ControllerError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        return StateSnapshot(**controller.snapshot())

    @app.post("/api/calibrate/finish", response_model=StateSnapshot)
    async def calibrate_finish():
        try:
            await run_in_threadpool(controller.finish_calibrate)
        except ControllerError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        return StateSnapshot(**controller.snapshot())

    @app.post("/api/calibrate/cancel", response_model=StateSnapshot)
    async def calibrate_cancel():
        try:
            await run_in_threadpool(controller.cancel_calibrate)
        except ControllerError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        return StateSnapshot(**controller.snapshot())

    @app.post("/api/motor_map", response_model=StateSnapshot)
    async def set_motor_map(req: MotorMapRequest):
        controller.set_motor_map(req.map)
        return StateSnapshot(**controller.snapshot())

    @app.get("/api/debug/slave")
    async def debug_slave():
        out = []
        for slave in controller.slaves:
            # Prefer a live refresh so the debug endpoint matches the UI bars.
            try:
                if hasattr(slave, "poll_measured_joint_states"):
                    measured = slave.poll_measured_joint_states() or {}
                elif hasattr(slave, "get_measured_joint_states"):
                    measured = slave.get_measured_joint_states() or {}
                else:
                    measured = {}
            except Exception:  # noqa: BLE001
                measured = {}
            motors = []
            for i, m in enumerate(getattr(slave, "motors", []) or []):
                def _f(x):
                    return float(x) if x is not None else None
                key = f"joint{i+1}" if i < 6 else "gripper"
                motors.append({
                    "idx": i,
                    "slave_id": getattr(m, "SlaveID", None),
                    "measured_pos": measured.get(key, _f(getattr(m, "state_q", None))),
                    "measured_tau": _f(getattr(m, "state_tau", None)),
                })
            out.append({"motors": motors})
        return {
            "commanded": controller.last_output_joint_states,
            "master_last": controller.last_joint_states,
            "measured": controller.last_measured_joint_states,
            "slaves": out,
        }

    @app.patch("/api/actions/{action_id}", response_model=ActionMeta)
    async def patch_action(action_id: str, body: ActionPatch):
        if not library.exists(action_id):
            raise HTTPException(status_code=404, detail="Action not found")
        action = await run_in_threadpool(
            library.update, action_id, body.name, body.default_play_mode
        )
        return ActionMeta(**action.meta_dict())

    @app.delete("/api/actions/{action_id}")
    async def delete_action(action_id: str):
        if not library.exists(action_id):
            raise HTTPException(status_code=404, detail="Action not found")
        # If this action is currently playing, stop first.
        snap = controller.snapshot()
        if snap.get("active_action_id") == action_id:
            controller.stop_playback()
        await run_in_threadpool(library.delete, action_id)
        return {"ok": True}

    # ----------------------------------------------------------------------
    # WebSocket
    # ----------------------------------------------------------------------

    @app.websocket("/ws")
    async def ws(socket: WebSocket):
        await socket.accept()
        # Send initial snapshot immediately so the client has state without
        # waiting for the next push tick.
        try:
            await socket.send_json(controller.snapshot())
        except WebSocketDisconnect:
            return
        q = hub.subscribe()
        try:
            while True:
                snap = await q.get()
                await socket.send_json(snap)
        except WebSocketDisconnect:
            pass
        finally:
            hub.unsubscribe(q)

    # ----------------------------------------------------------------------
    # Static frontend (must mount LAST so /api and /ws routes win)
    # ----------------------------------------------------------------------

    static_dir = Path(__file__).resolve().parent.parent / "frontend" / "dist"
    if static_dir.is_dir():
        app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="frontend")
    else:
        log.warning("Frontend dist not found at %s; serving API only", static_dir)

    return app


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)

app = build_app()
