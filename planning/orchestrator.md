# Orchestrator

The orchestrator is a Python script with no LLM. It is a deterministic state machine that drives the workflow by calling agents via the Claude Agent SDK.

## Batch extraction

Batch extraction is implemented directly in Python — no LLM call required. The logic is fully deterministic given the state file.

```python
import json
from pathlib import Path

def extract_batch(state: dict, max_batch: int = 5) -> list[str]:
    """
    Select up to max_batch WUs that are ready to run in parallel.

    A WU is eligible if:
      1. Its status is 'pending'
      2. All its depends_on WUs have status 'complete'

    Among eligible WUs, select a maximal non-overlapping set by solution domain.
    Prefer WUs that unblock the most downstream work (highest transitive block count).
    """
    wus = state["work_units"]

    # 1. Find eligible WUs
    eligible = [
        wu_id for wu_id, wu in wus.items()
        if wu["status"] == "pending"
        and all(wus[dep]["status"] == "complete" for dep in wu["depends_on"])
    ]

    # 2. Rank by unblocking power (transitive count of WUs this one blocks)
    def transitive_block_count(wu_id: str, visited: set = None) -> int:
        if visited is None:
            visited = set()
        if wu_id in visited:
            return 0
        visited.add(wu_id)
        return sum(1 + transitive_block_count(b, visited) for b in wus[wu_id]["blocks"])

    eligible.sort(key=lambda wu_id: transitive_block_count(wu_id), reverse=True)

    # 3. Greedy selection: add WUs whose domain doesn't overlap with already-selected WUs
    selected = []
    claimed_files: set[str] = set()
    for wu_id in eligible:
        domain = set(wus[wu_id]["solution_domain"])
        if not domain & claimed_files:
            selected.append(wu_id)
            claimed_files |= domain
            if len(selected) >= max_batch:
                break

    return selected
```

If `extract_batch()` returns an empty list and there are still `pending` WUs, those WUs are blocked by incomplete dependencies — a state inconsistency. The orchestrator emits a clear error and calls the triage agent.

## SDK usage pattern

```python
import asyncio
import anyio
from claude_agent_sdk import query, ClaudeAgentOptions, AgentDefinition, ResultMessage

# Call a named agent with a structured prompt
async def call_agent(agent_name: str, prompt: str, cwd: str | None = None) -> dict:
    """Call a named agent and return its parsed JSON result."""
    options = ClaudeAgentOptions(
        cwd=cwd or REPO_ROOT,
        permission_mode="bypassPermissions",
        allow_dangerously_skip_permissions=True,
        setting_sources=["project"],  # load CLAUDE.md and project agents
    )
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, ResultMessage):
            return parse_json_result(message.result)
    raise RuntimeError(f"Agent {agent_name} did not return a result")

# Run multiple implementers in parallel, each in its own worktree
async def run_batch(wu_ids: list[str]) -> list[dict]:
    """Run implementers in parallel worktrees."""
    tasks = []
    worktree_paths = []
    for wu_id in wu_ids:
        worktree_path = create_worktree(wu_id)  # git worktree add
        worktree_paths.append(worktree_path)
        prompt = build_implementer_prompt(wu_id)
        tasks.append(call_agent("implementer", prompt, cwd=worktree_path))

    results = await asyncio.gather(*tasks, return_exceptions=True)
    return list(zip(wu_ids, worktree_paths, results))
```

## Worktree management

The orchestrator manages git worktrees externally. The Agent SDK has no built-in worktree option — isolation is achieved by pointing each `query()` call at a different `cwd`.

```python
import subprocess
import os

WORKTREE_BASE = "/tmp/dev-workflow-worktrees"

def create_worktree(wu_id: str) -> str:
    """Create an isolated git worktree for a work unit."""
    branch = f"wu/{wu_id}"
    path = os.path.join(WORKTREE_BASE, wu_id)
    subprocess.run(
        ["git", "worktree", "add", "-b", branch, path],
        cwd=REPO_ROOT, check=True
    )
    return path

def merge_worktree(wu_id: str, worktree_path: str) -> None:
    """Merge an approved worktree back to the feature branch."""
    subprocess.run(
        ["git", "merge", f"wu/{wu_id}", "--no-ff", "-m", f"Complete {wu_id}"],
        cwd=REPO_ROOT, check=True
    )
    subprocess.run(["git", "worktree", "remove", worktree_path], check=True)

def remove_worktree(wu_id: str, worktree_path: str) -> None:
    """Remove an abandoned worktree (failed or rejected work)."""
    subprocess.run(["git", "worktree", "remove", "--force", worktree_path], check=True)
    subprocess.run(["git", "branch", "-D", f"wu/{wu_id}"], cwd=REPO_ROOT, check=True)
```

