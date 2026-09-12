from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .core import HarnessError, build_context, find_root, init_project, render_context, start_session


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="ekzd", description="Strict development harness.")
    sub = result.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="Create project harness configuration.")
    init.add_argument("--name")

    start = sub.add_parser("start", help="Start a scoped work session.")
    start.add_argument("objective")

    context = sub.add_parser("context", help="Render project and session context.")
    context.add_argument("--json", action="store_true", dest="as_json")

    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        root = find_root(Path.cwd())
        if args.command == "init":
            path = init_project(root, args.name)
            print(f"Created {path.relative_to(root)}. Configure acceptance and verification before starting.")
            return 0
        if args.command == "start":
            state = start_session(root, args.objective)
            print(f"Started: {state['objective']}")
            return 0
        if args.command == "context":
            context = build_context(root)
            print(json.dumps(context, indent=2, sort_keys=True) if args.as_json else render_context(context), end="")
            return 0
        raise HarnessError(f"Unsupported command: {args.command}")
    except HarnessError as exc:
        print(f"ekzd: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
