"""Interactive context gatherer.

Runs a multi-turn terminal conversation with the Gatherer agent to produce
context.md for the session.  Two modes:

Terminal mode (run_gather):
- Bottom status bar showing session slug, turn count, and phase.
- Backslash-continuation: a line ending with '\\' lets the user keep typing
  before submitting (mimics Claude Code's Shift+Enter multi-line behaviour
  on dumb terminals).
- '/done' command signals "happy path" — session is renamed and context saved.
- '/abort' exits without saving.

Web mode (WebGathererSession):
- Manages a persistent GathererSession across multiple HTTP requests.
- Caller drives the loop: send() for each user turn, done() to finalise.
- Context is written to session_manager when done() is called.
"""

from __future__ import annotations

import asyncio
import os
import readline  # noqa: F401 — enables word-navigation (Alt+Backspace, Ctrl+W) in input()
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .sdk_wrapper import GathererSession
from .session_manager import SessionManager

# ANSI helpers ---------------------------------------------------------------

_ESC = "\033"
_HIDE_CURSOR = f"{_ESC}[?25l"
_SHOW_CURSOR = f"{_ESC}[?25h"
_CLEAR_LINE = f"{_ESC}[2K"
_MOVE_COL1 = "\r"
_BOLD = f"{_ESC}[1m"
_DIM = f"{_ESC}[2m"
_CYAN = f"{_ESC}[36m"
_RESET = f"{_ESC}[0m"


def _term_width() -> int:
    try:
        return os.get_terminal_size().columns
    except OSError:
        return 80


def _status_line(session_id: str, turn: int, phase: str) -> str:
    width = _term_width()
    left = f" {_BOLD}{_CYAN}overture{_RESET}  session: {session_id}  turn: {turn}  phase: {phase} "
    padding = max(0, width - len(_strip_ansi(left)))
    return left + " " * padding


_ANSI_ESCAPE = None


def _strip_ansi(text: str) -> str:
    import re
    global _ANSI_ESCAPE
    if _ANSI_ESCAPE is None:
        _ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")
    return _ANSI_ESCAPE.sub("", text)


def _print_status(session_id: str, turn: int, phase: str) -> None:
    """Overwrite the last line with the status bar."""
    bar = _status_line(session_id, turn, phase)
    # Move to start of line, clear, print bar
    sys.stderr.write(f"{_MOVE_COL1}{_CLEAR_LINE}{_DIM}{bar}{_RESET}\n")
    sys.stderr.flush()


def _clear_status() -> None:
    sys.stderr.write(f"{_MOVE_COL1}{_CLEAR_LINE}")
    sys.stderr.flush()


# ---------------------------------------------------------------------------
# Prompt loading
# ---------------------------------------------------------------------------

def _load_prompt(name: str) -> str:
    """Load a prompt file from the prompts/ directory next to the package root."""
    prompts_dir = Path(__file__).parent.parent.parent / "prompts"
    path = prompts_dir / name
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def _gatherer_system_prompt() -> str:
    core = _load_prompt("core.md")
    planning = _load_prompt("common_planning.md")
    gatherer = _load_prompt("task_gatherer.md")
    return "\n\n".join(filter(None, [core, planning, gatherer]))


# ---------------------------------------------------------------------------
# MCP tool: write_context
# ---------------------------------------------------------------------------

def _make_context_mcp_server(context_path: Path) -> FastMCP:
    """Return a FastMCP server with a single ``write_context`` tool.

    The tool writes its argument to *context_path* and returns ``"OK"``.
    Registering this as the *only* write surface for the Gatherer agent
    ensures it cannot modify any other file in the repository.
    """
    mcp = FastMCP("overture-gatherer")

    @mcp.tool(
        name="write_context",
        description=(
            "Write the completed context document and signal that gathering is done. "
            "Call this ONCE with the full context.md markdown as 'content'. "
            "This is your ONLY permitted file-write action, and calling it ends the session."
        ),
    )
    def write_context(content: str) -> str:  # noqa: WPS430
        context_path.write_text(content, encoding="utf-8")
        return "OK"

    return mcp


# ---------------------------------------------------------------------------
# Multi-line input
# ---------------------------------------------------------------------------

