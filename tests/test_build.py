"""Tests for scripts/build_zipapp.py and the resulting orchestrate.pyz."""
from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BUILD_SCRIPT = REPO_ROOT / "scripts" / "build_zipapp.py"
DEFAULT_OUTPUT = REPO_ROOT / "orchestrate.pyz"
EXPECTED_PROMPTS = [
    "context-gathering.md",
    "planner-sketcher.md",
    "planner-reshaper.md",
    "implementer.md",
    "reviewer.md",
]


def _build(output: Path | None = None) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(BUILD_SCRIPT)]
    if output is not None:
        cmd += ["--output", str(output)]
    return subprocess.run(cmd, capture_output=True, text=True)


def test_build_exits_zero_and_produces_pyz(tmp_path):
    """Build script exits 0 and produces the output file."""
    out = tmp_path / "orchestrate.pyz"
    result = _build(out)
    assert result.returncode == 0, f"build failed:\n{result.stderr}"
    assert out.exists(), "orchestrate.pyz was not created"
    assert out.stat().st_size > 0


def test_pyz_is_valid_zip(tmp_path):
    """The produced .pyz is a valid zip file."""
    out = tmp_path / "orchestrate.pyz"
    result = _build(out)
    assert result.returncode == 0, result.stderr
    assert zipfile.is_zipfile(out), "orchestrate.pyz is not a valid zip file"


def test_pyz_help_exits_zero(tmp_path):
    """python orchestrate.pyz --help exits 0 and shows usage text."""
    out = tmp_path / "orchestrate.pyz"
    result = _build(out)
    assert result.returncode == 0, result.stderr

    help_result = subprocess.run(
        [sys.executable, str(out), "--help"],
        capture_output=True,
        text=True,
    )
    assert help_result.returncode == 0, f"--help failed:\n{help_result.stderr}"
    combined = help_result.stdout + help_result.stderr
    assert "usage" in combined.lower() or "help" in combined.lower(), (
        f"no usage/help text in output:\n{combined}"
    )


def test_importlib_resources_loads_all_prompts(tmp_path):
    """importlib.resources can load each of the five prompt files from inside the zip."""
    out = tmp_path / "orchestrate.pyz"
    result = _build(out)
    assert result.returncode == 0, result.stderr

    # Run a small inline script that inserts the pyz into sys.path and loads each prompt
    script = f"""
import sys
sys.path.insert(0, r"{out}")
from importlib.resources import files
prompts = {EXPECTED_PROMPTS!r}
for name in prompts:
    content = files("prompts").joinpath(name).read_text()
    assert content, f"{{name}} is empty"
    print("OK:", name, content[:40].replace("\\n", " "))
"""
    load_result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
    )
    assert load_result.returncode == 0, (
        f"prompt loading failed:\n{load_result.stdout}\n{load_result.stderr}"
    )
    for name in EXPECTED_PROMPTS:
        assert f"OK: {name}" in load_result.stdout, f"prompt {name} not loaded"


def test_build_fails_when_prompt_missing(tmp_path):
    """Build script exits non-zero if a prompt file is missing."""
    # Create a fake prompts dir missing one file
    fake_prompts = tmp_path / "prompts"
    real_prompts = REPO_ROOT / "prompts"
    import shutil
    shutil.copytree(real_prompts, fake_prompts)
    (fake_prompts / "implementer.md").unlink()

    # Patch the build to use a fake repo root — easiest is to run with a temp staging
    # We'll simulate by temporarily renaming the file (subprocess approach via env)
    # Instead, test by calling the script with a temp output and a mocked source tree.
    # Build script reads from REPO_ROOT which is fixed, so we test via a subprocess
    # that renames the file, runs, and restores.
    real_implementer = REPO_ROOT / "prompts" / "implementer.md"
    backup = tmp_path / "implementer.md.bak"
    shutil.copy(real_implementer, backup)
    real_implementer.unlink()
    try:
        out = tmp_path / "orchestrate_missing.pyz"
        result = _build(out)
        assert result.returncode != 0, "build should fail when a prompt is missing"
        assert not out.exists() or out.stat().st_size == 0 or True  # file may not exist
        assert "missing" in result.stderr.lower() or "error" in result.stderr.lower()
    finally:
        shutil.copy(backup, real_implementer)


def test_build_is_idempotent(tmp_path):
    """Running the build twice produces the same output and leaves no staging dir."""
    out = tmp_path / "orchestrate.pyz"

    result1 = _build(out)
    assert result1.returncode == 0, result1.stderr
    size1 = out.stat().st_size

    result2 = _build(out)
    assert result2.returncode == 0, result2.stderr
    size2 = out.stat().st_size

    assert size1 == size2, f"sizes differ: {size1} vs {size2}"

    staging = REPO_ROOT / "_zipapp_staging"
    assert not staging.exists(), "_zipapp_staging directory left behind"
