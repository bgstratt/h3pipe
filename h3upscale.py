#!/usr/bin/env python3
"""
h3upscale.py — Phase 13 (docs/PLAN.md): a final take, refined at 2x.

An upscale is a version of its take, not a new take: <stem>.up.mp4 beside it,
with <stem>.up.json. It re-samples the take at twice the size from late in the
schedule (step 7 of 8 by default), under the take's own frozen shotlist, so it
adds detail without re-inventing the shot, and it holds the take's audio so
the lips are re-drawn to the finished line. The mp4's audio is the take's own
stream, copied on. The start is a fraction of the take's own schedule (the
target's `start`, 0.875: step 7 of 8), so a take on the no-turbo base preset
starts at the same noise level.

    python h3upscale.py <episode> [--only sh760,sh770] [--take N] [--redo]
                        [--scale 2] [--start-step 7] [--vae] [--check]

Without --only: every shot of the final cut whose take (the pick, else the
latest usable) has no fresh upscale. The graph is the take's target's render
graph (`upscale` in its target.json says how), with the sampler's latent
replaced by the take's latent (<stem>.latent.safetensors, Phase 13a) or, for
a take without one, its frames and audio through the VAE (`--vae` forces it).

Stdlib only.
"""
from __future__ import annotations

import argparse
import copy
import os
import sys
import time
from dataclasses import dataclass, field

import h3jobs as J
import h3takes as T
import targets as TG

PASS = "final"
ROUTES = ("latent", "vae")


class UpscaleError(ValueError):
    pass


@dataclass
class UpscaleJob:
    root: str
    take: T.Take
    target: "TG.Target"
    spec: dict
    route: str                          # latent | vae
    scale: float
    start_step: int
    width: int
    height: int
    action: str = "upscale"             # upscale | skip | error
    why: str = ""
    notes: list = field(default_factory=list)

    @property
    def shot(self) -> str:
        return self.take.shot

    @property
    def label(self) -> str:
        return f"{self.shot} t{self.take.take:02d}"


def rel(root: str, path: str) -> str:
    return os.path.relpath(path, root)


def upscale_spec(target: "TG.Target") -> dict | None:
    """The target's `upscale` block (target.json), or None: it can't upscale."""
    s = target.spec.get("upscale")
    return s if isinstance(s, dict) and s.get("upscaler") else None


def scaled(w: int, h: int, scale: float, align: int = 32) -> tuple[int, int]:
    """The upscaled size; UpscaleError unless both sides land on `align`, so the
    conditioning's size is exactly what the latent upscaler makes."""
    sw, sh = w * scale, h * scale
    if sw != int(sw) or sh != int(sh) or int(sw) % align or int(sh) % align:
        raise UpscaleError(f"{w}x{h} at {scale}x is {sw:g}x{sh:g}, not a multiple of {align}: "
                           f"use a scale that is")
    return int(sw), int(sh)


def start_of(steps: int, spec: dict) -> int:
    """The default start step: `start` (a fraction of the take's own schedule,
    0.875 = step 7 of 8, step 5 of 6, step 18 of 20), never the last step's end."""
    return min(steps - 1, max(0, round(steps * float(spec.get("start", 0.875)))))


def plan_upscale(root: str, take: T.Take, *, scale: float | None = None,
                 start_step: int | None = None, route: str | None = None,
                 redo: bool = False) -> UpscaleJob:
    """What upscaling `take` would do. action "error" (with `why`) when it
    can't: not final, not usable, a target without `upscale`, a size that
    doesn't scale evenly; "skip" when it already has a fresh upscale."""
    sc = take.sidecar or {}
    target_id = sc.get("target") or T.DEFAULT_TARGET
    target = TG.load_target(target_id, "video", root=root)
    spec = upscale_spec(target) or {}
    w, h = int(sc.get("width") or 0), int(sc.get("height") or 0)
    s = float(scale or spec.get("scale", 2))
    steps = int(sc.get("steps") or 8)
    step = int(start_step) if start_step is not None else start_of(steps, spec)
    has_latent = bool(sc.get("latent")) and os.path.isfile(take.paths.latent)
    r = route or ("latent" if has_latent else "vae")
    job = UpscaleJob(root, take, target, spec, r, s, step, w, h)
    try:
        if take.pass_ != PASS:
            raise UpscaleError(f"{job.label} is a {take.pass_} take: only final takes are upscaled")
        if not take.usable:
            raise UpscaleError(f"{job.label} isn't a finished take ({take.status})")
        if not spec:
            raise UpscaleError(f"{target.short} can't upscale (its target.json has no `upscale`)")
        if not os.path.isfile(take.paths.shotlist):
            raise UpscaleError(f"{job.label} has no frozen shotlist to upscale from")
        if r == "latent" and not has_latent:
            raise UpscaleError(f"{job.label} kept no latent: upscale it through the VAE")
        if not w or not h:
            raise UpscaleError(f"{job.label}'s sidecar doesn't say its size")
        job.width, job.height = scaled(w, h, s, int(spec["upscaler"].get("align", 32)))
        if not 0 <= step < steps:
            raise UpscaleError(f"start step {step} isn't inside the take's {steps} steps")
    except UpscaleError as e:
        job.action, job.why = "error", str(e)
        return job
    up = T.upscale_of(take)
    if up and up["fresh"] and not redo:
        job.action, job.why = "skip", "already upscaled"
    return job


