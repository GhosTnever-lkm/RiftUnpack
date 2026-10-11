"""Archive inspection, safe extraction, integrity checking, and nested archives."""

from __future__ import annotations

import bz2
import gzip
import lzma
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from collections.abc import Callable, Iterable
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import IO, Any

from .detect import archive_base_name, detect_format, multipart_group
from .errors import (
    ArchiveLimitError,
    CancelledError,
    PasswordRequiredError,
    RiftUnpackError,
    UnsafeArchiveError,
    UnsupportedFormatError,
    UnsupportedMethodError,
)
from .safety import matches_patterns, safe_join, safe_member_name

Progress = Callable[[int, int, str], None]
Cancel = Callable[[], bool]


@dataclass(slots=True)
class ExtractOptions:
    conflict: str = "rename"  # overwrite | skip | rename
    max_total_size: int = 20 * 1024**3
    max_files: int = 100_000
    max_ratio: float = 1000.0
    password: str | None = None
    include: list[str] | None = None
    encoding: str | None = None
    preserve_metadata: bool = True
    seven_zip: str | None = None


@dataclass(slots=True)
class Member:
    name: str
    size: int
    compressed: int
    is_dir: bool
    is_link: bool
    opener: Callable[[], IO[bytes] | None] | None = None
    mtime: float | None = None
    mode: int | None = None


@dataclass(slots=True)
class ExtractionReport:
    archive: Path
    format: str
    destination: Path
    extracted: int = 0
    skipped: int = 0
    bytes_written: int = 0
    warnings: list[str] = field(default_factory=list)


def _password_bytes(password: str | None) -> bytes | None:
    return password.encode("utf-8") if password else None


def _decode_legacy_zip_name(item: zipfile.ZipInfo, encoding: str | None) -> None:
    """Decode non-UTF8 legacy ZIP names while retaining zipfile's raw local name."""
    if item.flag_bits & 0x800:
        return
    try:
        raw = item.orig_filename.encode("cp437")
        if encoding:
            item.filename = raw.decode(encoding)
            return
        candidates = []
        for candidate in ("shift_jis", "cp866", "cp1251"):
            try:
                text = raw.decode(candidate)
            except UnicodeError:
                continue
            japanese = sum(
                "\u3040" <= char <= "\u30ff" or "\u4e00" <= char <= "\u9fff" for char in text
            )
            cyrillic = sum("\u0400" <= char <= "\u04ff" for char in text)
            candidates.append((japanese, cyrillic, candidate, text))
        if not candidates:
            return
        japanese, cyrillic, selected, decoded = max(candidates)
        if japanese:
            item.filename = decoded
        elif any(0x80 <= byte <= 0x9F for byte in raw):
            item.filename = raw.decode("cp866")
        elif cyrillic:
            item.filename = raw.decode("cp1251")
    except (UnicodeError, LookupError):
        return


def _zip_members(
    path: Path, password: str | None, encoding: str | None
) -> tuple[list[Member], object]:
    # ZipFile(metadata_encoding=...) exists only on Python 3.11+. On 3.10 the name is
    # re-decoded by _decode_legacy_zip_name below instead.
    kwargs: dict[str, Any] = {}
    if encoding and sys.version_info >= (3, 11):
        kwargs["metadata_encoding"] = encoding
    try:
        archive = zipfile.ZipFile(path, **kwargs)
    except (OSError, zipfile.BadZipFile) as exc:
        raise RiftUnpackError(f"Could not open ZIP archive: {exc}") from exc
    result = []
    for item in archive.infolist():
        mode = item.external_attr >> 16
        _decode_legacy_zip_name(item, encoding)
        try:
            member_mtime = datetime(*item.date_time).timestamp() if item.date_time else None
        except (ValueError, OverflowError):
            member_mtime = None
        result.append(
            Member(
                item.filename,
                item.file_size,
                item.compress_size,
                item.is_dir(),
                stat.S_ISLNK(mode),
                partial(archive.open, item, pwd=_password_bytes(password)),
                member_mtime,
                mode & 0o777,
            )
        )
    return result, archive


