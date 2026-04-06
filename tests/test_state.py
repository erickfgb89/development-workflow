"""Tests for orchestrate/state.py"""
from __future__ import annotations

import json
import pytest
from pathlib import Path

from orchestrate.state import (
    extract_batch,
    load_state,
    save_state,
    parse_json_result,
    increment_recall,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_state(wus: dict) -> dict:
    return {"work_units": wus}


def wu(status: str, depends_on=None, blocks=None, solution_domain=None) -> dict:
    d: dict = {"status": status}
    if depends_on is not None:
        d["depends_on"] = depends_on
    if blocks is not None:
        d["blocks"] = blocks
    if solution_domain is not None:
        d["solution_domain"] = solution_domain
    return d


# ---------------------------------------------------------------------------
# extract_batch tests
# ---------------------------------------------------------------------------

def test_extract_batch_empty_when_all_complete():
    state = make_state({
        "wu-1": wu("complete"),
        "wu-2": wu("complete"),
    })
    assert extract_batch(state) == []


def test_extract_batch_empty_when_pending_has_incomplete_deps():
    state = make_state({
        "wu-1": wu("pending"),
        "wu-2": wu("pending", depends_on=["wu-1"]),
    })
    # wu-2 depends on wu-1 which is pending, so neither is eligible
    # wu-1 has no depends_on so it IS eligible — but wu-2 is blocked
    result = extract_batch(state)
    assert "wu-2" not in result
    # wu-1 itself has no deps so it should be returned
    assert "wu-1" in result


def test_extract_batch_empty_when_all_blocked():
    """All pending WUs have incomplete dependencies — nothing eligible."""
    state = make_state({
        "wu-1": wu("in_progress"),
        "wu-2": wu("pending", depends_on=["wu-1"]),
    })
    assert extract_batch(state) == []


def test_extract_batch_returns_eligible_when_deps_complete():
    state = make_state({
        "wu-1": wu("complete"),
        "wu-2": wu("pending", depends_on=["wu-1"]),
    })
    result = extract_batch(state)
    assert result == ["wu-2"]


def test_extract_batch_respects_max_batch():
    state = make_state({
        f"wu-{i}": wu("pending", solution_domain=[f"file-{i}.py"])
        for i in range(10)
    })
    result = extract_batch(state, max_batch=3)
    assert len(result) == 3


def test_extract_batch_excludes_overlapping_solution_domains():
    """Two WUs sharing a file should not both be selected."""
    state = make_state({
        "wu-1": wu("pending", solution_domain=["shared.py", "a.py"]),
        "wu-2": wu("pending", solution_domain=["shared.py", "b.py"]),
        "wu-3": wu("pending", solution_domain=["c.py"]),
    })
    result = extract_batch(state, max_batch=5)
    # wu-1 and wu-2 overlap on shared.py — only one should be selected
    assert not ("wu-1" in result and "wu-2" in result)
    # wu-3 has no overlap, should be selected
    assert "wu-3" in result


def test_extract_batch_ranks_by_transitive_block_count():
    """WU with higher transitive block count should appear first."""
    state = make_state({
        "wu-a": wu("pending", blocks=["wu-c", "wu-d"], solution_domain=["a.py"]),
        "wu-b": wu("pending", blocks=[], solution_domain=["b.py"]),
        "wu-c": wu("pending", solution_domain=["c.py"]),
        "wu-d": wu("pending", solution_domain=["d.py"]),
    })
    result = extract_batch(state, max_batch=5)
    # wu-a blocks two others, so it should come before wu-b
    assert result.index("wu-a") < result.index("wu-b")


def test_extract_batch_no_depends_on_key():
    """WUs with no depends_on key should be treated as having empty list (eligible)."""
    state = make_state({
        "wu-1": {"status": "pending"},  # no depends_on key at all
    })
    result = extract_batch(state)
    assert result == ["wu-1"]


def test_extract_batch_no_solution_domain_key():
    """WUs with no solution_domain key should never block each other on domain."""
    state = make_state({
        "wu-1": {"status": "pending"},  # no solution_domain
        "wu-2": {"status": "pending"},  # no solution_domain
    })
    result = extract_batch(state, max_batch=5)
    # Both have empty domain sets — empty & empty == empty, so neither blocks the other
    assert "wu-1" in result
    assert "wu-2" in result


# ---------------------------------------------------------------------------
# parse_json_result tests
# ---------------------------------------------------------------------------

def test_parse_json_result_raw_json():
    raw = '{"status": "complete", "notes": "done"}'
    result = parse_json_result(raw)
    assert result == {"status": "complete", "notes": "done"}


def test_parse_json_result_fenced_json():
    raw = '```json\n{"status": "complete"}\n```'
    result = parse_json_result(raw)
    assert result == {"status": "complete"}


def test_parse_json_result_json_with_prose():
    raw = 'Here is the result:\n{"status": "complete", "score": 42}\nHope that helps!'
    result = parse_json_result(raw)
    assert result == {"status": "complete", "score": 42}


def test_parse_json_result_raises_on_no_json():
    with pytest.raises(ValueError, match="No JSON object found"):
        parse_json_result("There is no JSON here at all.")


# ---------------------------------------------------------------------------
# save_state atomicity tests
# ---------------------------------------------------------------------------

def test_save_state_atomic(tmp_path: Path):
    state_file = tmp_path / "state.json"
    state = {"work_units": {"wu-1": {"status": "pending"}}}

    save_state(state, state_file)

    # The real file should exist and be valid JSON
    assert state_file.exists()
    loaded = json.loads(state_file.read_text())
    assert loaded == state

    # The .tmp file must NOT remain
    tmp_file = state_file.with_suffix(".tmp")
    assert not tmp_file.exists()


# ---------------------------------------------------------------------------
# increment_recall tests
# ---------------------------------------------------------------------------

def test_increment_recall_initializes_from_zero():
    state = make_state({
        "wu-1": {"status": "pending"},  # no recall_count key
    })
    result = increment_recall(state, "wu-1")
    assert result == 1
    assert state["work_units"]["wu-1"]["recall_count"] == 1


def test_increment_recall_increments_existing():
    state = make_state({
        "wu-1": {"status": "pending", "recall_count": 2},
    })
    result = increment_recall(state, "wu-1")
    assert result == 3
    assert state["work_units"]["wu-1"]["recall_count"] == 3
