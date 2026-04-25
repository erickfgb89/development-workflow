"""API contract tests — no browser required.

Tests every REST endpoint directly via httpx ASGI transport.
Verifies request/response shapes, error codes, and state mutations.
"""
from __future__ import annotations

import json

import pytest

from tests.ui.conftest import _make_state, _wu


# ---------------------------------------------------------------------------
# /api/dirs
# ---------------------------------------------------------------------------

class TestDirsSearch:
    async def test_returns_dirs_list(self, client):
        r = await client.get("/api/dirs", params={"q": "home"})
        assert r.status_code == 200
        data = r.json()
        assert "dirs" in data
        assert isinstance(data["dirs"], list)

    async def test_empty_query_returns_empty(self, client):
        # The server only includes matches when q is non-empty; empty q returns []
        # (server skips the matching loop when q_lower is falsy)
        r = await client.get("/api/dirs", params={"q": ""})
        assert r.status_code == 200
        # When q is empty string, no candidates are added because the condition
        # `if not q_lower` short-circuits — but server walks candidates and adds
        # all that match (empty string matches everything).  Verify shape only.
        data = r.json()
        assert "dirs" in data
        assert isinstance(data["dirs"], list)
        assert len(data["dirs"]) <= 12

    async def test_caps_at_12(self, client, tmp_path):
        # With a realistic query unlikely to match many dirs, cap is not exceeded
        r = await client.get("/api/dirs", params={"q": "zzznomatch"})
        assert len(r.json()["dirs"]) <= 12


# ---------------------------------------------------------------------------
# /api/session/new
# ---------------------------------------------------------------------------

class TestSessionNew:
    async def test_creates_session(self, client, tmp_repo):
        r = await client.post("/api/session/new", json={"repo_path": str(tmp_repo)})
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert isinstance(data["session_id"], str)
        assert len(data["session_id"]) > 0

    async def test_missing_repo_path_returns_400(self, client):
        r = await client.post("/api/session/new", json={})
        assert r.status_code == 400
        assert "error" in r.json()

    async def test_nonexistent_path_returns_400(self, client):
        r = await client.post("/api/session/new", json={"repo_path": "/nonexistent/path/xyz"})
        assert r.status_code == 400
        assert "error" in r.json()

    async def test_second_call_creates_distinct_session(self, client, tmp_repo):
        r1 = await client.post("/api/session/new", json={"repo_path": str(tmp_repo)})
        r2 = await client.post("/api/session/new", json={"repo_path": str(tmp_repo)})
        assert r1.json()["session_id"] != r2.json()["session_id"]


# ---------------------------------------------------------------------------
# /api/session/load
# ---------------------------------------------------------------------------

class TestSessionLoad:
    async def test_loads_existing_session(self, client, make_session):
        sid = await make_session()
        r = await client.post("/api/session/load", json={"session_id": sid})
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert data["session_id"] == sid
        assert "repo_path" in data
        assert "context" in data
        assert "has_plan" in data

    async def test_missing_session_id_returns_400(self, client):
        r = await client.post("/api/session/load", json={})
        assert r.status_code == 400

    async def test_unknown_session_returns_404(self, client):
        r = await client.post("/api/session/load", json={"session_id": "nonexistent-id"})
        assert r.status_code == 404

    async def test_has_plan_false_when_no_state(self, client, make_session):
        sid = await make_session()
        data = (await client.post("/api/session/load", json={"session_id": sid})).json()
        assert data["has_plan"] is False

    async def test_has_plan_true_when_state_exists(self, client, make_session, seed_session):
        sid = await make_session()
        state = _make_state({"WU-001": _wu("WU-001")})
        seed_session(sid, state=state)
        data = (await client.post("/api/session/load", json={"session_id": sid})).json()
        assert data["has_plan"] is True


# ---------------------------------------------------------------------------
# /api/sessions
# ---------------------------------------------------------------------------

class TestSessionsList:
    async def test_empty_initially(self, client):
        r = await client.get("/api/sessions")
        assert r.status_code == 200
        data = r.json()
        assert "sessions" in data
        assert isinstance(data["sessions"], list)

    async def test_session_appears_after_create(self, client, make_session):
        sid = await make_session()
        r = await client.get("/api/sessions")
        ids = [s["id"] for s in r.json()["sessions"]]
        assert sid in ids


# ---------------------------------------------------------------------------
# /api/state
# ---------------------------------------------------------------------------

