#!/usr/bin/env python3
"""
h3_erp.py — a plate placed on a green 2:1 equirectangular canvas (numpy, PIL).

The FLUX.2 Klein 9B 360 ERP outpaint LoRA (nomadoor) fills the green of such a
canvas into a whole 360 panorama, keeping what is placed on it. Its training
pairs were made by projecting 1-3 picture patches onto a green ERP canvas with
a pinhole camera model; this does the same for one plate, looking straight
ahead (yaw 0, pitch 0) with a horizontal field of view `--fov`. Every canvas
pixel whose ray falls inside the plate's frustum takes the plate's colour
(bilinear), the rest is pure green.

The pipeline is stdlib only, so h3refs runs this as a subprocess of the
running Python (ComfyUI's under the editor), as h3_refsheet.py is run:

    python h3_erp.py PLATE OUT [--width 2048] [--fov 70] [--yaw 0] [--pitch 0]
"""
from __future__ import annotations

import argparse
import math
import sys

GREEN = (0, 255, 0)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("plate")
    ap.add_argument("out")
    ap.add_argument("--width", type=int, default=2048)
    ap.add_argument("--fov", type=float, default=70.0, help="the plate's horizontal field of view")
    ap.add_argument("--yaw", type=float, default=0.0)
    ap.add_argument("--pitch", type=float, default=0.0)
    a = ap.parse_args(argv)
    try:
        import numpy as np
        from PIL import Image
    except ImportError as e:
        print(f"placing a plate on a 360 canvas needs numpy and PIL ({e}): run it from "
              f"ComfyUI's python", file=sys.stderr)
        return 3
    plate = np.asarray(Image.open(a.plate).convert("RGB")).astype(np.float32)
    ph, pw = plate.shape[:2]
    W, H = a.width, a.width // 2
    f = (pw / 2) / math.tan(math.radians(a.fov) / 2)
    lon = (np.arange(W) + 0.5) / W * 2 * math.pi - math.pi
    lat = math.pi / 2 - (np.arange(H) + 0.5) / H * math.pi
    lon, lat = np.meshgrid(lon, lat)
    # the world ray, then into the camera: undo yaw (about Y), then pitch (about X)
    x, y, z = np.cos(lat) * np.sin(lon), np.sin(lat), np.cos(lat) * np.cos(lon)
    yw, pt = math.radians(a.yaw), math.radians(a.pitch)
    x, z = x * math.cos(yw) - z * math.sin(yw), x * math.sin(yw) + z * math.cos(yw)
    y, z = y * math.cos(pt) - z * math.sin(pt), y * math.sin(pt) + z * math.cos(pt)
    front = z > 1e-6
    zs = np.where(front, z, 1.0)
    u = f * x / zs + pw / 2 - 0.5
    v = -f * y / zs + ph / 2 - 0.5
    inside = front & (u >= 0) & (u <= pw - 1) & (v >= 0) & (v <= ph - 1)
    u0 = np.clip(np.floor(u).astype(int), 0, pw - 1)
    v0 = np.clip(np.floor(v).astype(int), 0, ph - 1)
    u1, v1 = np.clip(u0 + 1, 0, pw - 1), np.clip(v0 + 1, 0, ph - 1)
    fu, fv = (u - u0)[..., None], (v - v0)[..., None]
    img = (plate[v0, u0] * (1 - fu) * (1 - fv) + plate[v0, u1] * fu * (1 - fv)
           + plate[v1, u0] * (1 - fu) * fv + plate[v1, u1] * fu * fv)
    out = np.empty((H, W, 3), np.float32)
    out[:] = GREEN
    out[inside] = img[inside]
    Image.fromarray(np.clip(out, 0, 255).astype(np.uint8)).save(a.out)
    print(a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
