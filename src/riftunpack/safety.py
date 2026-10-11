"""Path and metadata checks shared by every extraction backend."""

from __future__ import annotations

import fnmatch
import re
import stat
from pathlib import Path, PurePosixPath

from .errors import UnsafeArchiveError


def safe_member_name(raw: str) -> str:
    """Validate an archive name and return a normalized relative POSIX path."""
    if not raw or "\x00" in raw:
        raise UnsafeArchiveError("Archive contains an empty or invalid file name.")
    name = raw.replace("\\", "/")
    if name.startswith("/") or re.match(r"^[a-zA-Z]:", name) or name.startswith("//"):
        raise UnsafeArchiveError(f"Unsafe absolute archive path: {raw!r}")
    parts = [part for part in PurePosixPath(name).parts if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        raise UnsafeArchiveError(f"Unsafe archive path traversal: {raw!r}")
    return "/".join(parts)


def ensure_no_symlink_path(root: Path, target: Path) -> None:
    """Ensure target remains under root and no existing path component is a symlink."""
    root_abs = root.absolute()
    target_abs = target.absolute()
    try:
        target_abs.relative_to(root_abs)
    except ValueError as exc:
        raise UnsafeArchiveError(f"Output path escaped destination: {target}") from exc
    cursor = root_abs
    for part in target_abs.relative_to(root_abs).parts:
        cursor = cursor / part
        try:
            mode = cursor.lstat().st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(mode):
            raise UnsafeArchiveError(f"Refusing to write through symbolic link: {cursor}")


def matches_patterns(name: str, patterns: list[str] | None) -> bool:
    """Match a member against one or more shell-style patterns."""
    if not patterns:
        return True
    return any(
        fnmatch.fnmatchcase(name, pattern) or PurePosixPath(name).match(pattern)
        for pattern in patterns
    )


def safe_join(root: Path, member_name: str) -> Path:
    """Build a safe child path under root; rejects traversal and absolute paths."""
    normalized = safe_member_name(member_name)
    target = root.joinpath(*normalized.split("/"))
    ensure_no_symlink_path(root, target)
    return target
