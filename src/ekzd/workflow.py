from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .core import (
    CONFIG_RELATIVE,
    STATE_RELATIVE,
    HarnessError,
    _active_state,
    _session_start_head,
    enforce_protected_session_history,
    enforce_session_contract,
    git_state,
    git_state_fingerprint,
    load_committed_config,
    read_state,
    session_commit_count,
    start_session,
    write_state,
)


def _origin_remote(root: Path) -> str | None:
    process = subprocess.run(
        ["git", "config", "--get", "remote.origin.url"],
        cwd=root,
        text=True,
        capture_output=True,
    )
    if process.returncode == 0:
        value = process.stdout.strip()
        return value or None
    if process.returncode == 1:
        return None
    detail = process.stderr.strip() or process.stdout.strip()
    raise HarnessError(f"Unable to read Git origin remote: {detail}")


def _sanitize_repository_identifier(remote: str) -> str:
    parsed = urlsplit(remote)
    if parsed.scheme and parsed.hostname:
        host = parsed.hostname
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        if parsed.port is not None:
            host = f"{host}:{parsed.port}"
        path = parsed.path.removesuffix(".git")
        return urlunsplit((parsed.scheme, host, path, "", ""))

    if "@" in remote:
        _, tail = remote.split("@", 1)
        if ":" in tail:
            host, path = tail.split(":", 1)
            return f"{host}/{path.removesuffix('.git')}"

    return remote.removesuffix(".git")


def _frozen_repository_metadata(root: Path) -> dict[str, Any]:
    remote = _origin_remote(root)
    if remote is None:
        return {"origin_available": False, "identifier": None}
    return {
        "origin_available": True,
        "identifier": _sanitize_repository_identifier(remote),
    }


def _validate_implementation_branch(root: Path, branch: str, baseline_branch: str) -> str:
    branch = branch.strip()
    if not branch:
        raise HarnessError("Implementation branch must be a non-empty Git branch name.")
    process = subprocess.run(
        ["git", "check-ref-format", "--branch", branch],
        cwd=root,
        text=True,
        capture_output=True,
    )
    if process.returncode != 0:
        detail = process.stderr.strip() or process.stdout.strip() or branch
        raise HarnessError(f"Invalid implementation branch: {detail}")
    if baseline_branch != "(detached)" and branch == baseline_branch:
        raise HarnessError(
            "Implementation branch must be separate from the baseline branch; declare a dedicated task branch."
        )
    return branch


def _workflow_metadata(state: dict[str, Any]) -> dict[str, Any]:
    workflow = state.get("workflow")
    if not isinstance(workflow, dict):
        raise HarnessError(
            "Active session is missing V1.1 workflow metadata; abort it and start a fresh session with an explicit implementation branch."
        )
    branch = workflow.get("implementation_branch")
    repository = workflow.get("repository")
    if not isinstance(branch, str) or not branch:
        raise HarnessError("Active session is missing its frozen implementation branch.")
    if not isinstance(repository, dict):
        raise HarnessError("Active session is missing its frozen repository identity.")
    origin_available = repository.get("origin_available")
    identifier = repository.get("identifier")
    if not isinstance(origin_available, bool):
        raise HarnessError("Active session has invalid frozen repository metadata.")
    if origin_available and (not isinstance(identifier, str) or not identifier):
        raise HarnessError("Active session has invalid frozen repository identifier.")
    if not origin_available and identifier is not None:
        raise HarnessError("Active session has invalid no-origin repository metadata.")
    return workflow


def start_reproducible_session(root: Path, objective: str, *, implementation_branch: str) -> dict[str, Any]:
    baseline = git_state(root)
    if baseline["status"]:
        raise HarnessError(
            "A clean working tree is required before starting an EkzD session so the task baseline can be reproduced by another development environment."
        )
    branch = _validate_implementation_branch(root, implementation_branch, baseline["branch"])
    repository = _frozen_repository_metadata(root)
    state = start_session(root, objective)
    state["workflow"] = {
        "implementation_branch": branch,
        "repository": repository,
    }
    write_state(root, state)
    return state


def _bullets(values: list[str], *, empty: str = "(none)") -> list[str]:
    if not values:
        return [f"- {empty}"]
    return [f"- {value}" for value in values]


def _repository_prompt_line(repository: dict[str, Any], project: str) -> str:
    if repository["origin_available"]:
        return f"Repository: {repository['identifier']}"
    return f"Repository: (no origin remote recorded at session start; project: {project})"


