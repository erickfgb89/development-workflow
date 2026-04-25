"""Browser tests — Plan page.

Covers:
- Plan header meta (WU count, batch number, in-flight count)
- Outline sidebar with status dots
- WU cards: expand/collapse, status badge, description, acceptance criteria,
  solution domain, dependencies
- Failed banner (failure_count >= 2)
- Completed WU: "View diff" button
- Failed WU: "Reset" and "Trigger reshape" buttons
- Blocked WU: "Reset" button
- In-progress WU: "in flight" text, no kebab
- Expand-all toggle
- Re-plan open/close
- Sidebar navigation active highlight
"""
from __future__ import annotations
import re

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

def _open_plan_page(page: Page, live_server: LiveServer, tmp_path, wus: dict | None = None):
    """Create a session with a seeded state and navigate to the plan page."""
    repo = tmp_path / "plantest"
    repo.mkdir()
    (repo / ".git").mkdir()
    sid = create_session_via_api(live_server, str(repo))

    default_wus = {
        "WU-001": _wu("WU-001", title="First task", status="pending"),
        "WU-002": _wu("WU-002", title="Second task", status="completed",
                      completed_at="2025-01-01T12:00:00",
                      implementer_report={"summary": "Done", "files_touched": ["src/a.py"]}),
        "WU-003": _wu("WU-003", title="Third task", status="failed", failure_count=1, deps=["WU-001"]),
    }
    state = _make_state(wus or default_wus, batch_number=2)
    seed_state(live_server, sid, state, context="build a thing")

    navigate_to_session(page, live_server, sid)
    page.wait_for_selector("#page-plan", state="visible", timeout=8000)
    return sid


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------

class TestPlanHeader:
    def test_plan_header_shows_session_id(self, page: Page, live_server: LiveServer, tmp_path):
        sid = _open_plan_page(page, live_server, tmp_path)
        header = page.locator(".plan-header h2")
        expect(header).to_contain_text(sid)

    def test_meta_shows_wu_count(self, page: Page, live_server: LiveServer, tmp_path):
        _open_plan_page(page, live_server, tmp_path)
        meta = page.locator(".plan-header .meta")
        expect(meta).to_contain_text("3 work units")

    def test_meta_shows_batch_number(self, page: Page, live_server: LiveServer, tmp_path):
        _open_plan_page(page, live_server, tmp_path)
        expect(page.locator(".plan-header .meta")).to_contain_text("batch #2")

    def test_replan_button_present(self, page: Page, live_server: LiveServer, tmp_path):
        _open_plan_page(page, live_server, tmp_path)
        expect(page.locator("button", has_text="↻ Re-plan")).to_be_visible()

    def test_expand_all_button_present(self, page: Page, live_server: LiveServer, tmp_path):
        _open_plan_page(page, live_server, tmp_path)
        expect(page.locator("button", has_text="↓ expand all")).to_be_visible()


# ---------------------------------------------------------------------------
# WU cards — basic rendering
# ---------------------------------------------------------------------------

class TestWUCards:
    def test_wu_cards_rendered(self, page: Page, live_server: LiveServer, tmp_path):
        _open_plan_page(page, live_server, tmp_path)
        cards = page.locator(".wu-card")
        assert cards.count() == 3

    def test_wu_id_shown(self, page: Page, live_server: LiveServer, tmp_path):
        _open_plan_page(page, live_server, tmp_path)
        expect(page.locator("#wu-WU-001 .wu-id")).to_contain_text("WU-001")

    def test_wu_title_shown(self, page: Page, live_server: LiveServer, tmp_path):
        _open_plan_page(page, live_server, tmp_path)
        expect(page.locator("#wu-WU-001 .wu-title")).to_have_text("First task")

    def test_status_pill_shown(self, page: Page, live_server: LiveServer, tmp_path):
        _open_plan_page(page, live_server, tmp_path)
        pill = page.locator("#wu-WU-001 .pill")
        expect(pill).to_have_class(re.compile(r"(?:^| )pending(?:$| )"))

    def test_card_body_shows_description(self, page: Page, live_server: LiveServer, tmp_path):
        _open_plan_page(page, live_server, tmp_path)
        expect(page.locator("#wu-WU-001 .kv").first).to_contain_text("Description of WU-001")

    def test_acceptance_criteria_listed(self, page: Page, live_server: LiveServer, tmp_path):
        _open_plan_page(page, live_server, tmp_path)
        expect(page.locator("#wu-WU-001 .ac-list")).to_be_visible()
        expect(page.locator("#wu-WU-001 .ac-list li").first).to_contain_text("AC for WU-001")

    def test_solution_domain_shown(self, page: Page, live_server: LiveServer, tmp_path):
        _open_plan_page(page, live_server, tmp_path)
        expect(page.locator("#wu-WU-001 .mono-val")).to_contain_text("src/")

    def test_dependencies_shown_when_present(self, page: Page, live_server: LiveServer, tmp_path):
        _open_plan_page(page, live_server, tmp_path)
        expect(page.locator("#wu-WU-003")).to_contain_text("WU-001")

    def test_dependencies_none_when_empty(self, page: Page, live_server: LiveServer, tmp_path):
        _open_plan_page(page, live_server, tmp_path)
        expect(page.locator("#wu-WU-001")).to_contain_text("none")


