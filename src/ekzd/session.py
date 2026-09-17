"""Local lifecycle persistence. Frozen authority never comes from current inputs."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

from .candidate import capture_candidate, require_final
from .contract import (LEGACY_MESSAGE, canonical_bytes, compose_contract, contract_id,
                       parse_task, read_toml, validate_baseline_sources, validate_branch,
                       validate_contract)
from .core import (HarnessError, load_committed_config, run_git, utc_now)
from .identity import BuildIdentityUnavailable, runtime_identity

LOCAL = Path(".ekzd/local")
STATE = LOCAL / "session.json"
SESSION_VERSION = 3


def ensure_local(root: Path) -> None:
    for relative in (Path(".ekzd"), LOCAL):
        if (root / relative).is_symlink():
            raise HarnessError(f"Local EkzD state must not traverse symlinks: {relative}")
    if run_git(root, "ls-files", "--", LOCAL.as_posix()):
        raise HarnessError(".ekzd/local/ must remain untracked; remove it from the Git index.")
    if (root / ".ekzd/session.json").exists():
        raise HarnessError(LEGACY_MESSAGE)


@contextmanager
def lifecycle_lock(root: Path):
    ensure_local(root)
    directory = root / LOCAL
    directory.mkdir(parents=True, exist_ok=True)
    fd = os.open(directory / "lifecycle.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise HarnessError("Another EkzD lifecycle command is running.") from exc
        yield
    finally:
        os.close(fd)


def read_state(root: Path) -> dict | None:
    ensure_local(root)
    path = root / STATE
    if path.is_symlink():
        raise HarnessError("Local session must not be a symlink.")
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise HarnessError(f"Unable to read local session: {exc}") from exc
    try:
        state = json.loads(raw)
    except (UnicodeError, ValueError) as exc:
        raise HarnessError(f"Unable to read local session: {exc}") from exc
    if not isinstance(state, dict) or type(state.get("schema_version")) is not int or state["schema_version"] != SESSION_VERSION:
        raise HarnessError(LEGACY_MESSAGE)
    contract = validate_contract(state.get("contract"))
    if state.get("contract_id") != contract_id(contract):
        raise HarnessError("Frozen contract ID mismatch; abort/remove the local session and refreeze.")
    if state.get("status") not in {"active", "finished", "aborted"}:
        raise HarnessError("Invalid local session status.")
    return state


def write_state(root: Path, state: dict) -> None:
    ensure_local(root)
    path = root / STATE
    if path.is_symlink():
        raise HarnessError("Local session must not be a symlink.")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".session-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(canonical_bytes(state))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def state_digest(root: Path) -> str:
    ensure_local(root)
    try:
        return hashlib.sha256((root / STATE).read_bytes()).hexdigest()
    except OSError as exc:
        raise HarnessError(f"Unable to bind local session: {exc}") from exc


def active_state(root: Path) -> dict:
    state = read_state(root)
    if not state or state["status"] != "active":
        raise HarnessError("No active EkzD session.")
    return state


def require_runtime(contract: dict) -> None:
    try:
        identity = runtime_identity()
    except BuildIdentityUnavailable as exc:
        raise HarnessError(f"Exact runtime identity unavailable: {exc}") from exc
    if identity != contract["ekzd"]:
        raise HarnessError("Executing EkzD runtime does not match the frozen contract; use the pinned build.")


def start_session(root: Path, task_path: Path) -> dict:
    with lifecycle_lock(root):
        existing = read_state(root)
        if existing and existing["status"] == "active":
            raise HarnessError("An active EkzD session already exists; abort or finish before refreezing.")
        # A local draft/lock must not dirty the reproducible candidate.
        ignored = run_git(root, "check-ignore", "--", (LOCAL / "session.json").as_posix())
        if not ignored:
            raise HarnessError("Ignore .ekzd/local/ before starting a task.")
        candidate = capture_candidate(root)
        if not candidate.clean:
            raise HarnessError("A clean working tree is required before freezing a reproducible baseline.")
        project = load_committed_config(root)
        task = parse_task(read_toml(task_path.expanduser()))
        validate_branch(root, task["branch"])
        if task["branch"] == candidate.branch:
            raise HarnessError("Implementation branch must be separate from the baseline branch.")
        try:
            identity = runtime_identity()
        except BuildIdentityUnavailable as exc:
            raise HarnessError(f"Exact runtime identity unavailable: {exc}") from exc
        contract = compose_contract(project, task, candidate.head, identity)
        validate_baseline_sources(root, contract)
        if capture_candidate(root) != candidate:
            raise HarnessError("Candidate changed while freezing the baseline.")
        state = {"schema_version": SESSION_VERSION, "status": "active", "contract_id": contract_id(contract),
                 "contract": contract, "started_at": utc_now(), "verification": None, "acceptance": None}
        write_state(root, state)
        return state


def verify_session(root: Path) -> dict:
    from .contract_check import check_contract

    with lifecycle_lock(root):
        state = active_state(root)
        # Clear old evidence before any preflight failure or verifier execution.
        state["verification"] = None
        state["acceptance"] = None
        write_state(root, state)
        digest = state_digest(root)

        def guard():
            if state_digest(root) != digest or active_state(root) != state:
                raise HarnessError("Session changed during verification; frozen authority is no longer trusted.")

        result = check_contract(root, state["contract"], final=True, authority_guard=guard)
        guard()
        if result["ready"] and capture_candidate(root).binding() != result["git"]:
            raise HarnessError("Candidate changed before verification evidence could be recorded.")
        verification = {"verified_at": utc_now(), "passed": result["ready"], "status": result["overall_status"],
                        "contract_id": state["contract_id"], "candidate": result["git"], "steps": result["checks"]}
        state["verification"] = verification
        write_state(root, state)
        return verification


def finish_session(root: Path, *, accept: bool) -> dict:
    from .contract_check import check_contract

    if not accept:
        raise HarnessError("Acceptance requires explicit `ekzd finish --accept` approval.")
    with lifecycle_lock(root):
        state = active_state(root)
        digest = state_digest(root)
        contract = state["contract"]
        candidate = capture_candidate(root)
        require_final(candidate, contract["branch"])
        verification = state.get("verification")
        if not isinstance(verification, dict) or not verification.get("passed"):
            raise HarnessError("Acceptance blocked: verification has not passed.")
        if verification.get("contract_id") != state["contract_id"] or verification.get("candidate") != candidate.binding():
            raise HarnessError("Acceptance blocked: stale candidate/contract evidence; rerun verification.")
        result = check_contract(root, contract, final=True, run_commands=False)
        if not result["ready"]:
            raise HarnessError("Acceptance blocked: " + "; ".join(item["message"] for item in result["checks"] if item["status"] in {"FAIL", "UNAVAILABLE"}))
        if capture_candidate(root) != candidate or state_digest(root) != digest:
            raise HarnessError("Candidate or session changed during acceptance.")
        state["acceptance"] = {"accepted_at": utc_now(), "head": candidate.head, "candidate": candidate.binding(),
                               "contract_id": state["contract_id"], "explicit_approval": True}
        state["status"] = "finished"
        write_state(root, state)
        return state


def abort_session(root: Path) -> dict:
    with lifecycle_lock(root):
        state = active_state(root)
        state.update(status="aborted", aborted_at=utc_now(), verification=None, acceptance=None)
        write_state(root, state)
        return state
