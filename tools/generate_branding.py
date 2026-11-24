#!/usr/bin/env python3
"""Generate Hellbird vector SVG, PNG, ICO, and Windows RC branding assets without external deps."""

from __future__ import annotations

import math
from pathlib import Path
import struct
import zlib

BRANDING_DIR = Path(__file__).resolve().parent.parent / "resources" / "branding"

SVG_NORMAL = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" fill="none">
  <defs>
    <linearGradient id="bg" x1="24" y1="20" x2="232" y2="236" gradientUnits="userSpaceOnUse">
      <stop offset="0%" stop-color="#1E1B20"/>
      <stop offset="100%" stop-color="#111014"/>
    </linearGradient>
    <linearGradient id="ember" x1="56" y1="54" x2="206" y2="204" gradientUnits="userSpaceOnUse">
      <stop offset="0%" stop-color="#FF7A45"/>
      <stop offset="55%" stop-color="#E84A27"/>
      <stop offset="100%" stop-color="#B8291A"/>
    </linearGradient>
  </defs>
  <rect x="16" y="16" width="224" height="224" rx="54" fill="url(#bg)"/>
  <rect x="17.5" y="17.5" width="221" height="221" rx="52.5" stroke="#FFFFFF" stroke-opacity="0.10" stroke-width="3"/>
  <path d="M62 174 C76 116, 114 78, 186 68 C164 96, 154 114, 152 132 C170 120, 186 118, 202 122 C176 154, 144 178, 98 188 C84 191, 70 185, 62 174 Z" fill="url(#ember)"/>
  <path d="M96 164 C110 132, 134 108, 174 96 C158 118, 150 136, 148 152 C132 162, 114 167, 96 164 Z" fill="#FFF3EC" fill-opacity="0.92"/>
  <circle cx="154" cy="94" r="6" fill="#161418"/>
</svg>
"""

SVG_PRIVATE = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" fill="none">
  <defs>
    <linearGradient id="bgPriv" x1="24" y1="20" x2="232" y2="236" gradientUnits="userSpaceOnUse">
      <stop offset="0%" stop-color="#1E182B"/>
      <stop offset="100%" stop-color="#100D18"/>
    </linearGradient>
    <linearGradient id="violet" x1="56" y1="54" x2="206" y2="204" gradientUnits="userSpaceOnUse">
      <stop offset="0%" stop-color="#B794F6"/>
      <stop offset="55%" stop-color="#7C4DFF"/>
      <stop offset="100%" stop-color="#4A27B8"/>
    </linearGradient>
  </defs>
  <rect x="16" y="16" width="224" height="224" rx="54" fill="url(#bgPriv)"/>
  <rect x="17.5" y="17.5" width="221" height="221" rx="52.5" stroke="#FFFFFF" stroke-opacity="0.12" stroke-width="3"/>
  <path d="M62 174 C76 116, 114 78, 186 68 C164 96, 154 114, 152 132 C170 120, 186 118, 202 122 C176 154, 144 178, 98 188 C84 191, 70 185, 62 174 Z" fill="url(#violet)"/>
  <path d="M96 164 C110 132, 134 108, 174 96 C158 118, 150 136, 148 152 C132 162, 114 167, 96 164 Z" fill="#F4EEFF" fill-opacity="0.92"/>
  <circle cx="154" cy="94" r="6" fill="#14101E"/>
</svg>
"""

WINDOWS_RC = """IDI_ICON1 ICON DISCARDABLE "hellbird.ico"
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


def _point_in_poly(x: float, y: float, poly: list[tuple[float, float]]) -> bool:
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if ((y1 > y) != (y2 > y)) and (x < (x2 - x1) * (y - y1) / (y2 - y1 + 1e-9) + x1):
            inside = not inside
    return inside


def _render_icon(size: int, *, private: bool = False) -> bytes:
    rgba = bytearray(size * size * 4)

    # Stylized phoenix-wing polygons normalized in [0, 1]
    outer_wing = [
        (0.24, 0.68),
        (0.31, 0.50),
        (0.45, 0.35),
        (0.73, 0.26),
        (0.63, 0.40),
        (0.59, 0.51),
        (0.70, 0.46),
        (0.79, 0.48),
        (0.67, 0.62),
        (0.54, 0.71),
        (0.38, 0.74),
        (0.29, 0.72),
    ]
    inner_feather = [
        (0.37, 0.64),
        (0.45, 0.50),
        (0.56, 0.41),
        (0.68, 0.37),
        (0.61, 0.48),
        (0.57, 0.59),
        (0.47, 0.64),
    ]

    bg_top = (30, 24, 43) if private else (30, 27, 32)
    bg_bot = (16, 13, 24) if private else (17, 16, 20)
    fg_top = (183, 148, 246) if private else (255, 122, 69)
    fg_bot = (74, 39, 184) if private else (184, 41, 26)
    hi_col = (244, 238, 255) if private else (255, 243, 236)

    # 2x2 supersampling for clean anti-aliased edges
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

                    # Subtle top-left rim highlight
                    if -0.015 < sdf <= 0.0:
                        r = _lerp(r, 255.0, 0.12)
                        g = _lerp(g, 255.0, 0.12)
                        b = _lerp(b, 255.0, 0.12)

                    if _point_in_poly(u, v, outer_wing):
                        r = _lerp(fg_top[0], fg_bot[0], t)
                        g = _lerp(fg_top[1], fg_bot[1], t)
                        b = _lerp(fg_top[2], fg_bot[2], t)

                    if _point_in_poly(u, v, inner_feather):
                        r = _lerp(r, hi_col[0], 0.90)
                        g = _lerp(g, hi_col[1], 0.90)
                        b = _lerp(b, hi_col[2], 0.90)

                    # Eye dot
                    if math.hypot(u - 0.60, v - 0.37) < 0.024:
                        r, g, b = bg_bot

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
    print(f"Generated Hellbird branding assets in {BRANDING_DIR}")


if __name__ == "__main__":
    main()
