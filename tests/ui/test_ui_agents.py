"""Browser tests — Agents page.

Covers:
- Header: "Agent output" title + subtitle counting completed WUs
- Empty state when no completed WUs
- Empty state when no state at all
- Accordion items appear for completed and failed WUs
- Accordion expand/collapse
- Chevron rotates on open
- WU id, title, status pill in accordion head
- "View diff ↗" button in head for completed WUs
- Timestamp rendered for completed_at
- Implementer section: summary, files changed
- Domain breach warning
- Reviewer section: verdict pill, summary, AC coverage table
- "View merged diff ↗" footer button
- Round label when failure_count > 1
- No agent output fallback message
"""
from __future__ import annotations
import re

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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_IMPL_REPORT = {
    "summary": "Added the login endpoint",
    "files_touched": ["src/auth.py", "tests/test_auth.py"],
    "commit_message": "abc1234 feat: add login",
}

_IMPL_BREACH = {
    "summary": "Did too much",
    "files_touched": ["src/auth.py"],
    "domain_breaches": ["src/unrelated.py"],
}

_REVIEW_PASS = {
    "verdict": "pass",
    "summary": "All criteria met",
    "ac_coverage": [
        {"criterion": "Returns 200 on valid login", "covered": True, "notes": "verified"},
        {"criterion": "Returns 401 on bad password", "covered": True, "notes": ""},
    ],
}

_REVIEW_FAIL = {
    "verdict": "fail",
    "summary": "Missing error handling",
    "ac_coverage": [
        {"criterion": "Returns 200 on valid login", "covered": True, "notes": ""},
        {"criterion": "Returns 401 on bad password", "covered": False, "notes": "not tested"},
    ],
}


def _open_agents_page(page: Page, live_server: LiveServer, tmp_path, wus: dict | None = None):
    repo = tmp_path / "agentstest"
    repo.mkdir()
    (repo / ".git").mkdir()
    sid = create_session_via_api(live_server, str(repo))

    default_wus = {
        "WU-001": _wu("WU-001", title="Login endpoint", status="completed",
                      completed_at="2025-01-01T12:30:00",
                      implementer_report=_IMPL_REPORT,
                      last_review=_REVIEW_PASS),
        "WU-002": _wu("WU-002", title="Error handler", status="failed",
                      failure_count=2,
                      implementer_report=_IMPL_BREACH,
                      last_review=_REVIEW_FAIL),
        "WU-003": _wu("WU-003", title="Pending task", status="pending"),
    }
    state = _make_state(wus or default_wus)
    seed_state(live_server, sid, state)

    navigate_to_session(page, live_server, sid)
    page.wait_for_selector("#page-plan", state="visible", timeout=5000)
    page.locator(".nav-item", has_text="Agents").click()
    page.wait_for_selector("#page-agents", state="visible", timeout=3000)
    return sid


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------

class TestAgentsHeader:
    def test_title_present(self, page: Page, live_server: LiveServer, tmp_path):
        _open_agents_page(page, live_server, tmp_path)
        expect(page.locator(".agents-header h2")).to_have_text("Agent output")

    def test_subtitle_counts_completed(self, page: Page, live_server: LiveServer, tmp_path):
        _open_agents_page(page, live_server, tmp_path)
        # 1 of 3 WUs completed
        expect(page.locator(".agents-header .sub")).to_contain_text("1 of 3")


# ---------------------------------------------------------------------------
# Empty states
# ---------------------------------------------------------------------------

class TestAgentsEmptyState:
    def test_empty_when_no_state(self, page: Page, live_server: LiveServer, tmp_path):
        repo = tmp_path / "agentsempty"
        repo.mkdir()
        (repo / ".git").mkdir()
        sid = create_session_via_api(live_server, str(repo))

        page.route("**/api/gather/start", lambda r: r.fulfill(
            status=200, content_type="application/json",
            body='{"reply": "Hi", "ok": true}',
        ))
        navigate_to_session(page, live_server, sid)
        page.wait_for_selector("#page-context", state="visible", timeout=5000)
        page.locator(".nav-item", has_text="Agents").click()
        page.wait_for_selector("#page-agents", state="visible", timeout=3000)

        # First empty state: no state at all
        expect(page.locator(".agents-empty").first).to_be_visible()

    def test_empty_message_when_no_completed_wus(self, page: Page, live_server: LiveServer, tmp_path):
        wus = {
            "WU-001": _wu("WU-001", status="pending"),
            "WU-002": _wu("WU-002", status="in_progress"),
        }
        _open_agents_page(page, live_server, tmp_path, wus=wus)
        expect(page.locator("#page-agents .agents-empty").nth(1)).to_contain_text("No work units have completed yet")


