#!/usr/bin/env python3
"""The app's icon files from port/app/icon/mh4u.svg: mh4u.ico (Windows), mh4u.icns (macOS),
mh4u.png and the pixels of icon_data.h (SDL_SetWindowIcon).

usage: python3 tools/icon.py           # render them all (needs cairosvg and Pillow)
       python3 tools/icon.py --check   # the files on disk agree with each other (no deps)

The emblem is the port's own (a hexagonal badge with three claw marks): no artwork of the
game's goes in here.
"""
from __future__ import annotations

import argparse
import io
import os
import re
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ICON_DIR = os.path.join(ROOT, "port", "app", "icon")
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
ICNS_SIZES = (16, 32, 64, 128, 256, 512, 1024)
WINDOW_ICON_SIZE = 64


def ico_sizes(data: bytes) -> list[int]:
    """The widths of the images in an .ico (0 in the directory means 256)."""
    if len(data) < 6:
        raise ValueError("not an .ico: too short")
    reserved, kind, count = struct.unpack_from("<HHH", data, 0)
    if reserved != 0 or kind != 1:
        raise ValueError("not an .ico")
    sizes = []
    for i in range(count):
        width = data[6 + i * 16]
        sizes.append(width or 256)
    return sizes


def header_pixels(text: str) -> bytes:
    """The RGBA bytes of icon_data.h."""
    m = re.search(r"kPixels\[[^\]]*\] = \{(.*?)\};", text, re.S)
    if not m:
        raise ValueError("no kPixels in the header")
    return bytes(int(v, 16) for v in re.findall(r"0x([0-9a-fA-F]{2})", m.group(1)))


def write_header(pixels: bytes, size: int) -> str:
    rows = []
    for i in range(0, len(pixels), 16):
        rows.append("    " + ", ".join(f"0x{b:02x}" for b in pixels[i:i + 16]) + ",")
    return (
        "// The app's icon, {0}x{0} RGBA pixels rendered from icon/mh4u.svg (tools/icon.py), for\n"
        "// SDL_SetWindowIcon on the systems whose windows don't take it from the executable. Generated:\n"
        "// don't edit; run tools/icon.py after changing the SVG.\n"
        "#pragma once\n\n#include <cstdint>\n\nnamespace App::Icon {{\n\n"
        "constexpr int kSize = {0};\nconstexpr unsigned char kPixels[kSize * kSize * 4] = {{\n{1}\n}};\n\n"
        "}} // namespace App::Icon\n"
    ).format(size, "\n".join(rows))


def render(icon_dir: str) -> None:
    import cairosvg  # type: ignore
    from PIL import Image  # type: ignore

    svg = os.path.join(icon_dir, "mh4u.svg")

    def png(size: int):
        buf = io.BytesIO()
        cairosvg.svg2png(url=svg, write_to=buf, output_width=size, output_height=size)
        buf.seek(0)
        return Image.open(buf).convert("RGBA")

    images = {s: png(s) for s in sorted(set(ICO_SIZES) | set(ICNS_SIZES) | {WINDOW_ICON_SIZE})}
    images[256].save(os.path.join(icon_dir, "mh4u.ico"), format="ICO", sizes=[(s, s) for s in ICO_SIZES],
                     append_images=[images[s] for s in ICO_SIZES if s != 256])
    images[1024].save(os.path.join(icon_dir, "mh4u.icns"), format="ICNS",
                      append_images=[images[s] for s in ICNS_SIZES if s != 1024])
    images[256].save(os.path.join(icon_dir, "mh4u.png"))
    with open(os.path.join(icon_dir, "icon_data.h"), "w") as f:
        f.write(write_header(images[WINDOW_ICON_SIZE].tobytes(), WINDOW_ICON_SIZE))


def check(icon_dir: str) -> list[str]:
    problems = []
    with open(os.path.join(icon_dir, "mh4u.ico"), "rb") as f:
        sizes = ico_sizes(f.read())
    if sorted(sizes) != sorted(ICO_SIZES):
        problems.append(f"mh4u.ico has sizes {sorted(sizes)}, wanted {sorted(ICO_SIZES)}")
    with open(os.path.join(icon_dir, "icon_data.h")) as f:
        pixels = header_pixels(f.read())
    if len(pixels) != WINDOW_ICON_SIZE * WINDOW_ICON_SIZE * 4:
        problems.append(f"icon_data.h has {len(pixels)} bytes, wanted {WINDOW_ICON_SIZE * WINDOW_ICON_SIZE * 4}")
    elif not any(pixels[3::4]):
        problems.append("icon_data.h is fully transparent")
    with open(os.path.join(icon_dir, "mh4u.icns"), "rb") as f:
        if f.read(4) != b"icns":
            problems.append("mh4u.icns isn't an icns file")
    with open(os.path.join(icon_dir, "mh4u.png"), "rb") as f:
        if f.read(8) != b"\x89PNG\r\n\x1a\n":
            problems.append("mh4u.png isn't a PNG")
    return problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--dir", default=ICON_DIR, help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    if args.check:
        problems = check(args.dir)
        for p in problems:
            print(p, file=sys.stderr)
        return 1 if problems else 0
    render(args.dir)
    print("rendered", ", ".join(("mh4u.ico", "mh4u.icns", "mh4u.png", "icon_data.h")), "in", args.dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
