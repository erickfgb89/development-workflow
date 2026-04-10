"""Tests for orchestrate/protocol.py — JSON-RPC 2.0 wire protocol types."""
from __future__ import annotations

import json

import pytest

from orchestrate.protocol import (
    JSONRPC_VERSION,
    ERR_PARSE,
    ERR_INVALID_REQUEST,
    ERR_METHOD_NOT_FOUND,
    ERR_INVALID_PARAMS,
    ERR_INTERNAL,
    ERR_INPUT_ALREADY_RESPONDED,
    ERR_SESSION_NOT_FOUND,
    ERR_SESSION_ALREADY_EXISTS,
    METHOD_AGENT_MESSAGE,
    METHOD_CHECKPOINT,
    METHOD_PHASE_CHANGED,
    METHOD_STATUS_UPDATE,
    METHOD_SESSION_RENAMED,
    METHOD_WAITING_CHANGED,
    METHOD_AGENT_TAB_CREATED,
    METHOD_AGENT_TAB_REMOVED,
    METHOD_INPUT_REQUEST,
    METHOD_SESSION_ATTACH,
    METHOD_SESSION_DETACH,
    METHOD_INPUT_RESPOND,
    METHOD_SESSION_LIST,
    METHOD_SESSION_KILL,
    JsonRpcNotification,
    JsonRpcRequest,
    JsonRpcResponse,
    JsonRpcError,
    parse_message,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def roundtrip(msg) -> dict:
    """Serialize msg to JSON and parse it back as a raw dict."""
    return json.loads(msg.to_json())


# ---------------------------------------------------------------------------
# JsonRpcNotification
# ---------------------------------------------------------------------------


class TestJsonRpcNotification:
    def test_to_json_contains_jsonrpc_version(self):
        n = JsonRpcNotification(method=METHOD_AGENT_MESSAGE, params={"text": "hello"})
        data = roundtrip(n)
        assert data["jsonrpc"] == JSONRPC_VERSION

    def test_to_json_has_method_and_params(self):
        n = JsonRpcNotification(method=METHOD_AGENT_MESSAGE, params={"text": "hello"})
        data = roundtrip(n)
        assert data["method"] == METHOD_AGENT_MESSAGE
        assert data["params"] == {"text": "hello"}

    def test_to_json_has_no_id(self):
        n = JsonRpcNotification(method=METHOD_CHECKPOINT)
        data = roundtrip(n)
        assert "id" not in data

    def test_default_params_is_empty_dict(self):
        n = JsonRpcNotification(method=METHOD_STATUS_UPDATE)
        data = roundtrip(n)
        assert data["params"] == {}

    def test_roundtrip_via_parse_message(self):
        original = JsonRpcNotification(method=METHOD_PHASE_CHANGED, params={"phase": "running"})
        parsed = parse_message(original.to_json())
        assert isinstance(parsed, JsonRpcNotification)
        assert parsed.method == original.method
        assert parsed.params == original.params

    def test_roundtrip_empty_params(self):
        original = JsonRpcNotification(method=METHOD_SESSION_RENAMED)
        parsed = parse_message(original.to_json())
        assert isinstance(parsed, JsonRpcNotification)
        assert parsed.params == {}


# ---------------------------------------------------------------------------
# JsonRpcRequest
# ---------------------------------------------------------------------------


class TestJsonRpcRequest:
    def test_to_json_contains_jsonrpc_version(self):
        req = JsonRpcRequest(method=METHOD_INPUT_REQUEST, id="abc-123", params={"prompt": "?"})
        data = roundtrip(req)
        assert data["jsonrpc"] == JSONRPC_VERSION

    def test_to_json_has_id_method_and_params(self):
        req = JsonRpcRequest(method=METHOD_SESSION_ATTACH, id=42, params={"history_limit": 100})
        data = roundtrip(req)
        assert data["method"] == METHOD_SESSION_ATTACH
        assert data["id"] == 42
        assert data["params"] == {"history_limit": 100}

    def test_string_id(self):
        req = JsonRpcRequest(method=METHOD_INPUT_REQUEST, id="req-001")
        data = roundtrip(req)
        assert data["id"] == "req-001"

    def test_default_params_is_empty_dict(self):
        req = JsonRpcRequest(method=METHOD_SESSION_DETACH, id=1)
        data = roundtrip(req)
        assert data["params"] == {}

    def test_roundtrip_via_parse_message(self):
        original = JsonRpcRequest(
            method=METHOD_INPUT_REQUEST,
            id="req-007",
            params={"request_id": "r1", "prompt": "Continue?"},
        )
        parsed = parse_message(original.to_json())
        assert isinstance(parsed, JsonRpcRequest)
        assert parsed.method == original.method
        assert parsed.id == original.id
        assert parsed.params == original.params

    def test_roundtrip_integer_id(self):
        original = JsonRpcRequest(method=METHOD_SESSION_LIST, id=99)
        parsed = parse_message(original.to_json())
        assert isinstance(parsed, JsonRpcRequest)
        assert parsed.id == 99


# ---------------------------------------------------------------------------
# JsonRpcResponse
# ---------------------------------------------------------------------------


class TestJsonRpcResponse:
    def test_to_json_contains_jsonrpc_version(self):
        resp = JsonRpcResponse(id=1, result={"ok": True})
        data = roundtrip(resp)
        assert data["jsonrpc"] == JSONRPC_VERSION

    def test_to_json_has_id_and_result(self):
        resp = JsonRpcResponse(id="abc", result={"sessions": []})
        data = roundtrip(resp)
        assert data["id"] == "abc"
        assert data["result"] == {"sessions": []}

    def test_none_result(self):
        resp = JsonRpcResponse(id=1, result=None)
        data = roundtrip(resp)
        assert data["result"] is None

    def test_roundtrip_via_parse_message(self):
        original = JsonRpcResponse(id="req-001", result={"status": "ok"})
        parsed = parse_message(original.to_json())
        assert isinstance(parsed, JsonRpcResponse)
        assert parsed.id == original.id
        assert parsed.result == original.result

    def test_roundtrip_none_result(self):
        original = JsonRpcResponse(id=5, result=None)
        parsed = parse_message(original.to_json())
        assert isinstance(parsed, JsonRpcResponse)
        assert parsed.result is None


# ---------------------------------------------------------------------------
# JsonRpcError
# ---------------------------------------------------------------------------


class TestJsonRpcError:
    def test_to_json_contains_jsonrpc_version(self):
        err = JsonRpcError(id=1, code=ERR_INTERNAL, message="oops")
        data = roundtrip(err)
        assert data["jsonrpc"] == JSONRPC_VERSION

    def test_to_json_has_error_object(self):
        err = JsonRpcError(id="r1", code=ERR_METHOD_NOT_FOUND, message="unknown method")
        data = roundtrip(err)
        assert "error" in data
        assert data["error"]["code"] == ERR_METHOD_NOT_FOUND
        assert data["error"]["message"] == "unknown method"

    def test_none_id(self):
        err = JsonRpcError(id=None, code=ERR_PARSE, message="parse error")
        data = roundtrip(err)
        assert data["id"] is None

    def test_data_field_included_when_present(self):
        err = JsonRpcError(id=1, code=ERR_INVALID_PARAMS, message="bad params", data={"field": "x"})
        data = roundtrip(err)
        assert data["error"]["data"] == {"field": "x"}

    def test_data_field_omitted_when_none(self):
        err = JsonRpcError(id=1, code=ERR_INTERNAL, message="server error")
        data = roundtrip(err)
        assert "data" not in data["error"]

    def test_roundtrip_via_parse_message(self):
        original = JsonRpcError(id="req-5", code=ERR_SESSION_NOT_FOUND, message="no such session")
        parsed = parse_message(original.to_json())
        assert isinstance(parsed, JsonRpcError)
        assert parsed.id == original.id
        assert parsed.code == original.code
        assert parsed.message == original.message
        assert parsed.data is None

    def test_roundtrip_with_data(self):
        original = JsonRpcError(
            id=None,
            code=ERR_PARSE,
            message="parse error",
            data={"raw": "invalid json"},
        )
        parsed = parse_message(original.to_json())
        assert isinstance(parsed, JsonRpcError)
        assert parsed.id is None
        assert parsed.data == {"raw": "invalid json"}

    def test_roundtrip_none_id(self):
        original = JsonRpcError(id=None, code=ERR_INVALID_REQUEST, message="bad request")
        parsed = parse_message(original.to_json())
        assert isinstance(parsed, JsonRpcError)
        assert parsed.id is None


# ---------------------------------------------------------------------------
# parse_message — disambiguation
# ---------------------------------------------------------------------------


class TestParseMessage:
    def test_parses_notification_no_id(self):
        line = json.dumps({"jsonrpc": "2.0", "method": "agent.message", "params": {"text": "hi"}})
        msg = parse_message(line)
        assert isinstance(msg, JsonRpcNotification)
        assert msg.method == "agent.message"
        assert msg.params == {"text": "hi"}

    def test_parses_request_with_id(self):
        line = json.dumps({"jsonrpc": "2.0", "method": "input.request", "id": "r1", "params": {"prompt": "?"}})
        msg = parse_message(line)
        assert isinstance(msg, JsonRpcRequest)
        assert msg.id == "r1"

    def test_parses_response_with_result(self):
        line = json.dumps({"jsonrpc": "2.0", "id": "r1", "result": {"ok": True}})
        msg = parse_message(line)
        assert isinstance(msg, JsonRpcResponse)
        assert msg.result == {"ok": True}

    def test_parses_error(self):
        line = json.dumps({
            "jsonrpc": "2.0",
            "id": "r1",
            "error": {"code": -32600, "message": "invalid request"},
        })
        msg = parse_message(line)
        assert isinstance(msg, JsonRpcError)
        assert msg.code == -32600

    def test_parses_notification_with_empty_params(self):
        line = json.dumps({"jsonrpc": "2.0", "method": "checkpoint", "params": {}})
        msg = parse_message(line)
        assert isinstance(msg, JsonRpcNotification)
        assert msg.params == {}

    def test_parses_notification_missing_params_key(self):
        # A bare notification with no params key at all
        line = json.dumps({"jsonrpc": "2.0", "method": "status.update"})
        msg = parse_message(line)
        assert isinstance(msg, JsonRpcNotification)
        assert msg.params == {}

    def test_parses_request_missing_params_key(self):
        line = json.dumps({"jsonrpc": "2.0", "method": "session.detach", "id": 1})
        msg = parse_message(line)
        assert isinstance(msg, JsonRpcRequest)
        assert msg.params == {}

    def test_error_takes_priority_over_result(self):
        # Malformed message with both result and error — error wins
        line = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "result": "should be ignored",
            "error": {"code": -32603, "message": "internal"},
        })
        msg = parse_message(line)
        assert isinstance(msg, JsonRpcError)

    def test_raises_on_invalid_json(self):
        with pytest.raises(json.JSONDecodeError):
            parse_message("not valid json{")


