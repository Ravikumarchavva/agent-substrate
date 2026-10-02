"""PNG encoding with ``zlib`` and ``struct`` — so rendering a page for OCR or a figure for the model needs no imaging library."""

from __future__ import annotations

import struct
import zlib

_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_MODES = {"L": (0, 1), "RGB": (2, 3), "RGBA": (6, 4)}  # mode -> (PNG colour type, bytes per pixel)


def _chunk(kind: bytes, payload: bytes) -> bytes:
    body = kind + payload
    return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)


def encode_png(width: int, height: int, pixels: bytes, *, mode: str = "L", stride: int | None = None, level: int = 6) -> bytes:
    """A PNG of ``width`` × ``height`` from raw ``pixels`` (rows of ``stride`` bytes, default tightly packed)."""
    colour, depth = _MODES[mode]
    row = width * depth
    stride = stride or row
    if width < 1 or height < 1 or len(pixels) < stride * (height - 1) + row:
        raise ValueError("pixel data does not match the image size")
    raw = bytearray()
    for y in range(height):
        raw.append(0)  # filter: none
        raw += pixels[y * stride : y * stride + row]
    header = struct.pack(">IIBBBBB", width, height, 8, colour, 0, 0, 0)
    return _SIGNATURE + _chunk(b"IHDR", header) + _chunk(b"IDAT", zlib.compress(bytes(raw), level)) + _chunk(b"IEND", b"")


def png_size(data: bytes) -> tuple[int, int] | None:
    """``(width, height)`` of a PNG, or ``None`` if ``data`` is not one."""
    if data[:8] != _SIGNATURE or data[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", data[16:24])


__all__ = ["encode_png", "png_size"]