def _tar_members(path: Path) -> tuple[list[Member], object]:
    try:
        archive = tarfile.open(path, "r:*")
    except (OSError, tarfile.TarError) as exc:
        raise RiftUnpackError(f"Could not open TAR archive: {exc}") from exc
    result = []
    for item in archive.getmembers():
        unsafe_type = not (item.isfile() or item.isdir())
        result.append(
            Member(
                item.name,
                item.size,
                item.size,
                item.isdir(),
                item.issym() or item.islnk() or unsafe_type,
                partial(archive.extractfile, item) if item.isfile() else None,
                item.mtime,
                item.mode,
            )
        )
    return result, archive


def _rar_members(path: Path, password: str | None, encoding: str | None):
    try:
        import rarfile
    except ImportError as exc:
        raise UnsupportedFormatError(
            "RAR needs the optional 'rarfile' package and an installed UnRAR/unar/7-Zip "
            "tool. Install with: pip install 'riftunpack[all-formats]'."
        ) from exc
    try:
        archive = rarfile.RarFile(path, charset=encoding)
        result = []
        for item in archive.infolist():
            is_link = bool(getattr(item, "is_symlink", lambda: False)())
            result.append(
                Member(
                    item.filename,
                    item.file_size,
                    item.compress_size,
                    item.isdir(),
                    is_link,
                    partial(archive.open, item, pwd=password),
                )
            )
        return result, archive
    except rarfile.NeedFirstVolume as exc:
        raise RiftUnpackError(
            "This split RAR set is missing its first volume (.part1.rar)."
        ) from exc
    except rarfile.Error as exc:
        raise PasswordRequiredError(
            "RAR could not be read. Check the password and install UnRAR or 7-Zip."
        ) from exc


def _py7zr_extract(
    path: Path, destination: Path, password: str | None, options: ExtractOptions
) -> list[Member]:
    try:
        import py7zr
    except ImportError as exc:
        raise UnsupportedFormatError("7z needs py7zr. Install it with: pip install py7zr") from exc
    try:
        with py7zr.SevenZipFile(path, mode="r", password=password) as archive:
            listed = archive.list()
            members = []
            for item in listed:
                name = item.filename
                size = int(getattr(item, "uncompressed", 0) or 0)
                compressed = int(getattr(item, "compressed", 0) or 0)
                special = (
                    bool(getattr(item, "is_symlink", False))
                    or bool(getattr(item, "is_junction", False))
                    or bool(getattr(item, "is_socket", False))
                )
                members.append(
                    Member(
                        name,
                        size,
                        compressed,
                        bool(getattr(item, "is_directory", False)),
                        special,
                    )
                )
            _check_limits(members, options)
            # Validate every name before the backend writes even into its isolated staging folder.
            for member in members:
                safe_member_name(member.name)
                if member.is_link:
                    raise UnsafeArchiveError(f"Symbolic links are not extracted: {member.name}")
            wanted = [m.name for m in members if matches_patterns(m.name, options.include)]
            archive.extract(path=destination, targets=wanted)
        return members
    except (UnsafeArchiveError, ArchiveLimitError):
        raise
    except Exception as exc:
        text = str(exc).lower()
        if password or "password" in text or "crc" in text or "decrypt" in text:
            raise PasswordRequiredError(
                "7z needs a valid password, or its data failed a password/CRC check."
            ) from exc
        raise RiftUnpackError(f"7z extraction failed: {exc}") from exc


def _check_limits(members: list[Member], options: ExtractOptions) -> None:
    files = [m for m in members if not m.is_dir]
    if len(files) > options.max_files:
        raise ArchiveLimitError(
            f"Archive has {len(files):,} files; configured limit is {options.max_files:,}."
        )
    total = sum(max(0, m.size) for m in files)
    if total > options.max_total_size:
        raise ArchiveLimitError(
            f"Unpacked size {total:,} bytes exceeds limit {options.max_total_size:,} bytes."
        )
    compressed = sum(max(0, m.compressed) for m in files)
    if compressed and total / compressed > options.max_ratio:
        raise ArchiveLimitError(
            f"Compression ratio {total / compressed:.1f}:1 exceeds limit {options.max_ratio:.1f}:1."
        )


def _backend(path: Path, fmt: str, options: ExtractOptions):
    if fmt == "zip":
        return _zip_members(path, options.password, options.encoding)
    if fmt.startswith("tar") or fmt == "tar":
        return _tar_members(path)
    if fmt == "rar":
        return _rar_members(path, options.password, options.encoding)
    raise UnsupportedFormatError(_missing_backend_message(fmt))


