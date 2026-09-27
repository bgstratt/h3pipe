#!/usr/bin/env python3
"""
h3upscale.py — Phase 13 (docs/PLAN.md): a take, refined at 2x (either pass).

An upscale is a version of its take, not a new take: <stem>.up.mp4 beside it,
with <stem>.up.json. It re-samples the take at twice the size from late in the
schedule (step 7 of 8 by default), under the take's own frozen shotlist, so it
adds detail without re-inventing the shot, and it holds the take's audio so
the lips are re-drawn to the finished line. The mp4's audio is the take's own
stream, copied on. The start is a fraction of the take's own schedule (the
target's `start`, 0.875: step 7 of 8), so a take on the no-turbo base preset
starts at the same noise level.

    python h3upscale.py <episode> [--proxy] [--only sh760,sh770] [--take N] [--redo]
                        [--scale 2] [--start-step 7] [--vae] [--check]

Without --only: every shot of the pass's cut (final, or --proxy) whose take (the pick, else the
latest usable) has no fresh upscale. The graph is the take's target's render
graph (`upscale` in its target.json says how), with the sampler's latent
replaced by the take's latent (<stem>.latent.safetensors, Phase 13a) or, for
a take without one, its frames and audio through the VAE (`--vae` forces it).

Two methods. `latent` (the targets with an `upscale` block: H3 Ref2VA, LTX-2)
re-samples as above. `pixel` (any target, the default for the rest) runs an
upscale model from ComfyUI's models/upscale_models (RealESRGAN, UltraSharp...)
over the take's frames and copies its audio on: fast, adds no generated detail.
`--method pixel --pixel-model RealESRGAN_x4.pth` picks it for any take.

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

PASS = "final"                          # the default pass; a proxy take upscales too
ROUTES = ("latent", "vae")


class UpscaleError(ValueError):
    pass


@dataclass
class UpscaleJob:
    root: str
    take: T.Take
    target: "TG.Target"
    spec: dict
    route: str                          # latent | vae | pixel
    scale: float
    start_step: int | None              # None for the pixel method
    width: int
    height: int
    action: str = "upscale"             # upscale | skip | error
    method: str = "latent"              # latent | pixel
    pixel_model: str = ""
    # "then pixel": after a re-sample, an upscale model takes width x height on
    # to out_width x out_height in the same job ("" / 1.0: no second step)
    then_model: str = ""
    then_scale: float = 1.0
    out_width: int = 0
    out_height: int = 0

    @property
    def out_size(self) -> tuple[int, int]:
        """What the .up.mp4 is: after the then-pixel step if there is one."""
        return (self.out_width or self.width, self.out_height or self.height)
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


RESAMPLE, SECOND_STAGE = "resample", "second_stage"


def upscale_spec(target: "TG.Target") -> dict | None:
    """The target's `upscale` block (target.json), or None: it can't upscale.

    Two modes. `resample` (H3, the default): the take's latent through an
    external latent upscaler (`upscaler`), then the target's sampler from late
    in its schedule, conditioned at the new size by the loader. `second_stage`
    (LTX-2): the target's render graph is already two-stage (a latent upsampler
    and a short fixed schedule), so an upscale is that second stage run again on
    the take: its own `upsampler` node fed the take's latent, the stage's
    `sigmas` cut to their tail."""
    s = target.spec.get("upscale")
    if not isinstance(s, dict):
        return None
    mode = s.get("mode", RESAMPLE)
    if mode == RESAMPLE and s.get("upscaler"):
        return s
    if mode == SECOND_STAGE and s.get("upsampler") and s.get("steps"):
        return s
    return None


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


def schedule_steps(spec: dict, take_steps: int) -> int:
    """How many steps the upscale's schedule has: the take's own (resample), or
    the target's second stage's (LTX-2: its fixed tail, whatever the take's
    `steps` say)."""
    if spec.get("mode", RESAMPLE) == SECOND_STAGE:
        return int(spec["steps"])
    return take_steps


def align_of(spec: dict) -> int:
    return int(spec.get("align") or (spec.get("upscaler") or {}).get("align") or 32)


# the nodes an upscale graph adds to the target's (besides its upscaler)
UPSCALE_NODES = ("H3LoadTakeLatent", "H3LoadTakeVideo", "H3HoldAudio", "H3SaveUpscale",
                 "LTXVSeparateAVLatent", "LTXVConcatAVLatent", "SplitSigmas", "VAEEncode",
                 "VAEEncodeAudio")
AUDIO_ENCODE = {"class_type": "VAEEncodeAudio", "audio": "audio", "vae": "vae"}

METHODS = ("latent", "pixel")
PIXEL_NODES = ("H3LoadTakeVideo", "H3PixelUpscale", "UpscaleModelLoader", "H3SaveUpscale")
DEFAULT_PIXEL_MODEL = "RealESRGAN_x2.pth"
DETAILS = (0, 1, 2)                     # steps earlier than the default start
MAX_SCALE = 4.0                         # the H3 latent upscaler's limit; the pixel method's too


def pixel_models(object_info: dict | None) -> list[str] | None:
    """The upscale models this ComfyUI lists (models/upscale_models), or None."""
    return J.choices_in(object_info or {}, "UpscaleModelLoader", "model_name") \
        if object_info else None


def default_pixel_model(models: list[str] | None) -> str:
    """RealESRGAN_x2 when installed (exactly 2x, gentle on grain), else the
    first 2x model, else the first one."""
    models = models or []
    if DEFAULT_PIXEL_MODEL in models:
        return DEFAULT_PIXEL_MODEL
    two = [m for m in models if "x2" in m.lower() or m.lower().startswith("2x")]
    return (two or models or [DEFAULT_PIXEL_MODEL])[0]


def pixel_readiness(object_info: dict | None, want: str | None = None) -> dict:
    """Whether this ComfyUI can run the pixel method: its nodes, and an upscale
    model (`want`, if named). {"status", "missing", "models", "default"}."""
    if object_info is None:
        return {"status": "unknown", "missing": [], "models": [], "default": DEFAULT_PIXEL_MODEL}
    missing = [f"node {c} (update h3pipe's node pack, or ComfyUI, and restart it)"
               for c in PIXEL_NODES if c not in object_info]
    models = pixel_models(object_info) or []
    if not models:
        missing.append("an upscale model in models/upscale_models/ (RealESRGAN_x2.pth, say)")
    elif want and want not in models:
        missing.append(f"{want} in models/upscale_models/ (it has {', '.join(models)})")
    return {"status": "not_ready" if missing else "ready", "missing": missing,
            "models": models, "default": default_pixel_model(models)}


def upscale_readiness(target: "TG.Target", object_info: dict | None) -> dict | None:
    """Whether the running ComfyUI can upscale this target's takes (None: the
    target can't upscale at all). {"status": "ready" | "not_ready" | "unknown",
    "missing": [sentences]}. Separate from render readiness: a render never
    needs any of it. A `resample` upscaler must be the pack with temporal
    chunking (its "Plus" fork has the same node id without it, and the refine
    hallucinates); a `second_stage` target's upsampler and its model file are
    its render's own, so render readiness already covers them."""
    spec = upscale_spec(target)
    if spec is None:
        return None
    if object_info is None:
        return {"status": "unknown", "missing": []}
    need = list(UPSCALE_NODES)
    enc = (spec.get("encode_audio") or AUDIO_ENCODE)["class_type"]
    if enc not in need:
        need.append(enc)
    if spec.get("mode", RESAMPLE) == SECOND_STAGE:
        need.append(spec["upsampler"]["class_type"])
    missing = [f"node {c} (update h3pipe's node pack, or ComfyUI, and restart it)"
               for c in need if c not in object_info]
    if spec.get("mode", RESAMPLE) == RESAMPLE:
        u = spec["upscaler"]
        info = object_info.get(u["class_type"])
        if info is None:
            missing.append(f"node {u['class_type']}: install the Comfyui_Minimax_h3_latent_Upscaler "
                           f"pack (LBH-123-AI), not its Plus fork")
        else:
            req = (info.get("input") or {}).get("required") or {}
            if "enable_temporal_chunking" not in req:
                missing.append(f"{u['class_type']} has no temporal chunking: that is the Plus fork. "
                               f"Install LBH-123-AI's original pack instead")
            want = (u.get("inputs") or {}).get("model_name")
            choices = J.choices_in(object_info, u["class_type"], "model_name")
            if want and choices is not None and want not in choices:
                missing.append(f"{want} in models/latent_upscale_models/")
    return {"status": "not_ready" if missing else "ready", "missing": missing}


