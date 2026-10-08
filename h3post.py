#!/usr/bin/env python3
"""
h3post.py — the post pass: an upscale, finished (docs/POST_PROCESSING.md).

A post is a version of the upscale, the way an upscale is a version of its
take: <stem>.post.mp4 beside it, with <stem>.post.json. It is made from the
take's fresh .up.mp4, at the same size and frame count, its audio the
upscale's own stream copied on, and it is stale when that upscale changes.

    python h3.py post <episode> [--proxy] [--only sh760,sh770] [--take N] [--redo]
                      [--enhance TIER|METHOD] [--strength 0.2] [--blur 0.25]
                      [--recipe] [--check]

Two steps, in order, each optional:

- enhance (the whole frame): `pixel` (an upscale model run at 1x, its colour
  and tone the source's: a sharpen), `seedvr2` (3b or 7b at the same size: a
  video restoration) or `supir` (SUPIR on SDXL, a low denoise, one small batch
  of frames at a time, its colour and tone the source's). The tiers are names
  for these: draft (pixel), production (SeedVR2 7B, fixed 61-frame chunks
  crossfaded over 6), cinematic (SUPIR 0.2).
- motion blur: shutter blur along each pixel's optical flow (H3MotionBlur),
  `--blur` the fraction of the frame interval (0.5: a 180-degree shutter).

Without --enhance / --blur (or with --recipe): the series config's
`post.master` recipe, each shot's own over it (overrides.json's "post").
The face pass will be a third step once its bake-off settles it.

Stdlib only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from dataclasses import dataclass, field

import h3jobs as J
import h3takes as T
import h3upscale as U

PASS = "final"
METHODS = ("pixel", "seedvr2", "supir")
# production: chosen on ep01 (2026-10-06). 7B was cleaner than 3B and no slower
# (2.9 min against 4.4 for a 5 s shot at 1080p); fixed 61-frame chunks with a
# 6-frame crossfade drifted least, and auto chunks follow the free VRAM, so a
# redo would split the shot elsewhere
TIERS = {
    "draft":      {"method": "pixel"},
    "production": {"method": "seedvr2", "seedvr2_model": "7b", "chunk": "fit", "overlap": 6},
    "cinematic":  {"method": "supir", "strength": 0.2},
}
NONE = ("none", "off")

# SUPIR (ComfyUI core's SUPIRApply): its weights in models/model_patches/ (the
# pruned fp16 checkpoint; v0F is trained for light degradation, v0Q for heavy),
# on an SDXL checkpoint. Prompts are SUPIR's own defaults, shortened.
SUPIR_MODEL = "SUPIR-v0F_fp16.safetensors"
SUPIR_CHECKPOINT = "sd_xl_base_1.0_0.9vae.safetensors"
SUPIR_PROMPT = ("cinematic film still, high quality, highly detailed, sharp focus, "
                "natural skin texture, fine film grain")
SUPIR_NEGATIVE = ("painting, oil painting, illustration, drawing, cartoon, CG style, 3D render, "
                  "blurry, dirty, messy, worst quality, low quality, watermark, jpeg artifacts, "
                  "deformed, lowres, over-smooth")
SUPIR_STEPS = 10
SUPIR_CFG = 4.0
SUPIR_BATCH = 2
STRENGTH = (0.05, 0.5)                   # SUPIR's denoise: a clean-up, not a redraw
BLUR = (0.0, 1.0)
# SeedVR2's `chunk: "fit"`: 61 frames at 1080p (what fit on a 32 GB card with the
# 7B loaded), fewer as the frame grows, by its pixel count; fixed for a size, so a
# redo splits the shot at the same frames
FIT_CHUNK, FIT_PIXELS = 61, 1920 * 1088


def fit_chunk(width: int, height: int) -> int:
    """The largest 4n+1 frame count holding FIT_CHUNK frames' worth of 1080p pixels."""
    n = int(FIT_CHUNK * FIT_PIXELS / max(1, width * height))
    return max(5, (n - 1) // 4 * 4 + 1)

# torchvision's RAFT-large weights, which ComfyUI's OpticalFlowLoader reads
# from models/optical_flow/ (it never downloads them)
RAFT_FILE = "raft_large_C_T_SKHT_V2-ff5fadd5.pth"
RAFT_URL = "https://download.pytorch.org/models/raft_large_C_T_SKHT_V2-ff5fadd5.pth"
SUPIR_URL = "https://huggingface.co/Kijai/SUPIR_pruned/resolve/main/" + SUPIR_MODEL

RECIPE_FIELDS = ("enhance", "motion_blur")
ENHANCE_FIELDS = ("method", "pixel_model", "seedvr2_model", "chunk", "overlap", "strength",
                  "supir_model",
                  "checkpoint", "steps", "cfg", "prompt", "batch", "restore_cfg",
                  "frequency_split", "keep_soft", "grain")


class PostError(ValueError):
    pass


@dataclass
class PostJob:
    root: str
    take: T.Take
    enhance: dict | None                 # resolved: {"method": ..., every field}
    blur: float
    width: int = 0
    height: int = 0
    fps: float = 24.0
    action: str = "post"                 # post | skip | error
    why: str = ""
    encoder: str = "auto"
    quality: str = "review"
    notes: list = field(default_factory=list)

    @property
    def shot(self) -> str:
        return self.take.shot

    @property
    def label(self) -> str:
        return f"{self.shot} t{self.take.take:02d}"


def rel(root: str, path: str) -> str:
    return U.rel(root, path)


# ---------------------------------------------------------------------------
# what a post does
# ---------------------------------------------------------------------------

def resolve_enhance(enhance) -> dict | None:
    """A tier name, a method name, a dict (`tier` or `method` plus fields) or
    None / "none": the enhance step with every field filled in, or None."""
    if enhance is None or enhance in NONE or enhance == {}:
        return None
    if isinstance(enhance, str):
        enhance = {"tier": enhance} if enhance in TIERS else {"method": enhance}
    if not isinstance(enhance, dict):
        raise PostError(f"enhance {enhance!r}: a tier ({', '.join(TIERS)}), a method "
                        f"({', '.join(METHODS)}) or an object")
    e = dict(enhance)
    tier = e.pop("tier", None)
    if tier is not None:
        if tier not in TIERS:
            raise PostError(f"enhance tier {tier!r}: it's {', '.join(TIERS)}")
        e = {**TIERS[tier], **e}
    bad = sorted(set(e) - set(ENHANCE_FIELDS) - {"_note"})
    if bad:
        raise PostError(f"enhance: not a field: {', '.join(bad)}")
    m = e.get("method")
    if m not in METHODS:
        raise PostError(f"enhance method {m!r}: it's {', '.join(METHODS)}")
    out = {"method": m, "frequency_split": bool(e.get("frequency_split", True)),
           "keep_soft": float(e.get("keep_soft", 0.0)), "grain": float(e.get("grain", 0.0))}
    if tier:
        out["tier"] = tier
    if m == "pixel":
        out["pixel_model"] = e.get("pixel_model") or U.DEFAULT_PIXEL_MODEL
    elif m == "seedvr2":
        out["seedvr2_model"] = U.seedvr2_file(e.get("seedvr2_model"))
        # fixed chunks: auto sizes them to the free VRAM, so a redo can split the
        # shot elsewhere and a face shift at different frames
        chunk, overlap = e.get("chunk", 0), int(e.get("overlap", 2))
        if chunk != "fit":
            chunk = int(chunk)
            if chunk and (chunk < 5 or (chunk - 1) % 4):
                raise PostError(f"SeedVR2 chunk {chunk}: 0 (auto), \"fit\" or a 4n+1 frame "
                                f"count (41, 49, 61...)")
        if not 0 <= overlap <= 16:
            raise PostError(f"SeedVR2 overlap {overlap}: 0 to 16 latent frames")
        if chunk:
            out["chunk"] = chunk
        if overlap != 2:
            out["overlap"] = overlap
    else:
        s = float(e.get("strength", 0.2))
        if not STRENGTH[0] <= s <= STRENGTH[1]:
            raise PostError(f"SUPIR strength {s:g}: {STRENGTH[0]:g} to {STRENGTH[1]:g} "
                            f"(a clean-up, not a redraw)")
        out.update(strength=s, supir_model=e.get("supir_model") or SUPIR_MODEL,
                   checkpoint=e.get("checkpoint") or SUPIR_CHECKPOINT,
                   steps=int(e.get("steps", SUPIR_STEPS)), cfg=float(e.get("cfg", SUPIR_CFG)),
                   prompt=e.get("prompt") or SUPIR_PROMPT,
                   batch=max(1, int(e.get("batch", SUPIR_BATCH))),
                   # SUPIR's pull toward the input; 0 skips its extra VAE round trip
                   restore_cfg=float(e.get("restore_cfg", 4.0)))
    return out


def check_blur(v) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not BLUR[0] <= v <= BLUR[1]:
        raise PostError(f"motion_blur {v!r}: {BLUR[0]:g} to {BLUR[1]:g} of the frame interval "
                        f"(0.5 is a 180-degree shutter)")
    return float(v)


def settings_of(job: PostJob) -> dict:
    """What decides the post's picture (the record's `recipe`)."""
    d: dict = {"size": [job.width, job.height]}
    if job.enhance:
        d["enhance"] = {k: v for k, v in job.enhance.items() if k != "tier"}
    if job.blur > 0:
        d["motion_blur"] = job.blur
    if job.quality != "review":
        d["quality"] = job.quality
    return d


def settings_hash(settings: dict) -> str:
    return hashlib.sha1(json.dumps(settings, sort_keys=True).encode()).hexdigest()[:12]


def describe_recipe(kw: dict) -> str:
    """recipe_for's arguments in words ("SeedVR2 7b, then motion blur 0.3"), "" for none."""
    try:
        e = resolve_enhance(kw.get("enhance"))
        blur = check_blur(kw.get("blur") or 0.0)
    except PostError as err:
        return f"(post: {err})"
    parts = []
    if e:
        parts.append({"pixel": "pixel", "seedvr2": f"SeedVR2 {e.get('seedvr2_model', '')}",
                      "supir": f"SUPIR {e.get('strength', '')}"}[e["method"]]
                     .replace("seedvr2_", "").replace("_int8_convrot.safetensors", ""))
    if blur > 0:
        parts.append(f"motion blur {blur:g}")
    return ", then ".join(parts)


def describe(job: PostJob) -> str:
    parts = []
    e = job.enhance
    if e:
        tier = f"{e['tier']}: " if e.get("tier") else ""
        if e["method"] == "pixel":
            parts.append(f"{tier}pixel ({e['pixel_model']}) at 1x")
        elif e["method"] == "seedvr2":
            chunks = (f", {e['chunk']}-frame chunks" if e.get("chunk") else "") + (
                f", overlap {e['overlap']}" if "overlap" in e else "")
            parts.append(f"{tier}SeedVR2 ({e['seedvr2_model']}) at 1x{chunks}")
        else:
            parts.append(f"{tier}SUPIR {e['strength']:g} ({e['supir_model']}, {e['steps']} steps)")
    if job.blur > 0:
        parts.append(f"motion blur {job.blur:g}")
    return ", then ".join(parts) + f" -> {job.width}x{job.height}"


def plan_post(root: str, take: T.Take, *, enhance=None, blur: float = 0.0, redo: bool = False,
              encoder: str = "auto", quality: str | None = None,
              after_upscale: tuple | None = None) -> PostJob:
    """What the post pass would do with `take`: post it, skip it (a fresh post
    with these settings) or refuse (no fresh upscale; nothing asked). `quality`
    None: the upscale's own (a master upscale isn't finished into a lossier file).
    `after_upscale` (W, H): the post of an upscale queued but not made yet (master
    queues it right behind): planned at that size without one, never skipped."""
    job = PostJob(root=root, take=take, enhance=None, blur=0.0, encoder=encoder,
                  quality=quality or "review")
    try:
        job.enhance = resolve_enhance(enhance)
        job.blur = check_blur(blur or 0.0)
    except PostError as e:
        job.action, job.why = "error", str(e)
        return job
    if quality is not None and quality not in U.QUALITIES:
        job.action, job.why = "error", f"quality {quality!r}: it's {' or '.join(U.QUALITIES)}"
        return job
    if not job.enhance and job.blur <= 0:
        job.action, job.why = "error", "nothing to do (no enhance, no motion blur)"
        return job
    if after_upscale:
        job.width, job.height = (int(v) for v in after_upscale)
        job.fps = float((take.sidecar or {}).get("fps") or 24)
        if job.enhance and job.enhance.get("chunk") == "fit":
            job.enhance["chunk"] = fit_chunk(job.width, job.height)
        return job
    up = T.upscale_of(take)
    if quality is None and up:
        job.quality = up.get("quality") if up.get("quality") in U.QUALITIES else "review"
    if not up or not up.get("fresh"):
        job.action = "error"
        job.why = ("no upscale yet (h3.py upscale first)" if not up
                   else "its upscale is stale or unfinished (h3.py upscale first)")
        return job
    job.width = int(up.get("width") or 0)
    job.height = int(up.get("height") or 0)
    if job.enhance and job.enhance.get("chunk") == "fit":
        job.enhance["chunk"] = fit_chunk(job.width, job.height)
    job.fps = float((take.sidecar or {}).get("fps") or 24)
    rec = T.post_of(take, up)
    if (not redo and rec and rec.get("fresh")
            and rec.get("recipe_hash") == settings_hash(settings_of(job))):
        job.action, job.why = "skip", "a fresh post with these settings"
    return job


# ---------------------------------------------------------------------------
# the graph
# ---------------------------------------------------------------------------

def seed_of(job: PostJob) -> int:
    return int((job.take.sidecar or {}).get("seed") or 0) % (1 << 50)


def finish_of(job: PostJob) -> dict:
    e = job.enhance or {}
    return {"frequency_split": e.get("frequency_split", True), "keep_soft": e.get("keep_soft", 0.0),
            "grain": e.get("grain", 0.0), "grain_seed": seed_of(job) % (1 << 32)}


def supir_nodes(g: dict, job: PostJob, images: list) -> list:
    """SUPIR on SDXL at a low denoise, one small batch of frames at a time (it
    is an image model: RebatchImages makes the batches, every node after it runs
    once a batch, H3FramesToBatch joins them), the same seed on every batch so
    every frame starts from the same noise, then its colour and tone the
    source's (H3FinishUpscale's frequency split: only its detail is kept)."""
    e = job.enhance
    g.update({
        "pp_ckpt": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": e["checkpoint"]}},
        "pp_patch": {"class_type": "ModelPatchLoader", "inputs": {"name": e["supir_model"]}},
        "pp_split": {"class_type": "RebatchImages", "inputs": {"images": images,
                                                               "batch_size": e["batch"]}},
        "pp_supir": {"class_type": "SUPIRApply", "inputs": {
            "model": ["pp_ckpt", 0], "model_patch": ["pp_patch", 0], "vae": ["pp_ckpt", 2],
            "image": ["pp_split", 0], "strength_start": 1.0, "strength_end": 1.0,
            "restore_cfg": e["restore_cfg"], "restore_cfg_s_tmin": 0.05}},
        "pp_pos": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["pp_ckpt", 1], "text": e["prompt"]}},
        "pp_neg": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["pp_ckpt", 1], "text": SUPIR_NEGATIVE}},
        "pp_enc": {"class_type": "VAEEncode", "inputs": {"pixels": ["pp_split", 0], "vae": ["pp_ckpt", 2]}},
        "pp_ks": {"class_type": "KSampler", "inputs": {
            "model": ["pp_supir", 0], "positive": ["pp_pos", 0], "negative": ["pp_neg", 0],
            "latent_image": ["pp_enc", 0], "seed": seed_of(job), "steps": e["steps"],
            "cfg": e["cfg"], "sampler_name": "dpmpp_2m", "scheduler": "karras",
            "denoise": e["strength"]}},
        "pp_dec": {"class_type": "VAEDecodeTiled", "inputs": {
            "samples": ["pp_ks", 0], "vae": ["pp_ckpt", 2], "tile_size": 1024, "overlap": 64,
            "temporal_size": 64, "temporal_overlap": 8}},
        "pp_join": {"class_type": "H3FramesToBatch", "inputs": {"images": ["pp_dec", 0]}},
        "pp_finish": {"class_type": "H3FinishUpscale", "inputs": {
            "images": ["pp_join", 0], "source": images, "chunk": 8, **finish_of(job)}},
    })
    return ["pp_finish", 0]