# ---------------------------------------------------------------------------
# Status-specific actions
# ---------------------------------------------------------------------------

class TestWUStatusActions:
    def test_completed_wu_shows_view_diff_button(self, page: Page, live_server: LiveServer, tmp_path):
        _open_plan_page(page, live_server, tmp_path)
        expect(page.locator("#wu-WU-002 button", has_text="View diff ↗")).to_be_visible()

    def test_failed_wu_shows_reset_button(self, page: Page, live_server: LiveServer, tmp_path):
        _open_plan_page(page, live_server, tmp_path)
        expect(page.locator("#wu-WU-003 button", has_text="↺ Reset")).to_be_visible()

    def test_failed_wu_shows_trigger_reshape_button(self, page: Page, live_server: LiveServer, tmp_path):
        _open_plan_page(page, live_server, tmp_path)
        expect(page.locator("#wu-WU-003 button", has_text="Trigger reshape")).to_be_visible()

    def test_in_progress_shows_in_flight_text(self, page: Page, live_server: LiveServer, tmp_path):
        wus = {"WU-001": _wu("WU-001", status="in_progress")}
        _open_plan_page(page, live_server, tmp_path, wus=wus)
        expect(page.locator("#wu-WU-001 .mono.text-muted")).to_contain_text("in flight")

    def test_in_progress_no_kebab_button(self, page: Page, live_server: LiveServer, tmp_path):
        wus = {"WU-001": _wu("WU-001", status="in_progress")}
        _open_plan_page(page, live_server, tmp_path, wus=wus)
        # The ⋯ kebab should NOT be present for in_progress
        kebab = page.locator("#wu-WU-001 button", has_text="⋯")
        assert kebab.count() == 0

    def test_blocked_wu_shows_reset_button(self, page: Page, live_server: LiveServer, tmp_path):
        wus = {
            "WU-001": _wu("WU-001", status="failed"),
            "WU-002": _wu("WU-002", status="blocked", deps=["WU-001"]),
        }
        _open_plan_page(page, live_server, tmp_path, wus=wus)
        expect(page.locator("#wu-WU-002 button", has_text="↺ Reset")).to_be_visible()

    def test_completed_ac_checkmarks_shown(self, page: Page, live_server: LiveServer, tmp_path):
        wus = {
            "WU-001": _wu("WU-001", status="completed",
                          acceptance_criteria=["criterion A"],
                          implementer_report={"summary": "done", "files_touched": []})
        }
        _open_plan_page(page, live_server, tmp_path, wus=wus)
        expect(page.locator("#wu-WU-001 .ac-check.done")).to_be_visible()


# ---------------------------------------------------------------------------
# Failed banner
# ---------------------------------------------------------------------------

class TestFailedBanner:
    def test_banner_hidden_when_failure_count_lt_2(self, page: Page, live_server: LiveServer, tmp_path):
        wus = {"WU-001": _wu("WU-001", status="failed", failure_count=1)}
        _open_plan_page(page, live_server, tmp_path, wus=wus)
        expect(page.locator("#wu-WU-001 .wu-failed-banner")).to_be_hidden()

    def test_banner_visible_when_failure_count_ge_2(self, page: Page, live_server: LiveServer, tmp_path):
        wus = {"WU-001": _wu("WU-001", status="failed", failure_count=2)}
        _open_plan_page(page, live_server, tmp_path, wus=wus)
        expect(page.locator("#wu-WU-001 .wu-failed-banner")).to_be_visible()
        expect(page.locator("#wu-WU-001 .wu-failed-banner")).to_contain_text("failure_count = 2")


# ---------------------------------------------------------------------------
# Expand / collapse
# ---------------------------------------------------------------------------

class TestExpandCollapse:
    def test_card_body_visible_by_default(self, page: Page, live_server: LiveServer, tmp_path):
        _open_plan_page(page, live_server, tmp_path)
        # Body is shown when expandedWUs[id] !== false (default is undefined → truthy)
        expect(page.locator("#wu-WU-001 .kv").first).to_be_visible()

    def test_kebab_toggles_card_body(self, page: Page, live_server: LiveServer, tmp_path):
        _open_plan_page(page, live_server, tmp_path)
        kebab = page.locator("#wu-WU-001 button", has_text="⋯")
        kebab.click()
        page.wait_for_timeout(200)
        expect(page.locator("#wu-WU-001 .kv").first).to_be_hidden()
        kebab.click()
        page.wait_for_timeout(200)
        expect(page.locator("#wu-WU-001 .kv").first).to_be_visible()

    def test_expand_all_collapses_all_when_all_open(self, page: Page, live_server: LiveServer, tmp_path):
        _open_plan_page(page, live_server, tmp_path)
        btn = page.locator("button", has_text="↓ expand all")
        btn.click()
        page.wait_for_timeout(200)
        # All cards now collapsed
        for wu_id in ["WU-001", "WU-002", "WU-003"]:
            expect(page.locator(f"#wu-{wu_id} .kv").first).to_be_hidden()

    def test_expand_all_expands_all_when_any_collapsed(self, page: Page, live_server: LiveServer, tmp_path):
        _open_plan_page(page, live_server, tmp_path)
        # Collapse one card first
        page.locator("#wu-WU-001 button", has_text="⋯").click()
        page.wait_for_timeout(100)
        # Click expand-all
        page.locator("button", has_text="↓ expand all").click()
        page.wait_for_timeout(200)
        expect(page.locator("#wu-WU-001 .kv").first).to_be_visible()


