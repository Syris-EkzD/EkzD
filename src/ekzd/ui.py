from __future__ import annotations

import os
from typing import Any, TextIO

RESET = "\x1b[0m"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"
CYAN = "\x1b[36m"
GREEN = "\x1b[32m"
YELLOW = "\x1b[33m"
RED = "\x1b[31m"


def supports_color(stream: TextIO) -> bool:
    if os.environ.get("NO_COLOR") is not None or os.environ.get("TERM") == "dumb":
        return False
    return bool(getattr(stream, "isatty", lambda: False)())


def paint(text: str, *codes: str, enabled: bool) -> str:
    if not enabled or not codes:
        return text
    return "".join(codes) + text + RESET


def header(label: str, *, enabled: bool) -> str:
    return f"{paint('EkzD', BOLD, CYAN, enabled=enabled)} {paint('·', DIM, enabled=enabled)} {label}"


def success(message: str, *, enabled: bool) -> str:
    return f"{paint('✓', GREEN, enabled=enabled)} {paint(message, GREEN, enabled=enabled)}"


def failure(message: str, *, enabled: bool) -> str:
    return f"{paint('✗', RED, enabled=enabled)} {paint(message, RED, enabled=enabled)}"


def warning(message: str, *, enabled: bool) -> str:
    return f"{paint('!', YELLOW, enabled=enabled)} {paint(message, YELLOW, enabled=enabled)}"


def info(message: str, *, enabled: bool) -> str:
    return f"{paint('›', CYAN, enabled=enabled)} {message}"


def muted(message: str, *, enabled: bool) -> str:
    return paint(message, DIM, enabled=enabled)


def _section(title: str, *, enabled: bool) -> str:
    return paint(title, BOLD, CYAN, enabled=enabled)


def _meta(label: str, value: str, *, enabled: bool) -> str:
    return f"{muted(label, enabled=enabled)}  {value}"


def _field(label: str, value: object, *, enabled: bool) -> str:
    return f"{paint(label, BOLD, CYAN, enabled=enabled)}  {value}"


def _list_items(values: list[str], *, enabled: bool) -> list[str]:
    if not values:
        return [f"  {muted('(none)', enabled=enabled)}"]
    return [f"  {paint('•', CYAN, enabled=enabled)} {value}" for value in values]


def _next_lines(message: str, *, enabled: bool) -> list[str]:
    return ["", _section("Next", enabled=enabled), f"  {paint('›', CYAN, enabled=enabled)} {message}"]


def _tone_line(message: str, tone: str, *, enabled: bool) -> str:
    if tone == "success":
        return success(message, enabled=enabled)
    if tone == "warning":
        return warning(message, enabled=enabled)
    if tone == "failure":
        return failure(message, enabled=enabled)
    return info(message, enabled=enabled)


def render_command_summary(
    label: str,
    headline: str,
    *,
    tone: str,
    details: list[tuple[str, str]] | None = None,
    next_action: str | None = None,
    enabled: bool,
) -> str:
    lines = [header(label, enabled=enabled), _tone_line(headline, tone, enabled=enabled)]
    if details:
        lines.append("")
        lines.extend(_field(key, value, enabled=enabled) for key, value in details)
    if next_action:
        lines.extend(_next_lines(next_action, enabled=enabled))
    return "\n".join(lines) + "\n"


def _verification_value(state: str, *, enabled: bool) -> str:
    if state == "passed":
        return paint(state, GREEN, enabled=enabled)
    if state in {"failed", "blocked"}:
        return paint(state, RED, enabled=enabled)
    if state in {"stale", "not run"}:
        return paint(state, YELLOW, enabled=enabled)
    return muted(state, enabled=enabled)


def render_status_ui(status: dict[str, object], *, enabled: bool) -> str:
    lines = [header("status", enabled=enabled)]
    session_status = str(status["session_status"])
    if session_status != "active":
        if session_status == "finished":
            lines.append(
                success(f"Session: {session_status}", enabled=True)
                if enabled
                else warning(f"Session: {session_status}", enabled=False)
            )
        elif session_status == "aborted":
            lines.append(warning(f"Session: {session_status}", enabled=enabled))
        else:
            lines.append(info(f"Session: {session_status}", enabled=enabled))
        project = status.get("project")
        objective = status.get("objective")
        details: list[str] = []
        if project:
            details.append(_field("project", str(project), enabled=enabled))
        if objective:
            details.append(_field("objective", str(objective), enabled=enabled))
        if details:
            lines.extend(["", *details])
        lines.extend(_next_lines(str(status["next"]), enabled=enabled))
        return "\n".join(lines) + "\n"

    verification = str(status["verification"])
    lines.append(
        failure("Session blocked", enabled=enabled)
        if verification == "blocked"
        else success("Session active", enabled=enabled)
    )
    lines.extend(
        [
            "",
            _field("project", str(status["project"]), enabled=enabled),
            _field("objective", str(status["objective"]), enabled=enabled),
            _field("verification", _verification_value(verification, enabled=enabled), enabled=enabled),
            "",
            _section("Branches", enabled=enabled),
            _field("implementation", str(status["implementation_branch"]), enabled=enabled),
            _meta("current", str(status.get("current_branch", "unavailable")), enabled=enabled),
            "",
            _section("Repository", enabled=enabled),
            _meta("baseline HEAD", str(status["baseline_head"])[:12], enabled=enabled),
            _meta("handoff", str(status["handoff_path"]), enabled=enabled),
            _meta("worktree", "clean" if status.get("worktree_clean", False) else "changed", enabled=enabled),
        ]
    )
    commit_count = status.get("commit_count")
    commit_label = "unknown" if commit_count is None else str(commit_count)
    lines.append(_meta("commits", f"{commit_label} / {status['max_commits']}", enabled=enabled))

    blocked_reason = status.get("blocked_reason")
    if blocked_reason:
        lines.extend(["", _section("Blocked", enabled=enabled), f"  {failure(str(blocked_reason), enabled=enabled)}"])

    lines.extend(_next_lines(str(status["next"]), enabled=enabled))
    return "\n".join(lines) + "\n"


def render_verification_ui(verification: dict[str, Any], *, enabled: bool) -> str:
    lines = [header("verify", enabled=enabled), _section("Steps", enabled=enabled)]
    steps = verification.get("steps", [])
    if not steps:
        lines.extend(_list_items([], enabled=enabled))
    else:
        for step in steps:
            message = str(step["name"]) + (": " + step["message"] if step.get("message") else "")
            lines.append(f"  {success(message, enabled=enabled) if step.get('passed', step.get('status') == 'PASS') else failure(message, enabled=enabled)}")

    passed = bool(verification.get("passed"))
    lines.extend(
        [
            "",
            _section("Result", enabled=enabled),
            f"  {success('Structural verification passed', enabled=enabled) if passed else failure('Structural verification failed', enabled=enabled)}",
        ]
    )
    if passed:
        lines.append(f"  {muted('Bound to the current Git-visible state.', enabled=enabled)}")
        lines.extend(
            _next_lines(
                "Review the exact structurally verified candidate, confirm external CI, then run `ekzd finish --accept` if you approve it.",
                enabled=enabled,
            )
        )
    else:
        lines.extend(_next_lines("Fix the reported structural failure and rerun `ekzd verify`.", enabled=enabled))
    return "\n".join(lines) + "\n"
