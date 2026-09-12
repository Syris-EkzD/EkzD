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
    update_handoff,
    verify_session,
)
from .ui import (
    failure,
    render_command_summary,
    render_context_ui,
    render_handoff_ui,
    render_status_ui,
    render_verification_ui,
    supports_color,
)
from .workflow import build_implementation_prompt, build_workflow_status, start_reproducible_session


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="ekzd", description="EkzD — strict development harness.")
    sub = result.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="Create project harness configuration.")
    init.add_argument("--name")

    start = sub.add_parser("start", help="Start a scoped work session.")
    start.add_argument("objective")
    start.add_argument(
        "--branch",
        required=True,
        dest="implementation_branch",
        help="Dedicated implementation/task branch to use from the frozen baseline.",
    )

    sub.add_parser("status", help="Show the active workflow state and next action.")
    sub.add_parser("prompt", help="Render the frozen implementation handoff for the active session.")

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


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    color = supports_color(sys.stdout)
    try:
        root = find_root(Path.cwd())
        if args.command == "init":
            path = init_project(root, args.name)
            print(
                render_command_summary(
                    "init",
                    "Project harness created",
                    tone="success",
                    details=[("path", str(path.relative_to(root)))],
                    next_action="Configure scope, acceptance, and verification before starting a session.",
                    enabled=color,
                ),
                end="",
            )
            return 0
        if args.command == "start":
            state = start_reproducible_session(
                root,
                args.objective,
                implementation_branch=args.implementation_branch,
            )
            print(
                render_command_summary(
                    "session",
                    "Session started",
                    tone="success",
                    details=[
                        ("objective", str(state["objective"])),
                        ("implementation branch", str(state["workflow"]["implementation_branch"])),
                    ],
                    next_action="Run `ekzd status` or generate the implementation handoff with `ekzd prompt`.",
                    enabled=color,
                ),
                end="",
            )
            return 0
        if args.command == "status":
            print(render_status_ui(build_workflow_status(root), enabled=color), end="")
            return 0
        if args.command == "prompt":
            print(build_implementation_prompt(root), end="")
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
            print(render_verification_ui(verification, enabled=color), end="")
            return 0 if verification["passed"] else 1
        if args.command == "handoff":
            handoff = update_handoff(root, done=args.done, next_items=args.next_items)
            print(render_handoff_ui(handoff, enabled=color), end="")
            return 0
        if args.command == "abort":
            state = abort_session(root)
            print(
                render_command_summary(
                    "session",
                    "Session aborted without acceptance",
                    tone="warning",
                    details=[("objective", str(state["objective"]))],
                    next_action='Start a fresh task with `ekzd start "<objective>" --branch <task-branch>` when ready.',
                    enabled=color,
                ),
                end="",
            )
            return 0
        if args.command == "finish":
            state = finish_session(root, accept=args.accept)
            print(
                render_command_summary(
                    "session",
                    "Session accepted",
                    tone="success",
                    details=[("objective", str(state["objective"]))],
                    enabled=color,
                ),
                end="",
            )
            return 0
        raise HarnessError(f"Unsupported command: {args.command}")
    except HarnessError as exc:
        error_color = supports_color(sys.stderr)
        print(failure(f"EkzD: {exc}", enabled=error_color), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
