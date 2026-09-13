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

INTERACTIVE_INTRO = "EkzD guides a scoped development task through implementation, verification, and explicit acceptance."
IDLE_HEADLINE = "Ready for a new task"
IDLE_PURPOSE = "Scopes development work, verifies the result, and gates explicit acceptance."


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


def _semantic_value_codes(kind: str, value: str) -> tuple[str, ...]:
    if kind == "success":
        return (GREEN,)
    if kind == "warning":
        return (YELLOW,)
    if kind == "failure":
        return (RED,)
    normalized = value.lower()
    if kind in {"session", "state", "result"}:
        if normalized in {"active", "finished", "accepted", "passed", "clean"}:
            return (GREEN,)
        if normalized in {"aborted", "stale", "not run", "not accepted"}:
            return (YELLOW,)
        if normalized in {"failed", "blocked", "error"}:
            return (RED,)
    if kind == "verification":
        if normalized == "passed":
            return (GREEN,)
        if normalized in {"failed", "blocked", "error"}:
            return (RED,)
        if normalized in {"stale", "not run"}:
            return (YELLOW,)
    return ()


def _interactive_field(label: str, value: object, *, enabled: bool, kind: str = "neutral") -> str:
    text = str(value)
    rendered = paint(text, *_semantic_value_codes(kind, text), enabled=enabled)
    return f"{paint(label, BOLD, CYAN, enabled=enabled)}  {rendered}"


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
        lines.extend(_interactive_field(key, value, enabled=enabled) for key, value in details)
    if next_action:
        lines.extend(_next_lines(next_action, enabled=enabled))
    return "\n".join(lines) + "\n"


def render_handoff_ui(handoff: dict[str, object], *, enabled: bool) -> str:
    lines = [header("handoff", enabled=enabled), success("Handoff updated", enabled=enabled)]
    rendered_any = False
    for label in ("done", "next"):
        values = handoff.get(label, [])
        if not isinstance(values, list) or not values:
            continue
        if not rendered_any:
            lines.append("")
            rendered_any = True
        lines.append(_section(label.capitalize(), enabled=enabled))
        lines.extend(_list_items([str(value) for value in values], enabled=enabled))
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
            details.append(_interactive_field("project", str(project), enabled=enabled))
        if objective:
            details.append(_interactive_field("objective", str(objective), enabled=enabled))
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
            _interactive_field("project", str(status["project"]), enabled=enabled),
            _interactive_field("objective", str(status["objective"]), enabled=enabled),
            _interactive_field(
                "verification",
                _verification_value(verification, enabled=enabled),
                enabled=enabled,
            ),
            "",
            _section("Branches", enabled=enabled),
            _meta("baseline", str(status["baseline_branch"]), enabled=enabled),
            _interactive_field("implementation", str(status["implementation_branch"]), enabled=enabled),
            _meta("current", str(status["current_branch"]), enabled=enabled),
            "",
            _section("Repository", enabled=enabled),
            _meta("baseline HEAD", str(status["baseline_head"])[:12], enabled=enabled),
            _meta("worktree", "clean" if status["worktree_clean"] else "changed", enabled=enabled),
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


def _previous_task_fields(status: dict[str, object]) -> list[tuple[str, object, str]]:
    outcome = str(status.get("session_status") or "none")
    objective = status.get("objective")
    if outcome not in {"finished", "aborted"} or not objective:
        return []

    fields: list[tuple[str, object, str]] = [
        ("State", outcome, "state"),
        ("Objective", objective, "neutral"),
    ]
    branch = status.get("previous_implementation_branch")
    verification = status.get("previous_verification")
    result = status.get("previous_result")
    if branch:
        fields.append(("Implementation branch", branch, "neutral"))
    if verification:
        fields.append(("Verification", verification, "verification"))
    if result:
        fields.append(("Result", result, "result"))
    return fields