def _missing_backend_message(fmt: str) -> str:
    names = {
        "7z": "py7zr (pip install py7zr) or 7-Zip",
        "rar": "rarfile plus UnRAR/unar/7-Zip",
        "iso": "7-Zip or libarchive",
        "cab": "7-Zip or libarchive",
        "zst": "zstandard or 7-Zip",
        "gz": "the built-in gzip backend",
        "bz2": "the built-in bz2 backend",
        "xz": "the built-in lzma backend",
    }
    if fmt == "7z":
        return (
            "No 7z backend is available. Install py7zr with 'pip install py7zr' or install 7-Zip."
        )
    if fmt == "rar":
        return (
            "RAR needs rarfile and an UnRAR/unar/7-Zip executable. "
            "Install 'pip install rarfile' and a compatible tool."
        )
    return (
        f"Format {fmt.upper()} requires {names.get(fmt, 'an optional archive backend')}. "
        "Install 7-Zip or 'pip install riftunpack[all-formats]'."
    )


def _looks_like_password_error(lines: list[str]) -> bool:
    """7-Zip reports a missing password as 'Wrong password' or 'Break signaled'."""
    text = " ".join(lines).lower()
    return "password" in text or "break signaled" in text


def _seven_zip_binary(executable: str | None = None) -> str | None:
    return executable or shutil.which("7z") or shutil.which("7zz") or shutil.which("7za")


def _seven_zip_available(options: ExtractOptions) -> bool:
    return _seven_zip_binary(options.seven_zip) is not None


def _is_split_archive(path: Path) -> bool:
    """True for 7-Zip style volume names such as archive.7z.001 or archive.zip.001."""
    return re.search(r"\.\d{3}$", path.name) is not None


