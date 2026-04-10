"""Textual TUI for the development workflow orchestrator."""
from __future__ import annotations

import asyncio
import re

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.message import Message
from textual.reactive import reactive
from rich.text import Text
from textual.widgets import (
    Button,
    Footer,
    Header,
    Label,
    ListItem,
    ListView,
    LoadingIndicator,
    RichLog,
    Static,
    TabbedContent,
    TabPane,
    TextArea,
)


class AutoCompleteOverlay(ListView):
    """Dropdown overlay for @-mention file completion."""

    DEFAULT_CSS = """
    AutoCompleteOverlay {
        display: none;
        layer: overlay;
        max-height: 10;
        width: 50;
        background: $surface;
        border: solid $primary;
        overflow-y: auto;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._completions: list[str] = []

    def show_completions(self, completions: list[str]) -> None:
        self._completions = completions[:20]
        self.clear()
        for path in self._completions:
            self.append(ListItem(Label(path)))
        self.display = bool(self._completions)

    def hide(self) -> None:
        self.display = False
        self.clear()
        self._completions = []

    def get_selected_path(self) -> str | None:
        if self.index is not None and 0 <= self.index < len(self._completions):
            return self._completions[self.index]
        return None


class MessageSubmitted(Message):
    """Posted when the user submits a message."""

    def __init__(self, text: str) -> None:
        super().__init__()
        self.text = text


class MessageInput(TextArea):
    """Multi-line input with Alt+Enter / Esc→Enter submission and @-mention autocomplete."""

    BINDINGS = [
        Binding("alt+enter", "submit", "Send", show=False),
    ]

    def __init__(self, root_dir: str = ".", **kwargs) -> None:
        super().__init__(**kwargs)
        self._escape_pressed = False
        self._root_dir = root_dir
        self._file_cache: list[str] | None = None

    def _get_files(self) -> list[str]:
        if self._file_cache is None:
            from .console import AtMentionCompleter
            completer = AtMentionCompleter(self._root_dir)
            self._file_cache = completer._get_all_files()
        return self._file_cache

    def action_submit(self) -> None:
        app = self.app
        if isinstance(app, OrchestratorApp) and app.is_waiting:
            return  # Gated — don't submit while waiting
        text = self.text.strip()
        if text:
            self.post_message(MessageSubmitted(text))
            self.clear()
        self._escape_pressed = False
        self._hide_autocomplete()

    def _on_key(self, event) -> None:
        if event.key == "escape":
            self._escape_pressed = True
            event.prevent_default()
            return
        if event.key == "enter" and self._escape_pressed:
            self._escape_pressed = False
            event.prevent_default()
            self.action_submit()
            return
        self._escape_pressed = False
        # Schedule autocomplete check after the key is processed
        self.call_after_refresh(self._check_autocomplete)

    def _hide_autocomplete(self) -> None:
        try:
            overlay = self.app.query_one("#autocomplete-overlay", AutoCompleteOverlay)
            overlay.hide()
        except Exception:
            pass

    def _check_autocomplete(self) -> None:
        try:
            overlay = self.app.query_one("#autocomplete-overlay", AutoCompleteOverlay)
        except Exception:
            return

        lines = self.text.split("\n")
        row, col = self.cursor_location
        text_before = lines[row][:col] if row < len(lines) else ""

        match = re.search(r'@([^\s]*)$', text_before)
        if not match or len(match.group(1)) < 3:
            overlay.hide()
            return

        search_term = match.group(1).lower()
        files = self._get_files()
        matches = [f for f in files if search_term in f.lower()][:20]
        overlay.show_completions(matches)

    def accept_completion(self, path: str) -> None:
        """Replace the @query fragment with the selected file path."""
        lines = self.text.split("\n")
        row, col = self.cursor_location
        line = lines[row]
        text_before = line[:col]
        match = re.search(r'@([^\s]*)$', text_before)
        if match:
            start = match.start(1)
            new_line = line[:start] + path + line[col:]
            lines[row] = new_line
            self.load_text("\n".join(lines))
            new_col = start + len(path)
            self.cursor_location = (row, new_col)
        self._hide_autocomplete()


class StatusBar(Static):
    """Persistent status widget showing current orchestrator state."""

    DEFAULT_CSS = """
    StatusBar {
        dock: top;
        height: 3;
        background: $surface;
        border: solid $primary;
        padding: 0 1;
        content-align: left middle;
    }
    """

    phase: reactive[str] = reactive("IDLE")
    active_wus: reactive[list[str]] = reactive(list)
    done_count: reactive[int] = reactive(0)
    total_count: reactive[int] = reactive(0)

    def render(self) -> Text:
        wu_str = ", ".join(self.active_wus) if self.active_wus else "none"
        return Text.from_markup(
            f" [bold]{self.phase}[/bold] — {wu_str} — "
            f"[green]{self.done_count}[/green]/{self.total_count} complete"
        )

    def update_from_state(self, state: dict) -> None:
        """Update status from orchestrator state dict."""
        wus = state.get("work_units", {})
        self.total_count = len(wus)
        self.done_count = sum(1 for wu in wus.values() if wu.get("status") == "complete")
        self.active_wus = [
            wu_id for wu_id, wu in wus.items()
            if wu.get("status") == "in_progress"
        ]

    def set_phase(self, phase: str) -> None:
        """Update the current phase display."""
        self.phase = phase


class OrchestratorApp(App):
    """Main TUI application for the orchestrator."""

    CSS_PATH = "tui.tcss"
    TITLE = "Dev Workflow Orchestrator"

    BINDINGS = [
        Binding("ctrl+left", "previous_tab", "Prev Tab"),
        Binding("ctrl+right", "next_tab", "Next Tab"),
        Binding("ctrl+c", "quit", "Quit", show=False),
    ]

    is_waiting: reactive[bool] = reactive(False)

    _pending_input: asyncio.Future | None = None
    _orchestrator_input_queue: asyncio.Queue | None = None

    def __init__(self, repo_root: str = ".", resume: str | None = None, transition_pauses: bool = False, **kwargs) -> None:
        super().__init__(**kwargs)
        self.repo_root = repo_root
        self.resume = resume
        self.transition_pauses = transition_pauses
        # Maps tab_id -> RichLog; populated as agent tabs are created.
        self._agent_logs: dict[str, RichLog] = {}

    async def on_mount(self) -> None:
        """Start the orchestrator as a background worker after the app mounts."""
        self.run_worker(self._run_orchestrator(), exclusive=True)

    async def _run_orchestrator(self) -> None:
        """Run the orchestrator loop inside the Textual app."""
        from .orchestrator import run_orchestrator, set_tui

        set_tui(self)
        try:
            if self.resume:
                await run_orchestrator(self.repo_root, "", session_name=self.resume, resume=True, transition_pauses=self.transition_pauses)
            else:
                initial_prompt = await self.request_input("Describe what you want to build: ")
                await run_orchestrator(self.repo_root, initial_prompt, resume=False, transition_pauses=self.transition_pauses)
            self.append_to_output("[bold green]Orchestrator complete.[/bold green]")
        except SystemExit as e:
            self.append_to_output(f"[bold red]{e}[/bold red]")
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            self.append_to_output(f"[bold red]Orchestrator error: {e}[/bold red]\n{tb}")
        finally:
            self.is_waiting = False

    def action_quit(self) -> None:
        """Handle Ctrl+C gracefully."""
        self.exit()

    def compose(self) -> ComposeResult:
        yield StatusBar(id="status-bar")
        with TabbedContent(id="output-area"):
            with TabPane("System", id="tab-system"):
                yield RichLog(id="system-log", markup=True, wrap=True, max_lines=10000)
        with Horizontal(id="input-area"):
            yield MessageInput(id="message-input")
            yield Button("Send", id="send-button", variant="primary")
            yield LoadingIndicator(id="loading-indicator")
        yield AutoCompleteOverlay(id="autocomplete-overlay")

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Handle autocomplete selection."""
        try:
            overlay = self.query_one("#autocomplete-overlay", AutoCompleteOverlay)
        except Exception:
            return
        path = overlay.get_selected_path()
        if path:
            try:
                input_widget = self.query_one("#message-input", MessageInput)
                input_widget.accept_completion(path)
            except Exception:
                pass

    def on_message_submitted(self, event: MessageSubmitted) -> None:
        """Handle user message submission."""
        if self._pending_input and not self._pending_input.done():
            self._pending_input.set_result(event.text)
        else:
            self.append_to_output(f"[bold]You:[/bold] {event.text}")
            if self._orchestrator_input_queue is not None:
                self._orchestrator_input_queue.put_nowait(event.text)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "send-button":
            if not self.is_waiting:
                input_widget = self.query_one("#message-input", MessageInput)
                input_widget.action_submit()

    def watch_is_waiting(self, waiting: bool) -> None:
        """Toggle the send button between Send and Loading states."""
        try:
            button = self.query_one("#send-button", Button)
        except Exception:
            return

        if waiting:
            button.label = "⏳ Wait"
            button.disabled = True
            self._show_loading(True)
        else:
            button.label = "Send"
            button.disabled = False
            self._show_loading(False)

    def _show_loading(self, show: bool) -> None:
        """Toggle visibility of the loading indicator."""
        try:
            indicator = self.query_one("#loading-indicator", LoadingIndicator)
            indicator.display = show
        except Exception:
            pass

    def set_waiting(self, waiting: bool) -> None:
        """Called by orchestrator integration to toggle waiting state."""
        self.is_waiting = waiting

    def set_input_queue(self, queue: asyncio.Queue) -> None:
        """Register the orchestrator's input queue for normal message routing."""
        self._orchestrator_input_queue = queue

    async def request_input(self, prompt_text: str) -> str:
        """Display a prompt and wait for the user's response.

        Called from orchestrator async code. Shows the prompt in the output panel,
        enables the input area, and returns the submitted text.
        """
        # Switch to system tab so the user sees the prompt and any context written before it.
        try:
            tabbed = self.query_one("#output-area", TabbedContent)
            tabbed.active = "tab-system"
        except Exception:
            pass
        self.append_to_output(f"[bold yellow]>>> {prompt_text}[/bold yellow]")
        self.is_waiting = False  # Enable input so user can respond

        loop = asyncio.get_running_loop()
        self._pending_input = loop.create_future()
        result = await self._pending_input
        self._pending_input = None
        return result

    def update_status(self, state: dict) -> None:
        """Update the status bar from orchestrator state."""
        status_bar = self.query_one("#status-bar", StatusBar)
        status_bar.update_from_state(state)

    def set_phase(self, phase: str) -> None:
        """Update the phase displayed in the status bar."""
        status_bar = self.query_one("#status-bar", StatusBar)
        status_bar.set_phase(phase)

    def append_to_output(self, text: str, tab_id: str = "tab-system") -> None:
        """Append a message to the specified output tab's RichLog."""
        # Fast path: use cached log reference for agent tabs.
        if tab_id in self._agent_logs:
            self._agent_logs[tab_id].write(text)
            return
        # Default path: query the DOM (works for the static system tab).
        try:
            pane = self.query_one(f"#{tab_id}", TabPane)
            log = pane.query_one(RichLog)
            log.write(text)
        except Exception:
            # Fallback to system log
            try:
                system_log = self.query_one("#system-log", RichLog)
                system_log.write(text)
            except Exception:
                pass

    def create_agent_tab(self, agent_name: str) -> str:
        """Create a new tab for an agent. Returns the tab pane ID."""
        tab_id = f"tab-{agent_name}"
        tabbed = self.query_one("#output-area", TabbedContent)
        log = RichLog(markup=True, wrap=True, max_lines=10000)
        pane = TabPane(agent_name, log, id=tab_id)
        # Register the log before add_pane so append_to_output works immediately
        # (add_pane is not awaited, so the DOM mount is deferred).
        self._agent_logs[tab_id] = log
        tabbed.add_pane(pane)
        return tab_id

    def remove_agent_tab(self, agent_name: str) -> None:
        """Remove an agent's tab after it completes."""
        tab_id = f"tab-{agent_name}"
        self._agent_logs.pop(tab_id, None)
        tabbed = self.query_one("#output-area", TabbedContent)
        try:
            tabbed.remove_pane(tab_id)
        except Exception:
            pass  # Tab may already be removed

    def activate_tab(self, agent_name: str) -> None:
        """Switch the visible tab to the specified agent."""
        tab_id = f"tab-{agent_name}"
        tabbed = self.query_one("#output-area", TabbedContent)
        tabbed.active = tab_id

    def append_agent_message(self, agent_name: str, text: str) -> None:
        """Append a message to an agent's tab, creating the tab if needed."""
        tab_id = f"tab-{agent_name}"
        try:
            self.query_one(f"#{tab_id}", TabPane)
        except Exception:
            self.create_agent_tab(agent_name)
        self.append_to_output(text, tab_id)

    def action_previous_tab(self) -> None:
        tabbed = self.query_one("#output-area", TabbedContent)
        tabbed.action_previous_tab()

    def action_next_tab(self) -> None:
        tabbed = self.query_one("#output-area", TabbedContent)
        tabbed.action_next_tab()

    def append_checkpoint(
        self,
        state_name: str,
        transition: str,
        progress: str,
        batch: int | None = None,
        wus: list[str] | None = None,
        notes: str | None = None,
    ) -> None:
        """Render a state machine checkpoint into the system output tab."""
        lines = [
            "[bold cyan]══════ CHECKPOINT ══════[/bold cyan]",
            f"  [bold]state:[/bold]      {state_name}",
            f"  [bold]transition:[/bold] {transition}",
            f"  [bold]progress:[/bold]   {progress}",
        ]
        if batch is not None:
            wu_str = ", ".join(wus or [])
            lines.append(f"  [bold]batch:[/bold]      {batch}  wus: {wu_str}")
        if notes:
            lines.append(f"  [bold]notes:[/bold]      {notes}")
        self.append_to_output("\n".join(lines))