def build_implementation_prompt(root: Path) -> str:
    state = _active_state(root)
    enforce_session_contract(root, state)
    start_head = _session_start_head(state)
    enforce_protected_session_history(root, start_head)
    config = load_committed_config(root, ready=True)

    start_git = state.get("start_git")
    if not isinstance(start_git, dict):
        raise HarnessError("Active session is missing its starting Git state.")
    start_status = start_git.get("status")
    if not isinstance(start_status, list):
        raise HarnessError("Active session is missing its starting working-tree state.")
    if start_status:
        raise HarnessError(
            "This session started from a dirty working tree and cannot produce a portable implementation prompt. Abort it, restore or commit the baseline, then start a fresh session."
        )

    workflow = _workflow_metadata(state)
    repository = workflow["repository"]
    implementation_branch = workflow["implementation_branch"]
    baseline_branch = start_git.get("branch") or "(detached)"
    max_commits = config["session"]["max_commits"]
    sources = config.get("sources", {}).get("paths", [])
    scope = config.get("scope", {})
    authority = config.get("authority", {})
    acceptance = config.get("acceptance", {}).get("criteria", [])
    verification = config.get("verification", {}).get("steps", [])

    lines = [
        "You are the implementation session for the EkzD task below. Treat this contract as authoritative and make the smallest change that satisfies it.",
        "",
        _repository_prompt_line(repository, config["project"]["name"]),
        f"Project: {config['project']['name']}",
        f"Baseline branch: {baseline_branch}",
        f"Implementation branch: {implementation_branch}",
        f"Task baseline HEAD: {start_head}",
        "Baseline working tree: clean",
        "Session status: active",
        f"Maximum commits: {max_commits}",
        "",
        "Objective",
        "",
        state["objective"],
        "",
        "Declared sources",
        "",
        *_bullets(sources),
        "",
        "Scope",
        "",
        "Allowed paths/patterns:",
        *_bullets(scope.get("include", [])),
        "",
        "Excluded paths/patterns:",
        *_bullets(scope.get("exclude", [])),
        "",
        "Constraints:",
        *_bullets(scope.get("constraints", [])),
        "",
        "Authority",
        "",
        "May:",
        *_bullets(authority.get("may", [])),
        "",
        "Requires approval:",
        *_bullets(authority.get("requires_approval", [])),
        "",
        "May not:",
        *_bullets(authority.get("may_not", [])),
        "",
        "Acceptance criteria",
        "",
        *_bullets(acceptance),
        "",
        "Configured verification",
        "",
    ]

    for step in verification:
        argv = json.dumps(step["command"], ensure_ascii=False)
        cwd = json.dumps(step.get("cwd", "."), ensure_ascii=False)
        lines.extend(
            [
                f"- {step['name']}",
                f"  argv: {argv}",
                f"  cwd: {cwd}",
            ]
        )

    lines.extend(
        [
            "",
            "Implementation instructions",
            "",
            "- Confirm the repository access available to you and confirm the exact task baseline HEAD before making changes.",
            f"- Create/check out or otherwise operate on the declared implementation branch `{implementation_branch}` from the exact frozen baseline `{start_head}`.",
            f"- Do not implement directly on the baseline branch `{baseline_branch}`; the implementation branch is the task delivery branch.",
            "- If you have a local Git checkout, also confirm that its working tree is clean before editing. If you do not have a local checkout, use the repository access available to you and do not pretend local Git state was checked.",
            "- If no origin remote was recorded at session start and your environment requires a remote repository locator, report that limitation instead of guessing one.",
            "- Read the declared sources before making changes. Do not invent requirements that are not supported by the task contract or those sources.",
            "- Modify only paths allowed by scope.include and never modify paths matched by scope.exclude.",
            f"- Do not modify or commit the protected EkzD files `{CONFIG_RELATIVE.as_posix()}` or `{STATE_RELATIVE.as_posix()}`.",
            f"- Use small logical commits and keep the implementation within {max_commits} commit{'s' if max_commits != 1 else ''}.",
            "- Configured verification commands are argv arrays executed directly without a shell; preserve their argument boundaries exactly when running equivalent checks.",
            "- Run the configured verification commands when they are available in your environment, plus narrowly relevant checks for the files you changed. Fix failures and rerun checks until the implementation is clean or a contract blocker is found.",
            "- Self-review the final diff for scope, correctness, tests, and unrelated changes before publishing the result.",
            f"- When repository operations are supported, push/update the declared implementation branch `{implementation_branch}` and open or update the relevant pull request.",
            "- Do not merge the pull request. Final merge authority remains with the operator after planning/review and authoritative local EkzD verification.",
            "- If your environment cannot push/update the branch or create/update a pull request, report that limitation explicitly instead of claiming the operation was completed.",
            "- Do not require or claim authoritative `ekzd verify` from the implementation environment. Authoritative EkzD verification happens in the maintainer environment that owns the active local session state.",
            "- If the task cannot be completed without expanding scope or authority, stop and report the exact blocker instead of making the extra change.",
            "- When finished, report the final branch name, commit SHA(s), PR number/link/reference when available, files changed, checks run and their results, and any remaining limitations or review notes.",
            "",
        ]
    )
    return "\n".join(lines)


