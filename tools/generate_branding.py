#!/usr/bin/env python3
"""Generate unified Hellbird SVG, PNG, ICO, and Windows RC branding assets without external deps.

Uses the exact symmetrical raised-wings-and-flame emblem from UI/Qt/Icon.cpp and about: pages
with Hellbird's Ember warm-gray palette (#1a1918 / #d95a1e / #ff8a4c).
"""

from __future__ import annotations

import math
from pathlib import Path
import struct
import zlib

BRANDING_DIR = Path(__file__).resolve().parent.parent / "resources" / "branding"

# Exact vector mark matching UI/Qt/Icon.cpp, newtab.html, version.html, and welcome.html
MARK_PATH = (
    "M0 1.6 C-1.2 -2.6 -5.2 -5.2 -10 -5.4 C-9.4 1 -5.6 6.2 0 8.2 "
    "C5.6 6.2 9.4 1 10 -5.4 C5.2 -5.2 1.2 -2.6 0 1.6 Z "
    "M0 1.6 C-2.6 -1.2 -2.2 -4.6 0.4 -8.2 C2.6 -4.8 2.4 -1.4 0 1.6 Z"
)

SVG_NORMAL = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" fill="none">
  <defs>
    <linearGradient id="bg" x1="20" y1="16" x2="236" y2="240" gradientUnits="userSpaceOnUse">
      <stop offset="0%" stop-color="#262523"/>
      <stop offset="100%" stop-color="#141312"/>
    </linearGradient>
    <linearGradient id="ember" x1="-10" y1="-8.2" x2="10" y2="8.2" gradientUnits="userSpaceOnUse">
      <stop offset="0%" stop-color="#FF8A4C"/>
      <stop offset="55%" stop-color="#D95A1E"/>
      <stop offset="100%" stop-color="#B3430F"/>
    </linearGradient>
  </defs>
  <rect x="16" y="16" width="224" height="224" rx="54" fill="url(#bg)"/>
  <rect x="17.5" y="17.5" width="221" height="221" rx="52.5" stroke="#F1EFED" stroke-opacity="0.10" stroke-width="3"/>
  <g transform="translate(128 130) scale(7.8)">
    <path d="{MARK_PATH}" fill="url(#ember)" fill-opacity="0.18" stroke="url(#ember)" stroke-width="1.35" stroke-linecap="round" stroke-linejoin="round"/>
  </g>
</svg>
"""

SVG_PRIVATE = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" fill="none">
  <defs>
    <linearGradient id="bgPriv" x1="20" y1="16" x2="236" y2="240" gradientUnits="userSpaceOnUse">
      <stop offset="0%" stop-color="#281F33"/>
      <stop offset="100%" stop-color="#0F0B14"/>
    </linearGradient>
    <linearGradient id="violet" x1="-10" y1="-8.2" x2="10" y2="8.2" gradientUnits="userSpaceOnUse">
      <stop offset="0%" stop-color="#C9B5FF"/>
      <stop offset="55%" stop-color="#8A64E5"/>
      <stop offset="100%" stop-color="#5B38B8"/>
    </linearGradient>
  </defs>
  <rect x="16" y="16" width="224" height="224" rx="54" fill="url(#bgPriv)"/>
  <rect x="17.5" y="17.5" width="221" height="221" rx="52.5" stroke="#F3F0FA" stroke-opacity="0.12" stroke-width="3"/>
  <g transform="translate(128 130) scale(7.8)">
    <path d="{MARK_PATH}" fill="url(#violet)" fill-opacity="0.20" stroke="url(#violet)" stroke-width="1.35" stroke-linecap="round" stroke-linejoin="round"/>
  </g>
</svg>
"""

WINDOWS_RC = """IDI_ICON1 ICON DISCARDABLE "hellbird.ico"

1 VERSIONINFO
 FILEVERSION 0,1,0,1
 PRODUCTVERSION 0,1,0,1
 FILEFLAGSMASK 0x3fL
 FILEFLAGS 0x2L
 FILEOS 0x40004L
 FILETYPE 0x1L
 FILESUBTYPE 0x0L
BEGIN
    BLOCK "StringFileInfo"
    BEGIN
        BLOCK "040904b0"
        BEGIN
            VALUE "CompanyName", "Hellbird Project"
            VALUE "FileDescription", "Hellbird Web Browser"
            VALUE "FileVersion", "0.1.0-alpha.1"
            VALUE "InternalName", "Hellbird"
            VALUE "OriginalFilename", "Hellbird.exe"
            VALUE "ProductName", "Hellbird"
            VALUE "ProductVersion", "0.1.0-alpha.1"
        END
    END
    BLOCK "VarFileInfo"
    BEGIN
        VALUE "Translation", 0x0409, 1200
    END
END
"""