def plan_upscale(root: str, take: T.Take, *, scale: float | None = None,
                 start_step: int | None = None, route: str | None = None,
                 redo: bool = False, method: str | None = None,
                 pixel_model: str | None = None, detail: int | None = None,
                 then_model: str | None = None, then_scale: float | None = None) -> UpscaleJob:
    """What upscaling `take` would do. action "error" (with `why`) when it
    can't: not final, not usable, a latent upscale on a target without
    `upscale`, a size that doesn't scale evenly; "skip" when it already has a
    fresh upscale. `method`: latent (default where the target has one) or
    pixel (the default elsewhere). `detail` (0-2) starts that many steps
    earlier than the default; `start_step` names the step outright.
    `then_model` (a latent upscale only) adds a pixel step after the re-sample:
    that upscale model takes the result on by `then_scale` (default 2) in the
    same job, so re-sample 2x then RealESRGAN_x2 makes 4x."""
    sc = take.sidecar or {}
    target_id = sc.get("target") or T.DEFAULT_TARGET
    target = TG.load_target(target_id, "video", root=root)
    spec = upscale_spec(target) or {}
    m = method or ("latent" if spec else "pixel")
    w, h = int(sc.get("width") or 0), int(sc.get("height") or 0)
    if m == "pixel":
        job = UpscaleJob(root, take, target, spec, "pixel", float(scale or 2), None, w, h,
                         method="pixel", pixel_model=pixel_model or DEFAULT_PIXEL_MODEL)
        try:
            if not take.usable:
                raise UpscaleError(f"{job.label} isn't a finished take ({take.status})")
            if not w or not h:
                raise UpscaleError(f"{job.label}'s sidecar doesn't say its size")
            if not 1 < job.scale <= MAX_SCALE:
                raise UpscaleError(f"scale {job.scale:g}: it's more than 1, up to {MAX_SCALE:g}")
            job.width, job.height = scaled(w, h, job.scale, 2)
        except UpscaleError as e:
            job.action, job.why = "error", str(e)
            return job
        up = T.upscale_of(take)
        if up and up["fresh"] and not redo:
            job.action, job.why = "skip", "already upscaled"
        return job
    s = float(scale or spec.get("scale", 2))
    steps = schedule_steps(spec, int(sc.get("steps") or 8)) if spec else 8
    if start_step is not None:
        step = int(start_step)
    elif detail:
        step = max(0, start_of(steps, spec) - int(detail))
    else:
        step = start_of(steps, spec)
    has_latent = bool(sc.get("latent")) and os.path.isfile(take.paths.latent)
    r = route or ("latent" if has_latent else "vae")
    job = UpscaleJob(root, take, target, spec, r, s, step, w, h)
    try:
        if m not in METHODS:
            raise UpscaleError(f"method {m!r}: it's latent or pixel")
        if not take.usable:
            raise UpscaleError(f"{job.label} isn't a finished take ({take.status})")
        if not spec:
            raise UpscaleError(f"{target.short} has no latent upscale: use the pixel method")
        if not os.path.isfile(take.paths.shotlist):
            raise UpscaleError(f"{job.label} has no frozen shotlist to upscale from")
        if r == "latent" and not has_latent:
            raise UpscaleError(f"{job.label} kept no latent: upscale it through the VAE")
        if not w or not h:
            raise UpscaleError(f"{job.label}'s sidecar doesn't say its size")
        if spec.get("mode", RESAMPLE) == SECOND_STAGE and s != float(spec.get("scale", 2)):
            raise UpscaleError(f"{target.short}'s upsampler is fixed at {spec.get('scale', 2):g}x")
        if not 1 < s <= MAX_SCALE:
            raise UpscaleError(f"scale {s:g}: it's more than 1, up to {MAX_SCALE:g}")
        job.width, job.height = scaled(w, h, s, align_of(spec))
        if then_model:
            job.then_model, job.then_scale = then_model, float(then_scale or 2)
            if not 1 < job.then_scale <= MAX_SCALE:
                raise UpscaleError(f"then-scale {job.then_scale:g}: it's more than 1, up to {MAX_SCALE:g}")
            job.out_width, job.out_height = scaled(job.width, job.height, job.then_scale, 2)
        if not 0 <= step < steps:
            raise UpscaleError(f"start step {step} isn't inside the {steps}-step schedule")
    except UpscaleError as e:
        job.action, job.why = "error", str(e)
        return job
    up = T.upscale_of(take)
    if up and up["fresh"] and not redo:
        job.action, job.why = "skip", "already upscaled"
    return job