def _blocked_status(
    *,
    state: dict[str, Any],
    config: dict[str, Any],
    current_git: dict[str, Any],
    start_git: dict[str, Any],
    start_head: str,
    reason: str,
    commit_count: int | None = None,
) -> dict[str, Any]:
    workflow = _workflow_metadata(state)
    return {
        "session_status": "active",
        "project": config["project"]["name"],
        "objective": state["objective"],
        "current_branch": current_git["branch"],
        "baseline_branch": start_git.get("branch") or "(detached)",
        "implementation_branch": workflow["implementation_branch"],
        "baseline_head": start_head,
        "head": current_git["head"],
        "worktree_clean": not current_git["status"],
        "commit_count": commit_count,
        "max_commits": config["session"]["max_commits"],
        "verification": "blocked",
        "blocked_reason": reason,
        "next": "Abort this session and restore/restart from a contract-valid baseline before continuing.",
    }


def build_workflow_status(root: Path) -> dict[str, Any]:
    state = read_state(root)
    if not state or state.get("status") != "active":
        status = state.get("status") if state else "none"
        return {
            "session_status": status,
            "objective": state.get("objective") if state else None,
            "next": 'Start a new session with `ekzd start "<objective>" --branch <task-branch>`.',
        }

    enforce_session_contract(root, state)
    start_head = _session_start_head(state)
    enforce_protected_session_history(root, start_head)
    config = load_committed_config(root, ready=True)
    current_git = git_state(root)
    start_git = state["start_git"]
    workflow = _workflow_metadata(state)

    try:
        commit_count = session_commit_count(root, start_head)
    except HarnessError as exc:
        return _blocked_status(
            state=state,
            config=config,
            current_git=current_git,
            start_git=start_git,
            start_head=start_head,
            reason=str(exc),
        )

    max_commits = config["session"]["max_commits"]
    if commit_count > max_commits:
        return _blocked_status(
            state=state,
            config=config,
            current_git=current_git,
            start_git=start_git,
            start_head=start_head,
            commit_count=commit_count,
            reason=f"Session commit budget exceeded: {commit_count} commits > {max_commits} allowed.",
        )

    changed_since_start = (
        current_git["head"] != start_head
        or bool(current_git["status"])
        or commit_count > 0
    )

    verification = state.get("verification")
    verification_status = "not run"
    if isinstance(verification, dict):
        if verification.get("passed"):
            verified_git = verification.get("git") == current_git
            verified_fingerprint = verification.get("state_fingerprint") == git_state_fingerprint(root)
            if verified_git and verified_fingerprint:
                verification_status = "passed"
                next_action = "Review the verified changes, then run `ekzd finish --accept` if you approve them."
            else:
                verification_status = "stale"
                next_action = "Repository state changed after verification; rerun `ekzd verify` before acceptance."
        else:
            verification_status = "failed"
            next_action = "Fix the verification failure, then rerun `ekzd verify`."
    elif changed_since_start:
        next_action = "When the implementation is ready, run `ekzd verify`."
    else:
        next_action = "Generate the frozen implementation handoff with `ekzd prompt`."

    return {
        "session_status": "active",
        "project": config["project"]["name"],
        "objective": state["objective"],
        "current_branch": current_git["branch"],
        "baseline_branch": start_git.get("branch") or "(detached)",
        "implementation_branch": workflow["implementation_branch"],
        "baseline_head": start_head,
        "head": current_git["head"],
        "worktree_clean": not current_git["status"],
        "commit_count": commit_count,
        "max_commits": max_commits,
        "verification": verification_status,
        "blocked_reason": None,
        "next": next_action,
    }
