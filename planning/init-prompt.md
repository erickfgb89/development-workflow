# AI-Driven Development Workflow — Planning Index

This directory contains the planning documentation for an AI-driven software development workflow built on the **Claude Agent SDK** (Python).

The orchestrator is a **pure Python script with no LLM** — a deterministic state machine that calls named Claude Code agents via `claude_agent_sdk.query()`. All reasoning is delegated to the agents.

## Document index

| Document | Contents |
|----------|----------|
| [overview.md](overview.md) | Architecture summary, key decisions, component table |
| [orchestrator.md](orchestrator.md) | Python orchestrator: SDK usage, batch extraction, worktrees, states, transitions, cold start |
| [mcp-server.md](mcp-server.md) | Workflow MCP server: `ask_question` and `escalate` tools, user interaction pattern |
| [agents.md](agents.md) | Agent prompt structure and all agent definitions |
| [contracts.md](contracts.md) | All inter-agent JSON contracts |
| [state-schema.md](state-schema.md) | state.json schema, WU file template, plan.md structure |
| [future-features/](future-features/) | Backlog of planned future enhancements |

## Components

| Component | Implementation | Model | Role |
|-----------|---------------|-------|------|
| **Orchestrator** | `orchestrate.py` (no LLM) | — | State machine; `asyncio.gather()` for parallel agents; `extract_batch()` for DAG |
| **Workflow MCP server** | `orchestrator/mcp_server.py` (no LLM) | — | Provides `ask_question` and `escalate` tools; handles all user interaction |
| **context-gathering** | Prompt string injected via `system_prompt=` | Opus | Interactive problem expansion, gap identification |
| **planner-sketcher** | Prompt string injected via `system_prompt=` | Opus | Initial plan: DAG, WUs, traceability matrix |
| **planner-reshaper** | Prompt string injected via `system_prompt=` | Opus | Surgical plan updates from feedback |
| **implementer** | Prompt string injected via `system_prompt=` | Sonnet | Writes code in isolated git worktree |
| **reviewer** | Prompt string injected via `system_prompt=` | Sonnet | Six-hats review, traceability check, verdict |
| **triage** | Inline prompt in `handle_unexpected_failure()` | Sonnet | Classifies unexpected failures |

## Resolved decisions

Previously open questions, now closed:

1. **Worktree merge conflicts** — Resolved. The orchestrator merges worktrees in priority order (non-domain-breakers first; if both broke domain, first in batch list wins). When a conflict occurs, the lower-priority WU's implementer is recalled in its existing worktree with explicit context that a conflict occurred and break-glass is permitted again. Two consecutive conflict failures escalate to the user. See [orchestrator.md](orchestrator.md#merge-conflict-handling).

2. **WU size calibration** — Resolved. "~30 minutes" stays as general guidance with a plan to tune based on real run data. No stronger heuristic is needed upfront.

3. **Test infrastructure bootstrapping** — Resolved. The planner (mode 0) inspects the codebase for test infrastructure before planning feature WUs. If none is found, it returns `needs_feedback` with specific questions for the user (framework, test commands, required services like a test database). The user provides this context before planning continues. See [agents.md](agents.md#mode-0-sketching).

4. **Agent prompt storage and distribution** — Resolved. The tool ships as a single `.pyz` zipapp. Agent prompts are `.md` files embedded in the zip under `prompts/`, loaded at runtime via `importlib.resources`. The orchestrator injects them as `system_prompt=` to `ClaudeAgentOptions`. No external agent definition files, no YAML frontmatter, no file bundle to carry around. See [overview.md](overview.md#distribution-single-zipapp-binary) and [agents.md](agents.md#agent-prompt-format).

5. **Reviewer parallelism** — Resolved as a planned milestone. Initial implementation is sequential. Upgrade to `asyncio.gather()` (same pattern as parallel implementers) is tracked as a later milestone. See [overview.md](overview.md#reviewer-parallelism-planned-milestone).

6. **Skills extraction** — Resolved. With the zipapp approach, there are no separate skill files. Conditional behaviors (break-glass, recall, circuit-breaker) are prompt sections the orchestrator assembles and prepends/appends before calling `query()`. This is strictly better than external skill files: no context pollution, no file management, fully controlled by the Python script. See [overview.md](overview.md#skills-are-transparent-to-the-orchestrator).

7. **Context-gathering interactivity and handoff** — Resolved. `query()` does not create an interactive terminal session. When the context-gathering agent uses `AskUserQuestion`, the SDK fires a `PreToolUse` hook; the orchestrator handles it by printing the question and reading `input()`, then returning the answer to the agent. All state (expanded problem statement, plan, WUs) is written to disk by the agents and read back by the orchestrator — nothing passes through stdout or the orchestrator's memory. See [orchestrator.md](orchestrator.md#user-interaction-via-the-orchestrator).
