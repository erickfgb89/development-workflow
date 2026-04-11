"""Orchestrator — deterministic state machine driving the AI development workflow."""
from __future__ import annotations

import asyncio
import json
import uuid
import re
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path
from typing import Any, Callable, Awaitable

from claude_agent_sdk import query, ClaudeAgentOptions, ResultMessage, AssistantMessage
from claude_agent_sdk import TextBlock, ToolUseBlock

# ---------------------------------------------------------------------------
# Message sink type
# ---------------------------------------------------------------------------

# Async callback forwarded one (agent_name, message_text) pair per SDK message.
MessageSink = Callable[[str, str], Awaitable[None]]

from .mcp_server import workflow_server
from .state import (
    load_state, save_state, find_state_file,
    extract_batch, set_wu_status, set_wu_field, increment_recall,
    pending_wus, in_progress_wus, all_complete, parse_json_result,
)
from .worktrees import create_worktree, remove_worktree, merge_worktree, abort_merge
from .triage import handle_unexpected_failure

RECALL_CIRCUIT_BREAKER = 3


# ---------------------------------------------------------------------------
# TUI integration
# ---------------------------------------------------------------------------

_tui_app = None  # Set by set_tui() when TUI is active


def set_tui(app) -> None:
    """Register the TUI app instance so orchestrator code can call request_input."""
    global _tui_app
    _tui_app = app


async def _make_sink(agent_name: str) -> MessageSink | None:
    """Return a message sink that routes to the TUI, or None if no TUI is active."""
    if _tui_app is None:
        return None

    async def sink(name: str, text: str) -> None:
        _tui_app.append_agent_message(name, text)

    return sink


async def tui_input(prompt: str) -> str:
    """Request input from user via TUI, falling back to terminal input()."""
    if _tui_app is not None:
        return await _tui_app.request_input(prompt)
    return input(prompt)


# ---------------------------------------------------------------------------
# Prompt loading
# ---------------------------------------------------------------------------

def load_prompt(name: str) -> str:
    """Load a prompt from the prompts package (works from both source and zipapp)."""
    return files("prompts").joinpath(f"{name}.md").read_text()


def build_implementer_prompt(wu_id: str, state: dict, recall_context: dict | None = None) -> str:
    wu = state["work_units"][wu_id]
    wu_file = Path(state["repo_root"]) / wu["file"]
    wu_content = wu_file.read_text()
    base = load_prompt("implementer")
    prompt = f"{base}\n\n---\n## Work unit\n\n{wu_content}"
    if recall_context:
        prompt += f"\n\n---\n## Recall context\n\n{recall_context['reviewer_feedback']}"
    return prompt


def build_reviewer_prompt(wu_id: str, implementer_report: dict, state: dict) -> str:
    wu = state["work_units"][wu_id]
    wu_file = Path(state["repo_root"]) / wu["file"]
    wu_content = wu_file.read_text()
    base = load_prompt("reviewer")
    return (
        f"{base}\n\n---\n## Work unit\n\n{wu_content}"
        f"\n\n---\n## Implementer report\n\n```json\n{json.dumps(implementer_report, indent=2)}\n```"
    )


# ---------------------------------------------------------------------------
# Checkpoint emitter
# ---------------------------------------------------------------------------

def emit_checkpoint(
    state_name: str,
    transition: str,
    progress: str,
    batch: int | None = None,
    wus: list[str] | None = None,
    notes: str | None = None,
) -> None:
    """Emit a state machine checkpoint to the TUI or stdout."""
    if _tui_app is not None:
        _tui_app.append_checkpoint(state_name, transition, progress, batch, wus, notes)
        _tui_app.set_phase(state_name)
        return
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [
        "╔══════════════════════════════════════════════════════╗",
        "║ STATE MACHINE CHECKPOINT                             ║",
        f"║ state:      {state_name:<41}║",
        f"║ transition: {transition:<41}║",
        f"║ progress:   {progress:<41}║",
    ]
    if batch is not None:
        wu_str = ", ".join(wus or [])
        lines.append(f"║ batch:      {batch}  wus: {wu_str:<35}║")
    if notes:
        lines.append(f"║ notes:      {notes:<41}║")
    lines.append(f"║ timestamp:  {now:<41}║")
    lines.append("╚══════════════════════════════════════════════════════╝")
    print("\n".join(lines))


