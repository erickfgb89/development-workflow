import asyncio
import json
from typing import Any

class EventBus:
    def __init__(self):
        self.connections: list[Any] = []
        self.pending_interactions: dict[str, asyncio.Future] = {}

    async def connect(self, websocket: Any):
        await websocket.accept()
        self.connections.append(websocket)

    def disconnect(self, websocket: Any):
        if websocket in self.connections:
            self.connections.remove(websocket)

    async def broadcast(self, message: dict[str, Any]):
        msg_str = json.dumps(message)
        for connection in list(self.connections):
            try:
                await connection.send_text(msg_str)
            except Exception:
                self.disconnect(connection)

    def emit(self, event_type: str, payload: dict[str, Any]):
        if not self.connections:
            return
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.broadcast({"type": event_type, "payload": payload}))
        except RuntimeError:
            pass

    def create_interaction(self, interaction_id: str) -> asyncio.Future:
        future = asyncio.get_running_loop().create_future()
        self.pending_interactions[interaction_id] = future
        return future

    def resolve_interaction(self, interaction_id: str, result: Any):
        if interaction_id in self.pending_interactions:
            if not self.pending_interactions[interaction_id].done():
                self.pending_interactions[interaction_id].set_result(result)
            del self.pending_interactions[interaction_id]

global_bus = EventBus()
