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


def _list_items(values: list[str], *, enabled: bool) -> list[str]:
    if not values:
        return [f"  {muted('(none)', enabled=enabled)}"]
    return [f"  {paint('•', CYAN, enabled=enabled)} {value}" for value in values]


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
