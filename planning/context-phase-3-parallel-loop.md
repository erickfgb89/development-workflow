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
- **`gatherer.py` / `planner.py`**: no changes needed — inherently sequential phases.
- **Tests**: no test files exist yet (`tests/` directory is empty per `pyproject.toml` config).
- **Dependencies**: `pytest-asyncio` is not explicitly listed in `pyproject.toml`; needs to be added.

---

## Constraints

1. **Do not refactor `execute_wu()`'s merge behaviour** — it continues to call `merge_branch()` internally on a PASS verdict. Post-TaskGroup sequential merge (as sketched in the phase-3 plan doc) is not being implemented.
2. **Zero domain overlap per batch is enforced by `select_batch()`** — file-level git conflicts between concurrent WUs are not expected; the merge lock guards git-level concurrency only.
3. **No changes to `orchestrator.py`, `planner.py`, or `gatherer.py`** — `run_batch()`'s external interface (signature, return type) stays identical.
4. **Pivot / cancellation**: when a WU sets `needs_user_pivot = True`, do **not** cancel sibling tasks. Allow all tasks in the `TaskGroup` to run to completion; the orchestrator's outer loop handles halting.
5. **`asyncio.TaskGroup` exception propagation**: if a task raises an unhandled exception, `TaskGroup` will cancel siblings. The existing `except Exception` guard in `execute_wu()` must continue to swallow unexpected errors and return `"fail"` so the group never propagates an uncaught exception from a single WU.

---

## Acceptance Criteria

1. **Parallel execution**: `run_batch()` uses `asyncio.TaskGroup` to launch all WUs in the selected batch concurrently. The sequential `for wu in batch` loop is removed.

2. **Merge mutex** (`merge_lock: asyncio.Lock`):
   - Created once in `run_batch()` and passed into every `execute_wu()` call.
   - Wraps **only** the `merge_branch()` call inside `execute_wu()` — no other code holds this lock.
   - Ensures only one git merge/commit runs at a time against `target_repo`.

3. **State mutex** (`state_lock: asyncio.Lock`):
   - Created once in `run_batch()` and passed into every `execute_wu()` call.
   - Every location in `execute_wu()` that calls `write_state()` follows this exact pattern:
     ```
     async with state_lock:
         state = session_manager.read_state()   # fresh read inside the lock
         # apply this WU's changes to state
         session_manager.write_state(state)
     ```
   - Applies to all three write sites: initial `"in_progress"` update, final verdict update, and both `except` handlers (`AgentHaltError` and generic `Exception`).

4. **`execute_wu()` signature** is updated to accept `merge_lock` and `state_lock` as required parameters. The return type (`str` verdict) is unchanged.

5. **Pivot detection**: after the `TaskGroup` completes, `run_batch()` checks the summary counts (pass / fail / reshape) and returns them as before. It does **not** break mid-batch on pivot detection — that responsibility remains with the orchestrator.

6. **Summary dict** returned by `run_batch()` remains identical in shape: `{"ready": int, "passed": int, "failed": int, "reshaped": int}`.

7. **Tests — unit**:
   - `test_select_batch_no_overlap`: verifies that `select_batch()` returns WUs with non-overlapping `solution_domain` sets and respects `MAX_BATCH_SIZE`.
   - `test_select_batch_with_overlap`: verifies that WUs sharing a domain file are excluded from the same batch.

8. **Tests — integration** (using `pytest-asyncio` + a temporary git repo fixture):
   - Create a real git repo with 3 independent WUs (non-overlapping domains).
   - Mock `execute_wu()` or stub the agent calls so tasks complete near-simultaneously.
   - Assert that all 3 WU branches appear in the repo after the batch.
   - Assert that `state.json` is consistent after the run (no clobbered WU entries).

9. **`pytest-asyncio`** is added as a dev/test dependency in `pyproject.toml`.

---

## Notes

- The phase-3 plan doc (`planning/phase-3-parallel-loop.md`) contains a post-`TaskGroup` sequential merge loop. **This is not being implemented.** The merge lock is the chosen solution instead.
- `asyncio.TaskGroup` (Python 3.11+) is preferred over `asyncio.gather()` because it provides structured concurrency: if a task raises, sibling tasks are cancelled automatically. The existing `except Exception` guard in `execute_wu()` ensures tasks never raise, so this cancellation path is a safety net only.
- The state lock's "re-read inside the lock" pattern is deliberate: it ensures the writer always starts from the latest on-disk state, avoiding the stale-read race that exists in the current sequential code between the initial `read_state()` and the final `write_state()`.
- `write_wu()` (per-WU sidecar file) does not need locking — each task writes to its own `wus/{wu_id}.json` file with no contention.
- If `pytest-asyncio` is already transitively available via `claude-agent-sdk`, the explicit addition to `pyproject.toml` still improves clarity and version-pins the dependency for test stability.
