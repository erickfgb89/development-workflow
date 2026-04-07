"""Tests for the Textual TUI app (WU-002 shell + WU-003 input widget)."""
from __future__ import annotations

import pytest

from orchestrate.tui import AutoCompleteOverlay, MessageInput, MessageSubmitted, OrchestratorApp


# ---------------------------------------------------------------------------
# WU-002 shell tests
# ---------------------------------------------------------------------------

def test_import() -> None:
    """Verify the OrchestratorApp can be imported without error."""
    assert OrchestratorApp is not None


@pytest.mark.asyncio
async def test_app_composes_without_error() -> None:
    """Verify the app composes without raising, and key widgets exist."""
    app = OrchestratorApp()
    async with app.run_test() as pilot:
        assert pilot.app.query_one("#status-bar") is not None
        assert pilot.app.query_one("#output-area") is not None
        assert pilot.app.query_one("#message-input") is not None
        assert pilot.app.query_one("#send-button") is not None


@pytest.mark.asyncio
async def test_system_tab_exists() -> None:
    """Verify the 'System' tab pane is present on composition."""
    app = OrchestratorApp()
    async with app.run_test() as pilot:
        tab_pane = pilot.app.query_one("#tab-system")
        assert tab_pane is not None


# ---------------------------------------------------------------------------
# WU-003 MessageInput widget tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_enter_inserts_newline() -> None:
    """Plain Enter should insert a newline, not submit."""
    app = OrchestratorApp()
    async with app.run_test() as pilot:
        input_widget = pilot.app.query_one("#message-input", MessageInput)
        # Focus the input and type some text followed by Enter
        input_widget.focus()
        await pilot.press("h", "i", "enter")
        # The widget text should contain a newline, not be empty
        assert "\n" in input_widget.text


@pytest.mark.asyncio
async def test_alt_enter_submits_and_clears() -> None:
    """Alt+Enter should post MessageSubmitted and clear the input."""
    submitted: list[str] = []

    app = OrchestratorApp()
    async with app.run_test() as pilot:
        input_widget = pilot.app.query_one("#message-input", MessageInput)
        input_widget.focus()
        await pilot.press("h", "e", "l", "l", "o")
        # Patch the post_message to capture the event
        original_post = input_widget.post_message
        def capture(msg):
            if isinstance(msg, MessageSubmitted):
                submitted.append(msg.text)
            return original_post(msg)
        input_widget.post_message = capture  # type: ignore[method-assign]

        await pilot.press("alt+enter")
        # Input should be cleared after submission
        assert input_widget.text.strip() == ""
        # The submitted list should have the message
        assert submitted == ["hello"]


@pytest.mark.asyncio
async def test_send_button_submits() -> None:
    """Clicking the Send button should trigger submission."""
    submitted: list[str] = []

    app = OrchestratorApp()
    async with app.run_test() as pilot:
        input_widget = pilot.app.query_one("#message-input", MessageInput)
        input_widget.focus()
        await pilot.press("h", "i")
        original_post = input_widget.post_message
        def capture(msg):
            if isinstance(msg, MessageSubmitted):
                submitted.append(msg.text)
            return original_post(msg)
        input_widget.post_message = capture  # type: ignore[method-assign]

        await pilot.click("#send-button")
        assert input_widget.text.strip() == ""
        assert submitted == ["hi"]


@pytest.mark.asyncio
async def test_submission_echoes_to_system_log() -> None:
    """After submission with no pending input and no queue, text is echoed to system log."""
    from textual.widgets import RichLog

    app = OrchestratorApp()
    async with app.run_test() as pilot:
        # Cancel any pending orchestrator future so the message goes to the echo path
        if pilot.app._pending_input and not pilot.app._pending_input.done():
            pilot.app._pending_input.set_result("")  # satisfy the initial prompt request
        await pilot.pause()

        # Now clear the pending input and send a new message — it should echo
        pilot.app._pending_input = None
        input_widget = pilot.app.query_one("#message-input", MessageInput)
        input_widget.focus()
        await pilot.press("t", "e", "s", "t")
        await pilot.press("alt+enter")
        await pilot.pause()
        log = pilot.app.query_one("#system-log", RichLog)
        # RichLog stores lines; check that 'test' appears somewhere
        log_text = "\n".join(str(line) for line in log.lines)
        assert "test" in log_text


