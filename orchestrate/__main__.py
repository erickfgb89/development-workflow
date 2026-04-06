"""Entry point for orchestrate CLI."""
import argparse
import asyncio
import sys


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="orchestrate",
        description="AI-driven development workflow orchestrator",
    )
    parser.add_argument("project_root", nargs="?", help="Path to the project to orchestrate")
    parser.add_argument("--resume", metavar="PATH", help="Resume an interrupted session at PATH")
    parser.add_argument("--mcp", action="store_true", help="Start the stdio MCP server for the target project")
    return parser.parse_args()


async def main_async(args: argparse.Namespace) -> int:
    from .orchestrator import run_orchestrator

    if args.resume:
        project_root = args.resume
        initial_prompt = ""
        resume = True
    else:
        if not args.project_root:
            print("error: project_root is required for a new session", file=sys.stderr)
            return 1
        project_root = args.project_root
        if not args.mcp:
            from .console import get_prompt_session
            session = get_prompt_session(project_root)
            initial_prompt = (await session.prompt_async("Describe what you want to build: ")).strip()
        else:
            initial_prompt = ""
        resume = False

    tasks = []
    
    if args.mcp:
        from .stdio_mcp import run_stdio_mcp
        tasks.append(asyncio.create_task(run_stdio_mcp(args.project_root or args.resume)))
    else:
        tasks.append(asyncio.create_task(run_orchestrator(project_root, initial_prompt, resume=resume)))

    await asyncio.gather(*tasks)
    return 0


def main() -> None:
    args = parse_args()
    sys.exit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
