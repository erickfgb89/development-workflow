# Workflow Overview

An AI-driven software development workflow built on the Claude Agent SDK (Python).

## Components

| Component | Implementation | Model | Role |
|-----------|---------------|-------|------|
| **Orchestrator** | Python script (no LLM) | — | State machine; `asyncio.gather()` for parallel agents; `extract_batch()` for DAG traversal |
| **context-gathering** | Claude Code agent | Opus | Interactive problem expansion, gap identification |
| **planner-sketcher** | Claude Code agent | Opus | Produces initial plan: DAG, WUs, traceability matrix |
| **planner-reshaper** | Claude Code agent | Opus | Surgically updates the plan in response to feedback |
| **implementer** | Claude Code agent | Sonnet | Writes code in isolated git worktree |
| **reviewer** | Claude Code agent | Sonnet | Six-hats review, traceability check, verdict |
| **triage** | Ad-hoc `query()` call | Sonnet | Classifies unexpected failures |

> **Batch extraction is Python, not an agent.** The `BATCH_EXTRACT` state calls `extract_batch(state)` directly — a graph traversal on `state.json`. No LLM, no latency, no cost.

## Key architectural decisions

### Orchestrator is a pure Python script
The orchestrator has no LLM attached. It is a deterministic state machine that:
- Reads and writes `state.json`
- Calls agents via the Claude Agent SDK (`claude_agent_sdk.query()`)
- Manages git worktrees for implementer isolation
- Emits state machine checkpoints to stdout
- Handles all branching logic (circuit breaker, recall, reshape triggers)

This keeps the core loop cheap, fast, and auditable. Model costs are incurred only by the agents.

### Agents are named Claude Code agents
Each agent is defined as a markdown file with YAML frontmatter (Claude Code agent format) living in an `agents/` directory. The orchestrator passes the agent name and a structured prompt to `query()`.

### Implementers run in git worktrees
Each implementer in a batch gets its own git worktree (a lightweight copy of the repo at a separate path). The orchestrator creates the worktree before calling `query()` with `cwd` pointing at the worktree path. This keeps parallel implementers from touching each other's files.

### Parallel implementers via asyncio
The orchestrator uses `asyncio.gather()` to run multiple `query()` calls concurrently, one per implementer in a batch.

### Distribution: single zipapp binary
The entire tool ships as a single `.pyz` file built with Python's `zipapp` module. Agent prompts live as `.md` files inside the zip, loaded at runtime via `importlib.resources`. This avoids the problem of lugging around a bundle of related non-project files and keeps everything in one place.

Structure inside the zip:
```
orchestrate.pyz
├── __main__.py          # entry point
├── orchestrator/
│   ├── state.py
│   ├── worktrees.py
│   └── ...
└── prompts/
    ├── context-gathering.md
    ├── planner.md
    ├── implementer.md
    └── reviewer.md
```

Agent prompts are passed as `system_prompt=` to `ClaudeAgentOptions` when calling `query()` — no external agent definition files needed. The SDK's `agents/` YAML frontmatter format is not used; prompts are injected directly by the orchestrator.

This also simplifies the "skills as context" question: there are no separate skill files to load. Any behavior that would have been a skill is simply a section of the relevant agent's prompt that the orchestrator conditionally includes in the `system_prompt` string it passes to `query()`.

### Reviewer parallelism (planned milestone)
Reviewer calls for a batch run sequentially in the initial implementation. Since reviews are independent, this will be upgraded to `asyncio.gather()` in a later milestone — same pattern as parallel implementers.

### Skills are transparent to the orchestrator
Since prompts are loaded from the zipapp at runtime, there are no separate skill files. Conditional prompt sections (break-glass handling, recall context, circuit-breaker escalation) are assembled by the orchestrator before passing to `query()`. The orchestrator never intercepts or routes in-agent behavior.

## Document index

| Document | Contents |
|----------|----------|
| [overview.md](overview.md) | This file — architecture summary |
| [orchestrator.md](orchestrator.md) | Python orchestrator: states, transitions, SDK usage, cold start |
| [agents.md](agents.md) | Agent prompts: context-gathering, planner, implementer, reviewer |
| [contracts.md](contracts.md) | All inter-agent JSON contracts |
| [state-schema.md](state-schema.md) | state.json schema and work unit file template |
