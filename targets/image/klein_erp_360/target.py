"""
klein_erp_360: a location's 360 panorama by nomadoor's FLUX.2 Klein 9B 360 ERP
outpaint LoRA (target.json beside this file). Its one reference is the plate on
a green 2:1 canvas (comfy_nodes/h3_erp.py, made by h3refs.erp_canvas); the
graph code loads it into the workflow's "Reference 1" LoadImage, which is both
the reference latent and the latent the sampler starts from.

Stdlib only.
"""
from __future__ import annotations

from targets.image.common import keyframe_prompt, ref_prompt  # noqa: F401


def patch_graph(target, g: dict, job, inputs: dict) -> None:
    names = list(inputs.get("references") or [])
    if not names:
        raise ValueError("a 360 needs its canvas: the plate placed on a green 2:1 picture")
    load = next((k for k, v in g.items() if v.get("class_type") == "LoadImage"), None)
    if load is None:
        raise ValueError("a Klein 360 workflow needs a LoadImage for the canvas")
    g[load]["inputs"]["image"] = names[0]