def take_job(up: UpscaleJob) -> J.Job:
    """The render job the take was, rebuilt from its frozen shotlist and
    sidecar: graph_for then patches the target's graph exactly as for the take.
    Its staged inputs come back too (a first frame the second stage re-imposes),
    except a reference sheet, which only a first stage reads."""
    take, sc = up.take, up.take.sidecar or {}
    doc = T.read_json(take.paths.shotlist)
    shot = doc["shots"][0]
    inputs = {k: v for k, v in (sc.get("inputs") or {}).items() if k != "sheet"}
    return J.Job(root=up.root, pass_=take.pass_, index=0, shot=shot, doc=doc, folder=None,
                 action="render", take=take.take, seed=int(shot.get("seed", sc.get("seed", 0))),
                 seed_source=sc.get("seed_source", "stable"),
                 model=shot.get("model") or sc.get("model") or "",
                 loras=copy.deepcopy(shot["loras"]) if "loras" in shot else sc.get("loras"),
                 steps=int(shot.get("steps", sc.get("steps", 8))), prompt=shot.get("prompt", ""),
                 target=up.target.id, based=bool(sc.get("base")), inputs=inputs)


def take_source(g: dict, up: UpscaleJob) -> list:
    """Nodes that give the take's joint AV latent at the take's size: its kept
    latent, or its frames and the audio its lips were made against through the
    VAEs. Returns the link to it."""
    b, take, root = up.target.binding, up.take, up.root
    if up.route == "latent":
        g["up_source"] = {"class_type": "H3LoadTakeLatent", "inputs": {
            "project_root": root, "latent_file": rel(root, take.paths.latent)}}
        return ["up_source", 0]
    vvae = J.select_nodes(g, b.specs("video_vae")[0])[0]
    avae = J.select_nodes(g, b.specs("audio_vae")[0])[0]
    wav = take.paths.h3_wav if os.path.isfile(take.paths.h3_wav) else ""
    enc = up.spec.get("encode_audio") or AUDIO_ENCODE
    g["up_video"] = {"class_type": "H3LoadTakeVideo", "inputs": {
        "project_root": root, "video_file": rel(root, take.paths.mp4),
        "audio_file": rel(root, wav) if wav else ""}}
    g["up_venc"] = {"class_type": "VAEEncode", "inputs": {"pixels": ["up_video", 0],
                                                          "vae": [vvae, 0]}}
    g["up_aenc"] = {"class_type": enc["class_type"], "inputs": {enc["audio"]: ["up_video", 1],
                                                                enc["vae"]: [avae, 0]}}
    g["up_source"] = {"class_type": "LTXVConcatAVLatent", "inputs": {
        "video_latent": ["up_venc", 0], "audio_latent": ["up_aenc", 0]}}
    return ["up_source", 0]


