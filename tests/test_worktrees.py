"""Tests for orchestrate/worktrees.py — require a real git repo via tmp_path."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

import orchestrate.worktrees as wt
from orchestrate.worktrees import (
    abort_merge,
    create_worktree,
    merge_batch,
    merge_worktree,
    remove_worktree,
    worktree_exists,
    worktree_has_changes,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _init_repo(path: Path) -> None:
    """Initialise a bare-minimum git repo with an initial commit."""
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "test@test.com"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Test"], check=True, capture_output=True)
    # Create an initial commit so HEAD exists
    (path / "README.md").write_text("init\n")
    subprocess.run(["git", "-C", str(path), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "commit", "-m", "init"], check=True, capture_output=True)


def _branch_exists(repo: Path, branch: str) -> bool:
    result = subprocess.run(
        ["git", "-C", str(repo), "branch", "--list", branch],
        capture_output=True,
        text=True,
    )
    return branch in result.stdout


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_create_worktree_creates_directory_and_branch(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    worktree_base = tmp_path / "worktrees"
    monkeypatch.setattr(wt, "WORKTREE_BASE", str(worktree_base))

    path = create_worktree("wu-001", repo)

    assert os.path.isdir(path), "Worktree directory should exist"
    assert _branch_exists(repo, "wu/wu-001"), "Branch wu/wu-001 should have been created"


def test_create_worktree_raises_if_branch_already_exists(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    worktree_base = tmp_path / "worktrees"
    monkeypatch.setattr(wt, "WORKTREE_BASE", str(worktree_base))

    create_worktree("wu-dup", repo)

    with pytest.raises(subprocess.CalledProcessError):
        create_worktree("wu-dup", repo)


def test_remove_worktree_removes_directory_and_branch(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    worktree_base = tmp_path / "worktrees"
    monkeypatch.setattr(wt, "WORKTREE_BASE", str(worktree_base))

    path = create_worktree("wu-rem", repo)
    assert os.path.isdir(path)

    remove_worktree(path, repo, "wu-rem")

    assert not os.path.isdir(path), "Worktree directory should be gone"
    assert not _branch_exists(repo, "wu/wu-rem"), "Branch should be deleted"


def test_merge_worktree_merges_commit_into_calling_repo(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    worktree_base = tmp_path / "worktrees"
    monkeypatch.setattr(wt, "WORKTREE_BASE", str(worktree_base))

    wt_path = create_worktree("wu-merge", repo)

    # Add a commit in the worktree
    new_file = Path(wt_path) / "feature.txt"
    new_file.write_text("feature\n")
    subprocess.run(["git", "-C", wt_path, "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", wt_path, "commit", "-m", "add feature"], check=True, capture_output=True)

    merge_worktree("wu-merge", wt_path, repo)

    # Confirm the file now exists in the main repo
    assert (repo / "feature.txt").exists(), "Merged file should exist in main repo"
    # Confirm the worktree directory was cleaned up
    assert not os.path.isdir(wt_path), "Worktree directory should have been removed after merge"


def test_abort_merge_succeeds_with_no_merge_in_progress(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)

    # Should not raise even when there's no merge in progress (check=False)
    abort_merge(repo)


def test_worktree_has_changes_false_for_clean_true_after_modification(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    worktree_base = tmp_path / "worktrees"
    monkeypatch.setattr(wt, "WORKTREE_BASE", str(worktree_base))

    wt_path = create_worktree("wu-chk", repo)

    assert worktree_has_changes(wt_path) is False, "Fresh worktree should be clean"

    # Modify a file
    (Path(wt_path) / "dirty.txt").write_text("dirty\n")

    assert worktree_has_changes(wt_path) is True, "Worktree with untracked file should report changes"


def test_worktree_exists_true_and_false(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    worktree_base = tmp_path / "worktrees"
    monkeypatch.setattr(wt, "WORKTREE_BASE", str(worktree_base))

    wt_path = create_worktree("wu-ex", repo)

    assert worktree_exists(wt_path) is True

    remove_worktree(wt_path, repo, "wu-ex")

    assert worktree_exists(wt_path) is False


def test_merge_batch_merges_clean_before_domain_breached(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    worktree_base = tmp_path / "worktrees"
    monkeypatch.setattr(wt, "WORKTREE_BASE", str(worktree_base))

    # Create two worktrees — one clean, one domain-breached
    wt_clean_path = create_worktree("wu-clean", repo)
    (Path(wt_clean_path) / "clean.txt").write_text("clean\n")
    subprocess.run(["git", "-C", wt_clean_path, "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", wt_clean_path, "commit", "-m", "clean commit"], check=True, capture_output=True)

    wt_breach_path = create_worktree("wu-breach", repo)
    (Path(wt_breach_path) / "breached.txt").write_text("breached\n")
    subprocess.run(["git", "-C", wt_breach_path, "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", wt_breach_path, "commit", "-m", "breach commit"], check=True, capture_output=True)

    # Put domain-breached first in input list to verify ordering
    approved_wus = [
        {"wu_id": "wu-breach", "worktree_path": wt_breach_path, "domain_breached": True},
        {"wu_id": "wu-clean", "worktree_path": wt_clean_path, "domain_breached": False},
    ]

    merged, conflicts = merge_batch(approved_wus, repo)

    assert merged == ["wu-clean", "wu-breach"], "Clean WU should merge before domain-breached WU"
    assert conflicts == []
    assert (repo / "clean.txt").exists()
    assert (repo / "breached.txt").exists()


def test_merge_batch_conflict_preserved_and_others_continue(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    worktree_base = tmp_path / "worktrees"
    monkeypatch.setattr(wt, "WORKTREE_BASE", str(worktree_base))

    # Create a conflicting worktree: both main repo and worktree edit the same file
    wt_conflict_path = create_worktree("wu-conflict", repo)
    (Path(wt_conflict_path) / "README.md").write_text("conflict version\n")
    subprocess.run(["git", "-C", wt_conflict_path, "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", wt_conflict_path, "commit", "-m", "conflict commit"], check=True, capture_output=True)

    # Also commit a different change to README in main repo to cause conflict
    (repo / "README.md").write_text("main repo version\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "main repo change"], check=True, capture_output=True)

    # Create a clean worktree (touches a different file — no conflict)
    wt_ok_path = create_worktree("wu-ok", repo)
    (Path(wt_ok_path) / "ok.txt").write_text("ok\n")
    subprocess.run(["git", "-C", wt_ok_path, "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", wt_ok_path, "commit", "-m", "ok commit"], check=True, capture_output=True)

    approved_wus = [
        {"wu_id": "wu-conflict", "worktree_path": wt_conflict_path, "domain_breached": False},
        {"wu_id": "wu-ok", "worktree_path": wt_ok_path, "domain_breached": False},
    ]

    merged, conflicts = merge_batch(approved_wus, repo)

    assert "wu-conflict" in conflicts, "Conflicting WU should be in conflicts list"
    assert "wu-ok" in merged, "Non-conflicting WU should still merge successfully"
    # Worktree for conflict should still exist (preserved for recall)
    assert os.path.isdir(wt_conflict_path), "Conflicting worktree directory should be preserved"
