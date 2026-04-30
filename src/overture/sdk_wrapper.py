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
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Literal

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
    ThinkingBlock,
    query,
)
from claude_agent_sdk.types import McpSdkServerConfig

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


class TurnLimitError(Exception):
    """Raised when the SDK terminates a query because max_turns was reached.

    The partial text collected before the cutoff is preserved in `partial_text`
    so callers can inspect what the agent managed to do, save it for debugging,
    and decide whether to continue in a new session.
    """

    def __init__(self, partial_text: str) -> None:
        self.partial_text = partial_text
        super().__init__(
            f"Agent hit turn limit with {len(partial_text)} chars of partial output"
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
        # The SDK surfaces turn-limit termination as an error result.  We raise
        # TurnLimitError (with whatever partial text was collected) so callers
        # can handle the mid-flight state gracefully instead of treating it as
        # a generic failure.
        partial = "".join(text_parts)
        raise TurnLimitError(partial)

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

    # Tools the Gatherer must never use — it can read freely but not write files.
    _DISALLOWED_TOOLS = ["Write", "Edit", "MultiEdit", "NotebookEdit", "Bash"]

    def __init__(
        self,
        system_prompt: str,
        cwd: Path,
        model: str | None = None,
        mcp_server: Any | None = None,
    ) -> None:
        self._system_prompt = system_prompt
        self._cwd = cwd
        self._model = model
        self._client: ClaudeSDKClient | None = None

        mcp_servers: dict[str, McpSdkServerConfig] = {}
        if mcp_server is not None:
            mcp_servers["overture-gatherer"] = McpSdkServerConfig(
                type="sdk",
                name="overture-gatherer",
                instance=mcp_server._mcp_server,
            )

        self._options = ClaudeAgentOptions(
            permission_mode="bypassPermissions",
            system_prompt=system_prompt,
            cwd=cwd,
            model=model or DEFAULT_MODEL,
            max_turns=200,
            disallowed_tools=self._DISALLOWED_TOOLS,
            mcp_servers=mcp_servers,
        )

    async def __aenter__(self) -> "GathererSession":
        self._client = ClaudeSDKClient(options=self._options)
        await self._client.connect()
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._client is not None:
            await self._client.disconnect()
            self._client = None

    GatherEvent = tuple[Literal["msg_start", "thinking", "text", "done"], str]

    async def stream(self, user_text: str) -> "AsyncIterator[GathererSession.GatherEvent]":
        """Send *user_text* and yield ``(kind, text)`` tuples as they arrive.

        Kinds:
          - ``"msg_start"`` — a new AssistantMessage is beginning (text="")
          - ``"thinking"``  — a ThinkingBlock chunk
          - ``"text"``      — a TextBlock chunk
          - ``"done"``      — emitted once when all messages are complete (text="")

        ``"msg_start"`` lets the UI create a new bubble for each distinct
        AssistantMessage (e.g. when the agent sends text, calls a tool, then
        sends a follow-up message).
        """
        if self._client is None:
            raise RuntimeError("Not connected. Use as async context manager.")

        await self._client.query(user_text)
        emitted_message_ids: set[str] = set()
        msg_count = 0
        emitted_count = 0
        async for message in self._client.receive_response():
            if isinstance(message, AssistantMessage):
                msg_id = message.message_id or ""
                block_types = [type(b).__name__ for b in message.content]
                has_text = any(isinstance(b, TextBlock) and b.text for b in message.content)
                has_thinking = any(isinstance(b, ThinkingBlock) and b.thinking for b in message.content)
                msg_count += 1
                logger.debug(
                    "gatherer stream msg=%d id=%s blocks=%s has_text=%s has_thinking=%s",
                    msg_count, msg_id or "none", block_types, has_text, has_thinking,
                )
                # The CLI streams the same AssistantMessage multiple times as
                # blocks arrive.  The first delivery may have only a
                # ThinkingBlock; the final delivery adds the TextBlock.
                # Skip deliveries that have no text yet (partial thinking-only)
                # to avoid emitting empty bubbles in the UI.
                # Also skip if we already emitted this message_id (duplicate final).
                if msg_id and msg_id in emitted_message_ids:
                    logger.debug("gatherer stream msg=%d skipped duplicate id=%s", msg_count, msg_id)
                    continue
                if not has_text and not has_thinking:
                    logger.debug("gatherer stream msg=%d skipped empty", msg_count)
                    continue
                if not has_text:
                    # Thinking arrived but text hasn't yet — skip this partial.
                    logger.debug("gatherer stream msg=%d skipped thinking-only partial", msg_count)
                    continue
                if msg_id:
                    emitted_message_ids.add(msg_id)
                emitted_count += 1
                logger.debug("gatherer stream msg=%d emitting bubble #%d", msg_count, emitted_count)
                yield ("msg_start", "")
                for block in message.content:
                    if isinstance(block, ThinkingBlock) and block.thinking:
                        yield ("thinking", block.thinking)
                    elif isinstance(block, TextBlock) and block.text:
                        yield ("text", block.text)
        logger.info(
            "gatherer stream done: saw %d AssistantMessages, emitted %d bubbles",
            msg_count, emitted_count,
        )
        yield ("done", "")

    async def send(self, user_text: str) -> tuple[str, str]:
        """Send a user message and return ``(text_reply, thinking_text)``."""
        text_parts: list[str] = []
        thinking_parts: list[str] = []
        async for kind, chunk in self.stream(user_text):
            if kind == "text":
                text_parts.append(chunk)
            elif kind == "thinking":
                thinking_parts.append(chunk)
        return "".join(text_parts), "".join(thinking_parts)
