"""Browser tests for known broken contact points between core and web server.

Each test is named after the specific bug it exercises.  Tests marked
``xfail`` document known broken behaviour and become regression guards once fixed.

For the API-level (async) bug tests, see test_api_bugs.py.

Bug inventory
-------------
BUG-01  abortSession() on landing calls /api/wu/abort/pivot — "abort" is not
        a WU id.

BUG-02  doAbortSession() in the reshape modal falls back to wuId = 'session'
        when reshapeModal.wu is null (deadlock path).

BUG-03  openDiff() uses `findIndex() || 0` to set activeFile.  findIndex
        returns -1 when not found, which is truthy, so the fallback to 0
        is never applied.

BUG-05  In standalone mode the WebSocket endpoint does NOT push an initial
        state snapshot on connect.
"""
from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from tests.ui.conftest import (
    LiveServer,
    _make_state,
    _wu,
    create_session_via_api,
    navigate_to_session,
    seed_state,
)


# ---------------------------------------------------------------------------
# BUG-01 — abortSession() on landing calls the wrong endpoint
# ---------------------------------------------------------------------------

@pytest.mark.xfail(
    strict=False,
    reason="BUG-01: abortSession() uses /api/wu/abort/pivot — 'abort' is not a WU id",
)
def test_bug01_abort_session_landing_sends_valid_request(
    page: Page, live_server: LiveServer, tmp_path
):
    repo = tmp_path / "abortrepo"
    repo.mkdir()
    (repo / ".git").mkdir()
    sid = create_session_via_api(live_server, str(repo))
    state = _make_state({"WU-001": _wu("WU-001")})
    seed_state(live_server, sid, state)

    page.route("**/api/gather/start", lambda r: r.fulfill(
        status=200, content_type="application/json", body='{"reply": "Hi", "ok": true}',
    ))
    navigate_to_session(page, live_server, sid)
    page.wait_for_selector("#page-plan", state="visible", timeout=5000)

    page.locator(".add-tab").click()
    page.wait_for_selector("#page-landing", state="visible", timeout=3000)
    page.locator(".danger-zone-head").click()

    requests_made = []
    page.route("**/api/wu/**", lambda r: (
        requests_made.append({"url": r.url, "method": r.method}),
        r.continue_()
    ))

    page.on("dialog", lambda d: d.accept())
    page.locator(".btn.danger").click()
    page.wait_for_timeout(500)

    assert len(requests_made) > 0
    url = requests_made[0]["url"]
    assert "/api/wu/abort/" not in url, (
        f"BUG-01: abortSession() called the wrong endpoint: {url}"
    )


# ---------------------------------------------------------------------------
# BUG-02 — doAbortSession() uses 'session' as fallback WU id
# ---------------------------------------------------------------------------

@pytest.mark.xfail(
    strict=False,
    reason="BUG-02: doAbortSession() uses wuId='session' fallback, hitting a non-existent endpoint",
)
def test_bug02_deadlock_abort_with_no_wu_succeeds(
    page: Page, live_server: LiveServer, tmp_path
):
    repo = tmp_path / "deadlockabort"
    repo.mkdir()
    (repo / ".git").mkdir()
    sid = create_session_via_api(live_server, str(repo))
    seed_state(live_server, sid, _make_state({
        "WU-001": _wu("WU-001", status="failed", failure_count=2, needs_user_pivot=True),
    }))

    page.route("**/api/gather/start", lambda r: r.fulfill(
        status=200, content_type="application/json", body='{"reply": "Hi", "ok": true}',
    ))
    navigate_to_session(page, live_server, sid)
    page.wait_for_selector("#page-plan", state="visible", timeout=5000)

    page.evaluate("""
        () => {
            const comp = document.querySelector('#app')._x_dataStack?.[0];
            if (comp) {
                comp.reshapeModal.wu = null;
                comp.reshapeModal.deadlock = true;
                comp.reshapeModal.open = true;
            }
        }
    """)
    page.wait_for_timeout(300)

    abort_responses = []
    page.route("**/api/wu/**/pivot", lambda r: (
        abort_responses.append({"url": r.url}),
        r.fulfill(status=200, content_type="application/json", body='{"ok": true}'),
    ))

    abort_section = page.locator(".opt-section.danger")
    abort_section.locator("button.btn.danger").first.click()
    page.wait_for_timeout(200)
    abort_section.locator("button.btn.danger.primary").click()
    page.wait_for_timeout(500)

    assert abort_responses, "Abort should have fired a request"
    url = abort_responses[0]["url"]
    assert "/wu/session/" not in url, (
        f"BUG-02: doAbortSession() used 'session' as WU id in URL: {url}"
    )


# ---------------------------------------------------------------------------
# BUG-03 — openDiff() findIndex fallback is wrong
# ---------------------------------------------------------------------------

