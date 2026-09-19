"""
Wan 2.2 14B Fun VACE: the story IR compiled into shotlist entries that h3jobs
patches into a two-stage WanVaceToVideo graph (target.json beside this file),
which keeps the shot's characters and props looking like their picked refs
through VACE's reference image. No loader node: every value is a widget.

    compile_episode(target, story, series_cfg, pass_, only=None) -> (doc, report)
    compile_shot(target, shot_ir, series_cfg, preset, ctx) -> one entry
    compile_without(target, story, series_cfg, pass_, entry, missing) -> entry
    required_refs(target, shot_ir, series_cfg, ctx) -> [RefRequest]
    ref_slots(target, doc, shot) -> a slot per reference panel, then the keyframes
    stage_inputs(target, job, comfy) -> {"reference": name}   (composes + uploads)
    patch_graph(target, graph, job, inputs) -> reference, keyframe controls, split

The reference image is made at queue time from the files on disk then, as
ltx2_ingredients makes its sheet: comfy_nodes/h3_refsheet.py (PIL, a
subprocess of the running Python) composes one panel per subject at the
render size on white, the PNG is uploaded to ComfyUI's input folder, and
h3jobs.start_job moves it into the take as <shot>_tNN_reference.png with its
sha1 in the sidecar's `refs` (beside the panel files', which are what make a
take `ref`-stale when a view is re-picked).

Missing refs: every panel's file is required (the shot is blocked). Rendering
anyway recompiles the shot without them (common.compile_without): they leave
the reference image; the prose describes every subject in words anyway. With
no panel left, the reference input is disconnected: the shot renders from the
prompt (and its keyframes, if any).

Keyframes (optional): the first and/or last keyframe become VACE control
frames, the standard first/last recipe: control_video is [first] + mid-grey
frames + [last], control_masks is 0 (keep) on a keyframe and 1 (generate)
elsewhere, built from core nodes (LoadImage, ImageScale, EmptyImage,
ImageBatch, ImageToMask).

Stdlib only.
"""
from __future__ import annotations

import os
import uuid

import targets as TG
from targets.video.ltx2_ingredients import sheet as S
from targets.video.wan import common as C

ID = "wan22_vace"
TARGET = TG.load_target(ID, "video")
RECIPE = TARGET.recipe
REF = RECIPE["reference_image"]
CONTROL = RECIPE.get("control") or {}

compile_episode = C.compile_episode
compile_shot = C.compile_shot
compile_without = C.compile_without
required_refs = C.required_refs
ref_slots = C.ref_slots


# ---------------------------------------------------------------------------
# queue time: the reference image
# ---------------------------------------------------------------------------

def reference_panels(job) -> tuple[list[dict], list[dict]]:
    """(panels whose file is on disk, panels whose file isn't) of the entry
    the job renders (the recompile, rendering anyway)."""
    entry = job.recompiled or job.shot
    have, gone = [], []
    for p in entry.get("panels") or []:
        ok = p.get("path") and os.path.isfile(C.abs_path(job.root, p["path"]))
        (have if ok else gone).append(p)
    return have, gone


def reference_spec(job, panels: list[dict]) -> dict:
    """What comfy_nodes/h3_refsheet.py composes: the render size, white, and
    each panel's file (a character's one view of its 4-panel strip)."""
    n = int(REF.get("sheet_panels", 4))
    out = []
    for p in panels:
        d = {"path": os.path.abspath(C.abs_path(job.root, p["path"])),
             "fit": "cover" if p.get("kind") == "plate" else "figure"}
        if p.get("kind") == "character":
            d["crop"] = {"panels": n, "index": int(REF["views"][p.get("view", "body")])}
        out.append(d)
    return {"width": job.width, "height": job.height,
            "background": REF.get("background", "white"),
            "gap": REF.get("gap", 0.02), "panels": out}


def _label(p: dict) -> str:
    return p.get("subject") or f"the plate ({p.get('location')})"


