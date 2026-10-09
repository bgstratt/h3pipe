#!/usr/bin/env python3
"""
h3upscale.py — a take, refined at 2x (either pass).

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

Three methods. `latent` (the targets with an `upscale` block: every built-in
video target) re-samples as above; Wan's, with no latent upscaler, from its
frames through a pixel model. `pixel` (any target, the default for the rest) runs an
upscale model from ComfyUI's models/upscale_models (RealESRGAN, UltraSharp...)
over the take's frames and copies its audio on: fast, adds no generated detail.
`--method pixel --pixel-model RealESRGAN_x4.pth` picks it for any take.
`seedvr2` (any target) restores the frames with SeedVR2.

Stdlib only.
"""
from __future__ import annotations

import argparse
import copy
import math
import os
import re
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
    method: str = "latent"              # latent | pixel | seedvr2
    pixel_model: str = ""
    seedvr2_model: str = ""             # the SeedVR2 method's model file
    # "then pixel": after a re-sample, an upscale model takes width x height on
    # to out_width x out_height in the same job ("" / 1.0: no second step)
    then_model: str = ""
    then_scale: float = 1.0
    # what the then step is: "pixel" (then_model is an upscale model) or
    # "seedvr2" (then_model is a SeedVR2 model file)
    then_method: str = "pixel"
    # the pixel method on top of the take's existing upscale: that record (its
    # .up.mp4 is the input, replaced by the result); None: from the take
    previous: dict | None = None
    # how the .up.mp4 is written and the upscale model run (ENCODERS, PRECISIONS)
    encoder: str = "auto"
    precision: str = "fp16"
    # finishing a pixel model's output (H3PixelUpscale): colour and tone from
    # the source, invented detail faded where it was soft, grain
    frequency_split: bool = True
    keep_soft: float = 0.0
    grain: float = 0.0
    # a delivery size (W, H): the last step makes the frame, aspect kept, just
    # big enough to cover it (fit "crop") or fit inside it ("pad"), and the
    # saver crops or pads it to exactly W x H (and resizes it first when no
    # step made that size: a re-sample alone). None: the size the scale makes
    deliver: tuple | None = None
    fit: str = "crop"
    # how the .up.mp4 is encoded: review, or master (Phase 13e3: for delivery)
    quality: str = "review"
    # a long re-sample sampled in windows of `window` seconds overlapping by
    # `window_overlap` (the target's `windows`: H3ContextWindows), so a take
    # longer than a window fits the card; 0: one pass however long
    window: float = 0.0
    window_overlap: float = 0.0

    def finish_inputs(self, grain: bool = True) -> dict:
        """The finishing inputs of an H3PixelUpscale node (`grain` False: before a
        re-sample, which would take grain for noise). The grain is seeded by
        the take's seed, so a redo repeats it."""
        seed = int((self.take.sidecar or {}).get("seed") or 0) % (1 << 32)
        return {"frequency_split": self.frequency_split, "keep_soft": self.keep_soft,
                "grain": self.grain if grain else 0.0, "grain_seed": seed}

    def finish_record(self) -> dict:
        return {"frequency_split": self.frequency_split, "keep_soft": self.keep_soft,
                "grain": self.grain}
    out_width: int = 0
    out_height: int = 0

    @property
    def out_size(self) -> tuple[int, int]:
        """What the .up.mp4 is: the delivery size, else after the then-pixel step
        if there is one."""
        if self.deliver:
            return tuple(self.deliver)
        return (self.out_width or self.width, self.out_height or self.height)

    @property
    def made_size(self) -> tuple[int, int]:
        """What the last step makes, before the saver crops or pads it."""
        return (self.out_width or self.width, self.out_height or self.height)

    def save_inputs(self) -> dict:
        """H3SaveUpscale's delivery and quality inputs (only what isn't its default)."""
        out = {"quality": self.quality} if self.quality != "review" else {}
        if self.deliver:
            out.update(width=self.deliver[0], height=self.deliver[1], fit=self.fit)
        return out
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


RESAMPLE, SECOND_STAGE, PIXEL_REFINE = "resample", "second_stage", "pixel_refine"


def upscale_spec(target: "TG.Target") -> dict | None:
    """The target's `upscale` block (target.json), or None: it can't upscale.

    Three modes. `resample` (H3, LTX-2.3 ingredients; the default): the take's
    latent through a latent upscaler (`upscaler`), then the target's sampler
    from late in its schedule, conditioned at the new size (`size`: the loader's
    resolution_override, or "params": the binding's width and height).
    `second_stage` (LTX-2): the target's render graph is already two-stage (a
    latent upsampler and a short fixed schedule), so an upscale is that second
    stage run again on the take: its own `upsampler` node fed the take's latent,
    the stage's `sigmas` cut to their tail. `pixel_refine` (Wan): no latent
    upscaler, so the take's frames through a pixel model, then the sampler."""
    s = target.spec.get("upscale")
    if not isinstance(s, dict):
        return None
    mode = s.get("mode", RESAMPLE)
    if mode == RESAMPLE and s.get("upscaler"):
        return s
    if mode == SECOND_STAGE and s.get("upsampler") and s.get("steps"):
        return s
    if mode == PIXEL_REFINE and s.get("sampler"):
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


# delivery sizes by name (`deliver`); any "WxH" is taken too
DELIVER = {"1080p": (1920, 1080), "1440p": (2560, 1440), "4k": (3840, 2160)}
FITS = ("crop", "pad")


def parse_deliver(v) -> tuple | None:
    """(W, H) from "1080p" / "1440p" / "4k" / "WxH", None from None or ""."""
    if v in (None, ""):
        return None
    if isinstance(v, (list, tuple)) and len(v) == 2:
        w, h = v
    elif str(v).lower() in DELIVER:
        return DELIVER[str(v).lower()]
    else:
        m = re.fullmatch(r"\s*(\d+)\s*[xX×]\s*(\d+)\s*", str(v))
        if not m:
            raise UpscaleError(f"deliver {v!r}: it's 1080p, 1440p, 4k or WxH")
        w, h = m.groups()
    w, h = int(w), int(h)
    if w % 2 or h % 2 or not (64 <= w <= 8192 and 64 <= h <= 8192):
        raise UpscaleError(f"deliver {w}x{h}: both sides even, 64 to 8192")
    return (w, h)


def fit_size(w: int, h: int, W: int, H: int, fit: str = "crop") -> tuple[int, int]:
    """What a w x h frame is scaled to, keeping its aspect, before it is cropped
    (`crop`: it covers W x H) or padded (`pad`: it fits inside) to W x H; even
    sides. 1344x768 to 3840x2160: 3840x2196 cropped (18 rows off the top and
    bottom), or 3780x2160 padded (30 columns of black each side)."""
    if fit == "crop":
        s = max(W / w, H / h)
        return (max(W, 2 * math.ceil(w * s / 2 - 1e-9)), max(H, 2 * math.ceil(h * s / 2 - 1e-9)))
    s = min(W / w, H / h)
    return (min(W, 2 * math.floor(w * s / 2 + 1e-9)), min(H, 2 * math.floor(h * s / 2 + 1e-9)))


AUTO = "auto"                           # a recipe's `scale`: the smallest that covers `deliver`


def auto_scale(w: int, h: int, deliver: tuple | None, fit: str = "crop", align: int = 32,
               default: float = 2.0) -> float:
    """The re-sample scale "auto" means: the smallest in eighths (above 1, up to
    MAX_SCALE) whose size lands on `align` and is at least the frame `deliver`
    needs (fit_size), so the saver only scales down and crops, never stretches.
    960x544 to 1080p is 2 (1920x1088); 1344x768 is 1.5 (2016x1152, scaled to
    1920x1098 and cropped). No delivery size: `default`. UpscaleError when no
    scale up to MAX_SCALE covers it."""
    if not deliver:
        return default
    nw, nh = fit_size(w, h, *deliver, fit)
    for k in range(9, int(MAX_SCALE * 8) + 1):
        s = k / 8
        sw, sh = w * s, h * s
        if (sw == int(sw) and sh == int(sh) and not int(sw) % align and not int(sh) % align
                and sw >= nw and sh >= nh):
            return s
    raise UpscaleError(f"no re-sample scale up to {MAX_SCALE:g}x takes {w}x{h} onto the {align} grid "
                       f"at {nw}x{nh} or more: give the recipe a scale")


def align_of(spec: dict) -> int:
    return int(spec.get("align") or (spec.get("upscaler") or {}).get("align") or 32)


def fixed_scale(spec: dict) -> float | None:
    """The one scale a re-sample makes, or None: any. A second stage's
    upsampler, or a resample upscaler with no scale input (LTX's latent
    upsampler, a 2x model), makes only its own."""
    mode = spec.get("mode", RESAMPLE)
    if mode == SECOND_STAGE or (mode == RESAMPLE and spec.get("upscaler")
                                and not spec["upscaler"].get("scale")):
        return float(spec.get("scale", 2))
    return None


