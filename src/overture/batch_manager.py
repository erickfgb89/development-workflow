"""Batch Manager — Phase 2 core.

Responsibilities:
1. Select up to MAX_BATCH_SIZE ready WUs with no solution_domain overlap.
2. For each selected WU:
   a. Create a git worktree on a dedicated branch.
   b. Spawn an Implementer agent (run_query_json) in that worktree's cwd.
   c. Validate the Implementer output.
   d. Spawn a Reviewer agent.
   e. Validate the Reviewer output.
   f. On PASS: merge the branch into the session branch; delete worktree.
   g. On FAIL (×2): surface the WU for user pivot.
   h. On RESHAPE: mark WU as failed; caller handles replanning.
3. Update state.json after every WU resolution.

Phase 2 runs WUs sequentially within a batch (parallel execution is Phase 3).
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import textwrap
import traceback
from pathlib import Path
from typing import Any


def _format_exc() -> str:
    return traceback.format_exc()

from .planner import (
    _validate,
    _load_prompt,
    get_ready_wus,
    mark_downstream_blocked,
    schema_example,
)
from .sdk_wrapper import AgentHaltError, TurnLimitError, run_query, run_query_json
from .session_manager import SessionManager

logger = logging.getLogger(__name__)

MAX_BATCH_SIZE = 5
MAX_FAILURES = 2


# ---------------------------------------------------------------------------
# Prompt helpers
# ---------------------------------------------------------------------------

def _implementer_system_prompt() -> str:
    core = _load_prompt("core.md")
    execution = _load_prompt("common_execution.md")
    implementer = _load_prompt("task_implementer.md")
    return "\n\n".join(filter(None, [core, execution, implementer]))


def _reviewer_system_prompt() -> str:
    core = _load_prompt("core.md")
    reviewer = _load_prompt("task_reviewer.md")
    return "\n\n".join(filter(None, [core, reviewer]))


_IMPLEMENTER_PROMPT_TEMPLATE = textwrap.dedent("""\
    You are the Implementer agent for Work Unit {wu_id}.

    ## Work Unit
    {wu_json}

    ## Context
    {context_md}

    ## Your task
    Implement ONLY the changes described in the Work Unit above.
    - Check the solution_domain files before starting.
    - Write code that satisfies each acceptance criterion exactly.
    - Do not touch files outside solution_domain unless strictly unavoidable.
    - Run any relevant tests after implementing.

    When done, respond with ONLY a JSON object (no fences).

    On success, follow this example:
    {example_success}

    If you cannot proceed — due to a blocking ambiguity, missing prerequisite, or \
environment gap that the WU does not account for — set status to "failure" and \
describe the blocker precisely in error_detail so the Reviewer can escalate it:
    {example_failure}
""")

_REVIEWER_PROMPT_TEMPLATE = textwrap.dedent("""\
    You are the Reviewer agent for Work Unit {wu_id}.

    ## Work Unit
    {wu_json}

    ## Implementer Report
    {impl_report_json}

    ## Your task
    Audit the implementation against the acceptance criteria.
    1. White-Hat: Do the changes satisfy every AC?
    2. Black-Hat: What edge cases or error paths are unhandled?
    3. Domain: Were any files outside solution_domain touched without justification?

    If the Implementer reported status "failure", your primary job is to assess \
whether the blocker described in error_detail is a genuine gap in the WU scope. \
If so, verdict must be "reshape" so the Planner can address the gap.

    Respond with ONLY a JSON object (no fences).

    A passing review looks like:
    {example_pass}

    A reshape (planning gap) looks like:
    {example_reshape}
""")


_RESUME_INSPECTOR_PROMPT = textwrap.dedent("""\
    An Implementer agent was working on Work Unit {wu_id} but was cut off when it hit
    the turn limit. The worktree at {wt_path} contains whatever it managed to do.

    ## Work Unit
    {wu_json}

    ## Your task
    Inspect the worktree and produce a concise handoff note (plain text, no JSON) for
    the next Implementer agent that will continue this work. Cover:
    1. Which acceptance criteria are already satisfied by files on disk.
    2. Which acceptance criteria are partially addressed and what remains.
    3. Which acceptance criteria have not been started.
    4. Any test failures or errors you can see from the current state.

    Be specific and concrete — the next agent will use this note as its starting point.
    Do not redo any work; only inspect and report.
