from __future__ import annotations

import shutil
import subprocess
import tomllib
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from . import __version__
from .candidate import capture_candidate
from .check import check_result, empty_worker_result, finalize_worker_result
from .core import (
    CONFIG_RELATIVE,
    PROTECTED_SESSION_HISTORY_PATHS,
    STATE_RELATIVE,
    HarnessError,
    _matches_scope,
    changed_paths,
    ensure_git_visibility_supported,
    run_git,
    run_git_bytes,
    session_commit_count,
    session_history_paths,
    validate_config,
)
from .evaluation import EvaluationResult, StepEvidence, VerificationStep, evaluate_candidate
from .identity import BuildIdentityUnavailable, runtime_identity
from .task import TaskManifest, TaskVerificationStep, load_task_manifest, task_identity
from .workflow import _origin_remote, _sanitize_repository_identifier


def _add(result: dict[str, Any], name: str, status: str, message: str, *, required: bool = True, **details: Any) -> None:
    result["checks"].append(check_result(name, status, message, required=required, **details))


def _baseline_project_config(root: Path, baseline: str) -> dict[str, Any]:
    entry = run_git_bytes(root, "ls-tree", "-z", baseline, "--", CONFIG_RELATIVE.as_posix())
    if not entry.startswith((b"100644 blob ", b"100755 blob ")):
        raise HarnessError("Baseline project configuration must be a regular tracked file.")
    raw = run_git_bytes(root, "show", f"{baseline}:{CONFIG_RELATIVE.as_posix()}")
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise HarnessError(f"Unable to parse baseline {CONFIG_RELATIVE}: {exc}") from exc
    return validate_config(data, root, ready=True, source_revision=baseline)


def _worktree_binding(root: Path) -> dict[str, Any]:
    return capture_candidate(root).binding()


def _scope_violations(paths: list[str], task: TaskManifest) -> list[str]:
    violations: list[str] = []
    for path in paths:
        if not any(_matches_scope(path, pattern) for pattern in task.include):
            violations.append(f"{path}: outside task scope.include")
        if any(_matches_scope(path, pattern) for pattern in task.exclude):
            violations.append(f"{path}: matches task scope.exclude")
    return violations


def _hostless_path_has_credential_shape(scheme: str, path: str) -> bool:
    head = path.lstrip("/").split("/", 1)[0]
    if "@" not in head:
        return False
    userinfo, host = head.rsplit("@", 1)
    if not userinfo or not host:
        return False
    if scheme == "file":
        return ":" in userinfo
    return True


def _safe_repository_identifier(value: str) -> str:
    raw = value.strip()
    try:
        parsed = urlsplit(raw)
        if "://" in raw:
            if not parsed.scheme:
                raise ValueError
            if parsed.netloc:
                if not parsed.hostname:
                    raise ValueError
                parsed.port
            elif _hostless_path_has_credential_shape(parsed.scheme, parsed.path):
                raise ValueError
        elif "@" in raw:
            if raw.count("@") != 1:
                raise ValueError
            user_host, separator, path = raw.partition(":")
            user, at, host = user_host.partition("@")
            if not separator or not at or not user or not host or not path or "@" in path:
                raise ValueError
        sanitized = _sanitize_repository_identifier(raw)
        sanitized_parsed = urlsplit(sanitized)
        if (
            not sanitized
            or sanitized_parsed.username is not None
            or sanitized_parsed.password is not None
            or (sanitized_parsed.netloc and "@" in sanitized_parsed.netloc)
            or (
                "://" in sanitized
                and (
                    not sanitized_parsed.scheme
                    or (
                        not sanitized_parsed.netloc
                        and _hostless_path_has_credential_shape(sanitized_parsed.scheme, sanitized_parsed.path)
                    )
                )
            )
        ):
            raise ValueError
        return sanitized
    except (ValueError, TypeError) as exc:
        raise HarnessError("Repository identity cannot be normalized safely.") from exc


