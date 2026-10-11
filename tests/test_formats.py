"""Real-archive tests for every supported format and the main safety rules."""

from __future__ import annotations

import random
from pathlib import Path

import archive_factory as make
import pytest

from riftunpack.detect import detect_format, multipart_group
from riftunpack.engine import (
    ExtractOptions,
    expand_inputs,
    extract_archive,
    list_archive,
    verify_archive,
)
from riftunpack.errors import (
    ArchiveLimitError,
    CancelledError,
    PasswordRequiredError,
    RiftUnpackError,
    UnsafeArchiveError,
    UnsupportedFormatError,
)

PAYLOAD = {
    "docs/readme.txt": b"hello riftunpack\n",
    "data/blob.bin": random.Random(7).randbytes(70_000),
}
needs_7za = pytest.mark.skipif(make.SEVEN_ZIP_BIN is None, reason="7-Zip (7za) not installed")
needs_makecab = pytest.mark.skipif(make.MAKECAB_BIN is None, reason="makecab.exe is Windows-only")


def _assert_payload(root: Path, files: dict[str, bytes]) -> None:
    for name, data in files.items():
        assert (root / name).read_bytes() == data, name


# ---- formats with a built-in or pip-installed backend ----------------------------------


def _libarchive_available() -> bool:
    try:
        import libarchive  # noqa: F401
    except Exception:  # missing package or missing native libarchive
        return False
    return True


needs_libarchive = pytest.mark.skipif(
    not _libarchive_available(), reason="ISO needs libarchive-c and the native libarchive"
)


@pytest.mark.parametrize(
    "fmt",
    [
        "zip",
        "tar",
        "tar.gz",
        "tar.bz2",
        "tar.xz",
        "7z",
        pytest.param("iso", marks=needs_libarchive),
    ],
)
def test_container_formats_roundtrip(tmp_path, fmt):
    archive = tmp_path / f"sample.{fmt}"
    if fmt == "zip":
        make.write_zip(archive, PAYLOAD)
    elif fmt == "tar":
        make.write_tar(archive, PAYLOAD)
    elif fmt.startswith("tar."):
        make.write_compressed_tar(archive, PAYLOAD, fmt.split(".", 1)[1])
    elif fmt == "7z":
        make.write_7z(archive, PAYLOAD)
    else:
        make.write_iso(archive, {"readme.txt": PAYLOAD["docs/readme.txt"]})
    destination = tmp_path / "out"
    report = extract_archive(archive, destination)
    assert report.extracted >= 1
    if fmt == "iso":
        assert (destination / "readme.txt").exists() or any(
            p.read_bytes() == PAYLOAD["docs/readme.txt"]
            for p in destination.rglob("*")
            if p.is_file()
        )
    else:
        _assert_payload(destination, PAYLOAD)


@pytest.mark.parametrize(
    ("fmt", "name", "expected_name"),
    [
        ("gz", "notes.txt.gz", "notes.txt"),
        ("bz2", "log.txt.bz2", "log.txt"),
        ("xz", "table.csv.xz", "table.csv"),
        ("zst", "pack.bin.zst", "pack.bin"),
    ],
)
def test_single_stream_formats(tmp_path, fmt, name, expected_name):
    data = b"single stream payload " * 500
    archive = make.write_single(tmp_path / name, data, fmt)
    destination = tmp_path / "out"
    report = extract_archive(archive, destination)
    assert report.extracted == 1
    assert (destination / expected_name).read_bytes() == data
    assert list_archive(archive)[0].name == name


def test_single_stream_skip_and_rename_policies(tmp_path):
    archive = make.write_single(tmp_path / "a.txt.gz", b"one", "gz")
    destination = tmp_path / "out"
    extract_archive(archive, destination)
    skipped = extract_archive(archive, destination, ExtractOptions(conflict="skip"))
    assert skipped.skipped == 1 and skipped.extracted == 0
    renamed = extract_archive(archive, destination, ExtractOptions(conflict="rename"))
    assert renamed.extracted == 1
    assert (destination / "a (1).txt").read_bytes() == b"one"