""")

# ---------------------------------------------------------------------------
# Domain overlap check
# ---------------------------------------------------------------------------

def select_batch(ready_wus: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pick up to MAX_BATCH_SIZE WUs with non-overlapping solution_domains."""
    claimed: set[str] = set()
    batch: list[dict[str, Any]] = []
    for wu in ready_wus:
        domain = set(wu.get("solution_domain", []))
        if domain & claimed:
            continue
        claimed |= domain
        batch.append(wu)
        if len(batch) >= MAX_BATCH_SIZE:
            break
    return batch


# ---------------------------------------------------------------------------
# Git worktree helpers
# ---------------------------------------------------------------------------

def _git(args: list[str], cwd: Path) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _current_branch(repo: Path) -> str:
    return _git(["rev-parse", "--abbrev-ref", "HEAD"], repo)


def _branch_exists(branch: str, repo: Path) -> bool:
    result = subprocess.run(
        ["git", "rev-parse", "--verify", branch],
        cwd=repo,
        capture_output=True,
    )
    return result.returncode == 0


def create_worktree(wu_id: str, session_manager: SessionManager, target_repo: Path) -> Path:
    """Create a git worktree for the WU on a dedicated branch.

    If the branch already exists from a previous failed run, reuse it rather
    than failing with -b.  The worktree directory is removed first if it is
    stale (not registered with git) so the add doesn't collide on the path.
    """
    branch = f"wu/{wu_id.lower()}"
    wt_path = session_manager.worktree_path(wu_id)
    wt_path.parent.mkdir(parents=True, exist_ok=True)

    # Clean up a stale worktree directory that git no longer tracks.
    if wt_path.exists():
        try:
            _git(["worktree", "remove", "--force", str(wt_path)], target_repo)
        except subprocess.CalledProcessError:
            pass

    base_branch = _current_branch(target_repo)
    if _branch_exists(branch, target_repo):
        # Branch left over from a previous attempt — reset it to base and reuse.
        _git(["branch", "-f", branch, base_branch], target_repo)
        _git(["worktree", "add", str(wt_path), branch], target_repo)
    else:
        _git(["worktree", "add", "-b", branch, str(wt_path), base_branch], target_repo)

    logger.info("Created worktree %s on branch %s", wt_path, branch)
    return wt_path


def remove_worktree(wu_id: str, session_manager: SessionManager, target_repo: Path) -> None:
    """Remove the worktree directory and delete the WU branch."""
    branch = f"wu/{wu_id.lower()}"
    wt_path = session_manager.worktree_path(wu_id)
    try:
        _git(["worktree", "remove", "--force", str(wt_path)], target_repo)
    except subprocess.CalledProcessError:
        logger.warning("Could not remove worktree %s — manual cleanup may be needed.", wt_path)
    # Always attempt branch deletion so it doesn't block future resume attempts.
    try:
        _git(["branch", "-D", branch], target_repo)
    except subprocess.CalledProcessError:
        logger.warning("Could not delete branch %s — may not exist or already deleted.", branch)


def merge_branch(wu_id: str, target_repo: Path, commit_message: str) -> None:
    """Merge the WU branch into the current branch (squash merge).

    The implementer edits files in the worktree directly; it does not commit them.
    We therefore stage everything that changed before committing.
    """
    branch = f"wu/{wu_id.lower()}"
    _git(["merge", "--squash", branch], target_repo)
    _git(["add", "-A"], target_repo)
    # Only commit if there is something staged — a no-op WU should not fail.
    result = subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        cwd=target_repo,
        capture_output=True,
    )
    if result.returncode != 0:
        # returncode 1 means there are staged changes
        _git(["commit", "-m", commit_message], target_repo)
    else:
        logger.warning("WU-%s squash merge produced no changes; skipping commit.", wu_id)
    logger.info("Merged %s into HEAD", branch)