def upscale_graph(base: dict, up: UpscaleJob) -> dict:
    """The target's render graph (API form) turned into this upscale. Both modes:
    the take's latent in (take_source), its audio held (H3HoldAudio), its
    schedule started late, and H3SaveUpscale writing <stem>.up.mp4 with the
    take's audio copied on.

    - resample (H3): the loader reads the take's frozen shotlist at the new
      size, so the prompt and references are encoded as for the take; the
      video goes through the external upscaler; SplitSigmas starts the
      sampler's own schedule at `start_step`.
    - second_stage (LTX-2): the final stage's own upsampler takes the take's
      video, its join takes the take's audio, and its fixed sigmas are cut to
      their tail from `start_step`; the first stage falls away in the prune."""
    t, spec, take = up.target, up.spec, up.take
    b = t.binding
    g = J.graph_for(base, take_job(up), take, review_copy=False)
    saver = J.node_of(g, b.saver_class)
    sampler = J.latent_node(g, saver, b.saver["latent"])
    si = g[sampler]["inputs"]
    av = take_source(g, up)
    g["up_split_av"] = {"class_type": "LTXVSeparateAVLatent", "inputs": {"av_latent": av}}

    if spec.get("mode", RESAMPLE) == SECOND_STAGE:
        join = si["latent_image"][0]
        u = spec["upsampler"]
        ups = J.upstream_node(g, join, u["class_type"])
        if ups is None:
            raise UpscaleError(f"{t.short}'s graph has no {u['class_type']} before its last sampler")
        g[ups]["inputs"][u.get("input", "samples")] = ["up_split_av", 0]
        g[join]["inputs"]["audio_latent"] = ["up_split_av", 1]
        g["up_hold"] = {"class_type": "H3HoldAudio", "inputs": {"latent": [join, 0]}}
        si["latent_image"] = ["up_hold", 0]
        sig = si["sigmas"][0]
        sspec = spec.get("sigmas") or {"class_type": "ManualSigmas", "field": "sigmas"}
        if g[sig]["class_type"] != sspec["class_type"]:
            raise UpscaleError(f"{t.short}'s last sampler's sigmas aren't a {sspec['class_type']}")
        vals = [v.strip() for v in str(g[sig]["inputs"][sspec["field"]]).split(",") if v.strip()]
        if up.start_step >= len(vals) - 1:
            raise UpscaleError(f"start step {up.start_step} leaves nothing of {', '.join(vals)}")
        g[sig]["inputs"][sspec["field"]] = ", ".join(vals[up.start_step:])
    else:
        size = spec.get("size") or {"class_type": b.loader_class, "field": "resolution_override"}
        for nid in J.select_nodes(g, size):
            g[nid]["inputs"][size["field"]] = f"{up.width}x{up.height}"
        u = spec["upscaler"]
        g["up_scale"] = {"class_type": u["class_type"], "inputs": {
            **copy.deepcopy(u.get("inputs") or {}),
            u.get("latent", "latent"): ["up_split_av", 0], u.get("scale", "scale"): up.scale}}
        g["up_join"] = {"class_type": "LTXVConcatAVLatent", "inputs": {
            "video_latent": ["up_scale", 0], "audio_latent": ["up_split_av", 1]}}
        g["up_hold"] = {"class_type": "H3HoldAudio", "inputs": {"latent": ["up_join", 0]}}
        si["latent_image"] = ["up_hold", 0]
        g["up_sigmas"] = {"class_type": "SplitSigmas", "inputs": {"sigmas": si["sigmas"],
                                                                  "step": up.start_step}}
        si["sigmas"] = ["up_sigmas", 1]

    images = g[saver]["inputs"]["images"]
    del g[saver]
    g["up_save"] = {"class_type": "H3SaveUpscale", "inputs": {
        "images": images, "project_root": up.root, "source_mp4": rel(up.root, take.paths.mp4),
        "out_mp4": rel(up.root, take.paths.up_mp4), "fps": float((take.sidecar or {}).get("fps") or 24),
        "sidecar": rel(up.root, take.paths.up_sidecar)}}
    if up.then_model:
        # the re-sample's frames through an upscale model before they're saved
        g["up_then_model"] = {"class_type": "UpscaleModelLoader",
                              "inputs": {"model_name": up.then_model}}
        g["up_then"] = {"class_type": "H3PixelUpscale", "inputs": {
            "images": images, "upscale_model": ["up_then_model", 0],
            "width": up.out_size[0], "height": up.out_size[1], "chunk": 2}}
        g["up_save"]["inputs"]["images"] = ["up_then", 0]
    J.prune(g, "up_save")
    return g


