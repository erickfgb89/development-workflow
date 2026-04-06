"""Tests for recover_session() — cold start / --resume recovery."""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from orchestrate.orchestrator import recover_session
from orchestrate.state import save_state, load_state


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=path, check=True, capture_output=True)
    (path / "README.md").write_text("init\n")
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)


def _base_state(repo: Path, extra_wus: dict | None = None) -> dict:
    wus = extra_wus or {}
    return {
        "version": 1, "branch": "main", "pr_number": None,
        "current_phase": "executing", "repo_root": str(repo),
        "problem_statement_file": None, "traceability_matrix": {},
        "work_units": wus,
    }


def _wu(wu_id: str, status: str, worktree_path: str | None = None) -> dict:
    return {
        "id": wu_id, "title": wu_id, "file": f"work-units/{wu_id}.md",
        "solution_domain": [], "depends_on": [], "blocks": [],
        "status": status, "recall_count": 0,
        "assigned_batch": None,
        "worktree_path": worktree_path,
        "worktree_branch": f"wu/{wu_id}" if worktree_path else None,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_in_progress_with_dirty_worktree_routes_to_reviewer(tmp_path):
    """WU in_progress with uncommitted changes → reviewer is called."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)

    # Create a fake worktree dir with a dirty file
    wt_path = tmp_path / "worktrees" / "WU-001"
    wt_path.mkdir(parents=True)
    # Make it look like a git repo with changes
    subprocess.run(["git", "init"], cwd=wt_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=wt_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=wt_path, check=True, capture_output=True)
    (wt_path / "new_file.py").write_text("new\n")
    # Uncommitted change → worktree_has_changes returns True

    state_file = repo / "state.json"
    state = _base_state(repo, {"WU-001": _wu("WU-001", "in_progress", str(wt_path))})
    save_state(state, state_file)

    fake_review = {"verdict": "approved"}
    mock_reviewing = AsyncMock(return_value=[("WU-001", fake_review)])
    mock_triage = AsyncMock(return_value={"action": "user", "message": "no failed wus"})

    with (
        patch("orchestrate.orchestrator.handle_reviewing", mock_reviewing),
        patch("orchestrate.orchestrator.handle_unexpected_failure", mock_triage),
    ):
        result = await recover_session(state, state_file, str(repo))

    mock_reviewing.assert_called_once()
    assert result["work_units"]["WU-001"]["status"] == "complete"


@pytest.mark.asyncio
async def test_in_progress_with_clean_worktree_resets_to_pending(tmp_path):
    """WU in_progress with no worktree changes → reset to pending."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)

    # Worktree path doesn't exist at all
    state_file = repo / "state.json"
    state = _base_state(repo, {"WU-001": _wu("WU-001", "in_progress", "/tmp/nonexistent-wt")})
    save_state(state, state_file)

    mock_triage = AsyncMock(return_value={"action": "user", "message": "none"})

    with patch("orchestrate.orchestrator.handle_unexpected_failure", mock_triage):
        result = await recover_session(state, state_file, str(repo))

    assert result["work_units"]["WU-001"]["status"] == "pending"
    assert result["work_units"]["WU-001"]["worktree_path"] is None


@pytest.mark.asyncio
async def test_failed_wu_calls_triage(tmp_path):
    """WU in 'failed' state triggers a triage call."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)

    state_file = repo / "state.json"
    state = _base_state(repo, {"WU-001": _wu("WU-001", "failed")})
    save_state(state, state_file)

    mock_triage = AsyncMock(return_value={"action": "retry", "message": "transient"})

    with patch("orchestrate.orchestrator.handle_unexpected_failure", mock_triage):
        result = await recover_session(state, state_file, str(repo))

    mock_triage.assert_called_once()
    assert result["work_units"]["WU-001"]["status"] == "pending"
    assert result["work_units"]["WU-001"]["recall_count"] == 0


@pytest.mark.asyncio
async def test_recovery_checkpoint_emitted(tmp_path, capsys):
    """Recovery emits a checkpoint with notes describing what was found."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)

    state_file = repo / "state.json"
    state = _base_state(repo, {
        "WU-001": _wu("WU-001", "in_progress", "/tmp/nonexistent"),
        "WU-002": _wu("WU-002", "failed"),
    })
    save_state(state, state_file)

    mock_triage = AsyncMock(return_value={"action": "retry", "message": "ok"})

    with patch("orchestrate.orchestrator.handle_unexpected_failure", mock_triage):
        await recover_session(state, state_file, str(repo))

    out = capsys.readouterr().out
    assert "RESUME → BATCH_EXTRACT" in out
    assert "reset_to_pending" in out
    assert "triage" in out


@pytest.mark.asyncio
async def test_no_wus_remain_in_progress_after_recovery(tmp_path):
    """After recover_session, no WU has status 'in_progress'."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)

    state_file = repo / "state.json"
    state = _base_state(repo, {
        "WU-001": _wu("WU-001", "in_progress", "/tmp/nonexistent-a"),
        "WU-002": _wu("WU-002", "in_progress", "/tmp/nonexistent-b"),
    })
    save_state(state, state_file)

    mock_triage = AsyncMock(return_value={"action": "user", "message": "none"})

    with patch("orchestrate.orchestrator.handle_unexpected_failure", mock_triage):
        result = await recover_session(state, state_file, str(repo))

    statuses = [wu["status"] for wu in result["work_units"].values()]
    assert "in_progress" not in statuses


@pytest.mark.asyncio
async def test_triage_abort_raises_system_exit(tmp_path):
    """Triage action=abort during recovery raises SystemExit."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)

    state_file = repo / "state.json"
    state = _base_state(repo, {"WU-001": _wu("WU-001", "failed")})
    save_state(state, state_file)

    mock_triage = AsyncMock(return_value={"action": "abort", "message": "unrecoverable"})

    with patch("orchestrate.orchestrator.handle_unexpected_failure", mock_triage):
        with pytest.raises(SystemExit, match="unrecoverable"):
            await recover_session(state, state_file, str(repo))
