# Phase 3 — The Parallel Loop

## Status: Planned (not yet implemented)

---

## Goal

Replace the sequential WU execution in `batch_manager.run_batch()` with
**true parallel execution** using `asyncio.TaskGroup` (Python 3.11+).  Up to
`MAX_BATCH_SIZE` (currently 5) WUs execute concurrently, each in its own
isolated git worktree.

---

## Key Changes

### `batch_manager.py`

Replace the `for wu in batch` loop with:

```python
async with asyncio.TaskGroup() as tg:
    tasks = {
        wu["id"]: tg.create_task(execute_wu(wu, sm, target_repo, context_md))
        for wu in batch
    }
```

Each `execute_wu()` call already has its own worktree, so filesystem isolation
is already in place.  The only shared mutation is `session_manager.write_state()`.

#### State write contention

`write_state()` is not thread-safe under concurrent `asyncio` tasks if it
reads, modifies, and writes non-atomically.  Two options:

1. **asyncio.Lock** — wrap `read_state / modify / write_state` in a shared
   `asyncio.Lock`.  Simple, low overhead.
2. **Optimistic writes** — each task holds its own `wu_state` slice and only
   writes back its own key in the `work_units` dict; use `Path.write_text`
   which is atomic on Linux (single syscall for small files).

Recommendation: `asyncio.Lock` for correctness first; optimise later if
needed.

### `orchestrator.py`

No changes needed — `run_batch()` is the only caller and its interface stays
the same.

### `gatherer.py` / `planner.py`

No changes needed — these phases are inherently sequential.

---

## Merge order

When multiple WUs complete in parallel, they must be merged sequentially
(git merge is not parallelisable).  After the `TaskGroup` completes, iterate
results in WU-ID order and call `merge_branch()` for each PASS verdict.

```python
for wu_id in sorted(tasks):
    verdict = tasks[wu_id].result()
    if verdict == "pass":
        merge_branch(wu_id, target_repo, commit_messages[wu_id])
```

---

## User Pivot with parallel execution

If any WU in the batch triggers a pivot (`needs_user_pivot = True`), halt the
entire batch immediately rather than waiting for other tasks.  Use
`asyncio.TaskGroup`'s exception propagation or a shared `asyncio.Event` as a
cancellation flag.

---

## Testing strategy

- Unit-test `select_batch()` with overlapping domains.
- Integration-test with a dummy target repo:  create 3 independent WUs, verify
  all 3 branches appear concurrently, and that state.json is consistent after
  merge.
- Use `pytest-asyncio` (already transitively available via the SDK).

---

## Estimated effort

~4 hours to implement + ~2 hours to write integration tests.
