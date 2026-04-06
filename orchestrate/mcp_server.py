"""Workflow MCP server — provides ask_question and escalate tools to agents."""
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
                "description": "The question to ask the user",
            },
            "context": {
                "type": "string",
                "description": "Optional: additional context to show alongside the question",
            },
        },
        "required": ["question"],
    },
)
async def ask_question(args: dict[str, Any]) -> dict[str, Any]:
    question = args["question"]
    context = args.get("context", "")


    if context:
        print(f"\n[Context]: {context}")
    print(f"\n[Agent question]: {question}")

    from .console import get_prompt_session, patch_console
    session = get_prompt_session()
    
    with patch_console():
        answer = (await session.prompt_async("Your answer: ")).strip()

    return {"content": [{"type": "text", "text": json.dumps({"answer": answer})}]}


@tool(
    "escalate",
    "Request a go/no-go decision from the user when the reviewer cannot determine a verdict independently",
    {
        "type": "object",
        "properties": {
            "wu_id": {
                "type": "string",
                "description": "The work unit being reviewed",
            },
            "reason": {
                "type": "string",
                "description": "Why the reviewer cannot make a determination without user input",
            },
            "summary": {
                "type": "string",
                "description": "A brief summary of the change and the specific concern",
            },
        },
        "required": ["wu_id", "reason", "summary"],
    },
)
async def escalate(args: dict[str, Any]) -> dict[str, Any]:


    print(f"\n[Reviewer escalation — {args['wu_id']}]")
    print(f"Reason: {args['reason']}")
    print(f"Summary: {args['summary']}")
    print()

    from .console import get_prompt_session, patch_console
    session = get_prompt_session()

    with patch_console():
        while True:
            raw = (await session.prompt_async("Decision [approve/reject]: ")).strip().lower()
            if raw in ("approve", "reject"):
                break
            print("Please enter 'approve' or 'reject'.")

        guidance = (await session.prompt_async("Guidance for reviewer (optional, press Enter to skip): ")).strip()

    result = {"decision": raw, "guidance": guidance or ""}
    return {"content": [{"type": "text", "text": json.dumps(result)}]}


workflow_server = create_sdk_mcp_server(
    name="workflow",
    tools=[ask_question, escalate],
)
