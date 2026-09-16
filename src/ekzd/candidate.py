"""Git candidate evidence for conventional, quiescent Linux checkouts.

Git supplies paths and index/tree identities, never a rendered content diff.
Raw working bytes are hashed without filters or following symlinks. This is
change detection, not a filesystem sandbox or an atomic filesystem snapshot.
"""
from __future__ import annotations

import hashlib
import os
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .core import HarnessError, STATE_RELATIVE, ensure_git_visibility_supported, run_git, run_git_bytes


_OPERATIONS = (
    "MERGE_HEAD", "MERGE_AUTOSTASH", "CHERRY_PICK_HEAD", "REVERT_HEAD",
    "REBASE_HEAD", "rebase-merge", "rebase-apply", "sequencer", "BISECT_START",
    "index.lock",
)


def ensure_supported_repository(root: Path) -> None:
    if run_git(root, "rev-parse", "--is-shallow-repository") == "true":
        raise HarnessError("Shallow repositories are unsupported; fetch complete history before evaluation.")
    sparse = subprocess.run(["git", "config", "--bool", "core.sparseCheckout"], cwd=root, capture_output=True)
    if sparse.returncode not in (0, 1):
        raise HarnessError("Unable to inspect sparse-checkout configuration.")
    if sparse.stdout.strip() == b"true":
        raise HarnessError("Sparse checkout is unsupported; use a complete checkout.")
    for name in _OPERATIONS:
        path = Path(run_git(root, "rev-parse", "--git-path", name))
        if not path.is_absolute():
            path = root / path
        if path.exists() or path.is_symlink():
            raise HarnessError(f"Repository operation in progress ({name}); complete or abort it before evaluation.")
    grafts = Path(run_git(root, "rev-parse", "--git-path", "info/grafts"))
    if not grafts.is_absolute():
        grafts = root / grafts
    if run_git(root, "for-each-ref", "--format=%(refname)", "refs/replace/") or grafts.exists():
        raise HarnessError("Git replacement refs/grafts are unsupported; original history is required.")


def _index(root: Path) -> dict[bytes, tuple[bytes, bytes]]:
    entries = {}
    for record in run_git_bytes(root, "ls-files", "--stage", "-z").split(b"\0"):
        if not record:
            continue
        metadata, path = record.split(b"\t", 1)
        mode, oid, stage = metadata.split()
        if stage != b"0":
            raise HarnessError("Unresolved index conflicts; resolve and commit before evaluation.")
        if mode == b"160000":
            raise HarnessError("Submodules are unsupported, including dirty submodule working trees.")
        if mode not in (b"100644", b"100755", b"120000"):
            raise HarnessError(f"Unsupported index mode for {os.fsdecode(path)!r}.")
        entries[path] = (mode, oid)
    return entries


def _tree(root: Path) -> dict[bytes, tuple[bytes, bytes]]:
    entries = {}
    for record in run_git_bytes(root, "ls-tree", "-r", "-z", "HEAD").split(b"\0"):
        if record:
            metadata, path = record.split(b"\t", 1)
            mode, kind, oid = metadata.split()
            if mode == b"160000":
                raise HarnessError("Submodules are unsupported, including dirty submodule working trees.")
            entries[path] = (mode, oid)
    return entries


def _untracked(root: Path, tracked: set[bytes]) -> set[bytes]:
    # Git omits special files (e.g. FIFOs) from ls-files --others. Walk without
    # following links, pruning ignored directories before visiting their contents.
    paths: set[bytes] = set()
    stack = [root]
    while stack:
        directory = stack.pop()
        with os.scandir(directory) as scan:
            entries = {os.fsencode(Path(entry.path).relative_to(root)): entry for entry in scan
                       if not (directory == root and entry.name == ".git")}
        if not entries:
            continue
        ignored = subprocess.run(
            ["git", "check-ignore", "--no-index", "-z", "--stdin"], cwd=root,
            input=b"\0".join(entries) + b"\0", capture_output=True,
        )
        if ignored.returncode not in (0, 1):
            raise HarnessError("Unable to evaluate Git ignore rules for candidate paths.")
        excluded = set(ignored.stdout.split(b"\0"))
        for raw, entry in entries.items():
            if raw in excluded:
                continue
            if entry.is_dir(follow_symlinks=False):
                nested_git = Path(entry.path) / ".git"
                if nested_git.exists() or nested_git.is_symlink():
                    raise HarnessError(f"Nested repositories are unsupported: {os.fsdecode(raw)!r}")
                stack.append(Path(entry.path))
            elif raw not in tracked and raw != os.fsencode(STATE_RELATIVE):
                paths.add(raw)
    return paths


def _field(digest, value: bytes) -> None:
    digest.update(len(value).to_bytes(8, "big"))
    digest.update(value)