def post_graph(job: PostJob) -> dict:
    take, root = job.take, job.root
    g = {"pp_video": {"class_type": "H3LoadTakeVideo", "inputs": {
        "project_root": root, "video_file": rel(root, take.paths.up_mp4), "audio_file": ""}}}
    images = ["pp_video", 0]
    e = job.enhance
    if e and e["method"] == "pixel":
        g["pp_model"] = {"class_type": "UpscaleModelLoader", "inputs": {"model_name": e["pixel_model"]}}
        g["pp_pixels"] = {"class_type": "H3PixelUpscale", "inputs": {
            "images": images, "upscale_model": ["pp_model", 0], "width": job.width,
            "height": job.height, "chunk": 4, "precision": "fp16", **finish_of(job)}}
        images = ["pp_pixels", 0]
    elif e and e["method"] == "seedvr2":
        images = U.seedvr2_chain(g, images, e["seedvr2_model"], job.width, job.height,
                                 seed_of(job), finish_of(job), prefix="pp_sv_",
                                 chunk=e.get("chunk", 0), overlap=e.get("overlap", 2))
    elif e:
        images = supir_nodes(g, job, images)
    if job.blur > 0:
        g["pp_flow"] = {"class_type": "OpticalFlowLoader", "inputs": {"model_name": RAFT_FILE}}
        g["pp_blur"] = {"class_type": "H3MotionBlur", "inputs": {
            "images": images, "optical_flow": ["pp_flow", 0], "amount": job.blur,
            "samples": 9, "flow_width": 960, "max_motion": 0.08, "chunk": 8,
            "min_motion": 0.75}}
        images = ["pp_blur", 0]
    g["pp_save"] = {"class_type": "H3SaveUpscale", "inputs": {
        "images": images, "project_root": root,
        "source_mp4": rel(root, take.paths.up_mp4), "out_mp4": rel(root, take.paths.post_mp4),
        "fps": job.fps, "sidecar": rel(root, take.paths.post_sidecar), "encoder": job.encoder,
        # the upscale it's made from is stamped as it's saved: it may be queued
        # behind that upscale, which isn't there yet
        "stamp_source": True, "event": "h3pipe.post",
        **({"quality": job.quality} if job.quality != "review" else {})}}
    return g


