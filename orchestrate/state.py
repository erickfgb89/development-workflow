"""State machine I/O: read/write state.json and DAG batch extraction."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------

def load_state(state_file: Path) -> dict[str, Any]:
    """Read and return state.json as a dict."""
    return json.loads(state_file.read_text())


def save_state(state: dict[str, Any], state_file: Path) -> None:
    """Atomically write state to state.json (write-then-rename)."""
    tmp = state_file.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(state_file)


def find_state_file(project_root: Path) -> Path:
    """Return the path to state.json under project_root, or raise FileNotFoundError."""
    candidate = project_root / "state.json"
    if not candidate.exists():
        raise FileNotFoundError(f"No state.json found at {candidate}")
    return candidate


# ---------------------------------------------------------------------------
# Work unit status helpers
# ---------------------------------------------------------------------------

def set_wu_status(state: dict, wu_id: str, status: str) -> None:
    """Update a work unit's status in-memory (caller must save_state afterwards)."""
    state["work_units"][wu_id]["status"] = status


def set_wu_field(state: dict, wu_id: str, field: str, value: Any) -> None:
    """Set an arbitrary field on a work unit in-memory."""
    state["work_units"][wu_id][field] = value


def increment_recall(state: dict, wu_id: str) -> int:
    """Increment recall_count for a WU and return the new value."""
    count = state["work_units"][wu_id].get("recall_count", 0) + 1
    state["work_units"][wu_id]["recall_count"] = count
    return count


def pending_wus(state: dict) -> list[str]:
    """Return IDs of all WUs with status 'pending'."""
    return [
        wu_id
        for wu_id, wu in state["work_units"].items()
        if wu["status"] == "pending"
    ]


def in_progress_wus(state: dict) -> list[str]:
    """Return IDs of all WUs with status 'in_progress'."""
    return [
        wu_id
        for wu_id, wu in state["work_units"].items()
        if wu["status"] == "in_progress"
    ]


def all_complete(state: dict) -> bool:
    """Return True when every WU is 'complete'. Returns False if there are no WUs."""
    wus = state["work_units"]
    return bool(wus) and all(wu["status"] == "complete" for wu in wus.values())


# ---------------------------------------------------------------------------
# Batch extraction (pure Python, no LLM)
# ---------------------------------------------------------------------------

def extract_batch(state: dict, max_batch: int = 5) -> list[str]:
    """
    Select up to max_batch WUs that are ready to run in parallel.

    A WU is eligible if:
      1. Its status is 'pending'
      2. All its depends_on WUs have status 'complete'

    Among eligible WUs, select a maximal non-overlapping set by solution
    domain. Prefer WUs that unblock the most downstream work (highest
    transitive block count).

    Returns an empty list if no WUs are eligible (either all done, or all
    remaining are blocked by incomplete dependencies).
    """
    wus = state["work_units"]

    # 1. Find eligible WUs
    eligible = [
        wu_id for wu_id, wu in wus.items()
        if wu["status"] == "pending"
        and all(wus[dep]["status"] == "complete" for dep in wu.get("depends_on", []))
    ]

    if not eligible:
        return []

    # 2. Rank by unblocking power (transitive count of WUs this one blocks)
    def transitive_block_count(wu_id: str, visited: set[str] | None = None) -> int:
        if visited is None:
            visited = set()
        if wu_id in visited:
            return 0
        visited.add(wu_id)
        return sum(
            1 + transitive_block_count(blocked_id, visited)
            for blocked_id in wus[wu_id].get("blocks", [])
        )

    eligible.sort(key=transitive_block_count, reverse=True)

    # 3. Greedy selection: add WUs whose domain doesn't overlap with already-selected WUs
    selected: list[str] = []
    claimed_files: set[str] = set()
    for wu_id in eligible:
        domain = set(wus[wu_id].get("solution_domain", []))
        if not domain & claimed_files:
            selected.append(wu_id)
            claimed_files |= domain
            if len(selected) >= max_batch:
                break

    return selected


# ---------------------------------------------------------------------------
# JSON parsing helper (used by orchestrator for agent results)
# ---------------------------------------------------------------------------

def parse_json_result(raw: str) -> dict[str, Any]:
    """
    Extract and parse the first JSON object from a string.

    Agents may wrap their JSON in markdown code fences or include surrounding
    prose. This function strips fences and finds the first {...} block.
    """
    # Strip markdown code fences
    text = re.sub(r"```(?:json)?\s*", "", raw).strip()

    # Find the first JSON object
    start = text.find("{")
    if start == -1:
        raise ValueError(f"No JSON object found in agent result: {raw!r}")

    # Find the matching closing brace
    depth = 0
    for i, ch in enumerate(text[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start : i + 1])

    raise ValueError(f"Unmatched braces in agent result: {raw!r}")