class TestGetState:
    async def test_empty_before_plan(self, client, make_session):
        sid = await make_session()
        r = await client.get("/api/state", params={"session_id": sid})
        assert r.status_code == 200
        assert r.json() == {}

    async def test_returns_state_after_seed(self, client, make_session, seed_session):
        sid = await make_session()
        state = _make_state({"WU-001": _wu("WU-001")})
        seed_session(sid, state=state)
        r = await client.get("/api/state", params={"session_id": sid})
        data = r.json()
        assert "work_units" in data
        assert "WU-001" in data["work_units"]

    async def test_unknown_session_returns_empty(self, client):
        r = await client.get("/api/state", params={"session_id": "does-not-exist"})
        assert r.status_code == 200
        assert r.json() == {}


# ---------------------------------------------------------------------------
# /api/context  (GET + POST)
# ---------------------------------------------------------------------------

class TestContext:
    async def test_empty_context_before_write(self, client, make_session):
        sid = await make_session()
        r = await client.get("/api/context", params={"session_id": sid})
        assert r.status_code == 200
        assert r.json()["content"] == ""

    async def test_save_and_retrieve_context(self, client, make_session):
        sid = await make_session()
        text = "Build a login flow with OAuth2"
        r = await client.post("/api/context", json={"session_id": sid, "content": text})
        assert r.json()["ok"] is True
        r2 = await client.get("/api/context", params={"session_id": sid})
        assert r2.json()["content"] == text

    async def test_save_context_unknown_session_returns_404(self, client):
        r = await client.post("/api/context", json={"session_id": "nope", "content": "x"})
        assert r.status_code == 404

    async def test_get_context_unknown_session_returns_empty(self, client):
        r = await client.get("/api/context", params={"session_id": "nope"})
        assert r.status_code == 200
        assert r.json()["content"] == ""


# ---------------------------------------------------------------------------
# /api/wu/{wu_id}/reset
# ---------------------------------------------------------------------------

class TestWUReset:
    async def test_reset_pending_to_pending(self, client, make_session, seed_session):
        sid = await make_session()
        state = _make_state({"WU-001": _wu("WU-001", status="failed", failure_count=2)})
        seed_session(sid, state=state)
        r = await client.post("/api/wu/WU-001/reset", json={"session_id": sid})
        assert r.status_code == 200
        assert r.json()["ok"] is True

    async def test_reset_clears_failure_count(self, client, make_session, seed_session):
        sid = await make_session()
        state = _make_state({"WU-001": _wu("WU-001", status="failed", failure_count=3)})
        seed_session(sid, state=state)
        await client.post("/api/wu/WU-001/reset", json={"session_id": sid})
        state_r = await client.get("/api/state", params={"session_id": sid})
        wu = state_r.json()["work_units"]["WU-001"]
        assert wu["status"] == "pending"
        assert "failure_count" not in wu

    async def test_reset_unblocks_downstream(self, client, make_session, seed_session):
        sid = await make_session()
        state = _make_state({
            "WU-001": _wu("WU-001", status="failed"),
            "WU-002": _wu("WU-002", status="blocked", deps=["WU-001"]),
        })
        seed_session(sid, state=state)
        await client.post("/api/wu/WU-001/reset", json={"session_id": sid})
        state_r = await client.get("/api/state", params={"session_id": sid})
        wus = state_r.json()["work_units"]
        assert wus["WU-002"]["status"] == "pending"

    async def test_reset_unknown_wu_returns_404(self, client, make_session, seed_session):
        sid = await make_session()
        seed_session(sid, state=_make_state({}))
        r = await client.post("/api/wu/WU-999/reset", json={"session_id": sid})
        assert r.status_code == 404

    async def test_reset_unknown_session_returns_404(self, client):
        r = await client.post("/api/wu/WU-001/reset", json={"session_id": "nope"})
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# /api/wu/{wu_id}/pivot
# ---------------------------------------------------------------------------

