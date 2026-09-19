"""
Wan 2.2 5B text/image to video: the story IR compiled into shotlist entries
that h3jobs patches into the Wan 2.2 5B graph (target.json beside this file).
No loader node: every render value is a widget.

    compile_episode(target, story, series_cfg, pass_, only=None) -> (doc, report)
    compile_shot(target, shot_ir, series_cfg, preset, ctx) -> one entry
    required_refs(target, shot_ir, series_cfg, ctx) -> []   (nothing is required)
    ref_slots(target, doc, shot) -> the optional first keyframe
    patch_graph(target, graph, job, inputs) -> the first frame in, or text to video

The compile is targets/video/wan/common.py (shared with wan22_i2v and
wan22_vace); the prompt is Wan prose (wan/prompt.py). Audio: none.

Stdlib only.
"""
from __future__ import annotations

import targets as TG
from targets.video.wan import common as C

ID = "wan22_ti2v"
TARGET = TG.load_target(ID, "video")

compile_episode = C.compile_episode
compile_shot = C.compile_shot
required_refs = C.required_refs
ref_slots = C.ref_slots


def patch_graph(target, g: dict, job, inputs: dict) -> None:
    """`inputs["first"]` (the name LoadImage reads) is Wan22ImageToVideoLatent's
    start_image; without it the input is disconnected (text to video) and
    prune drops the LoadImage."""
    lat = C.one(g, "Wan22ImageToVideoLatent", what=target.id)
    li = g[lat]["inputs"]
    if inputs.get("first"):
        src = li.get("start_image")
        if not (isinstance(src, list) and g.get(src[0], {}).get("class_type") == "LoadImage"):
            raise ValueError(f"the {target.id} workflow's Wan22ImageToVideoLatent must read "
                             f"its start_image from a LoadImage")
        g[src[0]]["inputs"]["image"] = inputs["first"]
    else:
        li.pop("start_image", None)
