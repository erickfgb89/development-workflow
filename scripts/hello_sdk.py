#!/usr/bin/env python3
"""SDK smoke test — imports the SDK, calls query(), prints the result."""

import asyncio
import sys
from claude_agent_sdk import query, ClaudeAgentOptions, ResultMessage


async def main() -> None:
    options = ClaudeAgentOptions(
        permission_mode="acceptEdits",
    )
    async for message in query(prompt="Say hello.", options=options):
        if isinstance(message, ResultMessage):
            print(message.result)

    print("SDK smoke test passed.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        print(f"SDK smoke test FAILED: {exc}", file=sys.stderr)
        sys.exit(1)
