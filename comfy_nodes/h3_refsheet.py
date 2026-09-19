#!/usr/bin/env python3
"""
h3_refsheet.py — compose an LTX-2.3 ingredients reference sheet (PIL).

The ltx2_ingredients target (targets/video/ltx2_ingredients) conditions a shot
on ONE composite image: a clean panel per element (characters, then props and
vehicles, then the location plate), on black, no text, at the render size. The
pipeline is stdlib only, so it runs this file as a subprocess of the running
Python (ComfyUI's, under the routes; the CLI's otherwise), which needs PIL:

    python h3_refsheet.py OUT.png < spec.json

    spec = {"width": 768, "height": 448, "background": "black", "gap": 0.02,
            "panels": [{"path": "C:/.../dean_sheet_4panel.png",
                        "crop": {"panels": 4, "index": 0},   # one panel of a strip
                        "fit": "figure"},
                       {"path": "C:/.../kitchen.png", "fit": "cover"}]}

Layout. The panels tile the whole sheet, in order (left to right, top to
bottom), in rows: every cell of a row is as tall as the row, and the rows fill
the sheet's height and width, separated by thin black `gap` lines (a fraction
of the short side) and no border. The sheet has no empty bands: the IC-LoRA
reads the reference at the output's size and position, and a sheet framed by
black bars came back as a letterboxed shot.

`background` is "black" (the ingredients sheet) or "white" (the wan22_vace
target's reference image: VACE's references are subjects on white); the gap
lines and a figure's empty sides are that colour.

Each image is fitted to its cell by its `fit`:
  cover   (the plate) cropped to the cell, centred: the whole cell is picture
  figure  (a character's view, a prop) cropped at the sides to the cell, but
          never to less than `min_keep` (0.6) of its width, so a standing
          figure keeps its head, feet and arms; what is left of the cell is black

Of every way to split the panels into rows, the one that leaves the least
black and crops the least wins (fewer rows on a tie).

A spec with "mode": "composite" is something else: the ONE reference image a
single-reference edit target (flux_kontext) gets for a keyframe, the shot's
figures pasted side by side over the location plate, bottom-aligned, about
two thirds of the frame tall (`composite`, `composite_layout`).

`layout` and `composite_layout` are pure Python and `compose` needs PIL; all
are importable (tests).
Not a ComfyUI node: nothing here is registered, so adding it needs no restart.
"""
from __future__ import annotations

import json
import os
import sys

# black: the LTX ingredients sheet; white: the Wan VACE reference image
# (VACE pads its references on white)
BACKGROUNDS = {"black": (0, 0, 0), "white": (255, 255, 255)}
MIN_KEEP = 0.6            # a figure keeps at least this much of its width
CROP_COST = 0.5           # a cropped pixel costs half a black one


def _partitions(n: int):
    """Every way to cut 0..n-1 into contiguous rows, as lists of row lengths."""
    if n == 0:
        yield []
        return
    for first in range(1, n + 1):
        for rest in _partitions(n - first):
            yield [first] + rest


def fit(aspect: float, cw: float, ch: float, mode: str,
        min_keep: float = MIN_KEEP) -> tuple[tuple[float, float, float, float],
                                             tuple[float, float, float, float]]:
    """(crop, box) for an image of `aspect` (w / h) in a cw x ch cell: `crop`
    is (left, top, right, bottom) as fractions of the image, `box` is (x, y,
    w, h) inside the cell."""
    ca = cw / ch
    if mode == "cover":
        if ca < aspect:                                  # cell narrower: crop the sides
            k = ca / aspect
            return ((1 - k) / 2, 0.0, (1 + k) / 2, 1.0), (0.0, 0.0, cw, ch)
        k = aspect / ca                                  # cell wider: crop top and bottom
        return (0.0, (1 - k) / 2, 1.0, (1 + k) / 2), (0.0, 0.0, cw, ch)
    if ca >= aspect:                                     # as tall as the cell, black sides
        w = ch * aspect
        return (0.0, 0.0, 1.0, 1.0), ((cw - w) / 2, 0.0, w, ch)
    k = max(ca / aspect, min_keep)                       # crop the sides, within reason
    a2 = aspect * k
    h = cw / a2
    return ((1 - k) / 2, 0.0, (1 + k) / 2, 1.0), (0.0, (ch - h) / 2, cw, h)


