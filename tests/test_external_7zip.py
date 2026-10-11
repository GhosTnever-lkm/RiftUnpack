"""Paths that hand the archive to a 7-Zip executable (skipped when 7za is not installed)."""

from __future__ import annotations

import archive_factory as make
import pytest

from riftunpack.engine import ExtractOptions, extract_archive
from riftunpack.engine import _extract_via_7zip as extract_via_7zip
from riftunpack.errors import CancelledError, PasswordRequiredError, RiftUnpackError

needs_7za = pytest.mark.skipif(make.SEVEN_ZIP_BIN is None, reason="7-Zip (7za) not installed")


@needs_7za
def test_external_7zip_keeps_include_selection(tmp_path):
    # Regression: an include mask used to delete every staged file on the external path.
    archive = make.write_7z(tmp_path / "mask.7z", {"keep.pak": b"1", "drop.txt": b"2"})
    destination = tmp_path / "out"
    extract_via_7zip(archive, destination, "7z", ExtractOptions(include=["*.pak"]), None, None)
    assert (destination / "keep.pak").read_bytes() == b"1"
    assert not (destination / "drop.txt").exists()


@needs_7za
def test_external_7zip_handles_thousands_of_entries(tmp_path):
    # Regression: unread stdout of a large listing used to block 7-Zip forever.
    files = {f"many/f{i:04d}.txt": f"item {i}".encode() for i in range(3000)}
    archive = make.write_7z(tmp_path / "many.7z", files)
    destination = tmp_path / "out"
    report = extract_via_7zip(archive, destination, "7z", ExtractOptions(), None, None)
    assert report.extracted == 3000
    assert (destination / "many" / "f2999.txt").read_bytes() == b"item 2999"


@needs_7za
def test_external_7zip_password_errors(tmp_path):
    archive = make.write_zip_7za(tmp_path / "pw.zip", {"s.txt": b"x"}, "pw123")
    with pytest.raises(PasswordRequiredError):
        extract_via_7zip(archive, tmp_path / "none", "zip", ExtractOptions(), None, None)
    with pytest.raises(PasswordRequiredError):
        extract_via_7zip(
            archive, tmp_path / "bad", "zip", ExtractOptions(password="wrong"), None, None
        )


@needs_7za
def test_external_7zip_can_be_cancelled(tmp_path):
    files = {f"c/f{i:04d}.bin": bytes([i % 251]) * 4000 for i in range(400)}
    archive = make.write_7z(tmp_path / "cancel.7z", files)
    with pytest.raises(CancelledError):
        extract_via_7zip(archive, tmp_path / "out", "7z", ExtractOptions(), None, lambda: True)


@needs_7za
def test_7z_falls_back_to_7zip_when_py7zr_fails(tmp_path, monkeypatch):
    archive = make.write_7z(tmp_path / "fallback.7z", {"f.txt": b"fallback"})

    def broken(*args, **kwargs):
        raise RiftUnpackError("py7zr exploded")

    monkeypatch.setattr("riftunpack.engine._py7zr_extract", broken)
    extract_archive(archive, tmp_path / "out")
    assert (tmp_path / "out" / "f.txt").read_bytes() == b"fallback"


def test_7z_failure_without_7zip_is_reported(tmp_path, monkeypatch):
    archive = make.write_7z(tmp_path / "nofallback.7z", {"f.txt": b"x"})

    def broken(*args, **kwargs):
        raise RiftUnpackError("py7zr exploded")

    monkeypatch.setattr("riftunpack.engine._py7zr_extract", broken)
    monkeypatch.setattr("riftunpack.engine._seven_zip_binary", lambda executable=None: None)
    with pytest.raises(RiftUnpackError, match="py7zr exploded"):
        extract_archive(archive, tmp_path / "out")


@needs_7za
def test_split_zip_volumes_use_7zip(tmp_path):
    """Split ZIP sets written by 7-Zip (name.zip.001 ...) are read through 7-Zip."""
    source = tmp_path / "src"
    source.mkdir()
    (source / "a.bin").write_bytes(bytes(range(256)) * 400)
    import subprocess

    subprocess.run(
        [make.SEVEN_ZIP_BIN, "a", "-tzip", "-v40k", str(tmp_path / "vol.zip"), "a.bin"],
        cwd=source,
        check=True,
        capture_output=True,
    )
    first = sorted(tmp_path.glob("vol.zip.*"))[0]
    extract_archive(first, tmp_path / "out")
    assert (tmp_path / "out" / "a.bin").read_bytes() == (source / "a.bin").read_bytes()
