"""Entry point for orchestrate CLI."""
import argparse
import asyncio
import sys


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="orchestrate",
        description="AI-driven development workflow orchestrator",
    )
    parser.add_argument("project_root", nargs="?", default=None, help="Path to the project to orchestrate")
    parser.add_argument("--resume", metavar="SESSION_NAME", help="Resume an interrupted session by name")
    parser.add_argument("--mcp", metavar="SESSION_NAME", help="Start the stdio MCP server for the target session")
    parser.add_argument("--transition-pauses", action="store_true", help="Pause and print output between agent transitions")
    return parser.parse_args()


async def main_async(args: argparse.Namespace) -> int:
    if args.mcp:
        from .stdio_mcp import run_stdio_mcp
        project_root = args.project_root
        await run_stdio_mcp(project_root, args.mcp)
        return 0

    # TUI mode
    from .tui import OrchestratorApp

    project_root = args.project_root
    if not project_root:
        print("error: project_root is required for a new session", file=sys.stderr)
        return 1

    app = OrchestratorApp(
        repo_root=project_root,
        resume=args.resume,
        transition_pauses=args.transition_pauses,
    )
    await app.run_async()
    return 0


def main() -> None:
    args = parse_args()
    sys.exit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