def upscaler_model(spec: dict) -> str | None:
    """The model file a resample's upscaler loads (in its own inputs, or its
    loader's)."""
    u = spec.get("upscaler") or {}
    return ((u.get("inputs") or {}).get("model_name")
            or ((u.get("model") or {}).get("inputs") or {}).get("model_name"))


# the nodes an upscale graph adds to the target's (besides its upscaler)
UPSCALE_NODES = ("H3LoadTakeLatent", "H3LoadTakeVideo", "H3HoldAudio", "H3SaveUpscale",
                 "LTXVSeparateAVLatent", "LTXVConcatAVLatent", "SplitSigmas", "VAEEncode",
                 "VAEEncodeAudio")
AUDIO_ENCODE = {"class_type": "VAEEncodeAudio", "audio": "audio", "vae": "vae"}

METHODS = ("latent", "pixel", "seedvr2")
PIXEL_NODES = ("H3LoadTakeVideo", "H3PixelUpscale", "UpscaleModelLoader", "H3SaveUpscale")

# SeedVR2 (ByteDance, Apache 2.0), native in ComfyUI core: a one-step video
# restoration model, any take, no prompt. Its files, as ComfyUI's own templates
# record them (utility_seedvr2_*_int8_upscale_*.json, properties.models).
SEEDVR2_MODELS = {"7b": "seedvr2_7b_int8_convrot.safetensors",
                  "3b": "seedvr2_3b_int8_convrot.safetensors"}
SEEDVR2_DEFAULT = "7b"
SEEDVR2_VAE = "seedvr2_ema_vae_fp16.safetensors"
SEEDVR2_DOWNLOADS = {
    "seedvr2_ema_vae_fp16.safetensors": ("vae", "https://huggingface.co/Comfy-Org/SeedVR2/resolve/main/vae/seedvr2_ema_vae_fp16.safetensors"),
    "seedvr2_7b_int8_convrot.safetensors": ("diffusion_models", "https://huggingface.co/Comfy-Org/SeedVR2/resolve/main/diffusion_models/seedvr2_7b_int8_convrot.safetensors"),
    "seedvr2_3b_int8_convrot.safetensors": ("diffusion_models", "https://huggingface.co/Comfy-Org/SeedVR2/resolve/main/diffusion_models/seedvr2_3b_int8_convrot.safetensors"),
}
SEEDVR2_SOURCE = "ComfyUI templates utility_seedvr2_3b_int8_upscale_video.json / utility_seedvr2_7b_int8_upscale_image.json (properties.models)"
SEEDVR2_NODES = ("H3LoadTakeVideo", "ImageScale", "SeedVR2Preprocess", "VAEEncodeTiled",
                 "SeedVR2TemporalChunk", "SeedVR2Conditioning", "KSampler", "SeedVR2TemporalMerge",
                 "VAEDecodeTiled", "SeedVR2PostProcessing", "H3FinishUpscale", "H3SaveUpscale",
                 "UNETLoader", "VAELoader")


def seedvr2_file(name: str | None) -> str:
    """A SeedVR2 model: "7b" / "3b", or a file name as it is."""
    name = name or SEEDVR2_DEFAULT
    return SEEDVR2_MODELS.get(name, name)


def seedvr2_readiness(object_info: dict | None, want: str | None = None) -> dict:
    """Whether this ComfyUI can run SeedVR2 (core nodes since 0.3x, its model and
    VAE, our finish node). {"status", "missing", "models", "default"}."""
    if object_info is None:
        return {"status": "unknown", "missing": [], "models": [], "default": seedvr2_file(None)}
    missing = [f"node {c} (update ComfyUI / h3pipe's node pack and restart it)"
               for c in SEEDVR2_NODES if c not in object_info]
    unets = J.choices_in(object_info, "UNETLoader", "unet_name") or []
    models = [m for m in unets if "seedvr2" in m.lower()]
    vaes = J.choices_in(object_info, "VAELoader", "vae_name") or []
    if SEEDVR2_VAE not in vaes:
        missing.append(f"{SEEDVR2_VAE} in models/vae/")
    want = seedvr2_file(want)
    if want not in models:
        missing.append(f"{want} in models/diffusion_models/")
    default = seedvr2_file(None) if seedvr2_file(None) in models else (models or [seedvr2_file(None)])[0]
    return {"status": "not_ready" if missing else "ready", "missing": missing,
            "models": models, "default": default}
DEFAULT_PIXEL_MODEL = "RealESRGAN_x2.pth"
# auto: the GPU's NVENC when ffmpeg has it (H.264 up to 4096 wide, HEVC above),
# else x264; nvenc forces it (fails without); x264 forces the CPU encoder
ENCODERS = ("auto", "nvenc", "x264")
# the upscale model's precision: fp16 (autocast; about twice as fast) or fp32
PRECISIONS = ("fp16", "fp32")
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
    if spec.get("mode", RESAMPLE) == PIXEL_REFINE:
        # Wan: no latent, no audio; the pixel model's pieces instead
        need = ["H3LoadTakeVideo", "H3PixelUpscale", "UpscaleModelLoader", "VAEEncode",
                "H3SaveUpscale"]
        if spec.get("reference"):
            need += ["ImageScale", "LatentConcat"]
    if spec.get("mode", RESAMPLE) == RESAMPLE and spec["upscaler"].get("model"):
        # a model-loading upscaler (LTX's latent upsampler): core nodes, and its file
        u = spec["upscaler"]
        need += [u["class_type"], u["model"]["class_type"]]
        if spec.get("trim"):
            need.append("LatentCut")
        if spec.get("sampler_tail") == "advanced":
            need.append("KSamplerAdvanced")
    missing = [f"node {c} (update h3pipe's node pack, or ComfyUI, and restart it)"
               for c in need if c not in object_info]
    if spec.get("mode", RESAMPLE) == RESAMPLE and spec["upscaler"].get("model"):
        m = spec["upscaler"]["model"]
        want = (m.get("inputs") or {}).get("model_name")
        choices = J.choices_in(object_info, m["class_type"], "model_name")
        if want and choices is not None and want not in choices:
            missing.append(f"{want} in models/latent_upscale_models/")
    elif spec.get("mode", RESAMPLE) == RESAMPLE:
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


# ---------------------------------------------------------------------------
# The master recipe (Phase 13e): series.json `upscale.master`
# ---------------------------------------------------------------------------
#
# "upscale": {"master": {
#     "deliver": "4k", "fit": "crop", "quality": "master", "encoder": "auto",
#     "finish": {"frequency_split": true, "keep_soft": 0, "grain": 0},
#     "targets": {"minimax_h3_*": {"method": "latent", "then": "RealESRGAN_x2.pth"},
#                 "wan22_*": {"method": "seedvr2"},
#                 "*": {"method": "pixel"}}}}
#
# and per shot, overrides.json's top-level "upscale": {"sh020": {"detail": 1}}
# (merged over its target's section). A target key is an id or a glob; the
# exact id wins, then the longest matching glob.

# the whole master's (one size, one encoding)
RECIPE_GLOBAL = ("deliver", "fit", "quality", "encoder")
# a target section's, or a shot's
RECIPE_FIELDS = ("method", "scale", "detail", "start_step", "vae", "pixel_model",
                 "seedvr2_model", "then", "then_scale", "precision",
                 "frequency_split", "keep_soft", "grain", "window")
WINDOW_RANGE = (2.0, 30.0)          # a re-sample window, seconds (0: off)
QUALITIES = ("review", "master")


def master_recipe(root: str) -> dict | None:
    """The series config's `upscale.master` (None: it has none)."""
    m = ((J.series_config(root) or {}).get("upscale") or {}).get("master")
    return m if isinstance(m, dict) else None


def recipe_section(recipe: dict, target_id: str) -> tuple[str | None, dict]:
    """(the key that matched, its section) of `recipe.targets` for a target:
    the exact id, else the longest matching glob; (None, {}) when none."""
    import fnmatch
    targets = recipe.get("targets") or {}
    if isinstance(targets.get(target_id), dict):
        return target_id, targets[target_id]
    hits = [k for k, v in targets.items()
            if isinstance(v, dict) and k != target_id and fnmatch.fnmatchcase(target_id, k)]
    if not hits:
        return None, {}
    best = max(hits, key=lambda k: (len(k.replace("*", "")), len(k)))
    return best, targets[best]


def shot_recipes(root: str) -> dict:
    """overrides.json's per-shot upscale recipes: {shot: {field: value}}."""
    data = T.load_overrides(root).get("upscale")
    return {k: v for k, v in data.items() if isinstance(v, dict)} if isinstance(data, dict) else {}


def set_shot_recipe(root: str, shot: str, fields: dict | None) -> dict:
    """Set (a dict of RECIPE_FIELDS) or clear (None / {}) one shot's upscale
    recipe in overrides.json. Returns what the shot has now."""
    bad = sorted(set(fields or {}) - set(RECIPE_FIELDS))
    if bad:
        raise UpscaleError(f"not a per-shot upscale field: {', '.join(bad)}")
    data = T.load_overrides(root)
    ups = data.get("upscale") if isinstance(data.get("upscale"), dict) else {}
    if fields:
        ups[shot] = dict(fields)
    else:
        ups.pop(shot, None)
    if ups:
        data["upscale"] = ups
    else:
        data.pop("upscale", None)
    T.save_overrides(root, data)
    return ups.get(shot) or {}


