from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from typing import TextIO

from .core import HarnessError, abort_session, build_context, finish_session, verify_session
from .terminal import (
    KEY_COPY,
    KEY_DOWN,
    KEY_END,
    KEY_HOME,
    KEY_PAGE_DOWN,
    KEY_PAGE_UP,
    KEY_RETURN,
    KEY_UP,
    TerminalSession,
    copy_text_to_clipboard,
)
from .ui import (
    INTERACTIVE_INTRO,
    failure,
    info,
    render_command_summary,
    render_contract_review,
    render_interactive_home,
    render_status_ui,
    render_terminal_actions,
    render_terminal_contract_review,
    render_terminal_header,
    render_verification_ui,
    success,
    warning,
)
from .workflow import (
    build_contract_review,
    build_implementation_prompt,
    build_workflow_status,
    start_reproducible_session,
)


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


def _interactive_actions(status: dict[str, object], *, persistent: bool) -> list[tuple[str, str]]:
    actions = _actions_for(status)
    if not persistent:
        return actions
    return [action for action in actions if action[0] != "view"]


def _write_line(output: TextIO, message: str = "") -> None:
    output.write(message + "\n")


def _set_feedback(session: TerminalSession, output: TextIO, text: str) -> None:
    if session.persistent:
        session.set_feedback(text)
    else:
        output.write(text)


def _emit_action_result(
    session: TerminalSession,
    output: TextIO,
    *,
    persistent_feedback: str,
    fallback_output: str,
) -> None:
    if session.persistent:
        session.set_feedback(persistent_feedback)
    else:
        output.write(fallback_output)


def _present(
    session: TerminalSession,
    output: TextIO,
    *,
    project: str,
    status: dict[str, object],
    actions: list[tuple[str, str]],
    enabled: bool,
) -> None:
    labels = [label for _, label in actions]
    if session.persistent:
        session.redraw(
            render_terminal_header(project, status, width=session.width, enabled=enabled),
            render_terminal_actions(labels, enabled=enabled),
        )
    else:
        output.write(render_interactive_home(project, status, labels, enabled=enabled))


def _present_contract_review(
    session: TerminalSession,
    output: TextIO,
    review: dict[str, object],
    objective: str,
    implementation_branch: str,
    *,
    enabled: bool,
) -> None:
    decisions = ["Confirm and start", "Update contract first", "Cancel"]
    if session.persistent:
        session.redraw(
            render_terminal_contract_review(
                review,
                objective,
                implementation_branch,
                width=session.width,
                enabled=enabled,
            ),
            render_terminal_actions(decisions, enabled=enabled),
        )
    else:
        output.write(
            render_contract_review(
                review,
                objective,
                implementation_branch,
                enabled=enabled,
            )
        )


def _review_contract(
    session: TerminalSession,
    output: TextIO,
    input_fn: Callable[[str], str],
    review: dict[str, object],
    objective: str,
    implementation_branch: str,
    *,
    enabled: bool,
) -> str:
    while True:
        _present_contract_review(
            session,
            output,
            review,
            objective,
            implementation_branch,
            enabled=enabled,
        )
        choice = _read(input_fn, "Contract decision [1-3]: ").strip()
        if choice == "1":
            return "confirm"
        if choice == "2":
            return "update"
        if choice == "3":
            return "cancel"
        _set_feedback(
            session,
            output,
            warning("Invalid selection. Choose 1, 2, or 3.", enabled=enabled) + "\n",
        )


def _read_prompt_key(key_reader: Callable[[], str]) -> str:
    try:
        return key_reader()
    except (EOFError, KeyboardInterrupt) as exc:
        raise _ExitInteractive from exc


def _show_prompt_view(
    session: TerminalSession,
    prompt: str,
    key_reader: Callable[[], str],
    copy_fn: Callable[[str], bool] | None = None,
) -> None:
    copy_fn = copy_text_to_clipboard if copy_fn is None else copy_fn
    offset = 0
    feedback: str | None = None
    while True:
        offset, page_size, total = session.redraw_document(
            "Implementation Prompt",
            prompt,
            offset,
            feedback=feedback,
        )
        key = _read_prompt_key(key_reader)
        max_offset = max(0, total - page_size)
        if key == KEY_RETURN:
            return
        if key == KEY_COPY:
            if copy_fn(prompt):
                feedback = "✓ Implementation prompt copied to clipboard"
            else:
                feedback = "! Clipboard unavailable. Use `ekzd prompt` for raw output."
            continue

        feedback = None
        if key == KEY_UP:
            offset = max(0, offset - 1)
        elif key == KEY_DOWN:
            offset = min(max_offset, offset + 1)
        elif key == KEY_PAGE_UP:
            offset = max(0, offset - page_size)
        elif key == KEY_PAGE_DOWN:
            offset = min(max_offset, offset + page_size)
        elif key == KEY_HOME:
            offset = 0
        elif key == KEY_END:
            offset = max_offset


