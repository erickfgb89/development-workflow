"""Triage agent — classifies unexpected failures for the orchestrator."""
from __future__ import annotations

import json
from typing import Any

from claude_agent_sdk import query, ClaudeAgentOptions, ResultMessage

from .state import parse_json_result


TRIAGE_PROMPT = """\
You are a triage agent. An unexpected failure occurred in an AI-driven development workflow.
Your only job is to classify the failure and return a single JSON object.

Classify as:
- "retry"   — the failure looks transient (network error, timeout, rate limit); re-calling the agent with no changes is likely to succeed
- "reshape" — the failure indicates a plan-level problem (WU instructions are contradictory, impossible, or require architectural changes the implementer cannot make alone)
- "user"    — the failure requires human judgement (ambiguous requirement, missing credentials, external service down)
- "abort"   — the failure is unrecoverable (corrupt state, missing files the plan depends on)

Return ONLY this JSON object, no prose:
{"action": "retry | reshape | user | abort", "message": "brief explanation"}
"""


_RATE_LIMIT_PHRASES = ("you've hit your limit", "rate limit", "too many requests")


def _is_rate_limit_error(context: dict[str, Any]) -> bool:
    msg = context.get("failure_message", "").lower()
    return any(phrase in msg for phrase in _RATE_LIMIT_PHRASES)


async def handle_unexpected_failure(
    context: dict[str, Any],
    repo_root: str,
) -> dict[str, Any]:
    """
    Triage an unexpected agent failure. Returns {"action": ..., "message": ...}.

    Falls back to {"action": "user", "message": "..."} if the triage agent itself fails.
    Rate limit errors are immediately classified as "retry" without calling the triage agent.
    """
    if _is_rate_limit_error(context):
        return {"action": "retry", "message": f"Rate limit detected for {context.get('wu_id', '?')} — will retry"}

    prompt = (
        TRIAGE_PROMPT
        + f"\n\nFailure context:\n```json\n{json.dumps(context, indent=2, default=str)}\n```"
    )
    options = ClaudeAgentOptions(
        cwd=repo_root,
        model="claude-sonnet-4-6",
        permission_mode="bypassPermissions",
        allowed_tools=[],
        max_turns=3,
    )
    try:
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, ResultMessage):
                return parse_json_result(message.result)
    except Exception as exc:
        return {"action": "user", "message": f"Triage agent itself failed: {exc}"}

    return {"action": "user", "message": "Triage agent did not return a result"}