def _cells(aspects: list[float], groups: list[int], width: int, height: int, gap: int,
           across: bool) -> list[tuple[float, float, float, float]] | None:
    """The cells of one tiling, in panel order. `across`: `groups` are rows
    (justified at the sheet's width, then stretched to its height); else they
    are columns (justified at its height, stretched to its width). None when
    the gaps leave no room."""
    long_, short = (width, height) if across else (height, width)
    room = short - gap * (len(groups) - 1)
    if room <= 0:
        return None
    # along a row, a cell's extent is its aspect times the row's height;
    # down a column, its height is the column's width over its aspect
    ext = [a if across else 1 / a for a in aspects]
    i, natural = 0, []
    for k in groups:
        if long_ - gap * (k - 1) <= 0:
            return None
        natural.append((long_ - gap * (k - 1)) / sum(ext[i:i + k]))
        i += k
    f = room / sum(natural)
    out, pos, i = [], 0.0, 0
    for k, nat in zip(groups, natural):
        thick, along = nat * f, 0.0
        for j in range(i, i + k):
            size = ext[j] * nat
            out.append((along, pos, size, thick) if across else (pos, along, thick, size))
            along += size + gap
        pos += thick + gap
        i += k
    return out


def layout(aspects: list[float], fits: list[str], width: int, height: int,
           gap: int = 0) -> list[dict]:
    """Where each panel goes: [{"cell": (x, y, w, h), "crop": (l, t, r, b)
    fractions of the image, "box": (x, y, w, h) on the sheet, integers}], in
    the panels' order. Pure Python."""
    n = len(aspects)
    if n == 0:
        return []
    total = float(width * height)
    best = None
    for rows in _partitions(n):
        for across in (True, False):
            if not across and len(rows) == 1 and n == 1:
                continue                                 # the same as one row
            cells = _cells(aspects, rows, width, height, gap, across)
            if cells is None:
                continue
            placed, black, cropped = [], total, 0.0
            for j, (x, y, w, h) in enumerate(cells):
                crop, (bx, by, bw, bh) = fit(aspects[j], w, h, fits[j])
                kept = (crop[2] - crop[0]) * (crop[3] - crop[1])
                black -= bw * bh
                cropped += bw * bh * (1 - kept) / kept
                placed.append({"cell": (x, y, w, h), "crop": crop,
                               "box": (x + bx, y + by, bw, bh)})
            key = (round((black + CROP_COST * cropped) / total, 6), len(rows), not across)
            if best is None or key < best[0]:
                best = (key, placed)
    if best is None:
        raise ValueError(f"{n} panels don't fit a {width}x{height} sheet")
    out = []
    for p in best[1]:
        x, y, w, h = p["box"]
        x0, y0 = int(round(x)), int(round(y))
        out.append({"cell": tuple(round(v, 2) for v in p["cell"]), "crop": p["crop"],
                    "box": (x0, y0, max(1, int(round(x + w)) - x0),
                            max(1, int(round(y + h)) - y0))})
    return out


def _load(panel: dict, bg):
    from PIL import Image, ImageOps
    img = Image.open(panel["path"])
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        flat = Image.new("RGB", img.size, bg)
        flat.paste(img, mask=img.split()[-1])
        img = flat
    else:
        img = img.convert("RGB")
    crop = panel.get("crop")
    if crop and int(crop.get("panels", 1)) > 1:
        n, i = int(crop["panels"]), int(crop.get("index", 0))
        pw = img.width // n                      # as the H3 loader's crop_panels
        img = img.crop((i * pw, 0, (i + 1) * pw, img.height))
    return img