# ---------------------------------------------------------------------------
# Accordion items
# ---------------------------------------------------------------------------

class TestAgentAccordionItems:
    def test_completed_wu_in_feed(self, page: Page, live_server: LiveServer, tmp_path):
        _open_agents_page(page, live_server, tmp_path)
        # WU-001 completed should appear
        heads = page.locator(".agent-acc-head")
        ids_in_feed = [h.locator(".wu-id").inner_text() for h in heads.all()]
        assert "WU-001" in ids_in_feed

    def test_failed_wu_in_feed(self, page: Page, live_server: LiveServer, tmp_path):
        _open_agents_page(page, live_server, tmp_path)
        heads = page.locator(".agent-acc-head")
        ids_in_feed = [h.locator(".wu-id").inner_text() for h in heads.all()]
        assert "WU-002" in ids_in_feed

    def test_pending_wu_not_in_feed(self, page: Page, live_server: LiveServer, tmp_path):
        _open_agents_page(page, live_server, tmp_path)
        heads = page.locator(".agent-acc-head")
        ids_in_feed = [h.locator(".wu-id").inner_text() for h in heads.all()]
        assert "WU-003" not in ids_in_feed

    def test_accordion_head_shows_title(self, page: Page, live_server: LiveServer, tmp_path):
        _open_agents_page(page, live_server, tmp_path)
        head = page.locator(".agent-accordion:has(.wu-id:has-text('WU-001')) .agent-acc-head")
        expect(head.locator(".wu-title")).to_have_text("Login endpoint")

    def test_accordion_head_shows_status_pill(self, page: Page, live_server: LiveServer, tmp_path):
        _open_agents_page(page, live_server, tmp_path)
        head = page.locator(".agent-accordion:has(.wu-id:has-text('WU-001')) .agent-acc-head")
        expect(head.locator(".pill")).to_have_class(re.compile(r"(?:^| )completed(?:$| )"))

    def test_view_diff_button_in_completed_head(self, page: Page, live_server: LiveServer, tmp_path):
        _open_agents_page(page, live_server, tmp_path)
        head = page.locator(".agent-accordion:has(.wu-id:has-text('WU-001')) .agent-acc-head")
        expect(head.locator("button", has_text="View diff ↗")).to_be_visible()

    def test_timestamp_rendered_for_completed(self, page: Page, live_server: LiveServer, tmp_path):
        _open_agents_page(page, live_server, tmp_path)
        head = page.locator(".agent-accordion:has(.wu-id:has-text('WU-001')) .agent-acc-head")
        # 2025-01-01T12:30:00 → "12:30"
        expect(head.locator(".time")).to_contain_text("12:30")


# ---------------------------------------------------------------------------
# Accordion expand/collapse
# ---------------------------------------------------------------------------

