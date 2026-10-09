#!/usr/bin/env python3
"""
h3tour — a camera tour of a location on MiniMax H3, and its held frames as plates.

Start from a location's live plate, say where the camera goes and where it
stops, and H3's image-to-video model moves through the place keeping it the
same -- the same walls, signs and furniture -- which separately drawn plates
cannot. The video's held moments (comfy_nodes/h3_stills.py: motion under a
threshold, the sharpest frame of each run) become takes of the location's
`tour` pseudo-view (h3refs.TOUR_VIEW), never picked into the plate; one is
copied into the location or an angle of it as a plate candidate
(h3refs.copy_take). Ported from h3sets' tour.py and stills.py.

    python h3tour.py EPISODE location:ambulance_bay_night "The camera turns slowly to the left ... and holds"
        [--seconds 6] [--count 1] [--comfy URL]

The graph (workflows/h3_tour.json) is h3sets' H3 FL2VA image-to-video graph:
the fl2va model, the 4-step turbo LoRA, the first frame only, SaveVideo.
Frames soften as a clip runs, so keep the move early and short and the hold
right after it.

Stdlib only; the stills step runs h3_stills.py with the running Python (ComfyUI's
under the editor), which needs numpy and PIL, and ffmpeg on PATH.
"""
from __future__ import annotations

import argparse
import copy
import glob
import json
import os
import re
import subprocess
import sys
import zlib

import h3jobs as J
import h3refs as R
import h3takes as T

ROOT = os.path.dirname(os.path.abspath(__file__))
WORKFLOW = os.path.join(ROOT, "workflows", "h3_tour.json")
HELPER = os.path.join(ROOT, "comfy_nodes", "h3_stills.py")
TOURS = "_tours"                    # refs/_takes/<key>/_tours/tour_tNN.{json,mp4}, holds/
SECONDS = (2.0, 10.0)
DEFAULT_SECONDS = 6.0
# what every tour adds to the move: nothing moves but the camera, and it holds still
TAIL = (" Nothing in the place moves except the camera: no people, no animals, no traffic. "
        "While it holds, the camera is completely motionless, locked off on a tripod, the image "
        "sharp.")


class TourError(ValueError):
    pass


def tours_dir(ref: R.Ref) -> str:
    return os.path.join(R.takes_dir(ref), TOURS)


def _tour_file(ref: R.Ref, n: int, ext: str) -> str:
    return os.path.join(tours_dir(ref), f"tour_t{n:02d}{ext}")


def list_tours(ref: R.Ref) -> list[dict]:
    """Every tour of a location, oldest first: its sidecar."""
    out = []
    for p in sorted(glob.glob(os.path.join(tours_dir(ref), "tour_t*.json"))):
        d = T.read_json(p)
        if isinstance(d, dict):
            out.append(d)
    return sorted(out, key=lambda d: d.get("tour", 0))


def _next_number(ref: R.Ref) -> int:
    nums = [int(m.group(1)) for p in glob.glob(os.path.join(tours_dir(ref), "tour_t*.json"))
            if (m := re.search(r"tour_t(\d+)\.json$", p))]
    return max(nums, default=0) + 1


def tour_graph(base: dict, prompt: str, image: str, seconds: float, seed: int,
               prefix: str) -> dict:
    """The tour graph with this tour's frame, prompt, length and seed, found by
    node class (the graph's ids are a flattened subgraph's)."""
    g = copy.deepcopy(base)
    want = {"LoadImage": ("image", image), "MiniMaxH3ImageToVideo": ("prompt", prompt),
            "PrimitiveFloat": ("value", float(seconds)), "RandomNoise": ("noise_seed", seed),
            "SaveVideo": ("filename_prefix", prefix)}
    seen = set()
    for node in g.values():
        hit = want.get(node.get("class_type"))
        if hit:
            node["inputs"][hit[0]] = hit[1]
            seen.add(node["class_type"])
    missing = set(want) - seen
    if missing:
        raise TourError(f"{os.path.basename(WORKFLOW)} has no {', '.join(sorted(missing))}")
    return g


def check_request(ref: R.Ref, move: str, seconds: float, count: int) -> None:
    if ref.kind != "location":
        raise TourError(f"{ref.id} is not a location: a tour moves through a place")
    if not ref.file or not os.path.isfile(ref.file):
        raise TourError(f"{ref.id} has no live plate to start the tour from")
    if not isinstance(move, str) or not move.strip():
        raise TourError("a tour needs a move: where the camera goes and where it holds")
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) \
            or not SECONDS[0] <= seconds <= SECONDS[1]:
        raise TourError(f"seconds must be {SECONDS[0]:g} to {SECONDS[1]:g}")
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 4:
        raise TourError("count must be 1 to 4")