# ---------------------------------------------------------------------------
# WU-004 streaming output panel tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_append_to_output_writes_to_system_log() -> None:
    """append_to_output() should write the given text to the system log."""
    from textual.widgets import RichLog

    app = OrchestratorApp()
    async with app.run_test() as pilot:
        pilot.app.append_to_output("Hello world")
        log = pilot.app.query_one("#system-log", RichLog)
        log_text = "\n".join(str(line) for line in log.lines)
        assert "Hello world" in log_text


@pytest.mark.asyncio
async def test_append_checkpoint_renders_checkpoint_text() -> None:
    """append_checkpoint() should write CHECKPOINT header and state info to the log."""
    from textual.widgets import RichLog

    app = OrchestratorApp()
    async with app.run_test() as pilot:
        pilot.app.append_checkpoint(
            "IMPLEMENTING",
            "BATCH_EXTRACT → IMPLEMENTING",
            "2/5 complete",
        )
        log = pilot.app.query_one("#system-log", RichLog)
        log_text = "\n".join(str(line) for line in log.lines)
        assert "CHECKPOINT" in log_text
        assert "IMPLEMENTING" in log_text


@pytest.mark.asyncio
async def test_richlog_respects_max_lines() -> None:
    """Writing more than 10,000 lines should not exceed the scrollback limit."""
    from textual.widgets import RichLog

    app = OrchestratorApp()
    async with app.run_test() as pilot:
        log = pilot.app.query_one("#system-log", RichLog)
        for i in range(10_001):
            log.write(f"line {i}")
        assert len(log.lines) <= 10_000


# ---------------------------------------------------------------------------
# WU-005: call_agent() message sink callback tests
# ---------------------------------------------------------------------------

class _FakeTextBlock:
    """Minimal stand-in for claude_agent_sdk.TextBlock."""
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeToolUseBlock:
    """Minimal stand-in for claude_agent_sdk.ToolUseBlock."""
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeAssistantMessage:
    """Minimal stand-in for claude_agent_sdk.AssistantMessage."""
    def __init__(self, content: list) -> None:
        self.content = content


class _FakeResultMessage:
    """Minimal stand-in for claude_agent_sdk.ResultMessage."""
    def __init__(self, result: str) -> None:
        self.result = result


def test_format_message_text_block() -> None:
    """_format_message extracts text from an AssistantMessage with a TextBlock."""
    from unittest.mock import patch
    from orchestrate.orchestrator import _format_message

    block = _FakeTextBlock("hello")
    msg = _FakeAssistantMessage([block])

    with (
        patch("orchestrate.orchestrator.AssistantMessage", _FakeAssistantMessage),
        patch("orchestrate.orchestrator.TextBlock", _FakeTextBlock),
        patch("orchestrate.orchestrator.ToolUseBlock", _FakeToolUseBlock),
    ):
        result = _format_message(msg)

    assert result == "hello"


def test_format_message_tool_use_block() -> None:
    """_format_message renders [tool: <name>] for ToolUseBlock."""
    from unittest.mock import patch
    from orchestrate.orchestrator import _format_message

    block = _FakeToolUseBlock("Bash")
    msg = _FakeAssistantMessage([block])

    with (
        patch("orchestrate.orchestrator.AssistantMessage", _FakeAssistantMessage),
        patch("orchestrate.orchestrator.TextBlock", _FakeTextBlock),
        patch("orchestrate.orchestrator.ToolUseBlock", _FakeToolUseBlock),
    ):
        result = _format_message(msg)

    assert result == "[tool: Bash]"


def test_format_message_result_message_returns_none() -> None:
    """_format_message returns None for a ResultMessage (handled separately)."""
    from unittest.mock import patch
    from orchestrate.orchestrator import _format_message

    msg = _FakeResultMessage("{}")

    with (
        patch("orchestrate.orchestrator.AssistantMessage", _FakeAssistantMessage),
        patch("orchestrate.orchestrator.ResultMessage", _FakeResultMessage),
    ):
        result = _format_message(msg)

    assert result is None


