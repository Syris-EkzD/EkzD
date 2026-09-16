from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .check import render_worker_check, unavailable_worker_result
from .core import (
    HarnessError,
    abort_session,
    build_context,
    find_root,
    finish_session,
    init_project,
    read_state,
    verify_session,
)
from .identity import BuildIdentityUnavailable, runtime_identity
from .task import write_task_manifest_file
from .ui import (
    failure,
    render_command_summary,
    render_context_ui,
    render_status_ui,
    render_verification_ui,
    supports_color,
)
from .worker import run_worker_check
from .workflow import (
    build_implementation_prompt,
    build_worker_task_manifest,
    build_workflow_status,
    start_reproducible_session,
)


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
    start.add_argument("objective")
    start.add_argument(
        "--branch",
        required=True,
        dest="implementation_branch",
        help="Dedicated implementation/task branch to use from the frozen baseline.",
    )

    sub.add_parser("status", help="Show the active workflow state and next action.")
    sub.add_parser("prompt", help="Render the frozen implementation handoff for the active session.")

    task = sub.add_parser("task", help="Export the canonical schema-v2 worker task for the active session.")
    task.add_argument(
        "--output",
        type=Path,
        help="Write the canonical task manifest to an external file instead of stdout.",
    )

    context = sub.add_parser("context", help="Render project and session context.")
    context.add_argument("--json", action="store_true", dest="as_json")

    sub.add_parser("verify", help="Run configured verification and bind the result to current state.")

    check = sub.add_parser("check", help="Run stateless worker checks from a standalone task manifest.")
    check.add_argument("--task", required=True, type=Path, help="Path to the standalone task TOML manifest.")
    check.add_argument("--final", action="store_true", help="Require a clean final handoff candidate.")
    check.add_argument("--json", action="store_true", dest="as_json", help="Emit one structured JSON result.")

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

    if args.command == "check":
        try:
            root = find_root(Path.cwd())
            result = run_worker_check(root, args.task, final=args.final)
        except (HarnessError, OSError) as exc:
            result = unavailable_worker_result(args.task, final=args.final, message=str(exc))
        if args.as_json:
            print(json.dumps(result, indent=2, sort_keys=True))
        else:
            print(render_worker_check(result), end="")
        return 0 if result["ready"] else 1

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
                    next_action="Run `ekzd status`, generate the semantic handoff with `ekzd prompt`, or export the worker task with `ekzd task`.",
                    enabled=color,
                ),
                end="",
            )
            return 0
        if args.command == "status":
            status = build_workflow_status(root)
            if status.get("session_status") == "finished":
                state = read_state(root)
                project = state.get("project") if state else None
                if project:
                    status = {**status, "project": project}
            print(render_status_ui(status, enabled=color), end="")
            return 0
        if args.command == "prompt":
            print(build_implementation_prompt(root), end="")
            return 0
        if args.command == "task":
            manifest = build_worker_task_manifest(root)
            if args.output is None:
                sys.stdout.write(manifest)
            else:
                write_task_manifest_file(root, args.output, manifest)
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
