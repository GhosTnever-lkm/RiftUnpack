"""Hand-built RAR4 archive (stored entries) for tests that must not depend on UnRAR."""

from __future__ import annotations

import struct
import zlib
from pathlib import Path


def _block(htype: int, flags: int, body: bytes) -> bytes:
    """Block with CRC16 header (RAR4 layout: CRC, type, flags, size, body)."""
    head_size = 7 + len(body)
    rest = struct.pack("<BHH", htype, flags, head_size) + body
    crc = zlib.crc32(rest) & 0xFFFF
    return struct.pack("<H", crc) + rest


def write_rar4_stored(path: Path, files: dict[str, bytes]) -> Path:
    out = bytearray(b"Rar!\x1a\x07\x00")  # RAR4 signature
    out += _block(0x73, 0x0000, struct.pack("<HI", 0, 0))  # main archive header
    for name, data in files.items():
        name_b = name.encode("utf-8")
        ftime = ((2020 - 1980) << 25) | (1 << 21) | (2 << 16) | (12 << 11)
        body = (
            struct.pack(
                "<IIBIIBBHI",
                len(data),
                len(data),
                2,  # host OS: Windows (as WinRAR writes; 0x20 is the archive attribute)
                zlib.crc32(data) & 0xFFFFFFFF,
                ftime,
                29,  # unpack version 2.9
                0x30,  # method: store
                len(name_b),
                0x20,
            )
            + name_b
        )
        hdr = _block(0x74, 0x8000, body)
        out += hdr + data
    out += _block(0x7B, 0x4000, b"")  # end of archive
    Path(path).write_bytes(bytes(out))
    return path
