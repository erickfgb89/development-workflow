# Future Feature: Visual Web Interface

A browser-based interface for the workflow that replaces terminal `input()` prompts with a richer UI and gives the user live visibility into workflow state.

## Motivation

The current terminal interface works but is opaque during a long multi-batch run. The user sees checkpoints and agent output scrolling by, but has no persistent view of:
- Which WUs are complete, in-progress, pending, or blocked
- Which agents are currently running
- The shape of the dependency graph and where the workflow is in it
- The history of reviewer decisions and implementer reports

A visual interface would make the workflow significantly more comfortable to supervise.

## Concept

A local web server (started by the orchestrator alongside the MCP server) that serves a single-page app. The UI connects via WebSocket and receives live events from the orchestrator.

### Key views

**DAG graph** — A live dependency graph rendered as an interactive diagram. Nodes are WUs, colored by status (pending = gray, in-progress = blue, complete = green, failed = red, blocked = striped). Edges show dependencies. Clicking a node shows the WU file contents and any reports associated with it.

**Agent activity panel** — Which agents are currently running, with a live token-stream preview of their output if feasible.

**Review feed** — A chronological list of reviewer reports, with the six-hat sections expandable. Shows the full audit trail of approvals and rejections.

**User interaction panel** — When an agent calls `ask_question` or `escalate`, the question appears here instead of in the terminal. The user types their response and submits. The WebSocket message unblocks the MCP server handler and returns the answer to the agent.

## Integration point

The workflow MCP server (see [mcp-server.md](../mcp-server.md)) is the natural extension point. The `handle_ask_question` and `handle_escalate` functions currently call `input()`. In the web interface version, they instead:
1. Emit a WebSocket event to the browser with the question
2. Await a response event from the browser
3. Return the answer to the agent

The agent-facing MCP tool API is identical — no agent prompt changes required.

The orchestrator also emits events (state transitions, checkpoint data, WU status changes) to the WebSocket, which the browser uses to update the graph and activity panel in real time.

## Implementation notes

- Python web server: `aiohttp` or `fastapi` with `websockets`, running in the same asyncio event loop as the orchestrator
- Frontend: minimal — a static HTML/JS file served from the zipapp, using a lightweight graph library (e.g., `d3-dag` or `elk.js` for the DAG layout)
- No external dependencies for the user — the server and frontend are bundled in the zipapp
- The terminal interface remains as a fallback when the web server is not running (e.g., CI environments, headless servers)

## Milestone placement

After the core workflow loop is stable and the MCP server is proven. The MCP server's `input()` calls are the only thing that needs to change — the rest is additive.
