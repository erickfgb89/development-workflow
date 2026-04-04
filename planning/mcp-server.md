# Workflow MCP Server

The orchestrator runs a lightweight MCP server alongside the workflow. Agents that need to interact with the user call tools on this server rather than relying on Claude Code built-ins like `AskUserQuestion`, which require a VS Code or desktop app environment.

The server is defined in the zipapp and created once at startup, then passed to every `query()` call via `mcp_servers`.

## Why a custom MCP server

`AskUserQuestion` is a Claude Code built-in that depends on the IDE/app layer to render the question UI. It is not reliable when running from a standalone Python script. A custom MCP server gives agents a stable, environment-agnostic way to pause and request input, without needing to exit their `query()` run.

From the agent's perspective: call a tool, receive a result, continue. No state machine interruption visible to the agent at all.

From the orchestrator's perspective: the MCP tool handler blocks until the user responds, then returns the answer to the agent transparently. The orchestrator's state machine never changes state for a user question — the question and answer happen inside the `query()` call.

## Implementation

Built with the Claude Agent SDK's built-in in-process MCP server (`create_sdk_mcp_server`). No external MCP library needed; the server runs in the same process as the orchestrator.

```python
import asyncio
import json
from typing import Any

from claude_agent_sdk import tool, create_sdk_mcp_server


@tool(
    "ask_question",
    "Ask the user a clarifying question and wait for their response",
    {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "The question to ask the user"
            },
            "context": {
                "type": "string",
                "description": "Optional: additional context to show alongside the question"
            }
        },
        "required": ["question"]
    }
)
async def ask_question(args: dict[str, Any]) -> dict[str, Any]:
    question = args["question"]
    context = args.get("context", "")

    if context:
        print(f"\n[Context]: {context}")
    print(f"\n[Agent question]: {question}")

    loop = asyncio.get_event_loop()
    answer = await loop.run_in_executor(None, lambda: input("Your answer: ").strip())

    return {"content": [{"type": "text", "text": json.dumps({"answer": answer})}]}


@tool(
    "escalate",
    "Request a go/no-go decision from the user when the reviewer cannot determine a verdict independently",
    {
        "type": "object",
        "properties": {
            "wu_id": {
                "type": "string",
                "description": "The work unit being reviewed"
            },
            "reason": {
                "type": "string",
                "description": "Why the reviewer cannot make a determination without user input"
            },
            "summary": {
                "type": "string",
                "description": "A brief summary of the change and the specific concern"
            }
        },
        "required": ["wu_id", "reason", "summary"]
    }
)
async def escalate(args: dict[str, Any]) -> dict[str, Any]:
    print(f"\n[Reviewer escalation — {args['wu_id']}]")
    print(f"Reason: {args['reason']}")
    print(f"Summary: {args['summary']}")
    print()

    loop = asyncio.get_event_loop()

    while True:
        raw = await loop.run_in_executor(
            None, lambda: input("Decision [approve/reject]: ").strip().lower()
        )
        if raw in ("approve", "reject"):
            break
        print("Please enter 'approve' or 'reject'.")

    guidance = await loop.run_in_executor(
        None, lambda: input("Guidance for reviewer (optional, press Enter to skip): ").strip()
    )

    result = {"decision": raw, "guidance": guidance or ""}
    return {"content": [{"type": "text", "text": json.dumps(result)}]}


workflow_server = create_sdk_mcp_server(
    name="workflow",
    tools=[ask_question, escalate],
)
```

> **Note on `input()` in async handlers:** The SDK's tool handlers are `async def`. Calling `input()` directly would block the entire asyncio event loop. `run_in_executor(None, ...)` offloads the blocking call to a thread pool, keeping the loop responsive while waiting for the user.

## Tools

### `ask_question`

Pauses the agent to ask the user a question. Blocks until the user responds. Returns the user's answer as a string.

**Input schema:**
```json
{
  "type": "object",
  "properties": {
    "question": { "type": "string", "description": "The question to ask the user" },
    "context":  { "type": "string", "description": "Optional: additional context shown alongside the question" }
  },
  "required": ["question"]
}
```

**Returns:** `{ "answer": "string — the user's response" }`

---

### `escalate`

Used by the reviewer to request a go/no-go decision from the user when the verdict cannot be determined from the six-hats analysis alone. Blocks until the user responds.

**Input schema:**
```json
{
  "type": "object",
  "properties": {
    "wu_id":   { "type": "string", "description": "The work unit being reviewed" },
    "reason":  { "type": "string", "description": "Why the reviewer cannot determine a verdict independently" },
    "summary": { "type": "string", "description": "Brief summary of the change and specific concern" }
  },
  "required": ["wu_id", "reason", "summary"]
}
```

**Returns:** `{ "decision": "approve | reject", "guidance": "string — optional guidance for the reviewer" }`

---

## Passing the server to agents

The server is created once and passed to every `query()` call:

```python
from claude_agent_sdk import ClaudeAgentOptions

def base_options(cwd: str | None = None, **kwargs) -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        cwd=cwd or REPO_ROOT,
        permission_mode="bypassPermissions",
        allow_dangerously_skip_permissions=True,
        mcp_servers={"workflow": workflow_server},
        **kwargs,
    )
```

Tool names are namespaced as `mcp__{server_name}__{tool_name}`. For the workflow server:
- `mcp__workflow__ask_question`
- `mcp__workflow__escalate`

Agent prompts reference these tools by their namespaced names. The orchestrator may include them in `allowed_tools` to restrict which agents have access to each tool (e.g., only the reviewer should call `escalate`).

## Effect on the state machine

With the MCP server in place, user interaction is transparent to the state machine. The states `PLAN_FEEDBACK` and `USER_FEEDBACK` are eliminated — user questions happen inside whatever state is currently running and are invisible to the orchestrator's transition logic.

The `needs_feedback` response status is also eliminated from sketcher and reshaper contracts. Those agents call `ask_question` mid-run instead of returning early. The reviewer's escalation path is a call to the `escalate` tool rather than an early exit.

## Future extension point

The MCP server is the natural integration point for the visual web interface (see [future-features/visual-web-interface.md](future-features/visual-web-interface.md)). A future version replaces the `run_in_executor(input(...))` calls with WebSocket messages to a browser client, while the agent-facing tool API remains identical — no agent prompt changes required.