# ---------------------------------------------------------------------------
# Single WU execution
# ---------------------------------------------------------------------------

async def execute_wu(
    wu: dict[str, Any],
    session_manager: SessionManager,
    target_repo: Path,
    context_md: str,
) -> str:
    """Run Implementer → Reviewer for one WU.

    Returns the final verdict: 'pass', 'fail', or 'reshape'.
    Updates state.json in place.
    """
    wu_id = wu["id"]
    state = session_manager.read_state()
    wu_state = state["work_units"][wu_id]
    wu_state["status"] = "in_progress"
    session_manager.write_state(state)

    # Create worktree
    wt_path = create_worktree(wu_id, session_manager, target_repo)

    try:
        impl_report = await _run_implementer(wu, wt_path, context_md)
        session_manager.write_wu(wu_id, {**wu, "implementer_report": impl_report})

        failure_count = wu_state.get("failure_count", 0)
        review = await _run_reviewer(wu, impl_report, wt_path, context_md, failure_count)

        verdict = review.get("verdict", "fail")
        wu_state["failure_count"] = failure_count + (0 if verdict == "pass" else 1)
        wu_state["last_review"] = review

        if verdict == "pass":
            commit_msg = impl_report.get("commit_message", f"feat: implement {wu_id}")
            merge_branch(wu_id, target_repo, commit_msg)
            wu_state["status"] = "completed"
            wu_state["worktree_branch"] = None
        elif verdict == "reshape":
            wu_state["status"] = "failed"
            wu_state["reshape_requested"] = True
            mark_downstream_blocked(state, wu_id)
        else:
            # fail — check if we've hit the pivot threshold
            if wu_state["failure_count"] >= MAX_FAILURES:
                wu_state["status"] = "failed"
                wu_state["needs_user_pivot"] = True
                mark_downstream_blocked(state, wu_id)
            else:
                wu_state["status"] = "pending"  # retry in next batch

        session_manager.write_state(state)
        return verdict

    except AgentHaltError as halt:
        # An agent deliberately stopped — treat as an immediate pivot request,
        # not a retryable failure.  The full error report is preserved in state.
        logger.warning("Agent halt in %s: %s", wu_id, halt)
        state = session_manager.read_state()
        state["work_units"][wu_id]["status"] = "failed"
        state["work_units"][wu_id]["needs_user_pivot"] = True
        state["work_units"][wu_id]["agent_error"] = halt.report
        mark_downstream_blocked(state, wu_id)
        session_manager.write_state(state)
        return "fail"

    except Exception as exc:
        logger.error("Error executing %s: %s", wu_id, exc)
        # Save a diagnostic file in the session directory so the error is
        # inspectable after the worktree has been cleaned up.
        error_log = session_manager.session_dir / "wus" / f"{wu_id}.error.log"
        error_log.write_text(
            f"Error: {exc}\n\nTraceback:\n" + _format_exc(),
            encoding="utf-8",
        )
        state = session_manager.read_state()
        state["work_units"][wu_id]["status"] = "failed"
        state["work_units"][wu_id]["error"] = str(exc)
        session_manager.write_state(state)
        return "fail"

    finally:
        # Always clean up the worktree directory
        remove_worktree(wu_id, session_manager, target_repo)


