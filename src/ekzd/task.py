from __future__ import annotations

import hashlib
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .core import DEFAULT_MAX_COMMITS, HarnessError

TASK_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class TaskVerificationStep:
    name: str
    command: tuple[str, ...]
    cwd: str = "."
    timeout_seconds: int = 600


@dataclass(frozen=True)
class TaskManifest:
    path: Path
    sha256: str
    objective: str
    baseline: str
    implementation_branch: str
    include: tuple[str, ...]
    exclude: tuple[str, ...]
    acceptance_criteria: tuple[str, ...]
    max_commits: int
    repository: str | None
    verification_steps: tuple[TaskVerificationStep, ...]


def _string_list(value: Any, label: str, *, require_nonempty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise HarnessError(f"{label} must be a list of non-empty strings.")
    if require_nonempty and not value:
        raise HarnessError(f"{label} must contain at least one entry.")
    return tuple(value)


def _safe_cwd(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HarnessError(f"{label} must be a non-empty string.")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise HarnessError(f"{label} must stay inside the project: {value}")
    return value


def _verification_steps(value: Any) -> tuple[TaskVerificationStep, ...]:
    if value is None:
        return ()
    if not isinstance(value, dict):
        raise HarnessError("verification must be a table.")
    steps = value.get("steps", [])
    if not isinstance(steps, list):
        raise HarnessError("verification.steps must be a list.")
    parsed: list[TaskVerificationStep] = []
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            raise HarnessError(f"verification.steps[{index}] must be a table.")
        name = step.get("name")
        if not isinstance(name, str) or not name.strip():
            raise HarnessError(f"verification.steps[{index}].name must be non-empty.")
        command = step.get("command")
        if not isinstance(command, list) or not command or any(not isinstance(part, str) or not part for part in command):
            raise HarnessError(f"verification.steps[{index}].command must be a non-empty string array.")
        cwd = _safe_cwd(step.get("cwd", "."), f"verification.steps[{index}].cwd")
        timeout = step.get("timeout_seconds", 600)
        if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout < 1 or timeout > 3600:
            raise HarnessError(
                f"verification.steps[{index}].timeout_seconds must be an integer between 1 and 3600."
            )
        parsed.append(TaskVerificationStep(name=name, command=tuple(command), cwd=cwd, timeout_seconds=timeout))
    return tuple(parsed)


def task_identity(path: Path) -> dict[str, str | None]:
    resolved = path.expanduser().resolve()
    try:
        digest = hashlib.sha256(resolved.read_bytes()).hexdigest()
    except OSError:
        digest = None
    return {"path": str(resolved), "sha256": digest}


def load_task_manifest(path: Path) -> TaskManifest:
    resolved = path.expanduser().resolve()
    try:
        raw = resolved.read_bytes()
    except OSError as exc:
        raise HarnessError(f"Unable to read task manifest {resolved}: {exc}") from exc
    digest = hashlib.sha256(raw).hexdigest()
    try:
        text = raw.decode("utf-8")
        data = tomllib.loads(text)
    except (UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise HarnessError(f"Unable to parse task manifest {resolved}: {exc}") from exc
    if not isinstance(data, dict):
        raise HarnessError("Task manifest must contain a TOML document.")

    schema_version = data.get("schema_version")
    if isinstance(schema_version, bool) or not isinstance(schema_version, int) or schema_version != TASK_SCHEMA_VERSION:
        raise HarnessError(f"task schema_version must be integer {TASK_SCHEMA_VERSION}.")

    objective = data.get("objective")
    if not isinstance(objective, str) or not objective.strip():
        raise HarnessError("task objective must be a non-empty string.")

    baseline = data.get("baseline")
    if not isinstance(baseline, str) or len(baseline) != 40:
        raise HarnessError("task baseline must be an exact 40-character Git commit SHA.")
    try:
        int(baseline, 16)
    except ValueError as exc:
        raise HarnessError("task baseline must be an exact 40-character Git commit SHA.") from exc
    baseline = baseline.lower()

    branch = data.get("implementation_branch")
    if not isinstance(branch, str) or not branch.strip():
        raise HarnessError("task implementation_branch must be a non-empty string.")

    scope = data.get("scope")
    if not isinstance(scope, dict):
        raise HarnessError("task scope must be a table.")
    include = _string_list(scope.get("include", []), "task scope.include", require_nonempty=True)
    exclude = _string_list(scope.get("exclude", []), "task scope.exclude")

    acceptance = data.get("acceptance")
    if not isinstance(acceptance, dict):
        raise HarnessError("task acceptance must be a table.")
    criteria = _string_list(
        acceptance.get("criteria", []), "task acceptance.criteria", require_nonempty=True
    )

    max_commits = data.get("max_commits")
    if (
        isinstance(max_commits, bool)
        or not isinstance(max_commits, int)
        or max_commits < 1
        or max_commits > DEFAULT_MAX_COMMITS
    ):
        raise HarnessError(
            f"task max_commits must be an integer between 1 and {DEFAULT_MAX_COMMITS}."
        )

    repository = data.get("repository")
    if repository is not None and (not isinstance(repository, str) or not repository.strip()):
        raise HarnessError("task repository must be a non-empty string when provided.")

    return TaskManifest(
        path=resolved,
        sha256=digest,
        objective=objective,
        baseline=baseline,
        implementation_branch=branch,
        include=include,
        exclude=exclude,
        acceptance_criteria=criteria,
        max_commits=max_commits,
        repository=repository,
        verification_steps=_verification_steps(data.get("verification")),
    )
