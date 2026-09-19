"""
krea2: the graph one reference take runs, patched from the workflow file
(target.json binding.workflow, the moved krea2_refs_t2i.json) or built in.

The model stack's names (UNET, CLIP, VAE, sampler...) are data in target.json;
these are the functions that put them into a graph. h3refs re-exports them
under their old names, and kreagen through h3refs.

Stdlib only.
"""
from __future__ import annotations

import copy
import os

import targets as TG

TARGET = TG.load_target("krea2", "image")
_B = TARGET.binding
_G = dict(TARGET.spec["binding"].get("graph") or {})
_P = TARGET.presets["final"]
_T = TARGET.spec.get("template") or {}

REFS_WORKFLOW = _B.workflow_name
SAVER = _B.saver_class
UNET = _P.model
CLIP = _G["clip"]
CLIPTYPE = _G["clip_type"]
VAE = _G["vae"]
LORA = _P.lora or ""
LORA_M = float(_P.extra.get("lora_strength", 1.0))
LORA_C = float(_P.extra.get("lora_clip_strength", 1.0))
STEPS = int(_P.steps)
CFG = float(_P.extra.get("cfg", 1.0))
SAMPLER = _G["sampler"]
SCHED = _G["scheduler"]
PREFIX = _G["prefix"]      # SaveImage prefix, when the graph keeps SaveImage

VIEW_SIZE = tuple(_T.get("view_size", (1024, 1024)))
PLATE_SIZE = tuple(_T.get("plate_size", (1344, 768)))
OBJECT_SIZE = tuple(_T.get("object_size", (1024, 1024)))


def _one(g: dict, *ctypes: str) -> str:
    ids = [k for k, v in g.items() if v["class_type"] in ctypes]
    if len(ids) != 1:
        raise ValueError(f"the workflow needs exactly one {' / '.join(ctypes)} node "
                         f"(found {len(ids)})")
    return ids[0]


def _splice_lora(g: dict, ks: str, pos: str, name: str, sm: float, sc: float,
                 lid: str) -> None:
    """Insert a LoraLoader between the sampler's model / the prompt's clip and
    their sources, rewiring every text encoder that read the same clip."""
    ki = g[ks]["inputs"]
    model_src, clip_src = ki["model"], g[pos]["inputs"]["clip"]
    g[lid] = {"class_type": "LoraLoader",
              "inputs": {"model": model_src, "clip": clip_src, "lora_name": name,
                         "strength_model": sm, "strength_clip": sc}}
    ki["model"] = [lid, 0]
    for node in g.values():
        if node["class_type"] == "CLIPTextEncode" and node["inputs"].get("clip") == clip_src:
            node["inputs"]["clip"] = [lid, 1]


def patch_workflow(base: dict, prompt: str, negative: str, w: int, h: int, seed: int,
                   steps: int, cfg: float, prefix: str,
                   unet: str = "", lora: str = "", lora_m: float = 1.0,
                   lora_c: float = 1.0) -> dict:
    """Set this job's values on a loaded workflow, leaving its wiring alone.

    Positive and negative prompts are found by following the sampler's own
    links, because the two CLIPTextEncode nodes are otherwise identical.
    """
    g = copy.deepcopy(base)
    ks = _one(g, "KSampler", "KSamplerAdvanced")
    ki = g[ks]["inputs"]
    for key, val in (("seed", seed), ("noise_seed", seed), ("steps", steps),
                     ("cfg", cfg), ("denoise", 1.0)):
        if key in ki:
            ki[key] = val

    def text_node(slot: str) -> str | None:
        link = ki.get(slot)
        return link[0] if isinstance(link, list) else None

    pos, neg = text_node("positive"), text_node("negative")
    if pos and g[pos]["class_type"] == "CLIPTextEncode":
        g[pos]["inputs"]["text"] = prompt
    else:
        raise ValueError("the workflow's sampler has no CLIPTextEncode on `positive`")
    # The negative side is left exactly as the workflow wires it — a krea2 turbo
    # graph zeroes it, a guided model's graph carries its own text, and a NAG or
    # negpip setup is untouched. Only an explicit negative overrides that.
    if neg and negative and cfg > 1.0:
        g[neg] = {"class_type": "CLIPTextEncode",
                  "inputs": {"clip": g[pos]["inputs"]["clip"], "text": negative}}

    lat = _one(g, "EmptyLatentImage", "EmptySD3LatentImage")
    g[lat]["inputs"]["width"], g[lat]["inputs"]["height"] = w, h
    g[_one(g, "SaveImage")]["inputs"]["filename_prefix"] = prefix

    if unet:
        g[_one(g, "UNETLoader")]["inputs"]["unet_name"] = unet
    if lora:
        ids = [k for k, v in g.items() if v["class_type"] in ("LoraLoader",
                                                              "LoraLoaderModelOnly")]
        if len(ids) > 1:
            raise ValueError(f"--lora needs one LoraLoader in the workflow (found {len(ids)})")
        if ids:
            gi = g[ids[0]]["inputs"]
            gi["lora_name"] = lora
            if "strength_model" in gi:
                gi["strength_model"] = lora_m
            if "strength_clip" in gi:
                gi["strength_clip"] = lora_c
        else:
            # the workflow has no LoRA node: splice one in between the loaders
            # and everything that reads them, so --lora works on any graph
            _splice_lora(g, ks, pos, lora, lora_m, lora_c, "kreagen_lora")
    return g


