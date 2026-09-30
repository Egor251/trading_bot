#!/usr/bin/env python3
"""Generate minimal RGBA PNGs for Tauri icons (stdlib only)."""

from __future__ import annotations

import struct
import zlib
from pathlib import Path


def _png(width: int, height: int, rgba: bytes) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    raw = b""
    row = width * 4
    for y in range(height):
        raw += b"\x00" + rgba[y * row : (y + 1) * row]
    return b"".join(
        [
            b"\x89PNG\r\n\x1a\n",
            chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)),
            chunk(b"IDAT", zlib.compress(raw, 9)),
            chunk(b"IEND", b""),
        ]
    )


def solid(w: int, h: int, color: tuple[int, int, int, int]) -> bytes:
    r, g, b, a = color
    px = bytes([r, g, b, a]) * (w * h)
    return _png(w, h, px)


def main() -> None:
    root = Path(__file__).resolve().parent / "src-tauri" / "icons"
    root.mkdir(parents=True, exist_ok=True)
    # Dark teal institutional mark
    color = (14, 116, 144, 255)
    for name, size in (
        ("32x32.png", 32),
        ("128x128.png", 128),
        ("henry.w@example.net", 256),
        ("icon.png", 512),
    ):
        (root / name).write_bytes(solid(size, size, color))
    # Windows .ico: embed a simple 32x32 PNG as .ico is complex; Tauri also
    # accepts png named icon.ico on some setups — write multi-size via png copy.
    # Minimal ICO with one 32x32 PNG image.
    png32 = solid(32, 32, color)
    ico = bytearray()
    ico += struct.pack("<HHH", 0, 1, 1)
    ico += struct.pack("<BBBBHHII", 32, 32, 0, 0, 1, 32, len(png32), 22)
    ico += png32
    (root / "icon.ico").write_bytes(ico)
    (root / "icon.icns").write_bytes(png32)  # placeholder; macOS build needs real icns
    print(f"wrote icons under {root}")


if __name__ == "__main__":
    main()
