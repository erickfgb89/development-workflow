"""FastAPI web server for the Overture dashboard.

Serves the SPA, exposes a REST state endpoint, and pushes state updates via
WebSocket whenever state.json changes.

Usage (from orchestrator):
    from .web_ui.server import start_ui_server
    ui_task = asyncio.create_task(start_ui_server(sm, port=7337))
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Module-level asyncio.Queue; orchestrator puts() state snapshots here and the
# WebSocket broadcaster pops them to push to all connected clients.
_state_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

# Event that is set when a pivot response arrives via POST /api/wu/.../pivot.
pivot_event: asyncio.Event = asyncio.Event()


def notify_state_change(state: dict[str, Any]) -> None:
    """Called by SessionManager (or any writer) after writing state.json.

    Non-blocking — puts the snapshot onto the broadcast queue.
    """
    try:
        _state_queue.put_nowait(state)
    except asyncio.QueueFull:
        pass


async def start_ui_server(sm: Any, port: int = 7337) -> None:
    """Start the FastAPI/uvicorn server.  Runs until cancelled."""
    try:
        from fastapi import FastAPI, WebSocket, WebSocketDisconnect
        from fastapi.responses import HTMLResponse, JSONResponse
        from fastapi.staticfiles import StaticFiles
        import uvicorn
    except ImportError as exc:
        raise RuntimeError(
            "Web UI dependencies not installed. "
            "Run: pip install 'overture[ui]'"
        ) from exc

    app = FastAPI(title="Overture Dashboard")
    static_dir = Path(__file__).parent / "static"

    # ------------------------------------------------------------------ REST
    @app.get("/api/state")
    async def get_state() -> JSONResponse:
        try:
            state = sm.read_state()
        except FileNotFoundError:
            state = {}
        return JSONResponse(state)

    @app.get("/api/sessions")
    async def list_sessions() -> JSONResponse:
        sessions = sm.list_sessions()
        return JSONResponse({"sessions": sessions})

    @app.get("/api/context")
    async def get_context() -> JSONResponse:
        try:
            ctx = sm.read_context()
        except FileNotFoundError:
            ctx = ""
        return JSONResponse({"content": ctx})

    @app.post("/api/context")
    async def save_context(body: dict[str, Any]) -> JSONResponse:
        content = body.get("content", "")
        sm.write_context(content)
        return JSONResponse({"ok": True})

    @app.post("/api/wu/{wu_id}/reset")
    async def reset_wu(wu_id: str) -> JSONResponse:
        try:
            state = sm.read_state()
            wus = state.get("work_units", {})
            if wu_id not in wus:
                return JSONResponse({"error": "WU not found"}, status_code=404)
            wu = wus[wu_id]
            wu["status"] = "pending"
            wu.pop("failure_count", None)
            wu.pop("needs_user_pivot", None)
            wu.pop("error", None)
            # Unblock dependents
            for other in wus.values():
                if other.get("status") == "blocked" and wu_id in other.get("dependencies", []):
                    other["status"] = "pending"
            sm.write_state(state)
            notify_state_change(state)
            pivot_event.set()
            return JSONResponse({"ok": True})
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=500)

    @app.post("/api/wu/{wu_id}/pivot")
    async def pivot_wu(wu_id: str, body: dict[str, Any]) -> JSONResponse:
        action = body.get("action")
        try:
            state = sm.read_state()
            wus = state.get("work_units", {})
            if wu_id not in wus:
                return JSONResponse({"error": "WU not found"}, status_code=404)
            wu = wus[wu_id]
            if action == "approve":
                wu["status"] = "completed"
                wu.pop("needs_user_pivot", None)
            elif action == "reset":
                wu["status"] = "pending"
                wu.pop("needs_user_pivot", None)
                wu.pop("failure_count", None)
            elif action == "abort":
                state["status"] = "abandoned"
            sm.write_state(state)
            notify_state_change(state)
            pivot_event.set()
            return JSONResponse({"ok": True})
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=500)

    # ------------------------------------------------------------------ WS
    connected_clients: list[WebSocket] = []

    @app.websocket("/ws/state")
    async def ws_state(websocket: WebSocket) -> None:
        await websocket.accept()
        connected_clients.append(websocket)
        # Send current state immediately on connect
        try:
            state = sm.read_state()
            await websocket.send_text(json.dumps(state))
        except FileNotFoundError:
            await websocket.send_text(json.dumps({}))
        except Exception:
            pass
        try:
            # Keep alive — client sends pings or we just wait for disconnect
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            connected_clients.remove(websocket)

    async def broadcaster() -> None:
        """Drain _state_queue and push snapshots to all connected WS clients."""
        while True:
            state = await _state_queue.get()
            payload = json.dumps(state)
            dead: list[WebSocket] = []
            for ws in list(connected_clients):
                try:
                    await ws.send_text(payload)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                try:
                    connected_clients.remove(ws)
                except ValueError:
                    pass

    # ------------------------------------------------------------------ SPA
    @app.get("/", response_class=HTMLResponse)
    async def root() -> HTMLResponse:
        index = static_dir / "index.html"
        return HTMLResponse(index.read_text(encoding="utf-8"))

    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)

    print(f"  Dashboard: http://localhost:{port}")

    await asyncio.gather(
        server.serve(),
        broadcaster(),
    )
