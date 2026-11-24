#!/usr/bin/env python3
"""Rebuild Hellbird SVG wrappers, multi-size Windows ICO, and Windows RC from branding PNGs."""

from __future__ import annotations

import base64
from pathlib import Path
import struct

BRANDING_DIR = Path(__file__).resolve().parent.parent / "resources" / "branding"

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


def _wrap_png_as_svg(png_bytes: bytes) -> str:
    encoded = base64.b64encode(png_bytes).decode("ascii")
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" width="256" height="256">\n'
        f'  <image width="256" height="256" href="data:image/png;base64,{encoded}" />\n'
        "</svg>\n"
    )


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
    png_256 = (BRANDING_DIR / "hellbird-256.png").read_bytes()
    png_private_256 = (BRANDING_DIR / "hellbird-private-256.png").read_bytes()

    (BRANDING_DIR / "hellbird.svg").write_text(_wrap_png_as_svg(png_256), encoding="utf-8", newline="\n")
    (BRANDING_DIR / "hellbird-private.svg").write_text(
        _wrap_png_as_svg(png_private_256), encoding="utf-8", newline="\n"
    )
    (BRANDING_DIR / "hellbird.rc").write_text(WINDOWS_RC, encoding="utf-8", newline="\n")
    print(f"Updated Hellbird SVG wrappers and RC in {BRANDING_DIR}")


if __name__ == "__main__":
    main()
