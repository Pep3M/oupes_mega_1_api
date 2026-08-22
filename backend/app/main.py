"""FastAPI backend for Oupes power stations on the local network.

Maintains one persistent TCP connection to the device, parses each
\\r\\n-framed JSON message, keeps the latest merged state in memory,
and fans out updates to all subscribed WebSocket clients. REST exposes
snapshots and a short history ring for polling clients.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .protocol import (
    ATTR_MAPPING,
    ATTR_UNITS,
    MODEL_MAPPING,
    discover,
    discover_all,
    humanize,
    telemetry_frames,
)

# Uvicorn configures its own loggers but leaves the root logger alone, so
# without this our discovery progress is silently dropped -- and a startup
# that spends 30s sweeping the LAN just looks like a hang.
logging.basicConfig(
    level=(os.environ.get("LOG_LEVEL") or "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

_LOGGER = logging.getLogger(__name__)

# Explicit configuration always wins. Left unset, we auto-detect on the LAN --
# handy, but broadcast discovery is unreliable on some APs, so anyone who hits
# that should just pin MEGA_HOST.
MEGA_HOST = (os.environ.get("MEGA_HOST") or "").strip() or None
HISTORY_SIZE = int(os.environ.get("HISTORY_SIZE", "600"))  # ~10 minutes at 1Hz
DISCOVERY_RETRY_S = float(os.environ.get("DISCOVERY_RETRY_S", "30"))
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


class State:
    """Live state plus subscriber fanout. Single instance per process."""

    def __init__(self) -> None:
        self.host: str | None = MEGA_HOST
        self.latest_by_id: dict[int, Any] = {}
        self.last_frame_at: float | None = None
        self.connected: bool = False
        self.device_info: dict[str, Any] | None = None
        self.history: deque[dict[str, Any]] = deque(maxlen=HISTORY_SIZE)
        self.subscribers: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    def snapshot(self) -> dict[str, Any]:
        return {
            "connected": self.connected,
            "host": self.host,
            "device": self.device_info,
            "last_frame_at": self.last_frame_at,
            "attrs_by_id": dict(self.latest_by_id),
            "attrs": humanize(self.latest_by_id),
            "units": ATTR_UNITS,
        }

    async def merge_and_broadcast(self, frame: dict[str, Any]) -> None:
        data = (frame.get("msg") or {}).get("data") or {}
        if not data:
            return
        changed: dict[int, Any] = {}
        for k_str, v in data.items():
            try:
                k = int(k_str)
            except ValueError:
                continue
            if self.latest_by_id.get(k) != v:
                changed[k] = v
            self.latest_by_id[k] = v
        self.last_frame_at = time.time()
        self.history.append({"t": self.last_frame_at, "attrs_by_id": dict(self.latest_by_id)})
        if not changed:
            return
        event = {
            "type": "update",
            "t": self.last_frame_at,
            "changed_by_id": changed,
            "changed": humanize(changed),
            "snapshot": self.snapshot(),
        }
        await self._fanout(event)

    async def _fanout(self, event: dict[str, Any]) -> None:
        dead: list[WebSocket] = []
        async with self._lock:
            subs = list(self.subscribers)
        for ws in subs:
            try:
                await ws.send_json(event)
            except Exception:
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self.subscribers.discard(ws)


state = State()


async def resolve_host() -> str:
    """Return the device address, auto-detecting it if MEGA_HOST is unset.

    Retries forever: the backend may well start before the device is reachable
    (both booting after a power cut, say), and giving up would need a restart.
    """
    if MEGA_HOST:
        return MEGA_HOST
    loop = asyncio.get_running_loop()
    while True:
        _LOGGER.info("MEGA_HOST unset — searching for an Oupes device on the LAN")
        try:
            results = await loop.run_in_executor(None, discover_all, 3.0)
        except Exception:
            _LOGGER.exception("discovery failed")
            results = []
        if results:
            found = results[0]
            if len(results) > 1:
                _LOGGER.warning(
                    "%d devices found (%s); using %s. Set MEGA_HOST to pick another.",
                    len(results),
                    ", ".join(r.host for r in results),
                    found.host,
                )
            _LOGGER.info("found %s at %s", found.model, found.host)
            state.device_info = found.as_dict()
            return found.host
        _LOGGER.warning(
            "no device found; retrying in %.0fs. Set MEGA_HOST to skip discovery.",
            DISCOVERY_RETRY_S,
        )
        await asyncio.sleep(DISCOVERY_RETRY_S)


async def telemetry_loop() -> None:
    """Background task: stream frames forever, update state."""
    host = await resolve_host()
    state.host = host

    # Unicast discovery enriches device_info; failure is non-fatal because the
    # telemetry stream itself carries everything we actually render.
    if state.device_info is None:
        try:
            results = await asyncio.get_running_loop().run_in_executor(
                None, discover, host, 2.0
            )
            if results:
                state.device_info = results[0].as_dict()
        except Exception:
            _LOGGER.debug("device info lookup failed", exc_info=True)

    async for frame in telemetry_frames(host):
        if not state.connected:
            state.connected = True
            await state._fanout({"type": "connected", "t": time.time()})
        await state.merge_and_broadcast(frame)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(telemetry_loop())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="Oupes Local Backend", lifespan=lifespan)


@app.get("/status")
async def get_status() -> dict[str, Any]:
    return state.snapshot()


@app.get("/raw")
async def get_raw() -> dict[str, Any]:
    return {
        "host": state.host,
        "connected": state.connected,
        "last_frame_at": state.last_frame_at,
        "attrs_by_id": state.latest_by_id,
    }


@app.get("/history")
async def get_history(limit: int = 120) -> dict[str, Any]:
    items = list(state.history)[-limit:]
    return {"count": len(items), "items": items}


@app.get("/attrs")
async def get_attrs() -> dict[str, Any]:
    return {
        "known": ATTR_MAPPING,
        "units": ATTR_UNITS,
        "models": MODEL_MAPPING,
    }


@app.get("/discover")
async def trigger_discovery() -> dict[str, Any]:
    loop = asyncio.get_running_loop()
    results = await loop.run_in_executor(None, discover_all, 3.0)
    return {"count": len(results), "devices": [r.as_dict() for r in results]}


@app.websocket("/ws")
async def ws(websocket: WebSocket) -> None:
    await websocket.accept()
    async with state._lock:
        state.subscribers.add(websocket)
    try:
        await websocket.send_json({"type": "snapshot", "t": time.time(), "snapshot": state.snapshot()})
        while True:
            # We don't expect client messages yet; keep the connection open.
            msg = await websocket.receive_text()
            if msg == "ping":
                await websocket.send_json({"type": "pong", "t": time.time()})
    except WebSocketDisconnect:
        pass
    finally:
        async with state._lock:
            state.subscribers.discard(websocket)


@app.get("/")
async def index() -> Any:
    idx = STATIC_DIR / "index.html"
    if idx.exists():
        return FileResponse(idx)
    return JSONResponse({"ok": True, "msg": "no frontend bundled"})


# Mount static last so /ws, /status etc still resolve.
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