# ---------------------------------------------------------------------------
# Error code constants
# ---------------------------------------------------------------------------


class TestErrorCodes:
    def test_standard_error_codes(self):
        assert ERR_PARSE == -32700
        assert ERR_INVALID_REQUEST == -32600
        assert ERR_METHOD_NOT_FOUND == -32601
        assert ERR_INVALID_PARAMS == -32602
        assert ERR_INTERNAL == -32603

    def test_application_error_codes(self):
        assert ERR_INPUT_ALREADY_RESPONDED == -32000
        assert ERR_SESSION_NOT_FOUND == -32001
        assert ERR_SESSION_ALREADY_EXISTS == -32002


# ---------------------------------------------------------------------------
# Method name constants
# ---------------------------------------------------------------------------


class TestMethodConstants:
    def test_server_to_client_notification_methods(self):
        assert METHOD_AGENT_MESSAGE == "agent.message"
        assert METHOD_CHECKPOINT == "checkpoint"
        assert METHOD_PHASE_CHANGED == "phase.changed"
        assert METHOD_STATUS_UPDATE == "status.update"
        assert METHOD_SESSION_RENAMED == "session.renamed"
        assert METHOD_WAITING_CHANGED == "waiting.changed"
        assert METHOD_AGENT_TAB_CREATED == "agent.tab.created"
        assert METHOD_AGENT_TAB_REMOVED == "agent.tab.removed"

    def test_server_to_client_request_methods(self):
        assert METHOD_INPUT_REQUEST == "input.request"

    def test_client_to_server_request_methods(self):
        assert METHOD_SESSION_ATTACH == "session.attach"
        assert METHOD_SESSION_DETACH == "session.detach"
        assert METHOD_INPUT_RESPOND == "input.respond"
        assert METHOD_SESSION_LIST == "session.list"
        assert METHOD_SESSION_KILL == "session.kill"