def recipe_for(root: str, take: T.Take, recipe: dict | None = None,
               shots: dict | None = None) -> dict:
    """The plan_upscale keyword arguments the master recipe gives a take: its
    target's section, the shot's override over it, the finish and the master's
    size and encoding. UpscaleError when the series has no recipe, or none for
    this take's target."""
    recipe = master_recipe(root) if recipe is None else recipe
    if not recipe:
        raise UpscaleError("the series config has no upscale.master recipe")
    target_id = (take.sidecar or {}).get("target") or T.DEFAULT_TARGET
    key, section = recipe_section(recipe, target_id)
    if key is None:
        raise UpscaleError(f"upscale.master has no section for {target_id} (add it, or \"*\")")
    fields = {**(recipe.get("finish") or {}), **section,
              **((shot_recipes(root) if shots is None else shots).get(take.shot) or {})}
    return recipe_kwargs(fields, recipe)


def recipe_kwargs(fields: dict, recipe: dict | None = None) -> dict:
    """RECIPE_FIELDS (and the master's RECIPE_GLOBAL) as plan_upscale's kwargs."""
    g = recipe or {}
    kw = {k: fields[k] for k in ("method", "scale", "detail", "start_step", "pixel_model",
                                 "seedvr2_model", "then_scale", "precision",
                                 "frequency_split", "keep_soft", "grain", "window")
          if k in fields}
    if fields.get("vae"):
        kw["route"] = "vae"
    then = fields.get("then")
    if then == "seedvr2":
        kw["then_method"] = "seedvr2"
    elif then:
        kw["then_model"] = then
    for k in ("deliver", "fit", "encoder", "quality"):
        if g.get(k) is not None:
            kw[k] = g[k]
    return kw


def recipe_from_request(body: dict) -> dict:
    """The upscale request's choices (POST /h3pipe/upscale's fields) as a
    per-shot recipe (RECIPE_FIELDS): what the dialog saves for one shot."""
    out = {}
    for k in ("method", "scale", "detail", "start_step", "pixel_model", "seedvr2_model",
              "then_scale", "precision", "frequency_split", "keep_soft", "grain", "window"):
        if body.get(k) is not None:
            out[k] = body[k]
    if body.get("vae"):
        out["vae"] = True
    if body.get("then_method") == "seedvr2":
        out["then"] = "seedvr2"
    elif body.get("then_pixel_model"):
        out["then"] = body["then_pixel_model"]
    return out


def describe_recipe(fields: dict, recipe: dict | None = None) -> str:
    """A recipe section in words, for the dialog and the master's report."""
    m = fields.get("method") or "latent"
    if m == "latent":
        sc = fields.get('scale', 2)
        s = ("re-sample at the smallest scale that covers the size" if sc == AUTO
             else f"re-sample {sc:g}x")
        if fields.get("detail"):
            s += f" (detail {fields['detail']})"
        then = fields.get("then")
        if then:
            s += f", then {'SeedVR2 ' + str(fields.get('seedvr2_model') or SEEDVR2_DEFAULT) if then == 'seedvr2' else then}"
    elif m == "seedvr2":
        s = f"SeedVR2 {fields.get('seedvr2_model') or SEEDVR2_DEFAULT}"
    else:
        s = f"pixel {fields.get('pixel_model') or DEFAULT_PIXEL_MODEL}"
    g = recipe or {}
    if g.get("deliver"):
        s += f" → {g['deliver']} ({g.get('fit') or 'crop'})"
    return s


def check_recipe(recipe) -> list[str]:
    """What's wrong with a series config's `upscale.master` ([]: nothing), for
    the build's warnings. Model files aren't checked here: readiness does that
    against the running ComfyUI when an upscale is queued."""
    if recipe is None:
        return []
    if not isinstance(recipe, dict):
        return ["series.json upscale.master is not an object"]
    out = []
    known = set(RECIPE_GLOBAL) | {"targets", "finish", "_note"}
    for k in sorted(set(recipe) - known):
        out.append(f"series.json upscale.master.{k}: not a recipe field "
                   f"({', '.join(sorted(known - {'_note'}))})")
    try:
        parse_deliver(recipe.get("deliver"))
    except UpscaleError as e:
        out.append(f"series.json upscale.master: {e}")
    if recipe.get("fit", "crop") not in FITS:
        out.append(f"series.json upscale.master.fit {recipe['fit']!r}: it's crop or pad")
    if recipe.get("quality", "review") not in QUALITIES:
        out.append(f"series.json upscale.master.quality {recipe['quality']!r}: it's review or master")
    if recipe.get("encoder", "auto") not in ENCODERS:
        out.append(f"series.json upscale.master.encoder {recipe['encoder']!r}: it's "
                   f"{', '.join(ENCODERS)}")
    targets = recipe.get("targets")
    if not isinstance(targets, dict) or not targets:
        out.append("series.json upscale.master.targets: give at least one target section "
                   "(an id or a glob such as \"minimax_h3_*\", or \"*\")")
        targets = {}
    sections = [(f"targets.{k}", v) for k, v in targets.items()]
    if "finish" in recipe:
        sections.append(("finish", recipe["finish"]))
    for where, sec in sections:
        if not isinstance(sec, dict):
            out.append(f"series.json upscale.master.{where} is not an object")
            continue
        out += [f"series.json upscale.master.{where}: {m}" for m in check_fields(sec)]
    return out


def check_fields(sec: dict) -> list[str]:
    """What's wrong with one recipe section or per-shot recipe."""
    out = []
    for k in sorted(set(sec) - set(RECIPE_FIELDS) - {"_note"}):
        out.append(f"{k} is not a recipe field")
    if sec.get("method", "latent") not in METHODS:
        out.append(f"method {sec['method']!r}: it's {', '.join(METHODS)}")
    if sec.get("scale") == AUTO and (sec.get("method", "latent") != "latent" or sec.get("then")):
        out.append('scale "auto": only for a re-sample alone (method latent, no then)')
    for k, lo, hi in (("scale", 1, MAX_SCALE), ("then_scale", 1, MAX_SCALE)):  # "auto" above
        v = sec.get(k)
        if k == "scale" and v == AUTO:
            continue
        if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float)) or not lo < v <= hi):
            out.append(f"{k} {v!r}: more than {lo:g}, up to {hi:g}")
    if sec.get("detail") not in (None, *DETAILS):
        out.append(f"detail {sec['detail']!r}: it's 0, 1 or 2")
    if sec.get("precision", "fp16") not in PRECISIONS:
        out.append(f"precision {sec['precision']!r}: it's fp16 or fp32")
    for k, hi in (("keep_soft", 1.0), ("grain", 0.2)):
        v = sec.get(k)
        if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v <= hi):
            out.append(f"{k} {v!r}: 0 to {hi:g}")
    if sec.get("then") and sec.get("method", "latent") != "latent":
        out.append("then: only after a re-sample (method latent)")
    w = sec.get("window")
    if w is not None and (isinstance(w, bool) or not isinstance(w, (int, float))
                          or not (w == 0 or WINDOW_RANGE[0] <= w <= WINDOW_RANGE[1])):
        out.append(f"window {w!r}: 0 (off), or {WINDOW_RANGE[0]:g} to {WINDOW_RANGE[1]:g} seconds")
    return out


def plan_upscale(root: str, take: T.Take, *, encoder: str = "auto", precision: str = "fp16",
                 frequency_split: bool = True, keep_soft: float = 0.0, grain: float = 0.0,
                 deliver=None, fit: str = "crop", respect_keep: bool = False,
                 quality: str = "review", window: float | None = None, **kw) -> UpscaleJob:
    """_plan (below), with how the result is encoded (`encoder`) and the upscale
    model run (`precision`), both checked, and sized to `deliver` (see
    deliver_to). A `scale` of "auto" is worked out here, per take (auto_scale)."""
    if kw.get("scale") == AUTO:
        try:
            kw["scale"] = resolve_auto_scale(root, take, deliver, fit or "crop", kw)
        except UpscaleError as e:
            job = _plan(root, take, **{**kw, "scale": None})
            job.action, job.why = "error", str(e)
            return job
    job = _plan(root, take, **kw)
    if respect_keep and job.action == "upscale" and kept(take):
        job.action, job.why = "skip", "kept (its upscale is marked Keep)"
    if job.action != "error" and (deliver not in (None, "") or fit != "crop"):
        try:
            deliver_to(job, parse_deliver(deliver), fit or "crop")
        except UpscaleError as e:
            job.action, job.why = "error", str(e)
    job.encoder, job.precision = encoder or "auto", precision or "fp16"
    job.frequency_split = bool(frequency_split)
    job.keep_soft, job.grain = float(keep_soft or 0), float(grain or 0)
    if job.action != "error" and not 0 <= job.keep_soft <= 1:
        job.action, job.why = "error", f"keep_soft {keep_soft}: it's 0 to 1"
    if job.action != "error" and not 0 <= job.grain <= 0.2:
        job.action, job.why = "error", f"grain {grain}: it's 0 to 0.2"
    if job.action != "error" and job.encoder not in ENCODERS:
        job.action, job.why = "error", f"encoder {encoder!r}: it's {', '.join(ENCODERS)}"
    if job.action != "error" and job.precision not in PRECISIONS:
        job.action, job.why = "error", f"precision {precision!r}: it's fp16 or fp32"
    job.quality = quality or "review"
    if job.action != "error" and job.quality not in QUALITIES:
        job.action, job.why = "error", f"quality {quality!r}: it's review or master"
    if job.method == "latent" and job.action != "error":
        problems = check_fields({"window": window}) if window is not None else []
        if problems:
            job.action, job.why = "error", problems[0]
        else:
            set_window(job, window)
    return job


