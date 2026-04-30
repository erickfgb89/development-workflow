# WebSocket 403 Investigation

## Symptom

Every `ws://localhost:7337/ws/state` connection attempt returns HTTP 403 immediately
with no response body and no response headers beyond the status line. The browser
reconnects every ~2.5 s (the `onclose` retry interval) and fails each time.

## Confirmed facts

- The server is reachable — HTTP endpoints (`/`, `/api/*`) work normally.
- The `/ws/state` route IS registered in the FastAPI app (`APIWebSocketRoute /ws/state`).
- The 403 is pre-existing: confirmed by stashing all current changes and hitting
  the older running process — same result.
- A **minimal** FastAPI + uvicorn WebSocket server on the same stack works fine.
- The failure is **silent**: no exception traceback appears in any log, not even at
  `logging.DEBUG`. The handler never executes — `await websocket.accept()` is never
  reached.

## Stack versions

| Package    | Version |
|------------|---------|
| uvicorn    | 0.44.0  |
| starlette  | 1.0.0   |
| websockets | 16.0    |
| wsproto    | not installed |
| Python     | 3.14    |

`uvicorn[standard]` pulls in `websockets` and uses the `websockets_impl` backend
(`websockets.legacy.server.WebSocketServerProtocol` under the hood).

## Failure path

1. Client sends HTTP Upgrade request.
2. `uvicorn/protocols/websockets/websockets_impl.py:process_request()` calls
   `self.loop.create_task(self.run_asgi())` and waits on `handshake_started_event`.
3. `run_asgi()` calls the Starlette router with a `websocket` scope.
4. Starlette's `Router.app()` iterates `self.routes` calling `route.matches(scope)`.
5. **Every route returns `Match.NONE`** — including `APIWebSocketRoute /ws/state`.
6. Router falls through to `self.default` → `not_found` → sends `websocket.close`.
7. `websockets_impl` receives the close event and replies with HTTP 403.

## Root cause hypothesis

`WebSocketRoute.matches()` checks `scope["type"] == "websocket"` then calls
`get_route_path(scope)` which subtracts `root_path` from `scope["path"]`.

In `websockets_impl`, the scope is built as:

```python
"path": self.root_path + path_portion   # e.g. "" + "/ws/state" = "/ws/state"
"root_path": self.root_path             # e.g. ""
```

`get_route_path` returns `""` when `path == root_path`. If `root_path` is non-empty
and equals the full path this silently breaks matching. However the default
`root_path` is `""` so this alone doesn't explain it for a plain `127.0.0.1` server.

**More likely:** Starlette 1.0.0 changed how `APIWebSocketRoute` is constructed or
how FastAPI wraps it, and the route object stored in `app.routes` does not match
WebSocket scopes the way older versions did. The `_build_app` function (which
registers all routes including the WS route inside one `_build_app()` call with
deferred `if standalone:` branching) may be hitting a Starlette 1.0 regression or
a FastAPI incompatibility with websockets 16 / starlette 1.0.

Investigation was cut off before confirming this final link in the chain.

## What was ruled out

- CORS / middleware rejecting the request — no middleware is registered.
- The `StaticFiles` mount intercepting `/ws/state` — prefix is `/static`, no overlap.
- `websockets.legacy.handshake.check_request` failing — tested directly, passes fine.
- Our own logging / queue changes causing the 403 — pre-exists all our changes.
- The broadcaster task crashing at startup — it hasn't received any events to process.

## Alternatives considered

See `websocket-alternatives.md` for a full recommendation.