def _read_multiline(prompt_text: str) -> str:
    """Read user input, supporting backslash-continuation.

    A line ending with a single backslash means "continue on next line."
    The backslash is stripped and input accumulates until a non-continuation
    line is entered.
    """
    lines: list[str] = []
    continuation_prompt = "... "
    current_prompt = prompt_text
    while True:
        try:
            line = input(current_prompt)
        except EOFError:
            break
        if line.endswith("\\"):
            lines.append(line[:-1])
            current_prompt = continuation_prompt
        else:
            lines.append(line)
            break
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# INITIAL_PROMPT
# ---------------------------------------------------------------------------

_INITIAL_AGENT_PROMPT = "Begin the context gathering session."


# ---------------------------------------------------------------------------
# Main gather loop
# ---------------------------------------------------------------------------

async def run_gather(session_manager: SessionManager, target_repo: Path) -> bool:
    """Run the interactive gathering session.

    Returns True if the user completed (/done) or False if they aborted.
    """
    system_prompt = _gatherer_system_prompt()
    session_id = session_manager.session_id
    turn = 0

    print()
    print(f"{_BOLD}Overture — Context Gatherer{_RESET}")
    print(f"{_DIM}Type your replies below.  End a line with \\ to continue on the next line.")
    print(f"Commands:  /done  (save & continue)   /abort  (quit without saving){_RESET}")
    print()

    _print_status(session_id, turn, "gathering")

    transcript: list[dict[str, str]] = []

    context_path = session_manager.context_path()
    mcp_server = _make_context_mcp_server(context_path)

    async with GathererSession(
        system_prompt=system_prompt,
        cwd=target_repo,
        mcp_server=mcp_server,
    ) as session:
        # Kick off the agent with the initial instruction
        _clear_status()
        reply, _thinking = await session.send(_INITIAL_AGENT_PROMPT)
        turn += 1
        _print_assistant(reply)
        _print_status(session_id, turn, "gathering")
        transcript.append({"role": "assistant", "content": reply})

        while True:
            user_input = _read_multiline(f"\n{_BOLD}You:{_RESET} ").strip()

            if not user_input:
                continue

            cmd = user_input.lower()

            if cmd == "/abort":
                print(f"\n{_DIM}Session aborted.{_RESET}\n")
                return False

            if cmd == "/done":
                _clear_status()
                print(f"\n{_DIM}Generating context.md…{_RESET}")
                await session.send(
                    "The developer has signalled they are done providing context.  "
                    "Now compose the complete context.md document and call `write_context` "
                    "with the full markdown as its argument.  "
                    "Format the document with: ## Goal, ## Current State, ## Constraints, "
                    "## Acceptance Criteria, and ## Notes sections.  "
                    "\n\n"
                    "IMPORTANT: The first line after ## Goal will be used as the session name. "
                    "It must be 2–5 words, terse, and suitable for browser tabs, directory names, and logs. "
                    "Examples: 'Implement user auth', 'Fix search performance', 'Add dark mode toggle'. "
                    "Avoid complete sentences or complex grammar. "
                    "\n\n"
                    "Call `write_context` once with the complete markdown — do NOT output the "
                    "markdown as plain text.  Calling `write_context` is your final action."
                )

                # The agent wrote the file via the MCP tool; read it back.
                context_reply = context_path.read_text(encoding="utf-8")

                # Derive a slug from the goal line in context.md
                slug = _extract_slug(context_reply) or session_id
                final_slug = session_manager.rename_to_slug(slug)

                print(f"\n{_BOLD}Context saved.{_RESET}  Session: {_CYAN}{final_slug}{_RESET}\n")
                return True

            transcript.append({"role": "user", "content": user_input})
            _clear_status()
            reply, _thinking = await session.send(user_input)
            turn += 1
            _print_assistant(reply)
            _print_status(session_manager.session_id, turn, "gathering")
            transcript.append({"role": "assistant", "content": reply})


def _print_assistant(text: str) -> None:
    print(f"\n{_BOLD}{_CYAN}Overture:{_RESET} {text}")


