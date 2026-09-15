from __future__ import annotations

import hashlib
import shutil
import subprocess
import tomllib
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from . import __version__
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
from .identity import BuildIdentityUnavailable, runtime_identity
from .task import TaskManifest, TaskVerificationStep, load_task_manifest, task_identity
from .workflow import _origin_remote, _sanitize_repository_identifier


def _add(result: dict[str, Any], name: str, status: str, message: str, *, required: bool = True, **details: Any) -> None:
    result["checks"].append(check_result(name, status, message, required=required, **details))


def _baseline_project_config(root: Path, baseline: str) -> dict[str, Any]:
    raw = run_git_bytes(root, "show", f"{baseline}:{CONFIG_RELATIVE.as_posix()}")
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise HarnessError(f"Unable to parse baseline {CONFIG_RELATIVE}: {exc}") from exc
    config = validate_config(data, root, ready=False)
    scope = config.get("scope", {})
    if not scope.get("include"):
        raise HarnessError("Baseline project scope.include must contain at least one entry.")
    if not config.get("acceptance", {}).get("criteria"):
        raise HarnessError("Baseline project acceptance.criteria must contain at least one entry.")
    if not config.get("verification", {}).get("steps"):
        raise HarnessError("Baseline project verification.steps must contain at least one step.")
    return config