def take_seconds(take: T.Take) -> float:
    """How long a take is, from its sidecar's frames and rate (0: unknown)."""
    sc = take.sidecar or {}
    n, fps = int(sc.get("length") or 0), float(sc.get("fps") or 24)
    return n / fps if n and fps else 0.0


def set_window(job: UpscaleJob, window: float | None = None) -> None:
    """Window the job's re-sample when its take is longer than one window: the
    recipe's `window` (seconds, 0: off), else the target's `windows.seconds`.
    A re-sample at 2x holds four times the tokens of the take: 1344x768 x2 is
    2688x1536, about twice the pixels of the 1920x1088 obvpm measured at ~7 GB
    of activations per 5 s window, so a long shot runs out of memory on one
    pass. The target says how (its `windows`); the job only says how long."""
    spec = job.spec.get("windows") or {}
    if not spec or job.spec.get("mode", RESAMPLE) != RESAMPLE:
        return
    w = float(spec.get("seconds", 0) if window is None else window)
    if w <= 0 or take_seconds(job.take) <= w:
        return
    job.window = w
    job.window_overlap = min(float(spec.get("overlap", w / 4)), w / 2)
    job.notes.append(f"sampled in {w:g}s windows overlapping by {job.window_overlap:g}s "
                     f"({take_seconds(job.take):.1f}s take)")


def resolve_auto_scale(root: str, take: T.Take, deliver, fit: str, kw: dict) -> float | None:
    """What "auto" is for this take: auto_scale from its size, the delivery and
    its target's grid, for a re-sample alone. A target whose upsampler is fixed
    keeps its scale; with a `then` step after the re-sample (it makes the
    delivery size), and for the pixel and SeedVR2 methods (deliver_to sizes
    those), "auto" is the target's default."""
    sc = take.sidecar or {}
    target = TG.load_target(sc.get("target") or T.DEFAULT_TARGET, "video", root=root)
    spec = upscale_spec(target) or {}
    m = kw.get("method") or ("pixel" if kw.get("from_upscale") or not spec else "latent")
    if m != "latent" or kw.get("then_model") or kw.get("then_method"):
        return None
    if fixed_scale(spec) is not None:
        return fixed_scale(spec)
    w, h = int(sc.get("width") or 0), int(sc.get("height") or 0)
    if not w or not h:
        return None                                     # _plan says what's wrong
    return auto_scale(w, h, parse_deliver(deliver), fit, align_of(spec),
                      float(spec.get("scale", 2)))


def _plan(root: str, take: T.Take, *, scale: float | None = None,
                 start_step: int | None = None, route: str | None = None,
                 redo: bool = False, method: str | None = None,
                 pixel_model: str | None = None, detail: int | None = None,
                 then_model: str | None = None, then_scale: float | None = None,
                 then_method: str | None = None,
                 from_upscale: bool = False, seedvr2_model: str | None = None) -> UpscaleJob:
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
    kw_seedvr2 = seedvr2_model
    m = method or ("pixel" if from_upscale or not spec else "latent")
    w, h = int(sc.get("width") or 0), int(sc.get("height") or 0)
    if m in ("pixel", "seedvr2"):
        job = UpscaleJob(root, take, target, spec, m, float(scale or 2), None, w, h,
                         method=m, pixel_model=pixel_model or DEFAULT_PIXEL_MODEL)
        if m == "seedvr2":
            job.pixel_model = ""
            job.seedvr2_model = seedvr2_file(kw_seedvr2)
        try:
            if not take.usable:
                raise UpscaleError(f"{job.label} isn't a finished take ({take.status})")
            if from_upscale:
                # the take's upscale is the input: it must be finished and made from
                # the take as it is now, and its size is what gets scaled
                prev = T.upscale_of(take)
                if not prev or prev.get("status") != "ok" or not prev.get("fresh"):
                    raise UpscaleError(f"{job.label} has no fresh upscale to start from")
                job.previous = prev
                w, h = int(prev.get("width") or 0), int(prev.get("height") or 0)
            if not w or not h:
                raise UpscaleError(f"{job.label}'s sidecar doesn't say its size")
            if not 1 < job.scale <= MAX_SCALE:
                raise UpscaleError(f"scale {job.scale:g}: it's more than 1, up to {MAX_SCALE:g}")
            job.width, job.height = scaled(w, h, job.scale, 2)
        except UpscaleError as e:
            job.action, job.why = "error", str(e)
            return job
        up = T.upscale_of(take)
        if up and up["fresh"] and not redo and not from_upscale:
            job.action, job.why = "skip", "already upscaled"
        return job
    if from_upscale:
        job = UpscaleJob(root, take, target, spec, "latent", 2.0, None, w, h, action="error",
                         why="only the pixel and SeedVR2 methods run on top of an upscale")
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
    refine = spec.get("mode") == PIXEL_REFINE
    r = route or ("latent" if has_latent and not refine else "vae")
    job = UpscaleJob(root, take, target, spec, r, s, step, w, h)
    if refine:
        # Wan: the take's frames through an upscale model first, then its sampler
        job.pixel_model = pixel_model or spec.get("pixel_model") or DEFAULT_PIXEL_MODEL
    try:
        if refine and r == "latent":
            raise UpscaleError(f"{target.short} re-samples from the take's frames, not a latent")
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
        if fixed_scale(spec) is not None and s != fixed_scale(spec):
            raise UpscaleError(f"{target.short}'s upsampler is fixed at {fixed_scale(spec):g}x")
        if not 1 < s <= MAX_SCALE:
            raise UpscaleError(f"scale {s:g}: it's more than 1, up to {MAX_SCALE:g}")
        job.width, job.height = scaled(w, h, s, align_of(spec))
        if then_method not in (None, "", "pixel", "seedvr2"):
            raise UpscaleError(f"then_method {then_method!r}: it's pixel or seedvr2")
        if then_method == "seedvr2":
            # SeedVR2 after the re-sample: its model, not an upscale model's
            then_model = seedvr2_file(kw_seedvr2)
            job.then_method = "seedvr2"
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


def deliver_to(job: UpscaleJob, size: tuple | None, fit: str) -> None:
    """Size a planned job to deliver `size`: the last step that can make any size
    (the pixel method, SeedVR2, a then-pixel step) makes the frame that covers
    or fits it (fit_size), its scale worked out from what it starts from; a
    re-sample alone keeps its own scale and the saver resizes. UpscaleError
    when the delivery needs a step to shrink."""
    if fit not in FITS:
        raise UpscaleError(f"fit {fit!r}: it's crop or pad")
    job.fit = fit
    if size is None:
        return
    job.deliver = size
    W, H = size
    if job.method in ("pixel", "seedvr2"):
        # the step's input: the take, or the upscale it runs on
        src = job.previous or job.take.sidecar or {}
        w, h = int(src.get("width") or 0), int(src.get("height") or 0)
        iw, ih = fit_size(w, h, W, H, fit)
        s = round(iw / w, 4)
        if not 1 < s <= MAX_SCALE:
            raise UpscaleError(f"{W}x{H} from {w}x{h} is {s:g}x: it's more than 1, up to {MAX_SCALE:g}")
        job.scale, job.width, job.height = s, iw, ih
    elif job.then_model:
        iw, ih = fit_size(job.width, job.height, W, H, fit)
        s = round(iw / job.width, 4)
        if s <= 1:
            raise UpscaleError(f"the re-sample makes {job.width}x{job.height} already: {W}x{H} "
                               f"needs no {'SeedVR2' if job.then_method == 'seedvr2' else 'upscale model'} "
                               f"after it")
        if s > MAX_SCALE:
            raise UpscaleError(f"{W}x{H} from the re-sample's {job.width}x{job.height} is {s:g}x: "
                               f"at most {MAX_SCALE:g}")
        job.then_scale, job.out_width, job.out_height = s, iw, ih
    else:
        iw, ih = fit_size(job.width, job.height, W, H, fit)
        if iw > job.width:
            job.notes.append(f"the re-sample's {job.width}x{job.height} is stretched to {iw}x{ih} "
                             f"for {W}x{H}: an upscale model after it adds detail instead")


