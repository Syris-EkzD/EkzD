from __future__ import annotations

from pathlib import Path
from typing import Any

from . import __version__
from .identity import BuildIdentityUnavailable, runtime_identity
from .task import task_identity

CHECK_STATES = frozenset({"PASS", "FAIL", "UNAVAILABLE", "WARN"})


def check_result(name: str, status: str, message: str, *, required: bool = True, **details: Any) -> dict[str, Any]:
    if status not in CHECK_STATES:
        raise ValueError(f"Unsupported check status: {status}")
    result: dict[str, Any] = {
        "name": name,
        "status": status,
        "required": required,
        "message": message,
    }
    if details:
        result["details"] = details
    return result


def finalize_worker_result(result: dict[str, Any]) -> dict[str, Any]:
    blocking = [
        item["status"]
        for item in result["checks"]
        if item.get("required", True) and item.get("status") in {"FAIL", "UNAVAILABLE"}
    ]
    result["ready"] = not blocking
    if "FAIL" in blocking:
        result["overall_status"] = "FAIL"
    elif "UNAVAILABLE" in blocking:
        result["overall_status"] = "UNAVAILABLE"
    else:
        result["overall_status"] = "PASS"
    return result


def _best_effort_runtime_identity() -> dict[str, str | None]:
    try:
        return runtime_identity()
    except BuildIdentityUnavailable:
        return {"version": __version__, "build_sha256": None}


def empty_worker_result(
    task_path: Path,
    *,
    final: bool,
    ekzd_identity: dict[str, str | None] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "overall_status": "UNAVAILABLE",
        "ready": False,
        "mode": "final" if final else "development",
        "ekzd": ekzd_identity if ekzd_identity is not None else _best_effort_runtime_identity(),
        "task": {"path": str(task_path.expanduser().resolve()), "sha256": None},
        "baseline": None,
        "git": {
            "head": None,
            "branch": None,
            "clean": None,
            "worktree_fingerprint": None,
        },
        "checks": [],
    }


def unavailable_worker_result(task_path: Path, *, final: bool, message: str) -> dict[str, Any]:
    result = empty_worker_result(task_path, final=final)
    result["task"] = task_identity(task_path)
    result["checks"].append(check_result("worker-check", "UNAVAILABLE", message))
    return finalize_worker_result(result)


def render_worker_check(result: dict[str, Any]) -> str:
    lines = [
        "EkzD · check",
        f"status  {result['overall_status']}",
        f"mode    {result['mode']}",
    ]
    ekzd = result.get("ekzd", {})
    if ekzd.get("version"):
        lines.append(f"ekzd    {ekzd['version']}")
    lines.append(f"build   {ekzd.get('build_sha256') or 'unavailable'}")
    task = result.get("task", {})
    if task.get("sha256"):
        lines.append(f"task    {task['sha256']}")
    if result.get("baseline"):
        lines.append(f"base    {result['baseline']}")
    git = result.get("git", {})
    if git.get("head"):
        lines.append(f"HEAD    {git['head']}")
        lines.append(f"branch  {git.get('branch')}")
        lines.append(f"tree    {'clean' if git.get('clean') else 'dirty'}")
        if git.get("worktree_fingerprint"):
            lines.append(f"state   {git['worktree_fingerprint']}")
    lines.append("")
    for item in result.get("checks", []):
        marker = {"PASS": "✓", "FAIL": "✗", "UNAVAILABLE": "!", "WARN": "!"}.get(item["status"], "-")
        lines.append(f"{marker} {item['status']:<11} {item['name']}: {item['message']}")
        if item["status"] in {"FAIL", "UNAVAILABLE"}:
            details = item.get("details", {})
            for label in ("stderr", "stdout"):
                output = details.get(label)
                if isinstance(output, str) and output.strip():
                    lines.extend(f"    {label}: {line}" for line in output.rstrip().splitlines())
    lines.append("")
    lines.append("Ready for handoff." if result.get("ready") else "Not ready for handoff.")
    return "\n".join(lines) + "\n"
