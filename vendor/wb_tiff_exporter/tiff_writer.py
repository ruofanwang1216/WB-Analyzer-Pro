from __future__ import annotations

import struct


def build_grayscale16_tiff(
    pixel_bytes: bytes,
    *,
    width: int,
    height: int,
    byteorder: str = "little",
    white_is_zero: bool = True,
) -> bytes:
    """Build a minimal uncompressed 16-bit grayscale TIFF."""

    if width <= 0 or height <= 0:
        raise ValueError("width and height must be positive")
    if len(pixel_bytes) != width * height * 2:
        raise ValueError("pixel byte length does not match width * height * 2")
    if byteorder not in {"little", "big"}:
        raise ValueError("byteorder must be 'little' or 'big'")

    pack_prefix = "<" if byteorder == "little" else ">"
    header_magic = b"II*\x00" if byteorder == "little" else b"MM\x00*"

    entry_count = 13
    ifd_offset = 8
    ifd_size = 2 + (entry_count * 12) + 4
    x_res_offset = ifd_offset + ifd_size
    y_res_offset = x_res_offset + 8
    pixel_offset = y_res_offset + 8

    photometric = 0 if white_is_zero else 1
    entries = [
        _pack_ifd_entry(pack_prefix, 256, 4, 1, width),
        _pack_ifd_entry(pack_prefix, 257, 4, 1, height),
        _pack_ifd_entry(pack_prefix, 258, 3, 1, 16),
        _pack_ifd_entry(pack_prefix, 259, 3, 1, 1),
        _pack_ifd_entry(pack_prefix, 262, 3, 1, photometric),
        _pack_ifd_entry(pack_prefix, 273, 4, 1, pixel_offset),
        _pack_ifd_entry(pack_prefix, 277, 3, 1, 1),
        _pack_ifd_entry(pack_prefix, 278, 4, 1, height),
        _pack_ifd_entry(pack_prefix, 279, 4, 1, len(pixel_bytes)),
        _pack_ifd_entry(pack_prefix, 282, 5, 1, x_res_offset),
        _pack_ifd_entry(pack_prefix, 283, 5, 1, y_res_offset),
        _pack_ifd_entry(pack_prefix, 296, 3, 1, 2),
        _pack_ifd_entry(pack_prefix, 339, 3, 1, 1),
    ]

    return b"".join(
        [
            header_magic,
            struct.pack(f"{pack_prefix}I", ifd_offset),
            struct.pack(f"{pack_prefix}H", entry_count),
            b"".join(entries),
            struct.pack(f"{pack_prefix}I", 0),
            struct.pack(f"{pack_prefix}II", 300, 1),
            struct.pack(f"{pack_prefix}II", 300, 1),
            pixel_bytes,
        ]
    )


def _pack_ifd_entry(
    pack_prefix: str,
    tag: int,
    field_type: int,
    count: int,
    value: int,
) -> bytes:
    if field_type == 3 and count == 1:
        return struct.pack(f"{pack_prefix}HHI", tag, field_type, count) + struct.pack(
            f"{pack_prefix}H", value
        ) + b"\x00\x00"
    return struct.pack(f"{pack_prefix}HHII", tag, field_type, count, value)