def take_job(up: UpscaleJob) -> J.Job:
    """The render job the take was, rebuilt from its frozen shotlist and
    sidecar: graph_for then patches the target's graph exactly as for the take.
    Its staged inputs come back too (a first frame the second stage re-imposes,
    keyframes, VACE's reference, the ingredients sheet its guide re-encodes),
    except, for a second stage, a reference sheet, which only its first stage reads."""
    take, sc = up.take, up.take.sidecar or {}
    doc = T.read_json(take.paths.shotlist)
    shot = doc["shots"][0]
    first_only = ("sheet",) if up.spec.get("mode") == SECOND_STAGE else ()
    inputs = {k: v for k, v in (sc.get("inputs") or {}).items() if k not in first_only}
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
    vvae, avae = vae_link(g, up, "video_vae"), vae_link(g, up, "audio_vae")
    wav = take.paths.h3_wav if os.path.isfile(take.paths.h3_wav) else ""
    enc = up.spec.get("encode_audio") or AUDIO_ENCODE
    g["up_video"] = {"class_type": "H3LoadTakeVideo", "inputs": {
        "project_root": root, "video_file": rel(root, take.paths.mp4),
        "audio_file": rel(root, wav) if wav else ""}}
    g["up_venc"] = {"class_type": "VAEEncode", "inputs": {"pixels": ["up_video", 0],
                                                          "vae": vvae}}
    g["up_aenc"] = {"class_type": enc["class_type"], "inputs": {enc["audio"]: ["up_video", 1],
                                                                enc["vae"]: avae}}
    g["up_source"] = {"class_type": "LTXVConcatAVLatent", "inputs": {
        "video_latent": ["up_venc", 0], "audio_latent": ["up_aenc", 0]}}
    return ["up_source", 0]


def vae_link(g: dict, up: UpscaleJob, name: str) -> list:
    """A link to the graph's `name` VAE (video_vae / audio_vae): the spec's own
    {class_type, output} (a checkpoint's third output), else the binding's
    param of that name (a VAELoader)."""
    s = up.spec.get(name)
    if s:
        return [J.node_of(g, s["class_type"]), int(s.get("output", 0))]
    return [J.select_nodes(g, up.target.binding.specs(name)[0])[0], 0]


def sampler_tail(g: dict, sampler: str, start: int) -> None:
    """Start `sampler` at step `start` of its own schedule. A KSampler becomes
    the KSamplerAdvanced it is plus a start (same schedule, same seed), since
    its denoise would stretch the schedule, not cut it."""
    n = g[sampler]
    if n["class_type"] == "KSampler":
        i = n["inputs"]
        n["class_type"] = "KSamplerAdvanced"
        n["inputs"] = {**{k: v for k, v in i.items() if k not in ("seed", "denoise")},
                       "noise_seed": i["seed"], "add_noise": "enable", "start_at_step": start,
                       "end_at_step": 10000, "return_with_leftover_noise": "disable"}
    elif n["class_type"] == "KSamplerAdvanced":
        n["inputs"].update(add_noise="enable", start_at_step=start, end_at_step=10000,
                           return_with_leftover_noise="disable")
    else:
        raise UpscaleError(f"can't start a {n['class_type']} late")


def upscale_graph(base: dict, up: UpscaleJob) -> dict:
    """The target's render graph (API form) turned into this upscale. Both modes:
    the take's latent in (take_source), its audio held (H3HoldAudio), its
    schedule started late, and H3SaveUpscale writing <stem>.up.mp4 with the
    take's audio copied on.

    - resample (H3, LTX-2.3 ingredients): the conditioning is rebuilt at the
      new size (the loader reads the take's frozen shotlist at it; or the
      width/height params: an I2V node's keyframes, LTX's guide image), so the
      prompt and references are encoded as for the take; the video goes
      through the upscaler (the external H3 pack, or a model-loading one: LTX's
      latent upsampler), into the graph's own guide node when it has one
      (`into`: LTX's IC-LoRA guide is appended to it again); SplitSigmas (or a
      KSampler's start step) starts the sampler's own schedule at `start_step`.
    - second_stage (LTX-2): the final stage's own upsampler takes the take's
      video, its join takes the take's audio, and its fixed sigmas are cut to
      their tail from `start_step`; the first stage falls away in the prune."""
    t, spec, take = up.target, up.spec, up.take
    b = t.binding
    g = J.graph_for(base, take_job(up), take, review_copy=False)
    saver = J.node_of(g, b.saver_class)
    sampler = J.latent_node(g, saver, spec.get("sampler") or b.saver["latent"])
    si = g[sampler]["inputs"]

    if spec.get("mode") == PIXEL_REFINE:
        pixel_refine(g, up, sampler)
        images = g[saver]["inputs"]["images"]
        del g[saver]
        return finish_graph(g, up, images)

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
        resample(g, up, sampler)

    images = g[saver]["inputs"]["images"]
    del g[saver]
    return finish_graph(g, up, images)