# ---------------------------------------------------------------------------
# Agent options factory
# ---------------------------------------------------------------------------

def make_options(
    cwd: str,
    model: str = "claude-sonnet-4-6",
    allowed_tools: list[str] | None = None,
    max_turns: int = 50,
) -> ClaudeAgentOptions:
    opts: dict[str, Any] = dict(
        cwd=cwd,
        permission_mode="bypassPermissions",
        mcp_servers={"workflow": workflow_server},
        model=model,
        max_turns=max_turns,
    )
    if allowed_tools is not None:
        opts["allowed_tools"] = allowed_tools
    return ClaudeAgentOptions(**opts)


def _format_message(message: Any) -> str | None:
    """Extract displayable text from a query() message."""
    if isinstance(message, AssistantMessage):
        parts = []
        for block in message.content:
            if isinstance(block, TextBlock):
                parts.append(block.text)
            elif isinstance(block, ToolUseBlock):
                parts.append(f"[tool: {block.name}]")
        return "\n".join(parts) if parts else None
    if isinstance(message, ResultMessage):
        return None  # handled separately
    # For any other message type, use str representation
    return str(message) if message else None


async def call_agent(
    prompt: str,
    options: ClaudeAgentOptions,
    agent_name: str = "agent",
    repo_root: str | Path | None = None,
    message_sink: MessageSink | None = None,
) -> dict:
    """Call an agent and return its parsed JSON result.

    If message_sink is provided, each intermediate message is forwarded
    to it for real-time display.
    """
    log_file = None
    if repo_root:
        log_dir = Path(repo_root) / ".dev-workflow" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"{agent_name}.log"
        log_file = open(log_path, "a", encoding="utf-8")
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        log_file.write(f"\n{'='*50}\n[{now}] RUN START\n{'='*50}\n")
        log_file.flush()

    final_result: dict | None = None
    final_error: Exception | None = None
    try:
        async for message in query(prompt=prompt, options=options):
            msg_repr = str(message)
            if hasattr(message, "text"):
                msg_repr = message.text

            if log_file:
                log_file.write(msg_repr + "\n")
                log_file.flush()

            summary = msg_repr.strip().replace("\n", " ")
            if summary:
                if len(summary) > 70:
                    summary = summary[:67] + "..."
                print(f"[{agent_name}] {summary}")

            # Forward to sink before checking for ResultMessage
            if message_sink is not None:
                text = _format_message(message)
                if text:
                    await message_sink(agent_name, text)

            if isinstance(message, ResultMessage):
                if message.is_error:
                    result_text = message.result or ""
                    if "limit" in result_text.lower() or "rate" in result_text.lower():
                        final_error = RuntimeError(f"Rate limit: {result_text!r}")
                    else:
                        final_error = RuntimeError(f"Agent returned error result: {result_text!r}")
                else:
                    try:
                        final_result = parse_json_result(message.result)
                    except Exception as e:
                        final_error = e
                # Don't break — let the generator exhaust naturally so the
                # SDK can close the subprocess cleanly without a 10s timeout.
    finally:
        if log_file:
            log_file.write(f"\n{'='*50}\nRUN END\n{'='*50}\n")
            log_file.close()

    if final_error is not None:
        raise final_error
    if final_result is not None:
        return final_result
    raise RuntimeError("Agent did not return a ResultMessage")


# ---------------------------------------------------------------------------
# State handlers
# ---------------------------------------------------------------------------

async def handle_init(state: dict, repo_root: str) -> dict:
    # Create feature branch if not already on one
    import subprocess
    branch = state.get("branch", "feature/orchestrated")
    result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=repo_root, capture_output=True, text=True
    )
    current = result.stdout.strip()
    if current != branch:
        subprocess.run(["git", "checkout", "-b", branch], cwd=repo_root, check=True)
        state["branch"] = branch
    state["current_phase"] = "sketching"
    return state


