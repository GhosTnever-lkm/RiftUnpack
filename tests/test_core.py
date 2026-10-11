from __future__ import annotations

import gzip
import io
import tarfile
import zipfile
from pathlib import Path

import pytest

from riftunpack.detect import detect_format, multipart_group
from riftunpack.engine import ExtractOptions, extract_archive, list_archive, verify_archive
from riftunpack.errors import ArchiveLimitError, UnsafeArchiveError


def make_zip(path: Path, files: dict[str, bytes], compression=zipfile.ZIP_DEFLATED):
    with zipfile.ZipFile(path, "w", compression=compression) as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return path


def test_zip_detect_extract_list_verify(tmp_path):
    archive = make_zip(tmp_path / "renamed.data", {"mod/readme.txt": b"hello", "data.pak": b"game"})
    assert detect_format(archive) == "zip"
    assert len(list_archive(archive)) == 2
    verify_archive(archive)
    report = extract_archive(archive, tmp_path / "out")
    assert report.extracted == 2
    assert (tmp_path / "out/mod/readme.txt").read_bytes() == b"hello"


def test_tar_and_targz(tmp_path):
    for name, mode in (("bundle.tar", "w"), ("bundle.tar.gz", "w:gz")):
        path = tmp_path / name
        with tarfile.open(path, mode) as tf:
            info = tarfile.TarInfo("mods/plugin.dll")
            payload = b"mod-resource"
            info.size = len(payload)
            tf.addfile(info, io.BytesIO(payload))
        assert detect_format(path) in {"tar", "tar.gz"}
        extract_archive(path, tmp_path / name.replace(".", "_"))


def test_gzip_single_stream(tmp_path):
    path = tmp_path / "localized.bin.gz"
    path.write_bytes(gzip.compress("Привет".encode()))
    assert detect_format(path) == "gz"
    extract_archive(path, tmp_path / "out")
    assert (tmp_path / "out/localized.bin").read_text(encoding="utf-8") == "Привет"


def test_signature_fixtures_for_optional_formats(tmp_path):
    fixtures = {
        "7z": b"7z\xbc\xaf\x27\x1c",
        "rar": b"Rar!\x1a\x07\x01\x00",
        "cab": b"MSCF",
        "zst": b"\x28\xb5\x2f\xfd",
        "bz2": b"BZh",
        "xz": b"\xfd7zXZ\x00",
    }
    for name, magic in fixtures.items():
        path = tmp_path / f"{name}.dat"
        path.write_bytes(magic + b"\x00" * 512)
        assert detect_format(path) == name


def test_iso_signature(tmp_path):
    path = tmp_path / "disc.blob"
    data = bytearray(0x8006)
    data[0x8000:0x8006] = b"\x01CD001"
    path.write_bytes(data)
    assert detect_format(path) == "iso"


def test_zip_slip_rejected_before_write(tmp_path):
    archive = make_zip(tmp_path / "bad.zip", {"../outside.txt": b"escape"})
    with pytest.raises(UnsafeArchiveError):
        extract_archive(archive, tmp_path / "out")
    assert not (tmp_path / "outside.txt").exists()


def test_absolute_and_windows_paths_rejected(tmp_path):
    for bad in ("/tmp/escape", "C:\\Windows\\system.ini", "a/../../escape"):
        archive = make_zip(tmp_path / "bad.zip", {bad: b"x"})
        with pytest.raises(UnsafeArchiveError):
            extract_archive(archive, tmp_path / ("out" + str(len(bad))))


def test_bomb_limits_size_ratio_and_count(tmp_path):
    archive = make_zip(tmp_path / "bomb.zip", {"large.txt": b"0" * 100_000})
    with pytest.raises(ArchiveLimitError):
        extract_archive(archive, tmp_path / "out", ExtractOptions(max_total_size=100))
    with pytest.raises(ArchiveLimitError):
        extract_archive(archive, tmp_path / "out2", ExtractOptions(max_ratio=2))
    with pytest.raises(ArchiveLimitError):
        extract_archive(archive, tmp_path / "out3", ExtractOptions(max_files=0))


