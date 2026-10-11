"""Content-based archive identification and multipart-volume helpers."""

from __future__ import annotations

import re
import tarfile
from pathlib import Path

SIGNATURES = {
    "7z": (b"7z\xbc\xaf\x27\x1c",),
    "rar": (b"Rar!\x1a\x07\x00", b"Rar!\x1a\x07\x01\x00"),
    "zip": (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"),
    "gz": (b"\x1f\x8b",),
    "bz2": (b"BZh",),
    "xz": (b"\xfd7zXZ\x00",),
    "zst": (b"\x28\xb5\x2f\xfd",),
    "cab": (b"MSCF",),
}


def detect_format(path: str | Path) -> str | None:
    """Return a format label based on bytes, not the filename extension."""
    path = Path(path)
    try:
        with path.open("rb") as stream:
            head = stream.read(4096)
            stream.seek(0x8000)
            iso = stream.read(6)
        if iso == b"\x01CD001":
            return "iso"
        for fmt, signatures in SIGNATURES.items():
            if any(head.startswith(sig) for sig in signatures):
                if fmt in {"gz", "bz2", "xz"}:
                    try:
                        return f"tar.{fmt}" if tarfile.is_tarfile(path) else fmt
                    except (OSError, tarfile.TarError):
                        pass
                return fmt
        if len(head) > 262 and head[257:262] == b"ustar":
            return "tar"
        if tarfile.is_tarfile(path):
            return "tar"
        # Split ZIP continuation volumes start with a central-directory marker.
        if head.startswith(b"PK\x06\x06"):
            return "zip"
    except (OSError, PermissionError):
        return None
    return None


_COMPOUND_SUFFIXES = (
    ".tar.gz",
    ".tar.bz2",
    ".tar.xz",
    ".tar.zst",
    ".tgz",
    ".tbz2",
    ".txz",
    ".tzst",
)


def archive_base_name(path: str | Path) -> str:
    """Folder name for an archive: ``mods.tar.gz`` -> ``mods``, ``data.7z.001`` -> ``data``."""
    name = Path(path).name
    lower = name.lower()
    for suffix in _COMPOUND_SUFFIXES:
        if lower.endswith(suffix) and len(name) > len(suffix):
            return name[: -len(suffix)]
    split = re.match(r"^(.+)\.(\d{3})$", name)
    if split and split.group(1):
        name = split.group(1)
    part = re.match(r"^(.+)\.part\d+\.rar$", name, re.IGNORECASE)
    if part:
        return part.group(1)
    return Path(name).stem or name


def _part_number(path: Path) -> int:
    found = re.search(r"\.part(\d+)", path.name, re.IGNORECASE)
    return int(found.group(1)) if found else 0


def multipart_group(path: str | Path) -> list[Path]:
    """Return known sibling volumes in order, starting with the supplied path."""
    p = Path(path)
    name = p.name
    match = re.match(r"^(.*)\.part(\d+)\.rar$", name, re.IGNORECASE)
    if match:
        prefix = match.group(1)
        pattern = re.compile(rf"^{re.escape(prefix)}\.part(\d+)\.rar$", re.IGNORECASE)
        files = [x for x in p.parent.iterdir() if pattern.match(x.name)]
        return sorted(files, key=_part_number)
    match = re.match(r"^(.*\.7z)\.(\d{3})$", name, re.IGNORECASE)
    if match:
        base = match.group(1)
        pattern = re.compile(rf"^{re.escape(base)}\.(\d{{3}})$", re.IGNORECASE)
        files = [x for x in p.parent.iterdir() if pattern.match(x.name)]
        return sorted(files, key=lambda x: int(x.suffix[1:]))
    if re.match(r"^.+\.z\d{2}$", name, re.IGNORECASE):
        expected_zip = f"{p.stem}.zip".casefold()
        base = next(
            (x for x in p.parent.iterdir() if x.name.casefold() == expected_zip),
            p.with_suffix(".zip"),
        )
        if base.exists():
            pattern = re.compile(rf"^{re.escape(base.stem)}\.z(\d{{2}})$", re.IGNORECASE)
            zfiles = sorted(
                (x for x in p.parent.iterdir() if pattern.match(x.name)),
                key=lambda x: int(x.suffix[2:]),
            )
            return [*zfiles, base]
    if name.lower().endswith(".zip"):
        pattern = re.compile(rf"^{re.escape(p.stem)}\.z(\d{{2}})$", re.IGNORECASE)
        zfiles = sorted(
            (x for x in p.parent.iterdir() if pattern.match(x.name)),
            key=lambda x: int(x.suffix[2:]),
        )
        if zfiles:
            return [*zfiles, p]
    return [p]
