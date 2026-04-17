## Goal

Replace the sequential `for wu in batch` loop in `batch_manager.run_batch()` with true parallel execution using `asyncio.TaskGroup`, so that up to `MAX_BATCH_SIZE` Work Units execute concurrently, each in its own isolated git worktree.

---

## Current State

- **Runtime**: Python 3.14 — `asyncio.TaskGroup` is available without backports.
- **Primary file**: `src/overture/batch_manager.py`
  - `run_batch()` (lines 316–368) drives WU execution with a sequential `for wu in batch` loop.
  - `execute_wu()` (lines 190–264) is already `async` and manages its own worktree lifecycle (create → implement → review → merge → remove).
  - `merge_branch()` (lines 176–183) runs `git merge --squash` + `git commit` against the shared `target_repo` working directory — not safe for concurrent calls.
  - All `write_state()` calls in `execute_wu()` follow a read-at-top / modify-local-ref / write-at-bottom pattern. With concurrent tasks, the local `state` object becomes stale after the first `await`, creating a clobber race on `state.json`.
- **`session_manager.write_state()`** uses `Path.write_text()` (atomic on Linux for small files) but the surrounding read-modify-write sequence in `execute_wu()` is not atomic.
- **`orchestrator.py`**: calls `run_batch()` in a `while True` loop; already checks for `needs_user_pivot` and stops when only failed/blocked WUs remain. No changes needed here.
- **Tests**: no test files exist yet — `pytest-asyncio` is not yet in `pyproject.toml`.

---

## Constraints

1. **Do not refactor `execute_wu()`'s merge behaviour** — it continues to call `merge_branch()` internally on PASS. The post-TaskGroup sequential merge block from the plan doc is **not** being implemented.
2. **No changes to `orchestrator.py`, `planner.py`, or `gatherer.py`** — `run_batch()`'s external signature and return type stay identical.
3. **Pivot / cancellation**: do **not** cancel sibling tasks when a WU sets `needs_user_pivot`. Let all tasks run to completion; the orchestrator handles halting.
4. The existing `except Exception` guard in `execute_wu()` must continue to swallow unexpected errors and return `"fail"` — `TaskGroup` must never see an uncaught exception from a single WU.

---

## Acceptance Criteria

1. `run_batch()` launches all selected WUs concurrently via `asyncio.TaskGroup`; the sequential loop is removed.
2. A **`merge_lock: asyncio.Lock`** is created in `run_batch()`, passed to every `execute_wu()` call, and wraps **only** the `merge_branch()` call — nothing else.
3. A **`state_lock: asyncio.Lock`** is created in `run_batch()`, passed to every `execute_wu()` call, and wraps every `write_state()` site with the pattern: *acquire → re-read from disk → apply this WU's changes → write → release*.
4. `execute_wu()` signature gains `merge_lock` and `state_lock` parameters; return type (`str` verdict) is unchanged.
5. The summary dict shape `{"ready", "passed", "failed", "reshaped"}` returned by `run_batch()` is unchanged.
6. **Unit tests**: `test_select_batch_no_overlap` and `test_select_batch_with_overlap` cover `select_batch()` logic.
7. **Integration test**: a temporary git repo fixture with 3 stubbed WUs verifies all 3 branches appear post-batch and `state.json` is consistent (no clobbered WU entries).
8. `pytest-asyncio` is added as a test dependency in `pyproject.toml`.

---

## Notes

- The state lock's "re-read inside the lock" pattern is the key correctness guarantee: it ensures the writer always starts from the latest on-disk state, not the stale snapshot captured at the top of `execute_wu()`.
- `write_wu()` (per-WU sidecar files in `wus/{wu_id}.json`) requires **no** locking — each task writes only its own file.
- Context doc written to: `planning/context-phase-3-parallel-loop.md`