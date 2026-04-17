"""Planner agent.

Reads context.md from a session and produces:
- state.json    — the machine-readable DAG (source of truth)
- plan.md       — human-readable sketch
- wus/WU-NNN.json — individual Work Unit files

The Planner output is validated against contracts/planner_output.json
before anything is written to disk.
"""

from __future__ import annotations

import json
import logging
import textwrap
from pathlib import Path
from typing import Any

import jsonschema

from .sdk_wrapper import run_query_json
from .session_manager import SessionManager

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema loading
# ---------------------------------------------------------------------------

_CONTRACTS_DIR = Path(__file__).parent.parent.parent / "contracts"


def _load_schema(name: str) -> dict[str, Any]:
    path = _CONTRACTS_DIR / name
    return json.loads(path.read_text(encoding="utf-8"))


def schema_example(schema_name: str, index: int = 0) -> str:
    """Return the first (or nth) example from a contract schema as a JSON string.

    This is used to keep prompt templates in sync with the contracts directory —
    the example shown to agents is always the one defined in the schema, never
    a hard-coded snapshot in Python code.
    """
    schema = _load_schema(schema_name)
    examples = schema.get("examples", [])
    if not examples:
        return "{}"
    return json.dumps(examples[index], indent=2)


def _validate(data: dict[str, Any], schema_name: str) -> None:
    schema = _load_schema(schema_name)
    validator_cls = jsonschema.validators.validator_for(schema)
    validator_cls.check_schema(schema)
    validator = validator_cls(schema)
    errors = sorted(validator.iter_errors(data), key=lambda e: list(e.path))
    if errors:
        messages = "\n".join(f"  - {e.json_path}: {e.message}" for e in errors[:10])
        raise ValueError(f"Schema validation failed ({schema_name}):\n{messages}")


# ---------------------------------------------------------------------------
# Prompt loading
# ---------------------------------------------------------------------------

def _load_prompt(name: str) -> str:
    prompts_dir = Path(__file__).parent.parent.parent / "prompts"
    path = prompts_dir / name
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _planner_system_prompt() -> str:
    core = _load_prompt("core.md")
    planning = _load_prompt("common_planning.md")
    planner = _load_prompt("task_planner.md")
    return "\n\n".join(filter(None, [core, planning, planner]))


# ---------------------------------------------------------------------------
# Planner prompt
# ---------------------------------------------------------------------------

_PLANNER_PROMPT_TEMPLATE = textwrap.dedent("""\
    You are the Planner agent for an Overture session.

    ## Session ID
    {session_id}

    ## Context
    {context_md}

    ## Your task
    Analyse the context and produce a complete DAG of Work Units.

    Rules:
    1. Each WU must be atomic (max ~30 minutes of implementation work).
    2. `solution_domain` must list every file path the WU will create or modify.
       Be exhaustive — this is used to detect parallel conflicts.
    3. `dependencies` must reference only WU IDs in this same plan.
    4. Use backward-chaining: start from the final goal and work toward leaves.
    5. IDs must be sequential: WU-001, WU-002, … (zero-padded to 3 digits).
    6. All WUs start with status "pending".

    ## Output format
    Respond with ONLY a JSON object (no commentary, no fences) matching this example:

    {example}
""")


# ---------------------------------------------------------------------------
# Plan markdown renderer
# ---------------------------------------------------------------------------

def _render_plan_md(dag: dict[str, Any]) -> str:
    lines = [f"# Plan — {dag['session_id']}\n"]
    for wu in dag["work_units"]:
        deps = ", ".join(wu["dependencies"]) if wu["dependencies"] else "none"
        lines.append(f"## {wu['id']}: {wu['title']}")
        lines.append(f"**Dependencies:** {deps}  ")
        lines.append(f"**Estimate:** {wu.get('estimate_minutes', '?')} min\n")
        lines.append(wu["description"])
        lines.append("")
        lines.append("**Acceptance Criteria:**")
        for ac in wu["acceptance_criteria"]:
            lines.append(f"- {ac}")
        lines.append("")
        lines.append("**Solution Domain:**")
        for f in wu["solution_domain"]:
            lines.append(f"- `{f}`")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def run_plan(
    session_manager: SessionManager,
    target_repo: Path,
    *,
    verbose: bool = False,
) -> dict[str, Any]:
    """Generate the DAG from context.md and persist it.

    Returns the validated DAG dict (same shape as planner_output.json schema).
    """
    context_md = session_manager.read_context()
    session_id = session_manager.session_id

    prompt = _PLANNER_PROMPT_TEMPLATE.format(
        session_id=session_id,
        context_md=context_md,
        example=schema_example("planner_output.json"),
    )

    system_prompt = _planner_system_prompt()

    if verbose:
        logger.info("Sending planning prompt to Planner agent…")

    dag = await run_query_json(
        prompt,
        system_prompt=system_prompt,
        cwd=target_repo,
    )

    # Validate against schema
    _validate(dag, "planner_output.json")

    # Patch session_id in case agent returned wrong one
    dag["session_id"] = session_id

    # Build state.json structure (adds status tracking metadata)
    state = _build_state(dag)
    session_manager.write_state(state)

    # Write individual WU files
    for wu in dag["work_units"]:
        session_manager.write_wu(wu["id"], wu)

    # Write human-readable plan.md
    session_manager.write_plan(_render_plan_md(dag))

    wu_count = len(dag["work_units"])
    print(f"  Plan complete: {wu_count} Work Unit{'s' if wu_count != 1 else ''} across the DAG.")

    return dag


def _build_state(dag: dict[str, Any]) -> dict[str, Any]:
    """Build the state.json structure from a validated DAG."""
    wu_index: dict[str, dict[str, Any]] = {}
    for wu in dag["work_units"]:
        wu_index[wu["id"]] = {
            **wu,
            "failure_count": 0,
            "worktree_branch": None,
        }
    return {
        "session_id": dag["session_id"],
        "work_units": wu_index,
        "batch_number": 0,
    }


# ---------------------------------------------------------------------------
# DAG helpers (used by BatchManager)
# ---------------------------------------------------------------------------

def get_ready_wus(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Return WUs whose dependencies are all completed and status is pending."""
    wus = state["work_units"]
    ready = []
    for wu in wus.values():
        if wu["status"] != "pending":
            continue
        deps_done = all(
            wus.get(dep, {}).get("status") == "completed"
            for dep in wu["dependencies"]
        )
        if deps_done:
            ready.append(wu)
    return sorted(ready, key=lambda w: w["id"])


def mark_downstream_blocked(state: dict[str, Any], wu_id: str) -> None:
    """Mark all WUs that transitively depend on wu_id as 'blocked'."""
    wus = state["work_units"]
    changed = True
    while changed:
        changed = False
        for wu in wus.values():
            if wu["status"] in ("completed", "blocked"):
                continue
            if wu_id in wu["dependencies"] or any(
                dep in _blocked_ids(wus) for dep in wu["dependencies"]
            ):
                wu["status"] = "blocked"
                changed = True


def _blocked_ids(wus: dict[str, dict[str, Any]]) -> set[str]:
    return {wid for wid, wu in wus.items() if wu["status"] == "blocked"}