def test_bug03_open_diff_with_valid_focus_file_shows_correct_tab(
    page: Page, live_server: LiveServer, tmp_path
):
    impl = {
        "summary": "done",
        "files_touched": ["src/a.py", "src/b.py", "src/c.py"],
    }
    wus = {"WU-001": _wu("WU-001", status="completed", implementer_report=impl)}
    repo = tmp_path / "diffidx"
    repo.mkdir()
    (repo / ".git").mkdir()
    sid = create_session_via_api(live_server, str(repo))
    seed_state(live_server, sid, _make_state(wus))
    navigate_to_session(page, live_server, sid)
    page.wait_for_selector("#page-plan", state="visible", timeout=5000)

    page.evaluate("""
        () => {
            const comp = document.querySelector('#app')._x_dataStack?.[0];
            const wus = comp.state.work_units || {};
            const wu = {id: 'WU-001', ...wus['WU-001']};
            comp.openDiff(wu, 'src/b.py');
        }
    """)
    page.wait_for_timeout(300)

    active_idx = page.evaluate("""
        () => {
            const comp = document.querySelector('#app')._x_dataStack?.[0];
            return comp.diffModal.activeFile;
        }
    """)
    assert active_idx == 1, (
        f"Expected activeFile=1 for 'src/b.py', got {active_idx}"
    )


def test_bug03_open_diff_with_missing_focus_file_falls_back_to_zero(
    page: Page, live_server: LiveServer, tmp_path
):
    impl = {
        "summary": "done",
        "files_touched": ["src/a.py", "src/b.py"],
    }
    wus = {"WU-001": _wu("WU-001", status="completed", implementer_report=impl)}
    repo = tmp_path / "diffidxmiss"
    repo.mkdir()
    (repo / ".git").mkdir()
    sid = create_session_via_api(live_server, str(repo))
    seed_state(live_server, sid, _make_state(wus))
    navigate_to_session(page, live_server, sid)
    page.wait_for_selector("#page-plan", state="visible", timeout=5000)

    page.evaluate("""
        () => {
            const comp = document.querySelector('#app')._x_dataStack?.[0];
            const wus = comp.state.work_units || {};
            const wu = {id: 'WU-001', ...wus['WU-001']};
            comp.openDiff(wu, 'src/does_not_exist.py');
        }
    """)
    page.wait_for_timeout(300)

    active_idx = page.evaluate("""
        () => {
            const comp = document.querySelector('#app')._x_dataStack?.[0];
            return comp.diffModal.activeFile;
        }
    """)
    assert active_idx == 0, (
        f"Expected activeFile=0 for missing file (fallback), got {active_idx}"
    )


def test_bug03_diff_modal_opens_and_has_files(
    page: Page, live_server: LiveServer, tmp_path
):
    """Diff modal should open with file data for a completed WU."""
    impl = {"summary": "done", "files_touched": ["src/a.py"]}
    wus = {"WU-001": _wu("WU-001", status="completed", implementer_report=impl)}
    repo = tmp_path / "diffpanel"
    repo.mkdir()
    (repo / ".git").mkdir()
    sid = create_session_via_api(live_server, str(repo))
    seed_state(live_server, sid, _make_state(wus))
    navigate_to_session(page, live_server, sid)
    page.wait_for_selector("#page-plan", state="visible", timeout=5000)
    # Confirm WU card is rendered with the correct status
    page.wait_for_selector("#wu-WU-001", timeout=3000)

    page.locator("#wu-WU-001 button", has_text="View diff ↗").click()
    page.wait_for_timeout(500)

    # Check Alpine state: modal should be open with files
    modal_state = page.evaluate("""
        () => {
            const comp = document.querySelector('#app')._x_dataStack?.[0];
            return comp ? {open: comp.diffModal.open, fileCount: comp.diffModal.files.length} : null;
        }
    """)
    assert modal_state is not None, "Alpine component not found"
    assert modal_state["open"] is True, f"Diff modal should be open, got: {modal_state}"
    assert modal_state["fileCount"] == 1, f"Expected 1 file, got: {modal_state}"


# ---------------------------------------------------------------------------
# BUG-05 — State is loadable after session seeding (WS not required)
# ---------------------------------------------------------------------------

def test_bug05_state_fetch_returns_state_after_seed(
    page: Page, live_server: LiveServer, tmp_path
):
    """
    After seeding state, navigating to the session loads the plan page
    correctly — verifying the REST fetch path works regardless of WS.
    """
    repo = tmp_path / "wsstatetest"
    repo.mkdir()
    (repo / ".git").mkdir()
    sid = create_session_via_api(live_server, str(repo))
    wus = {"WU-001": _wu("WU-001", title="WS test", status="pending")}
    seed_state(live_server, sid, _make_state(wus))

    navigate_to_session(page, live_server, sid)
    page.wait_for_selector("#page-plan", state="visible", timeout=5000)

    state = page.evaluate("""
        () => {
            const comp = document.querySelector('#app')._x_dataStack?.[0];
            return comp ? comp.state : {};
        }
    """)
    assert state.get("work_units", {}).get("WU-001") is not None
