"""Shared fixtures for Overture UI / API tests.

Architecture
------------
- ``live_server`` — starts the FastAPI app in-process via ``anyio`` on a free port.
  Uses ``httpx.AsyncClient`` (ASGI transport) for API tests; Playwright ``page``
  fixture for browser tests.

- ``seeded_state`` — returns a helper that writes a state.json and context.md into
  a real on-disk session so browser tests can skip the gatherer/planner steps.

- Standalone mode is used throughout (``--web-server`` style) because that exposes
  the full lifecycle API.

All browser tests are **headless chromium** by default.  Pass ``--headed`` to pytest
to watch them run.
"""
from __future__ import annotations

import asyncio
import json
import socket
import tempfile
from collections.abc import AsyncGenerator, Generator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from playwright.sync_api import Page, sync_playwright


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _make_state(
    wus: dict[str, Any] | None = None,
    batch_number: int = 1,
    status: str | None = None,
) -> dict[str, Any]:
    """Return a minimal valid state.json dict."""
    state: dict[str, Any] = {
        "work_units": wus or {},
        "batch_number": batch_number,
    }
    if status:
        state["status"] = status
    return state


def _wu(
    wu_id: str,
    title: str = "Do something",
    status: str = "pending",
    deps: list[str] | None = None,
    failure_count: int = 0,
    needs_user_pivot: bool = False,
    acceptance_criteria: list[str] | None = None,
    solution_domain: list[str] | None = None,
    implementer_report: dict | None = None,
    last_review: dict | None = None,
    completed_at: str | None = None,
) -> dict[str, Any]:
    """Build a minimal work-unit dict."""
    wu: dict[str, Any] = {
        "id": wu_id,
        "title": title,
        "description": f"Description of {wu_id}",
        "status": status,
        "dependencies": deps or [],
        "acceptance_criteria": acceptance_criteria or [f"AC for {wu_id}"],
        "solution_domain": solution_domain or ["src/"],
    }
    if failure_count:
        wu["failure_count"] = failure_count
    if needs_user_pivot:
        wu["needs_user_pivot"] = True
    if implementer_report:
        wu["implementer_report"] = implementer_report
    if last_review:
        wu["last_review"] = last_review
    if completed_at:
        wu["completed_at"] = completed_at
    return wu


# ---------------------------------------------------------------------------
# Data dir / session fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_data_dir(tmp_path: Path) -> Path:
    """A temporary directory used as the web-server data_dir."""
    d = tmp_path / "sessions"
    d.mkdir()
    return d


@pytest.fixture
def tmp_repo(tmp_path: Path) -> Path:
    """A minimal git repo that the session manager accepts."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "README.md").write_text("# test repo\n")
    return repo


# ---------------------------------------------------------------------------
# ASGI app (no real uvicorn, just the FastAPI app object)
# ---------------------------------------------------------------------------

@pytest.fixture
def app(tmp_data_dir: Path):
    """Build a standalone FastAPI app backed by a temp data dir."""
    # Import lazily so tests that don't need it aren't slowed down.
    from overture.web_ui.server import _build_app
    return _build_app(None, standalone=True, data_dir=tmp_data_dir)


@pytest_asyncio.fixture
async def client(app) -> AsyncGenerator[AsyncClient, None]:
    """Async HTTPX client wired directly to the ASGI app (no network)."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


# ---------------------------------------------------------------------------
# Session helpers used by both API and browser tests
# ---------------------------------------------------------------------------

@pytest.fixture
def make_session(client, tmp_repo):
    """
    Async callable: ``session_id = await make_session()``.

    Creates a new session via the API and returns the session_id string.
    Accepts an optional repo path override.
    """
    async def _make(repo: Path | None = None) -> str:
        r = await client.post("/api/session/new", json={"repo_path": str(repo or tmp_repo)})
        assert r.status_code == 200, r.text
        data = r.json()
        assert data.get("ok"), data
        return data["session_id"]
    return _make