# ---------------------------------------------------------------------------
# Re-plan
# ---------------------------------------------------------------------------

class TestReplan:
    def test_replan_panel_hidden_by_default(self, page: Page, live_server: LiveServer, tmp_path):
        _open_plan_page(page, live_server, tmp_path)
        expect(page.locator(".replan-input-area")).to_be_hidden()

    def test_replan_button_opens_panel(self, page: Page, live_server: LiveServer, tmp_path):
        _open_plan_page(page, live_server, tmp_path)
        page.locator("button", has_text="↻ Re-plan").click()
        expect(page.locator(".replan-input-area")).to_be_visible()

    def test_replan_cancel_closes_panel(self, page: Page, live_server: LiveServer, tmp_path):
        _open_plan_page(page, live_server, tmp_path)
        page.locator("button", has_text="↻ Re-plan").click()
        page.locator(".replan-input-area button", has_text="Cancel").click()
        expect(page.locator(".replan-input-area")).to_be_hidden()

    def test_replan_submit_calls_reshape_api(self, page: Page, live_server: LiveServer, tmp_path):
        _open_plan_page(page, live_server, tmp_path)

        called = []
        page.route("**/api/reshape", lambda r: (
            called.append(r.request.post_data),
            r.fulfill(status=200, content_type="application/json",
                      body='{"work_units": ["WU-001"], "ok": true}'),
        ))

        page.locator("button", has_text="↻ Re-plan").click()
        page.locator(".replan-input-area textarea").fill("Add more tests")
        page.locator(".replan-input-area button", has_text="Re-plan").click()
        page.wait_for_timeout(600)
        assert len(called) == 1
        import json
        body = json.loads(called[0])
        assert body["instructions"] == "Add more tests"

    def test_replan_empty_instructions_does_not_submit(self, page: Page, live_server: LiveServer, tmp_path):
        _open_plan_page(page, live_server, tmp_path)
        called = []
        page.route("**/api/reshape", lambda r: (called.append(True), r.continue_()))

        page.locator("button", has_text="↻ Re-plan").click()
        page.locator(".replan-input-area textarea").fill("")
        page.locator(".replan-input-area button", has_text="Re-plan").click()
        page.wait_for_timeout(300)
        assert len(called) == 0


# ---------------------------------------------------------------------------
# Reset WU via button
# ---------------------------------------------------------------------------

class TestResetWUButton:
    def test_reset_button_calls_api_and_refreshes_state(self, page: Page, live_server: LiveServer, tmp_path):
        repo = tmp_path / "resetbtn"
        repo.mkdir()
        (repo / ".git").mkdir()
        sid = create_session_via_api(live_server, str(repo))
        wus = {"WU-001": _wu("WU-001", status="failed", failure_count=1)}
        state = _make_state(wus)
        seed_state(live_server, sid, state)

        navigate_to_session(page, live_server, sid)
        page.wait_for_selector("#page-plan", state="visible", timeout=5000)

        page.locator("#wu-WU-001 button", has_text="↺ Reset").click()
        page.wait_for_timeout(800)

        # State should have been refreshed — pill status should be pending
        expect(page.locator("#wu-WU-001 .pill")).to_have_class(re.compile(r"(?:^| )pending(?:$| )"))


# ---------------------------------------------------------------------------
# Outline sidebar
# ---------------------------------------------------------------------------

class TestOutlineSidebar:
    def test_outline_shows_all_wus(self, page: Page, live_server: LiveServer, tmp_path):
        _open_plan_page(page, live_server, tmp_path)
        items = page.locator(".outline-item")
        assert items.count() == 3

    def test_outline_status_dots(self, page: Page, live_server: LiveServer, tmp_path):
        _open_plan_page(page, live_server, tmp_path)
        # WU-002 is completed
        dot = page.locator(".outline-item:has(.wu-id-label:has-text('WU-002')) .status-dot")
        expect(dot).to_have_class(re.compile(r"(?:^| )completed(?:$| )"))

    def test_clicking_outline_item_scrolls_to_card(self, page: Page, live_server: LiveServer, tmp_path):
        _open_plan_page(page, live_server, tmp_path)
        item = page.locator(".outline-item:has(.wu-id-label:has-text('WU-003'))")
        item.click()
        page.wait_for_timeout(400)
        # Card should be visible in viewport after scroll
        expect(page.locator("#wu-WU-003")).to_be_in_viewport()