async def _run_implementer(
    wu: dict[str, Any], wt_path: Path, context_md: str
) -> dict[str, Any]:
    prompt = _IMPLEMENTER_PROMPT_TEMPLATE.format(
        wu_id=wu["id"],
        wu_json=json.dumps(wu, indent=2),
        context_md=context_md,
        example_success=schema_example("implementer_output.json", index=0),
        example_failure=schema_example("implementer_output.json", index=1),
    )
    system_prompt = _implementer_system_prompt()
    try:
        report = await run_query_json(
            prompt,
            system_prompt=system_prompt,
            cwd=wt_path,
            permission_mode="acceptEdits",
        )
    except TurnLimitError as exc:
        # The implementer was cut off mid-flight.  Save what it produced, then
        # run a lightweight inspector agent to summarise progress, and hand off
        # to a fresh implementer session so work already on disk is not lost.
        logger.warning("%s implementer hit turn limit — inspecting worktree and continuing.", wu["id"])
        raw_output_path = wt_path / ".overture_implementer_output"
        raw_output_path.write_text(exc.partial_text, encoding="utf-8")

        inspector_prompt = _RESUME_INSPECTOR_PROMPT.format(
            wu_id=wu["id"],
            wt_path=wt_path,
            wu_json=json.dumps(wu, indent=2),
        )
        handoff_note, _ = await run_query(
            inspector_prompt,
            cwd=wt_path,
            permission_mode="acceptEdits",
        )

        resume_prompt = (
            prompt
            + f"\n\n## Handoff Note — Previous Agent Was Cut Off\n\n"
            + handoff_note
            + "\n\nYou are continuing where the previous agent left off. "
            "Do not redo work that is already done. Resume from where it stopped."
        )
        report = await run_query_json(
            resume_prompt,
            system_prompt=system_prompt,
            cwd=wt_path,
            permission_mode="acceptEdits",
        )

    _validate(report, "implementer_output.json")
    return report


async def _run_reviewer(
    wu: dict[str, Any],
    impl_report: dict[str, Any],
    wt_path: Path,
    context_md: str,
    failure_count: int,
) -> dict[str, Any]:
    prompt = _REVIEWER_PROMPT_TEMPLATE.format(
        wu_id=wu["id"],
        wu_json=json.dumps(wu, indent=2),
        impl_report_json=json.dumps(impl_report, indent=2),
        failure_count=failure_count,
        example_pass=schema_example("reviewer_output.json", index=0),
        example_reshape=schema_example("reviewer_output.json", index=1),
    )
    review = await run_query_json(
        prompt,
        system_prompt=_reviewer_system_prompt(),
        cwd=wt_path,
        permission_mode="acceptEdits",
    )
    _validate(review, "reviewer_output.json")
    return review


# ---------------------------------------------------------------------------
# Batch loop (sequential in Phase 2 — parallel in Phase 3)
# ---------------------------------------------------------------------------

async def run_batch(
    session_manager: SessionManager,
    target_repo: Path,
    *,
    verbose: bool = False,
) -> dict[str, int]:
    """Run one full batch cycle.

    Selects ready WUs, executes each sequentially, returns a summary dict.
    """
    state = session_manager.read_state()
    context_md = session_manager.read_context()
    ready = get_ready_wus(state)

    if not ready:
        return {"ready": 0, "passed": 0, "failed": 0, "reshaped": 0}

    batch = select_batch(ready)
    batch_num = state.get("batch_number", 0) + 1
    state["batch_number"] = batch_num
    session_manager.write_state(state)

    print(f"\n  Batch {batch_num}: {len(batch)} WU(s) — {', '.join(w['id'] for w in batch)}")

    summary = {"ready": len(ready), "passed": 0, "failed": 0, "reshaped": 0}

    for wu in batch:
        wu_id = wu["id"]
        print(f"  → {wu_id}: {wu['title']}…", end=" ", flush=True)
        verdict = await execute_wu(wu, session_manager, target_repo, context_md)
        print(verdict.upper())

        if verdict == "pass":
            summary["passed"] += 1
        elif verdict == "reshape":
            summary["reshaped"] += 1
        else:
            summary["failed"] += 1

        # Reload state after each WU (it may have been modified)
        state = session_manager.read_state()

        # Check for user pivot requests
        needs_pivot = [
            wid for wid, w in state["work_units"].items()
            if w.get("needs_user_pivot")
        ]
        if needs_pivot:
            print(f"\n  ⚠  User pivot required for: {', '.join(needs_pivot)}")
            print("  The above WUs have failed twice. Please review and provide guidance.\n")
            break

    return summary
