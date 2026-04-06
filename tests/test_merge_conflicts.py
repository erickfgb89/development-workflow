"""Tests for commit_batch() — priority merge with conflict recall loop."""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from orchestrate.orchestrator import commit_batch
from orchestrate.state import load_state, save_state


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True, capture_output=True)
    (path / "README.md").write_text("initial\n")
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)


def _make_worktree(repo: Path, wu_id: str, worktree_base: Path, filename: str, content: str) -> str:
    """Create a worktree with a single new-file commit."""
    branch = f"wu/{wu_id}"
    wt_path = str(worktree_base / wu_id)
    subprocess.run(["git", "worktree", "add", "-b", branch, wt_path], cwd=repo, check=True, capture_output=True)
    (Path(wt_path) / filename).write_text(content)
    subprocess.run(["git", "add", "."], cwd=wt_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", f"implement {wu_id}"], cwd=wt_path, check=True, capture_output=True)
    return wt_path


def _make_state(repo: Path, wus: dict) -> dict:
    return {
        "version": 1,
        "branch": "main",
        "pr_number": None,
        "current_phase": "executing",
        "repo_root": str(repo),
        "problem_statement_file": None,
        "traceability_matrix": {},
        "work_units": {
            wu_id: {
                "id": wu_id,
                "title": wu_id,
                "file": f"work-units/{wu_id}.md",
                "solution_domain": [],
                "depends_on": [],
                "blocks": [],
                "status": "complete",
                "recall_count": 0,
                "assigned_batch": None,
                "worktree_path": info["worktree_path"],
                "worktree_branch": f"wu/{wu_id}",
            }
            for wu_id, info in wus.items()
        },
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_commit_batch_merges_single_clean_wu(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    wt_base = tmp_path / "worktrees"
    wt_base.mkdir()

    import orchestrate.worktrees as wt_mod
    monkeypatch.setattr(wt_mod, "WORKTREE_BASE", str(wt_base))

    wt_path = _make_worktree(repo, "WU-001", wt_base, "feature.txt", "feature\n")
    state_file = repo / "state.json"
    state = _make_state(repo, {"WU-001": {"worktree_path": wt_path}})
    save_state(state, state_file)

    approved = [{"wu_id": "WU-001", "worktree_path": wt_path, "domain_breached": False}]
    await commit_batch(approved, state, state_file, str(repo))

    # Worktree path cleared in state
    reloaded = load_state(state_file)
    assert reloaded["work_units"]["WU-001"]["worktree_path"] is None
    # File merged into repo
    assert (repo / "feature.txt").exists()


@pytest.mark.asyncio
async def test_commit_batch_merges_clean_before_domain_breached(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    wt_base = tmp_path / "worktrees"
    wt_base.mkdir()

    import orchestrate.worktrees as wt_mod
    monkeypatch.setattr(wt_mod, "WORKTREE_BASE", str(wt_base))

    wt_clean = _make_worktree(repo, "WU-clean", wt_base, "clean.txt", "clean\n")
    wt_breach = _make_worktree(repo, "WU-breach", wt_base, "breach.txt", "breach\n")

    state_file = repo / "state.json"
    state = _make_state(repo, {
        "WU-clean": {"worktree_path": wt_clean},
        "WU-breach": {"worktree_path": wt_breach},
    })
    save_state(state, state_file)

    # breach first in input list — should still merge after clean
    approved = [
        {"wu_id": "WU-breach", "worktree_path": wt_breach, "domain_breached": True},
        {"wu_id": "WU-clean", "worktree_path": wt_clean, "domain_breached": False},
    ]
    await commit_batch(approved, state, state_file, str(repo))

    log = subprocess.run(
        ["git", "log", "--oneline"], cwd=repo, capture_output=True, text=True
    ).stdout
    # git log is newest-first; merged-first means higher line number (lower in output string)
    clean_pos = log.index("WU-clean")
    breach_pos = log.index("WU-breach")
    assert clean_pos > breach_pos, "clean WU merged first so it appears later in git log output"


@pytest.mark.asyncio
async def test_commit_batch_aborts_conflict_and_recalls(tmp_path):
    """First merge raises, implementer is recalled, second merge succeeds."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)

    state_file = repo / "state.json"
    wt_path = "/tmp/fake-wt"
    state = _make_state(repo, {"WU-conf": {"worktree_path": wt_path}})
    save_state(state, state_file)

    fake_impl_report = {
        "wu_id": "WU-conf", "status": "complete",
        "domain_breach": {"occurred": False},
        "files_changed": [], "tests_passing": True,
    }
    fake_review = {"verdict": "approved", "feedback_target": None}

    mock_implementing = AsyncMock(return_value=[("WU-conf", wt_path, fake_impl_report)])
    mock_reviewing = AsyncMock(return_value=[("WU-conf", fake_review)])

    call_count = 0

    def patched_merge(wu_id, wt_path, repo_root):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise Exception("simulated conflict")
        # Second call succeeds (no-op — state update is what matters)

    with (
        patch("orchestrate.orchestrator.handle_implementing", mock_implementing),
        patch("orchestrate.orchestrator.handle_reviewing", mock_reviewing),
        patch("orchestrate.orchestrator.merge_worktree", patched_merge),
        patch("orchestrate.orchestrator.abort_merge"),
    ):
        approved = [{"wu_id": "WU-conf", "worktree_path": wt_path, "domain_breached": False}]
        await commit_batch(approved, state, state_file, str(repo))

    assert call_count == 2, "merge_worktree should be called twice (fail then succeed)"
    mock_implementing.assert_called_once()
    mock_reviewing.assert_called_once()


@pytest.mark.asyncio
async def test_commit_batch_second_conflict_prompts_user_skip(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)

    state_file = repo / "state.json"
    state = _make_state(repo, {"WU-conf": {"worktree_path": "/tmp/fake"}})
    save_state(state, state_file)

    mock_implementing = AsyncMock(return_value=[("WU-conf", "/tmp/fake", {"wu_id": "WU-conf", "status": "complete", "domain_breach": {"occurred": False}, "files_changed": [], "tests_passing": True})])
    mock_reviewing = AsyncMock(return_value=[("WU-conf", {"verdict": "approved"})])

    def always_conflict(wu_id, wt_path, repo_root):
        raise Exception("conflict")

    with (
        patch("orchestrate.orchestrator.merge_worktree", always_conflict),
        patch("orchestrate.orchestrator.abort_merge"),
        patch("orchestrate.orchestrator.handle_implementing", mock_implementing),
        patch("orchestrate.orchestrator.handle_reviewing", mock_reviewing),
        patch("builtins.input", return_value="skip"),
    ):
        approved = [{"wu_id": "WU-conf", "worktree_path": "/tmp/fake", "domain_breached": False}]
        await commit_batch(approved, state, state_file, str(repo))

    reloaded = load_state(state_file)
    assert reloaded["work_units"]["WU-conf"]["status"] == "failed"


@pytest.mark.asyncio
async def test_commit_batch_second_conflict_abort_raises(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)

    state_file = repo / "state.json"
    state = _make_state(repo, {"WU-conf": {"worktree_path": "/tmp/fake"}})
    save_state(state, state_file)

    mock_implementing = AsyncMock(return_value=[("WU-conf", "/tmp/fake", {"wu_id": "WU-conf", "status": "complete", "domain_breach": {"occurred": False}, "files_changed": [], "tests_passing": True})])
    mock_reviewing = AsyncMock(return_value=[("WU-conf", {"verdict": "approved"})])

    def always_conflict(wu_id, wt_path, repo_root):
        raise Exception("conflict")

    with (
        patch("orchestrate.orchestrator.merge_worktree", always_conflict),
        patch("orchestrate.orchestrator.abort_merge"),
        patch("orchestrate.orchestrator.handle_implementing", mock_implementing),
        patch("orchestrate.orchestrator.handle_reviewing", mock_reviewing),
        patch("builtins.input", return_value="abort"),
    ):
        approved = [{"wu_id": "WU-conf", "worktree_path": "/tmp/fake", "domain_breached": False}]
        with pytest.raises(SystemExit):
            await commit_batch(approved, state, state_file, str(repo))
