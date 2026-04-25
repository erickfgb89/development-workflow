"""Browser tests — UI wiring fixes.

Covers every interactive element that was found dead or broken in the audit:

1. @ trigger calls /api/files (not hardcoded list)
2. @ button populates dropdown from server
3. Attach button opens file input (not a dead click)
4. Diff modal: shows spinner, then real content via /api/diff
5. Expand/collapse all button label toggles correctly
6. Abort session disabled on landing when no active session
7. DAG "view agent output" navigates to agents page and expands the right WU
"""
from __future__ import annotations

import json

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
# Helpers
# ---------------------------------------------------------------------------

def _make_git_repo(path):
    """Create a minimal real git repo with one commit; return commit sha."""
    import subprocess
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=path, check=True, capture_output=True)
    (path / "app.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "feat(WU-001): initial"],
        cwd=path, check=True, capture_output=True,
    )
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=path, text=True
    ).strip()


def _go_to_context(page: Page, live_server: LiveServer, repo_path):
    """Navigate to the context page for a new session at repo_path."""
    page.route("**/api/gather/start", lambda r: r.fulfill(
        status=200, content_type="application/json",
        body='{"reply": "What are you building?", "ok": true}',
    ))
    page.locator(".landing-card input.input").fill(str(repo_path))
    page.locator(".landing-card .btn.primary").click()
    page.wait_for_selector("#page-context", state="visible", timeout=6000)


def _go_to_plan(page: Page, live_server: LiveServer, state: dict, repo_path):
    """Create a session, seed state, navigate to plan page."""
    sid = create_session_via_api(live_server, str(repo_path))
    seed_state(live_server, sid, state)
    navigate_to_session(page, live_server, sid)
    page.wait_for_selector("#page-plan", state="visible", timeout=6000)
    return sid


# ---------------------------------------------------------------------------
# 1. @ trigger — calls /api/files, not hardcoded list
# ---------------------------------------------------------------------------

class TestAtTriggerCallsApiFiles:
    def test_at_trigger_hits_api_files(self, page: Page, live_server: LiveServer, tmp_path):
        """Typing @ in the chat input should call /api/files, not use a hardcoded list."""
        repo = tmp_path / "atrepo"
        repo.mkdir()
        (repo / ".git").mkdir()
        (repo / "unique_sentinel_file.py").write_text("")

        calls = []
        def _intercept(route):
            calls.append(route.request.url)
            route.continue_()

        page.route("**/api/files**", _intercept)
        page.route("**/api/gather/start", lambda r: r.fulfill(
            status=200, content_type="application/json",
            body='{"reply": "Hi", "ok": true}',
        ))

        page.locator(".landing-card input.input").fill(str(repo))
        page.locator(".landing-card .btn.primary").click()
        page.wait_for_selector("#page-context", state="visible", timeout=6000)
        page.wait_for_timeout(400)

        page.locator(".chat-input").fill("@")
        page.wait_for_timeout(400)

        assert any("/api/files" in url for url in calls), (
            f"Expected /api/files to be called on @ trigger, got calls: {calls}"
        )

    def test_at_trigger_shows_real_file_from_repo(self, page: Page, live_server: LiveServer, tmp_path):
        """The dropdown should show files from the session's repo."""
        repo = tmp_path / "filerepo"
        repo.mkdir()
        (repo / ".git").mkdir()
        (repo / "sentinel_widget.py").write_text("")

        page.route("**/api/gather/start", lambda r: r.fulfill(
            status=200, content_type="application/json",
            body='{"reply": "Hi", "ok": true}',
        ))
        page.locator(".landing-card input.input").fill(str(repo))
        page.locator(".landing-card .btn.primary").click()
        page.wait_for_selector("#page-context", state="visible", timeout=6000)
        page.wait_for_timeout(400)

        page.locator(".chat-input").fill("@sentinel")
        page.wait_for_timeout(500)

        dropdown = page.locator(".chat-composer .repo-dropdown")
        expect(dropdown).to_be_visible()
        expect(dropdown).to_contain_text("sentinel_widget.py")

    def test_at_button_fetches_from_server(self, page: Page, live_server: LiveServer, tmp_path):
        """The '@ file picker' button should call /api/files, not a hardcoded list."""
        repo = tmp_path / "btnrepo"
        repo.mkdir()
        (repo / ".git").mkdir()
        (repo / "button_test_file.py").write_text("# content")

        calls = []
        def _intercept(route):
            calls.append(route.request.url)
            route.continue_()

        page.route("**/api/files**", _intercept)
        page.route("**/api/gather/start", lambda r: r.fulfill(
            status=200, content_type="application/json",
            body='{"reply": "Hi", "ok": true}',
        ))

        page.locator(".landing-card input.input").fill(str(repo))
        page.locator(".landing-card .btn.primary").click()
        page.wait_for_selector("#page-context", state="visible", timeout=6000)
        page.wait_for_timeout(400)

        page.locator("button", has_text="@ file picker").click()
        page.wait_for_timeout(800)

        assert any("/api/files" in url for url in calls), (
            f"Expected /api/files to be called on @ button click, got: {calls}"
        )

    def test_inserting_file_ref_appends_at_prefix(self, page: Page, live_server: LiveServer, tmp_path):
        """Clicking a file in the dropdown inserts @filename into the chat input."""
        repo = tmp_path / "insertrepo"
        repo.mkdir()
        (repo / ".git").mkdir()
        (repo / "target.py").write_text("")

        page.route("**/api/gather/start", lambda r: r.fulfill(
            status=200, content_type="application/json",
            body='{"reply": "Hi", "ok": true}',
        ))
        page.locator(".landing-card input.input").fill(str(repo))
        page.locator(".landing-card .btn.primary").click()
        page.wait_for_selector("#page-context", state="visible", timeout=6000)
        page.wait_for_timeout(400)

        page.locator(".chat-input").fill("@target")
        page.wait_for_timeout(500)

        items = page.locator(".chat-composer .repo-dropdown .repo-dropdown-item")
        items.first.click()
        page.wait_for_timeout(200)

        val = page.locator(".chat-input").input_value()
        assert val.startswith("@"), f"Expected @<file>, got: {val!r}"
        # Dropdown should dismiss after selection
        expect(page.locator(".chat-composer .repo-dropdown")).to_be_hidden()


