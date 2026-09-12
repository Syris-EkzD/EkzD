from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from typing import TextIO

from .core import HarnessError, abort_session, build_context, finish_session, verify_session
from .ui import (
    failure,
    info,
    render_command_summary,
    render_interactive_home,
    render_status_ui,
    render_verification_ui,
    warning,
)
from .workflow import build_implementation_prompt, build_workflow_status, start_reproducible_session


class _ExitInteractive(Exception):
    pass


def _read(input_fn: Callable[[str], str], prompt: str) -> str:
    try:
        return input_fn(prompt)
    except (EOFError, KeyboardInterrupt) as exc:
        raise _ExitInteractive from exc


def _confirm(input_fn: Callable[[str], str], prompt: str) -> bool:
    return _read(input_fn, f"{prompt} [y/N]: ").strip().lower() in {"y", "yes"}


def _implementation_ready(status: dict[str, object]) -> bool:
    if status.get("current_branch") != status.get("implementation_branch"):
        return False
    commit_count = status.get("commit_count")
    return (
        status.get("head") != status.get("baseline_head")
        or status.get("worktree_clean") is False
        or (isinstance(commit_count, int) and commit_count > 0)
    )


def _actions_for(status: dict[str, object]) -> list[tuple[str, str]]:
    if status.get("session_status") != "active":
        actions = [("start", "Start task")]
        if status.get("objective"):
            actions.append(("view", "View task"))
        actions.append(("exit", "Exit"))
        return actions

    verification = str(status.get("verification") or "not run")
    if verification == "blocked":
        return [("view", "View task"), ("abort", "Abort task"), ("exit", "Exit")]
    if verification == "passed":
        return [
            ("view", "View task"),
            ("accept", "Accept verified task"),
            ("abort", "Abort task"),
            ("exit", "Exit"),
        ]

    actions = [("prompt", "Generate implementation prompt")]
    if _implementation_ready(status):
        actions.append(("verify", "Verify changes"))
    actions.extend(
        [
            ("view", "View task"),
            ("abort", "Abort task"),
            ("exit", "Exit"),
        ]
    )
    return actions


def _write_line(output: TextIO, message: str = "") -> None:
    output.write(message + "\n")


def run_interactive(
    root: Path,
    *,
    enabled: bool,
    input_fn: Callable[[str], str] | None = None,
    output: TextIO | None = None,
    error_output: TextIO | None = None,
) -> int:
    input_fn = input if input_fn is None else input_fn
    output = sys.stdout if output is None else output
    error_output = sys.stderr if error_output is None else error_output

    try:
        while True:
            context = build_context(root)
            status = build_workflow_status(root)
            project = str(context["project"]["name"])
            actions = _actions_for(status)
            output.write(
                render_interactive_home(
                    project,
                    status,
                    [label for _, label in actions],
                    enabled=enabled,
                )
            )

            raw_choice = _read(input_fn, "Choose an action: ").strip()
            try:
                index = int(raw_choice) - 1
            except ValueError:
                index = -1
            if index < 0 or index >= len(actions):
                _write_line(output, warning("Invalid selection. Choose one of the listed actions.", enabled=enabled))
                continue

            action = actions[index][0]
            if action == "exit":
                _write_line(output, info("Exited.", enabled=enabled))
                return 0

            try:
                if action == "start":
                    objective = _read(input_fn, "Task objective: ").strip()
                    branch = _read(input_fn, "Implementation branch: ").strip()
                    if not objective or not branch:
                        _write_line(output, warning("Task not started: objective and branch are required.", enabled=enabled))
                        continue
                    state = start_reproducible_session(root, objective, implementation_branch=branch)
                    output.write(
                        render_command_summary(
                            "session",
                            "Session started",
                            tone="success",
                            details=[
                                ("objective", str(state["objective"])),
                                ("implementation branch", str(state["workflow"]["implementation_branch"])),
                            ],
                            next_action="Generate the implementation prompt when you are ready to hand off the task.",
                            enabled=enabled,
                        )
                    )
                elif action == "prompt":
                    output.write(build_implementation_prompt(root))
                elif action == "verify":
                    output.write(render_verification_ui(verify_session(root), enabled=enabled))
                elif action == "view":
                    output.write(render_status_ui({**status, "project": project}, enabled=enabled))
                elif action == "accept":
                    if not _confirm(input_fn, "Accept this verified task?"):
                        _write_line(output, warning("Acceptance cancelled.", enabled=enabled))
                        continue
                    state = finish_session(root, accept=True)
                    output.write(
                        render_command_summary(
                            "session",
                            "Session accepted",
                            tone="success",
                            details=[("objective", str(state["objective"]))],
                            enabled=enabled,
                        )
                    )
                elif action == "abort":
                    if not _confirm(input_fn, "Abort this task without acceptance?"):
                        _write_line(output, warning("Abort cancelled.", enabled=enabled))
                        continue
                    state = abort_session(root)
                    output.write(
                        render_command_summary(
                            "session",
                            "Session aborted without acceptance",
                            tone="warning",
                            details=[("objective", str(state["objective"]))],
                            next_action="Start a fresh task when ready.",
                            enabled=enabled,
                        )
                    )
            except HarnessError as exc:
                _write_line(error_output, failure(f"EkzD: {exc}", enabled=enabled))
    except (_ExitInteractive, KeyboardInterrupt):
        _write_line(output)
        _write_line(output, info("Exited.", enabled=enabled))
        return 0