def run_interactive(
    root: Path,
    *,
    enabled: bool,
    input_fn: Callable[[str], str] | None = None,
    output: TextIO | None = None,
    error_output: TextIO | None = None,
    terminal: TerminalSession | None = None,
    prompt_key_reader: Callable[[], str] | None = None,
) -> int:
    input_fn = input if input_fn is None else input_fn
    output = sys.stdout if output is None else output
    error_output = sys.stderr if error_output is None else error_output
    session = TerminalSession(output) if terminal is None else terminal
    prompt_key_reader = session.read_key if prompt_key_reader is None else prompt_key_reader

    with session:
        intro_shown = False
        try:
            while True:
                context = build_context(root)
                status = build_workflow_status(root)
                if session.persistent and status.get("session_status") == "active" and not intro_shown:
                    session.set_feedback(INTERACTIVE_INTRO)
                    intro_shown = True
                project = str(context["project"]["name"])
                actions = _interactive_actions(status, persistent=session.persistent)
                _present(
                    session,
                    output,
                    project=project,
                    status=status,
                    actions=actions,
                    enabled=enabled,
                )

                raw_choice = _read(input_fn, "Choose an action: ").strip()
                try:
                    index = int(raw_choice) - 1
                except ValueError:
                    index = -1
                if index < 0 or index >= len(actions):
                    _set_feedback(
                        session,
                        output,
                        warning("Invalid selection. Choose one of the listed actions.", enabled=enabled) + "\n",
                    )
                    continue

                action = actions[index][0]
                if action == "exit":
                    if not session.persistent:
                        _write_line(output, info("Exited.", enabled=enabled))
                    return 0

                try:
                    if action == "start":
                        objective = _read(input_fn, "Task objective: ").strip()
                        branch = _read(input_fn, "Implementation branch: ").strip()
                        if not objective or not branch:
                            _set_feedback(
                                session,
                                output,
                                warning("Task not started: objective and branch are required.", enabled=enabled) + "\n",
                            )
                            continue
                        review = build_contract_review(root)
                        decision = _review_contract(
                            session,
                            output,
                            input_fn,
                            review,
                            objective,
                            branch,
                            enabled=enabled,
                        )
                        if decision == "update":
                            _set_feedback(
                                session,
                                output,
                                warning(
                                    "Task not started. Update and commit `.ekzd/project.toml`, then try again.",
                                    enabled=enabled,
                                )
                                + "\n",
                            )
                            continue
                        if decision == "cancel":
                            _set_feedback(session, output, info("Task start cancelled.", enabled=enabled) + "\n")
                            continue

                        state = start_reproducible_session(root, objective, implementation_branch=branch)
                        intro_shown = True
                        _emit_action_result(
                            session,
                            output,
                            persistent_feedback=success("Task started", enabled=enabled) + "\n",
                            fallback_output=render_command_summary(
                                "session",
                                "Session started",
                                tone="success",
                                details=[
                                    ("objective", str(state["objective"])),
                                    ("implementation branch", str(state["workflow"]["implementation_branch"])),
                                ],
                                next_action="Generate the implementation prompt when you are ready to hand off the task.",
                                enabled=enabled,
                            ),
                        )
                    elif action == "prompt":
                        prompt = build_implementation_prompt(root)
                        if session.persistent:
                            _show_prompt_view(session, prompt, prompt_key_reader)
                        else:
                            output.write(prompt)
                    elif action == "verify":
                        verification = verify_session(root)
                        _emit_action_result(
                            session,
                            output,
                            persistent_feedback=(
                                success("Verification passed", enabled=enabled)
                                if verification.get("passed")
                                else failure("Verification failed", enabled=enabled)
                            )
                            + "\n",
                            fallback_output=render_verification_ui(verification, enabled=enabled),
                        )
                    elif action == "view":
                        output.write(render_status_ui({**status, "project": project}, enabled=enabled))
                    elif action == "accept":
                        if not _confirm(input_fn, "Accept this verified task?"):
                            _set_feedback(
                                session,
                                output,
                                warning("Acceptance cancelled.", enabled=enabled) + "\n",
                            )
                            continue
                        state = finish_session(root, accept=True)
                        _emit_action_result(
                            session,
                            output,
                            persistent_feedback=success("Task accepted", enabled=enabled) + "\n",
                            fallback_output=render_command_summary(
                                "session",
                                "Session accepted",
                                tone="success",
                                details=[("objective", str(state["objective"]))],
                                enabled=enabled,
                            ),
                        )
                    elif action == "abort":
                        if not _confirm(input_fn, "Abort this task without acceptance?"):
                            _set_feedback(
                                session,
                                output,
                                warning("Abort cancelled.", enabled=enabled) + "\n",
                            )
                            continue
                        state = abort_session(root)
                        _emit_action_result(
                            session,
                            output,
                            persistent_feedback=warning("Task aborted", enabled=enabled) + "\n",
                            fallback_output=render_command_summary(
                                "session",
                                "Session aborted without acceptance",
                                tone="warning",
                                details=[("objective", str(state["objective"]))],
                                next_action="Start a fresh task when ready.",
                                enabled=enabled,
                            ),
                        )
                except HarnessError as exc:
                    rendered_error = failure(f"EkzD: {exc}", enabled=enabled)
                    _write_line(error_output, rendered_error)
                    if session.persistent:
                        session.set_feedback(rendered_error + "\n")
        except (_ExitInteractive, KeyboardInterrupt):
            if not session.persistent:
                _write_line(output)
                _write_line(output, info("Exited.", enabled=enabled))
            return 0
