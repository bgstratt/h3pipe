#!/usr/bin/env python3
"""
mksheet.py — stitch generated views into a correctly-sized H3 reference sheet.

Generate the four views however you like (any image model, any tool). This
assembles them into the one layout H3 actually wants: a single horizontal
strip, tall enough that `ref_image_size: max` keeps every panel sharp.

Why horizontal: `max` scales the SHORT side to 2048. In a horizontal strip the
short side is the height, so each panel lands near 2048px tall. A square 2x2
grid scales the whole canvas instead and halves your panel resolution.

    python3 mksheet.py front.png side.png back.png face.png -o refs/huey/huey_sheet_4panel.png
    python3 mksheet.py views/huey/*.png -o refs/huey/huey_sheet_4panel.png
    python3 mksheet.py existing_sheet.png -o out.png --check

Order matters and must match what the prompt claims:
    three-quarter body, side profile, back view, facial close-up
"""

from __future__ import annotations

import argparse
import os
import sys

from PIL import Image

TARGET_H = 1024          # per-panel height before H3's max-mode upscale
MIN_SHORT = 1024         # warn below this: max mode would be upscaling from mush
PANEL_NAMES = ["three-quarter body", "side profile", "back view", "facial close-up"]


def load_flat(path: str, bg: tuple[int, int, int]) -> Image.Image:
    """Open an image and flatten transparency onto a solid background."""
    img = Image.open(path)
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        plate = Image.new("RGB", img.size, bg)
        plate.paste(img, mask=img.split()[-1])
        return plate
    return img.convert("RGB")


def fit_height(img: Image.Image, height: int, bg: tuple[int, int, int]) -> Image.Image:
    """Scale to a common height without distorting aspect."""
    if img.height == height:
        return img
    w = max(1, round(img.width * height / img.height))
    return img.resize((w, height), Image.LANCZOS)


def build(paths: list[str], out: str, bg: tuple[int, int, int],
          gap: int, panel_h: int) -> dict:
    imgs = [load_flat(p, bg) for p in paths]
    scaled = [fit_height(i, panel_h, bg) for i in imgs]

    total_w = sum(i.width for i in scaled) + gap * (len(scaled) - 1)
    sheet = Image.new("RGB", (total_w, panel_h), bg)
    x = 0
    for i in scaled:
        sheet.paste(i, (x, 0))
        x += i.width + gap

    os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
    sheet.save(out)
    return {
        "panels": len(scaled),
        "size": sheet.size,
        "short_side": min(sheet.size),
        "aspect": sheet.width / sheet.height,
        "sources": [(os.path.basename(p), i.size) for p, i in zip(paths, imgs)],
    }


def inspect(path: str, panels: int) -> dict:
    img = Image.open(path)
    return {"panels": panels, "size": img.size, "short_side": min(img.size),
            "aspect": img.width / img.height, "sources": [(os.path.basename(path), img.size)]}


def report(info: dict, panels_expected: int) -> int:
    w, h = info["size"]
    print(f"\n  {w} x {h}   {info['panels']} panel(s)   aspect {info['aspect']:.2f}:1")
    for name, size in info["sources"]:
        print(f"    {name:34} {size[0]}x{size[1]}")

    print(f"\n  after H3 `max` (short side -> 2048):")
    scale = 2048 / info["short_side"]
    print(f"    sheet becomes {round(w * scale)} x 2048")
    if info["panels"]:
        print(f"    each panel    ~{round(w * scale / info['panels'])} x 2048")
    mpx = (w * scale) * 2048 / 1e6
    print(f"    reference cost ~{mpx:.1f} MP"
          + ("   (the `pair` crop halves this on multi-character shots)"
             if info["panels"] == 4 else ""))

    rc = 0
    if info["short_side"] < MIN_SHORT:
        print(f"\n  ! short side is {info['short_side']}px. max mode would upscale from "
              f"this, so detail is already lost. Regenerate the views at "
              f"{MIN_SHORT}px tall or more.")
        rc = 1
    if info["aspect"] < 1.0:
        print(f"\n  ! this is taller than it is wide, so H3 will scale the WIDTH to 2048 "
              f"and squash the panels. Reference sheets must be horizontal.")
        rc = 1
    if info["panels"] != panels_expected:
        print(f"\n  ! {info['panels']} panels but the prompt template describes "
              f"{panels_expected}. Keep them in sync or H3 is told the wrong thing.")
    print()
    return rc


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("images", nargs="+",
                    help="views in order: 3/4 body, side, back, face")
    ap.add_argument("-o", "--out", help="output sheet path")
    ap.add_argument("--check", action="store_true",
                    help="inspect an existing sheet instead of building one")
    ap.add_argument("--bg", default="255,255,255",
                    help="flat background for transparency, R,G,B (default white)")
    ap.add_argument("--gap", type=int, default=0,
                    help="pixels between panels (default 0)")
    ap.add_argument("--panel-height", type=int, default=TARGET_H,
                    help=f"common panel height before H3 upscales (default {TARGET_H})")
    args = ap.parse_args()

    try:
        bg = tuple(int(v) for v in args.bg.split(","))
        if len(bg) != 3:
            raise ValueError
    except ValueError:
        print("error: --bg must be three numbers like 255,255,255", file=sys.stderr)
        return 1

    missing = [p for p in args.images if not os.path.isfile(p)]
    if missing:
        for p in missing:
            print(f"error: no such file: {p}", file=sys.stderr)
        return 1

    if args.check:
        if len(args.images) != 1:
            print("error: --check takes exactly one sheet", file=sys.stderr)
            return 1
        return report(inspect(args.images[0], 4), 4)

    if not args.out:
        print("error: -o/--out is required when building", file=sys.stderr)
        return 1
    if len(args.images) != 4:
        print(f"note: building a {len(args.images)}-panel sheet. The prompt template "
              f"describes four ({', '.join(PANEL_NAMES)}) — adjust the bible's design "
              f"text if you mean something else.\n")

    info = build(args.images, args.out, bg, args.gap, args.panel_height)
    print(f"  wrote {args.out}")
    return report(info, 4)


if __name__ == "__main__":
    raise SystemExit(main())
