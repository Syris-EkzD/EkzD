from __future__ import annotations

import json
import subprocess
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CONFIG_RELATIVE = Path(".ekzd/project.toml")
STATE_RELATIVE = Path(".ekzd/session.json")
SCHEMA_VERSION = 1


class HarnessError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise HarnessError(f"git {' '.join(args)} failed: {detail}")
    return result.stdout.strip()


def find_root(start: Path | None = None) -> Path:
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    raise HarnessError("EkzD must run inside a Git repository.")


def _string_list(value: Any, label: str, *, require_nonempty: bool = False) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise HarnessError(f"{label} must be a list of non-empty strings.")
    if require_nonempty and not value:
        raise HarnessError(f"{label} must contain at least one entry.")
    return value


def _safe_relative_path(value: str, label: str) -> Path:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise HarnessError(f"{label} must stay inside the project: {value}")
    return path


def validate_config(data: dict[str, Any], root: Path, *, ready: bool = True) -> dict[str, Any]:
    if data.get("schema_version") != SCHEMA_VERSION:
        raise HarnessError(f"schema_version must be {SCHEMA_VERSION}.")

    project = data.get("project")
    if not isinstance(project, dict) or not isinstance(project.get("name"), str) or not project["name"].strip():
        raise HarnessError("project.name must be a non-empty string.")

    sources = data.get("sources", {})
    if not isinstance(sources, dict):
        raise HarnessError("sources must be a table.")
    source_paths = _string_list(sources.get("paths", []), "sources.paths")
    for raw in source_paths:
        path = _safe_relative_path(raw, "source path")
        if ready and not (root / path).is_file():
            raise HarnessError(f"Configured source does not exist as a file: {raw}")

    scope = data.get("scope", {})
    if not isinstance(scope, dict):
        raise HarnessError("scope must be a table.")
    _string_list(scope.get("include", []), "scope.include")
    _string_list(scope.get("exclude", []), "scope.exclude")
    _string_list(scope.get("constraints", []), "scope.constraints")

    authority = data.get("authority", {})
    if not isinstance(authority, dict):
        raise HarnessError("authority must be a table.")
    _string_list(authority.get("may", []), "authority.may")
    _string_list(authority.get("requires_approval", []), "authority.requires_approval")
    _string_list(authority.get("may_not", []), "authority.may_not")

    acceptance = data.get("acceptance", {})
    if not isinstance(acceptance, dict):
        raise HarnessError("acceptance must be a table.")
    _string_list(
        acceptance.get("criteria", []),
        "acceptance.criteria",
        require_nonempty=ready,
    )

    verification = data.get("verification", {})
    if not isinstance(verification, dict):
        raise HarnessError("verification must be a table.")
    steps = verification.get("steps", [])
    if not isinstance(steps, list):
        raise HarnessError("verification.steps must be a list.")
    if ready and not steps:
        raise HarnessError("verification.steps must contain at least one step.")
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            raise HarnessError(f"verification.steps[{index}] must be a table.")
        if not isinstance(step.get("name"), str) or not step["name"].strip():
            raise HarnessError(f"verification.steps[{index}].name must be non-empty.")
        command = step.get("command")
        if not isinstance(command, list) or not command or any(
            not isinstance(part, str) or not part for part in command
        ):
            raise HarnessError(f"verification.steps[{index}].command must be a non-empty string array.")
        cwd = step.get("cwd", ".")
        if not isinstance(cwd, str) or not cwd.strip():
            raise HarnessError(f"verification.steps[{index}].cwd must be a non-empty string.")
        _safe_relative_path(cwd, f"verification.steps[{index}].cwd")
        timeout = step.get("timeout_seconds", 600)
        if not isinstance(timeout, int) or timeout < 1 or timeout > 3600:
            raise HarnessError(
                f"verification.steps[{index}].timeout_seconds must be between 1 and 3600."
            )

    return data


def load_config(root: Path, *, ready: bool = True) -> dict[str, Any]:
    path = root / CONFIG_RELATIVE
    if not path.is_file():
        raise HarnessError("Missing .ekzd/project.toml. Run `ekzd init` first.")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise HarnessError(f"Unable to read {CONFIG_RELATIVE}: {exc}") from exc
    return validate_config(data, root, ready=ready)