# ---------------------------------------------------------------------------
# readiness
# ---------------------------------------------------------------------------

BASE_NODES = ("H3LoadTakeVideo", "H3SaveUpscale")
SUPIR_NODES = ("CheckpointLoaderSimple", "ModelPatchLoader", "RebatchImages", "SUPIRApply",
               "CLIPTextEncode", "VAEEncode", "KSampler", "VAEDecodeTiled", "H3FramesToBatch",
               "H3FinishUpscale")
BLUR_NODES = ("OpticalFlowLoader", "H3MotionBlur")


def not_ready(jobs: list[PostJob], object_info: dict | None) -> list[str]:
    """Why this ComfyUI can't run these posts ([] when it can)."""
    if object_info is None:
        return []
    out: list[str] = []

    def need(nodes):
        for c in nodes:
            m = f"node {c} (update ComfyUI / h3pipe's node pack and restart it)"
            if c not in object_info and m not in out:
                out.append(m)

    def file_in(node, field_, want, where, url=""):
        have = J.choices_in(object_info, node, field_)
        if have is not None and want not in have:
            m = f"{want} in models/{where}/" + (f" ({url})" if url else "")
            if m not in out:
                out.append(m)

    need(BASE_NODES)
    for job in jobs:
        e = job.enhance
        if e and e["method"] == "pixel":
            out += [m for m in U.pixel_readiness(object_info, e["pixel_model"])["missing"]
                    if m not in out]
        elif e and e["method"] == "seedvr2":
            out += [m for m in U.seedvr2_readiness(object_info, e["seedvr2_model"])["missing"]
                    if m not in out]
        elif e:
            need(SUPIR_NODES)
            file_in("ModelPatchLoader", "name", e["supir_model"], "model_patches",
                    SUPIR_URL if e["supir_model"] == SUPIR_MODEL else "")
            file_in("CheckpointLoaderSimple", "ckpt_name", e["checkpoint"], "checkpoints")
        if job.blur > 0:
            need(BLUR_NODES)
            file_in("OpticalFlowLoader", "model_name", RAFT_FILE, "optical_flow", RAFT_URL)
    return out


