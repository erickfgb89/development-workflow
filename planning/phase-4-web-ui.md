# Phase 4 — The Cockpit (Web UI)

## Status: Planned (not yet implemented)

---

## Goal

Add a local-only web dashboard that:
1. **Visualises** the DAG in real time (nodes coloured by WU status).
2. Provides **HITL checkpoints** — approve/reject/reshape WUs via the browser.
3. Streams `state.json` changes via a local WebSocket so the UI stays live.

The web UI is additive — the text CLI continues to work unchanged.  Running
`overture --ui` starts the FastAPI server in addition to the normal loop.

---

## Stack

| Layer | Choice | Rationale |
|---|---|---|
| Backend | **FastAPI** + **uvicorn** | Async-native, matches our asyncio loop |
| Realtime | **WebSocket** (FastAPI built-in) | Simple; no extra broker needed |
| Frontend | **Plain HTML + Alpine.js + D3.js** | Zero build tooling; DAG viz via D3 force graph |
| Packaging | Served from `src/overture/web_ui/static/` | No separate server process |

Add to `pyproject.toml` dependencies (under an `[optional-dependencies]` `ui` extra):

```toml
[project.optional-dependencies]
ui = ["fastapi>=0.115", "uvicorn[standard]>=0.32"]
```

---

## Directory layout

```
src/overture/
└── web_ui/
    ├── server.py          # FastAPI app + WebSocket endpoint
    ├── static/
    │   ├── index.html     # Single-page dashboard
    │   ├── dag.js         # D3 DAG renderer
    │   └── style.css
    └── __init__.py
```

---

## API surface

### `GET /`
Serves `index.html`.

### `GET /api/state`
Returns the current `state.json` as JSON.

### `WebSocket /ws/state`
Server pushes the full `state.json` payload whenever it changes.
Client subscribes on page load.

Change detection: the orchestrator loop calls `sm.write_state()` after every
WU update.  The WebSocket broadcaster watches for those writes using an
`asyncio.Queue` that `write_state()` populates.

### `POST /api/wu/{wu_id}/pivot`
Body: `{"action": "approve" | "reshape" | "abort", "notes": "..."}`

Clears `needs_user_pivot`, applies the action, and resumes the batch loop.
The orchestrator pauses on a `asyncio.Event` while waiting for the pivot
response.

---

## Integration with the orchestrator

```python
# orchestrator.py (with --ui flag)

from .web_ui.server import start_ui_server, pivot_event

async def run(target_repo, *, verbose=False, ui=False):
    ...
    ui_task = None
    if ui:
        ui_task = asyncio.create_task(start_ui_server(sm, port=7337))

    while True:
        ...
        if pivot_wus:
            await pivot_event.wait()   # unblocked by POST /api/wu/.../pivot
            pivot_event.clear()
        ...

    if ui_task:
        ui_task.cancel()
```

---

## DAG visualisation

Use D3's force-directed layout:

- Each WU = node, colour-coded:
  - `pending` → grey
  - `in_progress` → blue (animated)
  - `completed` → green
  - `failed` → red
  - `blocked` → orange
- Dependency edges = directed arrows.
- Click a node to expand: shows title, ACs, domain, last review summary.
- HITL buttons appear on `failed` nodes with `needs_user_pivot = true`.

---

## Phase 4 → Phase 5 hook (MCP)

When the MCP interface is added (Phase 5), the pivot actions will also be
exposed as MCP tools:  `overture_approve_wu`, `overture_reshape_wu`, etc.
The web UI and MCP interface can coexist — both post to the same FastAPI
endpoint.

---

## Estimated effort

~1 day for FastAPI server + WebSocket + state push.
~1 day for D3 DAG renderer + HITL buttons.
~0.5 day for integration + `--ui` flag.