@pytest.fixture
def seed_session(tmp_data_dir):
    """
    Synchronous helper: writes state.json (and optionally context.md) directly
    to disk for a session that already exists.

    Usage::

        session_id = await make_session()
        seed_session(session_id, state=_make_state({...}), context="my context")
    """
    def _seed(
        session_id: str,
        state: dict[str, Any] | None = None,
        context: str | None = None,
    ) -> None:
        session_dir = tmp_data_dir / session_id
        assert session_dir.exists(), f"Session dir not found: {session_dir}"
        if state is not None:
            (session_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")
        if context is not None:
            (session_dir / "context.md").write_text(context, encoding="utf-8")
    return _seed


# ---------------------------------------------------------------------------
# Live HTTP server for browser (Playwright) tests
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def _browser():
    """Shared Playwright browser instance for the whole test session."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        yield browser
        browser.close()


class LiveServer:
    """Runs the FastAPI app in a background asyncio thread so Playwright can hit it."""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.port = _free_port()
        self.base_url = f"http://127.0.0.1:{self.port}"
        self._thread: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_event: asyncio.Event | None = None
        self._app: Any = None

    def start(self) -> None:
        import threading
        started = threading.Event()

        def _run() -> None:
            import uvicorn
            from overture.web_ui.server import _build_app

            self._app = _build_app(None, standalone=True, data_dir=self.data_dir)
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._stop_event = asyncio.Event()

            config = uvicorn.Config(
                self._app,
                host="127.0.0.1",
                port=self.port,
                log_level="error",
                loop="asyncio",
            )
            server = uvicorn.Server(config)

            async def _serve():
                # Start broadcaster coroutine alongside server
                server_task = self._loop.create_task(server.serve())
                broadcaster_task = self._loop.create_task(self._app.state.broadcaster())
                started.set()
                await self._stop_event.wait()
                server.should_exit = True
                await server_task
                broadcaster_task.cancel()

            self._loop.run_until_complete(_serve())

        import threading
        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()
        started.wait(timeout=10)

    def stop(self) -> None:
        if self._loop and self._stop_event:
            self._loop.call_soon_threadsafe(self._stop_event.set)
        if self._thread:
            self._thread.join(timeout=5)

    def api(self, path: str, method: str = "GET", json_body: Any = None) -> Any:
        """Synchronous HTTP helper for use inside Playwright tests."""
        import requests
        url = self.base_url + path
        if method == "GET":
            return requests.get(url, params=json_body or {})
        return requests.post(url, json=json_body or {})


@pytest.fixture(scope="function")
def live_server(tmp_path) -> Generator[LiveServer, None, None]:
    """
    A real uvicorn server in a background thread.
    Each test function gets its own data_dir (function scope) so sessions
    don't bleed between tests.
    """
    data_dir = tmp_path / "sessions"
    data_dir.mkdir()
    server = LiveServer(data_dir)
    server.start()
    yield server
    server.stop()


@pytest.fixture(scope="function")
def page(live_server: LiveServer, _browser) -> Generator[Page, None, None]:
    """
    A Playwright page pre-navigated to the live server root.
    Alpine.js is CDN-loaded, so we wait until #app has x-data resolved.
    """
    context = _browser.new_context(base_url=live_server.base_url)
    p = context.new_page()
    p.goto(live_server.base_url)
    # Wait for Alpine to init (landing page visible)
    p.wait_for_selector("#page-landing", state="visible", timeout=10_000)
    yield p
    context.close()


# ---------------------------------------------------------------------------
# Browser-test helpers (used across multiple test modules)
# ---------------------------------------------------------------------------

def create_session_via_api(server: LiveServer, repo_path: str) -> str:
    """Create a session via REST and return the session_id."""
    r = server.api("/api/session/new", "POST", {"repo_path": repo_path})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data.get("ok"), data
    return data["session_id"]


def seed_state(server: LiveServer, session_id: str, state: dict, context: str = "") -> None:
    """Write state.json and context.md directly for a session."""
    session_dir = server.data_dir / session_id
    assert session_dir.exists(), f"session dir missing: {session_dir}"
    (session_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")
    if context:
        (session_dir / "context.md").write_text(context, encoding="utf-8")


def navigate_to_session(page: Page, server: LiveServer, session_id: str) -> None:
    """
    Load a session into the UI by calling /api/session/load through the browser's
    fetch (simulates resumeSession) — achieved by injecting a JS call.
    """
    page.evaluate(f"""
        async () => {{
            const r = await fetch('/api/session/load', {{
                method: 'POST',
                headers: {{'Content-Type': 'application/json'}},
                body: JSON.stringify({{session_id: '{session_id}'}})
            }});
            const data = await r.json();
            if (!data.ok) throw new Error(data.error);
            // Trigger Alpine switchSession
            const alpine = document.querySelector('#app').__x;
            if (alpine) {{
                await alpine.$data.switchSession(data.session_id);
            }} else {{
                // Alpine 3 uses _x_dataStack
                const comp = document.querySelector('#app')._x_dataStack?.[0];
                if (comp) await comp.switchSession(data.session_id);
            }}
        }}
    """)


def get_alpine_data(page: Page) -> dict:
    """Extract the Alpine component's current data snapshot."""
    return page.evaluate("""
        () => {
            const el = document.querySelector('#app');
            const comp = el._x_dataStack?.[0];
            if (!comp) return {};
            return JSON.parse(JSON.stringify({
                currentPage: comp.currentPage,
                activeSession: comp.activeSession,
                wsStatus: comp.wsStatus,
                sessions: comp.sessions,
                state: comp.state,
                contextContent: comp.contextContent,
                contextLocked: comp.contextLocked,
            }));
        }
    """)