@pytest.mark.asyncio
async def test_call_agent_sink_receives_assistant_messages(monkeypatch) -> None:
    """Sink is called once per AssistantMessage with (agent_name, text)."""
    import asyncio
    from unittest.mock import AsyncMock, patch, MagicMock
    from orchestrate.orchestrator import call_agent

    # Build fake messages
    text_block = _FakeTextBlock("thinking...")
    assistant_msg = _FakeAssistantMessage([text_block])
    result_msg = _FakeResultMessage('{"status": "ok"}')

    async def _fake_query(**kwargs):
        yield assistant_msg
        yield result_msg

    sink_calls: list[tuple[str, str]] = []

    async def _sink(agent_name: str, text: str) -> None:
        sink_calls.append((agent_name, text))

    with (
        patch("orchestrate.orchestrator.query", side_effect=_fake_query),
        patch("orchestrate.orchestrator.AssistantMessage", _FakeAssistantMessage),
        patch("orchestrate.orchestrator.TextBlock", _FakeTextBlock),
        patch("orchestrate.orchestrator.ToolUseBlock", _FakeToolUseBlock),
        patch("orchestrate.orchestrator.ResultMessage", _FakeResultMessage),
        patch("orchestrate.orchestrator.parse_json_result", return_value={"status": "ok"}),
    ):
        options = MagicMock()
        result = await call_agent(
            prompt="go",
            options=options,
            agent_name="test-agent",
            message_sink=_sink,
        )

    assert result == {"status": "ok"}
    assert sink_calls == [("test-agent", "thinking...")]


@pytest.mark.asyncio
async def test_call_agent_without_sink_is_backward_compatible(monkeypatch) -> None:
    """Omitting message_sink should not raise and should still return the result."""
    from unittest.mock import patch, MagicMock
    from orchestrate.orchestrator import call_agent

    text_block = _FakeTextBlock("working")
    assistant_msg = _FakeAssistantMessage([text_block])
    result_msg = _FakeResultMessage('{"done": true}')

    async def _fake_query(**kwargs):
        yield assistant_msg
        yield result_msg

    with (
        patch("orchestrate.orchestrator.query", side_effect=_fake_query),
        patch("orchestrate.orchestrator.AssistantMessage", _FakeAssistantMessage),
        patch("orchestrate.orchestrator.TextBlock", _FakeTextBlock),
        patch("orchestrate.orchestrator.ToolUseBlock", _FakeToolUseBlock),
        patch("orchestrate.orchestrator.ResultMessage", _FakeResultMessage),
        patch("orchestrate.orchestrator.parse_json_result", return_value={"done": True}),
    ):
        options = MagicMock()
        result = await call_agent(prompt="go", options=options, agent_name="compat-agent")

    assert result == {"done": True}


# ---------------------------------------------------------------------------
# WU-006: tabbed output for parallel agents
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_agent_tab_exists() -> None:
    """create_agent_tab() should add a new TabPane with the correct id."""
    from textual.widgets import TabPane

    app = OrchestratorApp()
    async with app.run_test() as pilot:
        tab_id = pilot.app.create_agent_tab("implementer-WU-002")
        assert tab_id == "tab-implementer-WU-002"
        pane = pilot.app.query_one("#tab-implementer-WU-002", TabPane)
        assert pane is not None


@pytest.mark.asyncio
async def test_append_agent_message_creates_tab_and_writes() -> None:
    """append_agent_message() should create the tab if absent and write text."""
    app = OrchestratorApp()
    async with app.run_test() as pilot:
        pilot.app.append_agent_message("implementer-WU-002", "Starting...")
        pilot.app.activate_tab("implementer-WU-002")
        await pilot.pause()
        # Use the cached log reference (bypasses deferred DOM mount timing).
        log = pilot.app._agent_logs["tab-implementer-WU-002"]
        log_text = "\n".join(str(line) for line in log.lines)
        assert "Starting..." in log_text


