"""Edge-case error handling in the extraction engine."""

import zipfile
from pathlib import Path

import pytest
import rar4_factory

from riftunpack import engine
from riftunpack.engine import ExtractOptions, extract_archive
from riftunpack.errors import RiftUnpackError, UnsafeArchiveError, UnsupportedFormatError


def test_corrupt_zip_with_signature_is_friendly(tmp_path):
    bad = tmp_path / "broken.zip"
    bad.write_bytes(b"PK\x03\x04" + b"\x00" * 32)
    with pytest.raises(RiftUnpackError, match="ZIP"):
        extract_archive(bad, tmp_path / "out")


def test_zip_with_alternate_data_stream_member_is_rejected_before_output(tmp_path):
    archive = tmp_path / "ads.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("visible.txt", "normal")
        handle.writestr("visible.txt:hidden_payload", "hidden")

    output = tmp_path / "out"
    with pytest.raises(UnsafeArchiveError, match="alternate data stream"):
        extract_archive(archive, output)
    assert not output.exists()


def test_corrupt_tar_with_signature_is_friendly(tmp_path):
    bad = tmp_path / "broken.tar"
    header = bytearray(b"\x00" * 512)
    header[257:262] = b"ustar"
    bad.write_bytes(bytes(header) + b"garbage")
    with pytest.raises(RiftUnpackError, match="TAR"):
        extract_archive(bad, tmp_path / "out")


def test_corrupt_7z_is_friendly(tmp_path):
    bad = tmp_path / "broken.7z"
    bad.write_bytes(b"7z\xbc\xaf\x27\x1c" + b"\x00" * 64)
    with pytest.raises(RiftUnpackError):
        extract_archive(bad, tmp_path / "out")


def test_rar4_stored_extracts_through_rarfile_backend(tmp_path):
    archive = rar4_factory.write_rar4_stored(tmp_path / "plain.rar", {"doc.txt": b"hello rar"})
    out = tmp_path / "out"
    extract_archive(archive, out)
    assert (out / "doc.txt").read_bytes() == b"hello rar"


def test_rar_tool_detection_without_tool(monkeypatch):
    import rarfile

    def no_tool():
        raise rarfile.RarCannotExec("no unrar/unar/7z available")

    monkeypatch.setattr(rarfile, "tool_setup", no_tool)
    assert engine._rar_python_tool_available() is False


def test_rar_members_parsed_from_rar4(tmp_path):
    archive = rar4_factory.write_rar4_stored(tmp_path / "plain.rar", {"doc.txt": b"hello rar"})
    members, handle = engine._rar_members(archive, None, None)
    try:
        assert [m.name for m in members] == ["doc.txt"]
        assert members[0].size == len(b"hello rar")
    finally:
        handle.close()


def test_backend_reports_missing_format_support():
    with pytest.raises(UnsupportedFormatError, match="zst"):
        engine._backend(Path("x.zst"), "zst", ExtractOptions())