# ---------------------------------------------------------------------------
# the record
# ---------------------------------------------------------------------------

def queued_record(job: PostJob) -> dict:
    """The .post.json written as it's queued. The source stamp (which .up.mp4
    it was made from) is added by the saver when it's done."""
    s = settings_of(job)
    return {"shot": job.shot, "take": job.take.take, "status": "queued", "queued": T.now(),
            "comfy_prompt_id": None, "recipe": s, "recipe_hash": settings_hash(s),
            "encoder_asked": job.encoder, "quality": job.quality,
            "width": job.width, "height": job.height}


def start(job: PostJob) -> None:
    T.write_json(job.take.paths.post_sidecar, queued_record(job))


def mark_queued(job: PostJob, pid: str) -> None:
    T.update_sidecar(job.take.paths.post_sidecar, comfy_prompt_id=pid)


def mark_failed(job: PostJob, why: str) -> None:
    T.update_sidecar(job.take.paths.post_sidecar, status="failed", finished=T.now(),
                     save_notes=why)


# ---------------------------------------------------------------------------
# the recipe: series.json `post.master`, overrides.json `post`
# ---------------------------------------------------------------------------
#
# "post": {"master": {"enhance": "production", "motion_blur": 0}}
# "post": {"master": {"enhance": {"method": "supir", "strength": 0.15}}}
# overrides.json: "post": {"sh040": {"motion_blur": 0.3}, "sh050": {"enhance": "none"}}