@pytest.mark.asyncio
async def test_remove_agent_tab_removes_pane() -> None:
    """remove_agent_tab() should remove the tab so it's no longer queryable."""
    from textual.widgets import TabPane

    app = OrchestratorApp()
    async with app.run_test() as pilot:
        pilot.app.create_agent_tab("implementer-WU-002")
        pilot.app.remove_agent_tab("implementer-WU-002")
        await pilot.pause()
        with pytest.raises(Exception):
            pilot.app.query_one("#tab-implementer-WU-002", TabPane)


@pytest.mark.asyncio
async def test_activate_tab_switches_active() -> None:
    """activate_tab() should update TabbedContent.active to the specified tab."""
    from textual.widgets import TabbedContent

    app = OrchestratorApp()
    async with app.run_test() as pilot:
        pilot.app.create_agent_tab("agent-a")
        pilot.app.create_agent_tab("agent-b")
        pilot.app.activate_tab("agent-a")
        tabbed = pilot.app.query_one("#output-area", TabbedContent)
        assert tabbed.active == "tab-agent-a"
        pilot.app.activate_tab("agent-b")
        assert tabbed.active == "tab-agent-b"


@pytest.mark.asyncio
async def test_append_agent_message_auto_creates_tab() -> None:
    """append_agent_message() for a non-existent agent auto-creates the tab."""
    from textual.widgets import TabPane

    app = OrchestratorApp()
    async with app.run_test() as pilot:
        # No prior create_agent_tab call
        pilot.app.append_agent_message("new-agent", "hello from new-agent")
        pane = pilot.app.query_one("#tab-new-agent", TabPane)
        assert pane is not None


# ---------------------------------------------------------------------------
# WU-007: send/wait button gating and loading indicator tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_button_label_and_disabled_state() -> None:
    """Verify button shows 'Wait' when is_waiting=True and 'Send' when False."""
    from textual.widgets import Button

    app = OrchestratorApp()
    async with app.run_test() as pilot:
        button = pilot.app.query_one("#send-button", Button)
        
        pilot.app.is_waiting = True
        await pilot.pause()
        assert button.label.plain == "⏳ Wait"
        assert button.disabled is True

        pilot.app.is_waiting = False
        await pilot.pause()
        assert button.label.plain == "Send"
        assert button.disabled is False


@pytest.mark.asyncio
async def test_submission_gated_by_is_waiting() -> None:
    """Verify MessageSubmitted is not posted if is_waiting=True."""
    submitted: list[str] = []

    app = OrchestratorApp()
    async with app.run_test() as pilot:
        pilot.app.is_waiting = True
        
        input_widget = pilot.app.query_one("#message-input", MessageInput)
        
        original_post = input_widget.post_message
        def capture(msg):
            if isinstance(msg, MessageSubmitted):
                submitted.append(msg.text)
            return original_post(msg)
        input_widget.post_message = capture  # type: ignore[method-assign]
        
        # Test Alt+Enter submission
        input_widget.focus()
        await pilot.press("t", "e", "s", "t")
        await pilot.press("alt+enter")
        assert len(submitted) == 0

        # Now test submitting via the Send button
        await pilot.click("#send-button")
        assert len(submitted) == 0

        # Now enable it and verify it submits
        pilot.app.is_waiting = False
        await pilot.pause()
        
        await pilot.click("#send-button")
        assert len(submitted) == 1
        assert submitted[0] == "test"


# ---------------------------------------------------------------------------
# WU-008: status bar tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_update_status() -> None:
    """Status bar should correctly parse state and display WUs count."""
    from rich.text import Text

    app = OrchestratorApp()
    async with app.run_test() as pilot:
        state = {
            "work_units": {
                "WU-001": {"status": "complete"},
                "WU-002": {"status": "complete"},
                "WU-003": {"status": "in_progress"},
                "WU-004": {"status": "pending"},
                "WU-005": {"status": "pending"},
            }
        }
        pilot.app.update_status(state)
        await pilot.pause()

        status_bar = pilot.app.query_one("#status-bar")
        rendered = status_bar.render()
        text = rendered.plain if isinstance(rendered, Text) else str(rendered)
        
        assert "2/5 complete" in text
        assert "WU-003" in text