def build_graph(prompt: str, negative: str, w: int, h: int, seed: int,
                steps: int, cfg: float, prefix: str, lora_clip: bool,
                unet: str = "", lora: str = "", lora_m: float = LORA_M,
                lora_c: float = LORA_C) -> dict:
    """The built-in krea2 turbo graph, for when no workflow file is found."""
    lora = lora or LORA
    model_src = ["4", 0] if lora else ["1", 0]
    clip_src = (["4", 1] if lora_clip else ["2", 0]) if lora else ["2", 0]
    g = {
        "1": {"class_type": "UNETLoader",
              "inputs": {"unet_name": unet or UNET, "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader",
              "inputs": {"clip_name": CLIP, "type": CLIPTYPE, "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": VAE}},

        "5": {"class_type": "CLIPTextEncode",
              "inputs": {"clip": clip_src, "text": prompt}},
        "7": {"class_type": "EmptyLatentImage",
              "inputs": {"width": w, "height": h, "batch_size": 1}},
        "8": {"class_type": "KSampler",
              "inputs": {"model": model_src, "positive": ["5", 0], "negative": ["6", 0],
                         "latent_image": ["7", 0], "seed": seed, "steps": steps,
                         "cfg": cfg, "sampler_name": SAMPLER, "scheduler": SCHED,
                         "denoise": 1.0}},
        "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["3", 0]}},
        "10": {"class_type": "SaveImage",
               "inputs": {"images": ["9", 0], "filename_prefix": prefix}},
    }
    if lora:
        g["4"] = {"class_type": "LoraLoader",
                  "inputs": {"model": ["1", 0], "clip": ["2", 0], "lora_name": lora,
                             "strength_model": lora_m, "strength_clip": lora_c}}
    # At cfg 1.0 there is no guidance, so a negative prompt is inert and
    # ConditioningZeroOut is the cheap correct thing. Above 1.0 it bites.
    if cfg > 1.0 and negative:
        g["6"] = {"class_type": "CLIPTextEncode",
                  "inputs": {"clip": clip_src, "text": negative}}
    else:
        g["6"] = {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["5", 0]}}
    return g


def take_graph(base: dict | None, prompt: str, negative: str, w: int, h: int, seed: int,
               steps: int, cfg: float, model: str, loras: list[dict] | None,
               sidecar: str | None, lora_clip: bool = True) -> dict:
    """The API graph for one ref take: `base` patched (or the built-in graph),
    LoRAs applied (a list: the first in the workflow's loader, the rest spliced
    after it; [] zeroes the workflow's own; None leaves it alone), and, given a
    sidecar path, SaveImage replaced by the take saver."""
    first = loras[0] if loras else None
    fname, fstr = (first["name"], float(first.get("strength", 1.0))) if first else ("", 1.0)
    if base is not None:
        g = patch_workflow(base, prompt, negative, w, h, seed, steps, cfg, PREFIX,
                           unet=model, lora=fname, lora_m=fstr, lora_c=fstr)
    else:
        g = build_graph(prompt, negative, w, h, seed, steps, cfg, PREFIX, lora_clip,
                        unet=model, lora=fname, lora_m=fstr, lora_c=fstr)
    if loras == []:
        # no LoRA at all: a workflow's own loader is kept but does nothing
        for v in g.values():
            if v["class_type"] in ("LoraLoader", "LoraLoaderModelOnly"):
                for k in ("strength_model", "strength_clip"):
                    if k in v["inputs"]:
                        v["inputs"][k] = 0.0
    if loras and len(loras) > 1:
        ks = _one(g, "KSampler", "KSamplerAdvanced")
        pos = g[ks]["inputs"]["positive"][0]
        for i, lo in enumerate(loras[1:], start=2):
            st = float(lo.get("strength", 1.0))
            _splice_lora(g, ks, pos, lo["name"], st, st, f"h3refs_lora{i}")
    if sidecar:
        sid = _one(g, "SaveImage")
        g[sid] = {"class_type": SAVER,
                  "inputs": {"images": g[sid]["inputs"]["images"],
                             "sidecar": os.path.abspath(sidecar)},
                  "_meta": {"title": "H3 Save Ref Take"}}
    return g