## State machine

### States

```
INIT
CONTEXT_GATHER
PLAN_SKETCH
BATCH_EXTRACT
IMPLEMENTING
REVIEWING
RECALL
BATCH_COMMIT
RESHAPE
COMPLETE
```

User interaction (questions, escalations) happens transparently inside whichever state is currently running, via the workflow MCP server. It is invisible to the state machine.

### State diagram

```
┌─────────────────┐
│   INIT           │──▶ create branch/PR
└────────┬────────┘
         ▼
┌─────────────────┐
│ CONTEXT_GATHER   │──▶ query(context-gathering, opus)
└────────┬────────┘
         ▼
┌─────────────────┐
│ PLAN_SKETCH      │  (sketcher asks user questions via MCP tool mid-run)
└────────┬────────┘
         ▼
┌─────────────────┐
│ BATCH_EXTRACT    │──▶ extract_batch(state)  ← pure Python, no LLM
└────────┬────────┘
         ▼
┌─────────────────┐
│ IMPLEMENTING     │──▶ asyncio.gather(*[query(implementer, cwd=worktree) for wu in batch])
└────────┬────────┘
         ▼
┌─────────────────┐     ┌──────────────────┐
│ REVIEWING        │──▶──│ RECALL            │ (back to IMPLEMENTING for that WU)
└────────┬────────┘     └──────────────────┘
         ▼                     │ (circuit breaker: recall_count >= 3)
┌─────────────────┐            ▼
│ BATCH_COMMIT     │     ┌──────────────────┐
└────────┬────────┘     │ RESHAPE           │──▶ query(planner-reshaper, opus)
         │               └────────┬─────────┘
         ▼                        │
   ┌───────────┐                  │
   │ more WUs? │──yes──▶ BATCH_EXTRACT ◀──┘
   └─────┬─────┘
         │ no
         ▼
┌─────────────────┐
│ COMPLETE         │──▶ print summary, offer push/merge
└─────────────────┘
```

### Transition table

| From | To | Trigger |
|------|----|---------|
| INIT | CONTEXT_GATHER | Branch created or skipped by user |
| CONTEXT_GATHER | PLAN_SKETCH | context-gathering writes problem statement to disk |
| PLAN_SKETCH | BATCH_EXTRACT | planner-sketcher returns `plan_complete` |
| BATCH_EXTRACT | IMPLEMENTING | `extract_batch()` returns ≥1 WU IDs |
| BATCH_EXTRACT | COMPLETE | `extract_batch()` returns empty list and no pending WUs remain |
| IMPLEMENTING | REVIEWING | All implementers in batch have returned reports |
| REVIEWING | BATCH_COMMIT | All WUs in batch approved |
| REVIEWING | RECALL | Reviewer rejects; `feedback_target == "implementer"` |
| RECALL | IMPLEMENTING | `recall_count < 3`: re-call implementer with reviewer feedback |
| RECALL | RESHAPE | `recall_count >= 3`: circuit breaker; call planner-reshaper |
| REVIEWING | RESHAPE | Reviewer rejects; `feedback_target == "planner"` |
| IMPLEMENTING | RESHAPE | Implementer returns `needs_feedback` targeting planner |
| RESHAPE | BATCH_EXTRACT | planner-reshaper returns `reshape_complete` |
| BATCH_COMMIT | BATCH_EXTRACT | Pending WUs remain in state |
| BATCH_COMMIT | COMPLETE | No pending or in-progress WUs remain |

User questions and escalations (via `ask_question` / `escalate` MCP tools) are transparent pauses within a state — they do not trigger state transitions.

## Checkpoints

The orchestrator emits a checkpoint to stdout at every state transition. Format is a fenced code block for monospace rendering and grep-ability:

```
╔══════════════════════════════════════════════════════╗
║ STATE MACHINE CHECKPOINT                             ║
║ state:      BATCH_EXTRACT                            ║
║ transition: BATCH_COMMIT → BATCH_EXTRACT             ║
║ batch:      3  wus: WU-008, WU-009                   ║
║ progress:   7 / 12 WUs complete                      ║
║ timestamp:  2026-04-03T14:22:00Z                     ║
╚══════════════════════════════════════════════════════╝
```

