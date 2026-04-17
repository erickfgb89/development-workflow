"""Interactive context gatherer.

Runs a multi-turn terminal conversation with the Gatherer agent to produce
context.md for the session.  UI features:

- Bottom status bar showing session slug, turn count, and phase.
- Backslash-continuation: a line ending with '\\' lets the user keep typing
  before submitting (mimics Claude Code's Shift+Enter multi-line behaviour
  on dumb terminals).
- '/done' command signals "happy path" — session is renamed and context saved.
- '/abort' exits without saving.
"""

from __future__ import annotations

import asyncio
import os
import readline  # noqa: F401 — enables word-navigation (Alt+Backspace, Ctrl+W) in input()
import sys
import textwrap
from pathlib import Path

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

_INITIAL_AGENT_PROMPT = textwrap.dedent("""\
    You are the Gatherer agent for an Overture session.

    Your job is to have a focused conversation with the developer to build a
    complete, unambiguous context document that will be handed off to the
    Planner.

    Start by asking:
    1. What is the goal of this task? (one sentence)
    2. What is the current state of the codebase / environment?
    3. Are there any known constraints, blockers, or things to avoid?
    4. What does "done" look like? (Acceptance criteria in plain English)

    Ask follow-up questions until you feel confident you can write a
    complete context.md.  When you are satisfied, tell the developer to type
    /done to finalise the session.  If they want to stop early they can type
    /abort.

    Keep your questions short and clear.  One or two questions per turn.
    Do not write context.md yet — just gather information conversationally.
""")


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

    # Accumulate the conversation as plain text so we can write context.md
    transcript: list[dict[str, str]] = []

    async with GathererSession(system_prompt=system_prompt, cwd=target_repo) as session:
        # Kick off the agent with the initial instruction
        _clear_status()
        reply = await session.send(_INITIAL_AGENT_PROMPT)
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
                # Ask the agent to write the final context.md
                _clear_status()
                print(f"\n{_DIM}Generating context.md…{_RESET}")
                context_reply = await session.send(
                    "The developer has signalled they are done providing context.  "
                    "Now write the complete context.md document.  "
                    "Format it with: ## Goal, ## Current State, ## Constraints, "
                    "## Acceptance Criteria, and ## Notes sections.  "
                    "\n\n"
                    "IMPORTANT: The first line after ## Goal will be used as the session name. "
                    "It must be 2–5 words, terse, and suitable for browser tabs, directory names, and logs. "
                    "Examples: 'Implement user auth', 'Fix search performance', 'Add dark mode toggle'. "
                    "Avoid complete sentences or complex grammar. "
                    "\n\n"
                    "Output ONLY the markdown, no extra commentary."
                )
                session_manager.write_context(context_reply)

                # Derive a slug from the goal line in context.md
                slug = _extract_slug(context_reply) or session_id
                final_slug = session_manager.rename_to_slug(slug)

                print(f"\n{_BOLD}Context saved.{_RESET}  Session: {_CYAN}{final_slug}{_RESET}\n")
                return True

            transcript.append({"role": "user", "content": user_input})
            _clear_status()
            reply = await session.send(user_input)
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
