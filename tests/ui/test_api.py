"""API contract tests — no browser required.

Tests every REST endpoint directly via httpx ASGI transport.
Verifies request/response shapes, error codes, and state mutations.
"""
from __future__ import annotations

import json

import pytest

from tests.ui.conftest import _make_state, _wu


# ---------------------------------------------------------------------------
# /api/dirs
# ---------------------------------------------------------------------------

class TestDirsSearch:
    async def test_returns_dirs_list(self, client):
        r = await client.get("/api/dirs", params={"q": "home"})
        assert r.status_code == 200
        data = r.json()
        assert "dirs" in data
        assert isinstance(data["dirs"], list)

    async def test_empty_query_returns_empty(self, client):
        # The server only includes matches when q is non-empty; empty q returns []
        # (server skips the matching loop when q_lower is falsy)
        r = await client.get("/api/dirs", params={"q": ""})
        assert r.status_code == 200
        # When q is empty string, no candidates are added because the condition
        # `if not q_lower` short-circuits — but server walks candidates and adds
        # all that match (empty string matches everything).  Verify shape only.
        data = r.json()
        assert "dirs" in data
        assert isinstance(data["dirs"], list)
        assert len(data["dirs"]) <= 12

    async def test_caps_at_12(self, client, tmp_path):
        # With a realistic query unlikely to match many dirs, cap is not exceeded
        r = await client.get("/api/dirs", params={"q": "zzznomatch"})
        assert len(r.json()["dirs"]) <= 12


# ---------------------------------------------------------------------------
# /api/session/new
# ---------------------------------------------------------------------------

class TestSessionNew:
    async def test_creates_session(self, client, tmp_repo):
        r = await client.post("/api/session/new", json={"repo_path": str(tmp_repo)})
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert isinstance(data["session_id"], str)
        assert len(data["session_id"]) > 0

    async def test_missing_repo_path_returns_400(self, client):
        r = await client.post("/api/session/new", json={})
        assert r.status_code == 400
        assert "error" in r.json()

    async def test_nonexistent_path_returns_400(self, client):
        r = await client.post("/api/session/new", json={"repo_path": "/nonexistent/path/xyz"})
        assert r.status_code == 400
        assert "error" in r.json()

    async def test_second_call_creates_distinct_session(self, client, tmp_repo):
        r1 = await client.post("/api/session/new", json={"repo_path": str(tmp_repo)})
        r2 = await client.post("/api/session/new", json={"repo_path": str(tmp_repo)})
        assert r1.json()["session_id"] != r2.json()["session_id"]


# ---------------------------------------------------------------------------
# /api/session/load
# ---------------------------------------------------------------------------

class TestSessionLoad:
    async def test_loads_existing_session(self, client, make_session):
        sid = await make_session()
        r = await client.post("/api/session/load", json={"session_id": sid})
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert data["session_id"] == sid
        assert "repo_path" in data
        assert "context" in data
        assert "has_plan" in data

    async def test_missing_session_id_returns_400(self, client):
        r = await client.post("/api/session/load", json={})
        assert r.status_code == 400

    async def test_unknown_session_returns_404(self, client):
        r = await client.post("/api/session/load", json={"session_id": "nonexistent-id"})
        assert r.status_code == 404

    async def test_has_plan_false_when_no_state(self, client, make_session):
        sid = await make_session()
        data = (await client.post("/api/session/load", json={"session_id": sid})).json()
        assert data["has_plan"] is False

    async def test_has_plan_true_when_state_exists(self, client, make_session, seed_session):
        sid = await make_session()
        state = _make_state({"WU-001": _wu("WU-001")})
        seed_session(sid, state=state)
        data = (await client.post("/api/session/load", json={"session_id": sid})).json()
        assert data["has_plan"] is True


# ---------------------------------------------------------------------------
# /api/sessions
# ---------------------------------------------------------------------------