def stage_inputs(target, job, comfy=None) -> dict:
    """Compose the job's reference image and upload it to ComfyUI's input
    folder: {"reference": "h3pipe/<sha1>.png"}. It waits in the shot's folder
    until h3jobs.start_job moves it into the take (job.staged). Without
    `comfy` (a dry run) nothing is composed or written: the name is a
    stand-in. No panel on disk: {} and the shot renders without a reference."""
    import h3jobs as J
    import h3takes as T
    have, gone = reference_panels(job)
    if gone and have:
        job.notes.append("the reference image leaves out " + ", ".join(_label(p) for p in gone)
                         + " (no file on disk)")
    if not have:
        if (job.recompiled or job.shot).get("subjects"):
            job.notes.append(f"no reference image: nothing to put on it, so {target.short} "
                             f"renders this shot from the prompt alone")
        return {}
    spec = reference_spec(job, have)
    if comfy is None:
        return {"reference": f"{J.INPUT_SUBFOLDER}/dry_run_{T.safe_id(job.id)}_reference.png"}
    d = T.shot_dir(job.root, job.pass_, job.id, job.folder)
    os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, f".tmp_reference_{uuid.uuid4().hex}.png")
    try:
        S.compose(spec, tmp)
        name = J.input_name(tmp)
        comfy.upload_input(tmp, name)
    except BaseException:
        if os.path.isfile(tmp):
            os.remove(tmp)
        raise
    job.staged.append({"tmp": tmp, "suffix": REF.get("take_suffix", "_reference.png"),
                       "slot": "reference image", "kind": "image", "role": "reference"})
    return {"reference": name}


# ---------------------------------------------------------------------------
# the graph
# ---------------------------------------------------------------------------

def _add(g: dict, stem: str, ctype: str, title: str, **inputs) -> str:
    nid = C.new_id(g, stem)
    g[nid] = {"class_type": ctype, "inputs": inputs, "_meta": {"title": f"{title} (h3pipe)"}}
    return nid


def _frames(g: dict, w: int, h: int, n: int, color: int, title: str) -> str:
    return _add(g, "h3_vace_fill_", "EmptyImage", title, width=w, height=h, batch_size=n,
                color=int(color))


def _batch(g: dict, parts: list[str], title: str) -> str:
    out = parts[0]
    for p in parts[1:]:
        out = _add(g, "h3_vace_batch_", "ImageBatch", title, image1=[out, 0], image2=[p, 0])
    return out


def add_control(g: dict, vace: str, job, first: str | None, last: str | None) -> None:
    """The keyframes as VACE control frames: control_video = [first] + grey +
    [last] (each keyframe scaled and centre-cropped to the render size, as
    the node itself would), control_masks = 0 on a keyframe, 1 elsewhere."""
    w, h, n = int(job.width), int(job.height), int(job.frames)
    ends = [e for e in (first, last) if e]
    middle = n - len(ends)
    if middle < 1:
        raise ValueError(f"{job.id}: {n} frames leave no room between the keyframes")
    grey, keep, gen = (int(CONTROL.get("grey", 0x7F7F7F)), int(CONTROL.get("keep", 0)),
                       int(CONTROL.get("generate", 0xFFFFFF)))
    video, mask = [], []

    def key(name: str, which: str) -> None:
        load = _add(g, "h3_vace_key_", "LoadImage", f"{which} frame", image=name)
        video.append(_add(g, "h3_vace_scale_", "ImageScale", f"{which} frame at the render size",
                          image=[load, 0], upscale_method="lanczos", width=w, height=h,
                          crop="center"))
        mask.append(_frames(g, w, h, 1, keep, f"keep the {which} frame"))

    if first:
        key(first, "first")
    video.append(_frames(g, w, h, middle, grey, "frames to generate"))
    mask.append(_frames(g, w, h, middle, gen, "generate these frames"))
    if last:
        key(last, "last")
    cv = _batch(g, video, "control video")
    cm = _add(g, "h3_vace_mask_", "ImageToMask", "control masks",
              image=[_batch(g, mask, "control mask frames"), 0], channel="red")
    g[vace]["inputs"]["control_video"] = [cv, 0]
    g[vace]["inputs"]["control_masks"] = [cm, 0]


def patch_graph(target, g: dict, job, inputs: dict) -> None:
    """`inputs["reference"]` (a name LoadImage reads) is WanVaceToVideo's
    reference_image; without it the input is disconnected (prune drops the
    LoadImage, and the node's trim_latent is 0). `inputs["first"]` /
    `inputs["last"]` become control frames (add_control). Then the two
    samplers' step ranges (common.split_stages)."""
    vace = C.one(g, "WanVaceToVideo", what=target.id)
    vi = g[vace]["inputs"]
    if inputs.get("reference"):
        src = vi.get("reference_image")
        if not (isinstance(src, list) and g.get(src[0], {}).get("class_type") == "LoadImage"):
            raise ValueError(f"the {target.id} workflow's WanVaceToVideo must read its "
                             f"reference_image from a LoadImage")
        g[src[0]]["inputs"]["image"] = inputs["reference"]
    else:
        have, _ = reference_panels(job)
        if have:
            raise ValueError(f"{job.id}: its reference image wasn't staged "
                             f"(h3jobs.stage_inputs composes and uploads it)")
        vi.pop("reference_image", None)
    if inputs.get("first") or inputs.get("last"):
        add_control(g, vace, job, inputs.get("first"), inputs.get("last"))
    C.split_stages(target, g, job)