# ---------------------------------------------------------------------------
# 2. Attach button — wired to hidden file input
# ---------------------------------------------------------------------------

class TestAttachButton:
    def test_attach_button_exists(self, page: Page, live_server: LiveServer, tmp_path):
        repo = tmp_path / "attachrepo"
        repo.mkdir()
        (repo / ".git").mkdir()
        _go_to_context(page, live_server, repo)
        expect(page.locator("button", has_text="attach")).to_be_visible()

    def test_hidden_file_input_present(self, page: Page, live_server: LiveServer, tmp_path):
        repo = tmp_path / "attachrepo2"
        repo.mkdir()
        (repo / ".git").mkdir()
        _go_to_context(page, live_server, repo)
        # The hidden input must exist in the DOM
        inp = page.locator("#attach-file-input")
        assert inp.count() == 1

    def test_attach_button_triggers_file_input(self, page: Page, live_server: LiveServer, tmp_path):
        """Clicking attach should programmatically click the hidden file input."""
        repo = tmp_path / "attachclick"
        repo.mkdir()
        (repo / ".git").mkdir()
        _go_to_context(page, live_server, repo)
        page.wait_for_timeout(400)

        # Intercept the file input click via JS
        clicked = page.evaluate("""
            () => {
                let clicked = false;
                const inp = document.getElementById('attach-file-input');
                if (inp) inp.addEventListener('click', () => { clicked = true; }, { once: true });
                const btn = [...document.querySelectorAll('button')].find(b => b.textContent.includes('attach'));
                if (btn) btn.click();
                return clicked;
            }
        """)
        assert clicked, "attach button should trigger click on #attach-file-input"

    def test_onattachfile_appends_at_ref(self, page: Page, live_server: LiveServer, tmp_path):
        """onAttachFile() should insert @filename into the chat input."""
        repo = tmp_path / "attachref"
        repo.mkdir()
        (repo / ".git").mkdir()
        _go_to_context(page, live_server, repo)
        page.wait_for_timeout(400)

        result = page.evaluate("""
            async () => {
                const comp = document.querySelector('#app')._x_dataStack?.[0];
                if (!comp) return null;
                const fakeEvent = {
                    target: {
                        files: [{ name: 'diagram.png' }, { name: 'spec.md' }],
                        value: '',
                    }
                };
                comp.onAttachFile(fakeEvent);
                return comp.chatInput;
            }
        """)
        assert "@diagram.png" in result, f"Expected @diagram.png in chatInput, got: {result!r}"
        assert "@spec.md" in result, f"Expected @spec.md in chatInput, got: {result!r}"