Required fields: `state`, `transition`, `progress`, `timestamp`. Optional: `batch`, `wus`, `notes`.

## Unexpected failure handling

Because the orchestrator has no LLM, it cannot reason about unexpected agent failures. The fallback strategy for any failure not covered by the transition table:

1. **Log the raw output** — write the agent's response (or exception) to a failure log file
2. **Call the triage agent** — a vanilla sonnet agent given the failure context, whose only job is to classify the failure and return one of: `{ "action": "retry" | "reshape" | "user" | "abort", "message": "..." }`
3. **Act on classification**:
   - `retry`: re-call the failed agent once more with no changes
   - `reshape`: call planner mode 2 with the failure context
   - `user`: surface the failure and wait for user input
   - `abort`: write final checkpoint, print summary of completed work, exit

```python
async def handle_unexpected_failure(context: dict) -> str:
    """Triage an unexpected agent failure. Returns action string."""
    prompt = f"""
You are a triage agent. An unexpected failure occurred in the development workflow.
Failure context:
{json.dumps(context, indent=2)}

Classify this failure and return ONLY a JSON object:
{{"action": "retry|reshape|user|abort", "message": "brief explanation"}}
"""
    options = ClaudeAgentOptions(
        model="claude-sonnet-4-6",
        permission_mode="bypassPermissions",
        allow_dangerously_skip_permissions=True,
        allowed_tools=[],  # triage agent needs no tools
        max_turns=3,
    )
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, ResultMessage):
            return parse_json_result(message.result)
    return {"action": "user", "message": "Triage agent failed to respond"}
```

## Cold start recovery

If the session is closed, the user invokes the orchestrator with the path to an existing working directory:

```bash
python orchestrate.py --resume /path/to/project
```

Recovery procedure:

1. Read `state.json` to determine `current_phase` and WU statuses
2. Identify WUs marked `complete` (already done)
3. Find WUs marked `in_progress` — these were interrupted:
   - Check if their worktree exists and has uncommitted changes
   - If changes exist: call the reviewer on the partial work
   - If no changes: reset to `pending` and re-queue
4. Find WUs marked `failed` — read their failure log and call triage
5. Emit a recovery checkpoint summarizing what was found
6. Resume from `BATCH_EXTRACT`

## Merge conflict handling

When merging parallel worktrees back to the feature branch, conflicts are possible. The orchestrator merges worktrees one at a time in a defined priority order, not all at once.

### Priority order for merging a batch
1. WUs whose implementer **did not break its solution domain** are merged first
2. WUs whose implementer **did break its domain** (break-glass) are merged after
3. Within each group, order matches the batch list from the planner

This priority rule means that if a conflict arises, the lower-priority WU (the domain-breaker, or last in the list) is the one that must be recalled — not the one already cleanly merged.

### Conflict resolution procedure
1. Attempt `git merge --no-ff` for the next WU in priority order
2. If merge **succeeds**: record the merge, continue to next WU in the batch
3. If merge **fails** (exit code non-zero):
   - Abort the failed merge (`git merge --abort`)
   - Do not discard the conflicting worktree
   - Recall the implementer of the conflicting WU with:
     - Its original WU context
     - The reviewer's approval (it was already approved — the conflict is not a quality issue)
     - Explicit context: "A merge conflict occurred with WU-NNN which was merged first. Retry your implementation. You may break your solution domain again if that is what caused the conflict — report it as break-glass."
   - The recalled implementer works in its existing worktree (no new worktree needed)
   - After the recall completes, the reviewer re-reviews, then merge is re-attempted
   - If the conflict recurs a second time, escalate to the user

## User interaction via the MCP server

Agents request user interaction by calling tools on the workflow MCP server (`ask_question`, `escalate`). The MCP server's tool handlers call `input()` in the orchestrator process, blocking until the user responds. The agent receives the answer as a normal tool result and continues.

This approach is used instead of `AskUserQuestion` (a Claude Code built-in that requires VS Code or the desktop app) because the orchestrator runs as a standalone Python script.

See [mcp-server.md](mcp-server.md) for the server definition, tool schemas, and how the server is passed to agents.
