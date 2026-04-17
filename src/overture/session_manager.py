"""Session lifecycle management for Overture.

Handles:
- UUID-based session directory creation inside the target repo's .overture/
- Renaming UUID folders to human-readable slugs once context is gathered
- Writing/reading session state files (context.md, state.json, wus/)
"""

import json
import re
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any


class SessionManager:
    """Manages the on-disk lifecycle of a single Overture session."""

    SESSIONS_DIR = ".overture/sessions"

    def __init__(self, target_repo: Path) -> None:
        self.target_repo = target_repo.resolve()
        self._session_id: str | None = None
        self._session_dir: Path | None = None

    # ------------------------------------------------------------------
    # Creation
    # ------------------------------------------------------------------

    def new_session(self) -> str:
        """Create a fresh UUID-named session directory and return its ID."""
        self._session_id = str(uuid.uuid4())
        self._session_dir = self._sessions_root / self._session_id
        self._session_dir.mkdir(parents=True, exist_ok=True)
        (self._session_dir / "wus").mkdir(exist_ok=True)
        (self._session_dir / "worktrees").mkdir(exist_ok=True)
        return self._session_id

    # ------------------------------------------------------------------
    # Slug rename
    # ------------------------------------------------------------------

    def rename_to_slug(self, title: str) -> str:
        """Rename the UUID session folder to a readable slug derived from *title*.

        Returns the new slug.  The session_id property continues to return the
        slug after this call so callers don't need to update references.
        """
        slug = self._slugify(title)
        new_dir = self._sessions_root / slug
        # Avoid collisions by appending a short date suffix when needed.
        if new_dir.exists() and new_dir != self._session_dir:
            suffix = datetime.now().strftime("%Y%m%d-%H%M%S")
            slug = f"{slug}-{suffix}"
            new_dir = self._sessions_root / slug

        if self._session_dir is None:
            raise RuntimeError("No active session. Call new_session() first.")

        shutil.move(str(self._session_dir), str(new_dir))
        self._session_dir = new_dir
        self._session_id = slug
        return slug

    # ------------------------------------------------------------------
    # File helpers
    # ------------------------------------------------------------------

    def write_context(self, content: str) -> Path:
        path = self._require_dir() / "context.md"
        path.write_text(content, encoding="utf-8")
        return path

    def read_context(self) -> str:
        path = self._require_dir() / "context.md"
        if not path.exists():
            raise FileNotFoundError(f"context.md not found in {self._require_dir()}")
        return path.read_text(encoding="utf-8")

    def write_plan(self, content: str) -> Path:
        path = self._require_dir() / "plan.md"
        path.write_text(content, encoding="utf-8")
        return path

    def write_state(self, state: dict[str, Any]) -> Path:
        path = self._require_dir() / "state.json"
        path.write_text(json.dumps(state, indent=2), encoding="utf-8")
        return path

    def read_state(self) -> dict[str, Any]:
        path = self._require_dir() / "state.json"
        if not path.exists():
            raise FileNotFoundError(f"state.json not found in {self._require_dir()}")
        return json.loads(path.read_text(encoding="utf-8"))

    def write_wu(self, wu_id: str, data: dict[str, Any]) -> Path:
        path = self._require_dir() / "wus" / f"{wu_id}.json"
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return path

    def read_wu(self, wu_id: str) -> dict[str, Any]:
        path = self._require_dir() / "wus" / f"{wu_id}.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def list_wus(self) -> list[str]:
        """Return sorted list of WU IDs present on disk."""
        wus_dir = self._require_dir() / "wus"
        return sorted(p.stem for p in wus_dir.glob("WU-*.json"))

    def worktree_path(self, wu_id: str) -> Path:
        return self._require_dir() / "worktrees" / wu_id

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def session_id(self) -> str:
        if self._session_id is None:
            raise RuntimeError("No active session.")
        return self._session_id

    @property
    def session_dir(self) -> Path:
        return self._require_dir()

    # ------------------------------------------------------------------
    # Persistence helpers (load existing session)
    # ------------------------------------------------------------------

    def load_session(self, session_id: str) -> None:
        """Load an existing session by ID or slug."""
        candidate = self._sessions_root / session_id
        if not candidate.exists():
            raise FileNotFoundError(f"Session '{session_id}' not found under {self._sessions_root}")
        self._session_id = session_id
        self._session_dir = candidate

    def list_sessions(self) -> list[str]:
        """Return all session IDs/slugs present in the sessions root."""
        root = self._sessions_root
        if not root.exists():
            return []
        return sorted(p.name for p in root.iterdir() if p.is_dir())

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @property
    def _sessions_root(self) -> Path:
        return self.target_repo / self.SESSIONS_DIR

    def _require_dir(self) -> Path:
        if self._session_dir is None:
            raise RuntimeError("No active session. Call new_session() or load_session() first.")
        return self._session_dir

    @staticmethod
    def _slugify(text: str) -> str:
        text = text.lower().strip()
        text = re.sub(r"[^\w\s-]", "", text)
        text = re.sub(r"[\s_]+", "-", text)
        text = re.sub(r"-+", "-", text)
        return text[:60].strip("-") or "session"