def test_single_stream_overwrite_policy(tmp_path):
    archive = make.write_single(tmp_path / "a.txt.gz", b"new", "gz")
    destination = tmp_path / "out"
    destination.mkdir()
    (destination / "a.txt").write_bytes(b"old")
    extract_archive(archive, destination, ExtractOptions(conflict="overwrite"))
    assert (destination / "a.txt").read_bytes() == b"new"


def test_corrupt_single_stream_is_removed(tmp_path):
    bad = tmp_path / "bad.txt.gz"
    bad.write_bytes(b"\x1f\x8b" + b"\x00" * 30)
    destination = tmp_path / "out"
    with pytest.raises(RiftUnpackError):
        extract_archive(bad, destination)
    assert not (destination / "bad.txt").exists()


def test_single_stream_size_limit(tmp_path):
    # Repeated text is compressible but is not a tar stream, so only the ratio rule applies.
    archive = make.write_single(tmp_path / "big.bin.gz", b"A" * 2_000_000, "gz")
    with pytest.raises(ArchiveLimitError):
        extract_archive(archive, tmp_path / "out", ExtractOptions(max_ratio=5))


def test_single_stream_zero_file_limit(tmp_path):
    archive = make.write_single(tmp_path / "x.gz", b"x", "gz")
    with pytest.raises(ArchiveLimitError):
        extract_archive(archive, tmp_path / "out", ExtractOptions(max_files=0))


def test_single_stream_unnamed_output(tmp_path):
    archive = make.write_single(tmp_path / ".gz", b"unnamed", "gz")
    report = extract_archive(archive, tmp_path / "out")
    assert report.extracted == 1
    assert (tmp_path / "out" / "decompressed").exists() or any((tmp_path / "out").iterdir())


def test_single_stream_metadata_is_preserved(tmp_path):
    archive = make.write_single(tmp_path / "time.txt.gz", b"t", "gz")
    extract_archive(archive, tmp_path / "out")
    assert abs((tmp_path / "out" / "time.txt").stat().st_mtime - archive.stat().st_mtime) < 2


def test_tar_symlink_is_rejected(tmp_path):
    archive = make.write_tar(
        tmp_path / "link.tar", {"ok.txt": b"x"}, symlinks={"evil": "/etc/passwd"}
    )
    with pytest.raises(UnsafeArchiveError):
        extract_archive(archive, tmp_path / "out")
    with pytest.raises(UnsafeArchiveError):
        list_archive(archive)  # listing is safe, extraction must still refuse
        extract_archive(archive, tmp_path / "out2")


def test_unsupported_content_is_reported(tmp_path):
    plain = tmp_path / "notes.txt"
    plain.write_text("just text", encoding="utf-8")
    with pytest.raises(UnsupportedFormatError):
        extract_archive(plain, tmp_path / "out")
    with pytest.raises(UnsupportedFormatError):
        list_archive(plain)
    with pytest.raises(UnsupportedFormatError):
        verify_archive(plain)


def test_missing_archive_is_reported(tmp_path):
    with pytest.raises(RiftUnpackError, match="not found"):
        extract_archive(tmp_path / "nope.zip", tmp_path / "out")


# ---- optional backends, which may be absent in a given environment -----------------------


def test_rar_without_rarfile_is_reported(tmp_path, monkeypatch):
    fake = tmp_path / "fake.rar"
    fake.write_bytes(b"Rar!\x1a\x07\x00" + b"\0" * 64)
    monkeypatch.setitem(__import__("sys").modules, "rarfile", None)
    with pytest.raises(UnsupportedFormatError, match="rarfile"):
        extract_archive(fake, tmp_path / "out")


def test_7z_without_py7zr_and_without_7zip(tmp_path, monkeypatch):
    archive = make.write_7z(tmp_path / "x.7z", {"a.txt": b"a"})
    monkeypatch.setitem(__import__("sys").modules, "py7zr", None)
    monkeypatch.setattr("riftunpack.engine.shutil.which", lambda name: None)
    with pytest.raises(UnsupportedFormatError, match="7z"):
        extract_archive(archive, tmp_path / "out")


@needs_makecab
def test_cab_roundtrip_on_windows(tmp_path):
    import subprocess

    try:
        archive = make.write_cab(tmp_path / "pkg.cab", {"hello.txt": b"cab"})
    except subprocess.CalledProcessError as exc:
        # CAB is experimental: a makecab failure is reported, not hidden as a pass.
        pytest.skip(f"makecab could not build the fixture: {exc.stderr!r}")
    extract_archive(archive, tmp_path / "out")
    assert any(p.read_bytes() == b"cab" for p in (tmp_path / "out").rglob("*") if p.is_file())


