from __future__ import annotations

import hashlib
import os
from pathlib import Path

from . import __version__


class BuildIdentityUnavailable(RuntimeError):
    """Raised when the executing EkzD source set cannot be fingerprinted exactly."""


def _python_sources(package_dir: Path) -> tuple[Path, ...]:
    root = package_dir
    try:
        if not root.is_dir():
            raise BuildIdentityUnavailable(f"EkzD package directory is unavailable: {root}")
    except OSError as exc:
        raise BuildIdentityUnavailable("Unable to inspect the executing EkzD package directory.") from exc

    files: list[Path] = []
    stack = [root]
    try:
        while stack:
            current = stack.pop()
            with os.scandir(current) as entries:
                for entry in entries:
                    if entry.is_symlink():
                        if entry.name.endswith(".py") or entry.is_dir(follow_symlinks=True):
                            raise BuildIdentityUnavailable(
                                "Executing EkzD package contains a symlinked Python source path."
                            )
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(Path(entry.path))
                        continue
                    if entry.name.endswith(".py"):
                        if not entry.is_file(follow_symlinks=False):
                            raise BuildIdentityUnavailable(
                                "Executing EkzD package contains a non-regular Python source path."
                            )
                        files.append(Path(entry.path))
    except BuildIdentityUnavailable:
        raise
    except OSError as exc:
        raise BuildIdentityUnavailable("Unable to enumerate the executing EkzD Python source set.") from exc

    if not files:
        raise BuildIdentityUnavailable("Executing EkzD package contains no Python source files.")

    try:
        return tuple(sorted(files, key=lambda path: path.relative_to(root).as_posix()))
    except (OSError, ValueError) as exc:
        raise BuildIdentityUnavailable("Unable to determine stable relative paths for EkzD Python sources.") from exc


def _normalized_source_bytes(path: Path) -> bytes:
    try:
        if path.is_symlink() or not path.is_file():
            raise BuildIdentityUnavailable("Executing EkzD package contains an unusable Python source path.")
        raw = path.read_bytes()
    except BuildIdentityUnavailable:
        raise
    except OSError as exc:
        raise BuildIdentityUnavailable("Unable to read an executing EkzD Python source file.") from exc
    return raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def build_sha256(package_dir: Path | None = None) -> str:
    """Return the exact source-content build SHA-256 for an EkzD package directory."""

    root = Path(__file__).parent if package_dir is None else Path(package_dir)
    digest = hashlib.sha256()
    digest.update(b"ekzd-python-build-v1\0")

    for source in _python_sources(root):
        try:
            relative = source.relative_to(root).as_posix().encode("utf-8")
        except (UnicodeError, ValueError) as exc:
            raise BuildIdentityUnavailable("Unable to encode an EkzD Python source path deterministically.") from exc
        normalized = _normalized_source_bytes(source)
        digest.update(b"path\0")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(b"source\0")
        digest.update(len(normalized).to_bytes(8, "big"))
        digest.update(normalized)

    return digest.hexdigest()


def runtime_identity() -> dict[str, str]:
    return {"version": __version__, "build_sha256": build_sha256()}