def _evaluation_step(prefix: str, step: dict[str, Any] | TaskVerificationStep) -> VerificationStep:
    if isinstance(step, TaskVerificationStep):
        return VerificationStep(
            name=step.name,
            command=tuple(step.command),
            cwd=step.cwd,
            timeout_seconds=step.timeout_seconds,
            prefix=prefix,
        )
    return VerificationStep(
        name=str(step["name"]),
        command=tuple(step["command"]),
        cwd=str(step.get("cwd", ".")),
        timeout_seconds=int(step.get("timeout_seconds", 600)),
        prefix=prefix,
    )


def _add_evaluation_step(result: dict[str, Any], evidence: StepEvidence) -> None:
    details = evidence.details()
    _add(result, evidence.name, evidence.status, evidence.message, **details)


def _add_evaluation_result(result: dict[str, Any], evaluation: EvaluationResult) -> None:
    if evaluation.candidate is not None and not result.get("git", {}).get("head"):
        result["git"] = evaluation.candidate.binding()
    elif evaluation.candidate is None:
        for evidence in evaluation.steps:
            _add_evaluation_step(result, evidence)
        return
    for evidence in evaluation.steps:
        _add_evaluation_step(result, evidence)


def _validate_baseline(root: Path, baseline: str) -> tuple[str, str]:
    try:
        exists = subprocess.run(
            ["git", "cat-file", "-e", f"{baseline}^{{commit}}"],
            cwd=root,
            capture_output=True,
        )
    except OSError as exc:
        return "UNAVAILABLE", f"Unable to execute Git while locating the task baseline: {exc}"
    if exists.returncode != 0:
        return "UNAVAILABLE", "Task baseline is not available as a Git commit in this checkout."
    try:
        ancestor = subprocess.run(
            ["git", "merge-base", "--is-ancestor", baseline, "HEAD"],
            cwd=root,
            capture_output=True,
        )
    except OSError as exc:
        return "UNAVAILABLE", f"Unable to execute Git while validating baseline ancestry: {exc}"
    if ancestor.returncode == 1:
        return "FAIL", "Task baseline is not an ancestor of current HEAD."
    if ancestor.returncode != 0:
        detail = ancestor.stderr.decode("utf-8", errors="replace").strip()
        return "UNAVAILABLE", f"Unable to validate baseline ancestry: {detail or 'Git failed.'}"
    return "PASS", "Task baseline is an ancestor of current HEAD."