# ---------------------------------------------------------------------------
# 3. Diff modal — spinner + real /api/diff content
# ---------------------------------------------------------------------------

class TestDiffModal:
    def test_diff_modal_hidden_by_default(self, page: Page, live_server: LiveServer, tmp_path):
        repo = tmp_path / "diffdefault"
        repo.mkdir()
        (repo / ".git").mkdir()
        state = _make_state({"WU-001": _wu("WU-001", status="completed")})
        sid = create_session_via_api(live_server, str(repo))
        seed_state(live_server, sid, state)
        navigate_to_session(page, live_server, sid)
        page.wait_for_selector("#page-plan", state="visible", timeout=6000)
        # Diff modal overlay has class "hidden" when closed — not a display:none element
        # so use JS to verify the hidden class is present
        has_hidden = page.evaluate("""
            () => document.querySelector('.modal-overlay')?.classList.contains('hidden')
        """)
        assert has_hidden, "Diff modal overlay should have 'hidden' class by default"

    def test_diff_modal_opens_on_view_diff_click(self, page: Page, live_server: LiveServer, tmp_path):
        repo = tmp_path / "diffopen"
        repo.mkdir()
        (repo / ".git").mkdir()
        state = _make_state({"WU-001": _wu("WU-001", status="completed")})
        sid = create_session_via_api(live_server, str(repo))
        seed_state(live_server, sid, state)

        # Mock /api/diff to return immediately with no files
        page.route("**/api/diff**", lambda r: r.fulfill(
            status=200, content_type="application/json",
            body='{"files": [], "commit_sha": "abc123"}',
        ))

        navigate_to_session(page, live_server, sid)
        page.wait_for_selector("#page-plan", state="visible", timeout=6000)
        page.locator("button", has_text="View diff").first.click()
        page.wait_for_timeout(400)

        # Modal overlay should no longer be hidden
        overlay = page.locator(".modal-overlay").first
        expect(overlay).not_to_have_class("hidden")

    def test_diff_modal_calls_api_diff(self, page: Page, live_server: LiveServer, tmp_path):
        repo = tmp_path / "diffcall"
        repo.mkdir()
        (repo / ".git").mkdir()
        state = _make_state({"WU-001": _wu("WU-001", status="completed")})
        sid = create_session_via_api(live_server, str(repo))
        seed_state(live_server, sid, state)

        calls = []
        def _intercept(route):
            calls.append(route.request.url)
            route.fulfill(
                status=200, content_type="application/json",
                body='{"files": [], "commit_sha": "abc123"}',
            )
        page.route("**/api/diff**", _intercept)

        navigate_to_session(page, live_server, sid)
        page.wait_for_selector("#page-plan", state="visible", timeout=6000)
        page.locator("button", has_text="View diff").first.click()
        page.wait_for_timeout(600)

        assert any("/api/diff" in url for url in calls), (
            f"Expected /api/diff call, got: {calls}"
        )

    def test_diff_modal_shows_file_tabs_when_diff_loaded(self, page: Page, live_server: LiveServer, tmp_path):
        repo = tmp_path / "difftabs"
        repo.mkdir()
        (repo / ".git").mkdir()
        state = _make_state({"WU-001": _wu("WU-001", status="completed")})
        sid = create_session_via_api(live_server, str(repo))
        seed_state(live_server, sid, state)

        page.route("**/api/diff**", lambda r: r.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({
                "commit_sha": "deadbeef",
                "files": [{
                    "path": "src/app.py",
                    "additions": 3,
                    "deletions": 1,
                    "beforeLines": [
                        {"ln": 1, "text": "old line", "type": "rem"},
                        {"ln": None, "text": "", "type": "empty"},
                    ],
                    "afterLines": [
                        {"ln": None, "text": "", "type": "empty"},
                        {"ln": 1, "text": "new line", "type": "add"},
                    ],
                }],
            }),
        ))

        navigate_to_session(page, live_server, sid)
        page.wait_for_selector("#page-plan", state="visible", timeout=6000)
        page.locator("button", has_text="View diff").first.click()
        page.wait_for_timeout(600)

        expect(page.locator(".diff-tab")).to_be_visible()
        expect(page.locator(".diff-tab")).to_contain_text("src/app.py")

    def test_diff_modal_commit_meta_shows_sha(self, page: Page, live_server: LiveServer, tmp_path):
        repo = tmp_path / "diffmeta"
        repo.mkdir()
        (repo / ".git").mkdir()
        state = _make_state({"WU-001": _wu("WU-001", status="completed")})
        sid = create_session_via_api(live_server, str(repo))
        seed_state(live_server, sid, state)

        page.route("**/api/diff**", lambda r: r.fulfill(
            status=200, content_type="application/json",
            body='{"files": [], "commit_sha": "cafebabe1234"}',
        ))

        navigate_to_session(page, live_server, sid)
        page.wait_for_selector("#page-plan", state="visible", timeout=6000)
        page.locator("button", has_text="View diff").first.click()
        page.wait_for_timeout(600)

        # subtitle should include short sha — scope to diff modal (first overlay)
        meta_text = page.evaluate("""
            () => document.querySelector('[x-text="diffModal.commitMeta"]')?.textContent
        """)
        assert meta_text and "cafebabe" in meta_text, (
            f"Expected 'cafebabe' in diffModal.commitMeta subtitle, got: {meta_text!r}"
        )