class TestAccordionExpandCollapse:
    def test_body_hidden_by_default(self, page: Page, live_server: LiveServer, tmp_path):
        _open_agents_page(page, live_server, tmp_path)
        body = page.locator(".agent-accordion:has(.wu-id:has-text('WU-001')) .agent-acc-body")
        expect(body).not_to_have_class(re.compile(r"(?:^| )open(?:$| )"))

    def test_click_opens_body(self, page: Page, live_server: LiveServer, tmp_path):
        _open_agents_page(page, live_server, tmp_path)
        head = page.locator(".agent-accordion:has(.wu-id:has-text('WU-001')) .agent-acc-head")
        head.click()
        page.wait_for_timeout(200)
        body = page.locator(".agent-accordion:has(.wu-id:has-text('WU-001')) .agent-acc-body")
        expect(body).to_have_class(re.compile(r"(?:^| )open(?:$| )"))

    def test_click_again_closes_body(self, page: Page, live_server: LiveServer, tmp_path):
        _open_agents_page(page, live_server, tmp_path)
        head = page.locator(".agent-accordion:has(.wu-id:has-text('WU-001')) .agent-acc-head")
        head.click()
        head.click()
        page.wait_for_timeout(200)
        body = page.locator(".agent-accordion:has(.wu-id:has-text('WU-001')) .agent-acc-body")
        expect(body).not_to_have_class(re.compile(r"(?:^| )open(?:$| )"))

    def test_chevron_rotates_on_open(self, page: Page, live_server: LiveServer, tmp_path):
        _open_agents_page(page, live_server, tmp_path)
        head = page.locator(".agent-accordion:has(.wu-id:has-text('WU-001')) .agent-acc-head")
        head.click()
        page.wait_for_timeout(200)
        chev = page.locator(".agent-accordion:has(.wu-id:has-text('WU-001')) .chev")
        expect(chev).to_have_class(re.compile(r"(?:^| )open(?:$| )"))


# ---------------------------------------------------------------------------
# Implementer section
# ---------------------------------------------------------------------------

class TestImplementerSection:
    def _open_wu1(self, page, live_server, tmp_path):
        _open_agents_page(page, live_server, tmp_path)
        head = page.locator(".agent-accordion:has(.wu-id:has-text('WU-001')) .agent-acc-head")
        head.click()
        page.wait_for_timeout(300)

    def test_implementer_heading_present(self, page: Page, live_server: LiveServer, tmp_path):
        self._open_wu1(page, live_server, tmp_path)
        expect(page.locator(".agent-accordion:has(.wu-id:has-text('WU-001')) .imp-section h5")).to_contain_text("Implementer")

    def test_implementer_summary_shown(self, page: Page, live_server: LiveServer, tmp_path):
        self._open_wu1(page, live_server, tmp_path)
        expect(
            page.locator(".agent-accordion:has(.wu-id:has-text('WU-001')) .imp-section")
        ).to_contain_text("Added the login endpoint")

    def test_files_changed_listed(self, page: Page, live_server: LiveServer, tmp_path):
        self._open_wu1(page, live_server, tmp_path)
        files_list = page.locator(".agent-accordion:has(.wu-id:has-text('WU-001')) .files-list")
        expect(files_list).to_contain_text("src/auth.py")
        expect(files_list).to_contain_text("tests/test_auth.py")

    def test_file_link_opens_diff_modal(self, page: Page, live_server: LiveServer, tmp_path):
        self._open_wu1(page, live_server, tmp_path)
        link = page.locator(
            ".agent-accordion:has(.wu-id:has-text('WU-001')) .files-list a"
        ).first
        link.click()
        page.wait_for_timeout(300)
        expect(page.locator(".modal-overlay:not(.hidden)")).to_be_visible()

    def test_domain_breach_warning(self, page: Page, live_server: LiveServer, tmp_path):
        _open_agents_page(page, live_server, tmp_path)
        head = page.locator(".agent-accordion:has(.wu-id:has-text('WU-002')) .agent-acc-head")
        head.click()
        page.wait_for_timeout(300)
        expect(
            page.locator(".agent-accordion:has(.wu-id:has-text('WU-002')) .domain-breach")
        ).to_be_visible()
        expect(
            page.locator(".agent-accordion:has(.wu-id:has-text('WU-002')) .domain-breach")
        ).to_contain_text("src/unrelated.py")


# ---------------------------------------------------------------------------
# Reviewer section
# ---------------------------------------------------------------------------

