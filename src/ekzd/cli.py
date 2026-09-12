from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .core import (
    HarnessError,
    abort_session,
    build_context,
    find_root,
    finish_session,
    init_project,
    start_session,
    update_handoff,
    verify_session,
)
from .ui import failure, header, info, muted, render_context_ui, success, supports_color, warning


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="ekzd", description="EkzD — strict development harness.")
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

    sub.add_parser("abort", help="Close the active session without acceptance.")

    finish = sub.add_parser("finish", help="Accept and close a verified session.")
    finish.add_argument("--accept", action="store_true", help="Explicitly approve acceptance criteria.")

    return result


def _print_handoff(handoff: dict[str, object], *, color: bool) -> None:
    print(header("handoff", enabled=color))
    print(success("Handoff updated", enabled=color))
    for label in ("done", "next"):
        values = handoff.get(label, [])
        if not isinstance(values, list) or not values:
            continue
        print(muted(label, enabled=color))
        for value in values:
            print(f"  • {value}")


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    color = supports_color(sys.stdout)
    try:
        root = find_root(Path.cwd())
        if args.command == "init":
            path = init_project(root, args.name)
            print(header("init", enabled=color))
            print(success(f"Created {path.relative_to(root)}", enabled=color))
            print(info("Configure scope, acceptance, and verification before starting a session.", enabled=color))
            return 0
        if args.command == "start":
            state = start_session(root, args.objective)
            print(header("session", enabled=color))
            print(success("Session started", enabled=color))
            print(f"  {state['objective']}")
            return 0
        if args.command == "context":
            context = build_context(root)
            if args.as_json:
                print(json.dumps(context, indent=2, sort_keys=True))
            else:
                print(render_context_ui(context, enabled=color), end="")
            return 0
        if args.command == "verify":
            verification = verify_session(root)
            print(header("verify", enabled=color))
            for step in verification["steps"]:
                message = step["name"]
                print(success(message, enabled=color) if step["passed"] else failure(message, enabled=color))
            print()
            if verification["passed"]:
                print(success("Verification passed", enabled=color))
                print(muted("Bound to the current Git-visible state.", enabled=color))
                return 0
            print(failure("Verification failed", enabled=color))
            return 1
        if args.command == "handoff":
            handoff = update_handoff(root, done=args.done, next_items=args.next_items)
            _print_handoff(handoff, color=color)
            return 0
        if args.command == "abort":
            state = abort_session(root)
            print(header("session", enabled=color))
            print(warning("Session aborted without acceptance", enabled=color))
            print(f"  {state['objective']}")
            return 0
        if args.command == "finish":
            state = finish_session(root, accept=args.accept)
            print(header("session", enabled=color))
            print(success("Session accepted", enabled=color))
            print(f"  {state['objective']}")
            return 0
        raise HarnessError(f"Unsupported command: {args.command}")
    except HarnessError as exc:
        error_color = supports_color(sys.stderr)
        print(failure(f"EkzD: {exc}", enabled=error_color), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