def _working_file(root: Path, raw_path: bytes, object_format: str) -> tuple[bytes, bytes, bytes]:
    path = root / os.fsdecode(raw_path)
    # A tracked directory replaced by a link must never redirect content reads.
    for parent in reversed(path.relative_to(root).parents):
        if parent == Path("."):
            continue
        location = root / parent
        if location.is_symlink():
            raise HarnessError(f"Candidate path traverses a symlink: {os.fsdecode(raw_path)!r}")
    try:
        before = path.lstat()
    except (FileNotFoundError, NotADirectoryError):
        return b"missing", b"", b""
    if stat.S_ISLNK(before.st_mode):
        payload = os.fsencode(path.readlink())
        mode = b"120000"
        content = hashlib.sha256(payload).digest()
        git_hash = hashlib.new(object_format, b"blob " + str(len(payload)).encode() + b"\0" + payload)
    elif stat.S_ISREG(before.st_mode):
        mode = b"100755" if before.st_mode & stat.S_IXUSR else b"100644"
        content_hash = hashlib.sha256()
        git_hash = hashlib.new(object_format, b"blob " + str(before.st_size).encode() + b"\0")
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as handle:
            if _file_identity(os.fstat(handle.fileno())) != _file_identity(before):
                raise HarnessError("Candidate changed while opening a working file.")
            while chunk := handle.read(1024 * 1024):
                content_hash.update(chunk)
                git_hash.update(chunk)
            after_read = os.fstat(handle.fileno())
        # atime is not candidate state and can change because of this read.
        if _file_identity(before) != _file_identity(after_read):
            raise HarnessError("Candidate changed while reading a working file.")
        content = content_hash.digest()
    else:
        raise HarnessError(f"Unsupported candidate file type or nested repository: {os.fsdecode(raw_path)!r}")
    if _file_identity(before) != _file_identity(path.lstat()):
        raise HarnessError("Candidate changed while capturing a working file.")
    return mode, git_hash.hexdigest().encode(), content


def _file_identity(value: os.stat_result) -> tuple[int, ...]:
    return (value.st_dev, value.st_ino, value.st_mode, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


@dataclass(frozen=True)
class Candidate:
    head: str
    branch: str
    fingerprint: str
    changed_paths: tuple[str, ...]

    @property
    def clean(self) -> bool:
        return not self.changed_paths

    def binding(self) -> dict[str, object]:
        return {"head": self.head, "branch": self.branch, "clean": self.clean,
                "worktree_fingerprint": self.fingerprint}


def capture_candidate(root: Path) -> Candidate:
    """Capture raw candidate evidence; reject unsupported or observed unstable state."""
    try:
        ensure_git_visibility_supported(root)
        head = run_git(root, "rev-parse", "HEAD")
        branch = run_git(root, "branch", "--show-current") or "(detached)"
        index = _index(root)
        tree = _tree(root)
        untracked = _untracked(root, set(index))
        object_format = run_git(root, "rev-parse", "--show-object-format")
        if object_format not in ("sha1", "sha256"):
            raise HarnessError("Unsupported Git object format.")
        digest = hashlib.sha256(b"ekzd-candidate-v1\0")
        _field(digest, head.encode())
        _field(digest, branch.encode())
        changed = set()
        for path in sorted(set(tree) | set(index) | untracked):
            _field(digest, path)
            staged = index.get(path)
            _field(digest, b"" if staged is None else b" ".join(staged))
            mode, oid, content = _working_file(root, path, object_format)
            for value in (mode, oid, content):
                _field(digest, value)
            if staged != tree.get(path) or staged != (mode, oid):
                # A committed deletion has no path in either tree or index.
                changed.add(os.fsdecode(path))
        ensure_git_visibility_supported(root)
        if (head != run_git(root, "rev-parse", "HEAD")
                or branch != (run_git(root, "branch", "--show-current") or "(detached)")
                or index != _index(root) or untracked != _untracked(root, set(index))):
            raise HarnessError("Candidate changed while capturing repository state.")
        return Candidate(head, branch, digest.hexdigest(), tuple(sorted(changed)))
    except (OSError, ValueError) as exc:
        raise HarnessError(f"Unable to capture candidate safely: {exc}") from exc


def require_final(candidate: Candidate, implementation_branch: str) -> None:
    if candidate.branch == "(detached)" or candidate.branch != implementation_branch:
        raise HarnessError(f"Final candidate must be on declared implementation branch {implementation_branch!r}; current branch is {candidate.branch!r}.")
    if not candidate.clean:
        raise HarnessError("Final candidate requires a clean, fully committed worktree/index (raw files must match Git):\n- " + "\n- ".join(candidate.changed_paths))
