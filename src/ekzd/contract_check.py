"""Shared frozen-contract authority checks around the existing candidate evaluator."""
from __future__ import annotations

import sys
import shutil
from pathlib import Path
from typing import Callable

from .candidate import capture_candidate
from .check import check_result, empty_worker_result, finalize_worker_result
from .contract import contract_id, validate_baseline_sources, validate_branch, validate_contract
from .core import HarnessError, _matches_scope, session_commit_count, session_history_paths, run_git
from .evaluation import VerificationStep, evaluate_candidate
from .identity import BuildIdentityUnavailable, runtime_identity


def check_contract(root: Path, contract: dict, *, final: bool, run_commands: bool = True,
                   authority_guard: Callable[[], None] | None = None, prepare: bool = False) -> dict:
    """Bind authority prechecks and all verifiers to one captured candidate.

    The caller owns the contract's provenance and any persistence/file guard.
    Neither current project TOML nor authoring task files are consulted here.
    """
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
            add("ekzd-identity", "FAIL", "Executing EkzD runtime does not match the frozen contract.", expected=contract["ekzd"], actual=identity)
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
            raise HarnessError(f"Candidate must be on declared implementation branch {contract['branch']!r}; current branch is {candidate.branch!r}.")
        if prepare and (candidate.head != contract["baseline"] or not candidate.clean):
            raise HarnessError("Preparation requires a clean candidate at the exact frozen baseline.")
        count = session_commit_count(root, contract["baseline"])
        if count > contract["max_commits"]:
            raise HarnessError(f"Commit safety ceiling exceeded: {count} > {contract['max_commits']}.")
        validate_baseline_sources(root, contract)
        # Include every intermediate commit (including merges/reverted changes),
        # not just a baseline-to-HEAD diff. Current raw paths come from Candidate.
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
        add("contract-authority", "PASS", "Baseline, branch, commit ceiling and scope satisfy frozen authority.",
            commit_count=count, max_commits=contract["max_commits"], changed_paths=paths)
    except (HarnessError, OSError) as exc:
        add("contract-authority", "FAIL", str(exc))
        return finalize_worker_result(result)

    # Preparation runs capability probes only. Every checking attempt repeats
    # them; success is never cached. Both passes use the same initial candidate.
    groups = [("readiness", contract["readiness"])] if contract["readiness"] or prepare else []
    if not prepare:
        groups.append(("verification", contract["verification"]))
    if not run_commands:
        groups = [("verification", [])]
    for prefix, configured in groups:
        steps = [VerificationStep(name=s["name"], command=tuple(s["command"]), cwd=s["cwd"],
                                  timeout_seconds=s["timeout_seconds"], prefix=prefix) for s in configured]
        evaluation = evaluate_candidate(root, steps, mode="final" if final or prepare else "development",
                                        implementation_branch=contract["branch"], expected_candidate=candidate,
                                        authority_guard=authority_guard)
        for evidence in evaluation.steps:
            status = evidence.status
            if prefix == "readiness" and status == "FAIL" and not evidence.trust_lost:
                status = "UNAVAILABLE"
            add(evidence.name, status, evidence.message, **evidence.details())
        if not evaluation.passed:
            break
    try:
        if authority_guard:
            authority_guard()
        if runtime_identity() != contract["ekzd"]:
            raise HarnessError("Executing runtime changed during evaluation.")
        if capture_candidate(root) != candidate:
            raise HarnessError("Candidate changed after evaluation; result remains bound to the initial state.")
    except (HarnessError, BuildIdentityUnavailable, OSError) as exc:
        add("state-binding", "FAIL", str(exc))
    return finalize_worker_result(result)
