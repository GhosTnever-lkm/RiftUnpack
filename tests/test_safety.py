"""Unit tests for shared path-safety helpers."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from riftunpack.errors import UnsafeArchiveError
from riftunpack.safety import matches_patterns, safe_join, safe_member_name


def test_empty_and_nul_names_are_rejected():
    with pytest.raises(UnsafeArchiveError, match="empty or invalid"):
        safe_member_name("")
    with pytest.raises(UnsafeArchiveError, match="empty or invalid"):
        safe_member_name("bad\x00name.txt")


def test_normalizes_backslashes_and_dot_segments():
    assert safe_member_name(".\\dir\\.\\file.txt") == "dir/file.txt"


def test_absolute_and_drive_paths_are_rejected():
    for raw in ("/etc/passwd", "C:/Windows/x", "//server/share/x"):
        with pytest.raises(UnsafeArchiveError, match="absolute"):
            safe_member_name(raw)


def test_traversal_is_rejected():
    with pytest.raises(UnsafeArchiveError, match="traversal"):
        safe_member_name("a/../../b.txt")


@pytest.mark.parametrize(
    "raw",
    (
        "visible.txt:hidden_payload",
        "dir:stream/file.txt",
        "dir/file.txt:Zone.Identifier",
    ),
)
def test_windows_alternate_data_stream_names_are_rejected(raw):
    with pytest.raises(UnsafeArchiveError, match="alternate data stream"):
        safe_member_name(raw)


def test_safe_join_keeps_target_under_root(tmp_path: Path):
    target = safe_join(tmp_path, "sub/file.txt")
    assert target == tmp_path / "sub" / "file.txt"


def test_matches_patterns_defaults_to_match_all():
    assert matches_patterns("anything", None)
    assert matches_patterns("anything", [])


def test_matches_patterns_with_glob_and_path_forms():
    assert matches_patterns("data/a.txt", ["*.txt"])
    assert matches_patterns("data/a.txt", ["data/*"])
    assert not matches_patterns("data/a.bin", ["*.txt"])


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks unsupported")
def test_refuses_to_write_through_symlink(tmp_path: Path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(real, target_is_directory=True)
    except OSError:
        pytest.skip("cannot create symlinks on this system")
    with pytest.raises(UnsafeArchiveError, match="symbolic link"):
        safe_join(tmp_path, "link/file.txt")


def test_output_outside_root_is_rejected(tmp_path: Path):
    root = tmp_path / "root"
    root.mkdir()
    with pytest.raises(UnsafeArchiveError, match="escaped"):
        from riftunpack.safety import ensure_no_symlink_path

        ensure_no_symlink_path(root, tmp_path / "elsewhere" / "x.txt")
