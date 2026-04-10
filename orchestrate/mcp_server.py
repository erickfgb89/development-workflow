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

    try:
        from .orchestrator import _tui_app
        if _tui_app is not None:
            if context:
                _tui_app.append_to_output(f"[dim]Context: {context}[/dim]")
            answer = await _tui_app.request_input(f"Agent question: {question}")
            return {"content": [{"type": "text", "text": json.dumps({"answer": answer})}]}
    except Exception:
        pass

    # Fallback to console
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
    wu_id = args["wu_id"]
    reason = args["reason"]
    summary = args["summary"]

    try:
        from .orchestrator import _tui_app
        if _tui_app is not None:
            _tui_app.append_to_output(f"[bold red][Reviewer escalation — {wu_id}][/bold red]")
            _tui_app.append_to_output(f"[dim]Reason: {reason}[/dim]")
            _tui_app.append_to_output(f"[dim]Summary: {summary}[/dim]")
            _tui_app.append_to_output("[dim]Step 1 of 2: enter approve or reject.[/dim]")
            while True:
                raw = (await _tui_app.request_input("Decision [approve/reject]: ")).strip().lower()
                if raw in ("approve", "reject"):
                    break
                _tui_app.append_to_output("[yellow]Please enter 'approve' or 'reject'.[/yellow]")
            _tui_app.append_to_output("[dim]Step 2 of 2: provide guidance for the reviewer (explain your reasoning, flag concerns, or clarify intent). Press Enter to skip.[/dim]")
            guidance = (await _tui_app.request_input("Guidance: ")).strip()
            result = {"decision": raw, "guidance": guidance or ""}
            return {"content": [{"type": "text", "text": json.dumps(result)}]}
    except Exception:
        pass

    # Fallback to console
    print(f"\n[Reviewer escalation — {wu_id}]")
    print(f"Reason: {reason}")
    print(f"Summary: {summary}")
    print()
    print("Step 1 of 2: enter approve or reject.")

    from .console import get_prompt_session, patch_console
    session = get_prompt_session()

    with patch_console():
        while True:
            raw = (await session.prompt_async("Decision [approve/reject]: ")).strip().lower()
            if raw in ("approve", "reject"):
                break
            print("Please enter 'approve' or 'reject'.")

        print("\nStep 2 of 2: provide guidance for the reviewer (explain your reasoning, flag concerns, or clarify intent). Press Enter to skip.")
        guidance = (await session.prompt_async("Guidance: ")).strip()

    result = {"decision": raw, "guidance": guidance or ""}
    return {"content": [{"type": "text", "text": json.dumps(result)}]}


workflow_server = create_sdk_mcp_server(
    name="workflow",
    tools=[ask_question, escalate],
)