def master_recipe(root: str) -> dict | None:
    m = ((J.series_config(root) or {}).get("post") or {}).get("master")
    return m if isinstance(m, dict) else None


def shot_recipes(root: str) -> dict:
    data = T.load_overrides(root).get("post")
    return {k: v for k, v in data.items() if isinstance(v, dict)} if isinstance(data, dict) else {}


def recipe_for(root: str, shot: str, recipe: dict | None = None,
               shots: dict | None = None) -> dict:
    """plan_post's keyword arguments for a shot: the recipe, its own over it."""
    recipe = master_recipe(root) if recipe is None else recipe
    if not recipe:
        raise PostError("the series config has no post.master recipe")
    fields = {**recipe, **((shot_recipes(root) if shots is None else shots).get(shot) or {})}
    kw = {"enhance": fields.get("enhance"), "blur": fields.get("motion_blur", 0.0)}
    for k in ("encoder", "quality"):
        if recipe.get(k) is not None:
            kw[k] = recipe[k]
    return kw


def check_recipe(recipe) -> list[str]:
    """What's wrong with a series config's `post.master` ([]: nothing)."""
    if recipe is None:
        return []
    if not isinstance(recipe, dict):
        return ["series.json post.master is not an object"]
    out = []
    known = set(RECIPE_FIELDS) | {"encoder", "quality", "_note"}
    for k in sorted(set(recipe) - known):
        out.append(f"series.json post.master.{k}: not a post field "
                   f"({', '.join(sorted(known - {'_note'}))})")
    try:
        resolve_enhance(recipe.get("enhance"))
    except PostError as e:
        out.append(f"series.json post.master: {e}")
    try:
        check_blur(recipe.get("motion_blur", 0.0))
    except PostError as e:
        out.append(f"series.json post.master: {e}")
    return out