def _previous_session_lines(status: dict[str, object], *, enabled: bool) -> list[str]:
    fields = _previous_task_fields(status)
    if not fields:
        return []
    lines = ["", _section("Previous task", enabled=enabled)]
    lines.extend(f"  {_interactive_field(label, value, enabled=enabled, kind=kind)}" for label, value, kind in fields)
    return lines


def render_contract_review(
    review: dict[str, object],
    objective: str,
    implementation_branch: str,
    *,
    enabled: bool,
) -> str:
    scope_summary = (
        f"{review['scope_include_count']} included · {review['scope_exclude_count']} excluded · "
        f"{review['constraint_count']} constraints"
    )
    lines = [
        header("contract review", enabled=enabled),
        _section("Review committed contract", enabled=enabled),
        muted("Confirm that the committed EkzD contract matches this proposed task.", enabled=enabled),
        "",
        _interactive_field("Objective", objective, enabled=enabled),
        _interactive_field("Implementation branch", implementation_branch, enabled=enabled),
        _interactive_field("Project", review["project"], enabled=enabled),
        _interactive_field("Sources", f"{review['sources_count']} paths", enabled=enabled),
        _interactive_field("Scope", scope_summary, enabled=enabled),
        _interactive_field("Verification", f"{review['verification_count']} steps", enabled=enabled),
        _interactive_field("Max commits", review["max_commits"], enabled=enabled),
        "",
        _section("Decision", enabled=enabled),
        "  1. Confirm and start",
        "  2. Update contract first",
        "  3. Cancel",
    ]
    return "\n".join(lines) + "\n"


def render_interactive_idle_home(
    project: str,
    status: dict[str, object],
    actions: list[str],
    *,
    enabled: bool,
) -> str:
    lines = [
        header("interactive", enabled=enabled),
        IDLE_HEADLINE,
        muted(IDLE_PURPOSE, enabled=enabled),
        "",
        _interactive_field("Project", project, enabled=enabled),
        _interactive_field("State", IDLE_HEADLINE, enabled=enabled),
    ]
    lines.extend(_next_lines(str(status.get("next") or "Start a new task."), enabled=enabled))
    lines.extend(_previous_session_lines(status, enabled=enabled))
    lines.extend(["", _section("Actions", enabled=enabled)])
    lines.extend(f"  {index}. {label}" for index, label in enumerate(actions, start=1))
    return "\n".join(lines) + "\n"


def render_interactive_home(
    project: str,
    status: dict[str, object],
    actions: list[str],
    *,
    enabled: bool,
) -> str:
    if status.get("session_status") != "active":
        return render_interactive_idle_home(project, status, actions, enabled=enabled)

    session_status = str(status.get("session_status") or "none")
    lines = [
        header("interactive", enabled=enabled),
        INTERACTIVE_INTRO,
        "",
        _interactive_field("Project", project, enabled=enabled),
        _interactive_field("Session", session_status, enabled=enabled, kind="session"),
    ]
    objective = status.get("objective")
    if objective:
        lines.append(_interactive_field("Objective", objective, enabled=enabled))
    verification = status.get("verification")
    if verification is not None:
        lines.append(_interactive_field("Verification", verification, enabled=enabled, kind="verification"))
    lines.extend(_next_lines(str(status.get("next") or "Choose an action below."), enabled=enabled))
    lines.extend(["", _section("Actions", enabled=enabled)])
    lines.extend(f"  {index}. {label}" for index, label in enumerate(actions, start=1))
    return "\n".join(lines) + "\n"


