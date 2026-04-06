"""Git worktree management for parallel implementer isolation."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

WORKTREE_BASE = "/tmp/dev-workflow-worktrees"


def _run(cmd: list[str], cwd: str | Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    """Run a subprocess command, capturing output."""
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        check=check,
        capture_output=True,
        text=True,
    )


def create_worktree(wu_id: str, repo_root: str | Path) -> str:
    """
    Create an isolated git worktree for a work unit.

    Returns the absolute path to the new worktree.
    Raises subprocess.CalledProcessError if git fails.
    """
    branch = f"wu/{wu_id}"
    path = os.path.join(WORKTREE_BASE, wu_id)

    # Clean up any orphaned branch from a previous aborted run
    branch_exists = _run(
        ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
        cwd=repo_root, check=False
    ).returncode == 0
    if branch_exists:
        _run(["git", "branch", "-D", branch], cwd=repo_root, check=False)

    # Clean up any orphaned directory 
    if os.path.exists(path):
        _run(["git", "worktree", "remove", "--force", path], cwd=repo_root, check=False)
        import shutil
        if os.path.exists(path):
            shutil.rmtree(path, ignore_errors=True)

    # Ensure base directory exists
    os.makedirs(WORKTREE_BASE, exist_ok=True)

    _run(
        ["git", "worktree", "add", "-b", branch, path],
        cwd=repo_root,
    )
    return path


def remove_worktree(worktree_path: str, repo_root: str | Path, wu_id: str) -> None:
    """
    Remove an abandoned worktree (failed or rejected work).

    Deletes both the worktree directory and the associated branch.
    Uses --force in case the worktree has uncommitted changes.
    """
    _run(["git", "worktree", "remove", "--force", worktree_path], cwd=repo_root, check=False)
    _run(["git", "branch", "-D", f"wu/{wu_id}"], cwd=repo_root, check=False)


def merge_worktree(wu_id: str, worktree_path: str, repo_root: str | Path) -> None:
    """
    Merge an approved worktree back to the current branch (no-ff).

    Raises subprocess.CalledProcessError if the merge fails (e.g. conflict).
    Caller is responsible for calling abort_merge() if this raises.
    """
    _run(
        ["git", "merge", f"wu/{wu_id}", "--no-ff", "-m", f"Complete {wu_id}"],
        cwd=repo_root,
    )
    _run(["git", "worktree", "remove", worktree_path], cwd=repo_root)


def abort_merge(repo_root: str | Path) -> None:
    """Abort a failed merge, restoring the working tree to pre-merge state."""
    _run(["git", "merge", "--abort"], cwd=repo_root, check=False)


def worktree_has_changes(worktree_path: str) -> bool:
    """Return True if the worktree has any uncommitted changes or new commits."""
    result = _run(["git", "status", "--porcelain"], cwd=worktree_path, check=False)
    return bool(result.stdout.strip())


def worktree_exists(worktree_path: str) -> bool:
    """Return True if the worktree directory exists on disk."""
    return os.path.isdir(worktree_path)


# ---------------------------------------------------------------------------
# Priority-ordered batch merge
# ---------------------------------------------------------------------------

def merge_batch(
    approved_wus: list[dict],
    repo_root: str | Path,
) -> tuple[list[str], list[str]]:
    """
    Merge a batch of approved WUs in priority order.

    Priority:
      1. WUs whose implementer did NOT break its solution domain (no domain breach)
      2. WUs whose implementer DID break its domain (break-glass)
      3. Within each group, maintain the order from approved_wus

    Each element of approved_wus must have keys:
      - wu_id: str
      - worktree_path: str
      - domain_breached: bool  (from implementer report)

    Returns:
      merged: list of wu_ids successfully merged
      conflicts: list of wu_ids that had merge conflicts (not merged; worktrees preserved)
    """
    clean = [w for w in approved_wus if not w.get("domain_breached", False)]
    breached = [w for w in approved_wus if w.get("domain_breached", False)]
    ordered = clean + breached

    merged: list[str] = []
    conflicts: list[str] = []

    for wu in ordered:
        wu_id = wu["wu_id"]
        worktree_path = wu["worktree_path"]
        try:
            merge_worktree(wu_id, worktree_path, repo_root)
            merged.append(wu_id)
        except subprocess.CalledProcessError:
            abort_merge(repo_root)
            conflicts.append(wu_id)

    return merged, conflicts