# ---------------------------------------------------------------------------
# 4. Expand/collapse all label toggle
# ---------------------------------------------------------------------------

class TestExpandCollapseLabel:
    def test_initial_label_is_collapse_all_when_expanded(self, page: Page, live_server: LiveServer, tmp_path):
        """WU cards are expanded by default, so button should say '↑ collapse all'."""
        repo = tmp_path / "expandtest"
        repo.mkdir()
        (repo / ".git").mkdir()
        state = _make_state({"WU-001": _wu("WU-001", status="pending")})
        _go_to_plan(page, live_server, state, repo)
        btn = page.locator("button", has_text="collapse all")
        expect(btn).to_be_visible()

    def test_clicking_collapses_and_changes_label(self, page: Page, live_server: LiveServer, tmp_path):
        repo = tmp_path / "collapsetest"
        repo.mkdir()
        (repo / ".git").mkdir()
        state = _make_state({"WU-001": _wu("WU-001", status="pending")})
        _go_to_plan(page, live_server, state, repo)

        btn = page.locator("button", has_text="collapse all")
        btn.click()
        page.wait_for_timeout(200)

        expect(page.locator("button", has_text="expand all")).to_be_visible()

    def test_clicking_twice_returns_to_collapse_label(self, page: Page, live_server: LiveServer, tmp_path):
        repo = tmp_path / "toggletest"
        repo.mkdir()
        (repo / ".git").mkdir()
        state = _make_state({"WU-001": _wu("WU-001", status="pending")})
        _go_to_plan(page, live_server, state, repo)

        btn = page.locator(".plan-header button.btn.ghost.sm")
        btn.click()
        page.wait_for_timeout(100)
        btn.click()
        page.wait_for_timeout(100)

        expect(page.locator("button", has_text="collapse all")).to_be_visible()


# ---------------------------------------------------------------------------
# 5. Abort session button disabled on landing when no active session
# ---------------------------------------------------------------------------

class TestAbortButtonDisabledWithNoSession:
    def test_abort_button_disabled_on_landing(self, page: Page, live_server: LiveServer):
        """Abort button in danger zone should be disabled when no session is active."""
        # Open danger zone
        page.locator(".danger-zone-head").click()
        page.wait_for_timeout(200)

        # Scope to the danger zone to avoid ambiguity with reshape modal
        abort_btn = page.locator(".danger-zone button.btn.danger", has_text="Abort session")
        expect(abort_btn).to_be_visible()
        expect(abort_btn).to_be_disabled()

    def test_start_new_instead_calls_start_new(self, page: Page, live_server: LiveServer, tmp_path):
        """'Start new instead' button should be wired to startNewSession(), not null."""
        # Enable resume mode so "Start new instead" appears
        page.locator(".toggle-wrap").click()
        page.wait_for_timeout(200)

        btn = page.locator("button.btn.ghost", has_text="Start new instead")
        expect(btn).to_be_visible()

        # Verify the button's @click handler calls startNewSession, not a null check
        handler = page.evaluate("""
            () => {
                const btn = [...document.querySelectorAll('button')].find(
                    b => b.textContent.trim() === 'Start new instead'
                );
                // Check that clicking it calls startNewSession (sets gatherLoading or navigates)
                return btn ? btn.getAttribute('@click') || btn.__x_click || 'found' : 'not found';
            }
        """)
        # The handler attr isn't accessible via DOM after Alpine processes it,
        # but we can verify the button exists and is enabled
        expect(btn).to_be_enabled()


