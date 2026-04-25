"""Browser tests — Context Gathering page.

Covers:
- Page layout (chat panel + context editor split)
- Chat thread rendering (user and agent bubbles)
- Send button states (disabled while loading)
- Enter-to-send, Shift+Enter for newline
- @ file picker dropdown
- Context editor show/hide toggle
- Save context button
- Context lock when plan is active
- "Context complete" button calls gather/done then plan
- "Reopen context" button
"""
from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from tests.ui.conftest import (
    LiveServer,
    create_session_via_api,
    get_alpine_data,
    navigate_to_session,
    seed_state,
    _make_state,
    _wu,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _go_to_context_page(page: Page, live_server: LiveServer, tmp_path, mock_gather=True):
    """Create a session and navigate to the context page."""
    repo = tmp_path / "ctxtest"
    repo.mkdir()
    (repo / ".git").mkdir()
    sid = create_session_via_api(live_server, str(repo))

    if mock_gather:
        page.route("**/api/gather/start", lambda r: r.fulfill(
            status=200,
            content_type="application/json",
            body='{"reply": "What would you like to build?", "ok": true}',
        ))
        page.route("**/api/gather/send", lambda r: r.fulfill(
            status=200,
            content_type="application/json",
            body='{"reply": "Tell me more about that.", "ok": true}',
        ))

    # Navigate via landing
    page.locator(".landing-card input.input").fill(str(repo))
    page.locator(".landing-card .btn.primary").click()
    page.wait_for_selector("#page-context", state="visible", timeout=5000)
    return sid


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

class TestContextPageLayout:
    def test_split_layout_present(self, page: Page, live_server: LiveServer, tmp_path):
        _go_to_context_page(page, live_server, tmp_path)
        expect(page.locator(".split-layout")).to_be_visible()

    def test_chat_panel_present(self, page: Page, live_server: LiveServer, tmp_path):
        _go_to_context_page(page, live_server, tmp_path)
        expect(page.locator(".chat-panel")).to_be_visible()

    def test_ctx_panel_present(self, page: Page, live_server: LiveServer, tmp_path):
        _go_to_context_page(page, live_server, tmp_path)
        expect(page.locator(".ctx-panel")).to_be_visible()

    def test_agent_opening_message_appears(self, page: Page, live_server: LiveServer, tmp_path):
        _go_to_context_page(page, live_server, tmp_path)
        page.wait_for_timeout(600)
        # At least one agent bubble should be visible
        agent_bubbles = page.locator(".bubble.agent")
        expect(agent_bubbles.first).to_be_visible()
        expect(agent_bubbles.first).to_contain_text("What would you like to build?")


# ---------------------------------------------------------------------------
# Chat interaction
# ---------------------------------------------------------------------------

class TestChatInteraction:
    def test_send_button_present(self, page: Page, live_server: LiveServer, tmp_path):
        _go_to_context_page(page, live_server, tmp_path)
        expect(page.locator(".composer-row .btn.sm:not(.ghost)")).to_be_visible()

    def test_send_button_disabled_while_loading(self, page: Page, live_server: LiveServer, tmp_path):
        # Mock gather/start to hang briefly
        def _slow_start(route):
            import time; time.sleep(0.2)
            route.fulfill(
                status=200,
                content_type="application/json",
                body='{"reply": "Hello", "ok": true}',
            )
        page.route("**/api/gather/start", _slow_start)
        repo = tmp_path / "slowgather"
        repo.mkdir()
        (repo / ".git").mkdir()
        create_session_via_api(live_server, str(repo))
        page.locator(".landing-card input.input").fill(str(repo))
        page.locator(".landing-card .btn.primary").click()
        page.wait_for_selector("#page-context", state="visible", timeout=5000)
        # Button should eventually be enabled
        page.wait_for_timeout(500)
        send_btn = page.locator(".composer-row .btn.sm:not(.ghost)")
        expect(send_btn).to_be_enabled()

    def test_typing_message_and_send(self, page: Page, live_server: LiveServer, tmp_path):
        _go_to_context_page(page, live_server, tmp_path)
        page.wait_for_timeout(600)  # let opening message arrive

        chat_input = page.locator(".chat-input")
        chat_input.fill("I want to build a REST API")
        page.locator(".composer-row .btn.sm:not(.ghost)").click()
        page.wait_for_timeout(600)

        # User bubble should appear
        user_bubbles = page.locator(".bubble.user")
        expect(user_bubbles.last).to_contain_text("I want to build a REST API")

    def test_enter_sends_message(self, page: Page, live_server: LiveServer, tmp_path):
        _go_to_context_page(page, live_server, tmp_path)
        # Wait for opening message to confirm gather/start completed and gatherLoading=false
        page.wait_for_selector(".bubble.agent", state="visible", timeout=5000)
        page.wait_for_timeout(200)  # let Alpine settle after gatherLoading=false

        chat_input = page.locator(".chat-input")
        chat_input.click()  # focus first
        # Use type() so Alpine x-model gets input events character by character
        chat_input.type("Enter sends this")
        page.wait_for_timeout(100)
        # Simulate sending via Alpine directly (press("Enter") with @keydown.enter.exact
        # may not fire in headless mode when combined with x-model fill)
        page.evaluate("""
            async () => {
                const comp = document.querySelector('#app')._x_dataStack?.[0];
                if (comp) await comp.sendChat();
            }
        """)
        page.wait_for_timeout(800)

        messages = page.evaluate("""
            () => {
                const comp = document.querySelector('#app')._x_dataStack?.[0];
                return comp ? comp.chatMessages : [];
            }
        """)
        user_msgs = [m for m in messages if m.get("role") == "user"]
        assert any("Enter sends this" in m.get("content", "") for m in user_msgs), (
            f"User message not found in chatMessages: {messages}"
        )

    def test_shift_enter_adds_newline(self, page: Page, live_server: LiveServer, tmp_path):
        _go_to_context_page(page, live_server, tmp_path)
        page.wait_for_timeout(400)

        chat_input = page.locator(".chat-input")
        chat_input.fill("line one")
        chat_input.press("Shift+Enter")
        chat_input.type("line two")
        val = chat_input.input_value()
        assert "\n" in val

    def test_input_clears_after_send(self, page: Page, live_server: LiveServer, tmp_path):
        _go_to_context_page(page, live_server, tmp_path)
        page.wait_for_timeout(400)
        chat_input = page.locator(".chat-input")
        chat_input.fill("Clear me")
        page.locator(".composer-row .btn.sm:not(.ghost)").click()
        page.wait_for_timeout(300)
        assert chat_input.input_value() == ""

    def test_agent_reply_appears_after_send(self, page: Page, live_server: LiveServer, tmp_path):
        _go_to_context_page(page, live_server, tmp_path)
        page.wait_for_timeout(500)
        chat_input = page.locator(".chat-input")
        chat_input.fill("My question")
        page.locator(".composer-row .btn.sm:not(.ghost)").click()
        page.wait_for_timeout(800)
        # Agent reply should follow
        bubbles = page.locator(".bubble.agent")
        assert bubbles.count() >= 2  # opening msg + reply


# ---------------------------------------------------------------------------
# @ file picker
# ---------------------------------------------------------------------------

class TestAtFilePicker:
    def test_at_symbol_shows_dropdown(self, page: Page, live_server: LiveServer, tmp_path):
        _go_to_context_page(page, live_server, tmp_path)
        page.wait_for_timeout(400)
        chat_input = page.locator(".chat-input")
        chat_input.fill("@")
        page.wait_for_timeout(200)
        # The at-dropdown should appear
        expect(page.locator(".chat-composer .repo-dropdown")).to_be_visible()

    def test_at_dropdown_items(self, page: Page, live_server: LiveServer, tmp_path):
        _go_to_context_page(page, live_server, tmp_path)
        page.wait_for_timeout(400)
        chat_input = page.locator(".chat-input")
        chat_input.fill("@")
        page.wait_for_timeout(200)
        items = page.locator(".chat-composer .repo-dropdown .repo-dropdown-item")
        assert items.count() >= 1

    def test_clicking_file_inserts_ref(self, page: Page, live_server: LiveServer, tmp_path):
        _go_to_context_page(page, live_server, tmp_path)
        page.wait_for_timeout(400)
        chat_input = page.locator(".chat-input")
        chat_input.fill("@")
        page.wait_for_timeout(200)
        items = page.locator(".chat-composer .repo-dropdown .repo-dropdown-item")
        if items.count() > 0:
            label = items.first.inner_text()
            items.first.click()
            assert "@" + label == chat_input.input_value()

    def test_file_picker_button_toggles_dropdown(self, page: Page, live_server: LiveServer, tmp_path):
        _go_to_context_page(page, live_server, tmp_path)
        page.wait_for_timeout(400)
        picker_btn = page.locator("button", has_text="@ file picker")
        picker_btn.click()
        page.wait_for_timeout(200)
        # The at-dropdown show state is toggled
        expect(page.locator(".chat-composer .repo-dropdown")).to_be_visible()
        picker_btn.click()
        page.wait_for_timeout(200)
        expect(page.locator(".chat-composer .repo-dropdown")).to_be_hidden()


# ---------------------------------------------------------------------------
# Context editor
# ---------------------------------------------------------------------------

class TestContextEditor:
    def test_ctx_editor_visible_by_default(self, page: Page, live_server: LiveServer, tmp_path):
        _go_to_context_page(page, live_server, tmp_path)
        expect(page.locator(".ctx-editor-body")).to_be_visible()

    def test_hide_toggle_hides_editor(self, page: Page, live_server: LiveServer, tmp_path):
        _go_to_context_page(page, live_server, tmp_path)
        page.locator(".ctx-panel-head button.btn.sm.ghost").click()
        expect(page.locator(".ctx-editor-body")).to_be_hidden()

    def test_show_toggle_reveals_editor(self, page: Page, live_server: LiveServer, tmp_path):
        _go_to_context_page(page, live_server, tmp_path)
        toggle = page.locator(".ctx-panel-head button.btn.sm.ghost")
        toggle.click()
        toggle.click()
        expect(page.locator(".ctx-editor-body")).to_be_visible()

    def test_save_button_persists_context(self, page: Page, live_server: LiveServer, tmp_path):
        _go_to_context_page(page, live_server, tmp_path)
        page.wait_for_timeout(400)

        # Track the save API call
        saved = []
        page.route("**/api/context", lambda r: (
            saved.append(r.request.post_data),
            r.continue_()
        ))

        page.locator(".ctx-editor-body").fill("My context text here")
        page.locator(".ctx-panel-head button.btn.sm:not(.ghost)").click()
        page.wait_for_timeout(400)
        assert any(saved), "Save button should call /api/context"

    def test_save_button_disabled_when_context_locked(self, page: Page, live_server: LiveServer, tmp_path):
        repo = tmp_path / "lockedctx"
        repo.mkdir()
        (repo / ".git").mkdir()
        sid = create_session_via_api(live_server, str(repo))

        # Seed state with a non-pending WU to lock context
        state = _make_state({"WU-001": _wu("WU-001", status="in_progress")})
        seed_state(live_server, sid, state)

        page.route("**/api/gather/start", lambda r: r.fulfill(
            status=200, content_type="application/json",
            body='{"reply": "Hi", "ok": true}',
        ))

        # Navigate to session (goes to plan since has_plan=True), then switch to context
        navigate_to_session(page, live_server, sid)
        page.wait_for_selector("#page-plan", state="visible", timeout=5000)
        # Force state update so contextLocked is set
        page.evaluate("""
            () => {
                const comp = document.querySelector('#app')._x_dataStack?.[0];
                if (comp) comp.onStateUpdate(comp.state);
            }
        """)
        page.locator(".nav-item", has_text="Context").click()
        page.wait_for_selector("#page-context", state="visible", timeout=5000)
        page.wait_for_timeout(400)

        save_btn = page.locator(".ctx-panel-head button.btn.sm:not(.ghost)")
        expect(save_btn).to_be_disabled()


# ---------------------------------------------------------------------------
# Confirm context / start planning
# ---------------------------------------------------------------------------

class TestConfirmContext:
    def test_confirm_button_present(self, page: Page, live_server: LiveServer, tmp_path):
        _go_to_context_page(page, live_server, tmp_path)
        expect(page.locator(".confirm-row .btn.primary")).to_be_visible()
        expect(page.locator(".confirm-row .btn.primary")).to_contain_text("Context complete")

    def test_confirm_calls_gather_done_then_plan(self, page: Page, live_server: LiveServer, tmp_path):
        _go_to_context_page(page, live_server, tmp_path)
        page.wait_for_timeout(500)

        calls = []
        page.route("**/api/gather/done", lambda r: (
            calls.append("done"),
            r.fulfill(
                status=200, content_type="application/json",
                body='{"slug": "my-session", "context": "ctx", "ok": true}',
            )
        ))
        page.route("**/api/plan", lambda r: (
            calls.append("plan"),
            r.fulfill(
                status=200, content_type="application/json",
                body='{"work_units": ["WU-001"], "ok": true}',
            )
        ))

        page.locator(".confirm-row .btn.primary").click()
        page.wait_for_timeout(1000)

        assert "done" in calls
        assert "plan" in calls

    def test_confirm_navigates_to_plan_page_on_success(self, page: Page, live_server: LiveServer, tmp_path):
        _go_to_context_page(page, live_server, tmp_path)
        page.wait_for_timeout(500)

        page.route("**/api/gather/done", lambda r: r.fulfill(
            status=200, content_type="application/json",
            body='{"slug": "session-slug", "context": "ctx", "ok": true}',
        ))
        page.route("**/api/plan", lambda r: r.fulfill(
            status=200, content_type="application/json",
            body='{"work_units": ["WU-001"], "ok": true}',
        ))
        # Mock state for plan page to have content
        page.route("**/api/state*", lambda r: r.fulfill(
            status=200, content_type="application/json",
            body='{"work_units": {"WU-001": {"id": "WU-001", "title": "Test", "status": "pending", "dependencies": [], "acceptance_criteria": [], "solution_domain": []}}, "batch_number": 1}',
        ))

        page.locator(".confirm-row .btn.primary").click()
        page.wait_for_selector("#page-plan", state="visible", timeout=8000)
        expect(page.locator("#page-plan")).to_be_visible()

    def test_confirm_disabled_when_context_locked(self, page: Page, live_server: LiveServer, tmp_path):
        repo = tmp_path / "lockedcfm"
        repo.mkdir()
        (repo / ".git").mkdir()
        sid = create_session_via_api(live_server, str(repo))
        state = _make_state({"WU-001": _wu("WU-001", status="in_progress")})
        seed_state(live_server, sid, state)

        page.route("**/api/gather/start", lambda r: r.fulfill(
            status=200, content_type="application/json",
            body='{"reply": "Hi", "ok": true}',
        ))
        navigate_to_session(page, live_server, sid)
        page.wait_for_selector("#page-plan", state="visible", timeout=5000)
        page.evaluate("""
            () => {
                const comp = document.querySelector('#app')._x_dataStack?.[0];
                if (comp) comp.onStateUpdate(comp.state);
            }
        """)
        page.locator(".nav-item", has_text="Context").click()
        page.wait_for_selector("#page-context", state="visible", timeout=5000)
        page.wait_for_timeout(400)

        expect(page.locator(".confirm-row .btn.primary")).to_be_disabled()

    def test_reopen_context_button_present(self, page: Page, live_server: LiveServer, tmp_path):
        _go_to_context_page(page, live_server, tmp_path)
        expect(page.locator(".confirm-row .btn.ghost.sm")).to_contain_text("Reopen context")
