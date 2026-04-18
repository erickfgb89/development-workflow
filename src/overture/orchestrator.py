"""Top-level orchestration loop.

Drives the state machine:
  Gather → Plan → [Execute Batch]* → Done

Each phase is delegated to its module.  This module only manages the high-level
lifecycle and user-facing messaging between phases.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from .batch_manager import run_batch
from .gatherer import run_gather
from .planner import get_ready_wus, run_plan
from .session_manager import SessionManager


async def run(target_repo: Path, *, verbose: bool = False, ui: bool = False, port: int = 7337) -> None:
    """Full orchestration lifecycle for a target repository."""
    sm = SessionManager(target_repo)
    sm.new_session()

    ui_task = None
    if ui:
        from .web_ui.server import start_ui_server
        ui_task = asyncio.create_task(start_ui_server(sm, port=port))

    # ---- Phase 1: Gather ------------------------------------------------
    print()
    completed = await run_gather(sm, target_repo)
    if not completed:
        print("Session aborted.")
        sys.exit(0)

    # ---- Phase 2: Plan --------------------------------------------------
    print("\nGenerating plan…")
    dag = await run_plan(sm, target_repo, verbose=verbose)

    wu_ids = [wu["id"] for wu in dag["work_units"]]
    print(f"  Work Units: {', '.join(wu_ids)}")
    print()

    # ---- Phase 3+: Execute batches --------------------------------------
    while True:
        state = sm.read_state()
        wus = state["work_units"]

        # Check for any WUs needing user pivot
        pivot_wus = [wid for wid, w in wus.items() if w.get("needs_user_pivot")]
        if pivot_wus:
            print(f"\nUser pivot needed for: {', '.join(pivot_wus)}")
            print("Review state.json and the WU files to understand the failures.")
            print("Resolve the issue and re-run with --resume to continue.")
            break

        # Check overall completion
        statuses = [w["status"] for w in wus.values()]
        if all(s == "completed" for s in statuses):
            print("\nAll Work Units completed successfully.")
            break
        if all(s in ("completed", "failed", "blocked") for s in statuses):
            pending_failed = [wid for wid, w in wus.items() if w["status"] == "failed"]
            print(f"\nSession ended with failed/blocked WUs: {', '.join(pending_failed)}")
            break

        ready = get_ready_wus(state)
        if not ready:
            print("\nNo ready Work Units — possible DAG deadlock. Review state.json.")
            break

        summary = await run_batch(sm, target_repo, verbose=verbose)

        if summary["ready"] == 0:
            break

    if ui_task:
        ui_task.cancel()
        try:
            await ui_task
        except asyncio.CancelledError:
            pass

    print(f"\nSession: {sm.session_id}")
    print(f"State:   {sm.session_dir / 'state.json'}")