# ---------------------------------------------------------------------------
# the command
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("episode", help="episode folder")
    ap.add_argument("--proxy", action="store_true", help="the proxy pass's takes and cut")
    ap.add_argument("--only", help="comma-separated shot ids")
    ap.add_argument("--take", type=int, help="this take number instead of the cut's")
    ap.add_argument("--redo", action="store_true", help="post again even if fresh")
    ap.add_argument("--enhance", metavar="TIER|METHOD",
                    help=f"a tier ({', '.join(TIERS)}), a method ({', '.join(METHODS)}) or none")
    ap.add_argument("--strength", type=float,
                    help=f"SUPIR's denoise, {STRENGTH[0]:g}-{STRENGTH[1]:g} (default 0.2)")
    ap.add_argument("--seedvr2-model", help="SeedVR2's model: 3b or 7b, or a file name "
                                            "(production: 7b)")
    ap.add_argument("--pixel-model", help=f"the pixel method's model (default {U.DEFAULT_PIXEL_MODEL})")
    ap.add_argument("--grain", type=float, help="film grain after the enhance step, 0-0.2")
    ap.add_argument("--blur", type=float,
                    help="motion blur, 0-1 of the frame interval (0.5: a 180-degree shutter)")
    ap.add_argument("--recipe", action="store_true",
                    help="the series config's post.master (the default when neither --enhance "
                         "nor --blur is given)")
    ap.add_argument("--encoder", choices=U.ENCODERS, default="auto")
    ap.add_argument("--quality", choices=U.QUALITIES,
                    help="how the .post.mp4 is encoded (default: as its upscale was)")
    ap.add_argument("--check", action="store_true", help="list the jobs, queue nothing")
    ap.add_argument("--comfy", default="http://127.0.0.1:8188")
    ap.add_argument("--timeout", type=int, default=3600)
    args = ap.parse_args(argv)

    root = os.path.abspath(args.episode)
    only = {s.strip() for s in args.only.split(",")} if args.only else None
    pass_ = "proxy" if args.proxy else PASS
    use_recipe = args.recipe or (args.enhance is None and args.blur is None)
    recipe = master_recipe(root) if use_recipe else None
    if use_recipe and not recipe:
        print("  !! give --enhance and/or --blur, or add a post.master recipe to the series config")
        return 2
    shots = shot_recipes(root) if recipe else {}
    jobs = []
    for shot, take, why in U.cut_takes(root, only, args.take, pass_=pass_):
        if take is None:
            print(f"  -  {shot}: {why}")
            continue
        if recipe:
            kw = recipe_for(root, shot, recipe, shots)
        else:
            enhance = args.enhance
            if enhance and enhance not in NONE:
                enhance = {"tier": enhance} if enhance in TIERS else {"method": enhance}
                for k, v in (("strength", args.strength), ("seedvr2_model", args.seedvr2_model),
                             ("pixel_model", args.pixel_model), ("grain", args.grain)):
                    if v is not None:
                        enhance[k] = v
            kw = {"enhance": enhance, "blur": args.blur or 0.0,
                  "encoder": args.encoder, "quality": args.quality}
        job = plan_post(root, take, redo=args.redo, **kw)
        mark = {"post": "..", "skip": "= ", "error": "!!"}[job.action]
        print(f"  {mark} {job.label}: {describe(job) if job.action == 'post' else job.why}")
        jobs.append(job)
    todo = [j for j in jobs if j.action == "post"]
    print(f"\n  {len(todo)} to post-process")
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
        print("  !! this ComfyUI can't run these posts:")
        for m in missing:
            print(f"       {m}")
        return 1
    done = failed = 0
    t_all = time.time()
    for job in todo:
        t0 = time.time()
        try:
            start(job)
            pid = comfy.queue(post_graph(job))
            mark_queued(job, pid)
            comfy.wait(pid, args.timeout)
            rec = T.post_of(job.take) or {}
            if rec.get("status") != "ok":
                raise RuntimeError(rec.get("save_notes") or "the post wasn't written")
            done += 1
            print(f"  -> {job.take.paths.post_mp4}  [{time.time() - t0:.1f}s]", flush=True)
        except TimeoutError as e:
            failed += 1
            print(f"  !! {job.label}: {e} (left queued in ComfyUI)", flush=True)
        except Exception as e:
            failed += 1
            if os.path.isfile(job.take.paths.post_sidecar):
                mark_failed(job, str(e)[:800])
            print(f"  !! {job.label}: {e}", flush=True)
    print(f"\n  done in {time.time() - t_all:.0f}s: {done} post-processed"
          + (f", {failed} failed" if failed else "") + "\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