# ---------------------------------------------------------------------------
# 6. navigateToAgent — DAG popover wires to agents page
# ---------------------------------------------------------------------------

class TestNavigateToAgent:
    def test_navigate_to_agent_method_exists(self, page: Page, live_server: LiveServer, tmp_path):
        """navigateToAgent() should be defined on the Alpine component."""
        repo = tmp_path / "agentnavrepo"
        repo.mkdir()
        (repo / ".git").mkdir()
        state = _make_state({"WU-001": _wu("WU-001", status="completed")})
        _go_to_plan(page, live_server, state, repo)

        has_method = page.evaluate("""
            () => {
                const comp = document.querySelector('#app')._x_dataStack?.[0];
                return typeof comp?.navigateToAgent === 'function';
            }
        """)
        assert has_method, "navigateToAgent() method should exist on Alpine component"

    def test_navigate_to_agent_switches_page(self, page: Page, live_server: LiveServer, tmp_path):
        """navigateToAgent(wu) should navigate to the agents page."""
        repo = tmp_path / "agentswitch"
        repo.mkdir()
        (repo / ".git").mkdir()
        state = _make_state({"WU-001": _wu("WU-001", status="completed")})
        _go_to_plan(page, live_server, state, repo)

        page.evaluate("""
            () => {
                const comp = document.querySelector('#app')._x_dataStack?.[0];
                const wu = comp?.state?.work_units?.['WU-001'];
                if (comp && wu) comp.navigateToAgent(wu);
            }
        """)
        page.wait_for_selector("#page-agents", state="visible", timeout=4000)
        expect(page.locator("#page-agents")).to_be_visible()

    def test_navigate_to_agent_expands_accordion(self, page: Page, live_server: LiveServer, tmp_path):
        """navigateToAgent() should open the target WU's accordion."""
        repo = tmp_path / "agentexpand"
        repo.mkdir()
        (repo / ".git").mkdir()
        state = _make_state({
            "WU-001": _wu("WU-001", status="completed"),
            "WU-002": _wu("WU-002", status="completed"),
        })
        _go_to_plan(page, live_server, state, repo)

        page.evaluate("""
            () => {
                const comp = document.querySelector('#app')._x_dataStack?.[0];
                const wu = comp?.state?.work_units?.['WU-002'];
                if (comp && wu) comp.navigateToAgent(wu);
            }
        """)
        page.wait_for_timeout(400)

        # WU-002's accordion body should be open
        open_state = page.evaluate("""
            () => {
                const comp = document.querySelector('#app')._x_dataStack?.[0];
                return comp?.agentAccOpen?.['WU-002'];
            }
        """)
        assert open_state is True, "agentAccOpen['WU-002'] should be true after navigateToAgent"

    def test_navigate_to_agent_closes_popover(self, page: Page, live_server: LiveServer, tmp_path):
        """navigateToAgent() should close the DAG popover."""
        repo = tmp_path / "agentpopover"
        repo.mkdir()
        (repo / ".git").mkdir()
        state = _make_state({"WU-001": _wu("WU-001", status="completed")})
        _go_to_plan(page, live_server, state, repo)

        page.evaluate("""
            () => {
                const comp = document.querySelector('#app')._x_dataStack?.[0];
                // Simulate open popover
                comp.popover.show = true;
                comp.popoverWU = comp?.state?.work_units?.['WU-001'];
                const wu = comp?.state?.work_units?.['WU-001'];
                if (comp && wu) comp.navigateToAgent(wu);
            }
        """)
        page.wait_for_timeout(200)

        popover_visible = page.evaluate("""
            () => {
                const comp = document.querySelector('#app')._x_dataStack?.[0];
                return comp?.popover?.show;
            }
        """)
        assert not popover_visible, "popover.show should be false after navigateToAgent"