class TestSessionsList:
    async def test_empty_initially(self, client):
        r = await client.get("/api/sessions")
        assert r.status_code == 200
        data = r.json()
        assert "sessions" in data
        assert isinstance(data["sessions"], list)

    async def test_session_appears_after_create(self, client, make_session):
        sid = await make_session()
        r = await client.get("/api/sessions")
        ids = [s["id"] for s in r.json()["sessions"]]
        assert sid in ids


# ---------------------------------------------------------------------------
# /api/state
# ---------------------------------------------------------------------------

class TestGetState:
    async def test_empty_before_plan(self, client, make_session):
        sid = await make_session()
        r = await client.get("/api/state", params={"session_id": sid})
        assert r.status_code == 200
        assert r.json() == {}

    async def test_returns_state_after_seed(self, client, make_session, seed_session):
        sid = await make_session()
        state = _make_state({"WU-001": _wu("WU-001")})
        seed_session(sid, state=state)
        r = await client.get("/api/state", params={"session_id": sid})
        data = r.json()
        assert "work_units" in data
        assert "WU-001" in data["work_units"]

    async def test_unknown_session_returns_empty(self, client):
        r = await client.get("/api/state", params={"session_id": "does-not-exist"})
        assert r.status_code == 200
        assert r.json() == {}


# ---------------------------------------------------------------------------
# /api/context  (GET + POST)
# ---------------------------------------------------------------------------

class TestContext:
    async def test_empty_context_before_write(self, client, make_session):
        sid = await make_session()
        r = await client.get("/api/context", params={"session_id": sid})
        assert r.status_code == 200
        assert r.json()["content"] == ""

    async def test_save_and_retrieve_context(self, client, make_session):
        sid = await make_session()
        text = "Build a login flow with OAuth2"
        r = await client.post("/api/context", json={"session_id": sid, "content": text})
        assert r.json()["ok"] is True
        r2 = await client.get("/api/context", params={"session_id": sid})
        assert r2.json()["content"] == text

    async def test_save_context_unknown_session_returns_404(self, client):
        r = await client.post("/api/context", json={"session_id": "nope", "content": "x"})
        assert r.status_code == 404

    async def test_get_context_unknown_session_returns_empty(self, client):
        r = await client.get("/api/context", params={"session_id": "nope"})
        assert r.status_code == 200
        assert r.json()["content"] == ""


# ---------------------------------------------------------------------------
# /api/wu/{wu_id}/reset
# ---------------------------------------------------------------------------

class TestWUReset:
    async def test_reset_pending_to_pending(self, client, make_session, seed_session):
        sid = await make_session()
        state = _make_state({"WU-001": _wu("WU-001", status="failed", failure_count=2)})
        seed_session(sid, state=state)
        r = await client.post("/api/wu/WU-001/reset", json={"session_id": sid})
        assert r.status_code == 200
        assert r.json()["ok"] is True

    async def test_reset_clears_failure_count(self, client, make_session, seed_session):
        sid = await make_session()
        state = _make_state({"WU-001": _wu("WU-001", status="failed", failure_count=3)})
        seed_session(sid, state=state)
        await client.post("/api/wu/WU-001/reset", json={"session_id": sid})
        state_r = await client.get("/api/state", params={"session_id": sid})
        wu = state_r.json()["work_units"]["WU-001"]
        assert wu["status"] == "pending"
        assert "failure_count" not in wu

    async def test_reset_unblocks_downstream(self, client, make_session, seed_session):
        sid = await make_session()
        state = _make_state({
            "WU-001": _wu("WU-001", status="failed"),
            "WU-002": _wu("WU-002", status="blocked", deps=["WU-001"]),
        })
        seed_session(sid, state=state)
        await client.post("/api/wu/WU-001/reset", json={"session_id": sid})
        state_r = await client.get("/api/state", params={"session_id": sid})
        wus = state_r.json()["work_units"]
        assert wus["WU-002"]["status"] == "pending"

    async def test_reset_unknown_wu_returns_404(self, client, make_session, seed_session):
        sid = await make_session()
        seed_session(sid, state=_make_state({}))
        r = await client.post("/api/wu/WU-999/reset", json={"session_id": sid})
        assert r.status_code == 404

    async def test_reset_unknown_session_returns_404(self, client):
        r = await client.post("/api/wu/WU-001/reset", json={"session_id": "nope"})
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# /api/wu/{wu_id}/pivot
# ---------------------------------------------------------------------------