def start_tour(s: R.Series, ref: R.Ref, move: str, comfy, seconds: float = DEFAULT_SECONDS,
               count: int = 1, seed: int | None = None) -> list[dict]:
    """Queue `count` tours of location `ref` from its live plate; returns their
    sidecars (status queued, `comfy_prompt_id`)."""
    check_request(ref, move, seconds, count)
    with open(WORKFLOW, encoding="utf-8") as fh:
        base = json.load(fh)
    prompt = move.strip().rstrip(".") + "." + TAIL
    name = J.input_name(ref.file)
    comfy.upload_input(ref.file, name)
    os.makedirs(tours_dir(ref), exist_ok=True)
    out = []
    for c in range(count):
        n = _next_number(ref)
        sd = int(seed) if (c == 0 and seed is not None) else zlib.crc32(f"{ref.id}:tour:{n}".encode())
        side = {"tour": n, "ref": ref.id, "status": "queued", "queued": T.now(), "move": move.strip(),
                "prompt": prompt, "seconds": float(seconds), "seed": sd,
                "frame": R.ep_rel(s.ep, ref.file), "frame_sha1": T.file_sha1(ref.file),
                "comfy_prompt_id": None, "holds": [], "error": ""}
        T.write_json(_tour_file(ref, n, ".json"), side)
        g = tour_graph(base, prompt, name, seconds, sd, f"h3pipe/tours/{ref.key}_t{n:02d}")
        side["comfy_prompt_id"] = comfy.queue(g)
        T.write_json(_tour_file(ref, n, ".json"), side)
        out.append(side)
    return out


def _video_of(outputs: dict) -> dict | None:
    for out in (outputs or {}).values():
        for key in ("videos", "images", "gifs"):
            for f in out.get(key, []) or []:
                if str(f.get("filename", "")).lower().endswith((".mp4", ".webm", ".mov")):
                    return f
    return None


def find_holds(video: str, out_dir: str, python: str | None = None, timeout: int = 600) -> dict:
    """Run h3_stills.py on `video`: its JSON (holds written into out_dir)."""
    cmd = [python or sys.executable, HELPER, video, out_dir, R._ffmpeg("ffmpeg"),
           R._ffmpeg("ffprobe")]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise TourError((r.stderr or r.stdout or "finding the holds failed").strip()[-800:])
    return json.loads(r.stdout.strip().splitlines()[-1])


def finish_tour(s: R.Series, ref: R.Ref, side: dict, comfy, timeout: int = 1800,
                on_hold=None) -> dict:
    """Wait for a queued tour, keep its video, find its holds and add each as a
    take of the location's TOUR_VIEW. Returns the sidecar (status ok or failed).
    `on_hold(take)` is called for each hold added."""
    n = side["tour"]
    path = _tour_file(ref, n, ".json")
    try:
        outputs = comfy.wait(side["comfy_prompt_id"], timeout)
        f = _video_of(outputs)
        if f is None:
            raise TourError("ComfyUI finished without saving a video")
        mp4 = _tour_file(ref, n, os.path.splitext(f["filename"])[1].lower() or ".mp4")
        with open(mp4, "wb") as fh:
            fh.write(comfy.view(f))
        found = find_holds(mp4, os.path.join(tours_dir(ref), f"tour_t{n:02d}_holds"))
        takes = []
        for h in found["holds"]:
            take = R.reserve_take(ref, R.TOUR_VIEW, {
                "status": "queued", "queued": T.now(), "ep": s.ep, "source": "tour",
                "tour": n, "hold": h["hold"], "start": round(h["start"], 2),
                "end": round(h["end"], 2), "frame": h["frame"], "prompt": side["prompt"],
                "seed": side["seed"], "note": f"tour t{n:02d} hold {h['hold']} "
                                              f"({h['start']:.1f}-{h['end']:.1f}s)"})
            with open(h["path"], "rb") as fh:
                R.close_take(take, fh.read())
            takes.append(take.take)
            if on_hold:
                on_hold(take)
        side.update(status="ok", finished=T.now(), video=R.ep_rel(s.ep, mp4), holds=takes,
                    fps=found.get("fps"))
    except Exception as e:                      # noqa: BLE001 (any failure is the tour's)
        side.update(status="failed", finished=T.now(), error=str(e)[:800])
    T.write_json(path, side)
    return side


def run_tour(s: R.Series, ref: R.Ref, move: str, comfy, seconds: float = DEFAULT_SECONDS,
             count: int = 1, seed: int | None = None, timeout: int = 1800, log=print) -> list[dict]:
    """start_tour, then finish each in turn."""
    done = []
    for side in start_tour(s, ref, move, comfy, seconds, count, seed):
        log(f"  .. {ref.id} tour t{side['tour']:02d} queued")
        side = finish_tour(s, ref, side, comfy, timeout)
        log(f"  .. tour t{side['tour']:02d}: {side['status']}"
            + (f", {len(side['holds'])} hold(s)" if side["status"] == "ok" else f": {side['error']}"))
        done.append(side)
    return done


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("episode")
    ap.add_argument("ref", help="the location, e.g. location:ambulance_bay_night")
    ap.add_argument("move", help="where the camera goes and where it holds")
    ap.add_argument("--seconds", type=float, default=DEFAULT_SECONDS)
    ap.add_argument("--count", type=int, default=1)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--comfy", default="http://127.0.0.1:8188")
    ap.add_argument("--timeout", type=int, default=1800)
    a = ap.parse_args(argv)
    s = R.load_series(os.path.abspath(a.episode))
    try:
        ref = R.find_ref(s, a.ref if a.ref.startswith("location:") else f"location:{a.ref}")
        done = run_tour(s, ref, a.move, J.Comfy(a.comfy, client_id="h3tour"), a.seconds,
                        a.count, a.seed, a.timeout)
    except (TourError, R.RefError, R.UnknownRef) as e:
        print(f"  !! {e}")
        return 1
    for side in done:
        for k in side.get("holds", []):
            t = R.get_take(ref, R.TOUR_VIEW, k)
            print(f"  <- {R.ep_rel(s.ep, t.paths.image)}")
    return 0 if all(d["status"] == "ok" for d in done) else 1


if __name__ == "__main__":
    sys.exit(main())
