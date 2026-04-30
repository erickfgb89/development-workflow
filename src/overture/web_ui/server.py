"""FastAPI web server for the Overture dashboard.

Serves the SPA, exposes a REST state endpoint, and pushes state updates via
SSE whenever state.json changes.

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

# SSE subscriber registry: session_id → list of asyncio.Queue.
# Each open /api/events stream has one queue; notify_state_change enqueues into
# all queues whose session_id matches (or all queues when session_id is "").
# session_id="" is used in non-standalone (coupled) mode where there is only one session.
_sse_subscribers: dict[str, list[asyncio.Queue[str]]] = {}

# Event that is set when a pivot response arrives via POST /api/wu/.../pivot.
pivot_event: asyncio.Event = asyncio.Event()


def notify_state_change(state: dict[str, Any], session_id: str = "") -> None:
    """Push a state snapshot to all open SSE streams.

    Called by SessionManager after writing state.json.  Non-blocking.
    session_id="" broadcasts to every open stream (coupled / non-standalone mode).
    """
    payload = json.dumps({"session_id": session_id, "state": state})
    targets = (
        # coupled mode: push to every open stream regardless of session
        [q for qs in _sse_subscribers.values() for q in qs]
        if not session_id
        else _sse_subscribers.get(session_id, [])
    )
    for q in targets:
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            pass


def _build_app(sm: Any, *, standalone: bool = False, data_dir: Path | None = None) -> Any:
    """Build and return the FastAPI application.

    standalone=True adds the session/gather/plan/reshape endpoints used in
    --web-server mode.  data_dir is the central sessions store used in that
    mode; sm is unused when standalone=True.
    """
    from fastapi import FastAPI
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

    # ------------------------------------------------------------------ diff (shared)

    @app.get("/api/diff")
    async def get_diff(session_id: str = "", wu_id: str = "") -> JSONResponse:
        """Return parsed unified diff for a completed WU.

        Looks up the WU's commit_sha from state.json, then runs
        ``git show --patch <sha>`` in the target repo and parses the result
        into per-file before/after line arrays suitable for the side-by-side
        diff modal.
        """
        import subprocess as _sp

        # Resolve session manager
        if not standalone:
            sess_sm: SessionManager | None = sm
        else:
            sess_sm = _get_sm(session_id)  # type: ignore[name-defined]

        if sess_sm is None:
            return JSONResponse({"error": "session not found"}, status_code=404)

        try:
            state = sess_sm.read_state()
        except FileNotFoundError:
            return JSONResponse({"error": "no state"}, status_code=404)

        wus = state.get("work_units", {})
        if wu_id not in wus:
            return JSONResponse({"error": "WU not found"}, status_code=404)

        wu = wus[wu_id]
        commit_sha = wu.get("commit_sha")

        # Fallback: search git log by WU id in commit message
        target_repo = sess_sm.target_repo
        if not commit_sha:
            try:
                result = _sp.run(
                    ["git", "log", "--oneline", "--grep", wu_id, "-n", "1"],
                    cwd=target_repo,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if result.returncode == 0 and result.stdout.strip():
                    commit_sha = result.stdout.strip().split()[0]
            except Exception:
                pass

        if not commit_sha:
            return JSONResponse({"files": [], "error": "no commit found for this WU"})

        try:
            result = _sp.run(
                ["git", "show", "--patch", "--unified=3", commit_sha],
                cwd=target_repo,
                capture_output=True,
                text=True,
                timeout=15,
            )
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=500)

        if result.returncode != 0:
            return JSONResponse({"error": result.stderr.strip() or "git show failed"}, status_code=500)

        files = _parse_unified_diff(result.stdout)
        return JSONResponse({"files": files, "commit_sha": commit_sha})

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
                logger.info("session=new id=%s repo=%s", session_id, repo_path_str)
                return JSONResponse({"session_id": session_id, "ok": True})
            except Exception as exc:
                logger.exception("session/new failed repo=%s", repo_path_str)
                return JSONResponse({"error": str(exc)}, status_code=500)

        @app.post("/api/session/load")
        async def load_session(body: dict[str, Any]) -> JSONResponse:
            """Load an existing session by ID."""
            session_id = body.get("session_id", "").strip()
            if not session_id:
                return JSONResponse({"error": "session_id required"}, status_code=400)
            sess_sm = _get_sm(session_id)
            if sess_sm is None:
                logger.warning("session=load id=%s not_found", session_id)
                return JSONResponse({"error": f"Session '{session_id}' not found"}, status_code=404)
            try:
                try:
                    ctx = sess_sm.read_context()
                    has_context = True
                except FileNotFoundError:
                    ctx = ""
                    has_context = False
                try:
                    state = sess_sm.read_state()
                except FileNotFoundError:
                    state = {}
                has_plan = bool(state)
                logger.info(
                    "session=load id=%s has_context=%s has_plan=%s",
                    sess_sm.session_id, has_context, has_plan,
                )
                return JSONResponse({
                    "session_id": sess_sm.session_id,
                    "repo_path": str(sess_sm.target_repo),
                    "context": ctx,
                    "has_plan": has_plan,
                    "ok": True,
                })
            except Exception as exc:
                logger.exception("session/load failed id=%s", session_id)
                return JSONResponse({"error": str(exc)}, status_code=500)

        def _sse_stream(gen):  # type: ignore[return]
            """Wrap an async generator of (kind, chunk) into SSE text lines.

            Protocol (newline-delimited JSON, each line is one SSE ``data:`` field):
              {"t":"thinking","c":"<text>"}   — thinking block chunk
              {"t":"text","c":"<text>"}       — assistant text chunk
              {"t":"done","slug":"<slug>","context":"<md>"}  — stream finished
              {"t":"system","c":"<text>"}     — system notification
              {"t":"error","c":"<msg>"}       — error
            """
            import json as _json
            from fastapi.responses import StreamingResponse

            async def _iter():
                try:
                    async for kind, chunk in gen:
                        if kind == "done":
                            # done sentinel — caller sends the real done event separately
                            return
                        yield f"data: {_json.dumps({'t': kind, 'c': chunk})}\n\n"
                except Exception as exc:
                    yield f"data: {_json.dumps({'t': 'error', 'c': str(exc)})}\n\n"

            return StreamingResponse(_iter(), media_type="text/event-stream")

        @app.post("/api/gather/start")
        async def gather_start(body: dict[str, Any]):
            """Open the gatherer agent and stream its opening turn as SSE."""
            session_id = body.get("session_id", "")
            reopen = body.get("reopen", False)
            sess_sm = _get_sm(session_id)
            if sess_sm is None:
                logger.warning("gather/start session=unknown id=%s not_found", session_id)
                return JSONResponse({"error": "session not found"}, status_code=404)
            from ..gatherer import WebGathererSession, _REOPEN_PROMPT
            import json as _json
            from fastapi.responses import StreamingResponse

            logger.info("gather/start session=%s reopen=%s", session_id, reopen)
            old = _gather_sessions.pop(session_id, None)
            if old is not None:
                logger.info("gather/start session=%s closing_previous_gatherer", session_id)
                await old.close()
            wgs = WebGathererSession(sess_sm, sess_sm.target_repo)

            async def _iter():
                try:
                    if reopen:
                        logger.info("gather/start session=%s streaming_reopen", session_id)
                        async for kind, chunk in wgs.stream_start():
                            if kind == "done":
                                break
                            yield f"data: {_json.dumps({'t': kind, 'c': chunk})}\n\n"
                        yield f"data: {_json.dumps({'t': 'msg_start', 'c': ''})}\n\n"
                        async for kind, chunk in wgs.stream_send(_REOPEN_PROMPT):
                            if kind == "done":
                                break
                            yield f"data: {_json.dumps({'t': kind, 'c': chunk})}\n\n"
                    else:
                        logger.info("gather/start session=%s streaming_initial_turn", session_id)
                        async for kind, chunk in wgs.stream_start():
                            if kind == "done":
                                break
                            yield f"data: {_json.dumps({'t': kind, 'c': chunk})}\n\n"
                    _gather_sessions[session_id] = wgs
                    logger.info("gather/start session=%s complete registered_in_gather_sessions", session_id)
                    yield f"data: {_json.dumps({'t': 'done'})}\n\n"
                except Exception as exc:
                    logger.exception("gather/start session=%s streaming_failed", session_id)
                    yield f"data: {_json.dumps({'t': 'error', 'c': str(exc)})}\n\n"

            return StreamingResponse(_iter(), media_type="text/event-stream")

        @app.post("/api/gather/send")
        async def gather_send(body: dict[str, Any]):
            """Stream one user→agent turn as SSE."""
            import json as _json
            from fastapi.responses import StreamingResponse

            session_id = body.get("session_id", "")
            message = body.get("message", "").strip()
            if not message:
                return JSONResponse({"error": "message required"}, status_code=400)
            wgs = _gather_sessions.get(session_id)
            if wgs is None:
                logger.warning("gather/send session=%s no_active_gather_session", session_id)
                return JSONResponse(
                    {"error": "No active gather session — call /api/gather/start first"},
                    status_code=400,
                )

            logger.info("gather/send session=%s msg_len=%d", session_id, len(message))

            async def _iter():
                try:
                    async for kind, chunk in wgs.stream_send(message):
                        if kind == "done":
                            break
                        yield f"data: {_json.dumps({'t': kind, 'c': chunk})}\n\n"
                    logger.info("gather/send session=%s complete", session_id)
                    yield f"data: {_json.dumps({'t': 'done'})}\n\n"
                except Exception as exc:
                    logger.exception("gather/send session=%s streaming_failed", session_id)
                    yield f"data: {_json.dumps({'t': 'error', 'c': str(exc)})}\n\n"

            return StreamingResponse(_iter(), media_type="text/event-stream")

        @app.post("/api/gather/done")
        async def gather_done(body: dict[str, Any]):
            """Finalise: agent calls write_context, session is renamed. Streams SSE."""
            import json as _json
            from fastapi.responses import StreamingResponse

            session_id = body.get("session_id", "")
            sess_sm = _get_sm(session_id)
            if sess_sm is None:
                logger.warning("gather/done session=unknown id=%s not_found", session_id)
                return JSONResponse({"error": "session not found"}, status_code=404)
            wgs = _gather_sessions.get(session_id)
            if wgs is None:
                logger.warning("gather/done session=%s no_active_gather_session", session_id)
                return JSONResponse({"error": "No active gather session"}, status_code=400)

            logger.info("gather/done session=%s starting_finalisation", session_id)

            async def _iter():
                try:
                    logger.info("gather/done session=%s streaming_done_prompt", session_id)
                    async for kind, chunk in wgs.stream_done():
                        if kind == "done":
                            break
                        yield f"data: {_json.dumps({'t': kind, 'c': chunk})}\n\n"
                    context_exists = wgs._context_path.exists()
                    logger.info(
                        "gather/done session=%s done_prompt_complete context_file_exists=%s",
                        session_id, context_exists,
                    )
                    new_slug = await wgs.finalise()
                    await wgs.close()
                    _gather_sessions.pop(session_id, None)
                    _registry[new_slug] = sess_sm
                    try:
                        ctx = sess_sm.read_context()
                        ctx_len = len(ctx)
                    except FileNotFoundError:
                        ctx = ""
                        ctx_len = 0
                    logger.info(
                        "gather/done session=%s finalised new_slug=%s context_len=%d",
                        session_id, new_slug, ctx_len,
                    )
                    yield f"data: {_json.dumps({'t': 'finalised', 'slug': new_slug, 'context': ctx})}\n\n"
                except Exception as exc:
                    logger.exception("gather/done session=%s streaming_failed", session_id)
                    yield f"data: {_json.dumps({'t': 'error', 'c': str(exc)})}\n\n"

            return StreamingResponse(_iter(), media_type="text/event-stream")

        @app.post("/api/plan")
        async def run_plan_endpoint(body: dict[str, Any]) -> JSONResponse:
            """Run the planner against the current context.md."""
            session_id = body.get("session_id", "")
            sess_sm = _get_sm(session_id)
            if sess_sm is None:
                logger.warning("plan session=unknown id=%s not_found", session_id)
                return JSONResponse({"error": "session not found"}, status_code=404)
            logger.info("plan session=%s starting_planner", session_id)
            try:
                from ..planner import run_plan
                dag = await run_plan(sess_sm, sess_sm.target_repo, verbose=False)
                wu_ids = [wu["id"] for wu in dag["work_units"]]
                logger.info("plan session=%s complete wu_count=%d wus=%s", session_id, len(wu_ids), wu_ids)
                return JSONResponse({"work_units": wu_ids, "ok": True})
            except Exception as exc:
                logger.exception("plan session=%s failed", session_id)
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

        # Track running execution tasks per session so we can avoid double-starts.
        _exec_tasks: dict[str, "asyncio.Task[None]"] = {}

        @app.post("/api/execute")
        async def execute_endpoint(body: dict[str, Any]) -> JSONResponse:
            """Start (or no-op if already running) the batch execution loop for a session.

            Returns immediately; the loop runs as a background asyncio task and
            pushes state updates via SSE as each WU completes.
            """
            session_id = body.get("session_id", "")
            sess_sm = _get_sm(session_id)
            if sess_sm is None:
                return JSONResponse({"error": "session not found"}, status_code=404)

            existing = _exec_tasks.get(session_id)
            if existing and not existing.done():
                return JSONResponse({"ok": True, "status": "already_running"})

            async def _run_loop() -> None:
                from ..batch_manager import run_batch
                from ..planner import get_ready_wus
                logger.info("execute session=%s loop_start", session_id)
                try:
                    while True:
                        state = sess_sm.read_state()
                        wus = state.get("work_units", {})

                        pivot_wus = [wid for wid, w in wus.items() if w.get("needs_user_pivot")]
                        if pivot_wus:
                            logger.info("execute session=%s pivot_needed wus=%s", session_id, pivot_wus)
                            break

                        statuses = [w["status"] for w in wus.values()]
                        if all(s == "completed" for s in statuses):
                            logger.info("execute session=%s all_completed", session_id)
                            break
                        if statuses and all(s in ("completed", "failed", "blocked") for s in statuses):
                            logger.info("execute session=%s ended_with_failures", session_id)
                            break

                        ready = get_ready_wus(state)
                        if not ready:
                            logger.info("execute session=%s no_ready_wus deadlock", session_id)
                            break

                        summary = await run_batch(sess_sm, sess_sm.target_repo, verbose=False)
                        logger.info("execute session=%s batch_done summary=%s", session_id, summary)
                        if summary.get("ready", 0) == 0:
                            break
                except Exception:
                    logger.exception("execute session=%s loop_error", session_id)
                finally:
                    _exec_tasks.pop(session_id, None)

            task = asyncio.create_task(_run_loop())
            _exec_tasks[session_id] = task
            logger.info("execute session=%s task_created", session_id)
            return JSONResponse({"ok": True, "status": "started"})

    # ------------------------------------------------------------------ SSE state stream
    @app.get("/api/events")
    async def sse_events(session_id: str = "") -> Any:
        """Persistent SSE stream that pushes state snapshots to the browser.

        The client subscribes with ?session_id=<id>.  The server pushes a JSON
        object {"session_id": ..., "state": ...} whenever state.json changes for
        that session.  In coupled (non-standalone) mode session_id may be omitted.

        On connect, the current state is sent immediately so the browser doesn't
        have to poll separately.
        """
        import json as _json
        from fastapi.responses import StreamingResponse

        q: asyncio.Queue[str] = asyncio.Queue(maxsize=64)
        key = session_id or ""
        _sse_subscribers.setdefault(key, []).append(q)
        logger.info("sse/events session=%s subscriber_count=%d", key or "coupled",
                    len(_sse_subscribers.get(key, [])))

        # Send current state immediately on connect.
        if not standalone:
            try:
                initial = sm.read_state()
            except FileNotFoundError:
                initial = {}
            await q.put(_json.dumps({"session_id": "", "state": initial}))
        elif session_id:
            sess_sm = _get_sm(session_id)  # type: ignore[name-defined]
            if sess_sm is not None:
                try:
                    initial = sess_sm.read_state()
                except FileNotFoundError:
                    initial = {}
                await q.put(_json.dumps({"session_id": session_id, "state": initial}))

        async def _stream():
            try:
                while True:
                    payload = await q.get()
                    yield f"data: {payload}\n\n"
            except asyncio.CancelledError:
                pass
            finally:
                subs = _sse_subscribers.get(key, [])
                try:
                    subs.remove(q)
                except ValueError:
                    pass
                if not subs:
                    _sse_subscribers.pop(key, None)
                logger.info("sse/events session=%s disconnected remaining=%d",
                            key or "coupled", len(_sse_subscribers.get(key, [])))

        return StreamingResponse(
            _stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ------------------------------------------------------------------ SPA
    @app.get("/", response_class=HTMLResponse)
    async def root() -> HTMLResponse:
        index = static_dir / "index.html"
        return HTMLResponse(index.read_text(encoding="utf-8"))

    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    return app


def _parse_unified_diff(patch: str) -> list[dict]:
    """Parse a ``git show --patch`` output into per-file diff objects.

    Each entry::

        {
          "path": "src/foo.py",
          "additions": int,
          "deletions": int,
          "beforeLines": [{"ln": int|None, "text": str, "type": "normal"|"del"|"add"}],
          "afterLines":  [{"ln": int|None, "text": str, "type": "normal"|"del"|"add"}],
        }
    """
    files: list[dict] = []
    current: dict | None = None
    before_ln = 0
    after_ln = 0

    for raw_line in patch.splitlines():
        # New file section
        if raw_line.startswith("diff --git "):
            if current is not None:
                files.append(current)
            current = {
                "path": "",
                "additions": 0,
                "deletions": 0,
                "beforeLines": [],
                "afterLines": [],
            }
            before_ln = 0
            after_ln = 0
            continue

        if current is None:
            continue

        # Extract canonical path from +++ line (handles renames)
        if raw_line.startswith("+++ b/"):
            current["path"] = raw_line[6:]
            continue
        if raw_line.startswith("+++ /dev/null"):
            current["path"] = current["path"] or "(deleted)"
            continue
        if raw_line.startswith("--- ") or raw_line.startswith("+++ "):
            continue

        # Hunk header: @@ -a,b +c,d @@
        if raw_line.startswith("@@"):
            import re
            m = re.match(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", raw_line)
            if m:
                before_ln = int(m.group(1))
                after_ln = int(m.group(2))
            # Render hunk header as a separator on both sides
            sep = {"ln": None, "text": raw_line, "type": "hunk"}
            current["beforeLines"].append(sep)
            current["afterLines"].append(sep)
            continue

        if raw_line.startswith("-"):
            text = raw_line[1:]
            current["beforeLines"].append({"ln": before_ln, "text": text, "type": "rem"})
            current["afterLines"].append({"ln": None, "text": "", "type": "empty"})
            current["deletions"] += 1
            before_ln += 1
        elif raw_line.startswith("+"):
            text = raw_line[1:]
            current["beforeLines"].append({"ln": None, "text": "", "type": "empty"})
            current["afterLines"].append({"ln": after_ln, "text": text, "type": "add"})
            current["additions"] += 1
            after_ln += 1
        elif raw_line.startswith(" ") or raw_line == "":
            text = raw_line[1:] if raw_line.startswith(" ") else ""
            current["beforeLines"].append({"ln": before_ln, "text": text, "type": "normal"})
            current["afterLines"].append({"ln": after_ln, "text": text, "type": "normal"})
            before_ln += 1
            after_ln += 1
        # skip binary/index/mode lines

    if current is not None:
        files.append(current)

    # Drop entries with no path (binary files, submodules, etc.)
    return [f for f in files if f["path"]]


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
    await server.serve()


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

    await server.serve()