async def handle_context_gather(state: dict, repo_root: str, initial_prompt: str, session_dir: Path) -> tuple[dict, dict]:
    options = make_options(
        cwd=repo_root,
        model="claude-sonnet-4-6",
        allowed_tools=["Read", "Glob", "Grep", "WebFetch", "WebSearch", "mcp__workflow__ask_question"],
    )
    prompt = load_prompt("context-gathering") + f"\n\n---\n## Initial context\n\n{initial_prompt}"
    result = await call_agent(prompt, options, agent_name="context-gatherer", repo_root=repo_root,
                              message_sink=await _make_sink("context-gatherer"))
    # Write problem statement to disk
    ps_path = session_dir / "problem-statement.json"
    ps_path.write_text(json.dumps(result, indent=2))
    state["problem_statement_file"] = str(ps_path.absolute())
    return state, result


async def handle_plan_sketch(state: dict, repo_root: str, problem_statement: dict, session_dir: Path) -> tuple[dict, dict]:
    options = make_options(
        cwd=repo_root,
        model="claude-sonnet-4-6",
        allowed_tools=["Read", "Glob", "Grep", "Write", "mcp__workflow__ask_question"],
    )
    prompt = (
        load_prompt("planner-sketcher")
        + f"\n\n---\n## Session Directory\n{session_dir.absolute()}"
        + f"\n\n---\n## Problem statement\n\n```json\n{json.dumps(problem_statement, indent=2)}\n```"
    )
    result = await call_agent(prompt, options, agent_name="planner", repo_root=repo_root,
                              message_sink=await _make_sink("planner"))
    # Reload state.json — the planner wrote it to session_dir
    return load_state(session_dir / "state.json"), result


async def handle_implementing(
    state: dict, repo_root: str, batch: list[str], recall_contexts: dict | None = None
) -> list[tuple[str, str, dict | Exception]]:
    """Run implementers in parallel, one per WU in the batch. Returns (wu_id, worktree_path, result)."""
    recall_contexts = recall_contexts or {}

    if _tui_app is not None:
        for wu_id in batch:
            _tui_app.create_agent_tab(f"implementer-{wu_id}")
        _tui_app.set_waiting(True)

    async def _run_single(wu_id: str) -> tuple[str, str, dict | Exception]:
        try:
            worktree_path = create_worktree(wu_id, repo_root)
        except Exception as e:
            return wu_id, "", e

        set_wu_field(state, wu_id, "worktree_path", worktree_path)
        set_wu_field(state, wu_id, "worktree_branch", f"wu/{wu_id}")
        set_wu_status(state, wu_id, "in_progress")

        prompt = build_implementer_prompt(wu_id, state, recall_contexts.get(wu_id))
        options = make_options(
            cwd=worktree_path,
            model="claude-sonnet-4-6",
            allowed_tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep", "mcp__workflow__ask_question"],
        )
        try:
            result = await call_agent(prompt, options, agent_name=f"implementer-{wu_id}",
                                      repo_root=repo_root,
                                      message_sink=await _make_sink(f"implementer-{wu_id}"))
            return wu_id, worktree_path, result
        except Exception as e:
            return wu_id, worktree_path, e

    tasks = [_run_single(wu_id) for wu_id in batch]
    results = await asyncio.gather(*tasks)

    if _tui_app is not None:
        _tui_app.set_waiting(False)
        for wu_id in batch:
            _tui_app.remove_agent_tab(f"implementer-{wu_id}")

    return results


async def handle_reviewing(
    state: dict, repo_root: str, impl_results: list[tuple[str, str, dict | Exception]]
) -> list[tuple[str, dict | Exception]]:
    """Review each implementer result sequentially. Returns (wu_id, reviewer_result)."""
    reviews = []
    for wu_id, worktree_path, impl_result in impl_results:
        if isinstance(impl_result, Exception):
            reviews.append((wu_id, impl_result))
            continue
        prompt = build_reviewer_prompt(wu_id, impl_result, state)
        options = make_options(
            cwd=worktree_path,
            model="claude-sonnet-4-6",
            allowed_tools=["Read", "Bash", "Glob", "Grep", "mcp__workflow__escalate"],
        )
        try:
            result = await call_agent(prompt, options, agent_name=f"reviewer-{wu_id}", repo_root=repo_root,
                                      message_sink=await _make_sink(f"reviewer-{wu_id}"))
            reviews.append((wu_id, result))
        except Exception as e:
            reviews.append((wu_id, e))
    return reviews