def composite_layout(aspects: list[float], width: int, height: int,
                     figure_height: float = 2 / 3, gap: float = 0.02,
                     margin: float = 0.03) -> list[tuple[int, int, int, int]]:
    """Where each figure of a composed reference goes: [(x, y, w, h)], side
    by side in order, centred, bottom-aligned on the frame's bottom edge,
    `figure_height` of the frame tall, `gap` (a fraction of the width)
    between them; scaled down together when they'd be wider than the frame
    less `margin` at each side. Pure Python."""
    if not aspects:
        return []
    h = figure_height * height
    g = gap * width
    room = width * (1 - 2 * margin)
    total = sum(a * h for a in aspects) + g * (len(aspects) - 1)
    if total > room:
        k = (room - g * (len(aspects) - 1)) / sum(a * h for a in aspects)
        h *= k
        total = room
    x = (width - total) / 2
    out = []
    for a in aspects:
        w = a * h
        x0, y0 = int(round(x)), int(round(height - h))
        out.append((x0, y0, max(1, int(round(x + w)) - x0), max(1, height - y0)))
        x += w + g
    return out


def composite(spec: dict, out: str) -> dict:
    """A single-reference edit target's one reference (h3refs.compose_composite):
    the plate covering the frame (or `background`), with the figures pasted
    over it by composite_layout. spec = {"mode": "composite", "width",
    "height", "background", "figure_height", "gap", "plate": {"path",
    "crop"?} | None, "figures": [{"path", "crop"?}]}. Returns {"width",
    "height", "boxes"} (the figures')."""
    from PIL import Image
    width, height = int(spec["width"]), int(spec["height"])
    bg = BACKGROUNDS.get(spec.get("background", "white"), (255, 255, 255))
    frame = Image.new("RGB", (width, height), bg)
    plate = spec.get("plate")
    if plate:
        im = _load(plate, bg)
        (l, t, r, b), _box = fit(im.width / im.height, width, height, "cover")
        src = im.crop((int(round(l * im.width)), int(round(t * im.height)),
                       int(round(r * im.width)), int(round(b * im.height))))
        frame.paste(src.resize((width, height), Image.LANCZOS), (0, 0))
    figures = [_load(f, bg) for f in spec.get("figures") or []]
    if not figures and not plate:
        raise ValueError("a composed reference needs a plate or a figure")
    boxes = composite_layout([im.width / im.height for im in figures], width, height,
                             float(spec.get("figure_height", 2 / 3)),
                             float(spec.get("gap", 0.02)))
    for im, (x, y, w, h) in zip(figures, boxes):
        frame.paste(im.resize((w, h), Image.LANCZOS), (x, y))
    os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
    frame.save(out, format="PNG")
    return {"width": width, "height": height, "boxes": [list(b) for b in boxes]}


def compose(spec: dict, out: str) -> dict:
    """Write the sheet `spec` describes to `out` (PNG). Returns a report:
    {"width", "height", "boxes": [[x, y, w, h], ...]} (where each panel's
    picture landed). A spec with "mode": "composite" is a composed reference
    instead (composite)."""
    if spec.get("mode") == "composite":
        return composite(spec, out)
    from PIL import Image
    width, height = int(spec["width"]), int(spec["height"])
    bg = BACKGROUNDS.get(spec.get("background", "black"), (0, 0, 0))
    panels = list(spec.get("panels") or [])
    if not panels:
        raise ValueError("a reference sheet needs at least one panel")
    images = [_load(p, bg) for p in panels]
    gap = int(round(float(spec.get("gap", 0.02)) * min(width, height)))
    places = layout([im.width / im.height for im in images],
                    [p.get("fit", "figure") for p in panels], width, height, gap)
    sheet = Image.new("RGB", (width, height), bg)
    for im, pl in zip(images, places):
        l, t, r, b = pl["crop"]
        src = im.crop((int(round(l * im.width)), int(round(t * im.height)),
                       int(round(r * im.width)), int(round(b * im.height))))
        x, y, w, h = pl["box"]
        sheet.paste(src.resize((w, h), Image.LANCZOS), (x, y))
    os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
    sheet.save(out, format="PNG")
    return {"width": width, "height": height, "boxes": [list(p["box"]) for p in places]}


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: python h3_refsheet.py OUT.png < spec.json", file=sys.stderr)
        return 2
    try:
        spec = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        print(json.dumps(compose(spec, argv[0])))
    except ModuleNotFoundError as e:                       # no PIL in this Python
        print(f"ModuleNotFoundError: {e}", file=sys.stderr)
        return 3
    except Exception as e:
        print(f"{e.__class__.__name__}: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