def _png_chunk(tag: bytes, payload: bytes) -> bytes:
    crc = zlib.crc32(tag + payload) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + tag + payload + struct.pack(">I", crc)


def _encode_png(width: int, height: int, rgba: bytearray) -> bytes:
    raw = bytearray()
    stride = width * 4
    for y in range(height):
        raw.append(0)
        start = y * stride
        raw.extend(rgba[start : start + stride])
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", zlib.compress(bytes(raw), level=9))
        + _png_chunk(b"IEND", b"")
    )


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * max(0.0, min(1.0, t))


def _rounded_rect_sdf(px: float, py: float, half: float, radius: float) -> float:
    qx = abs(px) - (half - radius)
    qy = abs(py) - (half - radius)
    return math.hypot(max(qx, 0.0), max(qy, 0.0)) + min(max(qx, qy), 0.0) - radius


def _sample_cubic(
    p0: tuple[float, float],
    p1: tuple[float, float],
    p2: tuple[float, float],
    p3: tuple[float, float],
    steps: int = 24,
) -> list[tuple[float, float]]:
    pts: list[tuple[float, float]] = []
    for i in range(steps + 1):
        t = i / steps
        mt = 1.0 - t
        x = mt**3 * p0[0] + 3 * mt**2 * t * p1[0] + 3 * mt * t**2 * p2[0] + t**3 * p3[0]
        y = mt**3 * p0[1] + 3 * mt**2 * t * p1[1] + 3 * mt * t**2 * p2[1] + t**3 * p3[1]
        pts.append((x, y))
    return pts


def _build_mark_contours() -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    wings = (
        _sample_cubic((0.0, 1.6), (-1.2, -2.6), (-5.2, -5.2), (-10.0, -5.4))[:-1]
        + _sample_cubic((-10.0, -5.4), (-9.4, 1.0), (-5.6, 6.2), (0.0, 8.2))[:-1]
        + _sample_cubic((0.0, 8.2), (5.6, 6.2), (9.4, 1.0), (10.0, -5.4))[:-1]
        + _sample_cubic((10.0, -5.4), (5.2, -5.2), (1.2, -2.6), (0.0, 1.6))[:-1]
    )
    flame = (
        _sample_cubic((0.0, 1.6), (-2.6, -1.2), (-2.2, -4.6), (0.4, -8.2))[:-1]
        + _sample_cubic((0.4, -8.2), (2.6, -4.8), (2.4, -1.4), (0.0, 1.6))[:-1]
    )
    return wings, flame


def _point_in_poly(x: float, y: float, poly: list[tuple[float, float]]) -> bool:
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if ((y1 > y) != (y2 > y)) and (x < (x2 - x1) * (y - y1) / (y2 - y1 + 1e-9) + x1):
            inside = not inside
    return inside


def _dist_to_polyline(x: float, y: float, poly: list[tuple[float, float]]) -> float:
    best = 1e9
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        dx = x2 - x1
        dy = y2 - y1
        denom = dx * dx + dy * dy
        t = 0.0 if denom == 0.0 else max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / denom))
        px = x1 + t * dx
        py = y1 + t * dy
        d = math.hypot(x - px, y - py)
        if d < best:
            best = d
    return best


