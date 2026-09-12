from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .core import (
    HarnessError,
    build_context,
    find_root,
    finish_session,
    init_project,
    render_context,
    start_session,
    update_handoff,
    verify_session,
)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="ekzd", description="Strict development harness.")
    sub = result.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="Create project harness configuration.")
    init.add_argument("--name")

    start = sub.add_parser("start", help="Start a scoped work session.")
    start.add_argument("objective")

    context = sub.add_parser("context", help="Render project and session context.")
    context.add_argument("--json", action="store_true", dest="as_json")

    sub.add_parser("verify", help="Run configured verification and bind the result to current state.")

    handoff = sub.add_parser("handoff", help="Record semantic handoff notes.")
    handoff.add_argument("--done", action="append", default=[])
    handoff.add_argument("--next", action="append", dest="next_items", default=[])

    finish = sub.add_parser("finish", help="Accept and close a verified session.")
    finish.add_argument("--accept", action="store_true", help="Explicitly approve acceptance criteria.")

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
        if args.command == "verify":
            verification = verify_session(root)
            for step in verification["steps"]:
                mark = "PASS" if step["passed"] else "FAIL"
                print(f"[{mark}] {step['name']}")
            return 0 if verification["passed"] else 1
        if args.command == "handoff":
            handoff = update_handoff(root, done=args.done, next_items=args.next_items)
            print(json.dumps(handoff, indent=2, sort_keys=True))
            return 0
        if args.command == "finish":
            state = finish_session(root, accept=args.accept)
            print(f"Accepted: {state['objective']}")
            return 0
        raise HarnessError(f"Unsupported command: {args.command}")
    except HarnessError as exc:
        print(f"ekzd: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