def test_conflict_modes(tmp_path):
    archive = make_zip(tmp_path / "x.zip", {"file.txt": b"new"})
    out = tmp_path / "out"
    out.mkdir()
    (out / "file.txt").write_bytes(b"old")
    extract_archive(archive, out, ExtractOptions(conflict="skip"))
    assert (out / "file.txt").read_bytes() == b"old"
    extract_archive(archive, out, ExtractOptions(conflict="rename"))
    assert (out / "file (1).txt").read_bytes() == b"new"
    extract_archive(archive, out, ExtractOptions(conflict="overwrite"))
    assert (out / "file.txt").read_bytes() == b"new"


def test_password_is_ignored_for_unencrypted_archive(tmp_path):
    # A plaintext archive should not require a password.
    archive = make_zip(tmp_path / "plain.zip", {"a.txt": b"hello"})
    extract_archive(archive, tmp_path / "out", ExtractOptions(password="not-used"))
    assert (tmp_path / "out/a.txt").exists()


def test_encoding_override_does_not_affect_signature(tmp_path):
    archive = make_zip(tmp_path / "cyrillic.zip", {"моды/тест.txt": b"x"})
    assert detect_format(archive) == "zip"
    extract_archive(archive, tmp_path / "out", ExtractOptions(encoding="cp866"))


def test_multipart_7z_volumes(tmp_path):
    a = tmp_path / "game.7z.001"
    b = tmp_path / "game.7z.002"
    a.write_bytes(b"7z\xbc\xaf\x27\x1c")
    b.write_bytes(b"more")
    assert [p.name for p in multipart_group(a)] == [a.name, b.name]


def make_legacy_zip(path: Path, member_name: str, encoding: str, payload: bytes = b"hello"):
    import struct
    import zlib

    encoded = member_name.encode(encoding)
    placeholder = b"A" * len(encoded)
    crc = zlib.crc32(payload) & 0xFFFFFFFF
    local = struct.pack(
        "<IHHHHHIIIHH",
        0x04034B50,
        20,
        0,
        0,
        0,
        0,
        crc,
        len(payload),
        len(payload),
        len(placeholder),
        0,
    )
    central = struct.pack(
        "<IHHHHHHIIIHHHHHII",
        0x02014B50,
        20,
        20,
        0,
        0,
        0,
        0,
        crc,
        len(payload),
        len(payload),
        len(placeholder),
        0,
        0,
        0,
        0,
        0,
        0,
    )
    end = struct.pack(
        "<IHHHHIIH",
        0x06054B50,
        0,
        0,
        1,
        1,
        len(central) + len(placeholder),
        len(local) + len(placeholder) + len(payload),
        0,
    )
    path.write_bytes(local + placeholder + payload + central + placeholder + end)
    raw = path.read_bytes().replace(placeholder, encoded)
    path.write_bytes(raw)
    return path


def test_legacy_zip_encodings_manual_and_automatic(tmp_path):
    cases = [
        ("МОДЫ.txt", "cp866", None),
        ("ПАКЕТ.txt", "cp1251", None),
        ("テスト.txt", "shift_jis", "shift_jis"),
    ]
    for index, (name, encoding, override) in enumerate(cases):
        path = make_legacy_zip(tmp_path / f"legacy{index}.zip", name, encoding)
        out = tmp_path / f"legacy-out{index}"
        extract_archive(path, out, ExtractOptions(encoding=override))
        assert (out / name).read_bytes() == b"hello"


def test_py7zr_extract_and_password(tmp_path):
    import py7zr

    from riftunpack.errors import PasswordRequiredError

    path = tmp_path / "mods.7z"
    with py7zr.SevenZipFile(path, "w", password="secret") as archive:
        archive.writestr(b"game-mod", "mods/plugin.pak")
    assert detect_format(path) == "7z"
    out = tmp_path / "sevenzip-out"
    extract_archive(path, out, ExtractOptions(password="secret"))
    assert (out / "mods/plugin.pak").read_bytes() == b"game-mod"
    with pytest.raises(PasswordRequiredError):
        extract_archive(path, tmp_path / "bad-password", ExtractOptions(password="wrong"))