class TestReviewerSection:
    def _open_wu1(self, page, live_server, tmp_path):
        _open_agents_page(page, live_server, tmp_path)
        page.locator(".agent-accordion:has(.wu-id:has-text('WU-001')) .agent-acc-head").click()
        page.wait_for_timeout(300)

    def test_reviewer_heading_present(self, page: Page, live_server: LiveServer, tmp_path):
        self._open_wu1(page, live_server, tmp_path)
        expect(
            page.locator(".agent-accordion:has(.wu-id:has-text('WU-001')) .rev-section h5")
        ).to_contain_text("Reviewer")

    def test_pass_verdict_pill(self, page: Page, live_server: LiveServer, tmp_path):
        self._open_wu1(page, live_server, tmp_path)
        expect(
            page.locator(".agent-accordion:has(.wu-id:has-text('WU-001')) .rev-section .pill")
        ).to_have_class(re.compile(r"(?:^| )pass(?:$| )"))

    def test_fail_verdict_pill(self, page: Page, live_server: LiveServer, tmp_path):
        _open_agents_page(page, live_server, tmp_path)
        page.locator(".agent-accordion:has(.wu-id:has-text('WU-002')) .agent-acc-head").click()
        page.wait_for_timeout(300)
        expect(
            page.locator(".agent-accordion:has(.wu-id:has-text('WU-002')) .rev-section .pill")
        ).to_have_class(re.compile(r"(?:^| )fail(?:$| )"))

    def test_reviewer_summary_shown(self, page: Page, live_server: LiveServer, tmp_path):
        self._open_wu1(page, live_server, tmp_path)
        expect(
            page.locator(".agent-accordion:has(.wu-id:has-text('WU-001')) .rev-section")
        ).to_contain_text("All criteria met")

    def test_ac_coverage_table_present(self, page: Page, live_server: LiveServer, tmp_path):
        self._open_wu1(page, live_server, tmp_path)
        table = page.locator(".agent-accordion:has(.wu-id:has-text('WU-001')) .ac-table")
        expect(table).to_be_visible()

    def test_ac_table_has_header_columns(self, page: Page, live_server: LiveServer, tmp_path):
        self._open_wu1(page, live_server, tmp_path)
        table = page.locator(".agent-accordion:has(.wu-id:has-text('WU-001')) .ac-table")
        headers = table.locator("th").all_inner_texts()
        assert "AC" in headers
        assert "Covered" in headers
        assert "Notes" in headers

    def test_ac_table_rows_populated(self, page: Page, live_server: LiveServer, tmp_path):
        self._open_wu1(page, live_server, tmp_path)
        rows = page.locator(".agent-accordion:has(.wu-id:has-text('WU-001')) .ac-table tbody tr")
        assert rows.count() == 2


# ---------------------------------------------------------------------------
# Footer button and round label
# ---------------------------------------------------------------------------

class TestAgentFooter:
    def test_view_merged_diff_button_for_completed(self, page: Page, live_server: LiveServer, tmp_path):
        _open_agents_page(page, live_server, tmp_path)
        page.locator(".agent-accordion:has(.wu-id:has-text('WU-001')) .agent-acc-head").click()
        page.wait_for_timeout(300)
        body = page.locator(".agent-accordion:has(.wu-id:has-text('WU-001')) .agent-acc-body")
        expect(body.locator("button", has_text="View merged diff ↗")).to_be_visible()

    def test_round_label_shown_when_failure_count_gt_1(self, page: Page, live_server: LiveServer, tmp_path):
        _open_agents_page(page, live_server, tmp_path)
        page.locator(".agent-accordion:has(.wu-id:has-text('WU-002')) .agent-acc-head").click()
        page.wait_for_timeout(300)
        body = page.locator(".agent-accordion:has(.wu-id:has-text('WU-002')) .agent-acc-body")
        expect(body.locator(".round-label")).to_be_visible()
        expect(body.locator(".round-label")).to_contain_text("Round 2")

    def test_no_agent_output_fallback(self, page: Page, live_server: LiveServer, tmp_path):
        # WU with status completed but no reports
        wus = {
            "WU-001": _wu("WU-001", title="Bare WU", status="completed",
                          completed_at="2025-01-01T10:00:00"),
        }
        _open_agents_page(page, live_server, tmp_path, wus=wus)
        page.locator(".agent-acc-head").click()
        page.wait_for_timeout(300)
        body = page.locator(".agent-acc-body")
        expect(body).to_contain_text("No agent output recorded yet")
