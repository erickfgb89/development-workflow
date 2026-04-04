# Task 001 — SDK Smoke Test: Hello World

**Goal:** Verify the Claude Agent SDK is correctly installed and callable. Write a minimal script that imports the SDK, calls `query()` with a trivial prompt, prints the result, and exits cleanly. This is a build/infrastructure validation task — no workflow logic.

---

## Deliverables

1. **`scripts/hello_sdk.py`** — the smoke test script
2. **`pyproject.toml`** — verify `claude-agent-sdk` is in `dependencies` (already added; confirm it installs correctly)

---

## Script requirements

`scripts/hello_sdk.py` must:

- Import `query`, `ClaudeAgentOptions`, and `ResultMessage` from `claude_agent_sdk`
- Call `query()` with the prompt `"Say hello."` and minimal options (no tools, default model)
- Iterate over the message stream and print the final text from `ResultMessage`
- Print a clear success line on clean exit: `SDK smoke test passed.`
- Catch and print any exception, then exit with code 1

```python
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
```

---

## How to run

```bash
# Install dependencies
uv sync

# Run the smoke test
python scripts/hello_sdk.py
```

Expected output (exact text will vary):
```
Hello! How can I help you today?
SDK smoke test passed.
```

---

## Acceptance criteria

- [ ] `uv sync` installs without errors
- [ ] `python scripts/hello_sdk.py` exits with code 0
- [ ] Output contains the `SDK smoke test passed.` line
- [ ] No import errors or missing-dependency errors

---

## Notes

- `ResultMessage.result` contains the agent's final text output as a string. Other message types in the stream (tool calls, metadata) can be ignored for this smoke test.
- The script uses `acceptEdits` permission mode (not `bypassPermissions`) since it makes no file changes — this is the minimal permission level for a read-only agent call.
- If the SDK version in `pyproject.toml` is wrong or unavailable, `uv sync` will fail before the script runs — that failure is also a valid signal from this task.