# ---------------------------------------------------------------------------
# Cold start recovery (task-008)
# ---------------------------------------------------------------------------

async def recover_session(state: dict, state_file: Path, repo_root: str) -> dict:
    """
    Recover an interrupted session.

    1. WUs in 'in_progress' with a dirty worktree → route to reviewer.
    2. WUs in 'in_progress' with a clean/missing worktree → reset to 'pending'.
    3. WUs in 'failed' → call triage.
    4. Emit a recovery checkpoint and return the updated state.
    """
    from .worktrees import worktree_exists, worktree_has_changes

    in_prog = in_progress_wus(state)
    failed_wus = [
        wu_id for wu_id, wu in state["work_units"].items()
        if wu["status"] == "failed"
    ]

    recovered_to_review: list[str] = []
    reset_to_pending: list[str] = []
    needs_triage: list[str] = list(failed_wus)

    for wu_id in in_prog:
        wu = state["work_units"][wu_id]
        wt_path = wu.get("worktree_path")

        if wt_path and worktree_exists(wt_path) and worktree_has_changes(wt_path, state.get("branch")):
            recovered_to_review.append(wu_id)
        else:
            set_wu_status(state, wu_id, "pending")
            set_wu_field(state, wu_id, "worktree_path", None)
            set_wu_field(state, wu_id, "worktree_branch", None)
            if wt_path and worktree_exists(wt_path):
                remove_worktree(wt_path, repo_root, wu_id)
            reset_to_pending.append(wu_id)

    total = len(state["work_units"])
    done = sum(1 for wu in state["work_units"].values() if wu["status"] == "complete")
    notes = (
        f"recovered_to_review={recovered_to_review} "
        f"reset_to_pending={reset_to_pending} "
        f"triage={needs_triage}"
    )
    emit_checkpoint(
        "BATCH_EXTRACT", "RESUME → BATCH_EXTRACT",
        f"{done} / {total} WUs complete",
        notes=notes,
    )
    save_state(state, state_file)

    # Review partial work from recovered in-progress WUs
    if recovered_to_review:
        fake_impl_results = []
        for wu_id in recovered_to_review:
            wu = state["work_units"][wu_id]
            wt_path = wu["worktree_path"]
            fake_report = {
                "wu_id": wu_id,
                "status": "complete",
                "domain_breach": {"occurred": False, "files_touched_outside_domain": [], "justification": ""},
                "changes_summary": "Recovered from interrupted session — reviewing partial work",
                "files_changed": [],
                "tests_added": [],
                "tests_passing": False,
                "break_glass": {"occurred": False, "description": ""},
                "feedback": {"target": None, "message": ""},
            }
            fake_impl_results.append((wu_id, wt_path, fake_report))

        review_results = await handle_reviewing(state, repo_root, fake_impl_results)
        for wu_id, review in review_results:
            wu = state["work_units"][wu_id]
            if isinstance(review, Exception):
                set_wu_status(state, wu_id, "failed")
            elif review.get("verdict") == "approved":
                set_wu_status(state, wu_id, "complete")
            else:
                set_wu_status(state, wu_id, "pending")
                wt_path = wu.get("worktree_path")
                if wt_path and worktree_exists(wt_path):
                    remove_worktree(wt_path, repo_root, wu_id)
                set_wu_field(state, wu_id, "worktree_path", None)
                set_wu_field(state, wu_id, "worktree_branch", None)

    # Triage failed WUs
    for wu_id in needs_triage:
        failure_context = {
            "wu_id": wu_id,
            "wu_title": state["work_units"][wu_id].get("title", ""),
            "failure_reason": "WU was in 'failed' state when session was resumed",
        }
        triage_result = await handle_unexpected_failure(failure_context, repo_root)
        action = triage_result.get("action", "user")

        if action == "retry":
            set_wu_status(state, wu_id, "pending")
            set_wu_field(state, wu_id, "recall_count", 0)
        elif action == "abort":
            save_state(state, state_file)
            raise SystemExit(f"Triage agent requested abort for {wu_id}: {triage_result.get('message')}")
        else:  # "reshape" or "user"
            print(f"\n[Triage escalation — {wu_id}]: {triage_result.get('message')}")
            decision = (await tui_input("How would you like to proceed? [retry/skip/abort]: ")).strip().lower()
            if decision == "retry":
                set_wu_status(state, wu_id, "pending")
            elif decision == "abort":
                raise SystemExit(f"User aborted at {wu_id}")
            else:
                set_wu_status(state, wu_id, "blocked")

    save_state(state, state_file)
    return state