def _external_7z(
    path: Path,
    destination: Path,
    executable: str | None,
    password: str | None,
    options: ExtractOptions,
    cancel: Cancel | None,
) -> None:
    binary = _seven_zip_binary(executable)
    if not binary:
        raise UnsupportedFormatError(_missing_backend_message(detect_format(path) or "archive"))
    password_args = [f"-p{password}"] if password else []
    # stdin is closed so 7-Zip can never block on an interactive password prompt.
    try:
        listing = subprocess.run(
            [binary, "l", "-slt", *password_args, str(path)],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RiftUnpackError(f"7-Zip could not inspect the archive: {exc}") from exc
    if listing.returncode:
        detail = (listing.stderr or listing.stdout).strip().splitlines()
        if _looks_like_password_error(detail):
            raise PasswordRequiredError("Archive password is missing or incorrect.")
        raise RiftUnpackError(
            f"7-Zip could not inspect archive: {detail[-1] if detail else 'unknown error'}"
        )
    entries: list[dict[str, str]] = []
    for block in listing.stdout.split("\n\n"):
        values = {}
        for line in block.splitlines():
            if " = " in line:
                key, value = line.split(" = ", 1)
                values[key] = value
        if values.get("Path") and "Folder" in values:
            entries.append(values)
    checked: list[Member] = []
    for item in entries:
        name = item["Path"]
        safe_member_name(name)
        attributes = item.get("Attributes", "").split()
        if (
            "Symbolic Link" in item
            or "Hard Link" in item
            or any(token.startswith("l") for token in attributes)
        ):
            raise UnsafeArchiveError(f"Archive links are not extracted: {name}")
        size = int(item.get("Size", "0") or 0)
        packed = int(item.get("Packed Size", "0") or 0)
        checked.append(Member(name, size, packed, item.get("Folder") == "+", False))
    selected = [m for m in checked if matches_patterns(m.name, options.include)]
    _check_limits(selected, options)
    command = [binary, "x", "-y", f"-o{destination}", *password_args, str(path)]
    # Output goes to temporary files: reading a pipe only after exit can deadlock on large listings.
    with (
        tempfile.TemporaryFile("w+", encoding="utf-8", errors="replace") as stdout_file,
        tempfile.TemporaryFile("w+", encoding="utf-8", errors="replace") as stderr_file,
    ):
        try:
            process = subprocess.Popen(
                command, stdout=stdout_file, stderr=stderr_file, stdin=subprocess.DEVNULL
            )
        except OSError as exc:
            raise RiftUnpackError(f"7-Zip could not run: {exc}") from exc
        while True:
            try:
                returncode = process.wait(timeout=0.1)
                break
            except subprocess.TimeoutExpired:
                if cancel and cancel():
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                    raise CancelledError("Extraction cancelled by user.") from None
        stdout_file.seek(0)
        stderr_file.seek(0)
        stdout_text = stdout_file.read()
        stderr_text = stderr_file.read()
    if returncode:
        msg = (stderr_text or stdout_text).strip().splitlines()
        if _looks_like_password_error(msg):
            raise PasswordRequiredError("Archive password is missing or incorrect.")
        raise RiftUnpackError(f"7-Zip extraction failed: {msg[-1] if msg else 'unknown error'}")
    if options.include:
        for leftover in destination.rglob("*"):
            if leftover.is_file() and not matches_patterns(
                leftover.relative_to(destination).as_posix(), options.include
            ):
                leftover.unlink()


def _copy_stage(
    stage: Path,
    destination: Path,
    options: ExtractOptions,
    report: ExtractionReport,
    progress: Progress | None,
    cancel: Cancel | None,
) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    destination = destination.resolve()
    files = [p for p in stage.rglob("*") if p.is_file()]
    dirs = [p for p in stage.rglob("*") if p.is_dir()]
    if any(p.is_symlink() for p in stage.rglob("*")):
        raise UnsafeArchiveError("Archive backend produced a symbolic link; refusing to copy it.")
    if len(files) > options.max_files:
        raise ArchiveLimitError("Extracted file count exceeded the configured limit.")
    actual = sum(p.stat().st_size for p in files)
    if actual > options.max_total_size:
        raise ArchiveLimitError("Actual extracted size exceeded the configured limit.")
    for directory in sorted(dirs, key=lambda p: len(p.parts)):
        relative = directory.relative_to(stage)
        target = safe_join(destination, relative.as_posix())
        if target.exists() and not target.is_dir():
            raise RiftUnpackError(f"Output folder conflicts with an existing file: {target}")
        if not target.exists():
            target.mkdir(parents=True, exist_ok=True)
    for index, source in enumerate(files, 1):
        if cancel and cancel():
            raise CancelledError("Extraction cancelled by user.")
        relative = source.relative_to(stage)
        target = safe_join(destination, relative.as_posix())
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if options.conflict == "skip":
                report.skipped += 1
                continue
            if options.conflict == "rename":
                n = 1
                while True:
                    candidate = target.with_name(f"{target.stem} ({n}){target.suffix}")
                    if not candidate.exists():
                        target = candidate
                        break
                    n += 1
            elif options.conflict != "overwrite":
                raise ValueError("conflict must be overwrite, skip, or rename")
        ensure_target = safe_join(destination, target.relative_to(destination).as_posix())
        ensure_target.parent.mkdir(parents=True, exist_ok=True)
        with source.open("rb") as source_stream, ensure_target.open("wb") as output_stream:
            while chunk := source_stream.read(1024 * 1024):
                if cancel and cancel():
                    raise CancelledError("Extraction cancelled by user.")
                output_stream.write(chunk)
        if options.preserve_metadata:
            shutil.copystat(source, ensure_target, follow_symlinks=False)
        report.extracted += 1
        report.bytes_written += source.stat().st_size
        if progress:
            progress(index, len(files), relative.as_posix())


def extract_archive(
    path: str | Path,
    destination: str | Path,
    options: ExtractOptions | None = None,
    progress: Progress | None = None,
    cancel: Cancel | None = None,
    deep: bool = False,
    max_depth: int = 2,
    _depth: int = 0,
) -> ExtractionReport:
    """Extract one archive safely; optional nested extraction is bounded by max_depth."""
    path, destination = Path(path), Path(destination)
    options = options or ExtractOptions()
    if not path.is_file():
        raise RiftUnpackError(f"Archive file not found: {path}")
    existing_files = (
        {item.resolve() for item in destination.rglob("*") if item.is_file()}
        if deep and destination.exists()
        else set()
    )
    fmt = detect_format(path)
    if not fmt:
        raise UnsupportedFormatError(f"Could not recognize archive contents: {path.name}")
    if fmt in {"gz", "bz2", "xz", "zst"}:
        return _extract_single_compressed(path, destination, fmt, options, progress, cancel)
    if fmt in {"iso", "cab"}:
        return _extract_external(path, destination, fmt, options, progress, cancel)
    if fmt == "7z":
        return _extract_7z(path, destination, options, progress, cancel)
    if fmt == "zip" and _is_split_archive(path):
        return _extract_via_7zip(path, destination, fmt, options, progress, cancel)
    volumes = multipart_group(path)
    primary = volumes[0] if volumes and path.suffix.lower() in {".z01", ".001"} else path
    if len(volumes) > 1 and primary != path and primary.suffix.lower() == ".z01":
        primary = volumes[-1]
    if fmt == "zip" and len(volumes) > 1:
        if _seven_zip_available(options):
            return _extract_via_7zip(primary, destination, fmt, options, progress, cancel)
        raise UnsupportedFormatError(
            "Split ZIP archives need 7-Zip installed to read .z01/.zip volume sets."
        )
    report = ExtractionReport(path, fmt, destination)
    try:
        _extract_python_backend(primary, destination, fmt, options, report, progress, cancel)
    except UnsupportedMethodError as exc:
        if fmt != "zip" or not _seven_zip_available(options):
            raise RiftUnpackError(
                f"{path.name} uses an encryption or compression method that the built-in reader "
                "does not support. Install 7-Zip (7z or 7za on PATH) and try again."
            ) from exc
        report = _extract_via_7zip(primary, destination, fmt, options, progress, cancel)
    if deep and _depth < max_depth:
        nested = [
            p
            for p in destination.rglob("*")
            if p.is_file() and p.resolve() not in existing_files and detect_format(p)
        ]
        for child in nested:
            child_dest = child.parent / archive_base_name(child)
            extract_archive(
                child, child_dest, options, progress, cancel, True, max_depth, _depth + 1
            )
    return report


def _extract_python_backend(path, destination, fmt, options, report, progress, cancel):
    try:
        members, archive = _backend(path, fmt, options)
    except NotImplementedError as exc:
        raise UnsupportedMethodError(str(exc)) from exc
    try:
        selected = [m for m in members if matches_patterns(m.name, options.include)]
        _check_limits(selected, options)
        compressed_total = sum(max(0, item.compressed) for item in selected)
        if fmt.startswith("tar.") and compressed_total:
            archive_ratio = sum(max(0, item.size) for item in selected) / max(
                path.stat().st_size, 1
            )
            if archive_ratio > options.max_ratio:
                raise ArchiveLimitError(
                    f"Compression ratio {archive_ratio:.1f}:1 exceeds limit "
                    f"{options.max_ratio:.1f}:1."
                )
        for member in selected:
            safe_member_name(member.name)
            if member.is_link:
                raise UnsafeArchiveError(f"Archive links are not extracted: {member.name}")
        destination.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".riftunpack-", dir=destination) as tmp:
            stage = Path(tmp) / "stage"
            stage.mkdir()
            actual_total = 0
            ratio_denominator = (
                max(path.stat().st_size, 1) if fmt.startswith("tar.") else compressed_total
            )
            for index, member in enumerate(selected, 1):
                if cancel and cancel():
                    raise CancelledError("Extraction cancelled by user.")
                target = safe_join(stage, member.name)
                if member.is_dir:
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                if not member.opener:
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    with member.opener() as src, target.open("wb") as out:
                        while chunk := src.read(1024 * 1024):
                            actual_total += len(chunk)
                            if actual_total > options.max_total_size:
                                raise ArchiveLimitError(
                                    "Actual extracted size exceeded the configured limit."
                                )
                            if (
                                ratio_denominator
                                and actual_total / ratio_denominator > options.max_ratio
                            ):
                                raise ArchiveLimitError(
                                    "Actual compression ratio exceeded the configured limit."
                                )
                            out.write(chunk)
                except NotImplementedError as exc:
                    raise UnsupportedMethodError(str(exc)) from exc
                except (
                    RuntimeError,
                    zipfile.BadZipFile,
                    tarfile.TarError,
                    EOFError,
                    OSError,
                ) as exc:
                    if "password" in str(exc).lower() or "encrypted" in str(exc).lower():
                        raise PasswordRequiredError(
                            "Archive password is missing or incorrect."
                        ) from exc
                    raise RiftUnpackError(f"Could not extract {member.name}: {exc}") from exc
                # A zero mode in ZIP external attributes means "unspecified" for
                # common non-Unix writers, not an intentional no-access file.
                if member.mode is not None and member.mode & 0o777:
                    try:
                        target.chmod(member.mode & 0o777)
                    except OSError:
                        pass
                if options.preserve_metadata and member.mtime:
                    try:
                        os.utime(target, (member.mtime, member.mtime))
                    except OSError:
                        pass
                if target.stat().st_size > options.max_total_size:
                    raise ArchiveLimitError("Actual extracted size exceeded the configured limit.")
                if progress:
                    progress(index, len(selected), member.name)
            _copy_stage(stage, destination, options, report, progress, cancel)
    finally:
        close = getattr(archive, "close", None)
        if close:
            close()


