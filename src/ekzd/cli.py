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
from .ui import failure, header, info, muted, render_context_ui, success, supports_color, warning
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


def _print_status(status: dict[str, object], *, color: bool) -> None:
    print(header("status", enabled=color))
    session_status = str(status["session_status"])
    if session_status != "active":
        print(warning(f"Session: {session_status}", enabled=color))
        objective = status.get("objective")
        if objective:
            print(f"  {objective}")
        print(info(f"Next: {status['next']}", enabled=color))
        return

    verification = str(status["verification"])
    state_line = failure("Session blocked", enabled=color) if verification == "blocked" else success("Session active", enabled=color)
    print(state_line)
    print(f"  {status['objective']}")
    print()
    print(muted(f"project               {status['project']}", enabled=color))
    print(muted(f"baseline branch       {status['baseline_branch']}", enabled=color))
    print(muted(f"implementation branch {status['implementation_branch']}", enabled=color))
    print(muted(f"current branch        {status['current_branch']}", enabled=color))
    print(muted(f"baseline HEAD         {str(status['baseline_head'])[:12]}", enabled=color))
    worktree = "clean" if status["worktree_clean"] else "changed"
    print(muted(f"worktree              {worktree}", enabled=color))
    commit_count = status.get("commit_count")
    commit_label = "unknown" if commit_count is None else str(commit_count)
    print(muted(f"commits               {commit_label} / {status['max_commits']}", enabled=color))
    print(muted(f"verification          {verification}", enabled=color))
    blocked_reason = status.get("blocked_reason")
    if blocked_reason:
        print()
        print(failure(f"Blocked: {blocked_reason}", enabled=color))
    print()
    print(info(f"Next: {status['next']}", enabled=color))


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
            state = start_reproducible_session(
                root,
                args.objective,
                implementation_branch=args.implementation_branch,
            )
            print(header("session", enabled=color))
            print(success("Session started", enabled=color))
            print(f"  {state['objective']}")
            print(muted(f"  implementation branch: {state['workflow']['implementation_branch']}", enabled=color))
            print(info("Next: run `ekzd status` or generate the implementation handoff with `ekzd prompt`.", enabled=color))
            return 0
        if args.command == "status":
            _print_status(build_workflow_status(root), color=color)
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
            print(header("verify", enabled=color))
            for step in verification["steps"]:
                message = step["name"]
                print(success(message, enabled=color) if step["passed"] else failure(message, enabled=color))
            print()
            if verification["passed"]:
                print(success("Verification passed", enabled=color))
                print(muted("Bound to the current Git-visible state.", enabled=color))
                print(info("Next: review the verified changes, then run `ekzd finish --accept` if you approve them.", enabled=color))
                return 0
            print(failure("Verification failed", enabled=color))
            print(info("Next: fix the reported failure and rerun `ekzd verify`.", enabled=color))
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
            print(info('Next: start a fresh task with `ekzd start "<objective>" --branch <task-branch>` when ready.', enabled=color))
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