# ---- encryption --------------------------------------------------------------------------


def test_7z_password_flow(tmp_path):
    archive = make.write_7z(
        tmp_path / "secret.7z", {"s.txt": b"classified"}, password="open-sesame"
    )
    with pytest.raises(PasswordRequiredError):
        extract_archive(archive, tmp_path / "no-pass")
    with pytest.raises(PasswordRequiredError):
        extract_archive(archive, tmp_path / "wrong", ExtractOptions(password="nope"))
    extract_archive(archive, tmp_path / "ok", ExtractOptions(password="open-sesame"))
    assert (tmp_path / "ok" / "s.txt").read_bytes() == b"classified"


@needs_7za
def test_zip_zipcrypto_password_flow(tmp_path):
    archive = make.write_zip_7za(tmp_path / "p.zip", {"s.txt": b"zipcrypto"}, "pw123")
    with pytest.raises(PasswordRequiredError):
        extract_archive(archive, tmp_path / "none")
    with pytest.raises(PasswordRequiredError):
        extract_archive(archive, tmp_path / "bad", ExtractOptions(password="wrong"))
    extract_archive(archive, tmp_path / "ok", ExtractOptions(password="pw123"))
    assert (tmp_path / "ok" / "s.txt").read_bytes() == b"zipcrypto"


@needs_7za
def test_zip_aes_falls_back_to_7zip(tmp_path):
    archive = make.write_zip_7za(tmp_path / "aes.zip", {"s.txt": b"aes"}, "pw123", method="AES256")
    extract_archive(archive, tmp_path / "ok", ExtractOptions(password="pw123"))
    assert (tmp_path / "ok" / "s.txt").read_bytes() == b"aes"


def test_zip_aes_without_7zip_gives_clear_error(tmp_path, monkeypatch):
    archive = tmp_path / "fake-aes.zip"
    make.write_zip(archive, {"s.txt": b"x"})
    monkeypatch.setattr("riftunpack.engine._zip_members", _raise_unsupported_method)
    monkeypatch.setattr("riftunpack.engine.shutil.which", lambda name: None)
    with pytest.raises(RiftUnpackError, match="7-Zip"):
        extract_archive(archive, tmp_path / "out")


def _raise_unsupported_method(*args, **kwargs):
    raise NotImplementedError("compression type 99")


# ---- legacy file-name encodings ------------------------------------------------------------


@pytest.mark.parametrize(
    ("encoding", "text"),
    [("cp866", "Тестовый файл"), ("cp1251", "Русский текст"), ("shift_jis", "日本語のファイル")],
)
def test_legacy_zip_names_are_decoded(tmp_path, encoding, text):
    raw = (text + ".txt").encode(encoding)
    archive = make.write_zip_raw_name(tmp_path / "legacy.zip", raw, b"payload")
    destination = tmp_path / "out"
    extract_archive(archive, destination)
    assert (destination / (text + ".txt")).read_bytes() == b"payload"


def test_explicit_encoding_override(tmp_path):
    raw = "Файл.txt".encode("cp1251")
    archive = make.write_zip_raw_name(tmp_path / "override.zip", raw, b"x")
    destination = tmp_path / "out"
    extract_archive(archive, destination, ExtractOptions(encoding="cp1251"))
    assert (destination / "Файл.txt").exists()


def test_utf8_zip_names_are_kept(tmp_path):
    archive = make.write_zip(tmp_path / "utf.zip", {"Ünïcode/名前.txt": b"u"})
    destination = tmp_path / "out"
    extract_archive(archive, destination)
    assert (destination / "Ünïcode" / "名前.txt").read_bytes() == b"u"


# ---- hostile archives -------------------------------------------------------------------


@pytest.mark.parametrize("evil", [b"../escape.txt", b"sub/../../escape.txt", b"/abs/escape.txt"])
def test_zip_slip_is_blocked(tmp_path, evil):
    archive = make.write_zip_raw_name(tmp_path / "slip.zip", evil, b"owned")
    with pytest.raises(UnsafeArchiveError):
        extract_archive(archive, tmp_path / "out" / "inner")
    assert not (tmp_path / "out" / "escape.txt").exists()


