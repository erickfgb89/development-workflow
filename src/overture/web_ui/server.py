"""FastAPI web server for the Overture dashboard.

Serves the SPA, exposes a REST state endpoint, and pushes state updates via
WebSocket whenever state.json changes.

Two entry points:

  start_ui_server(sm, port)
      Launched alongside normal/resume orchestration (--ui flag).
      The session manager is already bound to an active session; gatherer and
      planner are NOT exposed as API endpoints because the CLI is driving them.

  start_ui_server_standalone(data_dir, port)
      Launched by --web-server flag.  No single target repo is assumed.
      The user drives the full lifecycle from the browser:
        POST /api/session/new       → create session for a chosen repo
        POST /api/gather/start      → open gatherer, get first message
        POST /api/gather/send       → one conversational turn
        POST /api/gather/done       → finalise context, write context.md
        POST /api/plan              → run planner
        POST /api/reshape           → re-run planner with instructions

      Every session endpoint takes a "session_id" body field so multiple
      sessions can be open simultaneously without sharing server state.
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


def _build_app(sm: Any, *, standalone: bool = False, data_dir: Path | None = None) -> Any:
    """Build and return the FastAPI application.

    standalone=True adds the session/gather/plan/reshape endpoints used in
    --web-server mode.  data_dir is the central sessions store used in that
    mode; sm is unused when standalone=True.
    """
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect
    from fastapi.responses import HTMLResponse, JSONResponse
    from fastapi.staticfiles import StaticFiles

    app = FastAPI(title="Overture Dashboard")
    static_dir = Path(__file__).parent / "static"

    # ------------------------------------------------------------------ dirs search (shared)

    @app.get("/api/dirs")
    async def search_dirs(q: str = "") -> JSONResponse:
        """Return filesystem directories whose path contains *q* (case-insensitive).

        Walks up to two levels from HOME.  Only git repos are included at the
        grandchild level so that a project's subdirectories (src/, tests/, etc.)
        don't crowd out the project root itself.  Results are capped at 12, git
        repos sorted first.
        """
        import os

        home = Path(os.path.expanduser("~"))
        q_lower = q.strip().lower()

        candidates: list[Path] = []
        search_roots = [home] + [
            p for p in home.iterdir() if p.is_dir() and not p.name.startswith(".")
        ]

        for root in search_roots:
            if len(candidates) >= 200:
                break
            try:
                for child in root.iterdir():
                    if not child.is_dir() or child.name.startswith("."):
                        continue
                    candidates.append(child)
                    try:
                        for grandchild in child.iterdir():
                            # Only include grandchildren that are themselves git
                            # repos — this prevents a project's subdirectories
                            # (src/, tests/, etc.) from crowding out the project
                            # root in search results.
                            if (
                                grandchild.is_dir()
                                and not grandchild.name.startswith(".")
                                and (grandchild / ".git").exists()
                            ):
                                candidates.append(grandchild)
                    except PermissionError:
                        pass
            except PermissionError:
                pass

        def is_git(p: Path) -> bool:
            return (p / ".git").exists()

        matches: list[str] = []
        for p in candidates:
            path_str = str(p)
            if not q_lower or q_lower in path_str.lower() or q_lower in p.name.lower():
                matches.append(path_str)

        matches.sort(key=lambda s: (not is_git(Path(s)), s))
        return JSONResponse({"dirs": matches[:12]})

    # ------------------------------------------------------------------ file search (shared)

    @app.get("/api/files")
    async def search_files(repo: str = "", q: str = "") -> JSONResponse:
        """Return files inside *repo* whose relative path contains *q*.

        Results are capped at 30.  Hidden files/dirs are skipped.
        """
        import os

        if not repo:
            return JSONResponse({"files": []})
        root = Path(repo)
        if not root.is_dir():
            return JSONResponse({"files": []})

        q_lower = q.strip().lower()
        matches: list[str] = []
        for dirpath, dirnames, filenames in os.walk(root):
            # prune hidden dirs in-place
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for fname in filenames:
                if fname.startswith("."):
                    continue
                rel = os.path.relpath(os.path.join(dirpath, fname), root)
                if not q_lower or q_lower in rel.lower():
                    matches.append(rel)
                if len(matches) >= 30:
                    break
            if len(matches) >= 30:
                break

        matches.sort()
        return JSONResponse({"files": matches})

    # ------------------------------------------------------------------ REST (non-standalone)

    if not standalone:
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

    # ------------------------------------------------------------------ Standalone-only endpoints

    if standalone:
        assert data_dir is not None
        from ..session_manager import SessionManager

        # session_id → SessionManager
        _registry: dict[str, SessionManager] = {}
        # session_id → WebGathererSession
        _gather_sessions: dict[str, Any] = {}

        def _get_sm(session_id: str) -> SessionManager | None:
            if session_id in _registry:
                return _registry[session_id]
            # Try loading from disk (server may have restarted)
            try:
                loaded = SessionManager.load_web_session(data_dir, session_id)
                _registry[session_id] = loaded
                return loaded
            except FileNotFoundError:
                return None

        @app.get("/api/sessions")
        async def list_sessions_standalone() -> JSONResponse:
            entries = SessionManager.list_web_sessions(data_dir)
            return JSONResponse({"sessions": entries})

        @app.get("/api/state")
        async def get_state_standalone(session_id: str = "") -> JSONResponse:
            sess_sm = _get_sm(session_id)
            if sess_sm is None:
                return JSONResponse({})
            try:
                state = sess_sm.read_state()
            except FileNotFoundError:
                state = {}
            return JSONResponse(state)

        @app.get("/api/context")
        async def get_context_standalone(session_id: str = "") -> JSONResponse:
            sess_sm = _get_sm(session_id)
            if sess_sm is None:
                return JSONResponse({"content": ""})
            try:
                ctx = sess_sm.read_context()
            except FileNotFoundError:
                ctx = ""
            return JSONResponse({"content": ctx})

        @app.post("/api/context")
        async def save_context_standalone(body: dict[str, Any]) -> JSONResponse:
            session_id = body.get("session_id", "")
            sess_sm = _get_sm(session_id)
            if sess_sm is None:
                return JSONResponse({"error": "session not found"}, status_code=404)
            sess_sm.write_context(body.get("content", ""))
            return JSONResponse({"ok": True})

        @app.post("/api/wu/{wu_id}/reset")
        async def reset_wu_standalone(wu_id: str, body: dict[str, Any]) -> JSONResponse:
            session_id = body.get("session_id", "")
            sess_sm = _get_sm(session_id)
            if sess_sm is None:
                return JSONResponse({"error": "session not found"}, status_code=404)
            try:
                state = sess_sm.read_state()
                wus = state.get("work_units", {})
                if wu_id not in wus:
                    return JSONResponse({"error": "WU not found"}, status_code=404)
                wu = wus[wu_id]
                wu["status"] = "pending"
                wu.pop("failure_count", None)
                wu.pop("needs_user_pivot", None)
                wu.pop("error", None)
                for other in wus.values():
                    if other.get("status") == "blocked" and wu_id in other.get("dependencies", []):
                        other["status"] = "pending"
                sess_sm.write_state(state)
                notify_state_change(state)
                pivot_event.set()
                return JSONResponse({"ok": True})
            except Exception as exc:
                return JSONResponse({"error": str(exc)}, status_code=500)

        @app.post("/api/wu/{wu_id}/pivot")
        async def pivot_wu_standalone(wu_id: str, body: dict[str, Any]) -> JSONResponse:
            session_id = body.get("session_id", "")
            action = body.get("action")
            sess_sm = _get_sm(session_id)
            if sess_sm is None:
                return JSONResponse({"error": "session not found"}, status_code=404)
            try:
                state = sess_sm.read_state()
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
                sess_sm.write_state(state)
                notify_state_change(state)
                pivot_event.set()
                return JSONResponse({"ok": True})
            except Exception as exc:
                return JSONResponse({"error": str(exc)}, status_code=500)

        @app.post("/api/session/new")
        async def new_session(body: dict[str, Any]) -> JSONResponse:
            """Create a new session for a given repo path."""
            repo_path_str = body.get("repo_path", "").strip()
            if not repo_path_str:
                return JSONResponse({"error": "repo_path required"}, status_code=400)
            repo_path = Path(repo_path_str)
            if not repo_path.is_dir():
                return JSONResponse({"error": f"Not a directory: {repo_path_str}"}, status_code=400)
            try:
                sess_sm = SessionManager.for_web(data_dir, repo_path)
                session_id = sess_sm.session_id
                _registry[session_id] = sess_sm
                return JSONResponse({"session_id": session_id, "ok": True})
            except Exception as exc:
                return JSONResponse({"error": str(exc)}, status_code=500)

        @app.post("/api/session/load")
        async def load_session(body: dict[str, Any]) -> JSONResponse:
            """Load an existing session by ID."""
            session_id = body.get("session_id", "").strip()
            if not session_id:
                return JSONResponse({"error": "session_id required"}, status_code=400)
            sess_sm = _get_sm(session_id)
            if sess_sm is None:
                return JSONResponse({"error": f"Session '{session_id}' not found"}, status_code=404)
            try:
                try:
                    ctx = sess_sm.read_context()
                except FileNotFoundError:
                    ctx = ""
                try:
                    state = sess_sm.read_state()
                except FileNotFoundError:
                    state = {}
                return JSONResponse({
                    "session_id": sess_sm.session_id,
                    "repo_path": str(sess_sm.target_repo),
                    "context": ctx,
                    "has_plan": bool(state),
                    "ok": True,
                })
            except Exception as exc:
                return JSONResponse({"error": str(exc)}, status_code=500)

        @app.post("/api/gather/start")
        async def gather_start(body: dict[str, Any]) -> JSONResponse:
            """Open the gatherer agent and return its opening message."""
            session_id = body.get("session_id", "")
            sess_sm = _get_sm(session_id)
            if sess_sm is None:
                return JSONResponse({"error": "session not found"}, status_code=404)
            try:
                from ..gatherer import WebGathererSession
                old = _gather_sessions.pop(session_id, None)
                if old is not None:
                    await old.close()
                wgs = WebGathererSession(sess_sm, sess_sm.target_repo)
                opening = await wgs.start()
                _gather_sessions[session_id] = wgs
                return JSONResponse({"reply": opening, "ok": True})
            except Exception as exc:
                logger.exception("gather/start failed")
                return JSONResponse({"error": str(exc)}, status_code=500)

        @app.post("/api/gather/send")
        async def gather_send(body: dict[str, Any]) -> JSONResponse:
            """Send one user message to the gatherer and return the agent reply."""
            session_id = body.get("session_id", "")
            message = body.get("message", "").strip()
            if not message:
                return JSONResponse({"error": "message required"}, status_code=400)
            wgs = _gather_sessions.get(session_id)
            if wgs is None:
                return JSONResponse(
                    {"error": "No active gather session — call /api/gather/start first"},
                    status_code=400,
                )
            try:
                reply = await wgs.send(message)
                return JSONResponse({"reply": reply, "ok": True})
            except Exception as exc:
                logger.exception("gather/send failed")
                return JSONResponse({"error": str(exc)}, status_code=500)

        @app.post("/api/gather/done")
        async def gather_done(body: dict[str, Any]) -> JSONResponse:
            """Finalise gathering: agent writes context.md and session is renamed."""
            session_id = body.get("session_id", "")
            sess_sm = _get_sm(session_id)
            if sess_sm is None:
                return JSONResponse({"error": "session not found"}, status_code=404)
            wgs = _gather_sessions.pop(session_id, None)
            if wgs is None:
                return JSONResponse({"error": "No active gather session"}, status_code=400)
            try:
                new_slug = await wgs.done()
                await wgs.close()
                # The SM was renamed; update registry under the new slug too
                _registry[new_slug] = sess_sm
                try:
                    ctx = sess_sm.read_context()
                except FileNotFoundError:
                    ctx = ""
                return JSONResponse({"slug": new_slug, "context": ctx, "ok": True})
            except Exception as exc:
                logger.exception("gather/done failed")
                return JSONResponse({"error": str(exc)}, status_code=500)

        @app.post("/api/plan")
        async def run_plan_endpoint(body: dict[str, Any]) -> JSONResponse:
            """Run the planner against the current context.md."""
            session_id = body.get("session_id", "")
            sess_sm = _get_sm(session_id)
            if sess_sm is None:
                return JSONResponse({"error": "session not found"}, status_code=404)
            try:
                from ..planner import run_plan
                dag = await run_plan(sess_sm, sess_sm.target_repo, verbose=False)
                wu_ids = [wu["id"] for wu in dag["work_units"]]
                return JSONResponse({"work_units": wu_ids, "ok": True})
            except Exception as exc:
                logger.exception("plan endpoint failed")
                return JSONResponse({"error": str(exc)}, status_code=500)

        @app.post("/api/reshape")
        async def reshape_endpoint(body: dict[str, Any]) -> JSONResponse:
            """Re-run the planner with additional reshape instructions."""
            session_id = body.get("session_id", "")
            instructions = body.get("instructions", "").strip()
            if not instructions:
                return JSONResponse({"error": "instructions required"}, status_code=400)
            sess_sm = _get_sm(session_id)
            if sess_sm is None:
                return JSONResponse({"error": "session not found"}, status_code=404)
            try:
                from ..planner import run_plan
                try:
                    ctx = sess_sm.read_context()
                except FileNotFoundError:
                    ctx = ""
                try:
                    plan_md_path = sess_sm.session_dir / "plan.md"
                    existing_plan = plan_md_path.read_text(encoding="utf-8") if plan_md_path.exists() else ""
                except Exception:
                    existing_plan = ""

                reshape_note = f"\n\n## Reshape Instructions\n{instructions}"
                if existing_plan:
                    reshape_note += f"\n\n## Existing Plan (for reference)\n{existing_plan}"

                augmented = ctx + reshape_note
                sess_sm.write_context(augmented)
                try:
                    dag = await run_plan(sess_sm, sess_sm.target_repo, verbose=False)
                finally:
                    sess_sm.write_context(ctx)

                wu_ids = [wu["id"] for wu in dag["work_units"]]
                return JSONResponse({"work_units": wu_ids, "ok": True})
            except Exception as exc:
                logger.exception("reshape endpoint failed")
                return JSONResponse({"error": str(exc)}, status_code=500)

    # ------------------------------------------------------------------ WS
    connected_clients: list[WebSocket] = []

    @app.websocket("/ws/state")
    async def ws_state(websocket: WebSocket) -> None:
        await websocket.accept()
        connected_clients.append(websocket)
        # In standalone mode we don't push an initial state — the UI requests
        # per-session state explicitly.  In coupled mode push current state.
        if not standalone:
            try:
                state = sm.read_state()
                await websocket.send_text(json.dumps(state))
            except FileNotFoundError:
                await websocket.send_text(json.dumps({}))
            except Exception:
                pass
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            try:
                connected_clients.remove(websocket)
            except ValueError:
                pass

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

    app.state.broadcaster = broadcaster

    # ------------------------------------------------------------------ SPA
    @app.get("/", response_class=HTMLResponse)
    async def root() -> HTMLResponse:
        index = static_dir / "index.html"
        return HTMLResponse(index.read_text(encoding="utf-8"))

    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    return app


async def start_ui_server(sm: Any, port: int = 7337) -> None:
    """Start the FastAPI/uvicorn server alongside normal orchestration.

    Runs until cancelled.
    """
    try:
        import uvicorn
    except ImportError as exc:
        raise RuntimeError(
            "Web UI dependencies not installed. "
            "Run: pip install overture"
        ) from exc

    app = _build_app(sm, standalone=False)
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)

    print(f"  Dashboard: http://localhost:{port}")

    await asyncio.gather(
        server.serve(),
        app.state.broadcaster(),
    )


async def start_ui_server_standalone(data_dir: Path, port: int = 7337) -> None:
    """Start the server in standalone (--web-server) mode.

    No orchestration runs automatically; the browser drives everything.
    Sessions for any repo can be created from the UI.
    Blocks until interrupted.
    """
    try:
        import uvicorn
    except ImportError as exc:
        raise RuntimeError(
            "Web UI dependencies not installed. "
            "Run: pip install overture"
        ) from exc

    data_dir.mkdir(parents=True, exist_ok=True)
    app = _build_app(None, standalone=True, data_dir=data_dir)
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)

    print(f"  Overture web server: http://localhost:{port}")
    print(f"  Session store: {data_dir}")
    print("  Session lifecycle is driven from the browser.")

    await asyncio.gather(
        server.serve(),
        app.state.broadcaster(),
    )
