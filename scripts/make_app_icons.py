#!/usr/bin/env python3
"""The board's app icons, drawn from the favicon's own geometry.

The pages carry one SVG favicon inline (a hook in the accent colour on the
surface colour, a green dot where the hook ends). A phone's home screen wants
PNGs — 192 and 512, and a "maskable" one whose glyph sits inside the safe
zone so Android can crop it to any shape. There is no image library in any
venv and none is worth adding for three files, so this rasterises the same
shapes directly: a rounded square, a stroked path of lines and quarter arcs,
two discs, anti-aliased by signed distance. Run it when the favicon changes;
the output is committed beside the manifest.

    python3 scripts/make_app_icons.py
"""

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "hookrelay" / "hookrelay" / "static"
SURFACE, ACCENT, OK = (0x11, 0x15, 0x1D), (0x4C, 0x8D, 0xFF), (0x3D, 0xD6, 0x8C)
STROKE = 2.2  # in the favicon's 24-unit grid


def _arc(cx: float, cy: float, r: float, a0: float, a1: float, n: int = 14) -> list[tuple[float, float]]:
    return [
        (
            cx + r * math.cos(math.radians(a0 + (a1 - a0) * i / n)),
            cy + r * math.sin(math.radians(a0 + (a1 - a0) * i / n)),
        )
        for i in range(n + 1)
    ]


# M6 8 h5 a3 3 0 0 1 3 3 v2 a3 3 0 0 0 3 3 h1 — the favicon's path, as points.
HOOK = (
    [(6.0, 8.0), (11.0, 8.0)]
    + _arc(11, 11, 3, -90, 0)[1:]
    + [(14.0, 13.0)]
    + _arc(17, 13, 3, 180, 90)[1:]
    + [(18.0, 16.0)]
)


def _seg_distance(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    dx, dy = bx - ax, by - ay
    length = dx * dx + dy * dy
    t = 0.0 if length == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _coverage(distance: float, half_width: float, px_unit: float) -> float:
    """How much of a pixel a shape covers, from the signed distance to its edge."""
    return max(0.0, min(1.0, 0.5 + (half_width - distance) / px_unit))


def _blend(base: tuple[int, int, int], colour: tuple[int, int, int], alpha: float) -> tuple[int, int, int]:
    return tuple(round(b + (c - b) * alpha) for b, c in zip(base, colour, strict=True))  # type: ignore[return-value]


def render(size: int, *, maskable: bool) -> bytes:
    """One PNG. Maskable: full-bleed background and the glyph at 70% in the centre."""
    unit = size / 24.0
    glyph_scale, offset = (0.7, 24 * 0.15) if maskable else (1.0, 0.0)
    radius = 0.0 if maskable else 6.0 * unit
    rows = []
    for y in range(size):
        row = bytearray()
        for x in range(size):
            px, py = x + 0.5, y + 0.5
            # The rounded square: distance outside the inset box, minus the corner radius.
            qx, qy = abs(px - size / 2) - (size / 2 - radius), abs(py - size / 2) - (size / 2 - radius)
            outside = math.hypot(max(qx, 0.0), max(qy, 0.0)) + min(max(qx, qy), 0.0) - radius
            alpha = _coverage(outside, 0.0, 1.0)
            if alpha <= 0.0:
                row += bytes((0, 0, 0, 0))
                continue
            gx, gy = (px / unit - offset) / glyph_scale, (py / unit - offset) / glyph_scale
            colour = SURFACE
            d = min(_seg_distance(gx, gy, *HOOK[i], *HOOK[i + 1]) for i in range(len(HOOK) - 1))
            colour = _blend(colour, ACCENT, _coverage(d, STROKE / 2, 1.0 / (unit * glyph_scale)))
            colour = _blend(colour, ACCENT, _coverage(math.hypot(gx - 6, gy - 8), 1.8, 1.0 / (unit * glyph_scale)))
            colour = _blend(colour, OK, _coverage(math.hypot(gx - 18, gy - 16), 1.8, 1.0 / (unit * glyph_scale)))
            row += bytes((*colour, round(255 * alpha)))
        rows.append(b"\x00" + bytes(row))
    raw = zlib.compress(b"".join(rows), 9)

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", raw) + chunk(b"IEND", b"")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, size, maskable in (
        ("icon-192.png", 192, False),
        ("icon-512.png", 512, False),
        ("icon-maskable-512.png", 512, True),
    ):
        (OUT / name).write_bytes(render(size, maskable=maskable))
        print(f"{OUT / name}: {size}x{size}{' maskable' if maskable else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
