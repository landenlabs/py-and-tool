#!/usr/bin/env python3
# ----------------------------------------------------------------------
# Copyright (c) 2026 LanDen Labs - Dennis Lang
# https://landenlabs.com
# ----------------------------------------------------------------------
"""make_icons.py - regenerate icon.icns and icon.ico from icon.png.

Run whenever icon.png changes, then commit all three files together.
Uses Pillow, which writes both ICO and ICNS in pure Python, so the output
is identical whether this runs on macOS, Windows, or Linux -- shared by
make-icons.bash and make-icons.ps1, so the two never drift apart.

Usage:
    python3 make_icons.py [path/to/icon.png]
"""

import os
import sys

try:
    from PIL import Image
except ImportError:
    print("Error: Pillow is required.  Install: pip install Pillow", file=sys.stderr)
    sys.exit(1)

ICO_SIZES = [16, 32, 48, 64, 128, 256]


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else "icon.png"
    if not os.path.isfile(src):
        print("Error: source image not found: %s" % src, file=sys.stderr)
        sys.exit(1)

    root = os.path.splitext(src)[0]
    ico_path = root + ".ico"
    icns_path = root + ".icns"

    im = Image.open(src).convert("RGBA")
    if im.width != im.height:
        print("Warning: %s is %dx%d, not square -- icons may look stretched."
              % (src, im.width, im.height))

    im.save(ico_path, sizes=[(s, s) for s in ICO_SIZES])
    print("Wrote %s  (%s)"
          % (ico_path, ", ".join("%dx%d" % (s, s) for s in ICO_SIZES)))

    im.save(icns_path)
    print("Wrote %s" % icns_path)


if __name__ == "__main__":
    main()
