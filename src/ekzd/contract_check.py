"""Structural frozen-contract checks shared by worker and maintainer.

EkzD verifies Git-visible authority and candidate identity only. Project-specific
formatting, linting, tests, builds, and other dependency-bound checks belong to
CI or another external verification system.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Callable

from .candidate import capture_candidate, require_final
from .check import check_result, empty_worker_result, finalize_worker_result
from .contract import contract_id, validate_baseline_sources, validate_branch, validate_contract
from .core import HarnessError, _matches_scope, session_commit_count, session_history_paths, run_git
from .identity import BuildIdentityUnavailable, runtime_identity


def check_contract(root: Path, contract: dict, *, final: bool,
                   authority_guard: Callable[[], None] | None = None,
                   prepare: bool = False) -> dict:
    """Verify structural authority without executing project commands."""
    result = empty_worker_result(final=final)
    result["contract_id"] = contract_id(contract)
    result["baseline"] = contract.get("baseline")

    def add(name, status, message, **details):
        result["checks"].append(check_result(name, status, message, **details))

    try:
        validate_contract(contract)
        if sys.platform != "linux" or sys.version_info < (3, 11):
            raise HarnessError("Worker evaluation requires Linux and Python 3.11 or newer.")
        if shutil.which("git") is None:
            raise HarnessError("Required executable is unavailable: git")
        identity = runtime_identity()
        result["ekzd"] = identity
        if identity != contract["ekzd"]:
            add("ekzd-identity", "FAIL",
                "Executing EkzD runtime does not match the frozen contract.",
                expected=contract["ekzd"], actual=identity)
            return finalize_worker_result(result)
        if authority_guard:
            authority_guard()
        candidate = capture_candidate(root)
        result["git"] = candidate.binding()
    except (HarnessError, BuildIdentityUnavailable, OSError) as exc:
        add("candidate-authority", "UNAVAILABLE", str(exc))
        return finalize_worker_result(result)

    try:
        run_git(root, "cat-file", "-e", f"{contract['baseline']}^{{commit}}")
    except (HarnessError, OSError) as exc:
        add("baseline", "UNAVAILABLE", f"Frozen baseline commit is unavailable: {exc}")
        return finalize_worker_result(result)

    try:
        validate_branch(root, contract["branch"])
        if candidate.branch != contract["branch"]:
            raise HarnessError(
                f"Candidate must be on declared implementation branch {contract['branch']!r}; "
                f"current branch is {candidate.branch!r}."
            )
        if prepare and (candidate.head != contract["baseline"] or not candidate.clean):
            raise HarnessError("Preparation requires a clean candidate at the exact frozen baseline.")

        count = session_commit_count(root, contract["baseline"])
        if count > contract["max_commits"]:
            raise HarnessError(
                f"Commit safety ceiling exceeded: {count} > {contract['max_commits']}."
            )
        validate_baseline_sources(root, contract)
        paths = sorted(session_history_paths(root, contract["baseline"]) | set(candidate.changed_paths))
        scope = contract["scope"]
        violations = []
        for path in paths:
            if not any(_matches_scope(path, pattern) for pattern in scope["include"]):
                violations.append(f"{path}: outside scope.include")
            for label in ("exclude", "protected"):
                if any(_matches_scope(path, pattern) for pattern in scope[label]):
                    violations.append(f"{path}: matches scope.{label}")
        if run_git(root, "ls-files", "--", ".ekzd/local/"):
            violations.append(".ekzd/local/ must remain untracked")
        if violations:
            raise HarnessError("Scope/protected-path violations: " + "; ".join(violations))
        if final:
            require_final(candidate, contract["branch"])
        add(
            "contract-authority", "PASS",
            "Baseline, branch, commit ceiling and scope satisfy frozen authority.",
            commit_count=count, max_commits=contract["max_commits"], changed_paths=paths,
        )
    except (HarnessError, OSError) as exc:
        add("contract-authority", "FAIL", str(exc))
        return finalize_worker_result(result)

    try:
        if authority_guard:
            authority_guard()
        if runtime_identity() != contract["ekzd"]:
            raise HarnessError("Executing runtime changed during structural verification.")
        if capture_candidate(root) != candidate:
            raise HarnessError(
                "Candidate changed during structural verification; result remains bound to the initial state."
            )
        add("state-binding", "PASS", "Candidate and frozen authority remained unchanged.")
    except (HarnessError, BuildIdentityUnavailable, OSError) as exc:
        add("state-binding", "FAIL", str(exc))
    return finalize_worker_result(result)