def _extract_7z(path, destination, options, progress, cancel):
    report = ExtractionReport(path, "7z", destination)
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".riftunpack-", dir=destination) as tmp:
        stage = Path(tmp) / "stage"
        stage.mkdir()
        if _is_split_archive(path):
            _external_7z(path, stage, options.seven_zip, options.password, options, cancel)
            filtered_by_tool = True
        else:
            try:
                _py7zr_extract(path, stage, options.password, options)
                filtered_by_tool = False
            except RiftUnpackError as exc:
                # Limits, unsafe names, and passwords are final answers; other failures may
                # be handled by 7-Zip when it is installed.
                if isinstance(exc, (PasswordRequiredError, UnsafeArchiveError, ArchiveLimitError)):
                    raise
                if not _seven_zip_available(options):
                    raise
                shutil.rmtree(stage, ignore_errors=True)
                stage.mkdir()
                _external_7z(path, stage, options.seven_zip, options.password, options, cancel)
                filtered_by_tool = True
        if options.include and not filtered_by_tool:
            # py7zr extracts every member; keep only the selected files before copying.
            for item in stage.rglob("*"):
                if item.is_file() and not matches_patterns(
                    item.relative_to(stage).as_posix(), options.include
                ):
                    item.unlink()
        _copy_stage(stage, destination, options, report, progress, cancel)
    return report