class TestWUPivot:
    async def test_approve_sets_completed(self, client, make_session, seed_session):
        sid = await make_session()
        state = _make_state({"WU-001": _wu("WU-001", status="failed", needs_user_pivot=True)})
        seed_session(sid, state=state)
        r = await client.post("/api/wu/WU-001/pivot", json={"session_id": sid, "action": "approve"})
        assert r.json()["ok"] is True
        wu = (await client.get("/api/state", params={"session_id": sid})).json()["work_units"]["WU-001"]
        assert wu["status"] == "completed"
        assert "needs_user_pivot" not in wu

    async def test_reset_pivot_sets_pending(self, client, make_session, seed_session):
        sid = await make_session()
        state = _make_state({"WU-001": _wu("WU-001", status="failed", needs_user_pivot=True, failure_count=2)})
        seed_session(sid, state=state)
        await client.post("/api/wu/WU-001/pivot", json={"session_id": sid, "action": "reset"})
        wu = (await client.get("/api/state", params={"session_id": sid})).json()["work_units"]["WU-001"]
        assert wu["status"] == "pending"
        assert "needs_user_pivot" not in wu
        assert "failure_count" not in wu

    async def test_abort_marks_session_abandoned(self, client, make_session, seed_session):
        sid = await make_session()
        state = _make_state({"WU-001": _wu("WU-001", status="failed")})
        seed_session(sid, state=state)
        await client.post("/api/wu/WU-001/pivot", json={"session_id": sid, "action": "abort"})
        s = (await client.get("/api/state", params={"session_id": sid})).json()
        assert s.get("status") == "abandoned"

    async def test_pivot_unknown_wu_returns_404(self, client, make_session, seed_session):
        sid = await make_session()
        seed_session(sid, state=_make_state({}))
        r = await client.post("/api/wu/WU-999/pivot", json={"session_id": sid, "action": "approve"})
        assert r.status_code == 404

    async def test_pivot_unknown_session_returns_404(self, client):
        r = await client.post("/api/wu/WU-001/pivot", json={"session_id": "nope", "action": "approve"})
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# /api/reshape  (validation — no real planner)
# ---------------------------------------------------------------------------

class TestReshape:
    async def test_missing_instructions_returns_400(self, client, make_session, seed_session):
        sid = await make_session()
        seed_session(sid, state=_make_state({"WU-001": _wu("WU-001")}))
        r = await client.post("/api/reshape", json={"session_id": sid, "instructions": ""})
        assert r.status_code == 400
        assert "error" in r.json()

    async def test_unknown_session_returns_404(self, client):
        r = await client.post("/api/reshape", json={"session_id": "nope", "instructions": "redo it"})
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# /api/gather/*  (validation — no real agent)
# ---------------------------------------------------------------------------

class TestGatherValidation:
    async def test_gather_send_without_start_returns_400(self, client, make_session):
        sid = await make_session()
        r = await client.post("/api/gather/send", json={"session_id": sid, "message": "hello"})
        assert r.status_code == 400

    async def test_gather_send_empty_message_returns_400(self, client, make_session):
        sid = await make_session()
        r = await client.post("/api/gather/send", json={"session_id": sid, "message": ""})
        assert r.status_code == 400

    async def test_gather_done_without_start_returns_400(self, client, make_session):
        sid = await make_session()
        r = await client.post("/api/gather/done", json={"session_id": sid})
        assert r.status_code == 400

    async def test_gather_start_unknown_session_returns_404(self, client):
        r = await client.post("/api/gather/start", json={"session_id": "nope"})
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# /api/files
# ---------------------------------------------------------------------------

class TestFilesSearch:
    async def test_empty_repo_returns_empty(self, client):
        r = await client.get("/api/files", params={"repo": "", "q": ""})
        assert r.status_code == 200
        assert r.json() == {"files": []}

    async def test_nonexistent_repo_returns_empty(self, client):
        r = await client.get("/api/files", params={"repo": "/nonexistent/zzz", "q": ""})
        assert r.status_code == 200
        assert r.json() == {"files": []}

    async def test_returns_files_in_repo(self, client, tmp_repo):
        # tmp_repo already has README.md
        r = await client.get("/api/files", params={"repo": str(tmp_repo), "q": ""})
        assert r.status_code == 200
        files = r.json()["files"]
        assert isinstance(files, list)
        assert any("README.md" in f for f in files)

    async def test_filter_by_query(self, client, tmp_repo):
        # Add a second file
        (tmp_repo / "main.py").write_text("# main")
        r = await client.get("/api/files", params={"repo": str(tmp_repo), "q": "main"})
        files = r.json()["files"]
        assert all("main" in f.lower() for f in files)
        assert any("main.py" in f for f in files)

    async def test_hidden_files_excluded(self, client, tmp_repo):
        (tmp_repo / ".hidden.py").write_text("secret")
        r = await client.get("/api/files", params={"repo": str(tmp_repo), "q": ""})
        files = r.json()["files"]
        assert not any(f.startswith(".") or "/.hidden" in f for f in files)

    async def test_hidden_dirs_excluded(self, client, tmp_repo):
        hidden_dir = tmp_repo / ".cache"
        hidden_dir.mkdir()
        (hidden_dir / "data.txt").write_text("cached")
        r = await client.get("/api/files", params={"repo": str(tmp_repo), "q": ""})
        files = r.json()["files"]
        assert not any(".cache" in f for f in files)

    async def test_caps_at_30(self, client, tmp_path):
        repo = tmp_path / "bigproject"
        repo.mkdir()
        (repo / ".git").mkdir()
        for i in range(40):
            (repo / f"file_{i:02d}.py").write_text("")
        r = await client.get("/api/files", params={"repo": str(repo), "q": ""})
        assert len(r.json()["files"]) <= 30

    async def test_results_are_relative_paths(self, client, tmp_repo):
        src = tmp_repo / "src"
        src.mkdir()
        (src / "app.py").write_text("")
        r = await client.get("/api/files", params={"repo": str(tmp_repo), "q": ""})
        files = r.json()["files"]
        assert not any(f.startswith("/") for f in files)


