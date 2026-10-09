#!/usr/bin/env python3
"""
h3_stills.py — the held moments of a camera-tour video, as plates (numpy, PIL).

A tour (h3tour.py) is one H3 video that starts on a good frame of a place and
swings the camera from view to view, stopping on each; the video model keeps
the place the same the whole way round, which separately drawn plates cannot.
This finds the runs of frames where the camera holds still and saves the
sharpest frame of each. Ported from h3sets' stills.py.

The pipeline is stdlib only, so h3tour runs this file as a subprocess of the
running Python -- ComfyUI's under the editor's routes, which has numpy and
PIL -- as h3_refsheet.py is run:

    python h3_stills.py VIDEO OUT_DIR FFMPEG FFPROBE [--still X] [--min-hold S]

writes OUT_DIR/hold_NN.png and prints one JSON object on stdout:
{"fps", "width", "height", "still", "holds": [{"hold", "start", "end",
"frame", "sharpness", "path"}]}. The opening frame is always hold 1 (it is
the frame the tour started from).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys


def probe(ffprobe: str, path: str) -> tuple[int, int, float]:
    out = subprocess.run([ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height,r_frame_rate", "-of", "json", path],
                         capture_output=True, text=True, check=True).stdout
    st = json.loads(out)["streams"][0]
    num, den = st["r_frame_rate"].split("/")
    return int(st["width"]), int(st["height"]), float(num) / float(den)


def frames(ffmpeg: str, path: str, w: int, h: int):
    import numpy as np
    p = subprocess.Popen([ffmpeg, "-v", "error", "-i", path, "-f", "rawvideo", "-pix_fmt",
                          "rgb24", "-"], stdout=subprocess.PIPE)
    size = w * h * 3
    try:
        while True:
            buf = p.stdout.read(size)
            if len(buf) < size:
                break
            yield np.frombuffer(buf, np.uint8).reshape(h, w, 3)
    finally:
        p.stdout.close()
        p.wait()


def sharpness(f) -> float:
    """Variance of a Laplacian: high for crisp detail, low for motion blur."""
    g = f.astype("float32").mean(axis=2)
    lap = (-4 * g[1:-1, 1:-1] + g[:-2, 1:-1] + g[2:, 1:-1] + g[1:-1, :-2] + g[1:-1, 2:])
    return float(lap.var())


def holds(motion, still: float, min_len: int) -> list[tuple[int, int]]:
    """Runs of frames whose motion is under `still`, at least `min_len` long."""
    out, start = [], None
    for i, m in enumerate(list(motion) + [1e9]):
        if m < still and start is None:
            start = i
        elif m >= still and start is not None:
            if i - start >= min_len:
                out.append((start, i))
            start = None
    return out


def turning_points(motion, fps: float, taken: list[tuple[int, int]],
                   window: float = 0.5, depth: float = 0.35) -> list[tuple[int, int]]:
    """Moments the camera all but stops without holding: where a pan reverses
    or settles, motion dips for a few frames, too briefly for `holds`. A frame
    whose (3-frame smoothed) motion is the least within `window` seconds either
    side and under `depth` x the most there, outside any hold already found,
    gives a short run round it. H3 often turns straight back from "turn left
    and hold", so these are the views a tour actually reaches."""
    n = len(motion)
    sm = [sum(motion[max(0, i - 1):i + 2]) / len(motion[max(0, i - 1):i + 2]) for i in range(n)]
    w = max(2, round(window * fps))
    inside = lambda i: any(s - w <= i < e + w for s, e in taken)
    out = []
    for i in range(w, n - w):
        near = sm[i - w:i + w + 1]
        if sm[i] == min(near) and sm[i] < depth * max(near) and not inside(i):
            if not out or i - out[-1][1] > w:
                out.append((max(0, i - 1), min(n, i + 2)))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("out")
    ap.add_argument("ffmpeg")
    ap.add_argument("ffprobe")
    ap.add_argument("--still", type=float, default=None,
                    help="motion below this counts as held (default: half the median motion)")
    ap.add_argument("--min-hold", type=float, default=0.25, help="shortest hold, seconds")
    a = ap.parse_args(argv)
    try:
        import numpy as np
        from PIL import Image
    except ImportError as e:
        print(f"finding a tour's holds needs numpy and PIL ({e}): run it from ComfyUI's "
              f"python", file=sys.stderr)
        return 3
    w, h, fps = probe(a.ffprobe, a.video)
    os.makedirs(a.out, exist_ok=True)
    motion, sharp, prev = [], [], None
    for f in frames(a.ffmpeg, a.video, w, h):
        g = f[::4, ::4].astype(np.float32).mean(axis=2)
        motion.append(0.0 if prev is None else float(np.abs(g - prev).mean()))
        sharp.append(sharpness(f[::2, ::2]))
        prev = g
    if not motion:
        print("the video has no frames", file=sys.stderr)
        return 2
    motion[0] = motion[1] if len(motion) > 1 else 0.0
    m = np.array(motion)
    still = a.still if a.still is not None else float(np.median(m)) / 2
    found = holds(m, still, max(2, round(a.min_hold * fps)))
    if not found or found[0][0] > 0:
        found.insert(0, (0, 1))
    found = sorted(found + turning_points(list(m), fps, found))
    best = [max(range(s, e), key=lambda i: sharp[i]) for s, e in found]
    want = {i: k for k, i in enumerate(best, 1)}
    for i, f in enumerate(frames(a.ffmpeg, a.video, w, h)):
        if i in want:
            Image.fromarray(f).save(os.path.join(a.out, f"hold_{want[i]:02d}.png"))
    out = {"fps": fps, "width": w, "height": h, "still": still, "holds": [
        {"hold": k, "start": s / fps, "end": e / fps, "frame": i, "sharpness": sharp[i],
         "path": os.path.join(a.out, f"hold_{k:02d}.png")}
        for k, ((s, e), i) in enumerate(zip(found, best), 1)]}
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
