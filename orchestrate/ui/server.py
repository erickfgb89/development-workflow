import importlib.resources
import json
import mimetypes
from fastapi import FastAPI, WebSocket, Request, WebSocketDisconnect
from fastapi.responses import HTMLResponse, Response
import uvicorn
from .events import global_bus
import os
from pathlib import Path

ui_state = {"project_root": "."}

app = FastAPI(title="Mission Control")

def get_static_file(filename: str) -> bytes:
    return importlib.resources.files("orchestrate.ui.static").joinpath(filename).read_bytes()

@app.get("/")
async def get_index():
    try:
        content = get_static_file("index.html")
        return HTMLResponse(content=content)
    except Exception as e:
        return HTMLResponse(content=f"Error loading UI: {e}", status_code=500)

@app.get("/static/{filename}")
async def get_static(filename: str):
    try:
        content = get_static_file(filename)
        mime_type, _ = mimetypes.guess_type(filename)
        return Response(content=content, media_type=mime_type or "application/octet-stream")
    except Exception as e:
        return Response(content=f"Not found: {e}", status_code=404)

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await global_bus.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                if msg.get("type") == "answer":
                    global_bus.resolve_interaction(msg["payload"]["id"], msg["payload"]["result"])
            except Exception as e:
                print(f"Error handling UI message: {e}")
    except WebSocketDisconnect:
        global_bus.disconnect(websocket)
    except Exception:
        global_bus.disconnect(websocket)

@app.get("/api/workspace")
async def get_workspace():
    root = Path(ui_state["project_root"]).resolve()
    def build_tree(p: Path) -> dict | None:
        if p.name in (".git", "__pycache__", ".venv", ".pytest_cache", "tmp") or p.name.endswith(".pyc") or p.name.endswith(".pyz"):
            return None
        # Don't recurse infinitely
        try:
            if p.is_file():
                return {"type": "file", "name": p.name, "path": str(p.relative_to(root))}
            elif p.is_dir():
                children = []
                for child in p.iterdir():
                    c_node = build_tree(child)
                    if c_node:
                        children.append(c_node)
                return {"type": "dir", "name": p.name, "path": str(p.relative_to(root)), "children": sorted(children, key=lambda x: (x["type"]=="file", x["name"]))}
        except Exception:
            pass
        return None
    return {"tree": build_tree(root)}

@app.get("/api/workspace/file")
async def get_workspace_file(path: str):
    root = Path(ui_state["project_root"]).resolve()
    target = (root / path).resolve()
    try:
        # Prevent path traversal
        target.relative_to(root)
        return Response(content=target.read_bytes(), media_type="text/plain")
    except Exception as e:
        return Response(content=str(e), status_code=404)

async def run_server(project_root: str) -> None:
    ui_state["project_root"] = project_root
    config = uvicorn.Config(
        app=app,
        host="127.0.0.1",
        port=8000,
        log_level="info",
    )
    server = uvicorn.Server(config)
    await server.serve()