def pixel_graph(up: UpscaleJob) -> dict:
    """The pixel method: the take's frames through an upscale model, resized to
    the target size, saved with the take's audio copied on. No model of the
    take's target is loaded, so it works for any take."""
    take, root = up.take, up.root
    return {
        "up_video": {"class_type": "H3LoadTakeVideo", "inputs": {
            "project_root": root, "video_file": rel(root, take.paths.mp4), "audio_file": ""}},
        "up_model": {"class_type": "UpscaleModelLoader", "inputs": {"model_name": up.pixel_model}},
        "up_pixels": {"class_type": "H3PixelUpscale", "inputs": {
            "images": ["up_video", 0], "upscale_model": ["up_model", 0],
            "width": up.width, "height": up.height, "chunk": 4}},
        "up_save": {"class_type": "H3SaveUpscale", "inputs": {
            "images": ["up_pixels", 0], "project_root": root,
            "source_mp4": rel(root, take.paths.mp4), "out_mp4": rel(root, take.paths.up_mp4),
            "fps": float((take.sidecar or {}).get("fps") or 24),
            "sidecar": rel(root, take.paths.up_sidecar)}},
    }


def not_ready(jobs: list[UpscaleJob], object_info: dict | None) -> list[str]:
    """Why this ComfyUI can't run these jobs ([] when it can)."""
    out = []
    for t in {j.target.id: j.target for j in jobs if j.method == "latent"}.values():
        r = upscale_readiness(t, object_info)
        if r and r["status"] == "not_ready":
            out += [f"{t.short}: {m}" for m in r["missing"]]
    wants = {j.pixel_model for j in jobs if j.method == "pixel"}
    wants |= {j.then_model for j in jobs if j.method == "latent" and j.then_model}
    for want in sorted(wants):
        r = pixel_readiness(object_info, want)
        if r["status"] == "not_ready":
            out += [f"pixel: {m}" for m in r["missing"]]
    return list(dict.fromkeys(out))


