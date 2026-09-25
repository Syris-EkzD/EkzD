from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .core import (
    HarnessError,
    find_root,
    init_project,
)
from .identity import BuildIdentityUnavailable, runtime_identity
from .session import abort_session, export_handoff, finish_session, start_session, verify_session
from .ui import (
    failure,
    render_command_summary,
    render_status_ui,
    render_verification_ui,
    supports_color,
)
from .workflow import build_workflow_status


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="ekzd", description="EkzD — strict development harness.")
    result.add_argument(
        "--version",
        action="store_true",
        dest="show_version",
        help="Show the executing EkzD semantic version and exact source build SHA-256.",
    )
    sub = result.add_subparsers(dest="command")

    init = sub.add_parser("init", help="Create project harness configuration.")
    init.add_argument("--name")

    start = sub.add_parser("start", help="Start a scoped work session.")
    start.add_argument("task_file", type=Path, help="Disposable task-authoring TOML file.")

    sub.add_parser("status", help="Show the active workflow state and next action.")
    handoff = sub.add_parser("handoff", help="Re-export the retained portable worker archive.")
    handoff.add_argument("--output", type=Path)
    sub.add_parser("verify", help="Independently verify the final candidate.")

    sub.add_parser("abort", help="Close the active session without acceptance.")

    finish = sub.add_parser("finish", help="Accept and close a verified session.")
    finish.add_argument("--accept", action="store_true", help="Explicitly approve acceptance criteria.")

    return result


def main(argv: list[str] | None = None) -> int:
    argument_parser = parser()
    args = argument_parser.parse_args(argv)

    if args.show_version:
        try:
            identity = runtime_identity()
        except BuildIdentityUnavailable as exc:
            print(f"EkzD: exact runtime build identity unavailable: {exc}", file=sys.stderr)
            return 1
        print(f"EkzD {identity['version']} {identity['build_sha256']}")
        return 0

    if args.command is None:
        argument_parser.print_help()
        return 0

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
                    next_action="Configure durable scope/protection guidance once, commit policy, then write a local task.",
                    enabled=color,
                ),
                end="",
            )
            return 0
        if args.command == "start":
            state = start_session(root, args.task_file)
            print(
                render_command_summary(
                    "session",
                    "Session started",
                    tone="success",
                    details=[
                        ("objective", str(state["contract"]["objective"])),
                        ("contract ID", state["contract_id"]),
                        ("handoff", str(root / state["handoff"]["path"])),
                        ("implementation branch", str(state["contract"]["branch"])),
                    ],
                    next_action="Transfer/extract the handoff outside the target checkout; from the declared branch run `python3 /path/to/handoff/run.py prepare`.",
                    enabled=color,
                ),
                end="",
            )
            return 0
        if args.command == "status":
            status = build_workflow_status(root)
            print(render_status_ui(status, enabled=color), end="")
            return 0
        if args.command == "handoff":
            print(export_handoff(root, args.output))
            return 0
        if args.command == "verify":
            verification = verify_session(root)
            print(render_verification_ui(verification, enabled=color), end="")
            return 0 if verification["passed"] else 1
        if args.command == "abort":
            state = abort_session(root)
            print(
                render_command_summary(
                    "session",
                    "Session aborted without acceptance",
                    tone="warning",
                    details=[("objective", str(state["contract"]["objective"]))],
                    next_action='Start a fresh task with `ekzd start task.toml` when ready.',
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
                    details=[("objective", str(state["contract"]["objective"]))],
                    enabled=color,
                ),
                end="",
            )
            return 0
        raise HarnessError(f"Unsupported command: {args.command}")
    except (HarnessError, OSError) as exc:
        error_color = supports_color(sys.stderr)
        print(failure(f"EkzD: {exc}", enabled=error_color), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
