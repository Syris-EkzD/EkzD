from __future__ import annotations

import fnmatch
import subprocess
import tomllib
from datetime import datetime, timezone
from pathlib import Path

CONFIG_RELATIVE = Path(".ekzd/project.toml")



class HarnessError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise HarnessError(f"git {' '.join(args)} failed: {detail}")
    return result.stdout.strip()


def run_git_bytes(root: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip() or result.stdout.decode("utf-8", errors="replace").strip()
        raise HarnessError(f"git {' '.join(args)} failed: {detail}")
    return result.stdout


def hidden_index_paths(root: Path) -> list[str]:
    hidden: set[str] = set()
    for entry in run_git_bytes(root, "ls-files", "-v", "-z").split(b"\0"):
        if not entry:
            continue
        if len(entry) < 3 or entry[1:2] != b" ":
            raise HarnessError("Unable to parse Git index visibility flags.")
        tag = entry[:1]
        if tag == b"S" or tag.islower():
            hidden.add(entry[2:].decode("utf-8", errors="surrogateescape"))
    return sorted(hidden)


def ensure_index_paths_visible(root: Path) -> None:
    hidden = hidden_index_paths(root)
    if hidden:
        raise HarnessError(
            "Git index flags hide tracked paths from EkzD; clear assume-unchanged/skip-worktree before continuing:\n- "
            + "\n- ".join(hidden)
        )


def filtered_tracked_paths(root: Path) -> list[str]:
    tracked = [path for path in run_git_bytes(root, "ls-files", "-z").split(b"\0") if path]
    if not tracked:
        return []
    result = subprocess.run(
        ["git", "check-attr", "-z", "--stdin", "filter"],
        cwd=root,
        input=b"".join(path + b"\0" for path in tracked),
        capture_output=True,
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip() or result.stdout.decode("utf-8", errors="replace").strip()
        raise HarnessError(f"git check-attr filter failed: {detail}")
    fields = result.stdout.split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()
    if len(fields) % 3 != 0:
        raise HarnessError("Unable to parse Git filter attributes.")
    filtered: set[str] = set()
    for index in range(0, len(fields), 3):
        path, attribute, value = fields[index : index + 3]
        if attribute != b"filter":
            raise HarnessError("Unable to parse Git filter attributes.")
        if value not in {b"unspecified", b"unset"}:
            filtered.add(path.decode("utf-8", errors="surrogateescape"))
    return sorted(filtered)


def ensure_tracked_paths_unfiltered(root: Path) -> None:
    filtered = filtered_tracked_paths(root)
    if filtered:
        raise HarnessError(
            "Git content filters are unsupported for tracked files; remove the filter attribute before continuing:\n- "
            + "\n- ".join(filtered)
        )


def ensure_git_visibility_supported(root: Path) -> None:
    from .candidate import ensure_supported_repository

    ensure_supported_repository(root)
    ensure_index_paths_visible(root)
    ensure_tracked_paths_unfiltered(root)


def ensure_project_config_committed_clean(root: Path) -> None:
    ensure_git_visibility_supported(root)
    path = CONFIG_RELATIVE.as_posix()
    tree_entry = run_git(root, "ls-tree", "HEAD", "--", path)
    if not tree_entry:
        raise HarnessError(
            "Project harness configuration must be committed before starting a task: "
            f"{path}"
        )
    mode = tree_entry.split(None, 1)[0]
    if mode not in {"100644", "100755"} or (root / CONFIG_RELATIVE).is_symlink():
        raise HarnessError(
            "Project harness configuration must be a regular tracked file; symlinked contracts are not supported: "
            f"{path}"
        )
    unstaged = run_git(root, "diff", "--no-ext-diff", "--no-textconv", "--name-only", "--no-renames", "--", path)
    staged = run_git(root, "diff", "--no-ext-diff", "--no-textconv", "--cached", "--name-only", "--no-renames", "--", path)
    if unstaged or staged:
        raise HarnessError(
            "Project policy must be clean before freezing a task; "
            "abort the session, restore or commit the configuration outside the session, then start a fresh session."
        )


def find_root(start: Path | None = None) -> Path:
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    raise HarnessError("EkzD must run inside a Git repository.")


def committed_config_bytes(root: Path) -> bytes:
    return run_git_bytes(root, "show", f"HEAD:{CONFIG_RELATIVE.as_posix()}")


def _decode_git_paths(output: bytes) -> set[str]:
    return {raw.decode("utf-8", errors="surrogateescape") for raw in output.split(b"\0") if raw}


def session_history_paths(root: Path, start_head: str) -> set[str]:
    return _decode_git_paths(
        run_git_bytes(
            root,
            "log",
            "-m",
            "--format=",
            "--name-only",
            "--no-renames",
            "--no-ext-diff",
            "--no-textconv",
            "-z",
            f"{start_head}..HEAD",
        )
    )


def _matches_scope(path: str, pattern: str) -> bool:
    normalized = pattern.rstrip("/")
    if pattern.endswith("/"):
        return path == normalized or path.startswith(normalized + "/")
    return fnmatch.fnmatchcase(path, pattern)


def session_commit_count(root: Path, start_head: str) -> int:
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", start_head, "HEAD"],
        cwd=root,
        capture_output=True,
    )
    if ancestor.returncode == 1:
        raise HarnessError("Session history diverged from its starting commit; start a fresh session.")
    if ancestor.returncode != 0:
        detail = ancestor.stderr.decode("utf-8", errors="replace").strip() or ancestor.stdout.decode("utf-8", errors="replace").strip()
        raise HarnessError(f"Unable to validate session Git history: {detail}")
    try:
        return int(run_git(root, "rev-list", "--count", f"{start_head}..HEAD"))
    except ValueError as exc:
        raise HarnessError("Unable to count commits in the active session.") from exc


def load_config(root: Path, *, ready: bool = True) -> dict:
    from .contract import parse_project, read_toml
    return parse_project(read_toml(root / CONFIG_RELATIVE), ready=ready)


def load_committed_config(root: Path, *, ready: bool = True) -> dict:
    from .contract import parse_project
    ensure_project_config_committed_clean(root)
    try:
        data = tomllib.loads(committed_config_bytes(root).decode("utf-8"))
    except (UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise HarnessError(f"Unable to read committed {CONFIG_RELATIVE}: {exc}") from exc
    return parse_project(data, ready=ready)


def init_project(root: Path, name: str | None = None) -> Path:
    import json
    from .contract import DEFAULT_MAX_COMMITS, PROJECT_VERSION

    path = root / CONFIG_RELATIVE
    if path.exists() or path.is_symlink():
        raise HarnessError(f"{CONFIG_RELATIVE} already exists.")
    if path.parent.is_symlink():
        raise HarnessError(".ekzd must not be a symlink.")
    path.parent.mkdir(parents=True, exist_ok=True)
    project_name = (name or root.name).strip()
    if not project_name:
        raise HarnessError("Project name cannot be empty.")
    path.write_text(
        f'schema_version = {PROJECT_VERSION}\nname = {json.dumps(project_name)}\n'
        'protected = []\nexclude = []\nguidance = []\n'
        f'max_commits = {DEFAULT_MAX_COMMITS}\n'
        '# Add at least one required verification step before starting a task.\n'
        '#\n'
        '# [[verification]]\n'
        '# name = "tests"\n'
        '# command = ["..."]\n'
        '# cwd = "."\n'
        '# timeout_seconds = 600\n', encoding="utf-8")
    gitignore = root / ".gitignore"
    if gitignore.is_symlink():
        raise HarnessError(".gitignore must not be a symlink.")
    existing = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    if "/.ekzd/local/" not in existing.splitlines():
        separator = "" if not existing or existing.endswith("\n") else "\n"
        gitignore.write_text(existing + separator + "/.ekzd/local/\n", encoding="utf-8")
    return path
