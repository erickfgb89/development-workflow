"""Socket directory resolution, socket/PID file management, and stale socket detection."""
from __future__ import annotations

import os
from pathlib import Path


def get_socket_dir() -> Path:
    """Return the directory for socket and PID files.

    Resolution: $XDG_RUNTIME_DIR/dev-workflow/ if set, else ~/.dev-workflow/sockets/
    """
    xdg = os.environ.get("XDG_RUNTIME_DIR")
    if xdg:
        d = Path(xdg) / "dev-workflow"
    else:
        d = Path.home() / ".dev-workflow" / "sockets"
    d.mkdir(parents=True, exist_ok=True)
    return d


def socket_path(session_name: str) -> Path:
    return get_socket_dir() / f"{session_name}.sock"


def pid_path(session_name: str) -> Path:
    return get_socket_dir() / f"{session_name}.pid"


def write_pid_file(session_name: str, pid: int | None = None) -> Path:
    p = pid_path(session_name)
    p.write_text(str(pid or os.getpid()))
    return p


def read_pid_file(session_name: str) -> int | None:
    p = pid_path(session_name)
    if not p.exists():
        return None
    try:
        return int(p.read_text().strip())
    except (ValueError, OSError):
        return None


def is_pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists but can't signal


def check_session_alive(session_name: str) -> bool:
    """Returns True if session is running, False if stale (files cleaned up)."""
    pid = read_pid_file(session_name)
    if pid is None:
        sp = socket_path(session_name)
        if sp.exists():
            sp.unlink(missing_ok=True)
        return False
    if is_pid_alive(pid):
        return True
    cleanup_session_files(session_name)
    return False


def cleanup_session_files(session_name: str) -> None:
    socket_path(session_name).unlink(missing_ok=True)
    pid_path(session_name).unlink(missing_ok=True)


def list_sessions() -> list[dict]:
    """List sessions with socket files. Cleans up stale ones."""
    d = get_socket_dir()
    sessions = []
    for sock_file in d.glob("*.sock"):
        name = sock_file.stem
        pid = read_pid_file(name)
        alive = pid is not None and is_pid_alive(pid)
        if not alive and pid is not None:
            cleanup_session_files(name)
            continue
        sessions.append({
            "name": name,
            "pid": pid,
            "alive": alive,
            "socket_path": str(sock_file),
        })
    return sessions


def create_socket_file(session_name: str) -> Path:
    """Return socket path for binding. Remove stale socket first."""
    sp = socket_path(session_name)
    sp.unlink(missing_ok=True)
    return sp


def set_socket_permissions(session_name: str) -> None:
    """Set socket file to 0600 (owner read/write only)."""
    socket_path(session_name).chmod(0o600)


def rename_session_files(old_name: str, new_name: str) -> None:
    """Rename socket and PID files. Connected client fds are unaffected."""
    old_sock, new_sock = socket_path(old_name), socket_path(new_name)
    old_pid, new_pid = pid_path(old_name), pid_path(new_name)
    if old_sock.exists():
        old_sock.rename(new_sock)
    if old_pid.exists():
        old_pid.rename(new_pid)