@pytest.mark.asyncio
async def test_set_phase() -> None:
    """Status bar should display the updated phase."""
    from rich.text import Text

    app = OrchestratorApp()
    async with app.run_test() as pilot:
        pilot.app.set_phase("IMPLEMENTING")
        await pilot.pause()

        status_bar = pilot.app.query_one("#status-bar")
        rendered = status_bar.render()
        text = rendered.plain if isinstance(rendered, Text) else str(rendered)

        assert "IMPLEMENTING" in text


# ---------------------------------------------------------------------------
# WU-009: TUI input routing tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_request_input_resolves_on_submission() -> None:
    """request_input() should resolve when the user submits text via the input widget."""
    import asyncio

    app = OrchestratorApp()
    async with app.run_test() as pilot:
        # Start request_input in a task so we can type while it awaits
        input_task = asyncio.create_task(pilot.app.request_input("What is your name?"))
        await pilot.pause()

        # The prompt should have been written to the system log
        from textual.widgets import RichLog
        log = pilot.app.query_one("#system-log", RichLog)
        log_text = "\n".join(str(line) for line in log.lines)
        assert "What is your name?" in log_text

        # Simulate the user typing a response and submitting via Alt+Enter
        input_widget = pilot.app.query_one("#message-input", MessageInput)
        input_widget.focus()
        await pilot.press("A", "l", "i", "c", "e")
        await pilot.press("alt+enter")
        await pilot.pause()

        result = await input_task
        assert result == "Alice"


@pytest.mark.asyncio
async def test_message_submitted_resolves_pending_input_not_queue() -> None:
    """When _pending_input is set, MessageSubmitted resolves it instead of the queue."""
    import asyncio

    app = OrchestratorApp()
    queue: asyncio.Queue = asyncio.Queue()

    async with app.run_test() as pilot:
        pilot.app.set_input_queue(queue)

        # Kick off a request_input so _pending_input is set
        input_task = asyncio.create_task(pilot.app.request_input("Continue? "))
        await pilot.pause()

        input_widget = pilot.app.query_one("#message-input", MessageInput)
        input_widget.focus()
        await pilot.press("y", "e", "s")
        await pilot.press("alt+enter")
        await pilot.pause()

        result = await input_task
        assert result == "yes"
        # Queue should NOT have received anything
        assert queue.empty()


@pytest.mark.asyncio
async def test_normal_message_goes_to_queue_when_no_pending_input() -> None:
    """When no _pending_input, MessageSubmitted puts text into the orchestrator queue."""
    import asyncio

    app = OrchestratorApp()
    queue: asyncio.Queue = asyncio.Queue()

    async with app.run_test() as pilot:
        pilot.app.set_input_queue(queue)

        # Clear any pending orchestrator future so message routes to the queue
        if pilot.app._pending_input and not pilot.app._pending_input.done():
            pilot.app._pending_input.set_result("")
        await pilot.pause()
        pilot.app._pending_input = None

        input_widget = pilot.app.query_one("#message-input", MessageInput)
        input_widget.focus()
        await pilot.press("h", "e", "l", "l", "o")
        await pilot.press("alt+enter")
        await pilot.pause()

        assert not queue.empty()
        assert queue.get_nowait() == "hello"


@pytest.mark.asyncio
async def test_tui_input_with_tui_app(monkeypatch) -> None:
    """tui_input() delegates to _tui_app.request_input when TUI is active."""
    import orchestrate.orchestrator as orch

    class _FakeApp:
        async def request_input(self, prompt: str) -> str:
            return f"response to: {prompt}"

    monkeypatch.setattr(orch, "_tui_app", _FakeApp())
    result = await orch.tui_input("hello?")
    assert result == "response to: hello?"