def test_tar_bz2_and_tar_xz(tmp_path):
    for name, mode in (("pack.tar.bz2", "w:bz2"), ("pack.tar.xz", "w:xz")):
        path = tmp_path / name
        with tarfile.open(path, mode) as archive:
            payload = b"compressed mod data"
            info = tarfile.TarInfo("data/plugin.bin")
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
        assert detect_format(path) == f"tar.{name.split('.')[-1]}"
        out = tmp_path / f"out-{name}"
        extract_archive(path, out)
        assert (out / "data/plugin.bin").read_bytes() == payload


def test_bz2_xz_and_zst_single_streams(tmp_path):
    import bz2
    import lzma

    import zstandard

    cases = {
        "texture.dat.bz2": bz2.compress(b"texture-bz2"),
        "texture.dat.xz": lzma.compress(b"texture-xz"),
        "texture.dat.zst": zstandard.ZstdCompressor().compress(b"texture-zst"),
    }
    for filename, contents in cases.items():
        path = tmp_path / filename
        path.write_bytes(contents)
        out = tmp_path / f"out-{filename}"
        extract_archive(path, out)
        expected = filename.rsplit(".", 1)[0]
        assert (out / expected).read_bytes().startswith(b"texture-")


def test_existing_symlink_is_never_followed(tmp_path):
    archive = make_zip(tmp_path / "link.zip", {"redirect/payload.txt": b"nope"})
    outside = tmp_path / "outside"
    outside.mkdir()
    out = tmp_path / "out"
    out.mkdir()
    (out / "redirect").symlink_to(outside, target_is_directory=True)
    with pytest.raises(UnsafeArchiveError):
        extract_archive(archive, out)
    assert not (outside / "payload.txt").exists()


def test_zip_symlink_entry_rejected(tmp_path):
    archive = tmp_path / "symlink.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        info = zipfile.ZipInfo("link")
        info.create_system = 3
        info.external_attr = 0o120777 << 16
        zf.writestr(info, "../../outside")
    with pytest.raises(UnsafeArchiveError):
        extract_archive(archive, tmp_path / "symlink-out")


def test_member_masks_and_nested_archive_depth(tmp_path):
    archive = make_zip(
        tmp_path / "mask.zip",
        {"readme.txt": b"read", "mods/item.pak": b"payload"},
    )
    out = tmp_path / "mask-out"
    extract_archive(archive, out, ExtractOptions(include=["*.pak"]))
    assert (out / "mods/item.pak").exists()
    assert not (out / "readme.txt").exists()

    inner_buffer = io.BytesIO()
    with zipfile.ZipFile(inner_buffer, "w") as nested:
        nested.writestr("inside.txt", b"nested")
    outer = tmp_path / "nested.zip"
    make_zip(outer, {"inner.zip": inner_buffer.getvalue()})
    nested_out = tmp_path / "nested-out"
    extract_archive(outer, nested_out, deep=True, max_depth=1)
    assert (nested_out / "inner/inside.txt").read_bytes() == b"nested"
    capped = tmp_path / "depth-capped"
    extract_archive(outer, capped, deep=True, max_depth=0)
    assert (capped / "inner.zip").exists()
    assert not (capped / "inner/inside.txt").exists()


def test_tar_gzip_compression_ratio_limit(tmp_path):
    path = tmp_path / "bomb.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        payload = b"0" * 250_000
        info = tarfile.TarInfo("huge.txt")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
    with pytest.raises(ArchiveLimitError):
        extract_archive(path, tmp_path / "limited", ExtractOptions(max_ratio=10))


def test_cancellation_cleans_staging_folder(tmp_path):
    from riftunpack.errors import CancelledError

    archive = make_zip(tmp_path / "cancel.zip", {"file.txt": b"data"})
    output = tmp_path / "cancel-out"
    with pytest.raises(CancelledError):
        extract_archive(archive, output, cancel=lambda: True)
    assert list(output.iterdir()) == []