# ---------------------------------------------------------------------------
# Batch commit with conflict recall loop (task-009)
# ---------------------------------------------------------------------------

async def commit_batch(
    approved: list[dict],
    state: dict,
    state_file: Path,
    repo_root: str,
) -> None:
    """
    Merge approved WUs in priority order. Recall implementers for any conflicts.

    Priority:
      1. WUs whose implementer did NOT break its solution domain (clean domain)
      2. WUs whose implementer DID break its domain (break-glass / domain breach)
      Within each group: order from approved list

    Conflict resolution:
      - Abort the failed merge, preserve the conflicting worktree
      - Recall the implementer with the conflict context
      - Re-review; if approved, re-attempt merge
      - If conflict recurs a second time: escalate to user
    """
    clean = [w for w in approved if not w.get("domain_breached", False)]
    breached = [w for w in approved if w.get("domain_breached", False)]
    ordered = clean + breached

    already_merged: list[str] = []

    for wu in ordered:
        wu_id = wu["wu_id"]
        wt_path = wu["worktree_path"]
        conflict_count = 0

        while True:
            try:
                merge_worktree(wu_id, wt_path, repo_root)
                already_merged.append(wu_id)
                set_wu_field(state, wu_id, "worktree_path", None)
                set_wu_field(state, wu_id, "worktree_branch", None)
                save_state(state, state_file)
                break

            except Exception:
                abort_merge(repo_root)
                conflict_count += 1

                if conflict_count >= 2:
                    print(f"\n[Merge conflict — {wu_id}] Conflict recurred twice.")
                    print(f"Already merged: {already_merged}")
                    print("Options: [skip] abandon this WU | [abort] stop the workflow")
                    decision = (await tui_input("Decision: ")).strip().lower()
                    if decision == "abort":
                        save_state(state, state_file)
                        raise SystemExit(f"User aborted at merge conflict for {wu_id}")
                    else:
                        set_wu_status(state, wu_id, "failed")
                        save_state(state, state_file)
                        break

                # First conflict — recall the implementer
                print(f"\n[Merge conflict — {wu_id}] Recalling implementer with conflict context.")
                conflict_msg = (
                    f"A merge conflict occurred with WUs that were merged before you: "
                    f"{already_merged}. Retry your implementation. "
                    f"You may break your solution domain again if that is what caused the conflict "
                    f"— report it as break-glass."
                )
                recall_ctx = {"reviewer_feedback": conflict_msg}

                impl_results = await handle_implementing(
                    state, repo_root, [wu_id], {wu_id: recall_ctx}
                )
                review_results = await handle_reviewing(state, repo_root, impl_results)
                _, review = review_results[0]

                if isinstance(review, Exception) or review.get("verdict") != "approved":
                    set_wu_status(state, wu_id, "failed")
                    save_state(state, state_file)
                    break

                # Reviewer approved — loop back and retry the merge


# ---------------------------------------------------------------------------
# Main orchestrator loop
# ---------------------------------------------------------------------------

async def _pause_if_needed(data: Any, agent: str, enabled: bool) -> None:
    if not enabled:
        return
    header = f"{'='*50}\nTRANSITION PAUSE: Output from {agent}\n{'='*50}"
    if _tui_app is not None:
        _tui_app.append_to_output(header)
        if data is not None:
            try:
                text = json.dumps(data, indent=2, default=str) if isinstance(data, (dict, list)) else str(data)
                _tui_app.append_to_output(text)
            except Exception:
                _tui_app.append_to_output("<unprintable data>")
        await tui_input("[PAUSED] Press Enter to continue to next phase... ")
    else:
        print(header)
        if data is not None:
            try:
                print(json.dumps(data, indent=2, default=str) if isinstance(data, (dict, list)) else str(data))
            except Exception:
                print("<unprintable data>")
        from .console import get_prompt_session
        session = get_prompt_session(".")
        await session.prompt_async("\n[PAUSED] Press Enter to continue to next phase... ")


