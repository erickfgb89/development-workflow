"""Opinionated Stdio MCP server for querying workflow state."""
import asyncio
from pathlib import Path
from mcp.server import Server
from mcp.types import Tool, TextContent
from mcp.server.stdio import stdio_server

from .state import load_state, find_state_file

async def run_stdio_mcp(project_root: str):
    root = Path(project_root).resolve()
    
    app = Server("dev-workflow-mcp")

    @app.list_resources()
    async def list_resources():
        from mcp.types import Resource
        return [
            Resource(
                uri="mcp://plan",
                name="plan.md",
                mimeType="text/markdown",
                description="The current generated execution plan",
            ),
            Resource(
                uri="mcp://state",
                name="state.json",
                mimeType="application/json",
                description="The internal graph execution state",
            )
        ]

    @app.read_resource()
    async def read_resource(uri: str):
        if uri == "mcp://plan":
            plan_file = root / "plan.md"
            content = plan_file.read_text() if plan_file.exists() else "No plan.md found."
            return content
        if uri == "mcp://state":
            state_file = root / "state.json"
            content = state_file.read_text() if state_file.exists() else "No state.json found."
            return content
        raise ValueError(f"Unknown resource {uri}")

    @app.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name="get_status",
                description="Return the high level status of the workflow.",
                inputSchema={"type": "object", "properties": {}}
            ),
            Tool(
                name="read_wu",
                description="Read a specific work unit by ID",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "wu_id": {"type": "string", "description": "e.g., WU-001"}
                    },
                    "required": ["wu_id"]
                }
            )
        ]

    @app.call_tool()
    async def call_tool(name: str, arguments: dict):
        if name == "get_status":
            try:
                state_file = find_state_file(root)
                state = load_state(state_file)
                total = len(state.get("work_units", {}))
                pending = len([w for w in state["work_units"].values() if w["status"] == "pending"])
                ip = len([w for w in state["work_units"].values() if w["status"] == "in_progress"])
                complete = len([w for w in state["work_units"].values() if w["status"] == "complete"])
                msg = f"Workflow Status:\nTotal WUs: {total}\nPending: {pending}\nIn Progress: {ip}\nComplete: {complete}"
            except Exception as e:
                msg = f"Could not read state: {e}"
            return [TextContent(type="text", text=msg)]
            
        elif name == "read_wu":
            wu_id = arguments.get("wu_id")
            wu_file = root / "work-units" / f"{wu_id}.md"
            if wu_file.exists():
                return [TextContent(type="text", text=wu_file.read_text())]
            return [TextContent(type="text", text=f"Work unit {wu_id} not found at {wu_file}.")]
            
        raise ValueError(f"Unknown tool {name}")

    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())
