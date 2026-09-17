"""Closed authoring schemas and the single canonical, effective authority format.

IDs are integrity checksums, not signatures. Arrays preserve declared order;
sets of scope patterns are sorted/deduplicated during composition.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tomllib
from pathlib import Path
from typing import Any

from .core import HarnessError, run_git_bytes

PROJECT_VERSION = 2
TASK_VERSION = 1
CONTRACT_VERSION = 1
DEFAULT_MAX_COMMITS = 20  # A default, never a universal ceiling.
BUILTIN_PROTECTED = [".ekzd/project.toml", ".ekzd/local/", ".ekzd/session.json"]
LEGACY_MESSAGE = "Unsupported legacy EkzD session/task format. Remove the old local session and start a new task with the current schema."


def _object(value: Any, allowed: set[str], label: str) -> dict:
    if not isinstance(value, dict):
        raise HarnessError(f"{label} must be an object/table.")
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise HarnessError(f"{label} contains unknown key: {unknown[0]}")
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise HarnessError(f"{label} must be a non-empty string without NUL.")
    return value


def _strings(value: Any, label: str, *, required: bool = False, paths: bool = False) -> list[str]:
    if not isinstance(value, list) or (required and not value):
        raise HarnessError(f"{label} must be {'a non-empty' if required else 'a'} string array.")
    result = [_text(item, label) for item in value]
    if paths:
        for item in result:
            if Path(item).is_absolute() or ".." in Path(item).parts:
                raise HarnessError(f"{label} must stay inside the project: {item}")
    return result


def _positive(value: Any, label: str) -> int:
    if type(value) is not int or value < 1:
        raise HarnessError(f"{label} must be a positive integer.")
    return value


def _version(value: Any, expected: int, label: str) -> None:
    if type(value) is not int or value != expected:
        raise HarnessError(f"Unsupported {label} version; expected integer {expected}. {LEGACY_MESSAGE}")


def verification_steps(value: Any, *, required: bool = False) -> list[dict]:
    if not isinstance(value, list) or (required and not value):
        raise HarnessError("verification must be a non-empty array of steps." if required else "verification must be an array of steps.")
    result = []
    for index, raw in enumerate(value):
        label = f"verification[{index}]"
        step = _object(raw, {"name", "command", "cwd", "timeout_seconds"}, label)
        command = _strings(step.get("command"), f"{label}.command", required=True)
        cwd = _strings([step.get("cwd", ".")], f"{label}.cwd", paths=True)[0]
        timeout = _positive(step.get("timeout_seconds", 600), f"{label}.timeout_seconds")
        if timeout > 3600:
            raise HarnessError(f"{label}.timeout_seconds must be at most 3600.")
        result.append({"name": _text(step.get("name"), f"{label}.name"), "command": command,
                       "cwd": cwd, "timeout_seconds": timeout})
    return result


def parse_project(data: Any, *, ready: bool = True) -> dict:
    if isinstance(data, dict):
        _version(data.get("schema_version"), PROJECT_VERSION, "project schema")
    data = _object(data, {"schema_version", "name", "protected", "exclude", "guidance", "max_commits", "verification"}, "project")
    return {"schema_version": PROJECT_VERSION, "name": _text(data.get("name"), "project.name"),
            "protected": sorted(set(_strings(data.get("protected", []), "project.protected", paths=True))),
            "exclude": sorted(set(_strings(data.get("exclude", []), "project.exclude", paths=True))),
            "guidance": _strings(data.get("guidance", []), "project.guidance"),
            "max_commits": _positive(data.get("max_commits", DEFAULT_MAX_COMMITS), "project.max_commits"),
            "verification": verification_steps(data.get("verification", []), required=ready)}


def parse_task(data: Any) -> dict:
    data = _object(data, {"schema_version", "objective", "branch", "include", "exclude", "sources", "acceptance", "guidance", "max_commits", "verification"}, "task")
    _version(data.get("schema_version"), TASK_VERSION, "task schema")
    result = {"schema_version": TASK_VERSION,
              "objective": _text(data.get("objective"), "task.objective"),
              "branch": _text(data.get("branch"), "task.branch"),
              "include": sorted(set(_strings(data.get("include"), "task.include", required=True, paths=True))),
              "exclude": sorted(set(_strings(data.get("exclude", []), "task.exclude", paths=True))),
              "sources": sorted(set(_strings(data.get("sources", []), "task.sources", paths=True))),
              "acceptance": _strings(data.get("acceptance"), "task.acceptance", required=True),
              "guidance": _strings(data.get("guidance", []), "task.guidance"),
              "verification": verification_steps(data.get("verification", []))}
    if "max_commits" in data:
        result["max_commits"] = _positive(data["max_commits"], "task.max_commits")
    return result


def read_toml(path: Path) -> dict:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise HarnessError(f"Unable to read {path}: {exc}") from exc


def canonical_bytes(value: dict) -> bytes:
    """UTF-8, sorted object keys, compact separators, exactly one LF."""
    try:
        return (json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise HarnessError(f"Cannot serialize canonical contract: {exc}") from exc


def contract_id(contract: dict) -> str:
    return hashlib.sha256(canonical_bytes(contract)).hexdigest()


def _hex(value: Any, lengths: tuple[int, ...], label: str) -> str:
    if not isinstance(value, str) or len(value) not in lengths or not re.fullmatch("[0-9a-f]+", value):
        raise HarnessError(f"{label} must be an exact lowercase hexadecimal identity.")
    return value


def validate_contract(data: Any) -> dict:
    if isinstance(data, dict):
        _version(data.get("contract_version"), CONTRACT_VERSION, "contract")
    data = _object(data, {"contract_version", "baseline", "branch", "project", "objective", "scope", "sources", "acceptance", "guidance", "max_commits", "verification", "ekzd"}, "contract")
    _hex(data.get("baseline"), (40, 64), "contract.baseline")
    for key in ("branch", "objective"):
        _text(data.get(key), f"contract.{key}")
    project = _object(data.get("project"), {"name", "config_sha256"}, "contract.project")
    _text(project.get("name"), "contract.project.name")
    _hex(project.get("config_sha256"), (64,), "contract.project.config_sha256")
    scope = _object(data.get("scope"), {"include", "exclude", "protected"}, "contract.scope")
    for key in ("include", "exclude", "protected"):
        _strings(scope.get(key), f"contract.scope.{key}", paths=True, required=key == "include")
        if scope[key] != sorted(set(scope[key])):
            raise HarnessError(f"contract.scope.{key} must be sorted and unique.")
    if not set(BUILTIN_PROTECTED).issubset(scope["protected"]):
        raise HarnessError("Contract must retain built-in EkzD protected paths.")
    for key in ("sources", "acceptance", "guidance"):
        _strings(data.get(key), f"contract.{key}", required=key == "acceptance", paths=key == "sources")
    _positive(data.get("max_commits"), "contract.max_commits")
    if verification_steps(data.get("verification"), required=True) != data["verification"]:
        raise HarnessError("Contract verification steps must contain resolved defaults.")
    identity = _object(data.get("ekzd"), {"version", "build_sha256"}, "contract.ekzd")
    _text(identity.get("version"), "contract.ekzd.version")
    _hex(identity.get("build_sha256"), (64,), "contract.ekzd.build_sha256")
    return data


def compose_contract(project: dict, task: dict, baseline: str, identity: dict) -> dict:
    project, task = parse_project(project), parse_task(task)
    return validate_contract({
        "contract_version": CONTRACT_VERSION, "baseline": baseline, "branch": task["branch"],
        "project": {"name": project["name"], "config_sha256": contract_id(project)},
        "objective": task["objective"],
        "scope": {"include": task["include"], "exclude": sorted(set(project["exclude"] + task["exclude"])),
                  "protected": sorted(set(BUILTIN_PROTECTED + project["protected"]))},
        "sources": task["sources"], "acceptance": task["acceptance"],
        "guidance": project["guidance"] + task["guidance"],
        "max_commits": task.get("max_commits", project["max_commits"]),
        "verification": project["verification"] + task["verification"], "ekzd": dict(identity),
    })


def validate_baseline_sources(root: Path, contract: dict) -> None:
    for source in contract["sources"]:
        entry = run_git_bytes(root, "ls-tree", "-z", contract["baseline"], "--", f":(literal){source}")
        if not entry.startswith((b"100644 blob ", b"100755 blob ")):
            raise HarnessError(f"Source does not exist as a regular file at frozen baseline: {source}")


def validate_branch(root: Path, branch: str) -> None:
    result = subprocess.run(["git", "check-ref-format", "--branch", branch], cwd=root, capture_output=True)
    if branch.startswith("-") or result.returncode or result.stdout.decode().strip() != branch:
        raise HarnessError(f"Invalid literal implementation branch: {branch!r}")


def load_contract(path: Path) -> dict:
    try:
        raw = path.read_bytes()
        data = validate_contract(json.loads(raw.decode("utf-8")))
        if raw != canonical_bytes(data):
            raise HarnessError("Frozen contract must use canonical JSON bytes; export again with `ekzd contract`.")
        return data
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise HarnessError(f"Unable to read frozen contract. {LEGACY_MESSAGE} ({exc})") from exc