async def run_orchestrator(
    repo_root: str,
    initial_prompt: str,
    session_name: str | None = None,
    resume: bool = False,
    transition_pauses: bool = False,
) -> None:
    repo_path = Path(repo_root)
    workflows_dir = repo_path / ".dev-workflow"
    workflows_dir.mkdir(exist_ok=True)

    if resume:
        if not session_name:
            raise ValueError("session_name is required when resuming")
        session_dir = workflows_dir / session_name
        if not session_dir.exists():
            raise FileNotFoundError(f"Session directory not found: {session_dir}")
        state_file = session_dir / "state.json"
        
        state = load_state(state_file)
        state = await recover_session(state, state_file, repo_root)

        # If the sketcher never completed (e.g. interrupted mid-sketch), re-run it.
        if not state.get("work_units"):
            ps_file = state.get("problem_statement_file")
            if not ps_file or not Path(ps_file).exists():
                raise FileNotFoundError(
                    f"Cannot re-run sketcher: problem-statement.json not found at {ps_file!r}"
                )
            with open(ps_file) as f:
                problem_statement = json.load(f)
            emit_checkpoint("PLAN_SKETCH", "RESUME → PLAN_SKETCH (re-sketch)", "0 / 0 WUs complete")
            state, plan_result = await handle_plan_sketch(state, repo_root, problem_statement, session_dir)
            save_state(state, state_file)
            await _pause_if_needed(plan_result, "planner-sketcher", transition_pauses)

            total = len(state["work_units"])
            emit_checkpoint(
                "BATCH_EXTRACT", "PLAN_SKETCH → BATCH_EXTRACT",
                f"0 / {total} WUs complete",
                notes="Re-sketch complete — entering implementation loop",
            )
    else:
        temp_id = str(uuid.uuid4())
        session_dir = workflows_dir / temp_id
        session_dir.mkdir(exist_ok=False)
        (session_dir / "work-units").mkdir(exist_ok=True)
        
        state_file = session_dir / "state.json"
        
        state = {
            "version": 1,
            "branch": "feature/orchestrated",
            "pr_number": None,
            "current_phase": "sketching",
            "repo_root": repo_root,
            "session_name": None,
            "problem_statement_file": None,
            "traceability_matrix": {},
            "work_units": {},
        }
        # Do not save state yet since we will rename the directory

        # INIT
        emit_checkpoint("INIT", "START → INIT", "0 / 0 WUs complete")
        state = await handle_init(state, repo_root)

        # CONTEXT_GATHER
        emit_checkpoint("CONTEXT_GATHER", "INIT → CONTEXT_GATHER", "0 / 0 WUs complete")
        state, problem_statement = await handle_context_gather(state, repo_root, initial_prompt, session_dir)
        await _pause_if_needed(problem_statement, "context-gatherer", transition_pauses)
        
        # Rename session_dir based on friendly_session_name
        base_name = problem_statement.get("friendly_session_name", "unnamed-session").lower()
        base_name = re.sub(r'[^a-z0-9]+', '-', base_name).strip('-')
        if not base_name:
            base_name = "unnamed-session"
            
        final_dir = workflows_dir / base_name
        count = 2
        while final_dir.exists():
            final_dir = workflows_dir / f"{base_name}-{count}"
            count += 1
            
        session_dir.rename(final_dir)
        session_dir = final_dir
        state_file = session_dir / "state.json"
        
        # Update state references
        state["session_name"] = session_dir.name
        state["problem_statement_file"] = str((session_dir / "problem-statement.json").absolute())
        save_state(state, state_file)

        # PLAN_SKETCH
        emit_checkpoint("PLAN_SKETCH", "CONTEXT_GATHER → PLAN_SKETCH", "0 / 0 WUs complete")
        state, plan_result = await handle_plan_sketch(state, repo_root, problem_statement, session_dir)
        save_state(state, state_file)
        await _pause_if_needed(plan_result, "planner-sketcher", transition_pauses)

        total = len(state["work_units"])
        emit_checkpoint(
            "BATCH_EXTRACT", "PLAN_SKETCH → BATCH_EXTRACT",
            f"0 / {total} WUs complete",
            notes="Plan complete — entering implementation loop",
        )

    # Main loop: BATCH_EXTRACT → IMPLEMENTING → REVIEWING → BATCH_COMMIT → repeat
    recall_contexts: dict[str, dict] = {}

    while True:
        # BATCH_EXTRACT
        batch = extract_batch(state)
        total = len(state["work_units"])
        done = sum(1 for wu in state["work_units"].values() if wu["status"] == "complete")

        if not batch:
            if all_complete(state):
                emit_checkpoint("COMPLETE", "BATCH_COMMIT → COMPLETE", f"{done} / {total} WUs complete")
                print("\nAll work units complete.")
                break
            else:
                failure_context = {
                    "failure_type": "DAGDeadlock",
                    "failure_message": "No eligible batch but pending WUs remain",
                    "pending_wus": pending_wus(state),
                    "work_units": {
                        wu_id: {"status": wu["status"], "depends_on": wu.get("depends_on", [])}
                        for wu_id, wu in state["work_units"].items()
                    },
                }
                emit_checkpoint("ERROR", "BATCH_EXTRACT → ERROR", f"{done} / {total} WUs complete",
                                notes="No eligible batch but pending WUs remain")
                triage_result = await handle_unexpected_failure(failure_context, repo_root)
                action = triage_result.get("action", "user")
                if action == "reshape":
                    emit_checkpoint("RESHAPE", "ERROR → RESHAPE", _progress(state),
                                    notes="DAG deadlock — triage requested reshape")
                    options = make_options(
                        cwd=repo_root,
                        model="claude-sonnet-4-6",
                        allowed_tools=["Read", "Glob", "Grep", "Write", "mcp__workflow__ask_question"],
                    )
                    feedback = json.dumps({"deadlock": triage_result.get("message")})
                    prompt = load_prompt("planner-reshaper") + f"\n\n---\n## Feedback\n\n```json\n{feedback}\n```"
                    await call_agent(prompt, options, agent_name="reshaper", repo_root=repo_root,
                                    message_sink=await _make_sink("reshaper"))
                    state = load_state(state_file)
                    await _pause_if_needed(state, "reshaper (deadlock)", transition_pauses)
                    continue
                elif action == "abort":
                    save_state(state, state_file)
                    raise SystemExit(f"Triage agent requested abort: {triage_result.get('message')}")
                else:
                    print(f"\n[Triage escalation]: {triage_result.get('message')}")
                    await tui_input("Press Enter to abort, or fix state.json manually and restart with --resume: ")
                    raise SystemExit("User aborted at DAG deadlock")

        emit_checkpoint(
            "BATCH_EXTRACT", "BATCH_COMMIT → BATCH_EXTRACT",
            f"{done} / {total} WUs complete",
            batch=len(batch), wus=batch,
        )

        # IMPLEMENTING
        emit_checkpoint("IMPLEMENTING", "BATCH_EXTRACT → IMPLEMENTING",
                        f"{done} / {total} WUs complete", batch=len(batch), wus=batch)
        impl_results = await handle_implementing(state, repo_root, batch, recall_contexts)
        await _pause_if_needed([r[2] for r in impl_results], "implementers", transition_pauses)
        recall_contexts = {}
        save_state(state, state_file)
        if _tui_app is not None:
            _tui_app.update_status(state)

        # REVIEWING
        emit_checkpoint("REVIEWING", "IMPLEMENTING → REVIEWING",
                        f"{done} / {total} WUs complete", batch=len(batch), wus=batch)
        review_results = await handle_reviewing(state, repo_root, impl_results)
        await _pause_if_needed([r[1] for r in review_results], "reviewers", transition_pauses)

        # Process review outcomes
        approved: list[dict] = []
        needs_recall: list[str] = []
        needs_reshape: list[str] = []

        impl_map = {wu_id: (wt_path, report) for wu_id, wt_path, report in impl_results}

        for wu_id, review in review_results:
            wt_path, impl_report = impl_map[wu_id]

            if isinstance(review, Exception):
                failure_context = {
                    "wu_id": wu_id,
                    "failure_type": type(review).__name__,
                    "failure_message": str(review),
                    "stage": "reviewing",
                }
                triage_result = await handle_unexpected_failure(failure_context, repo_root)
                action = triage_result.get("action", "user")
                if action == "retry":
                    set_wu_status(state, wu_id, "pending")
                    set_wu_field(state, wu_id, "recall_count", 0)
                    # Clear stale worktree fields so state is accurate between retries
                    set_wu_field(state, wu_id, "worktree_path", None)
                    set_wu_field(state, wu_id, "worktree_branch", None)
                elif action == "reshape":
                    needs_reshape.append(wu_id)
                    set_wu_status(state, wu_id, "failed")
                elif action == "abort":
                    save_state(state, state_file)
                    raise SystemExit(f"Triage agent requested abort: {triage_result.get('message')}")
                else:  # "user"
                    print(f"\n[Triage escalation — {wu_id}]: {triage_result.get('message')}")
                    decision = (await tui_input("How would you like to proceed? [retry/reshape/skip/abort]: ")).strip().lower()
                    if decision == "retry":
                        set_wu_status(state, wu_id, "pending")
                    elif decision == "reshape":
                        needs_reshape.append(wu_id)
                        set_wu_status(state, wu_id, "failed")
                    elif decision == "abort":
                        raise SystemExit(f"User aborted at {wu_id}")
                    else:
                        set_wu_status(state, wu_id, "failed")
                continue

            if review.get("verdict") == "approved":
                approved.append({
                    "wu_id": wu_id,
                    "worktree_path": wt_path,
                    "domain_breached": impl_report.get("domain_breach", {}).get("occurred", False),
                })
                set_wu_status(state, wu_id, "complete")
            else:
                ft = review.get("feedback_target", "implementer")
                if ft == "planner":
                    needs_reshape.append(wu_id)
                    set_wu_status(state, wu_id, "failed")
                else:
                    recall_count = increment_recall(state, wu_id)
                    if recall_count >= RECALL_CIRCUIT_BREAKER:
                        needs_reshape.append(wu_id)
                        set_wu_status(state, wu_id, "failed")
                    else:
                        needs_recall.append(wu_id)
                        recall_contexts[wu_id] = {"reviewer_feedback": review.get("feedback_message", "")}
                        set_wu_status(state, wu_id, "pending")

        save_state(state, state_file)

        # RESHAPE if needed
        if needs_reshape:
            emit_checkpoint("RESHAPE", "REVIEWING → RESHAPE",
                            f"{done} / {total} WUs complete", wus=needs_reshape)
            feedback = json.dumps({"wus_needing_reshape": needs_reshape})
            options = make_options(
                cwd=repo_root,
                model="claude-sonnet-4-6",
                allowed_tools=["Read", "Glob", "Grep", "Write", "mcp__workflow__ask_question"],
            )
            prompt = load_prompt("planner-reshaper") + f"\n\n---\n## Feedback\n\n```json\n{feedback}\n```"
            await call_agent(prompt, options, agent_name="reshaper", repo_root=repo_root,
                              message_sink=await _make_sink("reshaper"))
            state = load_state(state_file)  # reshaper rewrote state.json
            await _pause_if_needed(state, "reshaper (rejected implementation)", transition_pauses)

        # BATCH_COMMIT (merge approved worktrees)
        if approved:
            emit_checkpoint("BATCH_COMMIT", "REVIEWING → BATCH_COMMIT",
                            f"{done} / {total} WUs complete", batch=len(approved))
            await commit_batch(approved, state, state_file, repo_root)

        # Clean up rejected/failed worktrees
        for wu_id in needs_reshape:
            wu = state["work_units"][wu_id]
            if wu.get("worktree_path"):
                remove_worktree(wu["worktree_path"], repo_root, wu_id)
                set_wu_field(state, wu_id, "worktree_path", None)
                set_wu_field(state, wu_id, "worktree_branch", None)

        save_state(state, state_file)
        if _tui_app is not None:
            _tui_app.update_status(state)


def _progress(state: dict) -> str:
    total = len(state["work_units"])
    done = sum(1 for wu in state["work_units"].values() if wu["status"] == "complete")
    return f"{done} / {total} WUs complete"
