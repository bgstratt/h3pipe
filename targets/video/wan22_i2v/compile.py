"""
Wan 2.2 14B image to video: the story IR compiled into shotlist entries that
h3jobs patches into the two-stage Wan 2.2 14B I2V graph (target.json beside
this file). No loader node: every render value is a widget.

    compile_episode(target, story, series_cfg, pass_, only=None) -> (doc, report)
    compile_shot(target, shot_ir, series_cfg, preset, ctx) -> one entry
    required_refs(target, shot_ir, series_cfg, ctx) -> []   (nothing the image target makes)
    ref_slots(target, doc, shot) -> the first frame (required) and the last (optional)
    patch_graph(target, graph, job, inputs) -> the keyframes, the stage split

The first keyframe is what Wan animates, so it is required and has no
render-anyway: its ref slot says `anyway: false` and why, so h3jobs blocks the
shot (even with allow_missing_refs) and the CLI, routes and editor say
"Wan 14B I2V needs a first frame: use continuity or import one, or retarget
to wan22_ti2v". With a last keyframe too, WanFirstLastFrameToVideo takes the
place of WanImageToVideo (the same inputs, plus end_image).

The compile is targets/video/wan/common.py (shared with wan22_ti2v and
wan22_vace); the prompt is Wan prose (wan/prompt.py). Audio: none.

Stdlib only.
"""
from __future__ import annotations

import targets as TG
from targets.video.wan import common as C

ID = "wan22_i2v"
TARGET = TG.load_target(ID, "video")

compile_episode = C.compile_episode
compile_shot = C.compile_shot
required_refs = C.required_refs
ref_slots = C.ref_slots


def patch_graph(target, g: dict, job, inputs: dict) -> None:
    """`inputs["first"]` (the name LoadImage reads) is the start image; without
    it the job can't render (plan_job blocks such a shot, so this only guards
    a caller that skipped planning). `inputs["last"]` switches the conditioning
    node to WanFirstLastFrameToVideo with its end_image. Then the two samplers'
    step ranges (common.split_stages)."""
    why = target.recipe["keyframe_required"]["first"]
    if not inputs.get("first"):
        raise ValueError(f"{job.id}: {why}")
    i2v = C.one(g, "WanImageToVideo", what=target.id)
    load = g[i2v]["inputs"]["start_image"][0]
    g[load]["inputs"]["image"] = inputs["first"]
    if inputs.get("last"):
        g[i2v]["class_type"] = "WanFirstLastFrameToVideo"
        g[i2v]["_meta"] = {"title": "Wan first/last frame to video (h3pipe)"}
        end = C.new_id(g, "h3_last_frame_")
        g[end] = {"class_type": "LoadImage", "inputs": {"image": inputs["last"]},
                  "_meta": {"title": "Last frame (h3pipe)"}}
        g[i2v]["inputs"]["end_image"] = [end, 0]
    C.split_stages(target, g, job)