def take_job(up: UpscaleJob) -> J.Job:
    """The render job the take was, rebuilt from its frozen shotlist and
    sidecar: graph_for then patches the target's graph exactly as for the take."""
    take, sc = up.take, up.take.sidecar or {}
    doc = T.read_json(take.paths.shotlist)
    shot = doc["shots"][0]
    return J.Job(root=up.root, pass_=PASS, index=0, shot=shot, doc=doc, folder=None,
                 action="render", take=take.take, seed=int(shot.get("seed", sc.get("seed", 0))),
                 seed_source=sc.get("seed_source", "stable"),
                 model=shot.get("model") or sc.get("model") or "",
                 loras=copy.deepcopy(shot["loras"]) if "loras" in shot else sc.get("loras"),
                 steps=int(shot.get("steps", sc.get("steps", 8))), prompt=shot.get("prompt", ""),
                 target=up.target.id, based=bool(sc.get("base")))


def upscale_graph(base: dict, up: UpscaleJob) -> dict:
    """The target's render graph (API form) turned into this upscale:

    - the loader reads the take's frozen shotlist at the upscaled size, so the
      prompt and references are encoded as for the take, at the new size;
    - the sampler starts from the take's latent (or its frames and audio through
      the VAE), its video upscaled, its audio held (H3HoldAudio);
    - the schedule starts at `start_step` (SplitSigmas' low half);
    - H3SaveUpscale writes <stem>.up.mp4 with the take's audio copied on."""
    t, spec, take = up.target, up.spec, up.take
    b = t.binding
    job = take_job(up)
    g = J.graph_for(base, job, take, review_copy=False)
    size = spec.get("size") or {"class_type": b.loader_class, "field": "resolution_override"}
    for nid in J.select_nodes(g, size):
        g[nid]["inputs"][size["field"]] = f"{up.width}x{up.height}"

    sampler = J.node_of(g, b.saver["latent"]["class_type"])
    root = up.root
    if up.route == "latent":
        g["up_source"] = {"class_type": "H3LoadTakeLatent", "inputs": {
            "project_root": root, "latent_file": rel(root, take.paths.latent)}}
        av = ["up_source", 0]
    else:
        vvae = J.select_nodes(g, b.specs("video_vae")[0])[0]
        avae = J.select_nodes(g, b.specs("audio_vae")[0])[0]
        wav = take.paths.h3_wav if os.path.isfile(take.paths.h3_wav) else ""
        g["up_video"] = {"class_type": "H3LoadTakeVideo", "inputs": {
            "project_root": root, "video_file": rel(root, take.paths.mp4),
            "audio_file": rel(root, wav) if wav else ""}}
        g["up_venc"] = {"class_type": "VAEEncode", "inputs": {"pixels": ["up_video", 0],
                                                              "vae": [vvae, 0]}}
        g["up_aenc"] = {"class_type": "VAEEncodeAudio", "inputs": {"audio": ["up_video", 1],
                                                                   "vae": [avae, 0]}}
        g["up_source"] = {"class_type": "LTXVConcatAVLatent", "inputs": {
            "video_latent": ["up_venc", 0], "audio_latent": ["up_aenc", 0]}}
        av = ["up_source", 0]
    u = spec["upscaler"]
    g["up_split_av"] = {"class_type": "LTXVSeparateAVLatent", "inputs": {"av_latent": av}}
    g["up_scale"] = {"class_type": u["class_type"], "inputs": {
        **copy.deepcopy(u.get("inputs") or {}),
        u.get("latent", "latent"): ["up_split_av", 0], u.get("scale", "scale"): up.scale}}
    g["up_join"] = {"class_type": "LTXVConcatAVLatent", "inputs": {
        "video_latent": ["up_scale", 0], "audio_latent": ["up_split_av", 1]}}
    g["up_hold"] = {"class_type": "H3HoldAudio", "inputs": {"latent": ["up_join", 0]}}
    si = g[sampler]["inputs"]
    si["latent_image"] = ["up_hold", 0]
    g["up_sigmas"] = {"class_type": "SplitSigmas", "inputs": {"sigmas": si["sigmas"],
                                                              "step": up.start_step}}
    si["sigmas"] = ["up_sigmas", 1]

    old = J.node_of(g, b.saver_class)
    images = g[old]["inputs"]["images"]
    del g[old]
    g["up_save"] = {"class_type": "H3SaveUpscale", "inputs": {
        "images": images, "project_root": root, "source_mp4": rel(root, take.paths.mp4),
        "out_mp4": rel(root, take.paths.up_mp4), "fps": float((take.sidecar or {}).get("fps") or 24),
        "sidecar": rel(root, take.paths.up_sidecar)}}
    J.prune(g, "up_save")
    return g


