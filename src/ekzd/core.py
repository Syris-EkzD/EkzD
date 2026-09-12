from __future__ import annotations

import fnmatch
import hashlib
import json
import subprocess
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CONFIG_RELATIVE = Path(".ekzd/project.toml")
STATE_RELATIVE = Path(".ekzd/session.json")
SCHEMA_VERSION = 1
DEFAULT_MAX_COMMITS = 3


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


def run_git_bytes(root: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip() or result.stdout.decode("utf-8", errors="replace").strip()
        raise HarnessError(f"git {' '.join(args)} failed: {detail}")
    return result.stdout


def ensure_session_state_untracked(root: Path) -> None:
    if run_git(root, "ls-files", "--", STATE_RELATIVE.as_posix()):
        raise HarnessError("EkzD session state must remain local and untracked.")


def hidden_index_paths(root: Path) -> list[str]:
    hidden: set[str] = set()
    for entry in run_git_bytes(root, "ls-files", "-v", "-z").split(b"\0"):
        if not entry:
            continue
        if len(entry) < 3 or entry[1:2] != b" ":
            raise HarnessError("Unable to parse Git index visibility flags.")
        tag = entry[:1]
        if tag == b"S" or tag.islower():
            hidden.add(entry[2:].decode("utf-8", errors="surrogateescape"))
    return sorted(hidden)


def ensure_index_paths_visible(root: Path) -> None:
    hidden = hidden_index_paths(root)
    if hidden:
        raise HarnessError(
            "Git index flags hide tracked paths from EkzD; clear assume-unchanged/skip-worktree before continuing:\n- "
            + "\n- ".join(hidden)
        )


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
    schema_version = data.get("schema_version")
    if isinstance(schema_version, bool) or not isinstance(schema_version, int) or schema_version != SCHEMA_VERSION:
        raise HarnessError(f"schema_version must be integer {SCHEMA_VERSION}.")

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
    _string_list(scope.get("include", []), "scope.include", require_nonempty=ready)
    _string_list(scope.get("exclude", []), "scope.exclude")
    _string_list(scope.get("constraints", []), "scope.constraints")

    authority = data.get("authority", {})
    if not isinstance(authority, dict):
        raise HarnessError("authority must be a table.")
    _string_list(authority.get("may", []), "authority.may")
    _string_list(authority.get("requires_approval", []), "authority.requires_approval")
    _string_list(authority.get("may_not", []), "authority.may_not")

    session = data.get("session", {})
    if not isinstance(session, dict):
        raise HarnessError("session must be a table.")
    max_commits = session.get("max_commits", DEFAULT_MAX_COMMITS)
    if isinstance(max_commits, bool) or not isinstance(max_commits, int) or max_commits < 1:
        raise HarnessError("session.max_commits must be a positive integer.")
    session["max_commits"] = max_commits
    data["session"] = session

    acceptance = data.get("acceptance", {})
    if not isinstance(acceptance, dict):
        raise HarnessError("acceptance must be a table.")
    _string_list(acceptance.get("criteria", []), "acceptance.criteria", require_nonempty=ready)

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
        if not isinstance(command, list) or not command or any(not isinstance(part, str) or not part for part in command):
            raise HarnessError(f"verification.steps[{index}].command must be a non-empty string array.")
        cwd = step.get("cwd", ".")
        if not isinstance(cwd, str) or not cwd.strip():
            raise HarnessError(f"verification.steps[{index}].cwd must be a non-empty string.")
        _safe_relative_path(cwd, f"verification.steps[{index}].cwd")
        timeout = step.get("timeout_seconds", 600)
        if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout < 1 or timeout > 3600:
            raise HarnessError(f"verification.steps[{index}].timeout_seconds must be an integer between 1 and 3600.")

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
    ensure_session_state_untracked(root)
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
    ensure_session_state_untracked(root)
    path = root / STATE_RELATIVE
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp.replace(path)


def session_state_digest(root: Path) -> str:
    ensure_session_state_untracked(root)
    path = root / STATE_RELATIVE
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise HarnessError(f"Unable to read {STATE_RELATIVE}: {exc}") from exc


def git_state(root: Path) -> dict[str, Any]:
    ensure_index_paths_visible(root)
    return {
        "head": run_git(root, "rev-parse", "HEAD"),
        "branch": run_git(root, "branch", "--show-current") or "(detached)",
        "status": run_git(root, "status", "--porcelain=v1", "--untracked-files=all").splitlines(),
    }


def git_state_fingerprint(root: Path) -> str:
    ensure_index_paths_visible(root)
    digest = hashlib.sha256()
    components = (
        ("head", ("rev-parse", "HEAD")),
        ("branch", ("branch", "--show-current")),
        ("status", ("status", "--porcelain=v1", "--untracked-files=all")),
        ("worktree-diff", ("diff", "--binary", "HEAD")),
        ("index-diff", ("diff", "--binary", "--cached", "HEAD")),
    )
    for label, args in components:
        digest.update(label.encode("utf-8") + b"\0")
        digest.update(run_git_bytes(root, *args))
        digest.update(b"\0")

    untracked = [path for path in run_git_bytes(root, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0") if path]
    for raw_path in sorted(untracked):
        digest.update(b"untracked\0" + raw_path + b"\0")
        path = root / raw_path.decode("utf-8", errors="surrogateescape")
        if path.is_symlink():
            digest.update(b"symlink\0")
            digest.update(str(path.readlink()).encode("utf-8", errors="surrogateescape"))
        elif path.is_file():
            digest.update(b"file\0")
            with path.open("rb") as handle:
                while chunk := handle.read(1024 * 1024):
                    digest.update(chunk)
        else:
            digest.update(b"missing-or-non-file\0")
        digest.update(b"\0")
    return digest.hexdigest()


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
        '[session]\nmax_commits = 3\n\n'
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
        raise HarnessError("An active EkzD session already exists. Finish or abort it before starting another.")

    state = {
        "schema_version": SCHEMA_VERSION,
        "status": "active",
        "objective": objective,
        "project": config["project"]["name"],
        "started_at": utc_now(),
        "start_git": git_state(root),
        "config_digest": config_digest(root),
        "session_policy": {"max_commits": config["session"]["max_commits"]},
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
        "session": config.get("session", {}),
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
        ("Session Policy", "session"),
        ("Sources", "sources"),
        ("Scope", "scope"),
        ("Authority", "authority"),
        ("Acceptance", "acceptance"),
        ("Verification", "verification"),
    ):
        lines.extend(["", f"## {title}", json.dumps(context[key], indent=2, sort_keys=True)])
    return "\n".join(lines) + "\n"


def config_digest(root: Path) -> str:
    return hashlib.sha256((root / CONFIG_RELATIVE).read_bytes()).hexdigest()


def _decode_git_paths(output: bytes) -> set[str]:
    return {raw.decode("utf-8", errors="surrogateescape") for raw in output.split(b"\0") if raw}


def changed_paths(root: Path, *, start_head: str | None = None) -> list[str]:
    ensure_index_paths_visible(root)
    committed: set[str] = set()
    if start_head is not None:
        committed = _decode_git_paths(
            run_git_bytes(
                root,
                "log",
                "-m",
                "--format=",
                "--name-only",
                "--no-renames",
                "-z",
                f"{start_head}..HEAD",
            )
        )
    tracked = _decode_git_paths(run_git_bytes(root, "diff", "--name-only", "--no-renames", "-z"))
    staged = _decode_git_paths(run_git_bytes(root, "diff", "--cached", "--name-only", "--no-renames", "-z"))
    untracked = _decode_git_paths(run_git_bytes(root, "ls-files", "--others", "--exclude-standard", "-z"))
    ignored = {STATE_RELATIVE.as_posix()}
    return sorted(path for path in committed | tracked | staged | untracked if path and path not in ignored)


def _matches_scope(path: str, pattern: str) -> bool:
    normalized = pattern.rstrip("/")
    if pattern.endswith("/"):
        return path == normalized or path.startswith(normalized + "/")
    return fnmatch.fnmatchcase(path, pattern)


def enforce_scope(root: Path, config: dict[str, Any], *, start_head: str | None = None) -> list[str]:
    paths = changed_paths(root, start_head=start_head)
    scope = config.get("scope", {})
    include = scope.get("include", [])
    exclude = scope.get("exclude", [])
    violations: list[str] = []
    for path in paths:
        if not any(_matches_scope(path, pattern) for pattern in include):
            violations.append(f"{path}: outside scope.include")
        if any(_matches_scope(path, pattern) for pattern in exclude):
            violations.append(f"{path}: matches scope.exclude")
    if violations:
        raise HarnessError("Scope violations:\n- " + "\n- ".join(violations))
    return paths


def _active_state(root: Path) -> dict[str, Any]:
    state = read_state(root)
    if not state or state.get("status") != "active":
        raise HarnessError("No active EkzD session.")
    return state


def _session_start_head(state: dict[str, Any]) -> str:
    start_git = state.get("start_git")
    if not isinstance(start_git, dict) or not isinstance(start_git.get("head"), str) or not start_git["head"]:
        raise HarnessError("Active session is missing its starting Git HEAD.")
    return start_git["head"]


def enforce_session_contract(root: Path, state: dict[str, Any]) -> None:
    recorded_digest = state.get("config_digest")
    if not isinstance(recorded_digest, str) or not recorded_digest:
        raise HarnessError("Active session is missing its starting project configuration digest.")
    if recorded_digest != config_digest(root):
        raise HarnessError("Project harness configuration changed after the session started; abort this session and start a fresh one.")


def session_commit_count(root: Path, start_head: str) -> int:
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", start_head, "HEAD"],
        cwd=root,
        capture_output=True,
    )
    if ancestor.returncode == 1:
        raise HarnessError("Session history diverged from its starting commit; start a fresh session.")
    if ancestor.returncode != 0:
        detail = ancestor.stderr.decode("utf-8", errors="replace").strip() or ancestor.stdout.decode("utf-8", errors="replace").strip()
        raise HarnessError(f"Unable to validate session Git history: {detail}")
    try:
        return int(run_git(root, "rev-list", "--count", f"{start_head}..HEAD"))
    except ValueError as exc:
        raise HarnessError("Unable to count commits in the active session.") from exc


def enforce_session_budget(root: Path, config: dict[str, Any], state: dict[str, Any]) -> dict[str, int]:
    max_commits = config["session"]["max_commits"]
    recorded_policy = state.get("session_policy")
    if isinstance(recorded_policy, dict) and recorded_policy.get("max_commits") != max_commits:
        raise HarnessError("Session commit budget changed after the session started; start a fresh session to use the new budget.")
    commit_count = session_commit_count(root, _session_start_head(state))
    if commit_count > max_commits:
        raise HarnessError(
            f"Session commit budget exceeded: {commit_count} commits > {max_commits} allowed. "
            "Abort this session and start a fresh session with an appropriate configured budget."
        )
    return {"commit_count": commit_count, "max_commits": max_commits}


def verify_session(root: Path) -> dict[str, Any]:
    config = load_config(root, ready=True)
    state = _active_state(root)
    state_digest = session_state_digest(root)
    enforce_session_contract(root, state)
    budget = enforce_session_budget(root, config, state)
    paths = enforce_scope(root, config, start_head=_session_start_head(state))
    results: list[dict[str, Any]] = []
    for step in config["verification"]["steps"]:
        cwd = (root / step.get("cwd", ".")).resolve()
        if root not in (cwd, *cwd.parents) or not cwd.is_dir():
            raise HarnessError(f"Verification cwd is invalid: {step.get('cwd', '.')}")
        try:
            process = subprocess.run(step["command"], cwd=cwd, text=True, capture_output=True, timeout=step.get("timeout_seconds", 600))
            result = {
                "name": step["name"],
                "command": step["command"],
                "cwd": str(cwd.relative_to(root)),
                "exit_code": process.returncode,
                "stdout": process.stdout[-4000:],
                "stderr": process.stderr[-4000:],
                "passed": process.returncode == 0,
            }
        except subprocess.TimeoutExpired as exc:
            result = {
                "name": step["name"],
                "command": step["command"],
                "cwd": str(cwd.relative_to(root)),
                "exit_code": None,
                "stdout": (exc.stdout or "")[-4000:] if isinstance(exc.stdout, str) else "",
                "stderr": (exc.stderr or "")[-4000:] if isinstance(exc.stderr, str) else "",
                "passed": False,
                "timed_out": True,
            }
        except OSError as exc:
            result = {
                "name": step["name"],
                "command": step["command"],
                "cwd": str(cwd.relative_to(root)),
                "exit_code": None,
                "stdout": "",
                "stderr": str(exc)[-4000:],
                "passed": False,
                "launch_error": True,
            }
        results.append(result)
        if not result["passed"]:
            break

    config = load_config(root, ready=True)
    current_state = _active_state(root)
    if current_state != state or session_state_digest(root) != state_digest:
        raise HarnessError("EkzD session state changed during verification; verification cannot trust a modified session contract.")
    enforce_session_contract(root, state)
    budget = enforce_session_budget(root, config, state)
    paths = enforce_scope(root, config, start_head=_session_start_head(state))
    passed = len(results) == len(config["verification"]["steps"]) and all(item["passed"] for item in results)
    verification = {
        "verified_at": utc_now(),
        "passed": passed,
        "config_digest": config_digest(root),
        "git": git_state(root),
        "state_fingerprint": git_state_fingerprint(root),
        "changed_paths": paths,
        "session_budget": budget,
        "steps": results,
    }
    state["verification"] = verification
    state["acceptance"] = None
    write_state(root, state)
    return verification


def update_handoff(root: Path, *, done: list[str], next_items: list[str]) -> dict[str, Any]:
    state = _active_state(root)
    handoff = state.setdefault("handoff", {"done": [], "next": []})
    handoff["done"].extend(item.strip() for item in done if item.strip())
    handoff["next"].extend(item.strip() for item in next_items if item.strip())
    handoff["updated_at"] = utc_now()
    write_state(root, state)
    return handoff


def abort_session(root: Path) -> dict[str, Any]:
    state = _active_state(root)
    state["status"] = "aborted"
    state["aborted_at"] = utc_now()
    state["acceptance"] = None
    write_state(root, state)
    return state


def finish_session(root: Path, *, accept: bool) -> dict[str, Any]:
    if not accept:
        raise HarnessError("Acceptance requires explicit `ekzd finish --accept` approval.")
    config = load_config(root, ready=True)
    state = _active_state(root)
    enforce_session_contract(root, state)
    verification = state.get("verification")
    if not isinstance(verification, dict) or not verification.get("passed"):
        raise HarnessError("Acceptance blocked: verification has not passed.")
    if verification.get("config_digest") != config_digest(root):
        raise HarnessError("Acceptance blocked: project configuration changed after verification.")
    current_git = git_state(root)
    if verification.get("git") != current_git:
        raise HarnessError("Acceptance blocked: Git/worktree state changed after verification.")
    if verification.get("state_fingerprint") != git_state_fingerprint(root):
        raise HarnessError("Acceptance blocked: Git-visible file contents changed after verification.")
    enforce_session_budget(root, config, state)
    enforce_scope(root, config, start_head=_session_start_head(state))
    acceptance = {
        "accepted_at": utc_now(),
        "criteria": list(config["acceptance"]["criteria"]),
        "explicit_approval": True,
        "verification_bound": True,
    }
    state["acceptance"] = acceptance
    state["status"] = "finished"
    state["finished_at"] = acceptance["accepted_at"]
    write_state(root, state)
    return state