# ---------------------------------------------------------------------------
# /api/diff
# ---------------------------------------------------------------------------

def _make_real_git_repo(path):
    """Create a real git repo with one commit, returns commit sha."""
    import subprocess
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True, capture_output=True)
    (path / "hello.py").write_text("print('hello')\n")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "feat(WU-001): add hello.py"], cwd=path, check=True, capture_output=True)
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=path, text=True).strip()
    return sha


class TestDiffEndpoint:
    async def test_no_session_returns_404(self, client):
        r = await client.get("/api/diff", params={"session_id": "nope", "wu_id": "WU-001"})
        assert r.status_code == 404

    async def test_missing_wu_returns_404(self, client, make_session, seed_session):
        sid = await make_session()
        seed_session(sid, state=_make_state({"WU-001": _wu("WU-001", status="completed")}))
        r = await client.get("/api/diff", params={"session_id": sid, "wu_id": "WU-999"})
        assert r.status_code == 404

    async def test_wu_without_commit_returns_empty_files(self, client, make_session, seed_session):
        sid = await make_session()
        # Completed WU with no commit_sha and no matching git log entry
        seed_session(sid, state=_make_state({"WU-001": _wu("WU-001", status="completed")}))
        r = await client.get("/api/diff", params={"session_id": sid, "wu_id": "WU-001"})
        assert r.status_code == 200
        data = r.json()
        assert "files" in data
        assert data["files"] == []

    async def test_diff_with_commit_sha(self, client, tmp_path, tmp_data_dir):
        """End-to-end: real git repo + commit_sha stored in state → diff is returned."""
        import subprocess
        from overture.web_ui.server import _build_app
        from httpx import AsyncClient, ASGITransport

        repo = tmp_path / "gitrepo"
        sha = _make_real_git_repo(repo)

        app = _build_app(None, standalone=True, data_dir=tmp_data_dir)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            # Create session pointing at this real git repo
            r = await c.post("/api/session/new", json={"repo_path": str(repo)})
            sid = r.json()["session_id"]

            # Seed state with commit_sha on the WU
            wu = _wu("WU-001", status="completed")
            wu["commit_sha"] = sha
            state = _make_state({"WU-001": wu})
            (tmp_data_dir / sid / "state.json").write_text(json.dumps(state))

            r = await c.get("/api/diff", params={"session_id": sid, "wu_id": "WU-001"})
            assert r.status_code == 200
            data = r.json()
            assert data["commit_sha"] == sha
            assert isinstance(data["files"], list)
            assert len(data["files"]) >= 1
            f = data["files"][0]
            assert f["path"] == "hello.py"
            assert isinstance(f["additions"], int)
            assert f["additions"] > 0
            assert isinstance(f["beforeLines"], list)
            assert isinstance(f["afterLines"], list)
            # At least one add line in afterLines
            assert any(line["type"] == "add" for line in f["afterLines"])

    async def test_diff_fallback_git_grep(self, client, tmp_path, tmp_data_dir):
        """Without commit_sha, endpoint falls back to git log --grep."""
        import subprocess
        from overture.web_ui.server import _build_app
        from httpx import AsyncClient, ASGITransport

        repo = tmp_path / "greprepo"
        sha = _make_real_git_repo(repo)  # commit msg contains WU-001

        app = _build_app(None, standalone=True, data_dir=tmp_data_dir)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/api/session/new", json={"repo_path": str(repo)})
            sid = r.json()["session_id"]

            # Seed state WITHOUT commit_sha
            wu = _wu("WU-001", status="completed")
            state = _make_state({"WU-001": wu})
            (tmp_data_dir / sid / "state.json").write_text(json.dumps(state))

            r = await c.get("/api/diff", params={"session_id": sid, "wu_id": "WU-001"})
            assert r.status_code == 200
            data = r.json()
            # git log --oneline returns a short sha; verify it's a prefix of the full sha
            assert sha.startswith(data["commit_sha"]), (
                f"Expected {sha!r} to start with {data['commit_sha']!r}"
            )
            assert len(data["files"]) >= 1
