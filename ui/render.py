"""Anti-aliased shapes as transparent PNGs, generated in pure Python.

Tk's canvas can't anti-alias on Windows and ttk's built-in themes are square
and flat. Drawing the few shapes the UI needs (rounded rectangles, circles,
chevrons, radio buttons) ourselves, at the real pixel size, gives smooth
edges at any display scale with no third-party imaging library.
"""

import base64
import math
import struct
import zlib
from typing import Callable, Optional

_RGB = tuple[int, int, int]


def hex_rgb(color: str) -> _RGB:
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def to_tk_data(png: bytes) -> str:
    return base64.b64encode(png).decode("ascii")


def _encode(width: int, height: int, rows: list[bytes]) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    raw = b"".join(b"\x00" + row for row in rows)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def _coverage(signed_distance: float) -> float:
    return min(1.0, max(0.0, 0.5 - signed_distance))


def _symmetric_rows(width: int, height: int, reach: int, pixel: Callable[[int, int], bytes]) -> list[bytes]:
    """Build rows of a shape that is mirror-symmetric on both axes, computing
    only the pixels near the edges and repeating the constant middle — keeps
    wide shapes (list-row highlights) cheap."""
    cx = min(width // 2, reach)
    cy = min(height // 2, reach)
    cache: dict[int, bytes] = {}

    def row(y: int) -> bytes:
        key = min(y, height - 1 - y, cy)
        if key not in cache:
            left = [pixel(x, key) for x in range(cx)]
            middle = pixel(cx, key) * (width - 2 * cx)
            cache[key] = b"".join(left) + middle + b"".join(reversed(left))
        return cache[key]

    return [row(y) for y in range(height)]


def rounded_rect(
    width: int,
    height: int,
    radius: float,
    fill: Optional[str],
    border: Optional[str] = None,
    border_width: float = 0,
) -> bytes:
    """A rounded rectangle with an optional inside border. fill=None leaves the
    interior transparent (border only)."""
    radius = min(radius, width / 2, height / 2)
    fill_rgb = hex_rgb(fill) if fill else (0, 0, 0)
    border_rgb = hex_rgb(border) if border else fill_rgb
    half_w, half_h = width / 2, height / 2

    def pixel(x: int, y: int) -> bytes:
        qx = abs(x + 0.5 - half_w) - (half_w - radius)
        qy = abs(y + 0.5 - half_h) - (half_h - radius)
        sd = math.hypot(max(qx, 0.0), max(qy, 0.0)) + min(max(qx, qy), 0.0) - radius
        outer = _coverage(sd)
        if outer <= 0:
            return b"\x00\x00\x00\x00"
        inner = _coverage(sd + border_width) if border_width else outer
        edge = outer - inner
        if fill:
            alpha = outer
            mix = [(fill_rgb[i] * inner + border_rgb[i] * edge) / outer for i in range(3)]
        else:
            alpha = edge
            if alpha <= 0:
                return b"\x00\x00\x00\x00"
            mix = border_rgb
        return bytes((round(mix[0]), round(mix[1]), round(mix[2]), round(255 * alpha)))

    reach = math.ceil(radius + border_width + 2)
    return _encode(width, height, _symmetric_rows(width, height, reach, pixel))


def _shape(width: int, height: int, color: str, alpha_at: Callable[[float, float], float]) -> bytes:
    r, g, b = hex_rgb(color)
    rows = []
    for y in range(height):
        row = bytearray()
        for x in range(width):
            row += bytes((r, g, b, round(255 * alpha_at(x + 0.5, y + 0.5))))
        rows.append(bytes(row))
    return _encode(width, height, rows)


def circle(diameter: int, color: str) -> bytes:
    return rounded_rect(diameter, diameter, diameter / 2, color)


def ring(diameter: int, color: str, thickness: float) -> bytes:
    return rounded_rect(diameter, diameter, diameter / 2, None, color, thickness)


def radio(size: int, color: str, selected: bool, scale: float, margin: int = 0) -> bytes:
    """A radio button; margin adds transparent space on its right so a label
    can sit beside it without a separate padding element."""
    center = size / 2
    outer = size / 2 - 0.75 * scale
    thickness = 1.4 * scale
    dot = size * 0.2

    def alpha_at(x: float, y: float) -> float:
        d = math.hypot(x - center, y - center)
        a = _coverage(abs(d - (outer - thickness / 2)) - thickness / 2)
        if selected:
            a = max(a, _coverage(d - dot))
        return a

    return _shape(size + margin, size, color, alpha_at)


def chevron(width: int, height: int, direction: str, color: str, stroke: float, reach: float) -> bytes:
    """A rounded-cap chevron pointing 'down', 'up' or 'right', centred in the
    image; reach is its half-width in pixels."""
    cx, cy = width / 2, height / 2
    tip = reach / 2
    if direction == "down":
        points = [(cx - reach, cy - tip), (cx, cy + tip), (cx + reach, cy - tip)]
    elif direction == "up":
        points = [(cx - reach, cy + tip), (cx, cy - tip), (cx + reach, cy + tip)]
    else:
        points = [(cx - tip, cy - reach), (cx + tip, cy), (cx - tip, cy + reach)]

    def segment_distance(px: float, py: float, a: tuple[float, float], b: tuple[float, float]) -> float:
        dx, dy = b[0] - a[0], b[1] - a[1]
        t = max(0.0, min(1.0, ((px - a[0]) * dx + (py - a[1]) * dy) / (dx * dx + dy * dy)))
        return math.hypot(px - (a[0] + t * dx), py - (a[1] + t * dy))

    def alpha_at(x: float, y: float) -> float:
        d = min(segment_distance(x, y, points[0], points[1]), segment_distance(x, y, points[1], points[2]))
        return _coverage(d - stroke / 2)

    return _shape(width, height, color, alpha_at)
