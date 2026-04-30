# WebSocket: Root Cause and Alternatives

## Root Cause (confirmed)

The 403 is not a configuration error. It is a silent exception inside FastAPI's
WebSocket dependency resolver (`get_websocket_app` → `solve_dependencies` in
`fastapi/routing.py:751`). The exception is caught by `wrap_app_handling_exceptions`,
which re-raises it because no handler is registered for it. Starlette then sends
`WebSocketClose`, which `uvicorn/websockets_impl` converts to HTTP 403.

This is a **FastAPI 0.136 + Starlette 1.0 regression** — the same route works fine
in a minimal FastAPI app but fails when built inside `_build_app()`. The exact trigger
inside `solve_dependencies` was not isolated before investigation was paused.

**Probable fix without switching anything:** pin `starlette<1.0` or `fastapi<0.136`
and the problem likely disappears. But this is a dependency management treadmill.

---

## Options

### Option A — Pin starlette to <1.0

**Effort:** 1 line in `pyproject.toml`.  
**Risk:** Starlette 1.0 is the current stable release. Pinning below it means we
diverge from upstream and eventually have to upgrade anyway. If the bug is in
FastAPI 0.136's interaction with Starlette 1.0, both will keep moving and we will
keep chasing.  
**Verdict:** Quick fix but not a real solution.

---

### Option B — Keep FastAPI, replace uvicorn with a different ASGI server

e.g. Hypercorn, Daphne, Granian.  
The 403 is emitted by `uvicorn/websockets_impl.py` converting a WS close to 403.
But the *cause* is in FastAPI/Starlette — a different ASGI server would still see
the same `websocket.close` message from the app and reject the connection.  
**Verdict:** Does not fix the root cause.

---

### Option C — Replace FastAPI with Starlette directly

FastAPI is just Starlette with auto-generated OpenAPI docs and dependency injection.
We do not use either feature. We use:
- `app.get / app.post / app.websocket` decorators → plain Starlette has these
- `JSONResponse, StreamingResponse, HTMLResponse, StaticFiles` → all from Starlette
- `WebSocket, WebSocketDisconnect` → all from Starlette

Replacing `FastAPI()` with `Starlette(routes=[...])` (or even just `Router()`)
removes the dependency injection layer that is silently failing. The Starlette
`WebSocketRoute` works correctly (confirmed in minimal tests).

**Effort:** ~1 hour. Route registration changes from decorators to explicit route
objects, or we keep the decorator style with `Starlette`'s own router.  
**Verdict:** Clean fix, removes the broken layer, keeps everything else identical.

---

### Option D — Replace FastAPI + uvicorn with aiohttp

aiohttp has its own event loop integration, HTTP server, and WebSocket support in a
single package. No ASGI, no Starlette, no uvicorn.

Pro: battle-tested WS, no version matrix to worry about.  
Con: different paradigm (no ASGI), not installed, larger conceptual change.  
**Verdict:** Overkill for what is ultimately a route-registration fix.

---

### Option E — Drop WebSockets entirely; use Server-Sent Events for push

We already use SSE successfully for streaming gatherer output (`/api/gather/start`,
`/api/gather/send`, `/api/gather/done`). The WebSocket's only job is to push
`state.json` changes to the browser so the plan view updates in real time.

SSE is one-way (server→client), which is all we need for state push. The browser
already knows how to open an `EventSource`. We could expose `GET /api/events` as a
persistent SSE stream that the server pushes state updates to.

**Effort:** ~2 hours.  
- Add `GET /api/events?session_id=X` that opens an SSE stream per session.
- Replace `connected_clients: list[WebSocket]` with `connected_streams: list[asyncio.Queue]`.
- `notify_state_change` enqueues the state snapshot to all matching queues.
- Frontend replaces `new WebSocket(...)` + `onmessage` with `new EventSource(...)` + `onmessage`.
- Frontend sends no data back over the channel (it uses POST for that), so one-way is fine.

SSE runs over plain HTTP/1.1, so there is no upgrade handshake — the same path
that already works for gatherer streaming. Zero new infrastructure.

**Verdict: Recommended.** We already proved SSE works. This removes the broken
WebSocket entirely by leaning into the pattern we already have.

---

### Option F — Polling

Browser sends `GET /api/state?session_id=X` every N seconds.

Pro: trivially simple.  
Con: latency (N seconds between a WU completing and the UI updating), wasteful on
the server.  
**Verdict:** Acceptable fallback if SSE has issues, but SSE is strictly better.

---

## Recommendation: Option E (SSE for state push)

**Why:**
1. SSE already works — we proved it with the gatherer streaming endpoints.
2. The WS job is strictly one-way push; SSE is designed for exactly that.
3. We delete ~50 lines of WS code and replace with ~30 lines of SSE that follow
   the same pattern already in the codebase.
4. No new dependencies, no ASGI version matrix, no upgrade treadmill.

**What changes:**
- Server: `GET /api/events?session_id=X` → SSE stream. `notify_state_change` pushes
  to all open streams for that session (or all streams if session_id is "").
- Frontend: `connectWS()` → `connectSSE()`, `new EventSource(...)` instead of
  `new WebSocket(...)`. `onStateUpdate` wiring stays identical.

**What stays the same:**
- All existing SSE streaming (gatherer) is untouched.
- `notify_state_change()` API is unchanged — callers don't care about the transport.
- The session_id scoping we added to the WS broadcast applies equally to SSE.