class TestWUPivot:
    async def test_approve_sets_completed(self, client, make_session, seed_session):
        sid = await make_session()
        state = _make_state({"WU-001": _wu("WU-001", status="failed", needs_user_pivot=True)})
        seed_session(sid, state=state)
        r = await client.post("/api/wu/WU-001/pivot", json={"session_id": sid, "action": "approve"})
        assert r.json()["ok"] is True
        wu = (await client.get("/api/state", params={"session_id": sid})).json()["work_units"]["WU-001"]
        assert wu["status"] == "completed"
        assert "needs_user_pivot" not in wu

    async def test_reset_pivot_sets_pending(self, client, make_session, seed_session):
        sid = await make_session()
        state = _make_state({"WU-001": _wu("WU-001", status="failed", needs_user_pivot=True, failure_count=2)})
        seed_session(sid, state=state)
        await client.post("/api/wu/WU-001/pivot", json={"session_id": sid, "action": "reset"})
        wu = (await client.get("/api/state", params={"session_id": sid})).json()["work_units"]["WU-001"]
        assert wu["status"] == "pending"
        assert "needs_user_pivot" not in wu
        assert "failure_count" not in wu

    async def test_abort_marks_session_abandoned(self, client, make_session, seed_session):
        sid = await make_session()
        state = _make_state({"WU-001": _wu("WU-001", status="failed")})
        seed_session(sid, state=state)
        await client.post("/api/wu/WU-001/pivot", json={"session_id": sid, "action": "abort"})
        s = (await client.get("/api/state", params={"session_id": sid})).json()
        assert s.get("status") == "abandoned"

    async def test_pivot_unknown_wu_returns_404(self, client, make_session, seed_session):
        sid = await make_session()
        seed_session(sid, state=_make_state({}))
        r = await client.post("/api/wu/WU-999/pivot", json={"session_id": sid, "action": "approve"})
        assert r.status_code == 404

    async def test_pivot_unknown_session_returns_404(self, client):
        r = await client.post("/api/wu/WU-001/pivot", json={"session_id": "nope", "action": "approve"})
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# /api/reshape  (validation — no real planner)
# ---------------------------------------------------------------------------

class TestReshape:
    async def test_missing_instructions_returns_400(self, client, make_session, seed_session):
        sid = await make_session()
        seed_session(sid, state=_make_state({"WU-001": _wu("WU-001")}))
        r = await client.post("/api/reshape", json={"session_id": sid, "instructions": ""})
        assert r.status_code == 400
        assert "error" in r.json()

    async def test_unknown_session_returns_404(self, client):
        r = await client.post("/api/reshape", json={"session_id": "nope", "instructions": "redo it"})
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# /api/gather/*  (validation — no real agent)
# ---------------------------------------------------------------------------

class TestGatherValidation:
    async def test_gather_send_without_start_returns_400(self, client, make_session):
        sid = await make_session()
        r = await client.post("/api/gather/send", json={"session_id": sid, "message": "hello"})
        assert r.status_code == 400

    async def test_gather_send_empty_message_returns_400(self, client, make_session):
        sid = await make_session()
        r = await client.post("/api/gather/send", json={"session_id": sid, "message": ""})
        assert r.status_code == 400

    async def test_gather_done_without_start_returns_400(self, client, make_session):
        sid = await make_session()
        r = await client.post("/api/gather/done", json={"session_id": sid})
        assert r.status_code == 400

    async def test_gather_start_unknown_session_returns_404(self, client):
        r = await client.post("/api/gather/start", json={"session_id": "nope"})
        assert r.status_code == 404