@pytest.mark.asyncio
async def test_tui_input_without_tui_app_falls_back_to_builtin_input(monkeypatch) -> None:
    """tui_input() falls back to input() when _tui_app is None."""
    import orchestrate.orchestrator as orch

    monkeypatch.setattr(orch, "_tui_app", None)
    monkeypatch.setattr("builtins.input", lambda _: "fallback")
    result = await orch.tui_input("prompt?")
    assert result == "fallback"


# ---------------------------------------------------------------------------
# WU-010: @-mention autocomplete tests
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_project(tmp_path):
    """Create a temp directory with a few files for autocomplete testing."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "foo.py").write_text("# foo")
    (tmp_path / "src" / "bar.py").write_text("# bar")
    (tmp_path / "README.md").write_text("# readme")
    return tmp_path


@pytest.mark.asyncio
async def test_autocomplete_overlay_exists_in_compose() -> None:
    """AutoCompleteOverlay should be present in the composed app."""
    app = OrchestratorApp()
    async with app.run_test() as pilot:
        overlay = pilot.app.query_one("#autocomplete-overlay", AutoCompleteOverlay)
        assert overlay is not None


@pytest.mark.asyncio
async def test_autocomplete_overlay_hidden_by_default() -> None:
    """Overlay should be hidden at startup."""
    app = OrchestratorApp()
    async with app.run_test() as pilot:
        overlay = pilot.app.query_one("#autocomplete-overlay", AutoCompleteOverlay)
        assert overlay.display is False


@pytest.mark.asyncio
async def test_autocomplete_shows_for_at_mention_3_chars(tmp_project) -> None:
    """Typing @foo in the input should show the overlay with matching files."""
    app = OrchestratorApp()
    async with app.run_test() as pilot:
        input_widget = pilot.app.query_one("#message-input", MessageInput)
        input_widget._root_dir = str(tmp_project)
        input_widget._file_cache = None  # Reset cache so it picks up new root_dir

        input_widget.focus()
        await pilot.press("@", "f", "o", "o")
        await pilot.pause()
        await pilot.pause()

        overlay = pilot.app.query_one("#autocomplete-overlay", AutoCompleteOverlay)
        assert overlay.display is True
        assert any("foo.py" in path for path in overlay._completions)


@pytest.mark.asyncio
async def test_autocomplete_hidden_for_at_mention_2_chars(tmp_project) -> None:
    """Typing only @fo (2 chars after @) should NOT show the overlay."""
    app = OrchestratorApp()
    async with app.run_test() as pilot:
        input_widget = pilot.app.query_one("#message-input", MessageInput)
        input_widget._root_dir = str(tmp_project)
        input_widget._file_cache = None

        input_widget.focus()
        await pilot.press("@", "f", "o")
        await pilot.pause()
        await pilot.pause()

        overlay = pilot.app.query_one("#autocomplete-overlay", AutoCompleteOverlay)
        assert overlay.display is False


@pytest.mark.asyncio
async def test_autocomplete_hidden_without_at_symbol(tmp_project) -> None:
    """Typing text without @ should not trigger the overlay."""
    app = OrchestratorApp()
    async with app.run_test() as pilot:
        input_widget = pilot.app.query_one("#message-input", MessageInput)
        input_widget._root_dir = str(tmp_project)
        input_widget._file_cache = None

        input_widget.focus()
        await pilot.press("f", "o", "o", "b", "a", "r")
        await pilot.pause()
        await pilot.pause()

        overlay = pilot.app.query_one("#autocomplete-overlay", AutoCompleteOverlay)
        assert overlay.display is False


@pytest.mark.asyncio
async def test_accept_completion_replaces_at_query(tmp_project) -> None:
    """accept_completion() should replace the @query with the selected path."""
    app = OrchestratorApp()
    async with app.run_test() as pilot:
        input_widget = pilot.app.query_one("#message-input", MessageInput)
        input_widget._root_dir = str(tmp_project)
        input_widget._file_cache = None

        input_widget.focus()
        await pilot.press("@", "f", "o", "o")
        await pilot.pause()
        await pilot.pause()

        # Manually accept completion
        input_widget.accept_completion("src/foo.py")

        assert "src/foo.py" in input_widget.text
        assert "@foo" not in input_widget.text
