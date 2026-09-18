"""Read-only views of frozen authority and lifecycle evidence."""
from pathlib import Path

from .candidate import capture_candidate
from .core import HarnessError, session_commit_count
from .session import read_state
from .identity import BuildIdentityUnavailable, runtime_identity


def build_workflow_status(root: Path) -> dict:
    state = read_state(root)
    if not state:
        return {"session_status": "none", "next": "Write a local task, then run `ekzd start task.toml`."}
    contract = state["contract"]
    result = {"session_status": state["status"], "project": contract["project"]["name"],
              "objective": contract["objective"], "contract_id": state["contract_id"],
              "next": "Write the next task and run `ekzd start task.toml`."}
    if state["status"] != "active":
        return result
    result.update(implementation_branch=contract["branch"], baseline_head=contract["baseline"],
                  max_commits=contract["max_commits"], verification="not run",
                  next="Transfer the archive from `ekzd handoff`; worker runs `python3 run.py prepare`. Independently run `ekzd verify` on return.")
    try:
        if runtime_identity() != contract["ekzd"]:
            raise HarnessError("Executing runtime differs from the frozen contract; use the pinned build.")
        candidate = capture_candidate(root)
        count = session_commit_count(root, contract["baseline"])
        result.update(current_branch=candidate.branch, head=candidate.head, worktree_clean=candidate.clean, commit_count=count)
        if count > contract["max_commits"]:
            raise HarnessError("Commit budget exceeded.")
        verification = state["verification"]
        if verification:
            if verification.get("passed") is not True or verification.get("status") != "PASS":
                result.update(verification="failed", next="Fix the failure, then run `ekzd verify`.")
            elif verification["candidate"] != candidate.binding() or verification["contract_id"] != state["contract_id"]:
                result.update(verification="stale", next="Candidate or contract changed; rerun `ekzd verify`.")
            else:
                result.update(verification="passed", next="Review the exact candidate, then run `ekzd finish --accept`.")
    except (HarnessError, BuildIdentityUnavailable) as exc:
        result.update(verification="blocked", blocked_reason=str(exc), next="Resolve the reported repository blocker before verification.")
    return result