def graph_of(up: UpscaleJob, bases: dict, comfy_url: str) -> dict:
    """The graph that runs this upscale (a latent one reads its target's graph
    once per `bases`)."""
    if up.method == "pixel":
        return pixel_graph(up)
    if up.target.id not in bases:
        bases[up.target.id] = J.target_workflow(up.target, None, comfy_url)[0]
    return upscale_graph(bases[up.target.id], up)


def describe(up: UpscaleJob) -> str:
    if up.method == "pixel":
        return f"pixel ({up.pixel_model}), {up.scale:g}x -> {up.width}x{up.height}"
    then = (f", then {up.then_model} {up.then_scale:g}x -> {up.out_size[0]}x{up.out_size[1]}"
            if up.then_model else "")
    return (f"{up.route}, {up.scale:g}x -> {up.width}x{up.height}, from step {up.start_step}{then}")


def queued_record(up: UpscaleJob) -> dict:
    if up.method == "pixel":
        return {"shot": up.shot, "take": up.take.take, "status": "queued", "queued": T.now(),
                "comfy_prompt_id": None, "target": up.target.id, "route": "pixel",
                "method": "pixel", "mode": "pixel", "scale": up.scale, "start_step": None,
                "steps": None, "seed": None, "upscaler": up.pixel_model,
                "pixel_model": up.pixel_model,
                "width": up.width, "height": up.height, **T.source_stamp(up.take.paths.mp4)}
    if up.spec.get("mode", RESAMPLE) == SECOND_STAGE:
        upscaler = f"{up.spec['upsampler']['class_type']} (the target's own second stage)"
    else:
        u = up.spec["upscaler"]
        upscaler = (u.get("inputs") or {}).get("model_name") or u["class_type"]
    return {"shot": up.shot, "take": up.take.take, "status": "queued", "queued": T.now(),
            "comfy_prompt_id": None, "target": up.target.id, "route": up.route,
            "method": "latent", "mode": up.spec.get("mode", RESAMPLE),
            "scale": up.scale, "start_step": up.start_step,
            "steps": schedule_steps(up.spec, int((up.take.sidecar or {}).get("steps") or 0)),
            "seed": (up.take.sidecar or {}).get("seed"),
            "upscaler": upscaler,
            **({"then_pixel": {"model": up.then_model, "scale": up.then_scale,
                               "from": [up.width, up.height]}} if up.then_model else {}),
            "width": up.out_size[0], "height": up.out_size[1], **T.source_stamp(up.take.paths.mp4)}


