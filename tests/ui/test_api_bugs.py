"""API-level tests for known broken contact points (pure async, no browser).

These are the async tests extracted from test_ui_bugs.py to avoid
event-loop conflicts with sync Playwright tests in the same collection.
"""
from __future__ import annotations

import pytest

from tests.ui.conftest import _make_state, _wu


# ---------------------------------------------------------------------------
# BUG-01 — /api/wu/abort/pivot returns 404 ("abort" is not a WU id)
# ---------------------------------------------------------------------------

async def test_bug01_abort_as_wu_id_returns_404(client, make_session, seed_session):
    """The API correctly returns 404 when 'abort' is used as a WU id."""
    sid = await make_session()
    seed_session(sid, state=_make_state({"WU-001": _wu("WU-001")}))
    r = await client.post("/api/wu/abort/pivot", json={"session_id": sid, "action": "abort"})
    assert r.status_code == 404, (
        f"BUG-01: Expected 404 for /api/wu/abort/pivot but got {r.status_code}. "
        f"abortSession() in landing calls this endpoint with 'abort' as WU id."
    )


# ---------------------------------------------------------------------------
# BUG-02 — /api/wu/session/pivot returns 404 ("session" is not a WU id)
# ---------------------------------------------------------------------------

async def test_bug02_session_as_wu_id_returns_404(client, make_session, seed_session):
    """Demonstrates: doAbortSession() falls back to wuId='session', which is invalid."""
    sid = await make_session()
    seed_session(sid, state=_make_state({"WU-001": _wu("WU-001")}))
    r = await client.post("/api/wu/session/pivot", json={"session_id": sid, "action": "abort"})
    assert r.status_code == 404, (
        f"BUG-02: Expected 404 when 'session' is used as a WU id, got {r.status_code}"
    )


# ---------------------------------------------------------------------------
# BUG-04 — resetWU state consistency after API reset
# ---------------------------------------------------------------------------

async def test_bug04_reset_wu_state_consistent(client, make_session, seed_session):
    """After reset, both the mutated WU and downstream blocked WUs are updated."""
    sid = await make_session()
    state = _make_state({
        "WU-001": _wu("WU-001", status="failed", failure_count=2),
        "WU-002": _wu("WU-002", status="blocked", deps=["WU-001"]),
    })
    seed_session(sid, state=state)

    r = await client.post("/api/wu/WU-001/reset", json={"session_id": sid})
    assert r.json()["ok"] is True

    r2 = await client.get("/api/state", params={"session_id": sid})
    wus = r2.json()["work_units"]
    assert wus["WU-001"]["status"] == "pending"
    assert "failure_count" not in wus["WU-001"]
    # Downstream WU-002 should be unblocked
    assert wus["WU-002"]["status"] == "pending"


# ---------------------------------------------------------------------------
# BUG-05 — Standalone WS route exists
# ---------------------------------------------------------------------------

async def test_bug05_standalone_ws_route_registered(app):
    """Verify /ws/state is registered in standalone mode."""
    routes = {r.path for r in app.routes if hasattr(r, "path")}
    assert "/ws/state" in routes, (
        "BUG-05: /ws/state route missing from standalone app"
    )


# ---------------------------------------------------------------------------
# BUG-06 — Session registry accessible after creation
# ---------------------------------------------------------------------------

async def test_bug06_context_save_and_retrieve(client, make_session, seed_session):
    """Context saved and retrieved under the original session_id."""
    sid = await make_session()
    r = await client.post("/api/context", json={"session_id": sid, "content": "initial"})
    assert r.json()["ok"] is True
    r2 = await client.get("/api/context", params={"session_id": sid})
    assert r2.json()["content"] == "initial"


async def test_bug06_state_readable_after_seeding(client, make_session, seed_session):
    """State written to disk is readable via /api/state."""
    sid = await make_session()
    state = _make_state({"WU-001": _wu("WU-001")})
    seed_session(sid, state=state)
    r = await client.get("/api/state", params={"session_id": sid})
    assert "WU-001" in r.json().get("work_units", {})
