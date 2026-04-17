"""Thin wrapper around the Claude Agent SDK.

Provides two surfaces:
- `run_query()`: fire-and-forget one-shot call (used by Planner, Implementer, Reviewer)
- `GathererSession`: stateful `ClaudeSDKClient` wrapper for interactive multi-turn
  context gathering.

Schema validation of normal agent output lives in the callers.  However, AgentError
detection is handled here so that every caller gets it automatically — an agent that
responds with {"error": true, ...} raises AgentHaltError before the caller's
validation even runs.
"""

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
    query,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# AgentError — the universal halt signal
# ---------------------------------------------------------------------------

class AgentHaltError(Exception):
    """Raised when an agent responds with the AgentError envelope.

    The orchestrator catches this, marks the WU as failed, and surfaces the
    report for human review rather than retrying blindly.
    """

    def __init__(self, report: dict[str, Any]) -> None:
        self.report = report
        super().__init__(
            f"[{report.get('agent_role', '?')} / {report.get('wu_id', '?')}] "
            f"{report.get('blocker', '(no blocker message)')}"
        )


def _check_for_agent_error(data: dict[str, Any]) -> None:
    """Raise AgentHaltError if *data* matches the AgentError envelope.

    Detection is intentionally cheap: we only test for the `"error": true`
    discriminator field that the schema defines as a boolean const.  Full
    schema validation of the error envelope is left to the orchestrator so
    this path stays on the fast route.
    """
    if data.get("error") is True:
        raise AgentHaltError(data)


# ---------------------------------------------------------------------------
# One-shot query helper
# ---------------------------------------------------------------------------


DEFAULT_MODEL = "claude-sonnet-4-6"


async def run_query(
    prompt: str,
    *,
    system_prompt: str | None = None,
    cwd: Path | None = None,
    permission_mode: str = "bypassPermissions",
    model: str | None = None,
    max_turns: int = 50,
) -> tuple[str, ResultMessage]:
    """Run a one-shot query and return (text_result, result_message).

    Raises RuntimeError if the query ends in an error.
    """
    options = ClaudeAgentOptions(
        permission_mode=permission_mode,  # type: ignore[arg-type]
        system_prompt=system_prompt,  # type: ignore[arg-type]
        cwd=cwd,
        model=model or DEFAULT_MODEL,
        max_turns=max_turns,
    )

    text_parts: list[str] = []
    result_msg: ResultMessage | None = None

    async for message in query(prompt=prompt, options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    text_parts.append(block.text)
        elif isinstance(message, ResultMessage):
            result_msg = message

    if result_msg is None:
        raise RuntimeError("SDK query ended without a ResultMessage")
    if result_msg.is_error:
        raise RuntimeError(f"SDK query failed: {result_msg.result}")

    return "".join(text_parts), result_msg


async def run_query_json(
    prompt: str,
    *,
    system_prompt: str | None = None,
    cwd: Path | None = None,
    permission_mode: str = "bypassPermissions",
    model: str | None = None,
    max_turns: int = 50,
) -> dict[str, Any]:
    """Like run_query() but parses the result as JSON.

    Strips markdown code fences if the model wraps its response.
    """
    text, _ = await run_query(
        prompt,
        system_prompt=system_prompt,
        cwd=cwd,
        permission_mode=permission_mode,
        model=model,
        max_turns=max_turns,
    )
    data = _extract_json(text)
    _check_for_agent_error(data)
    return data


def _extract_json(text: str) -> dict[str, Any]:
    """Extract a JSON object from agent output.

    Agents may prefix their JSON contract response with freeform narrative text.
    This function finds the JSON object in the response, accepting it either
    inside a markdown ```json ... ``` fence or as bare JSON.

    Strategy:
    1. Try fenced ```[json]...``` blocks from last to first — a fence is an
       unambiguous signal that the model is deliberately emitting JSON.
    2. Fall back to bare-JSON scanning: collect every top-level '{' and its
       matching '}', then try candidates sorted largest-first so the outermost
       (full) object wins over any nested sub-object.
    """
    import re

    # --- Pass 1: fenced code blocks (preferred) ---
    fenced: list[str] = []
    for match in re.finditer(r"```(?:json)?\s*\n(.*?)```", text, re.DOTALL):
        fenced.append(match.group(1).strip())

    for candidate in reversed(fenced):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue

    # --- Pass 2: bare JSON objects, largest span first ---
    bare: list[str] = []
    for i, ch in enumerate(text):
        if ch != "{":
            continue
        depth = 0
        in_string = False
        escape = False
        for j, c in enumerate(text[i:], start=i):
            if escape:
                escape = False
                continue
            if c == "\\" and in_string:
                escape = True
                continue
            if c == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    bare.append(text[i : j + 1])
                    break

    # Largest span = outermost object; most likely to be the top-level contract.
    for candidate in sorted(bare, key=len, reverse=True):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue

    raise ValueError(
        f"Agent did not return valid JSON anywhere in its response.\n\nRaw:\n{text}"
    )


# ---------------------------------------------------------------------------
# Interactive Gatherer session
# ---------------------------------------------------------------------------


class GathererSession:
    """Wraps ClaudeSDKClient for a multi-turn interactive gathering conversation.

    Usage::

        async with GathererSession(system_prompt=..., cwd=target) as session:
            reply = await session.send("What is the goal of this task?")
            reply2 = await session.send("Any more context?")
    """

    def __init__(
        self,
        system_prompt: str,
        cwd: Path,
        model: str | None = None,
    ) -> None:
        self._system_prompt = system_prompt
        self._cwd = cwd
        self._model = model
        self._client: ClaudeSDKClient | None = None
        self._options = ClaudeAgentOptions(
            permission_mode="acceptEdits",
            system_prompt=system_prompt,
            cwd=cwd,
            model=model or DEFAULT_MODEL,
            max_turns=200,
        )

    async def __aenter__(self) -> "GathererSession":
        self._client = ClaudeSDKClient(options=self._options)
        await self._client.connect()
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._client is not None:
            await self._client.disconnect()
            self._client = None

    async def send(self, user_text: str) -> str:
        """Send a user message and collect the full assistant reply."""
        if self._client is None:
            raise RuntimeError("Not connected. Use as async context manager.")

        await self._client.query(user_text)
        text_parts: list[str] = []
        async for message in self._client.receive_response():
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        text_parts.append(block.text)
        return "".join(text_parts)