def _extract_slug(context_md: str) -> str:
    """Pull the Goal line and enforce session naming constraints.

    Session names must be 2-5 words, terse, and suitable for:
    - Browser tabs / headers
    - Directory names
    - Log output / status lines

    Returns the constraint-compliant slug, or empty string if none found.
    """
    for line in context_md.splitlines():
        line = line.strip()
        if line.startswith("## Goal"):
            # Next non-empty line after the heading
            continue
        if line and not line.startswith("#"):
            # Extract first 2-5 words as the slug
            words = line.split()
            slug = " ".join(words[:5])  # Take up to 5 words
            # Bail out if fewer than 2 words (too sparse to be meaningful)
            if len(words) < 2:
                continue
            return slug
    return ""


# ---------------------------------------------------------------------------
# Web-mode gatherer (driven by HTTP rather than terminal input)
# ---------------------------------------------------------------------------

_DONE_PROMPT = (
    "The developer has signalled they are done providing context.  "
    "Now compose the complete context.md document and call `write_context` "
    "with the full markdown as its argument.  "
    "Format the document with: ## Goal, ## Current State, ## Constraints, "
    "## Acceptance Criteria, and ## Notes sections.  "
    "\n\n"
    "IMPORTANT: The first line after ## Goal will be used as the session name. "
    "It must be 2–5 words, terse, and suitable for browser tabs, directory names, and logs. "
    "Examples: 'Implement user auth', 'Fix search performance', 'Add dark mode toggle'. "
    "Avoid complete sentences or complex grammar. "
    "\n\n"
    "Call `write_context` once with the complete markdown — do NOT output the "
    "markdown as plain text.  Calling `write_context` is your final action."
)

_REOPEN_PROMPT = (
    "The user wishes to continue context gathering with you.  "
    "Resume the conversation naturally — ask if there is additional context, "
    "corrections, or new requirements they'd like to capture before they signal "
    "done again."
)


class WebGathererSession:
    """A persistent gatherer conversation that lives across HTTP requests.

    Streaming lifecycle::

        wgs = WebGathererSession(session_manager, target_repo)
        async for kind, chunk in wgs.stream_start():   # open + first agent turn
            ...  # kind in ("thinking", "text", "done")
        async for kind, chunk in wgs.stream_send("user text"):
            ...
        slug = await wgs.finalise()   # ask agent to call write_context, rename session
        await wgs.close()

    The caller is responsible for calling close() when done.
    """

    def __init__(self, session_manager: SessionManager, target_repo: Path) -> None:
        self._sm = session_manager
        self._target_repo = target_repo
        self._system_prompt = _gatherer_system_prompt()
        self._session: GathererSession | None = None
        self._context_path = session_manager.context_path()

    async def __aenter__(self) -> "WebGathererSession":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    def _ensure_session(self) -> GathererSession:
        if self._session is None:
            raise RuntimeError("WebGathererSession not started — call stream_start() first")
        return self._session

    async def _open_session(self) -> None:
        mcp_server = _make_context_mcp_server(self._context_path)
        self._session = GathererSession(
            system_prompt=self._system_prompt,
            cwd=self._target_repo,
            mcp_server=mcp_server,
        )
        await self._session.__aenter__()

    async def stream_start(self):  # type: ignore[return]
        """Open the SDK session and stream the opening agent turn."""
        await self._open_session()
        async for event in self._session.stream(_INITIAL_AGENT_PROMPT):  # type: ignore[union-attr]
            yield event

    async def stream_send(self, user_text: str):  # type: ignore[return]
        """Stream one user turn."""
        sess = self._ensure_session()
        async for event in sess.stream(user_text):
            yield event

    async def stream_done(self):  # type: ignore[return]
        """Ask the agent to finalise (call write_context) and stream its response."""
        sess = self._ensure_session()
        async for event in sess.stream(_DONE_PROMPT):
            yield event

    async def finalise(self) -> str:
        """Read the written context file and rename the session.  Returns new slug."""
        context_reply = self._context_path.read_text(encoding="utf-8")
        slug = _extract_slug(context_reply) or self._sm.session_id
        return self._sm.rename_to_slug(slug)

    async def close(self) -> None:
        """Tear down the underlying SDK session."""
        if self._session is not None:
            try:
                await self._session.__aexit__(None, None, None)
            except Exception:
                pass
            self._session = None