def _extract_via_7zip(path, destination, fmt, options, progress, cancel):
    """Extract any archive that 7-Zip understands (used for split and unsupported sets)."""
    report = ExtractionReport(path, fmt, destination)
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".riftunpack-", dir=destination) as tmp:
        stage = Path(tmp) / "stage"
        stage.mkdir()
        _external_7z(path, stage, options.seven_zip, options.password, options, cancel)
        _copy_stage(stage, destination, options, report, progress, cancel)
    return report


def _is_root_entry(name: str) -> bool:
    """ISO and some other images list their root directory as '.' or '/'."""
    return name.strip("./\\") == ""


def _libarchive_list(path: Path, options: ExtractOptions) -> list[Member]:
    try:
        import libarchive
    except ImportError as exc:
        raise UnsupportedFormatError(
            _missing_backend_message(detect_format(path) or "archive")
        ) from exc
    try:
        with libarchive.file_reader(
            str(path), header_codec=options.encoding or "utf-8", passphrase=options.password
        ) as reader:
            members = []
            for entry in reader:
                name = entry.pathname
                if _is_root_entry(name):
                    continue
                members.append(
                    Member(
                        name,
                        int(entry.size or 0),
                        0,
                        entry.isdir,
                        entry.issym or entry.islnk,
                        mtime=entry.mtime,
                        mode=entry.perm,
                    )
                )
        return members
    except Exception as exc:
        raise RiftUnpackError(f"Could not inspect {path.name} with libarchive: {exc}") from exc