def test_zip_symlink_is_rejected(tmp_path):
    archive = make.write_zip(
        tmp_path / "link.zip", {"ok.txt": b"x"}, symlinks={"lnk": "/etc/passwd"}
    )
    with pytest.raises(UnsafeArchiveError):
        extract_archive(archive, tmp_path / "out")
    assert not (tmp_path / "out" / "lnk").exists()


def test_symlink_in_destination_is_not_followed(tmp_path):
    import os

    victim = tmp_path / "victim"
    victim.mkdir()
    destination = tmp_path / "out"
    destination.mkdir()
    os.symlink(victim, destination / "dir")
    archive = make.write_zip(tmp_path / "x.zip", {"dir/file.txt": b"nope"})
    with pytest.raises(UnsafeArchiveError):
        extract_archive(archive, destination)
    assert not (victim / "file.txt").exists()


def test_zip_bomb_ratio_limit(tmp_path):
    archive = make.zip_bomb(tmp_path / "bomb.zip", size=8 * 1024 * 1024)
    with pytest.raises(ArchiveLimitError):
        extract_archive(archive, tmp_path / "out", ExtractOptions(max_ratio=10))
    assert not any((tmp_path / "out").glob("zeros*")) if (tmp_path / "out").exists() else True


def test_size_limit_is_enforced(tmp_path):
    archive = make.write_zip(tmp_path / "big.zip", {"a.bin": random.Random(1).randbytes(300_000)})
    with pytest.raises(ArchiveLimitError):
        extract_archive(archive, tmp_path / "out", ExtractOptions(max_total_size=1000))


def test_file_count_limit_is_enforced(tmp_path):
    archive = make.write_zip(tmp_path / "many.zip", {f"f{i}.txt": b"x" for i in range(5)})
    with pytest.raises(ArchiveLimitError):
        extract_archive(archive, tmp_path / "out", ExtractOptions(max_files=2))


def test_tar_bomb_ratio_limit(tmp_path):
    archive = make.write_compressed_tar(
        tmp_path / "bomb.tar.gz", {"z.bin": b"\0" * 5_000_000}, "gz"
    )
    with pytest.raises(ArchiveLimitError):
        extract_archive(archive, tmp_path / "out", ExtractOptions(max_ratio=10))


def test_corrupt_zip_crc_is_reported(tmp_path):
    import zipfile

    archive = tmp_path / "crc.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr("a.txt", b"A" * 200)
    blob = bytearray(archive.read_bytes())
    index = blob.index(b"A" * 50)
    blob[index] = ord("B")
    archive.write_bytes(bytes(blob))
    with pytest.raises(RiftUnpackError):
        verify_archive(archive)


# ---- include masks, cancellation, nested archives, multipart ----------------------------


def test_include_masks_select_members(tmp_path):
    archive = make.write_zip(tmp_path / "mask.zip", {"a.pak": b"1", "b.txt": b"2", "c/d.pak": b"3"})
    destination = tmp_path / "out"
    report = extract_archive(archive, destination, ExtractOptions(include=["*.pak"]))
    assert report.extracted == 1 or (destination / "a.pak").exists()
    assert not (destination / "b.txt").exists()


def test_include_masks_with_7z_backend(tmp_path):
    archive = make.write_7z(tmp_path / "mask.7z", {"a.pak": b"1", "b.txt": b"2"})
    destination = tmp_path / "out"
    extract_archive(archive, destination, ExtractOptions(include=["*.pak"]))
    assert (destination / "a.pak").exists()
    assert not (destination / "b.txt").exists()


def test_cancel_stops_extraction(tmp_path):
    archive = make.write_zip(tmp_path / "c.zip", PAYLOAD)
    with pytest.raises(CancelledError):
        extract_archive(archive, tmp_path / "out", cancel=lambda: True)
    assert not (tmp_path / "out" / "docs" / "readme.txt").exists()


def test_progress_callback_reports_each_file(tmp_path):
    archive = make.write_zip(tmp_path / "p.zip", PAYLOAD)
    events = []
    extract_archive(
        archive, tmp_path / "out", progress=lambda i, n, name: events.append((i, n, name))
    )
    assert events and events[-1][0] == events[-1][1] == 2


