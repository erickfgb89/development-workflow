"""
End-to-end integration test: full orchestrator workflow on the toy calculator project.

Requires:
  - ANTHROPIC_API_KEY set in environment
  - orchestrate.pyz built at repo root

Run with:
  ORCHESTRATE_E2E=1 pytest tests/test_e2e.py -v -s
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
ZIPAPP = REPO_ROOT / "orchestrate.pyz"
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def requires_e2e(func):
    return pytest.mark.skipif(
        not os.environ.get("ORCHESTRATE_E2E"),
        reason="Set ORCHESTRATE_E2E=1 to run end-to-end tests",
    )(func)


@pytest.fixture
def toy_repo(tmp_path: Path) -> Path:
    """Set up the toy calculator project as a fresh git repo."""
    project = tmp_path / "toy_project"
    shutil.copytree(FIXTURES / "toy_project", project)

    subprocess.run(["git", "init"], cwd=project, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=project, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=project, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=project, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        cwd=project, check=True, capture_output=True
    )
    return project


@requires_e2e
def test_full_workflow_completes(toy_repo: Path, tmp_path: Path):
    """Drive the orchestrator through a complete workflow on the toy project."""
    assert ZIPAPP.exists(), f"orchestrate.pyz not found at {ZIPAPP} — run task-011 first"

    result = subprocess.run(
        [sys.executable, str(ZIPAPP), str(toy_repo)],
        input="Add a multiply function to the calculator module so the test suite passes.\n",
        cwd=str(toy_repo),
        capture_output=False,  # let stdout stream so we can see checkpoints
        text=True,
        timeout=600,  # 10 minutes max
    )

    assert result.returncode == 0, f"Orchestrator exited with code {result.returncode}"

    # Verify the toy project's test suite now passes
    test_result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-v"],
        cwd=str(toy_repo),
        capture_output=True,
        text=True,
    )
    assert test_result.returncode == 0, (
        f"Toy project tests still failing after orchestrator run:\n{test_result.stdout}\n{test_result.stderr}"
    )


@requires_e2e
def test_resume_after_interrupt(toy_repo: Path):
    """
    Verify --resume works: start the workflow, kill it after CONTEXT_GATHER,
    then resume and complete.

    This test is approximate — it kills after a fixed delay, which may or may not
    land after CONTEXT_GATHER. The important invariant is that resume completes
    without error even if the kill lands at an arbitrary point.
    """
    assert ZIPAPP.exists()

    # Start orchestrator, kill after 30 seconds
    proc = subprocess.Popen(
        [sys.executable, str(ZIPAPP), str(toy_repo)],
        stdin=subprocess.PIPE,
        text=True,
    )
    try:
        proc.communicate(
            input="Add a multiply function to the calculator module so the test suite passes.\n",
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()

    # Resume
    state_file = toy_repo / "state.json"
    if not state_file.exists():
        pytest.skip("Orchestrator did not write state.json before being killed")

    result = subprocess.run(
        [sys.executable, str(ZIPAPP), "--resume", str(toy_repo)],
        capture_output=False,
        text=True,
        timeout=600,
    )
    assert result.returncode == 0


@requires_e2e
def test_zipapp_is_self_contained(tmp_path: Path):
    """Verify --help works from any directory with no source tree present."""
    # Copy only the zipapp to an isolated directory
    isolated = tmp_path / "isolated"
    isolated.mkdir()
    shutil.copy(ZIPAPP, isolated / "orchestrate.pyz")

    result = subprocess.run(
        [sys.executable, "orchestrate.pyz", "--help"],
        cwd=str(isolated),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "project_root" in result.stdout