def run_worker_check(root: Path, task_path: Path, *, final: bool = False) -> dict[str, Any]:
    identity_error: str | None = None
    try:
        executing_identity: dict[str, str | None] = runtime_identity()
    except BuildIdentityUnavailable as exc:
        executing_identity = {"version": __version__, "build_sha256": None}
        identity_error = str(exc)

    result = empty_worker_result(task_path, final=final, ekzd_identity=executing_identity)
    identity = task_identity(task_path)
    result["task"] = identity
    try:
        task = load_task_manifest(task_path)
    except HarnessError as exc:
        _add(result, "task-manifest", "FAIL", str(exc))
        return finalize_worker_result(result)

    result["task"] = {
        "path": str(task.path),
        "sha256": task.sha256,
        "schema_version": task.schema_version,
        "objective": task.objective,
    }
    result["baseline"] = task.baseline
    _add(result, "task-manifest", "PASS", "Task manifest schema and exact-byte identity are valid.")

    if identity_error is not None:
        _add(
            result,
            "ekzd-identity",
            "UNAVAILABLE",
            f"Exact executing EkzD build identity is unavailable: {identity_error}",
        )
        return finalize_worker_result(result)

    executing_version = str(executing_identity["version"])
    executing_build = str(executing_identity["build_sha256"])
    if task.schema_version == 1:
        _add(
            result,
            "ekzd-identity",
            "WARN",
            "Task schema v1 does not pin the EkzD harness build.",
            required=False,
            version=executing_version,
            build_sha256=executing_build,
        )
    elif task.ekzd_version == executing_version and task.ekzd_build_sha256 == executing_build:
        _add(
            result,
            "ekzd-identity",
            "PASS",
            "Executing EkzD version and build SHA-256 match the schema-v2 task pin.",
            version=executing_version,
            build_sha256=executing_build,
        )
    else:
        _add(
            result,
            "ekzd-identity",
            "FAIL",
            "Executing EkzD version or build SHA-256 does not match the schema-v2 task pin.",
            expected_version=task.ekzd_version,
            expected_build_sha256=task.ekzd_build_sha256,
            actual_version=executing_version,
            actual_build_sha256=executing_build,
        )
        return finalize_worker_result(result)

    if shutil.which("git") is None:
        _add(result, "git-tool", "UNAVAILABLE", "Required executable is unavailable: git")
        return finalize_worker_result(result)

    try:
        ensure_git_visibility_supported(root)
        worker_candidate = capture_candidate(root)
        result["git"] = worker_candidate.binding()
        _add(result, "git-visibility", "PASS", "Git index visibility and tracked-file filters are supported.")
    except HarnessError as exc:
        message = str(exc)
        status = "FAIL" if message.startswith(("Git index flags hide", "Git content filters are unsupported")) else "UNAVAILABLE"
        _add(result, "git-visibility", status, message)
        return finalize_worker_result(result)
    except OSError as exc:
        _add(result, "git-visibility", "UNAVAILABLE", f"Unable to evaluate Git visibility: {exc}")
        return finalize_worker_result(result)

    baseline_status, baseline_message = _validate_baseline(root, task.baseline)
    baseline_ok = baseline_status == "PASS"
    _add(result, "baseline-ancestry", baseline_status, baseline_message)

    branch_format = subprocess.run(
        ["git", "check-ref-format", "--branch", task.implementation_branch],
        cwd=root,
        capture_output=True,
    )
    branch = result["git"]["branch"]
    if branch_format.returncode != 0:
        _add(result, "implementation-branch", "FAIL", f"Declared implementation branch is not a valid Git branch name: {task.implementation_branch!r}.")
    elif branch == task.implementation_branch:
        _add(result, "implementation-branch", "PASS", f"Current branch matches {task.implementation_branch}.")
    else:
        _add(
            result,
            "implementation-branch",
            "FAIL",
            f"Current branch {branch!r} does not match declared implementation branch {task.implementation_branch!r}.",
        )

    clean = bool(result["git"]["clean"])
    if final:
        _add(
            result,
            "worktree-mode",
            "PASS" if clean else "FAIL",
            "Final checking requires and has a clean worktree." if clean else "Final checking requires a clean reviewable worktree.",
        )
    else:
        _add(
            result,
            "worktree-mode",
            "PASS" if clean else "WARN",
            "Development checking is bound to the clean current state." if clean else "Development checking includes current staged, unstaged, and untracked non-ignored work.",
            required=False if not clean else True,
        )

    project_config: dict[str, Any] | None = None
    if baseline_ok:
        try:
            project_config = _baseline_project_config(root, task.baseline)
            _add(result, "baseline-project-config", "PASS", f"Loaded project verification from {task.baseline}:{CONFIG_RELATIVE.as_posix()}.")
        except (HarnessError, OSError) as exc:
            _add(result, "baseline-project-config", "UNAVAILABLE", str(exc))
    else:
        _add(result, "baseline-project-config", "UNAVAILABLE", "Cannot load trusted project configuration until baseline ancestry is valid.")

    if task.repository is None:
        _add(result, "repository-identity", "WARN", "Task manifest does not declare a repository identity.", required=False)
    else:
        try:
            origin = _origin_remote(root)
            if origin is None:
                _add(result, "repository-identity", "UNAVAILABLE", "Task declares a repository identity but this checkout has no origin remote.")
            else:
                actual = _safe_repository_identifier(origin)
                expected = _safe_repository_identifier(task.repository)
                if actual == expected:
                    _add(result, "repository-identity", "PASS", "Sanitized repository identity matches the task manifest.", repository=actual)
                else:
                    _add(result, "repository-identity", "FAIL", "Sanitized repository identity does not match the task manifest.", expected=expected, actual=actual)
        except (HarnessError, ValueError):
            _add(result, "repository-identity", "UNAVAILABLE", "Unable to evaluate sanitized repository identity safely.")

    if baseline_ok:
        try:
            commit_count = session_commit_count(root, task.baseline)
            if commit_count <= task.max_commits:
                _add(result, "commit-ceiling", "PASS", f"Commit count is {commit_count}/{task.max_commits}.", commit_count=commit_count, max_commits=task.max_commits)
            else:
                _add(result, "commit-ceiling", "FAIL", f"Commit ceiling exceeded: {commit_count} > {task.max_commits}.", commit_count=commit_count, max_commits=task.max_commits)
        except (HarnessError, OSError) as exc:
            _add(result, "commit-ceiling", "UNAVAILABLE", str(exc))

        try:
            history = session_history_paths(root, task.baseline)
            protected = sorted(PROTECTED_SESSION_HISTORY_PATHS & history)
            current_config_changed = CONFIG_RELATIVE.as_posix() in set(changed_paths(root))
            state_tracked = bool(run_git(root, "ls-files", "--", STATE_RELATIVE.as_posix()))
            violations = list(protected)
            if current_config_changed and CONFIG_RELATIVE.as_posix() not in violations:
                violations.append(CONFIG_RELATIVE.as_posix())
            if state_tracked and STATE_RELATIVE.as_posix() not in violations:
                violations.append(STATE_RELATIVE.as_posix())
            if violations:
                _add(result, "protected-harness", "FAIL", "Protected EkzD harness paths changed during worker implementation: " + ", ".join(sorted(violations)), paths=sorted(violations))
            else:
                _add(result, "protected-harness", "PASS", "Protected EkzD project/session paths remain trusted.")
        except (HarnessError, OSError) as exc:
            _add(result, "protected-harness", "UNAVAILABLE", str(exc))

        try:
            paths = changed_paths(root, start_head=task.baseline)
            violations = _scope_violations(paths, task)
            if violations:
                _add(result, "task-scope", "FAIL", "Task scope violations: " + "; ".join(violations), changed_paths=paths, violations=violations)
            else:
                _add(result, "task-scope", "PASS", f"All {len(paths)} changed path(s) are inside task scope.", changed_paths=paths)
        except (HarnessError, OSError) as exc:
            _add(result, "task-scope", "UNAVAILABLE", str(exc))
    else:
        _add(result, "commit-ceiling", "UNAVAILABLE", "Cannot count task commits until baseline ancestry is valid.")
        _add(result, "protected-harness", "UNAVAILABLE", "Cannot inspect protected task history until baseline ancestry is valid.")
        _add(result, "task-scope", "UNAVAILABLE", "Cannot calculate baseline-relative task scope until baseline ancestry is valid.")

    steps: list[tuple[str, dict[str, Any] | TaskVerificationStep]] = []
    if project_config is not None:
        steps.extend(("project", step) for step in project_config["verification"]["steps"])
    steps.extend(("task", step) for step in task.verification_steps)

    evaluation = evaluate_candidate(
        root,
        (_evaluation_step(prefix, step) for prefix, step in steps),
        mode="final" if final else "development",
        implementation_branch=task.implementation_branch if final else None,
        expected_candidate=worker_candidate,
    )
    _add_evaluation_result(result, evaluation)
    if evaluation.final_binding_error is None:
        try:
            if capture_candidate(root) != worker_candidate:
                _add(result, "state-binding", "FAIL", "Candidate changed after verification evaluation; result remains bound to the initial state.")
        except (HarnessError, OSError) as exc:
            _add(result, "state-binding", "UNAVAILABLE", f"Unable to bind final Git/worktree state after evaluation: {exc}")

    return finalize_worker_result(result)