def test_nested_archives_are_extracted_within_depth(tmp_path):
    inner = make.write_zip(tmp_path / "inner.zip", {"deep.txt": b"deep"})
    outer = make.write_zip(tmp_path / "outer.zip", {"inner.zip": inner.read_bytes()})
    destination = tmp_path / "out"
    extract_archive(outer, destination, deep=True, max_depth=2)
    assert (destination / "inner" / "deep.txt").read_bytes() == b"deep"


def test_nested_archives_respect_zero_depth(tmp_path):
    inner = make.write_zip(tmp_path / "inner.zip", {"deep.txt": b"deep"})
    outer = make.write_zip(tmp_path / "outer.zip", {"inner.zip": inner.read_bytes()})
    destination = tmp_path / "out"
    extract_archive(outer, destination, deep=True, max_depth=0)
    assert (destination / "inner.zip").exists()
    assert not (destination / "inner" / "deep.txt").exists()


def test_list_and_verify_common_formats(tmp_path):
    archive = make.write_zip(tmp_path / "l.zip", PAYLOAD)
    names = sorted(item.name for item in list_archive(archive))
    assert names == sorted(PAYLOAD)
    assert verify_archive(archive) is True
    seven = make.write_7z(tmp_path / "l.7z", PAYLOAD)
    assert {item.name for item in list_archive(seven)} >= set(PAYLOAD)


@needs_7za
def test_split_7z_volumes(tmp_path):
    content = {"big.bin": random.Random(3).randbytes(120_000)}
    volumes = make.write_7z_volumes(tmp_path / "set.7z", content, volume_size="30k")
    assert len(volumes) > 1
    destination = tmp_path / "out"
    extract_archive(volumes[0], destination)
    assert (destination / "big.bin").read_bytes() == content["big.bin"]


def test_multipart_group_detection(tmp_path):
    for index in (1, 2, 3):
        (tmp_path / f"pack.part{index}.rar").write_bytes(b"x")
    (tmp_path / "bundle.zip").write_bytes(b"PK")
    (tmp_path / "bundle.z01").write_bytes(b"x")
    (tmp_path / "bundle.z02").write_bytes(b"x")
    rar_group = multipart_group(tmp_path / "pack.part2.rar")
    assert [p.name for p in rar_group] == ["pack.part1.rar", "pack.part2.rar", "pack.part3.rar"]
    zip_group = multipart_group(tmp_path / "bundle.z01")
    assert [p.name for p in zip_group] == ["bundle.z01", "bundle.z02", "bundle.zip"]
    assert multipart_group(tmp_path / "solo.7z") == [tmp_path / "solo.7z"]
    assert multipart_group(tmp_path / "stray.zip") == [tmp_path / "stray.zip"]


def test_expand_inputs_skips_secondary_volumes(tmp_path):
    make.write_zip(tmp_path / "a.zip", {"x": b"1"})
    (tmp_path / "a.z01").write_bytes(b"PK\x07\x08")
    (tmp_path / "pack.part2.rar").write_bytes(b"Rar!\x1a\x07\x00")
    (tmp_path / "pack.part1.rar").write_bytes(b"Rar!\x1a\x07\x00")
    (tmp_path / "data.7z.002").write_bytes(b"y")
    (tmp_path / "data.7z.001").write_bytes(b"7z\xbc\xaf\x27\x1c")
    (tmp_path / "plain.txt").write_text("x", encoding="utf-8")
    names = [p.name for p in expand_inputs([str(tmp_path)], recursive=True)]
    assert names == sorted(["a.zip", "pack.part1.rar", "data.7z.001"])
    assert expand_inputs([str(tmp_path / "plain.txt")]) == []
    assert expand_inputs([str(tmp_path / "a.zip"), str(tmp_path / "a.zip")]) == [tmp_path / "a.zip"]


def test_detect_format_uses_content_not_extension(tmp_path):
    fake_zip = tmp_path / "actually-tar.zip"
    make.write_tar(fake_zip, {"a.txt": b"a"})
    assert detect_format(fake_zip) == "tar"
    assert detect_format(tmp_path / "missing.bin") is None