def _libarchive_extract(
    path: Path,
    stage: Path,
    options: ExtractOptions,
    progress: Progress | None,
    cancel: Cancel | None,
) -> list[Member]:
    try:
        import libarchive
    except ImportError as exc:
        raise UnsupportedFormatError(
            _missing_backend_message(detect_format(path) or "archive")
        ) from exc
    members = _libarchive_list(path, options)
    selected = [m for m in members if matches_patterns(m.name, options.include)]
    _check_limits(selected, options)
    for member in selected:
        safe_member_name(member.name)
        if member.is_link:
            raise UnsafeArchiveError(f"Archive links are not extracted: {member.name}")
    total = 0
    compressed_size = max(path.stat().st_size, 1)
    try:
        with libarchive.file_reader(
            str(path), header_codec=options.encoding or "utf-8", passphrase=options.password
        ) as reader:
            for index, entry in enumerate(reader, 1):
                if _is_root_entry(entry.pathname):
                    continue
                if not matches_patterns(entry.pathname, options.include):
                    # Consume skipped member blocks so the streaming reader can advance.
                    for _ in entry.get_blocks():
                        pass
                    continue
                if cancel and cancel():
                    raise CancelledError("Extraction cancelled by user.")
                target = safe_join(stage, entry.pathname)
                if entry.isdir:
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                if not entry.isreg:
                    raise UnsafeArchiveError(
                        f"Special filesystem entry is not extracted: {entry.pathname}"
                    )
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("wb") as output:
                    for block in entry.get_blocks():
                        total += len(block)
                        if total > options.max_total_size:
                            raise ArchiveLimitError(
                                "Actual extracted size exceeded the configured limit."
                            )
                        if total / compressed_size > options.max_ratio:
                            raise ArchiveLimitError(
                                "Actual compression ratio exceeded the configured limit."
                            )
                        output.write(block)
                if options.preserve_metadata:
                    if entry.perm:
                        try:
                            target.chmod(entry.perm & 0o777)
                        except OSError:
                            pass
                    if entry.mtime:
                        try:
                            os.utime(target, (entry.mtime, entry.mtime))
                        except OSError:
                            pass
                if progress:
                    progress(index, max(len(selected), 1), entry.pathname)
    except (UnsafeArchiveError, ArchiveLimitError, CancelledError):
        raise
    except Exception as exc:
        raise RiftUnpackError(f"Could not extract {path.name} with libarchive: {exc}") from exc
    return selected


def _extract_external(path, destination, fmt, options, progress, cancel):
    report = ExtractionReport(path, fmt, destination)
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".riftunpack-", dir=destination) as tmp:
        stage = Path(tmp) / "stage"
        stage.mkdir()
        try:
            _libarchive_extract(path, stage, options, progress, cancel)
        except (UnsafeArchiveError, ArchiveLimitError, CancelledError):
            raise
        except RiftUnpackError:
            if not _seven_zip_available(options):
                raise
            # libarchive may have left partial files; start the fallback from an empty stage.
            shutil.rmtree(stage, ignore_errors=True)
            stage.mkdir()
            _external_7z(path, stage, options.seven_zip, options.password, options, cancel)
        _copy_stage(stage, destination, options, report, progress, cancel)
    return report


def _extract_single_compressed(path, destination, fmt, options, progress, cancel):
    opener = {"gz": gzip.open, "bz2": bz2.open, "xz": lzma.open}.get(fmt)
    if fmt == "zst":
        try:
            import zstandard
        except ImportError as exc:
            raise UnsupportedFormatError(
                "ZST needs the optional zstandard package. Install it with: pip install zstandard"
            ) from exc

        @contextmanager
        def opener(file_path, mode):
            with Path(file_path).open("rb") as compressed:
                with zstandard.ZstdDecompressor().stream_reader(compressed) as reader:
                    yield reader

    output = path.name
    suffixes = {
        "gz": (".gz",),
        "bz2": (".bz2",),
        "xz": (".xz",),
        "zst": (".zst",),
    }
    for suffix in suffixes[fmt]:
        if output.lower().endswith(suffix):
            output = output[: -len(suffix)]
            break
    if not output:
        output = path.stem or "decompressed"
    if options.max_files < 1:
        raise ArchiveLimitError("Archive contains one file; configured file limit is zero.")
    destination.mkdir(parents=True, exist_ok=True)
    target = safe_join(destination, output)
    if target.exists() and options.conflict == "skip":
        return ExtractionReport(path, fmt, destination, skipped=1)
    if target.exists() and options.conflict == "rename":
        n = 1
        while target.exists():
            target = target.with_name(f"{target.stem} ({n}){target.suffix}")
            n += 1
    report = ExtractionReport(path, fmt, destination)
    try:
        with opener(path, "rb") as src, target.open("wb") as out:
            compressed_size = max(path.stat().st_size, 1)
            while chunk := src.read(1024 * 1024):
                if cancel and cancel():
                    raise CancelledError("Extraction cancelled by user.")
                out.write(chunk)
                report.bytes_written += len(chunk)
                if report.bytes_written > options.max_total_size:
                    raise ArchiveLimitError("Actual extracted size exceeded the configured limit.")
                if report.bytes_written / compressed_size > options.max_ratio:
                    raise ArchiveLimitError("Compression ratio exceeded the configured limit.")
        report.extracted = 1
        if options.preserve_metadata:
            try:
                os.utime(target, (path.stat().st_mtime, path.stat().st_mtime))
            except OSError:
                pass
        if progress:
            progress(1, 1, output)
    except RiftUnpackError:
        target.unlink(missing_ok=True)
        raise
    except (gzip.BadGzipFile, EOFError, lzma.LZMAError, OSError) as exc:
        target.unlink(missing_ok=True)
        raise RiftUnpackError(f"Could not decompress {path.name}: {exc}") from exc
    except Exception as exc:
        target.unlink(missing_ok=True)
        raise RiftUnpackError(f"Could not decompress {path.name}: {exc}") from exc
    return report