def _worker_untracked_paths(root: Path) -> list[bytes]:
    return sorted(
        raw
        for raw in run_git_bytes(root, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0")
        if raw and raw.decode("utf-8", errors="surrogateescape") != STATE_RELATIVE.as_posix()
    )


def _worker_fingerprint(root: Path) -> str:
    ensure_git_visibility_supported(root)
    digest = hashlib.sha256()
    for label, args in (
        (b"head", ("rev-parse", "HEAD")),
        (b"branch", ("branch", "--show-current")),
        (b"worktree-diff", ("diff", "--binary", "HEAD")),
        (b"index-diff", ("diff", "--binary", "--cached", "HEAD")),
    ):
        digest.update(label + b"\0")
        digest.update(run_git_bytes(root, *args))
        digest.update(b"\0")
    for raw_path in _worker_untracked_paths(root):
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


def _worktree_binding(root: Path) -> dict[str, Any]:
    worktree_diff = run_git_bytes(root, "diff", "--binary", "HEAD")
    index_diff = run_git_bytes(root, "diff", "--binary", "--cached", "HEAD")
    untracked = _worker_untracked_paths(root)
    return {
        "head": run_git(root, "rev-parse", "HEAD"),
        "branch": run_git(root, "branch", "--show-current") or "(detached)",
        "clean": not worktree_diff and not index_diff and not untracked,
        "worktree_fingerprint": _worker_fingerprint(root),
    }


def _scope_violations(paths: list[str], task: TaskManifest) -> list[str]:
    violations: list[str] = []
    for path in paths:
        if not any(_matches_scope(path, pattern) for pattern in task.include):
            violations.append(f"{path}: outside task scope.include")
        if any(_matches_scope(path, pattern) for pattern in task.exclude):
            violations.append(f"{path}: matches task scope.exclude")
    return violations


def _tracked_snapshot(root: Path) -> str:
    digest = hashlib.sha256()
    for label, args in (
        (b"head", ("rev-parse", "HEAD")),
        (b"index", ("diff", "--binary", "--cached", "HEAD")),
        (b"worktree", ("diff", "--binary", "HEAD")),
    ):
        digest.update(label + b"\0")
        digest.update(run_git_bytes(root, *args))
        digest.update(b"\0")
    return digest.hexdigest()


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


def _run_step(root: Path, prefix: str, step: dict[str, Any] | TaskVerificationStep) -> tuple[dict[str, Any], bool]:
    if isinstance(step, TaskVerificationStep):
        name = step.name
        command = list(step.command)
        cwd = step.cwd
        timeout = step.timeout_seconds
    else:
        name = str(step["name"])
        command = list(step["command"])
        cwd = str(step.get("cwd", "."))
        timeout = int(step.get("timeout_seconds", 600))
    check_name = f"{prefix}:{name}"
    try:
        resolved_root = root.resolve()
        command_cwd = (root / cwd).resolve()
    except OSError:
        return check_result(check_name, "UNAVAILABLE", f"Configured working directory is unusable: {cwd}"), False
    if resolved_root not in (command_cwd, *command_cwd.parents) or not command_cwd.is_dir():
        return check_result(check_name, "UNAVAILABLE", f"Configured working directory must resolve inside the project: {cwd}"), False

    try:
        before_tracked = _tracked_snapshot(root)
        before_visible = _worker_fingerprint(root)
    except (HarnessError, OSError) as exc:
        return check_result(check_name, "UNAVAILABLE", f"Unable to capture pre-check Git state: {exc}"), False

    try:
        process = subprocess.run(
            command,
            cwd=command_cwd,
            capture_output=True,
            timeout=timeout,
        )
        status = "PASS" if process.returncode == 0 else "FAIL"
        message = "Verification command passed." if process.returncode == 0 else f"Verification command exited with status {process.returncode}."
        details: dict[str, Any] = {"command": command, "cwd": cwd, "returncode": process.returncode}
        if process.stdout:
            details["stdout"] = process.stdout.decode("utf-8", errors="replace")
        if process.stderr:
            details["stderr"] = process.stderr.decode("utf-8", errors="replace")
        item = check_result(check_name, status, message, **details)
    except FileNotFoundError:
        item = check_result(check_name, "UNAVAILABLE", f"Required executable is unavailable: {command[0]}", command=command, cwd=cwd)
    except subprocess.TimeoutExpired as exc:
        item = check_result(check_name, "FAIL", f"Verification command timed out after {timeout} seconds.", command=command, cwd=cwd)
        if exc.stdout:
            output = exc.stdout if isinstance(exc.stdout, bytes) else str(exc.stdout).encode("utf-8", errors="replace")
            item.setdefault("details", {})["stdout"] = output.decode("utf-8", errors="replace")
        if exc.stderr:
            output = exc.stderr if isinstance(exc.stderr, bytes) else str(exc.stderr).encode("utf-8", errors="replace")
            item.setdefault("details", {})["stderr"] = output.decode("utf-8", errors="replace")
    except OSError as exc:
        item = check_result(check_name, "UNAVAILABLE", f"Unable to execute verification command: {exc}", command=command, cwd=cwd)

    try:
        after_tracked = _tracked_snapshot(root)
        after_visible = _worker_fingerprint(root)
    except (HarnessError, OSError) as exc:
        item.setdefault("details", {})["post_check_git_error"] = str(exc)
        return item, True

    mutated = before_tracked != after_tracked or before_visible != after_visible
    if mutated:
        item.setdefault("details", {})["git_state_mutated"] = True
    return item, mutated


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
        result["git"] = _worktree_binding(root)
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

    mutation_detected = False
    for prefix, step in steps:
        if mutation_detected:
            name = step.name if isinstance(step, TaskVerificationStep) else str(step["name"])
            _add(result, f"{prefix}:{name}", "UNAVAILABLE", "Not run because an earlier verification command mutated Git-visible project state.")
            continue
        item, mutated = _run_step(root, prefix, step)
        result["checks"].append(item)
        if mutated:
            mutation_detected = True
            _add(result, f"{item['name']}:mutation", "FAIL", "Verification command changed tracked or non-ignored Git-visible project state; EkzD left the mutation untouched.")

    try:
        post = _worktree_binding(root)
        if not mutation_detected:
            result["git"] = post
        elif post != result["git"]:
            _add(result, "state-binding", "FAIL", "Git-visible state changed while verification was running; result remains bound to the pre-verification state and is not ready.")
        else:
            _add(result, "state-binding", "PASS", "Verification completed against the captured Git/worktree state.")
        if not mutation_detected:
            _add(result, "state-binding", "PASS", "Result is bound to the exact Git/worktree state evaluated.")
    except (HarnessError, OSError) as exc:
        _add(result, "state-binding", "UNAVAILABLE", f"Unable to bind final Git/worktree state: {exc}")

    return finalize_worker_result(result)