def _render_icon(size: int, *, private: bool = False) -> bytes:
    rgba = bytearray(size * size * 4)
    wings, flame = _build_mark_contours()

    bg_top = (40, 31, 51) if private else (38, 37, 35)
    bg_bot = (15, 11, 20) if private else (20, 19, 18)
    fg_top = (201, 181, 255) if private else (255, 138, 76)
    fg_bot = (91, 56, 184) if private else (179, 67, 15)

    # Stroke width in mark units (~22x18.4 box) scaled slightly thicker at tiny sizes
    half_stroke = 0.78 if size >= 48 else 1.05
    offsets = (0.25, 0.75)

    for py in range(size):
        for px in range(size):
            r_acc = g_acc = b_acc = a_acc = 0.0
            for oy in offsets:
                for ox in offsets:
                    u = (px + ox) / size
                    v = (py + oy) / size
                    sdf = _rounded_rect_sdf(u - 0.5, v - 0.5, 0.44, 0.19)
                    if sdf > 0.0:
                        continue

                    t = (u + v) * 0.5
                    r = _lerp(bg_top[0], bg_bot[0], t)
                    g = _lerp(bg_top[1], bg_bot[1], t)
                    b = _lerp(bg_top[2], bg_bot[2], t)

                    if -0.015 < sdf <= 0.0:
                        r = _lerp(r, 241.0, 0.12)
                        g = _lerp(g, 239.0, 0.12)
                        b = _lerp(b, 237.0, 0.12)

                    # Transform pixel [0,1] into mark coordinate space (centered at (128, 130)/256, scale 7.8/256)
                    mx = (u * 256.0 - 128.0) / 7.8
                    my = (v * 256.0 - 130.0) / 7.8

                    grad_t = max(0.0, min(1.0, ((mx + 10.0) / 20.0 + (my + 8.2) / 16.4) * 0.5))
                    fr = _lerp(fg_top[0], fg_bot[0], grad_t)
                    fg = _lerp(fg_top[1], fg_bot[1], grad_t)
                    fb = _lerp(fg_top[2], fg_bot[2], grad_t)

                    in_wings = _point_in_poly(mx, my, wings)
                    in_flame = _point_in_poly(mx, my, flame)
                    if in_wings or in_flame:
                        alpha = 0.24 if in_flame else 0.18
                        r = _lerp(r, fr, alpha)
                        g = _lerp(g, fg, alpha)
                        b = _lerp(b, fb, alpha)

                    d_mark = min(_dist_to_polyline(mx, my, wings), _dist_to_polyline(mx, my, flame))
                    if d_mark <= half_stroke:
                        r, g, b = fr, fg, fb

                    r_acc += r
                    g_acc += g
                    b_acc += b
                    a_acc += 255.0

            idx = (py * size + px) * 4
            if a_acc > 0:
                samples = a_acc / 255.0
                rgba[idx] = int(round(r_acc / samples))
                rgba[idx + 1] = int(round(g_acc / samples))
                rgba[idx + 2] = int(round(b_acc / samples))
                rgba[idx + 3] = int(round(a_acc / 4.0))

    return _encode_png(size, size, rgba)


def _encode_ico(png_images: list[tuple[int, bytes]]) -> bytes:
    header = struct.pack("<HHH", 0, 1, len(png_images))
    entries = bytearray()
    offset = 6 + 16 * len(png_images)
    payloads = bytearray()
    for size, data in png_images:
        dim = 0 if size >= 256 else size
        entries.extend(struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset))
        payloads.extend(data)
        offset += len(data)
    return header + bytes(entries) + bytes(payloads)


def main() -> None:
    BRANDING_DIR.mkdir(parents=True, exist_ok=True)
    (BRANDING_DIR / "hellbird.svg").write_text(SVG_NORMAL, encoding="utf-8", newline="\n")
    (BRANDING_DIR / "hellbird-private.svg").write_text(SVG_PRIVATE, encoding="utf-8", newline="\n")
    (BRANDING_DIR / "hellbird.rc").write_text(WINDOWS_RC, encoding="utf-8", newline="\n")

    png_48 = _render_icon(48, private=False)
    png_256 = _render_icon(256, private=False)
    png_private_256 = _render_icon(256, private=True)

    (BRANDING_DIR / "hellbird-48.png").write_bytes(png_48)
    (BRANDING_DIR / "hellbird-256.png").write_bytes(png_256)
    (BRANDING_DIR / "hellbird-private-256.png").write_bytes(png_private_256)

    ico_frames = [
        (16, _render_icon(16, private=False)),
        (32, _render_icon(32, private=False)),
        (48, png_48),
        (256, png_256),
    ]
    (BRANDING_DIR / "hellbird.ico").write_bytes(_encode_ico(ico_frames))
    print(f"Generated unified Hellbird branding assets in {BRANDING_DIR}")


if __name__ == "__main__":
    main()
