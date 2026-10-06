#!/usr/bin/env python3
"""The three boards' app icons, drawn from each favicon's own geometry.

Each page carries one SVG favicon inline — a glyph in the accent colour on the
surface colour: the pipe's hook, the judge's scales, the investigator's lens.
A phone's home screen wants PNGs — 192 and 512, and a "maskable" one whose
glyph sits inside the safe zone so Android can crop it to any shape. There is
no image library in any venv and none is worth adding for nine files, so this
rasterises the same shapes directly: a rounded square, stroked polylines
(lines and arcs as points), a ring, discs, anti-aliased by signed distance.
Run it when a favicon changes; the output is committed beside each manifest.

    python3 scripts/make_app_icons.py
"""

from __future__ import annotations

import math
import struct
import zlib
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SURFACE, ACCENT, OK = (0x11, 0x15, 0x1D), (0x4C, 0x8D, 0xFF), (0x3D, 0xD6, 0x8C)
Colour = tuple[int, int, int]
# (distance from a point to the shape's centre line, the half width painted, the colour, the bounding box)
Shape = tuple[Callable[[float, float], float], float, Colour, tuple[float, float, float, float]]


def _arc(cx: float, cy: float, r: float, a0: float, a1: float, n: int = 14) -> list[tuple[float, float]]:
    return [
        (
            cx + r * math.cos(math.radians(a0 + (a1 - a0) * i / n)),
            cy + r * math.sin(math.radians(a0 + (a1 - a0) * i / n)),
        )
        for i in range(n + 1)
    ]


def _seg_distance(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    dx, dy = bx - ax, by - ay
    length = dx * dx + dy * dy
    t = 0.0 if length == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def line(points: list[tuple[float, float]], width: float, colour: Colour) -> Shape:
    """A stroked polyline, round-capped — a favicon's `path` with `fill='none'`."""
    xs, ys = [x for x, _ in points], [y for _, y in points]
    box = (min(xs) - width, min(ys) - width, max(xs) + width, max(ys) + width)
    return (
        lambda x, y: min(_seg_distance(x, y, *points[i], *points[i + 1]) for i in range(len(points) - 1)),
        width / 2,
        colour,
        box,
    )


def ring(cx: float, cy: float, r: float, width: float, colour: Colour) -> Shape:
    """A stroked circle."""
    reach = r + width
    box = (cx - reach, cy - reach, cx + reach, cy + reach)
    return (lambda x, y: abs(math.hypot(x - cx, y - cy) - r), width / 2, colour, box)


def disc(cx: float, cy: float, r: float, colour: Colour) -> Shape:
    """A filled circle."""
    return (lambda x, y: math.hypot(x - cx, y - cy), r, colour, (cx - r - 1, cy - r - 1, cx + r + 1, cy + r + 1))


def _pan(cx: float) -> list[tuple[float, float]]:
    """One pan of the scales: `M{cx} 8 l-2.5 5.5 a2.5 2.5 0 0 0 5 0 z` — two strings and a bowl."""
    return [(cx, 8.0), (cx - 2.5, 13.5)] + _arc(cx, 13.5, 2.5, 180, 0)[1:] + [(cx, 8.0)]


# M6 8 h5 a3 3 0 0 1 3 3 v2 a3 3 0 0 0 3 3 h1 — the pipe's hook, as points.
HOOK = (
    [(6.0, 8.0), (11.0, 8.0)]
    + _arc(11, 11, 3, -90, 0)[1:]
    + [(14.0, 13.0)]
    + _arc(17, 13, 3, 180, 90)[1:]
    + [(18.0, 16.0)]
)

# Each favicon's shapes, in paint order, on the favicon's own 24-unit grid.
GLYPHS: dict[str, list[Shape]] = {
    # The hook, a dot at each end.
    "hookrelay": [
        line(HOOK, 2.2, ACCENT),
        disc(6, 8, 1.8, ACCENT),
        disc(18, 16, 1.8, OK),
    ],
    # M12 5v14 M7 19h10 M6 8h12, and a pan hanging from each end of the beam.
    "hookjudge": [
        line([(12.0, 5.0), (12.0, 19.0)], 1.8, ACCENT),
        line([(7.0, 19.0), (17.0, 19.0)], 1.8, ACCENT),
        line([(6.0, 8.0), (18.0, 8.0)], 1.8, ACCENT),
        line(_pan(6), 1.8, ACCENT),
        line(_pan(18), 1.8, ACCENT),
    ],
    # A lens, and a handle in the colour of a finding.
    "hookprobe": [
        ring(10.5, 10.5, 5, 2.0, ACCENT),
        line([(14.5, 14.5), (19.0, 19.0)], 2.2, OK),
    ],
}


def _coverage(distance: float, half_width: float, px_unit: float) -> float:
    """How much of a pixel a shape covers, from the signed distance to its edge."""
    return max(0.0, min(1.0, 0.5 + (half_width - distance) / px_unit))


def _blend(base: Colour, colour: Colour, alpha: float) -> Colour:
    return tuple(round(b + (c - b) * alpha) for b, c in zip(base, colour, strict=True))  # type: ignore[return-value]


def render(size: int, shapes: list[Shape], *, maskable: bool) -> bytes:
    """One PNG. Maskable: full-bleed background and the glyph at 70% in the centre."""
    unit = size / 24.0
    glyph_scale, offset = (0.7, 24 * 0.15) if maskable else (1.0, 0.0)
    radius = 0.0 if maskable else 6.0 * unit
    px_unit = 1.0 / (unit * glyph_scale)
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
            for distance, half_width, paint, (x0, y0, x1, y1) in shapes:
                if x0 <= gx <= x1 and y0 <= gy <= y1:
                    colour = _blend(colour, paint, _coverage(distance(gx, gy), half_width, px_unit))
            row += bytes((*colour, round(255 * alpha)))
        rows.append(b"\x00" + bytes(row))
    raw = zlib.compress(b"".join(rows), 9)

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", raw) + chunk(b"IEND", b"")


def main() -> int:
    for service, shapes in GLYPHS.items():
        out = ROOT / service / service / "static"
        out.mkdir(parents=True, exist_ok=True)
        for name, size, maskable in (
            ("icon-192.png", 192, False),
            ("icon-512.png", 512, False),
            ("icon-maskable-512.png", 512, True),
        ):
            (out / name).write_bytes(render(size, shapes, maskable=maskable))
            print(f"{out.relative_to(ROOT) / name}: {size}x{size}{' maskable' if maskable else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