def render_verification_ui(verification: dict[str, Any], *, enabled: bool) -> str:
    lines = [header("verify", enabled=enabled), _section("Steps", enabled=enabled)]
    steps = verification.get("steps", [])
    if not steps:
        lines.extend(_list_items([], enabled=enabled))
    else:
        for step in steps:
            message = str(step["name"])
            lines.append(f"  {success(message, enabled=enabled) if step['passed'] else failure(message, enabled=enabled)}")

    passed = bool(verification.get("passed"))
    lines.extend(
        [
            "",
            _section("Result", enabled=enabled),
            f"  {success('Verification passed', enabled=enabled) if passed else failure('Verification failed', enabled=enabled)}",
        ]
    )
    if passed:
        lines.append(f"  {muted('Bound to the current Git-visible state.', enabled=enabled)}")
        lines.extend(
            _next_lines(
                "Review the verified changes, then run `ekzd finish --accept` if you approve them.",
                enabled=enabled,
            )
        )
    else:
        lines.extend(_next_lines("Fix the reported failure and rerun `ekzd verify`.", enabled=enabled))
    return "\n".join(lines) + "\n"


def render_context_ui(context: dict[str, Any], *, enabled: bool) -> str:
    project = context["project"]["name"]
    objective = context.get("objective") or "(no active session)"
    session_status = context.get("session_status") or "none"
    git = context["git"]
    status_entries = git.get("status", [])
    worktree = "clean" if not status_entries else f"{len(status_entries)} changed entr{'y' if len(status_entries) == 1 else 'ies'}"

    status_color = GREEN if session_status in {"active", "finished"} else YELLOW if session_status == "aborted" else DIM
    worktree_color = GREEN if not status_entries else YELLOW

    lines = [
        header(project, enabled=enabled),
        _meta("objective", objective, enabled=enabled),
        _meta("session", paint(session_status, status_color, enabled=enabled), enabled=enabled),
        _meta(
            "git",
            f"{git['branch']} @ {git['head'][:8]} · {paint(worktree, worktree_color, enabled=enabled)}",
            enabled=enabled,
        ),
        "",
        _section("Sources", enabled=enabled),
    ]

    sources = context.get("sources", {}).get("paths", [])
    lines.extend(_list_items(sources, enabled=enabled))

    lines.extend(["", _section("Scope", enabled=enabled)])
    scope = context.get("scope", {})
    lines.append(f"  {muted('include', enabled=enabled)}")
    lines.extend(_list_items(scope.get("include", []), enabled=enabled))
    if scope.get("exclude"):
        lines.append(f"  {muted('exclude', enabled=enabled)}")
        lines.extend(_list_items(scope.get("exclude", []), enabled=enabled))
    if scope.get("constraints"):
        lines.append(f"  {muted('constraints', enabled=enabled)}")
        lines.extend(_list_items(scope.get("constraints", []), enabled=enabled))

    authority = context.get("authority", {})
    lines.extend(["", _section("Authority", enabled=enabled)])
    for label, key in (("may", "may"), ("approval", "requires_approval"), ("may not", "may_not")):
        lines.append(f"  {muted(label, enabled=enabled)}")
        lines.extend(_list_items(authority.get(key, []), enabled=enabled))

    acceptance = context.get("acceptance", {}).get("criteria", [])
    lines.extend(["", _section("Acceptance", enabled=enabled)])
    lines.extend(_list_items(acceptance, enabled=enabled))

    verification = context.get("verification", {}).get("steps", [])
    lines.extend(["", _section("Verification", enabled=enabled)])
    if not verification:
        lines.extend(_list_items([], enabled=enabled))
    else:
        for step in verification:
            command = " ".join(step.get("command", []))
            name = step.get("name", "unnamed")
            lines.append(f"  {paint('•', CYAN, enabled=enabled)} {name} {muted(command, enabled=enabled)}")

    session = context.get("session", {})
    if session:
        lines.extend(["", _section("Session policy", enabled=enabled)])
        lines.append(f"  {muted('max commits', enabled=enabled)}  {session.get('max_commits', '?')}")

    return "\n".join(lines) + "\n"


def _clip_terminal_value(value: object, limit: int) -> str:
    text = " ".join(str(value).split())
    if len(text) <= limit:
        return text
    if limit <= 1:
        return text[:limit]
    return text[: limit - 1] + "…"