def queued_record(up: UpscaleJob) -> dict:
    u = up.spec["upscaler"]
    return {"shot": up.shot, "take": up.take.take, "status": "queued", "queued": T.now(),
            "comfy_prompt_id": None, "target": up.target.id, "route": up.route,
            "scale": up.scale, "start_step": up.start_step,
            "steps": int((up.take.sidecar or {}).get("steps") or 0),
            "seed": (up.take.sidecar or {}).get("seed"),
            "upscaler": (u.get("inputs") or {}).get("model_name") or u["class_type"],
            "width": up.width, "height": up.height, **T.source_stamp(up.take.paths.mp4)}


def start(up: UpscaleJob) -> None:
    T.write_json(up.take.paths.up_sidecar, queued_record(up))


def mark_queued(up: UpscaleJob, pid: str) -> None:
    T.update_sidecar(up.take.paths.up_sidecar, comfy_prompt_id=pid)


def mark_failed(up: UpscaleJob, why: str) -> None:
    T.update_sidecar(up.take.paths.up_sidecar, status="failed", finished=T.now(),
                     save_notes=why)


def cut_takes(root: str, only: set[str] | None = None, take_n: int | None = None) -> list:
    """(shot, Take | None, why) for each final-cut shot (or `only`): the pick,
    else the latest usable take; `take_n` forces a number."""
    from h3assemble import choose_take
    entries = T.resolve_cut(T.load_cut(root), PASS, J.script_order(root))
    out = []
    for e in entries:
        if only is not None and e.shot not in only:
            continue
        if e.pass_ != PASS:
            out.append((e.shot, None, f"the cut uses its {e.pass_} take"))
            continue
        t, why = choose_take(root, e, None, take_n)
        out.append((e.shot, t, why))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("episode", help="episode folder")
    ap.add_argument("--only", help="comma-separated shot ids")
    ap.add_argument("--take", type=int, help="this take number instead of the cut's")
    ap.add_argument("--redo", action="store_true", help="upscale again even if fresh")
    ap.add_argument("--scale", type=float, help="default: the target's (2)")
    ap.add_argument("--start-step", type=int,
                    help="where the re-sample starts, a step of the take's schedule "
                         "(default 7/8 of the way: step 7 of 8, which keeps the take's "
                         "performance; 6 or 5 of 8 add detail and change more)")
    ap.add_argument("--vae", action="store_true",
                    help="encode the take's frames even if it kept a latent")
    ap.add_argument("--check", action="store_true", help="list the jobs, queue nothing")
    ap.add_argument("--comfy", default="http://127.0.0.1:8188")
    ap.add_argument("--timeout", type=int, default=3600)
    args = ap.parse_args(argv)

    root = os.path.abspath(args.episode)
    TG.add_thread_root(root)
    only = {s.strip() for s in args.only.split(",")} if args.only else None
    jobs = []
    for shot, take, why in cut_takes(root, only, args.take):
        if take is None:
            print(f"  -  {shot}: {why}")
            continue
        up = plan_upscale(root, take, scale=args.scale, start_step=args.start_step,
                          route="vae" if args.vae else None, redo=args.redo)
        mark = {"upscale": "..", "skip": "= ", "error": "!!"}[up.action]
        what = (f"{up.route}, {up.scale:g}x -> {up.width}x{up.height}, from step {up.start_step}"
                if up.action == "upscale" else up.why)
        print(f"  {mark} {up.label}: {what}")
        jobs.append(up)
    todo = [j for j in jobs if j.action == "upscale"]
    print(f"\n  {len(todo)} to upscale")
    if args.check or not todo:
        return 0

    comfy = J.Comfy(args.comfy)
    bases: dict = {}
    done = failed = 0
    t_all = time.time()
    for up in todo:
        t0 = time.time()
        try:
            if up.target.id not in bases:
                bases[up.target.id] = J.target_workflow(up.target, None, args.comfy)[0]
            g = upscale_graph(bases[up.target.id], up)
            start(up)
            pid = comfy.queue(g)
            mark_queued(up, pid)
            comfy.wait(pid, args.timeout)
            rec = T.upscale_of(up.take) or {}
            if rec.get("status") != "ok":
                raise RuntimeError(rec.get("save_notes") or "the upscale wasn't written")
            done += 1
            print(f"  -> {up.take.paths.up_mp4}  [{time.time() - t0:.1f}s]", flush=True)
        except TimeoutError as e:
            failed += 1
            print(f"  !! {up.label}: {e} (left queued in ComfyUI)", flush=True)
        except Exception as e:
            failed += 1
            if os.path.isfile(up.take.paths.up_sidecar):
                mark_failed(up, str(e)[:800])
            print(f"  !! {up.label}: {e}", flush=True)
    print(f"\n  done in {time.time() - t_all:.0f}s: {done} upscaled"
          + (f", {failed} failed" if failed else "") + "\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
