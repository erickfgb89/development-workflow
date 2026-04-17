"""CLI entry point for Overture.

Usage:
    overture [TARGET_REPO]

    TARGET_REPO defaults to the current working directory.

Flags:
    -v, --verbose   Enable verbose logging
    --resume SLUG   Resume an existing session by slug
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="overture",
        description="AI-driven workflow orchestrator",
    )
    parser.add_argument(
        "target",
        nargs="?",
        default=".",
        help="Path to the target repository (default: current directory)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )
    parser.add_argument(
        "--resume",
        metavar="SLUG",
        help="Resume an existing session by its slug",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    else:
        logging.basicConfig(level=logging.WARNING, stream=sys.stderr)

    target_repo = Path(args.target).resolve()
    if not target_repo.is_dir():
        print(f"Error: {target_repo} is not a directory.", file=sys.stderr)
        sys.exit(1)

    if args.resume:
        asyncio.run(_resume(target_repo, args.resume, verbose=args.verbose))
    else:
        from .orchestrator import run
        asyncio.run(run(target_repo, verbose=args.verbose))


async def _resume(target_repo: Path, session_slug: str, *, verbose: bool) -> None:
    """Resume an existing session from the batch-execution phase."""
    from .batch_manager import run_batch
    from .planner import get_ready_wus, resume_from_planner_output, run_plan
    from .session_manager import SessionManager

    sm = SessionManager(target_repo)
    sm.load_session(session_slug)
    print(f"Resuming session: {session_slug}")

    # If state.json is missing, try to recover without calling the planner again.
    state_path = sm.session_dir / "state.json"
    if not state_path.exists():
        planner_output_path = sm.session_dir / "planner_output"
        if planner_output_path.exists():
            # Planner ran but crashed before state was written — parse saved output.
            print("No state.json found — recovering from saved planner_output…")
            await resume_from_planner_output(sm, verbose=verbose)
        else:
            # Planner never ran at all — re-run it from context.md.
            print("No state.json found — re-running planner from context.md…")
            await run_plan(sm, target_repo, verbose=verbose)

    while True:
        state = sm.read_state()
        wus = state["work_units"]

        pivot_wus = [wid for wid, w in wus.items() if w.get("needs_user_pivot")]
        if pivot_wus:
            print(f"User pivot needed for: {', '.join(pivot_wus)}")
            break

        statuses = [w["status"] for w in wus.values()]
        if all(s == "completed" for s in statuses):
            print("All Work Units completed.")
            break
        if all(s in ("completed", "failed", "blocked") for s in statuses):
            print("Session ended with some failed/blocked WUs.")
            break

        ready = get_ready_wus(state)
        if not ready:
            print("No ready Work Units.")
            break

        summary = await run_batch(sm, target_repo, verbose=verbose)
        if summary["ready"] == 0:
            break


if __name__ == "__main__":
    main()