def _terminal_value_codes(kind: str, value: str) -> tuple[str, ...]:
    return _semantic_value_codes(kind, value)


def _terminal_card_row(
    label: str,
    value: object,
    *,
    content_width: int,
    enabled: bool,
    kind: str = "neutral",
    label_width: int | None = None,
) -> str:
    if label_width is None:
        label_width = min(12, max(1, content_width // 3))
    else:
        label_width = min(label_width, max(1, content_width // 2))
    value_width = max(1, content_width - label_width - 1)
    label_text = f"{label:<{label_width}}"
    clipped_value = _clip_terminal_value(value, value_width)
    value_text = paint(clipped_value, *_terminal_value_codes(kind, str(value)), enabled=enabled)
    padding = " " * (value_width - len(clipped_value))
    return f"│ {paint(label_text, BOLD, CYAN, enabled=enabled)} {value_text}{padding} │"


def _terminal_card_top(title: str, card_width: int, *, enabled: bool) -> str:
    title_prefix = f"┌─ {title} "
    return (
        f"┌─ {paint(title, BOLD, CYAN, enabled=enabled)} "
        + "─" * max(0, card_width - len(title_prefix) - 1)
        + "┐"
    )


def render_previous_task_card(status: dict[str, object], *, width: int, enabled: bool) -> str:
    fields = _previous_task_fields(status)
    if not fields:
        return ""
    values = {label: (value, kind) for label, value, kind in fields}
    outcome = str(values["State"][0])
    result = str(values.get("Result", (outcome, "result"))[0])
    outcome_kind = "success" if outcome == "finished" else "warning"
    compact_fields: list[tuple[str, object, str]] = [
        ("State / Result", f"{outcome} · {result}", outcome_kind),
        ("Objective", values["Objective"][0], "neutral"),
    ]
    if "Implementation branch" in values:
        compact_fields.append(("Implementation branch", values["Implementation branch"][0], "neutral"))
    if "Verification" in values:
        compact_fields.append(("Verification", values["Verification"][0], "verification"))

    card_width = max(4, width - 1)
    content_width = max(1, card_width - 4)
    lines = [_terminal_card_top("Previous task", card_width, enabled=enabled)]
    lines.extend(
        _terminal_card_row(
            label,
            value,
            content_width=content_width,
            enabled=enabled,
            kind=kind,
            label_width=21,
        )
        for label, value, kind in compact_fields
    )
    lines.append("└" + "─" * max(0, card_width - 2) + "┘")
    return "\n".join(lines) + "\n"


def render_terminal_contract_review(
    review: dict[str, object],
    objective: str,
    implementation_branch: str,
    *,
    width: int,
    enabled: bool,
) -> str:
    card_width = max(4, width - 1)
    content_width = max(1, card_width - 4)
    scope_summary = (
        f"{review['scope_include_count']} included · {review['scope_exclude_count']} excluded · "
        f"{review['constraint_count']} constraints"
    )
    rows = [
        ("Objective", objective, "neutral"),
        ("Implementation branch", implementation_branch, "neutral"),
        ("Project", review["project"], "neutral"),
        ("Sources", f"{review['sources_count']} paths", "neutral"),
        ("Scope", scope_summary, "neutral"),
        ("Verification", f"{review['verification_count']} steps", "neutral"),
        ("Max commits", review["max_commits"], "neutral"),
    ]
    lines = [_terminal_card_top("EkzD · Contract review", card_width, enabled=enabled)]
    lines.extend(
        _terminal_card_row(
            label,
            value,
            content_width=content_width,
            enabled=enabled,
            kind=kind,
            label_width=21,
        )
        for label, value, kind in rows
    )
    lines.append("└" + "─" * max(0, card_width - 2) + "┘")
    return "\n".join(lines) + "\n"


def render_terminal_idle_header(
    project: str,
    status: dict[str, object],
    *,
    width: int,
    enabled: bool,
) -> str:
    card_width = max(4, width - 1)
    content_width = max(1, card_width - 4)
    top = _terminal_card_top("EkzD", card_width, enabled=enabled)
    bottom = "└" + "─" * max(0, card_width - 2) + "┘"
    rows = [
        ("Project", project, "neutral"),
        ("State", IDLE_HEADLINE, "neutral"),
        ("Purpose", IDLE_PURPOSE, "neutral"),
        ("Next", status.get("next") or "Start a new task.", "neutral"),
    ]
    lines = [top]
    lines.extend(
        _terminal_card_row(
            label,
            value,
            content_width=content_width,
            enabled=enabled,
            kind=kind,
        )
        for label, value, kind in rows
    )
    lines.append(bottom)
    previous = render_previous_task_card(status, width=width, enabled=enabled)
    if previous:
        lines.extend(["", *previous.rstrip("\n").splitlines()])
    return "\n".join(lines) + "\n"


def render_terminal_header(
    project: str,
    status: dict[str, object],
    *,
    width: int,
    enabled: bool,
) -> str:
    if status.get("session_status") != "active":
        return render_terminal_idle_header(project, status, width=width, enabled=enabled)

    card_width = max(4, width - 1)
    content_width = max(1, card_width - 4)
    top = _terminal_card_top("EkzD", card_width, enabled=enabled)
    bottom = "└" + "─" * max(0, card_width - 2) + "┘"

    commit_count = status.get("commit_count")
    max_commits = status.get("max_commits")
    commit_label = "?" if commit_count is None else str(commit_count)
    commit_value = "?" if max_commits is None else f"{commit_label} / {max_commits}"
    rows = [
        ("Project", project, "neutral"),
        ("Task", status.get("objective") or "(none)", "neutral"),
        ("Branch", status.get("current_branch") or "(unknown)", "neutral"),
        ("Session", str(status.get("session_status") or "none"), "session"),
        ("Verification", str(status.get("verification") or "not run"), "verification"),
        ("Commits", commit_value, "neutral"),
        ("Next", status.get("next") or "Choose an action below.", "neutral"),
    ]
    lines = [top]
    lines.extend(
        _terminal_card_row(
            label,
            value,
            content_width=content_width,
            enabled=enabled,
            kind=kind,
        )
        for label, value, kind in rows
    )
    lines.append(bottom)
    return "\n".join(lines) + "\n"


def render_terminal_task_details(status: dict[str, object], *, enabled: bool) -> str:
    lines = [_section("Task details", enabled=enabled)]
    objective = status.get("objective")
    if objective:
        lines.extend(["", _section("Objective", enabled=enabled), f"  {objective}"])
    lines.extend(
        [
            "",
            _section("Branches", enabled=enabled),
            _meta("baseline", str(status.get("baseline_branch") or "(unknown)"), enabled=enabled),
            _meta(
                "implementation",
                str(status.get("implementation_branch") or "(unknown)"),
                enabled=enabled,
            ),
            "",
            _section("Repository", enabled=enabled),
            _meta(
                "baseline HEAD",
                str(status.get("baseline_head") or "")[:12] or "(unknown)",
                enabled=enabled,
            ),
            _meta(
                "worktree",
                "clean" if status.get("worktree_clean") else "changed",
                enabled=enabled,
            ),
        ]
    )
    blocked_reason = status.get("blocked_reason")
    if blocked_reason:
        lines.extend(["", _section("Blocked", enabled=enabled), f"  {blocked_reason}"])
    return "\n".join(lines) + "\n"


def render_terminal_actions(actions: list[str], *, enabled: bool) -> str:
    lines = [_section("Actions", enabled=enabled)]
    lines.extend(f"  {index}. {label}" for index, label in enumerate(actions, start=1))
    return "\n".join(lines) + "\n"