def start(up: UpscaleJob) -> None:
    T.write_json(up.take.paths.up_sidecar, queued_record(up))


def mark_queued(up: UpscaleJob, pid: str) -> None:
    T.update_sidecar(up.take.paths.up_sidecar, comfy_prompt_id=pid)


def mark_failed(up: UpscaleJob, why: str) -> None:
    T.update_sidecar(up.take.paths.up_sidecar, status="failed", finished=T.now(),
                     save_notes=why)


def cut_takes(root: str, only: set[str] | None = None, take_n: int | None = None,
              pass_: str = PASS) -> list:
    """(shot, Take | None, why) for each shot of the pass's cut (or `only`): the
    pick, else the latest usable take; `take_n` forces a number. A placeholder
    (a take from the other pass) is left out."""
    from h3assemble import choose_take
    entries = T.resolve_cut(T.load_cut(root), pass_, J.script_order(root))
    out = []
    for e in entries:
        if only is not None and e.shot not in only:
            continue
        if e.pass_ != pass_:
            out.append((e.shot, None, f"the cut uses its {e.pass_} take"))
            continue
        t, why = choose_take(root, e, None, take_n)
        out.append((e.shot, t, why))
    return out


def prune_latents(root: str, only: set[str] | None = None,
                  dry_run: bool = False, pass_: str = PASS) -> list[tuple[str, int, str]]:
    """Delete the latents nothing needs: of the pass's takes its cut doesn't use, and
    of takes with a fresh upscale (either can still be upscaled, through the
    VAE). Returns (path, bytes, why) for each; `dry_run` deletes nothing. The
    sidecar's `latent` goes with the file (and `latent_pruned` says when)."""
    picked = {shot: take.take for shot, take, _ in cut_takes(root, only, pass_=pass_)
              if take is not None}
    out = []
    for shot in J.script_order(root):
        if only is not None and shot not in only:
            continue
        for t in T.list_takes(root, pass_, shot):
            if not os.path.isfile(t.paths.latent):
                continue
            up = T.upscale_of(t)
            why = ("not in the cut" if picked.get(shot) != t.take
                   else "upscaled" if up and up["fresh"] else None)
            if why is None:
                continue
            out.append((t.paths.latent, os.path.getsize(t.paths.latent), why))
            if not dry_run:
                os.remove(t.paths.latent)
                if t.sidecar is not None and "latent" in t.sidecar:
                    sc = dict(t.sidecar)
                    sc.pop("latent")
                    sc["latent_pruned"] = T.now()
                    T.write_json(t.paths.sidecar, sc)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("episode", help="episode folder")
    ap.add_argument("--proxy", action="store_true", help="the proxy pass's takes and cut")
    ap.add_argument("--only", help="comma-separated shot ids")
    ap.add_argument("--take", type=int, help="this take number instead of the cut's")
    ap.add_argument("--redo", action="store_true", help="upscale again even if fresh")
    ap.add_argument("--scale", type=float, help="default: the target's (2)")
    ap.add_argument("--start-step", type=int,
                    help="where the re-sample starts, a step of the take's schedule "
                         "(default 7/8 of the way: step 7 of 8, which keeps the take's "
                         "performance; 6 or 5 of 8 add detail and change more)")
    ap.add_argument("--method", choices=METHODS,
                    help="latent (re-sample; the default where the target has one) or pixel "
                         "(an upscale model over the frames; any target)")
    ap.add_argument("--pixel-model", help=f"the pixel method's model (default {DEFAULT_PIXEL_MODEL})")
    ap.add_argument("--then-pixel", metavar="MODEL",
                    help="after a re-sample, an upscale model takes it on by --then-scale "
                         "(e.g. re-sample 2x then RealESRGAN_x2.pth: 4x)")
    ap.add_argument("--then-scale", type=float, help="the --then-pixel step's scale (default 2)")
    ap.add_argument("--detail", type=int, choices=DETAILS,
                    help="latent: start 0-2 steps earlier than the default (more detail, more change)")
    ap.add_argument("--vae", action="store_true",
                    help="encode the take's frames even if it kept a latent")
    ap.add_argument("--check", action="store_true", help="list the jobs, queue nothing")
    ap.add_argument("--prune-latents", action="store_true",
                    help="delete the latents of the pass's takes its cut doesn't use, and of takes "
                         "whose upscale is fresh (with --check: only list them)")
    ap.add_argument("--comfy", default="http://127.0.0.1:8188")
    ap.add_argument("--timeout", type=int, default=3600)
    args = ap.parse_args(argv)

    root = os.path.abspath(args.episode)
    TG.add_thread_root(root)
    only = {s.strip() for s in args.only.split(",")} if args.only else None
    pass_ = "proxy" if args.proxy else PASS
    if args.prune_latents:
        gone = prune_latents(root, only, dry_run=args.check, pass_=pass_)
        for path, _, why in gone:
            print(f"  {'-' if args.check else 'x'}  {rel(root, path)}  ({why})")
        mb = sum(n for _, n, _ in gone) / 1e6
        print(f"\n  {len(gone)} latent(s), {mb:.1f} MB"
              + (" would be deleted" if args.check else " deleted"))
        return 0
    jobs = []
    for shot, take, why in cut_takes(root, only, args.take, pass_=pass_):
        if take is None:
            print(f"  -  {shot}: {why}")
            continue
        up = plan_upscale(root, take, scale=args.scale, start_step=args.start_step,
                          route="vae" if args.vae else None, redo=args.redo,
                          method=args.method, pixel_model=args.pixel_model, detail=args.detail,
                          then_model=args.then_pixel, then_scale=args.then_scale)
        mark = {"upscale": "..", "skip": "= ", "error": "!!"}[up.action]
        what = describe(up) if up.action == "upscale" else up.why
        print(f"  {mark} {up.label}: {what}")
        jobs.append(up)
    todo = [j for j in jobs if j.action == "upscale"]
    print(f"\n  {len(todo)} to upscale")
    if args.check or not todo:
        return 0

    comfy = J.Comfy(args.comfy)
    try:
        info = comfy.object_info()
    except Exception as e:
        print(f"  !! cannot reach ComfyUI at {args.comfy}: {e}")
        return 1
    missing = not_ready(todo, info)
    if missing:
        print("  !! this ComfyUI can't run these upscales:")
        for m in missing:
            print(f"       {m}")
        return 1
    bases: dict = {}
    done = failed = 0
    t_all = time.time()
    for up in todo:
        t0 = time.time()
        try:
            g = graph_of(up, bases, args.comfy)
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