def resample(g: dict, up: UpscaleJob, sampler: str) -> None:
    """upscale_graph's resample mode, from the take's latent split in two
    (`up_split_av`)."""
    spec, b, take = up.spec, up.target.binding, up.take
    si = g[sampler]["inputs"]
    size = spec.get("size") or {"class_type": b.loader_class, "field": "resolution_override"}
    if size == "params":
        for name, value in (("width", up.width), ("height", up.height)):
            J.patch_param(g, b, name, value)
    else:
        for nid in J.select_nodes(g, size):
            g[nid]["inputs"][size["field"]] = f"{up.width}x{up.height}"
    video = ["up_split_av", 0]
    if spec.get("trim") and up.route == "latent":
        # a kept latent is the sampler's, guide frames and all: the take's own
        # frames are the first (length - 1) / temporal + 1
        n = int((take.sidecar or {}).get("length") or 0)
        g["up_trim"] = {"class_type": "LatentCut", "inputs": {
            "samples": video, "dim": "t", "index": 0,
            "amount": (n - 1) // int(spec["trim"]["temporal"]) + 1 if n else 4096}}
        video = ["up_trim", 0]
    u = spec["upscaler"]
    g["up_scale"] = {"class_type": u["class_type"], "inputs": {
        **copy.deepcopy(u.get("inputs") or {}), u.get("latent", "latent"): video}}
    if u.get("scale"):
        g["up_scale"]["inputs"][u["scale"]] = up.scale
    if u.get("model"):
        m = u["model"]
        g["up_scale_model"] = {"class_type": m["class_type"],
                               "inputs": copy.deepcopy(m.get("inputs") or {})}
        g["up_scale"]["inputs"][m["input"]] = ["up_scale_model", 0]
    if u.get("vae"):
        g["up_scale"]["inputs"][u["vae"]] = vae_link(g, up, "video_vae")
    if spec.get("into"):
        # the graph's own guide node takes the upscaled video (its guide
        # re-encoded at the new size and appended), and the join after it the
        # take's audio
        into = spec["into"]
        guide = J.upstream_node(g, sampler, into["class_type"])
        join = si["latent_image"][0]
        if guide is None or g[join]["class_type"] != "LTXVConcatAVLatent":
            raise UpscaleError(f"{up.target.short}'s sampler isn't fed a {into['class_type']} "
                               f"through an LTXVConcatAVLatent")
        g[guide]["inputs"][into["input"]] = ["up_scale", 0]
        g[join]["inputs"]["audio_latent"] = ["up_split_av", 1]
        g["up_hold"] = {"class_type": "H3HoldAudio", "inputs": {"latent": [join, 0]}}
    else:
        g["up_join"] = {"class_type": "LTXVConcatAVLatent", "inputs": {
            "video_latent": ["up_scale", 0], "audio_latent": ["up_split_av", 1]}}
        g["up_hold"] = {"class_type": "H3HoldAudio", "inputs": {"latent": ["up_join", 0]}}
    si["latent_image"] = ["up_hold", 0]
    if "sigmas" in si:
        g["up_sigmas"] = {"class_type": "SplitSigmas", "inputs": {"sigmas": si["sigmas"],
                                                                  "step": up.start_step}}
        si["sigmas"] = ["up_sigmas", 1]
    else:
        sampler_tail(g, sampler, up.start_step)
    if up.window:
        windowed(g, up, sampler)


def windowed(g: dict, up: UpscaleJob, sampler: str) -> None:
    """The re-sample's model sampled in windows along time (the target's
    `windows`: obvpm's H3 Context Windowing, MultiDiffusion with pyramid
    weights over each overlap, every step): the model that feeds the sampler,
    or its guider, goes through it first. One window's worth of memory, the
    same work per step."""
    spec = up.spec["windows"]
    si = g[sampler]["inputs"]
    host, field = sampler, "model"
    if "model" not in si and isinstance(si.get("guider"), list):
        host = si["guider"][0]
    if not isinstance(g[host]["inputs"].get(field), list):
        raise UpscaleError(f"{up.target.short}'s re-sample has no model input to window")
    g["up_windows"] = {"class_type": spec.get("class_type", "H3ContextWindows"), "inputs": {
        "model": g[host]["inputs"][field],
        spec.get("seconds_input", "window_seconds"): up.window,
        spec.get("overlap_input", "overlap_seconds"): up.window_overlap}}
    g[host]["inputs"][field] = ["up_windows", 0]


def pixel_refine(g: dict, up: UpscaleJob, sampler: str) -> None:
    """Wan: the take's frames upscaled by a pixel model, encoded with the target's
    VAE, and handed to its final sampler from late in its schedule; the sized
    conditioning (a first frame, references) rebuilt at the new size. The
    earlier samplers fall away in the prune."""
    b, take, root = up.target.binding, up.take, up.root
    for name, value in (("width", up.width), ("height", up.height)):
        if b.specs(name):
            J.patch_param(g, b, name, value)
    vae = J.select_nodes(g, b.specs("vae")[0])[0]
    g["up_video"] = {"class_type": "H3LoadTakeVideo", "inputs": {
        "project_root": root, "video_file": rel(root, take.paths.mp4), "audio_file": ""}}
    g["up_model"] = {"class_type": "UpscaleModelLoader", "inputs": {"model_name": up.pixel_model}}
    g["up_pixels"] = {"class_type": "H3PixelUpscale", "inputs": {
        "images": ["up_video", 0], "upscale_model": ["up_model", 0],
        "width": up.width, "height": up.height, "chunk": 4, "precision": up.precision,
        **up.finish_inputs(grain=False)}}
    g["up_venc"] = {"class_type": "VAEEncode", "inputs": {"pixels": ["up_pixels", 0], "vae": [vae, 0]}}
    si = g[sampler]["inputs"]
    si["latent_image"] = ["up_venc", 0]
    ref = up.spec.get("reference")
    if ref:
        # VACE: its latent starts with the reference picture's frame (cut off
        # after sampling by trim_latent); the refine's starts with it too,
        # sized as the node sizes it (bilinear, centre crop)
        node = J.node_of(g, ref["class_type"])
        image = g[node]["inputs"].get(ref["image"])
        if J.is_link(image):
            g["up_ref"] = {"class_type": "ImageScale", "inputs": {
                "image": image, "upscale_method": "bilinear", "width": up.width,
                "height": up.height, "crop": "center"}}
            g["up_ref_enc"] = {"class_type": "VAEEncode", "inputs": {"pixels": ["up_ref", 0],
                                                                     "vae": [vae, 0]}}
            g["up_with_ref"] = {"class_type": "LatentConcat", "inputs": {
                "samples1": ["up_ref_enc", 0], "samples2": ["up_venc", 0], "dim": "t"}}
            si["latent_image"] = ["up_with_ref", 0]
    steps = schedule_steps(up.spec, int((take.sidecar or {}).get("steps") or 0))
    if g[sampler]["class_type"] == "KSamplerAdvanced":
        # the last expert alone, from the start step: noise added at that level
        si.update(add_noise="enable", start_at_step=up.start_step, end_at_step=10000,
                  return_with_leftover_noise="disable")
    else:
        # a plain KSampler: its tail, as a denoise
        si["denoise"] = round(1.0 - up.start_step / max(1, steps), 4)


def finish_graph(g: dict, up: UpscaleJob, images: list) -> dict:
    """The saver on `images` (and a then-pixel step before it), then the prune."""
    take = up.take
    g["up_save"] = {"class_type": "H3SaveUpscale", "inputs": {
        "images": images, "project_root": up.root, "source_mp4": rel(up.root, take.paths.mp4),
        "out_mp4": rel(up.root, take.paths.up_mp4), "fps": float((take.sidecar or {}).get("fps") or 24),
        "sidecar": rel(up.root, take.paths.up_sidecar), "encoder": up.encoder,
        **up.save_inputs()}}
    if up.then_model and up.then_method == "seedvr2":
        # the re-sample's frames through SeedVR2 before they're saved
        g["up_save"]["inputs"]["images"] = seedvr2_nodes(g, up, images, up.then_model,
                                                          *up.made_size)
    elif up.then_model:
        # the re-sample's frames through an upscale model before they're saved
        g["up_then_model"] = {"class_type": "UpscaleModelLoader",
                              "inputs": {"model_name": up.then_model}}
        g["up_then"] = {"class_type": "H3PixelUpscale", "inputs": {
            "images": images, "upscale_model": ["up_then_model", 0],
            "width": up.made_size[0], "height": up.made_size[1], "chunk": 2,
            "precision": up.precision, **up.finish_inputs()}}
        g["up_save"]["inputs"]["images"] = ["up_then", 0]
    J.prune(g, "up_save")
    return g


def seedvr2_graph(up: UpscaleJob) -> dict:
    """SeedVR2, as ComfyUI's own video template runs it (lanczos to the target
    size, pre-process, tiled encode, one step, tiled decode, LAB colour
    correction), with the clip split in time as VRAM needs (auto) and our finish
    after it: the frequency split halved its frame-to-frame shimmer and its colour
    drift in the evaluation. The take's audio copied on."""
    take, root = up.take, up.root
    g = {"up_video": {"class_type": "H3LoadTakeVideo", "inputs": {
        "project_root": root, "audio_file": "",
        "video_file": rel(root, take.paths.up_mp4 if up.previous else take.paths.mp4)}}}
    out = seedvr2_nodes(g, up, ["up_video", 0], up.seedvr2_model, up.width, up.height)
    g["up_save"] = {"class_type": "H3SaveUpscale", "inputs": {
        "images": out, "project_root": root,
        "source_mp4": rel(root, take.paths.mp4), "out_mp4": rel(root, take.paths.up_mp4),
        "fps": float((take.sidecar or {}).get("fps") or 24),
        "sidecar": rel(root, take.paths.up_sidecar), "encoder": up.encoder,
        **up.save_inputs()}}
    return g


def seedvr2_nodes(g: dict, up: UpscaleJob, images: list, model: str, width: int,
                  height: int) -> list:
    """SeedVR2's nodes on `images` (the take's frames, or a re-sample's) to
    width x height, finished against them; returns the link to the result."""
    seed = int((up.take.sidecar or {}).get("seed") or 0) % (1 << 50)
    return seedvr2_chain(g, images, model, width, height, seed, up.finish_inputs())


def seedvr2_chain(g: dict, images: list, model: str, width: int, height: int, seed: int,
                  finish: dict, prefix: str = "up_", chunk: int = 0, overlap: int = 2) -> list:
    """SeedVR2 on `images` to width x height (the same size: a restoration),
    finished against them by H3FinishUpscale with `finish`; the node ids start
    with `prefix`. Returns the link to the result. The upscale's and the post
    pass's (h3post). `chunk`: pixel frames per temporal chunk (4n+1), 0 for
    auto, which sizes chunks to the VRAM free at the time, so two runs can split
    a shot differently; `overlap`: latent frames crossfaded between chunks."""
    p = prefix
    chunking = ({"chunking_mode": "manual", "chunking_mode.frames_per_chunk": int(chunk)}
                if chunk else {"chunking_mode": "auto"})
    tile = {"tile_size": 512, "overlap": 128, "temporal_size": 64, "temporal_overlap": 8}
    g.update({
        p + "resize": {"class_type": "ImageScale", "inputs": {
            "image": images, "upscale_method": "lanczos", "width": width,
            "height": height, "crop": "disabled"}},
        p + "pre": {"class_type": "SeedVR2Preprocess", "inputs": {"resized_images": [p + "resize", 0]}},
        p + "vae": {"class_type": "VAELoader", "inputs": {"vae_name": SEEDVR2_VAE}},
        p + "enc": {"class_type": "VAEEncodeTiled", "inputs": {"pixels": [p + "pre", 0], "vae": [p + "vae", 0], **tile}},
        p + "chunk": {"class_type": "SeedVR2TemporalChunk", "inputs": {
            "latent": [p + "enc", 0], "temporal_overlap": int(overlap), **chunking}},
        p + "unet": {"class_type": "UNETLoader", "inputs": {"unet_name": model,
                                                           "weight_dtype": "default"}},
        p + "cond": {"class_type": "SeedVR2Conditioning", "inputs": {
            "model": [p + "unet", 0], "vae_conditioning": [p + "chunk", 0]}},
        p + "ks": {"class_type": "KSampler", "inputs": {
            "model": [p + "unet", 0], "positive": [p + "cond", 0], "negative": [p + "cond", 1],
            "latent_image": [p + "chunk", 0], "seed": seed, "steps": 1, "cfg": 1.0,
            "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}},
        p + "merge": {"class_type": "SeedVR2TemporalMerge", "inputs": {
            "latents": [p + "ks", 0], "temporal_overlap": [p + "chunk", 1]}},
        p + "dec": {"class_type": "VAEDecodeTiled", "inputs": {"samples": [p + "merge", 0], "vae": [p + "vae", 0], **tile}},
        p + "post": {"class_type": "SeedVR2PostProcessing", "inputs": {
            "images": [p + "dec", 0], "original_resized_images": [p + "resize", 0],
            "color_correction_method": "lab"}},
        p + "finish": {"class_type": "H3FinishUpscale", "inputs": {
            "images": [p + "post", 0], "source": images, "chunk": 8, **finish}},
    })
    return [p + "finish", 0]


def pixel_graph(up: UpscaleJob) -> dict:
    """The pixel method: the take's frames through an upscale model, resized to
    the target size, saved with the take's audio copied on. No model of the
    take's target is loaded, so it works for any take."""
    take, root = up.take, up.root
    return {
        "up_video": {"class_type": "H3LoadTakeVideo", "inputs": {
            "project_root": root, "audio_file": "",
            # on top of its existing upscale, or from the take
            "video_file": rel(root, take.paths.up_mp4 if up.previous else take.paths.mp4)}},
        "up_model": {"class_type": "UpscaleModelLoader", "inputs": {"model_name": up.pixel_model}},
        "up_pixels": {"class_type": "H3PixelUpscale", "inputs": {
            "images": ["up_video", 0], "upscale_model": ["up_model", 0],
            "width": up.width, "height": up.height, "chunk": 4, "precision": up.precision,
            **up.finish_inputs()}},
        "up_save": {"class_type": "H3SaveUpscale", "inputs": {
            "images": ["up_pixels", 0], "project_root": root,
            "source_mp4": rel(root, take.paths.mp4), "out_mp4": rel(root, take.paths.up_mp4),
            "fps": float((take.sidecar or {}).get("fps") or 24),
            "sidecar": rel(root, take.paths.up_sidecar), "encoder": up.encoder,
            **up.save_inputs()}},
    }


def not_ready(jobs: list[UpscaleJob], object_info: dict | None) -> list[str]:
    """Why this ComfyUI can't run these jobs ([] when it can)."""
    out = []
    for t in {j.target.id: j.target for j in jobs if j.method == "latent"}.values():
        r = upscale_readiness(t, object_info)
        if r and r["status"] == "not_ready":
            out += [f"{t.short}: {m}" for m in r["missing"]]
    wants = {j.pixel_model for j in jobs if j.method == "pixel"}
    wants |= {j.then_model for j in jobs
              if j.method == "latent" and j.then_model and j.then_method == "pixel"}
    wants |= {j.pixel_model for j in jobs
              if j.method == "latent" and j.spec.get("mode") == PIXEL_REFINE}
    for j in jobs:
        ct = (j.spec.get("windows") or {}).get("class_type", "H3ContextWindows")
        if j.window and object_info is not None and ct not in object_info:
            out.append(f"{j.label} is {take_seconds(j.take):.1f}s, longer than one "
                       f"{j.window:g}s window, and windowing it needs {ct} "
                       f"({(j.spec.get('windows') or {}).get('pack', 'comfyui-obvpm-timeline')}); "
                       f"install it, or set the recipe's window to 0 to try one pass")
    sv2 = {j.seedvr2_model for j in jobs if j.method == "seedvr2"}
    sv2 |= {j.then_model for j in jobs if j.method == "latent" and j.then_method == "seedvr2"}
    for want in sorted(sv2):
        r = seedvr2_readiness(object_info, want)
        if r["status"] == "not_ready":
            out += [f"SeedVR2: {m}" for m in r["missing"]]
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
    if up.method == "seedvr2":
        return seedvr2_graph(up)
    if up.target.id not in bases:
        bases[up.target.id] = J.target_workflow(up.target, None, comfy_url)[0]
    return upscale_graph(bases[up.target.id], up)


def describe(up: UpscaleJob) -> str:
    d = (f" -> {up.deliver[0]}x{up.deliver[1]} ({'cropped' if up.fit == 'crop' else 'padded'})"
         if up.deliver and tuple(up.deliver) != up.made_size else "")
    return _describe(up) + d + "".join(f"\n       note: {n}" for n in up.notes)


def _describe(up: UpscaleJob) -> str:
    if up.method == "seedvr2":
        on = " on its upscale" if up.previous else ""
        return f"SeedVR2 ({up.seedvr2_model}){on}, {up.scale:g}x -> {up.width}x{up.height}"
    if up.method == "pixel":
        on = " on its upscale" if up.previous else ""
        return f"pixel ({up.pixel_model}){on}, {up.scale:g}x -> {up.width}x{up.height}"
    then = (f", then {'SeedVR2 (' + up.then_model + ')' if up.then_method == 'seedvr2' else up.then_model}"
            f" {up.then_scale:g}x -> {up.made_size[0]}x{up.made_size[1]}"
            if up.then_model else "")
    return (f"{up.route}, {up.scale:g}x -> {up.width}x{up.height}, from step {up.start_step}{then}")


def previous_summary(prev: dict) -> dict:
    """The parts of an upscale record that say what it was (for the next one's
    `on_upscale`), its own `on_upscale` kept, so a chain reads back in full."""
    keep = ("method", "route", "mode", "scale", "start_step", "pixel_model", "then_pixel",
            "deliver", "width", "height", "finished", "on_upscale")
    return {k: prev[k] for k in keep if prev.get(k) is not None}


def deliver_record(up: UpscaleJob) -> dict:
    """`deliver`: the size asked for, the fit, and what the last step made."""
    if not up.deliver:
        return {}
    return {"deliver": {"width": up.deliver[0], "height": up.deliver[1], "fit": up.fit,
                        "made": list(up.made_size)}}


def settings_of(up: UpscaleJob) -> dict:
    """What decides an upscale's picture, normalised (the record's `recipe`):
    method, scales, start, models, the then step, the finish, the size and fit.
    Not the route (latent or VAE: the same settings either way) or the
    encoder. Two upscales of a take with equal settings are the same recipe."""
    d: dict = {"method": up.method, "scale": up.scale}
    model = up.method == "pixel" or (up.method == "latent" and up.spec.get("mode") == PIXEL_REFINE)
    if up.method == "latent":
        d.update(mode=up.spec.get("mode", RESAMPLE), start_step=up.start_step)
    if model:
        d["pixel_model"] = up.pixel_model
    if up.method == "seedvr2":
        d["seedvr2_model"] = up.seedvr2_model
    if up.then_model:
        d["then"] = {"method": up.then_method, "model": up.then_model, "scale": up.then_scale}
        model = model or up.then_method == "pixel"
    if model:
        d["precision"] = up.precision
    if up.method != "latent" or up.then_model or up.spec.get("mode") == PIXEL_REFINE:
        d["finish"] = up.finish_record()
    if up.previous:
        d["on_upscale"] = True
    d["size"] = list(up.out_size)
    if up.deliver:
        d["fit"] = up.fit
    if up.quality != "review":
        d["quality"] = up.quality
    if up.window:
        d["window"] = [up.window, up.window_overlap]
    return d


def settings_hash(settings: dict) -> str:
    import hashlib
    import json
    return hashlib.sha1(json.dumps(settings, sort_keys=True).encode()).hexdigest()[:12]


def recipe_status(root: str, take: T.Take, recipe: dict | None = None,
                  shots: dict | None = None, rec: dict | None = None) -> str | None:
    """How a take's upscale stands against the master recipe: "same" (made with
    the settings the recipe gives it now), "different", "unknown" (made before
    upscales recorded their settings), or None (no upscale, or no recipe)."""
    rec = T.upscale_of(take) if rec is None else rec
    recipe = master_recipe(root) if recipe is None else recipe
    if not rec or not recipe:
        return None
    if not rec.get("recipe_hash"):
        return "unknown"
    try:
        job = plan_upscale(root, take, redo=True, **recipe_for(root, take, recipe, shots))
    except UpscaleError:
        return "different"
    if job.action == "error":
        return "different"
    return "same" if settings_hash(settings_of(job)) == rec["recipe_hash"] else "different"


def set_keep(take: T.Take, keep: bool) -> dict:
    """Mark (or unmark) a take's upscale Keep: nothing that redoes a cut's
    upscales wholesale (Master, --conform, the whole cut's redo) replaces it
    while it is fresh; naming the take does. UpscaleError without a finished one."""
    rec = T.upscale_of(take)
    if not rec or rec.get("status") != "ok":
        raise UpscaleError(f"{take.shot} t{take.take:02d} has no finished upscale to keep")
    T.update_sidecar(take.paths.up_sidecar, keep=bool(keep) or None)
    return T.upscale_of(take) or {}


def kept(take: T.Take, rec: dict | None = None) -> bool:
    """A fresh upscale marked Keep."""
    rec = T.upscale_of(take) if rec is None else rec
    return bool(rec and rec.get("keep") and rec.get("fresh"))


def queued_record(up: UpscaleJob) -> dict:
    s = settings_of(up)
    return {**_queued_record(up), "quality": up.quality, "recipe": s,
            "recipe_hash": settings_hash(s)}


def _queued_record(up: UpscaleJob) -> dict:
    if up.method == "seedvr2":
        return {"shot": up.shot, "take": up.take.take, "status": "queued", "queued": T.now(),
                "comfy_prompt_id": None, "target": up.target.id, "route": "seedvr2",
                "method": "seedvr2", "mode": "seedvr2", "scale": up.scale, "start_step": None,
                "steps": 1, "seed": (up.take.sidecar or {}).get("seed"),
                "upscaler": up.seedvr2_model, "seedvr2_model": up.seedvr2_model,
                "color_correction": "lab", "encoder_asked": up.encoder,
                "finish": up.finish_record(),
                **({"on_upscale": previous_summary(up.previous)} if up.previous else {}),
                **deliver_record(up),
                "width": up.out_size[0], "height": up.out_size[1], **T.source_stamp(up.take.paths.mp4)}
    if up.method == "pixel":
        return {"shot": up.shot, "take": up.take.take, "status": "queued", "queued": T.now(),
                "comfy_prompt_id": None, "target": up.target.id, "route": "pixel",
                "method": "pixel", "mode": "pixel", "scale": up.scale, "start_step": None,
                "steps": None, "seed": None, "upscaler": up.pixel_model,
                "pixel_model": up.pixel_model, "precision": up.precision,
                "encoder_asked": up.encoder, "finish": up.finish_record(),
                # the chain: what the upscale it ran on was (so a later look can
                # tell "re-sample 2x, then pixel 2x"); the stamp stays the take's
                **({"on_upscale": previous_summary(up.previous)} if up.previous else {}),
                **deliver_record(up),
                "width": up.out_size[0], "height": up.out_size[1], **T.source_stamp(up.take.paths.mp4)}
    if up.spec.get("mode") == PIXEL_REFINE:
        upscaler = f"{up.pixel_model}, then the target's own sampler"
    elif up.spec.get("mode", RESAMPLE) == SECOND_STAGE:
        upscaler = f"{up.spec['upsampler']['class_type']} (the target's own second stage)"
    else:
        upscaler = upscaler_model(up.spec) or up.spec["upscaler"]["class_type"]
    return {"shot": up.shot, "take": up.take.take, "status": "queued", "queued": T.now(),
            "comfy_prompt_id": None, "target": up.target.id, "route": up.route,
            "method": "latent", "mode": up.spec.get("mode", RESAMPLE),
            "scale": up.scale, "start_step": up.start_step,
            "steps": schedule_steps(up.spec, int((up.take.sidecar or {}).get("steps") or 0)),
            "seed": (up.take.sidecar or {}).get("seed"),
            "upscaler": upscaler, "encoder_asked": up.encoder,
            **({"precision": up.precision, "finish": up.finish_record()}
               if up.then_model or up.spec.get("mode") == PIXEL_REFINE else {}),
            **({"then_pixel": {"model": up.then_model, "scale": up.then_scale,
                               "from": [up.width, up.height],
                               **({"method": "seedvr2"} if up.then_method == "seedvr2" else {})}}
               if up.then_model else {}),
            **deliver_record(up),
            "width": up.out_size[0], "height": up.out_size[1], **T.source_stamp(up.take.paths.mp4)}


def start(up: UpscaleJob) -> None:
    T.write_json(up.take.paths.up_sidecar, queued_record(up))


def mark_queued(up: UpscaleJob, pid: str) -> None:
    T.update_sidecar(up.take.paths.up_sidecar, comfy_prompt_id=pid)


def mark_failed(up: UpscaleJob, why: str) -> None:
    T.update_sidecar(up.take.paths.up_sidecar, status="failed", finished=T.now(),
                     save_notes=why)


def cut_takes(root: str, only: set[str] | None = None, take_n: int | None = None,
              pass_: str = PASS, include_out: bool = False) -> list:
    """(shot, Take | None, why) for each shot of the pass's cut (or `only`): the
    pick, else the latest usable take; `take_n` forces a number. A placeholder
    (a take from the other pass) is left out. A shot left out of the cut isn't
    listed unless `only` names it or `include_out`."""
    from h3assemble import choose_take
    entries = T.resolve_cut(T.load_cut(root), pass_, J.script_order(root))
    out = []
    for e in entries:
        if only is not None and e.shot not in only:
            continue
        if e.out and not include_out and only is None:
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
    # a shot left out keeps its pick's latent: it may come back
    picked = {shot: take.take for shot, take, _ in cut_takes(root, only, pass_=pass_,
                                                           include_out=True)
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
    ap.add_argument("--seedvr2-model", help="the SeedVR2 method's model: 7b (default) or 3b, or a file name")
    ap.add_argument("--encoder", choices=ENCODERS, default="auto",
                    help="auto: H.264, on the GPU (NVENC) up to 4096 wide, x264 past it; "
                         "nvenc forces the GPU (HEVC past 4096); x264 the CPU")
    ap.add_argument("--quality", choices=QUALITIES, default="review",
                    help="how the .up.mp4 is encoded: review (the default) or master "
                         "(x264 CRF 12, slower: for delivery)")
    ap.add_argument("--precision", choices=PRECISIONS, default="fp16",
                    help="the upscale model's precision (fp16: about twice as fast)")
    ap.add_argument("--no-frequency-split", dest="frequency_split", action="store_false",
                    help="a pixel model's colour and tone as it made them (default: the source's)")
    ap.add_argument("--keep-soft", type=float, default=0.0,
                    help="0-1: fade the model's invented detail where the source was soft")
    ap.add_argument("--grain", type=float, default=0.0,
                    help="film grain after a pixel model, 0-0.2 (0.02 is light)")
    ap.add_argument("--from-upscale", action="store_true",
                    help="the pixel method on the take's existing upscale (its .up.mp4 is the "
                         "input and is replaced): e.g. a re-sample first, pixel 2x later")
    ap.add_argument("--then-pixel", metavar="MODEL",
                    help="after a re-sample, an upscale model takes it on by --then-scale "
                         "(e.g. re-sample 2x then RealESRGAN_x2.pth: 4x)")
    ap.add_argument("--then-seedvr2", action="store_true",
                    help="after a re-sample, SeedVR2 (--seedvr2-model) takes it on by --then-scale, "
                         "instead of an upscale model")
    ap.add_argument("--then-scale", type=float, help="the --then-pixel step's scale (default 2)")
    ap.add_argument("--deliver", metavar="SIZE",
                    help="the upscale's exact size: 1080p, 1440p, 4k or WxH. The last pixel step "
                         "(pixel, SeedVR2, --then-pixel) is scaled to it, aspect kept, then --fit "
                         "crops or pads; a re-sample alone is resized to it")
    ap.add_argument("--fit", choices=FITS, default="crop",
                    help="when the take's aspect isn't the delivery's: crop (fill, trim the "
                         "overhang; the default) or pad (fit inside, black bars)")
    ap.add_argument("--detail", type=int, choices=DETAILS,
                    help="latent: start 0-2 steps earlier than the default (more detail, more change)")
    ap.add_argument("--vae", action="store_true",
                    help="encode the take's frames even if it kept a latent")
    ap.add_argument("--keep", action="store_true",
                    help="mark the upscales of these takes (--only / --take) Keep: nothing that "
                         "redoes the cut's upscales wholesale replaces them")
    ap.add_argument("--unkeep", action="store_true", help="clear Keep on these takes' upscales")
    ap.add_argument("--recipe", action="store_true",
                    help="each take by the series config's upscale.master recipe (its target's "
                         "section, its shot's override); the method and size flags are ignored")
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
    if args.keep or args.unkeep:
        for shot, take, why in cut_takes(root, only, args.take, pass_=pass_):
            if take is None:
                print(f"  -  {shot}: {why}")
                continue
            try:
                set_keep(take, args.keep)
                print(f"  {'kept' if args.keep else 'unkept'}  {shot} t{take.take:02d}")
            except UpscaleError as e:
                print(f"  !! {e}")
        return 0
    jobs = []
    recipe = master_recipe(root) if args.recipe else None
    if args.recipe and not recipe:
        print("  !! the series config has no upscale.master recipe")
        return 2
    for shot, take, why in cut_takes(root, only, args.take, pass_=pass_):
        if take is None:
            print(f"  -  {shot}: {why}")
            continue
        if recipe:
            try:
                up = plan_upscale(root, take, redo=args.redo, respect_keep=only is None,
                                  **recipe_for(root, take, recipe))
            except UpscaleError as e:
                print(f"  !! {shot} t{take.take:02d}: {e}")
                continue
        else:
            up = plan_upscale(root, take, scale=args.scale, start_step=args.start_step,
                              route="vae" if args.vae else None, redo=args.redo,
                              respect_keep=only is None and args.take is None,
                              method=args.method, pixel_model=args.pixel_model, detail=args.detail,
                              then_model=args.then_pixel, then_scale=args.then_scale,
                              then_method="seedvr2" if args.then_seedvr2 else None,
                              from_upscale=args.from_upscale, encoder=args.encoder,
                              precision=args.precision, frequency_split=args.frequency_split,
                              keep_soft=args.keep_soft, grain=args.grain,
                              seedvr2_model=args.seedvr2_model, deliver=args.deliver, fit=args.fit,
                              quality=args.quality)
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