def read_state(root: Path) -> dict[str, Any] | None:
    path = root / STATE_RELATIVE
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise HarnessError(f"Unable to read {STATE_RELATIVE}: {exc}") from exc
    if not isinstance(data, dict):
        raise HarnessError(f"{STATE_RELATIVE} must contain a JSON object.")
    return data


def write_state(root: Path, state: dict[str, Any]) -> None:
    path = root / STATE_RELATIVE
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp.replace(path)


def git_state(root: Path) -> dict[str, Any]:
    return {
        "head": run_git(root, "rev-parse", "HEAD"),
        "branch": run_git(root, "branch", "--show-current") or "(detached)",
        "status": run_git(root, "status", "--porcelain=v1", "--untracked-files=all").splitlines(),
    }


def init_project(root: Path, name: str | None = None) -> Path:
    path = root / CONFIG_RELATIVE
    if path.exists():
        raise HarnessError(f"{CONFIG_RELATIVE} already exists.")
    path.parent.mkdir(parents=True, exist_ok=True)
    project_name = (name or root.name).strip()
    if not project_name:
        raise HarnessError("Project name cannot be empty.")
    content = (
        'schema_version = 1\n\n'
        '[project]\n'
        f'name = {json.dumps(project_name)}\n\n'
        '[sources]\npaths = []\n\n'
        '[scope]\ninclude = []\nexclude = []\nconstraints = []\n\n'
        '[authority]\nmay = []\nrequires_approval = []\nmay_not = []\n\n'
        '[acceptance]\ncriteria = []\n\n'
        '[verification]\nsteps = []\n'
    )
    path.write_text(content, encoding="utf-8")

    gitignore = root / ".gitignore"
    ignore_line = ".ekzd/session.json"
    existing = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    lines = existing.splitlines()
    if ignore_line not in lines:
        separator = "" if not existing or existing.endswith("\n") else "\n"
        gitignore.write_text(existing + separator + ignore_line + "\n", encoding="utf-8")
    return path


def start_session(root: Path, objective: str) -> dict[str, Any]:
    objective = objective.strip()
    if not objective:
        raise HarnessError("Objective cannot be empty.")
    config = load_config(root, ready=True)
    existing = read_state(root)
    if existing and existing.get("status") == "active":
        raise HarnessError("An active EkzD session already exists. Finish it before starting another.")

    state = {
        "schema_version": SCHEMA_VERSION,
        "status": "active",
        "objective": objective,
        "project": config["project"]["name"],
        "started_at": utc_now(),
        "start_git": git_state(root),
        "handoff": {"done": [], "next": []},
        "verification": None,
        "acceptance": None,
    }
    write_state(root, state)
    return state


def build_context(root: Path) -> dict[str, Any]:
    config = load_config(root, ready=True)
    state = read_state(root)
    return {
        "schema_version": SCHEMA_VERSION,
        "project": config["project"],
        "objective": state.get("objective") if state else None,
        "session_status": state.get("status") if state else None,
        "sources": config.get("sources", {}),
        "scope": config.get("scope", {}),
        "authority": config.get("authority", {}),
        "acceptance": config.get("acceptance", {}),
        "verification": config.get("verification", {}),
        "git": git_state(root),
    }


def render_context(context: dict[str, Any]) -> str:
    lines = [
        f"# EkzD Context — {context['project']['name']}",
        "",
        f"Objective: {context['objective'] or '(no active session)'}",
        f"Session: {context['session_status'] or 'none'}",
        "",
        "## Git",
        f"- Branch: {context['git']['branch']}",
        f"- HEAD: {context['git']['head']}",
        f"- Working tree entries: {len(context['git']['status'])}",
    ]
    for title, key in (
        ("Sources", "sources"),
        ("Scope", "scope"),
        ("Authority", "authority"),
        ("Acceptance", "acceptance"),
    ):
        lines.extend(["", f"## {title}", json.dumps(context[key], indent=2, sort_keys=True)])
    return "\n".join(lines) + "\n"
