"""JSON-RPC 2.0 wire protocol types for socket communication.

This is a pure data-structure module with zero side effects.
All downstream socket/server code depends on these definitions.
"""
from __future__ import annotations

import dataclasses
import json
from typing import Any

JSONRPC_VERSION = "2.0"

# ---------------------------------------------------------------------------
# Standard JSON-RPC 2.0 error codes
# ---------------------------------------------------------------------------

ERR_PARSE = -32700
ERR_INVALID_REQUEST = -32600
ERR_METHOD_NOT_FOUND = -32601
ERR_INVALID_PARAMS = -32602
ERR_INTERNAL = -32603

# Application-specific error codes
ERR_INPUT_ALREADY_RESPONDED = -32000
ERR_SESSION_NOT_FOUND = -32001
ERR_SESSION_ALREADY_EXISTS = -32002

# ---------------------------------------------------------------------------
# Server-to-client notification method constants (no response expected)
# ---------------------------------------------------------------------------

METHOD_AGENT_MESSAGE = "agent.message"
METHOD_CHECKPOINT = "checkpoint"
METHOD_PHASE_CHANGED = "phase.changed"
METHOD_STATUS_UPDATE = "status.update"
METHOD_SESSION_RENAMED = "session.renamed"
METHOD_WAITING_CHANGED = "waiting.changed"
METHOD_AGENT_TAB_CREATED = "agent.tab.created"
METHOD_AGENT_TAB_REMOVED = "agent.tab.removed"

# ---------------------------------------------------------------------------
# Server-to-client request (requires response)
# ---------------------------------------------------------------------------

METHOD_INPUT_REQUEST = "input.request"  # params: {request_id: str, prompt: str}

# ---------------------------------------------------------------------------
# Client-to-server request methods
# ---------------------------------------------------------------------------

METHOD_SESSION_ATTACH = "session.attach"   # params: {history_limit?: int}
METHOD_SESSION_DETACH = "session.detach"   # params: {}
METHOD_INPUT_RESPOND = "input.respond"     # params: {request_id: str, answer: str}
METHOD_SESSION_LIST = "session.list"       # params: {}
METHOD_SESSION_KILL = "session.kill"       # params: {session_name: str}

# ---------------------------------------------------------------------------
# Message dataclasses
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class JsonRpcNotification:
    """Server-to-client one-way event (no id, no response expected)."""

    method: str
    params: dict[str, Any] = dataclasses.field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps({"jsonrpc": JSONRPC_VERSION, "method": self.method, "params": self.params})


@dataclasses.dataclass
class JsonRpcRequest:
    """Request expecting a response (has id for correlation)."""

    method: str
    id: str | int
    params: dict[str, Any] = dataclasses.field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps({"jsonrpc": JSONRPC_VERSION, "method": self.method, "id": self.id, "params": self.params})


@dataclasses.dataclass
class JsonRpcResponse:
    """Success response to a request."""

    id: str | int
    result: Any = None

    def to_json(self) -> str:
        return json.dumps({"jsonrpc": JSONRPC_VERSION, "id": self.id, "result": self.result})


@dataclasses.dataclass
class JsonRpcError:
    """Error response to a request."""

    id: str | int | None
    code: int
    message: str
    data: Any = None

    def to_json(self) -> str:
        err: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.data is not None:
            err["data"] = self.data
        return json.dumps({"jsonrpc": JSONRPC_VERSION, "id": self.id, "error": err})


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def parse_message(line: str) -> JsonRpcNotification | JsonRpcRequest | JsonRpcResponse | JsonRpcError:
    """Parse a JSON-RPC message from a newline-delimited JSON line."""
    data = json.loads(line)
    if "error" in data:
        err = data["error"]
        return JsonRpcError(
            id=data.get("id"),
            code=err["code"],
            message=err["message"],
            data=err.get("data"),
        )
    if "result" in data:
        return JsonRpcResponse(id=data["id"], result=data["result"])
    if "id" in data:
        return JsonRpcRequest(method=data["method"], id=data["id"], params=data.get("params", {}))
    return JsonRpcNotification(method=data["method"], params=data.get("params", {}))
