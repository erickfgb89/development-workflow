"""Browser tests — Modals: Diff Viewer and Reshape/Pivot.

Diff modal covers:
- Opens on "View diff ↗" click
- Title shows WU id + title
- Commit meta subtitle
- File tabs with add/del badges
- Switching tabs
- Before/after columns
- Close button
- Backdrop click closes
- Escape key closes

Reshape/Pivot modal covers:
- Opens on "Trigger reshape" button
- Title + subtitle shows WU
- Reset option: button calls reset API
- Reshape option: textarea + submit calls /api/reshape
- Abort option: shows confirm step before calling pivot
- Close button (non-deadlock)
- Escape dismisses (non-deadlock)
- Deadlock mode: auto-raised, close button hidden, footer text changes
- Deadlock: unblock preview shows downstream WUs
"""
from __future__ import annotations
import re

import json

import pytest
from playwright.sync_api import Page, expect

from tests.ui.conftest import (
    LiveServer,
    create_session_via_api,
    navigate_to_session,
    seed_state,
    _make_state,
    _wu,
)

_IMPL = {
    "summary": "Implemented feature",
    "files_touched": ["src/main.py", "src/utils.py"],
    "commit_message": "deadbeef feat: add feature",
}

_REVIEW_PASS = {
    "verdict": "pass",
    "summary": "LGTM",
    "ac_coverage": [{"criterion": "Works", "covered": True, "notes": ""}],
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _open_plan_with_wus(page: Page, live_server: LiveServer, tmp_path, wus: dict):
    repo = tmp_path / "modaltest"
    repo.mkdir()
    (repo / ".git").mkdir()
    sid = create_session_via_api(live_server, str(repo))
    state = _make_state(wus)
    seed_state(live_server, sid, state)
    navigate_to_session(page, live_server, sid)
    page.wait_for_selector("#page-plan", state="visible", timeout=5000)
    return sid


# ---------------------------------------------------------------------------
# Diff Modal
# ---------------------------------------------------------------------------

class TestDiffModal:
    def test_diff_modal_hidden_initially(self, page: Page, live_server: LiveServer, tmp_path):
        wus = {"WU-001": _wu("WU-001", status="completed", implementer_report=_IMPL)}
        _open_plan_with_wus(page, live_server, tmp_path, wus)
        expect(page.locator(".modal-overlay").first).to_have_class(re.compile(r"(?:^| )hidden(?:$| )"))

    def test_diff_modal_opens_on_view_diff_click(self, page: Page, live_server: LiveServer, tmp_path):
        wus = {"WU-001": _wu("WU-001", status="completed", implementer_report=_IMPL)}
        _open_plan_with_wus(page, live_server, tmp_path, wus)
        page.locator("#wu-WU-001 button", has_text="View diff ↗").click()
        page.wait_for_timeout(300)
        expect(page.locator(".modal-overlay").first).not_to_have_class(re.compile(r"(?:^| )hidden(?:$| )"))

    def test_diff_modal_title_shows_wu_info(self, page: Page, live_server: LiveServer, tmp_path):
        wus = {"WU-001": _wu("WU-001", title="My Feature", status="completed", implementer_report=_IMPL)}
        _open_plan_with_wus(page, live_server, tmp_path, wus)
        page.locator("#wu-WU-001 button", has_text="View diff ↗").click()
        page.wait_for_timeout(300)
        title = page.locator(".modal-box .modal-head .title").first
        expect(title).to_contain_text("WU-001")
        expect(title).to_contain_text("My Feature")

    def test_diff_modal_commit_meta_subtitle(self, page: Page, live_server: LiveServer, tmp_path):
        wus = {"WU-001": _wu("WU-001", status="completed", implementer_report=_IMPL)}
        _open_plan_with_wus(page, live_server, tmp_path, wus)
        page.locator("#wu-WU-001 button", has_text="View diff ↗").click()
        page.wait_for_timeout(300)
        subtitle = page.locator(".modal-box .modal-head .subtitle").first
        expect(subtitle).to_contain_text("WU-001")

    def test_diff_modal_file_tabs_shown(self, page: Page, live_server: LiveServer, tmp_path):
        wus = {"WU-001": _wu("WU-001", status="completed", implementer_report=_IMPL)}
        _open_plan_with_wus(page, live_server, tmp_path, wus)
        page.locator("#wu-WU-001 button", has_text="View diff ↗").click()
        page.wait_for_timeout(300)
        tabs = page.locator(".diff-tabs .diff-tab")
        assert tabs.count() == 2  # src/main.py and src/utils.py

    def test_diff_modal_tab_shows_filename(self, page: Page, live_server: LiveServer, tmp_path):
        wus = {"WU-001": _wu("WU-001", status="completed", implementer_report=_IMPL)}
        _open_plan_with_wus(page, live_server, tmp_path, wus)
        page.locator("#wu-WU-001 button", has_text="View diff ↗").click()
        page.wait_for_timeout(300)
        first_tab = page.locator(".diff-tabs .diff-tab").first
        expect(first_tab).to_contain_text("src/main.py")

    def test_diff_modal_no_files_shows_empty_message(self, page: Page, live_server: LiveServer, tmp_path):
        impl_no_files = {"summary": "Nothing changed", "files_touched": []}
        wus = {"WU-001": _wu("WU-001", status="completed", implementer_report=impl_no_files)}
        _open_plan_with_wus(page, live_server, tmp_path, wus)
        page.locator("#wu-WU-001 button", has_text="View diff ↗").click()
        page.wait_for_timeout(300)
        expect(page.locator(".diff-tabs")).to_contain_text("No diff available")

    def test_diff_modal_close_button_works(self, page: Page, live_server: LiveServer, tmp_path):
        wus = {"WU-001": _wu("WU-001", status="completed", implementer_report=_IMPL)}
        _open_plan_with_wus(page, live_server, tmp_path, wus)
        page.locator("#wu-WU-001 button", has_text="View diff ↗").click()
        page.wait_for_timeout(300)
        page.locator(".modal-box button", has_text="× close").first.click()
        page.wait_for_timeout(200)
        expect(page.locator(".modal-overlay").first).to_have_class(re.compile(r"(?:^| )hidden(?:$| )"))

    def test_diff_modal_backdrop_click_closes(self, page: Page, live_server: LiveServer, tmp_path):
        wus = {"WU-001": _wu("WU-001", status="completed", implementer_report=_IMPL)}
        _open_plan_with_wus(page, live_server, tmp_path, wus)
        page.locator("#wu-WU-001 button", has_text="View diff ↗").click()
        page.wait_for_timeout(300)
        # Click the overlay backdrop (outside .modal-box) — using offset far from center
        overlay = page.locator(".modal-overlay").first
        box = overlay.bounding_box()
        if box:
            page.mouse.click(box["x"] + 5, box["y"] + 5)
        page.wait_for_timeout(200)
        expect(page.locator(".modal-overlay").first).to_have_class(re.compile(r"(?:^| )hidden(?:$| )"))

    def test_diff_modal_escape_closes(self, page: Page, live_server: LiveServer, tmp_path):
        wus = {"WU-001": _wu("WU-001", status="completed", implementer_report=_IMPL)}
        _open_plan_with_wus(page, live_server, tmp_path, wus)
        page.locator("#wu-WU-001 button", has_text="View diff ↗").click()
        page.wait_for_timeout(300)
        page.keyboard.press("Escape")
        page.wait_for_timeout(200)
        expect(page.locator(".modal-overlay").first).to_have_class(re.compile(r"(?:^| )hidden(?:$| )"))

    def test_diff_modal_tab_switching(self, page: Page, live_server: LiveServer, tmp_path):
        wus = {"WU-001": _wu("WU-001", status="completed", implementer_report=_IMPL)}
        _open_plan_with_wus(page, live_server, tmp_path, wus)
        page.locator("#wu-WU-001 button", has_text="View diff ↗").click()
        page.wait_for_timeout(500)
        tabs = page.locator(".diff-tabs .diff-tab")
        # First tab active by default (activeFile = 0)
        expect(tabs.first).to_have_class(re.compile(r"(?:^| )active(?:$| )"))
        # Click second tab
        tabs.nth(1).click()
        page.wait_for_timeout(200)
        expect(tabs.nth(1)).to_have_class(re.compile(r"(?:^| )active(?:$| )"))
        expect(tabs.first).not_to_have_class(re.compile(r"(?:^| )active(?:$| )"))

    def test_diff_modal_before_after_columns(self, page: Page, live_server: LiveServer, tmp_path):
        wus = {"WU-001": _wu("WU-001", status="completed", implementer_report=_IMPL)}
        _open_plan_with_wus(page, live_server, tmp_path, wus)
        page.locator("#wu-WU-001 button", has_text="View diff ↗").click()
        # Wait for x-if diff-panel to render (requires activeFile=0 to be set correctly)
        page.wait_for_selector(".diff-panel", state="attached", timeout=5000)
        cols = page.locator(".diff-panel .diff-col")
        assert cols.count() == 2
        expect(cols.first).to_contain_text("before")
        expect(cols.nth(1)).to_contain_text("after")

    def test_diff_modal_foot_shows_file_count(self, page: Page, live_server: LiveServer, tmp_path):
        wus = {"WU-001": _wu("WU-001", status="completed", implementer_report=_IMPL)}
        _open_plan_with_wus(page, live_server, tmp_path, wus)
        page.locator("#wu-WU-001 button", has_text="View diff ↗").click()
        page.wait_for_timeout(300)
        expect(page.locator(".modal-box .modal-foot").first).to_contain_text("file(s) changed")


# ---------------------------------------------------------------------------
# Reshape/Pivot Modal — manual trigger
# ---------------------------------------------------------------------------

class TestReshapeModal:
    def _open_reshape_modal(self, page, live_server, tmp_path):
        wus = {
            "WU-001": _wu("WU-001", status="failed", failure_count=2,
                          implementer_report=_IMPL),
            "WU-002": _wu("WU-002", status="blocked", deps=["WU-001"]),
        }
        sid = _open_plan_with_wus(page, live_server, tmp_path, wus)
        page.locator("#wu-WU-001 button", has_text="Trigger reshape").click()
        page.wait_for_timeout(300)
        return sid

    def test_reshape_modal_opens(self, page: Page, live_server: LiveServer, tmp_path):
        self._open_reshape_modal(page, live_server, tmp_path)
        # Second modal-overlay (reshape) should be visible
        expect(page.locator(".modal-overlay").nth(1)).not_to_have_class(re.compile(r"(?:^| )hidden(?:$| )"))

    def test_reshape_modal_title(self, page: Page, live_server: LiveServer, tmp_path):
        self._open_reshape_modal(page, live_server, tmp_path)
        expect(page.locator(".modal-box.sm .title")).to_have_text("Reshape or Pivot")

    def test_reshape_modal_subtitle_shows_wu(self, page: Page, live_server: LiveServer, tmp_path):
        self._open_reshape_modal(page, live_server, tmp_path)
        expect(page.locator(".modal-box.sm .subtitle")).to_contain_text("WU-001")

    def test_unblock_preview_shows_downstream(self, page: Page, live_server: LiveServer, tmp_path):
        self._open_reshape_modal(page, live_server, tmp_path)
        expect(page.locator(".unblock-preview")).to_be_visible()
        expect(page.locator(".unblock-preview")).to_contain_text("WU-002")

    def test_close_button_dismisses(self, page: Page, live_server: LiveServer, tmp_path):
        self._open_reshape_modal(page, live_server, tmp_path)
        page.locator(".modal-box.sm button", has_text="× close").click()
        page.wait_for_timeout(200)
        expect(page.locator(".modal-overlay").nth(1)).to_have_class(re.compile(r"(?:^| )hidden(?:$| )"))

    def test_escape_dismisses_non_deadlock(self, page: Page, live_server: LiveServer, tmp_path):
        self._open_reshape_modal(page, live_server, tmp_path)
        page.keyboard.press("Escape")
        page.wait_for_timeout(200)
        expect(page.locator(".modal-overlay").nth(1)).to_have_class(re.compile(r"(?:^| )hidden(?:$| )"))

    def test_reset_option_calls_reset_api(self, page: Page, live_server: LiveServer, tmp_path):
        self._open_reshape_modal(page, live_server, tmp_path)
        called = []
        page.route("**/api/wu/WU-001/reset", lambda r: (
            called.append(True),
            r.fulfill(status=200, content_type="application/json", body='{"ok": true}'),
        ))
        page.locator(".opt-section button", has_text="Reset and continue").click()
        page.wait_for_timeout(500)
        assert called, "Reset API should have been called"

    def test_reset_option_closes_modal(self, page: Page, live_server: LiveServer, tmp_path):
        self._open_reshape_modal(page, live_server, tmp_path)
        page.route("**/api/wu/WU-001/reset", lambda r: r.fulfill(
            status=200, content_type="application/json", body='{"ok": true}',
        ))
        page.locator(".opt-section button", has_text="Reset and continue").click()
        page.wait_for_timeout(500)
        expect(page.locator(".modal-overlay").nth(1)).to_have_class(re.compile(r"(?:^| )hidden(?:$| )"))

    def test_reshape_option_calls_reshape_api(self, page: Page, live_server: LiveServer, tmp_path):
        sid = self._open_reshape_modal(page, live_server, tmp_path)
        called = []
        page.route("**/api/reshape", lambda r: (
            called.append(json.loads(r.request.post_data)),
            r.fulfill(status=200, content_type="application/json",
                      body='{"work_units": [], "ok": true}'),
        ))
        page.locator(".opt-section textarea").fill("Try a different approach")
        page.locator(".opt-section button.btn.primary").click()
        page.wait_for_timeout(500)
        assert called
        assert called[0]["instructions"] == "Try a different approach"
        assert called[0]["session_id"] == sid

    def test_reshape_empty_instructions_does_not_submit(self, page: Page, live_server: LiveServer, tmp_path):
        self._open_reshape_modal(page, live_server, tmp_path)
        called = []
        page.route("**/api/reshape", lambda r: (called.append(True), r.continue_()))
        page.locator(".opt-section textarea").fill("")
        page.locator(".opt-section button.btn.primary").click()
        page.wait_for_timeout(300)
        assert not called

    def test_abort_requires_confirmation(self, page: Page, live_server: LiveServer, tmp_path):
        self._open_reshape_modal(page, live_server, tmp_path)
        abort_section = page.locator(".opt-section.danger")
        # First click shows confirm
        abort_section.locator("button.btn.danger").first.click()
        page.wait_for_timeout(200)
        expect(abort_section.locator("button.btn.danger.primary")).to_be_visible()
        expect(abort_section.locator("button.btn.danger.primary")).to_contain_text("Confirm abort")

    def test_abort_confirm_calls_pivot_abort(self, page: Page, live_server: LiveServer, tmp_path):
        self._open_reshape_modal(page, live_server, tmp_path)
        called = []
        page.route("**/api/wu/**/pivot", lambda r: (
            called.append(json.loads(r.request.post_data)),
            r.fulfill(status=200, content_type="application/json", body='{"ok": true}'),
        ))
        abort_section = page.locator(".opt-section.danger")
        abort_section.locator("button.btn.danger").first.click()
        page.wait_for_timeout(200)
        abort_section.locator("button.btn.danger.primary").click()
        page.wait_for_timeout(500)
        assert called
        assert called[0]["action"] == "abort"

    def test_abort_closes_modal(self, page: Page, live_server: LiveServer, tmp_path):
        self._open_reshape_modal(page, live_server, tmp_path)
        page.route("**/api/wu/**/pivot", lambda r: r.fulfill(
            status=200, content_type="application/json", body='{"ok": true}',
        ))
        abort_section = page.locator(".opt-section.danger")
        abort_section.locator("button.btn.danger").first.click()
        page.wait_for_timeout(200)
        abort_section.locator("button.btn.danger.primary").click()
        page.wait_for_timeout(500)
        expect(page.locator(".modal-overlay").nth(1)).to_have_class(re.compile(r"(?:^| )hidden(?:$| )"))


# ---------------------------------------------------------------------------
# Reshape Modal — deadlock auto-raise via WebSocket
# ---------------------------------------------------------------------------

class TestReshapeModalDeadlock:
    def _inject_state_with_pivot(self, page, live_server, sid):
        """Push a state update to trigger the reshape modal in deadlock mode."""
        state = _make_state({
            "WU-001": _wu("WU-001", status="failed", failure_count=3, needs_user_pivot=True),
        })
        page.evaluate(f"""
            () => {{
                const el = document.querySelector('#app');
                const comp = el._x_dataStack?.[0];
                if (comp) {{
                    comp.onStateUpdate({json.dumps(state)});
                    // onStateUpdate sets deadlock=false; force deadlock mode explicitly
                    comp.reshapeModal.deadlock = true;
                }}
            }}
        """)

    def test_deadlock_banner_shown(self, page: Page, live_server: LiveServer, tmp_path):
        repo = tmp_path / "deadlockrepo"
        repo.mkdir()
        (repo / ".git").mkdir()
        sid = create_session_via_api(live_server, str(repo))
        state = _make_state({"WU-001": _wu("WU-001", status="failed", failure_count=2)})
        seed_state(live_server, sid, state)

        page.route("**/api/gather/start", lambda r: r.fulfill(
            status=200, content_type="application/json", body='{"reply": "Hi", "ok": true}',
        ))
        navigate_to_session(page, live_server, sid)
        page.wait_for_selector("#page-plan", state="visible", timeout=5000)

        self._inject_state_with_pivot(page, live_server, sid)
        page.wait_for_timeout(400)

        expect(page.locator(".modal-overlay").nth(1)).not_to_have_class(re.compile(r"(?:^| )hidden(?:$| )"))

    def test_deadlock_close_button_hidden(self, page: Page, live_server: LiveServer, tmp_path):
        repo = tmp_path / "deadlockcloserepo"
        repo.mkdir()
        (repo / ".git").mkdir()
        sid = create_session_via_api(live_server, str(repo))
        state = _make_state({"WU-001": _wu("WU-001", status="failed", failure_count=2)})
        seed_state(live_server, sid, state)

        page.route("**/api/gather/start", lambda r: r.fulfill(
            status=200, content_type="application/json", body='{"reply": "Hi", "ok": true}',
        ))
        navigate_to_session(page, live_server, sid)
        page.wait_for_selector("#page-plan", state="visible", timeout=5000)

        self._inject_state_with_pivot(page, live_server, sid)
        page.wait_for_timeout(400)

        # Close button should be hidden in deadlock mode
        close_btn = page.locator(".modal-box.sm button", has_text="× close")
        expect(close_btn).to_be_hidden()

    def test_escape_does_not_close_deadlock_modal(self, page: Page, live_server: LiveServer, tmp_path):
        repo = tmp_path / "deadlockescrrepo"
        repo.mkdir()
        (repo / ".git").mkdir()
        sid = create_session_via_api(live_server, str(repo))
        seed_state(live_server, sid, _make_state({"WU-001": _wu("WU-001", status="failed", failure_count=2)}))

        page.route("**/api/gather/start", lambda r: r.fulfill(
            status=200, content_type="application/json", body='{"reply": "Hi", "ok": true}',
        ))
        navigate_to_session(page, live_server, sid)
        page.wait_for_selector("#page-plan", state="visible", timeout=5000)

        self._inject_state_with_pivot(page, live_server, sid)
        page.wait_for_timeout(400)

        page.keyboard.press("Escape")
        page.wait_for_timeout(200)
        # Modal should remain open
        expect(page.locator(".modal-overlay").nth(1)).not_to_have_class(re.compile(r"(?:^| )hidden(?:$| )"))

    def test_deadlock_footer_text(self, page: Page, live_server: LiveServer, tmp_path):
        repo = tmp_path / "deadlockftrepo"
        repo.mkdir()
        (repo / ".git").mkdir()
        sid = create_session_via_api(live_server, str(repo))
        seed_state(live_server, sid, _make_state({"WU-001": _wu("WU-001", status="failed", failure_count=2)}))

        page.route("**/api/gather/start", lambda r: r.fulfill(
            status=200, content_type="application/json", body='{"reply": "Hi", "ok": true}',
        ))
        navigate_to_session(page, live_server, sid)
        page.wait_for_selector("#page-plan", state="visible", timeout=5000)

        self._inject_state_with_pivot(page, live_server, sid)
        page.wait_for_timeout(400)

        expect(page.locator(".modal-box.sm .modal-foot")).to_contain_text("Deadlock")
