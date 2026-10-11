"""Helpers that create real archives for tests (no binary fixtures are committed)."""

from __future__ import annotations

import bz2
import gzip
import io
import lzma
import re
import shutil
import stat
import subprocess
import tarfile
import zipfile
from pathlib import Path

SEVEN_ZIP_BIN = shutil.which("7za") or shutil.which("7z") or shutil.which("7zz")
MAKECAB_BIN = shutil.which("makecab")


def write_zip(
    path: Path, files: dict[str, bytes], *, symlinks: dict[str, str] | None = None
) -> Path:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
        for name, target in (symlinks or {}).items():
            info = zipfile.ZipInfo(name)
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, target)
    return path


def write_zip_raw_name(path: Path, raw_name: bytes, data: bytes) -> Path:
    """Write a ZIP whose single name is arbitrary legacy bytes (no UTF-8 flag)."""
    placeholder = b"X" * len(raw_name)
    write_zip(path, {placeholder.decode(): data})
    blob = path.read_bytes().replace(placeholder, raw_name)
    path.write_bytes(blob)
    return path


def write_zip_7za(
    path: Path, files: dict[str, bytes], password: str, method: str = "ZipCrypto"
) -> Path:
    staging = path.parent / f"{path.stem}-src"
    staging.mkdir(exist_ok=True)
    names = []
    for name, data in files.items():
        target = staging / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        names.append(name)
    command = [
        SEVEN_ZIP_BIN,
        "a",
        "-tzip",
        f"-p{password}",
        f"-mem={method}",
        "-y",
        str(path),
        *names,
    ]
    subprocess.run(command, cwd=staging, check=True, capture_output=True)
    shutil.rmtree(staging)
    return path


def write_tar(
    path: Path, files: dict[str, bytes], *, symlinks: dict[str, str] | None = None, mode: str = "w"
) -> Path:
    with tarfile.open(path, mode) as archive:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
        for name, target in (symlinks or {}).items():
            info = tarfile.TarInfo(name)
            info.type = tarfile.SYMTYPE
            info.linkname = target
            archive.addfile(info)
    return path


def write_compressed_tar(path: Path, files: dict[str, bytes], compression: str) -> Path:
    mode = {"gz": "w:gz", "bz2": "w:bz2", "xz": "w:xz"}[compression]
    return write_tar(path, files, mode=mode)


def write_single(path: Path, data: bytes, fmt: str) -> Path:
    if fmt == "gz":
        path.write_bytes(gzip.compress(data))
    elif fmt == "bz2":
        path.write_bytes(bz2.compress(data))
    elif fmt == "xz":
        path.write_bytes(lzma.compress(data))
    elif fmt == "zst":
        import zstandard

        path.write_bytes(zstandard.ZstdCompressor().compress(data))
    else:  # pragma: no cover - test helper misuse
        raise ValueError(fmt)
    return path


def write_7z(path: Path, files: dict[str, bytes], password: str | None = None) -> Path:
    import py7zr

    kwargs = {"password": password, "header_encryption": bool(password)} if password else {}
    with py7zr.SevenZipFile(path, "w", **kwargs) as archive:
        for name, data in files.items():
            archive.writestr(data, name)
    return path


def write_7z_volumes(path: Path, files: dict[str, bytes], volume_size: str = "20k") -> list[Path]:
    """Create a split 7z set with 7-Zip (path.001, path.002, ...)."""
    staging = path.parent / f"{path.stem}-src"
    staging.mkdir(exist_ok=True)
    names = []
    for name, data in files.items():
        target = staging / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        names.append(name)
    subprocess.run(
        [SEVEN_ZIP_BIN, "a", f"-v{volume_size}", "-y", str(path), *names],
        cwd=staging,
        check=True,
        capture_output=True,
    )
    shutil.rmtree(staging)
    return sorted(path.parent.glob(f"{path.name}.*"))


def write_iso(path: Path, files: dict[str, bytes]) -> Path:
    """Write an ISO 9660 image with Rock Ridge names so long file names survive."""
    import pycdlib

    iso = pycdlib.PyCdlib()
    iso.new(rock_ridge="1.09")
    for index, (name, data) in enumerate(files.items()):
        base = Path(name).name
        stem, dot, ext = base.rpartition(".")
        if not dot:
            stem, ext = base, ""
        clean = re.sub(r"[^A-Z0-9_]", "_", stem.upper())[:6] or "F"
        short = f"{clean}{index:02d}.{re.sub(r'[^A-Z0-9_]', '_', ext.upper())[:3]};1"
        iso.add_fp(io.BytesIO(data), len(data), f"/{short}", rr_name=base)
    iso.write(str(path))
    iso.close()
    return path


def write_cab(path: Path, files: dict[str, bytes]) -> Path:
    """Create a CAB with makecab.exe (Windows only). Caller must skip when unavailable."""
    staging = path.parent / f"{path.stem}-src"
    staging.mkdir(exist_ok=True)
    ddf = staging / "files.ddf"
    lines = [
        ".OPTION EXPLICIT",
        f".Set CabinetNameTemplate={path.name}",
        ".Set DiskDirectoryTemplate=",
        ".Set Compress=OFF",
        f".Set CabinetPath={path.parent}",
    ]
    for name, data in files.items():
        (staging / name).write_bytes(data)
        lines.append(f'"{staging / name}" "{name}"')
    ddf.write_text("\n".join(lines) + "\n", encoding="utf-8")
    subprocess.run([MAKECAB_BIN, "/F", str(ddf)], cwd=staging, check=True, capture_output=True)
    shutil.rmtree(staging)
    return path


def zip_bomb(path: Path, size: int = 20 * 1024 * 1024) -> Path:
    return write_zip(path, {"zeros.bin": b"\0" * size})


def write_cab_stored(path: Path, files: dict[str, bytes]) -> Path:
    """Build an uncompressed (typeCompress=none) single-folder CAB without makecab.

    Layout follows the Microsoft CAB spec: CFHEADER, one CFFOLDER, CFFILE records, then
    CFDATA blocks of at most 32 KiB. Checksums are left as zero, which the spec allows.
    """
    import struct

    names = list(files)
    payload = b"".join(files[name] for name in names)
    blocks = [payload[i : i + 0x8000] for i in range(0, len(payload), 0x8000)] or [b""]
    # Fixed date 2020-01-02 12:00:00 so output is reproducible.
    date = ((2020 - 1980) << 9) | (1 << 5) | 2
    time_ = (12 << 11) | (0 << 5) | 0
    files_offset = 36 + 8
    file_records = bytearray()
    offset = 0
    for name in names:
        encoded = name.encode("ascii") + b"\x00"
        file_records += struct.pack("<IIHHHH", len(files[name]), offset, 0, date, time_, 0x20)
        file_records += encoded
        offset += len(files[name])
    data_offset = files_offset + len(file_records)
    data = bytearray()
    for block in blocks:
        data += struct.pack("<IHH", 0, len(block), len(block)) + block
    total = data_offset + len(data)
    header = struct.pack(
        "<4sIIIIIBBHHHHH",
        b"MSCF",
        0,
        total,
        0,
        files_offset,
        0,
        3,
        1,
        1,  # cFolders
        len(names),
        0,  # flags (no reserve, no prev/next cabinet)
        0,  # setID
        0,  # iCabinet
    )
    folder = struct.pack("<IHH", data_offset, len(blocks), 0)
    path.write_bytes(header + folder + bytes(file_records) + bytes(data))
    return path