def list_archive(path: str | Path, options: ExtractOptions | None = None) -> list[Member]:
    """Return validated member metadata without writing archive contents."""
    path = Path(path)
    options = options or ExtractOptions()
    fmt = detect_format(path)
    if not fmt:
        raise UnsupportedFormatError(f"Could not recognize archive contents: {path.name}")
    if fmt == "7z":
        try:
            import py7zr

            with py7zr.SevenZipFile(path, mode="r", password=options.password) as archive:
                return [
                    Member(
                        x.filename,
                        int(getattr(x, "uncompressed", 0) or 0),
                        int(getattr(x, "compressed", 0) or 0),
                        bool(getattr(x, "is_directory", False)),
                        (
                            bool(getattr(x, "is_symlink", False))
                            or bool(getattr(x, "is_junction", False))
                            or bool(getattr(x, "is_socket", False))
                        ),
                    )
                    for x in archive.list()
                ]
        except ImportError as exc:
            raise UnsupportedFormatError(_missing_backend_message(fmt)) from exc
    if fmt == "zst":
        return [Member(path.name, path.stat().st_size, path.stat().st_size, False, False)]
    if fmt in {"iso", "cab"}:
        return _libarchive_list(path, options)
    if fmt in {"gz", "bz2", "xz"}:
        return [Member(path.name, path.stat().st_size, path.stat().st_size, False, False)]
    members, archive = _backend(path, fmt, options)
    try:
        return members
    finally:
        close = getattr(archive, "close", None)
        if close:
            close()


def verify_archive(path: str | Path, options: ExtractOptions | None = None) -> bool:
    """Read/extract to an isolated temporary folder to validate CRCs and archive structure."""
    path = Path(path)
    options = options or ExtractOptions()
    fmt = detect_format(path)
    if not fmt:
        raise UnsupportedFormatError(f"Could not recognize archive contents: {path.name}")
    with tempfile.TemporaryDirectory(prefix="riftunpack-check-") as tmp:
        extract_archive(path, Path(tmp) / "checked", options)
    return True


def expand_inputs(inputs: Iterable[str], recursive: bool = False) -> list[Path]:
    """Resolve input files/folders to unique recognizable archives."""
    found: dict[str, Path] = {}
    for raw in inputs:
        path = Path(raw)
        candidates = (
            path.rglob("*") if path.is_dir() and recursive else ([path] if path.is_file() else [])
        )
        for candidate in candidates:
            if candidate.is_file() and detect_format(candidate):
                # Treat secondary volumes as part of their leading archive, not independent jobs.
                lower_name = candidate.name.lower()
                secondary_7z_volume = ".7z." in lower_name and not lower_name.endswith(".001")
                if (
                    lower_name.endswith(".z01")
                    or secondary_7z_volume
                    or (
                        ".part" in lower_name
                        and lower_name.endswith(".rar")
                        and not lower_name.endswith(".part1.rar")
                    )
                ):
                    continue
                found[str(candidate.resolve())] = candidate
    return sorted(found.values())
