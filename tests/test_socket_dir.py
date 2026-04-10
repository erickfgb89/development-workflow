"""Tests for orchestrate/socket_dir.py"""
from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

import orchestrate.socket_dir as sd


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_socket_dir(tmp_path, monkeypatch):
    d = tmp_path / "sockets"
    d.mkdir()
    monkeypatch.setattr("orchestrate.socket_dir.get_socket_dir", lambda: d)
    return d


# ---------------------------------------------------------------------------
# get_socket_dir tests
# ---------------------------------------------------------------------------

def test_get_socket_dir_uses_xdg_runtime_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    result = sd.get_socket_dir()
    assert result == tmp_path / "dev-workflow"
    assert result.is_dir()


def test_get_socket_dir_uses_home_fallback(tmp_path, monkeypatch):
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    result = sd.get_socket_dir()
    assert result == tmp_path / ".dev-workflow" / "sockets"
    assert result.is_dir()


def test_get_socket_dir_creates_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "nonexistent"))
    result = sd.get_socket_dir()
    assert result.is_dir()


# ---------------------------------------------------------------------------
# PID file tests
# ---------------------------------------------------------------------------

def test_pid_write_read_roundtrip(mock_socket_dir):
    path = sd.write_pid_file("mysession", pid=12345)
    assert path.exists()
    assert sd.read_pid_file("mysession") == 12345


def test_write_pid_file_uses_own_pid_when_none(mock_socket_dir):
    sd.write_pid_file("mysession")
    assert sd.read_pid_file("mysession") == os.getpid()


def test_read_pid_file_returns_none_when_missing(mock_socket_dir):
    assert sd.read_pid_file("nosuchsession") is None


def test_read_pid_file_returns_none_on_corrupt(mock_socket_dir):
    p = mock_socket_dir / "badsession.pid"
    p.write_text("not-a-number")
    assert sd.read_pid_file("badsession") is None


# ---------------------------------------------------------------------------
# is_pid_alive tests
# ---------------------------------------------------------------------------

def test_is_pid_alive_own_process():
    assert sd.is_pid_alive(os.getpid()) is True


def test_is_pid_alive_nonexistent_pid():
    # PID 99999999 is extremely unlikely to exist
    assert sd.is_pid_alive(99999999) is False


# ---------------------------------------------------------------------------
# check_session_alive tests
# ---------------------------------------------------------------------------

def test_check_session_alive_cleans_up_dead_pid(mock_socket_dir):
    # Write a PID that definitely doesn't exist
    sd.write_pid_file("dead", pid=99999999)
    sock = mock_socket_dir / "dead.sock"
    sock.touch()

    result = sd.check_session_alive("dead")

    assert result is False
    assert not sock.exists()
    assert not (mock_socket_dir / "dead.pid").exists()


def test_check_session_alive_returns_true_for_live_pid(mock_socket_dir):
    sd.write_pid_file("live", pid=os.getpid())
    result = sd.check_session_alive("live")
    assert result is True


def test_check_session_alive_no_pid_file_cleans_stale_socket(mock_socket_dir):
    sock = mock_socket_dir / "orphan.sock"
    sock.touch()

    result = sd.check_session_alive("orphan")

    assert result is False
    assert not sock.exists()


def test_check_session_alive_no_pid_no_socket(mock_socket_dir):
    result = sd.check_session_alive("ghost")
    assert result is False


# ---------------------------------------------------------------------------
# list_sessions tests
# ---------------------------------------------------------------------------

def test_list_sessions_empty_when_no_sockets(mock_socket_dir):
    assert sd.list_sessions() == []


def test_list_sessions_returns_live_sessions(mock_socket_dir):
    sock = mock_socket_dir / "alive.sock"
    sock.touch()
    sd.write_pid_file("alive", pid=os.getpid())

    sessions = sd.list_sessions()

    assert len(sessions) == 1
    assert sessions[0]["name"] == "alive"
    assert sessions[0]["pid"] == os.getpid()
    assert sessions[0]["alive"] is True
    assert sessions[0]["socket_path"] == str(sock)


def test_list_sessions_cleans_up_stale(mock_socket_dir):
    sock = mock_socket_dir / "stale.sock"
    sock.touch()
    sd.write_pid_file("stale", pid=99999999)

    sessions = sd.list_sessions()

    assert sessions == []
    assert not sock.exists()
    assert not (mock_socket_dir / "stale.pid").exists()


def test_list_sessions_includes_no_pid_alive_false(mock_socket_dir):
    """A socket with no corresponding PID file is listed as alive=False (no pid to clean)."""
    sock = mock_socket_dir / "nopid.sock"
    sock.touch()
    # No PID file written — pid is None, alive is False, no cleanup triggered

    sessions = sd.list_sessions()

    # alive is False and pid is None → not cleaned up (only cleaned when pid is not None)
    assert len(sessions) == 1
    assert sessions[0]["name"] == "nopid"
    assert sessions[0]["pid"] is None
    assert sessions[0]["alive"] is False


# ---------------------------------------------------------------------------
# rename_session_files tests
# ---------------------------------------------------------------------------

def test_rename_session_files_moves_both(mock_socket_dir):
    old_sock = mock_socket_dir / "old.sock"
    old_pid = mock_socket_dir / "old.pid"
    old_sock.touch()
    old_pid.write_text("12345")

    sd.rename_session_files("old", "new")

    assert not old_sock.exists()
    assert not old_pid.exists()
    assert (mock_socket_dir / "new.sock").exists()
    assert (mock_socket_dir / "new.pid").read_text() == "12345"


def test_rename_session_files_missing_files_no_error(mock_socket_dir):
    # Neither file exists — should not raise
    sd.rename_session_files("nonexistent", "target")
    assert not (mock_socket_dir / "target.sock").exists()
    assert not (mock_socket_dir / "target.pid").exists()


def test_rename_session_files_partial_missing(mock_socket_dir):
    """Only socket exists — rename works without error."""
    sock = mock_socket_dir / "partial.sock"
    sock.touch()

    sd.rename_session_files("partial", "renamed")

    assert not sock.exists()
    assert (mock_socket_dir / "renamed.sock").exists()
    assert not (mock_socket_dir / "renamed.pid").exists()


# ---------------------------------------------------------------------------
# set_socket_permissions tests
# ---------------------------------------------------------------------------

def test_set_socket_permissions(mock_socket_dir):
    sock = mock_socket_dir / "perms.sock"
    sock.touch()
    # Set a broad permission first
    sock.chmod(0o644)

    sd.set_socket_permissions("perms")

    mode = stat.S_IMODE(sock.stat().st_mode)
    assert mode == 0o600


# ---------------------------------------------------------------------------
# create_socket_file tests
# ---------------------------------------------------------------------------

def test_create_socket_file_removes_stale(mock_socket_dir):
    sock = mock_socket_dir / "fresh.sock"
    sock.touch()

    result = sd.create_socket_file("fresh")

    assert result == sock
    assert not result.exists()  # removed stale; caller binds to create it


def test_create_socket_file_returns_path_when_absent(mock_socket_dir):
    result = sd.create_socket_file("new")
    assert result == mock_socket_dir / "new.sock"
    assert not result.exists()  # doesn't create the file, just returns path
