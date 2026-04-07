# development-workflow

An opinionated, agentic development workflow orchestrator. You describe a feature; it plans, implements, reviews, and merges the code — driven by a deterministic state machine that coordinates multiple Claude agents in parallel.

---

## Product design

### Concept

The orchestrator takes a natural-language description of what you want to build and drives a target git repository through a complete development cycle:

1. **Context gathering** — an Opus agent reads the repo and asks clarifying questions to produce a structured problem statement.
2. **Plan sketching** — an Opus planner writes `state.json`, decomposing the work into **work units (WUs)**: self-contained tasks with file-level solution domains and explicit `depends_on` edges.
3. **Batch extract** — a pure-Python DAG scheduler picks a batch of up to 5 independent WUs (non-overlapping domains, dependencies satisfied).
4. **Implementing** — one Sonnet agent per WU runs in an isolated git worktree (`/tmp/dev-workflow-worktrees/<wu_id>`), implementing only within its declared domain.
5. **Reviewing** — a Sonnet reviewer checks each implementer's JSON report (tests passing, no domain breach, break-glass disclosures).
6. **Batch commit** — approved worktrees are merged back in priority order (clean-domain first, break-glass last). Merge conflicts trigger a single implementer recall; a second conflict escalates to the user.
7. **Reshape** — if the reviewer sends feedback to the planner, or a DAG deadlock is detected, an Opus reshaper rewrites `state.json` and the loop restarts.
8. Repeat until all WUs are `complete`.

### State machine

```
START → INIT → CONTEXT_GATHER → PLAN_SKETCH
  ↓
BATCH_EXTRACT → IMPLEMENTING → REVIEWING → BATCH_COMMIT
       ↑_____________________________|
                   (loop)
  ↓ (all WUs complete)
COMPLETE

  ↕ (on plan-level failures)
RESHAPE
```

Checkpoints are emitted to stdout at every transition so you can follow progress in real time.

### MCP server

Agents communicate with the user through an in-process MCP server (`orchestrate/mcp_server.py`) that exposes two tools:

| Tool | Who uses it | What it does |
|---|---|---|
| `ask_question` | any agent | Pauses and prompts the user for a clarifying answer |
| `escalate` | reviewer | Requests a go/no-go decision when the reviewer is uncertain |

### Triage

Unexpected agent failures (exceptions, bad JSON, etc.) are routed to a lightweight triage agent that classifies them as `retry`, `reshape`, `user`, or `abort` and takes the appropriate action automatically.

### Cold-start recovery

Interrupted sessions (Ctrl-C, crash, OOM) are resumable with `--resume`. The recovery logic:
- WUs with uncommitted work in their worktree → sent directly to the reviewer.
- WUs with a clean/missing worktree → reset to `pending`.
- WUs in `failed` state → triaged.

### Key files

```
orchestrate/
├── __main__.py      CLI entry point (argparse + asyncio.run)
├── orchestrator.py  State machine + all async handlers
├── state.py         state.json I/O and DAG batch extraction
├── worktrees.py     git worktree create / merge / remove
├── mcp_server.py    In-process MCP server (ask_question, escalate)
└── triage.py        Unexpected-failure classification agent
prompts/
├── context-gathering.md
├── planner-sketcher.md
├── planner-reshaper.md
├── implementer.md
└── reviewer.md

```

---

## Requirements

- Python 3.14+
- `uv` (for dependency management)
- `claude` CLI authenticated (`claude` command available in PATH)
- Git 2.5+ (for worktree support)

---

## Setup

```bash
# Install dependencies
uv sync
```

---

## Installing (Globally)

You can install the orchestrator globally on your machine using `uv tool`.

```bash
# Install directly from the repository
uv tool install git+https://github.com/your-username/development-workflow.git

# (Or if you have the source cloned locally):
uv tool install .
```

---

## Running

### From source (with uv)

```bash
# New session
uv run orchestrate /path/to/target/repo

# Resume an interrupted session
uv run orchestrate --resume /path/to/target/repo
```

### From installed tool

```bash
# New session
dev-workflow /path/to/target/repo

# Resume
dev-workflow --resume /path/to/target/repo
```

The orchestrator will:
1. Prompt you for a description of what to build.
2. Check out a `feature/orchestrated` branch in the target repo.
3. Run the full plan → implement → review → merge cycle, printing checkpoint banners at each transition.
4. Ask for your input via `[Agent question]` prompts whenever an agent needs clarification.

---

## Running tests

```bash
uv run pytest
```
