"""
LTX-2.3 ingredients: the story IR compiled into shotlist entries for ComfyUI's
LTX-2.3 IC-LoRA "ingredients" template (target.json beside this file), which
keeps recurring characters, props and the set looking like their refs by
conditioning the clip on one composite reference sheet.

    compile_episode(target, story, series_cfg, pass_, only=None) -> (doc, report)
    compile_shot(target, shot_ir, series_cfg, preset, ctx) -> one entry
    compile_without(target, story, series_cfg, pass_, entry, missing) -> entry
    required_refs(target, shot_ir, series_cfg, ctx) -> [RefRequest]
    ref_slots(target, doc, shot) -> one slot per sheet panel
    stage_inputs(target, job, comfy) -> {"sheet": name}   (composes + uploads)
    patch_graph(target, graph, job, inputs) -> the sheet into LoadImage, or text-only

An entry carries the neutral keys every target's entries do (id, sequence,
subjects, background, size, length, seed, steps, audio_policy, prompt, plus
duration / audio_in-audio_out), LTX's `negative`, audio_intent / audio_note
when the script asked for a policy LTX can't render, and `panels`: the sheet,
in order, each {"subject", "kind", "path", "view"?} or {"location", "kind":
"plate", "path"}. Paths are the series config's (the picked, live files).

Lengths: the shot's own, on the 8k+1 grid from 49 to 481 frames (2-20 s);
the sheet loops to exactly that many frames (RepeatImageBatch), since
LTXVAddGuide only asks that a guide be no longer than the output. The
IC-LoRA was trained at 121 frames (recipe.trained_frames): other lengths get
one soft warning listing them. `dur: model` renders the estimate (LTX-2.3 has
no duration head), with a warning and a note in the take.

The sheet itself is made at queue time (stage_inputs), from the files on disk
then: sheet.py runs comfy_nodes/h3_refsheet.py (PIL) with the running Python,
the PNG is uploaded to ComfyUI's input folder, and h3jobs.start_job moves it
into the take as <shot>_tNN_refsheet.png and records its sha1 in the sidecar's
`refs` (beside the panel files', which are what make a take `ref`-stale when a
view is re-picked).

Missing refs: every panel's file is required (the shot is blocked, as H3's
are). Rendering anyway recompiles the shot without them (compile_without):
they leave the sheet and the `Reference sheet:` half of the prompt, and stay
described in words in the prose. With no panel left, the shot renders
text-only: patch_graph takes the reference video, its guide and the IC-LoRA
out of the graph, and the prompt is plain ltx2 prose.

The IC-LoRA is the presets' LoRA. A LoRA list from a script line or profile
(written for another model, `lora: none` included) that doesn't name it gets
it put back first at stage time, and the take's notes say so.

Stdlib only.
"""
from __future__ import annotations

import os
import uuid

import targets as TG
from h3core import ir
from h3core.ir import stable_seed
from h3core.speech import RATE_CEILING, SPEECH_RATE, forced_rate, pacing, speech_seconds
from targets.video.ltx2.compile import (_consumers, _duration, _lines, _of,
                                        put_model_duration, shotlist_extra)

from . import sheet as S
from .prompt import build_prompt

ID = "ltx2_ingredients"
TARGET = TG.load_target(ID, "video")
RECIPE = TARGET.recipe
SHEET = RECIPE["reference_sheet"]
IC_LORA = RECIPE["ic_lora"]
PLATE = "plate"                      # the absent-set name of the plate
BUCKET = int(RECIPE["trained_frames"])   # the IC-LoRA's training length


def _hint(kind: str) -> str:
    return RECIPE["size_hints"].get(kind, "")


def subject_request(sid: str, entry: dict, slot: str | None = None) -> TG.RefRequest:
    if entry.get("kind", "character") == "character":
        r = RECIPE["refs"]["character"]
        label, views = r["kind"], int(r.get("views", 1))
    else:
        r = RECIPE["refs"]["object"]
        label, views = r["kind"].format(kind=entry.get("kind", "prop")), 1
    return TG.RefRequest(entry.get("sheet") or "", label, r["shape"], subject=sid,
                         views=views, slot=slot, entry=entry, size_hint=_hint(label))


def plate_request(key: str, entry: dict, slot: str | None = None) -> TG.RefRequest:
    r = RECIPE["refs"]["location"]
    return TG.RefRequest(entry.get("plate") or "", r["kind"], r["shape"], location=key,
                         slot=slot, entry=entry, size_hint=_hint(r["kind"]))


class Ctx:
    """One pass of one episode: the preset, the size, the report, and the refs
    the shots need. `absent` names refs to compile WITHOUT (render anyway):
    subject ids and/or "plate"."""

    def __init__(self, target, series_cfg: dict, pass_: str, absent: set | None = None):
        self.target, self.series_cfg, self.pass_ = target, series_cfg, pass_
        self.preset = target.preset(pass_, series_cfg)
        self.fps = target.template.fps_for(series_cfg)
        self.warnings: list[str] = []
        own = ((series_cfg.get("series") or {}).get("target")
               or TG.DEFAULT_VIDEO_TARGET) == target.id
        # The IC-LoRA knows one bucket: another target's series block lends no
        # size (Target.preset would pass its width/height on)
        base = self.preset if own else target.presets[pass_]
        w, h = int(base.width), int(base.height)
        self.width, self.height = target.template.fit_size(w, h)
        if (self.width, self.height) != (w, h):
            self.warnings.append(f"{pass_} size {w}x{h} is not legal on {target.short}; "
                                 f"rendering {self.width}x{self.height}")
        self.total_req = self.total_raw = 0
        self.off_bucket: list[str] = []          # "<shot> <frames>" not at BUCKET
        self.fallbacks: dict[str, list[str]] = {}
        self.profiles = TG.series_profiles(series_cfg)
        self.recording = series_cfg.get("audio", {}).get("track", "")
        self.needed: dict[str, dict] = {}
        self.blocked: dict[str, list[str]] = {}
        self.image = TG.image_target(series_cfg)
        self.absent = set(absent or ())

    def need(self, req: TG.RefRequest, shot_id: str) -> None:
        if req.path:
            self.needed.setdefault(req.path, {"kind": req.kind,
                                              "prompt": self.image.ref_prompt(req, self.series_cfg)})
            if shot_id not in self.blocked.setdefault(req.path, []):
                self.blocked[req.path].append(shot_id)


def _ordered(shot: ir.Shot, book: dict) -> list[str]:
    """The shot's subjects in sheet order: characters, then props and
    vehicles, each in script order."""
    subjects = list(shot.cast) + [p for p in shot.props if p not in shot.cast]
    chars = [s for s in subjects if book[s].get("kind", "character") == "character"]
    return chars + [s for s in subjects if s not in chars]


def panel_view(subjects: list[str], book: dict, size: str) -> str:
    """Which panel of a character's 4-panel sheet goes on the reference sheet:
    the face on a single-character close-up, else the three-quarter body (the
    H3 loader's choice)."""
    return ("face" if len(subjects) == 1
            and book[subjects[0]].get("kind", "character") == "character"
            and size in SHEET["face_sizes"] else "body")


def _panels(ctx: Ctx, shot: ir.Shot, loc_key: str) -> list[dict]:
    book, series_cfg = ctx.series_cfg["subjects"], ctx.series_cfg
    subjects = _ordered(shot, book)
    view = panel_view(subjects, book, shot.size)
    out = []
    for s in subjects:
        e = book[s]
        ctx.need(subject_request(s, e), shot.id)
        if s in ctx.absent:
            continue
        p = {"subject": s, "kind": e.get("kind", "character"), "path": e.get("sheet", "")}
        if p["kind"] == "character":
            p["view"] = view
        out.append(p)
    loc = series_cfg["locations"][loc_key]
    if loc.get("plate"):
        ctx.need(plate_request(loc_key, loc), shot.id)
        if PLATE not in ctx.absent:
            out.append({"location": loc_key, "kind": "plate", "path": loc["plate"]})
    return out


def _compile(ctx: Ctx, sq: ir.Sequence, shot: ir.Shot, ep_id: str) -> dict:
    series_cfg, template = ctx.series_cfg, ctx.target.template
    book = series_cfg["subjects"]
    for s in list(shot.cast) + list(shot.props):
        if s not in book:
            raise ValueError(f"shot {shot.id}: '{s}' is not in series.json's subjects")
    loc_key = shot.plate or sq.location
    if loc_key not in series_cfg["locations"]:
        raise ValueError(f"shot {shot.id}: location '{loc_key}' is not in series.json "
                         f"({', '.join(series_cfg['locations'])})")
    if not shot.cast and not shot.props and not shot.dialogue and not shot.action:
        raise ValueError(f"shot {shot.id}: nothing in it. Add `who:`, `with:`, action "
                         f"text, or dialogue.")
    pace = shot.pace or series_cfg.get("speech", {}).get("pace", "normal")
    if pace not in SPEECH_RATE:
        raise ValueError(f"series.json speech.pace '{pace}' must be one of {sorted(SPEECH_RATE)}")
    dur, windowed = _duration(ctx, shot, pace)
    fps = ctx.fps
    req = max(1, round(dur * fps))
    try:
        raw = template.snap(req)
    except ValueError:
        raise ValueError(f"shot {shot.id}: {dur:.2f}s is longer than {ctx.target.short}'s "
                         f"maximum of {template.max} frames ({template.max / fps:.2f}s at "
                         f"{fps:g} fps). Split the shot, or render it on another "
                         f"target.") from None
    if shot.dialogue:
        held = raw / fps
        rate = forced_rate(_lines(shot), held)
        syl, _ = pacing(_lines(shot))
        if rate > RATE_CEILING:
            ctx.warnings.append(f"{shot.id}: CRAMMED — {syl} syllables in {held:.2f}s forces "
                                f"{rate:.1f} syl/s (ceiling {RATE_CEILING}). Split the shot.")
    if raw != BUCKET:
        ctx.off_bucket.append(f"{shot.id} {raw}")
    ctx.total_req += req
    ctx.total_raw += raw

    intent = TG.audio_intent(shot, series_cfg, ctx.pass_)
    policy, note = ctx.target.audio_policy(intent)
    if note:
        ctx.fallbacks.setdefault(intent, []).append(shot.id)

    seq = {"id": sq.id, "profile": sq.profile, "target": sq.target,
           **{k: v for k, v in (sq.overrides or {}).items() if v is not None}}
    sh = {"id": shot.id, "profile": shot.profile, "target": shot.target,
          **{k: v for k, v in (shot.overrides or {}).items() if v is not None}}
    for node in (sq, shot):
        if "steps" in node.unparsed:
            raise ValueError(f"{'shot' if node is shot else 'sequence'} {node.id}: steps "
                             f"must be a whole number, not {node.unparsed['steps']!r}")
    layers = TG.render_layers(series_cfg, seq, sh, ctx.profiles)
    panels = _panels(ctx, shot, loc_key)
    entry = {
        "id": shot.id,
        "sequence": sq.id,
        "subjects": list(shot.cast) + [p for p in shot.props if p not in shot.cast],
        "background": series_cfg["locations"][loc_key].get("plate", ""),
        "size": shot.size,
        "length": raw,
        "seed": stable_seed(ep_id, sq.id, shot.id),
        "steps": int(TG.layered(layers, "steps", ctx.preset.steps, present=True)),
        "audio_policy": policy,
        "prompt": build_prompt(shot, sq, series_cfg, panels),
        "negative": ctx.preset.extra.get("negative", ""),
        "panels": panels,
    }
    if note:
        entry["audio_intent"] = intent
        entry["audio_note"] = note
    model = TG.layered(layers, "model")
    if model:
        entry["model"] = model
    lora = TG.layered_lora(layers)
    if lora:
        entry[lora[0]] = lora[1]
    profile = shot.profile or sq.profile
    if profile:
        entry["profile"] = profile
    t = shot.timing or {}
    if windowed:
        entry["audio_in"], entry["audio_out"] = t["audio_in"], t["audio_out"]
    else:
        entry["duration"] = round(dur, 3)
    put_model_duration(ctx, shot, pace, entry)
    return entry


def _episode(target, story: ir.Episode, series_cfg: dict, pass_: str,
             only: set[str] | None = None, absent: set | None = None) -> tuple[dict, dict]:
    ctx = Ctx(target, series_cfg, pass_, absent)
    shots_out, seqs = [], 0
    for sq in story.sequences:
        mine = [s for s in sq.shots if only is None or s.id in only]
        if mine:
            seqs += 1
        for s in mine:
            shots_out.append(_compile(ctx, sq, s, story.id))
    p = ctx.preset
    extra = shotlist_extra(p)
    doc = {
        "episode": story.id,
        "title": story.title,
        "target": target.id,
        "defaults": {"width": ctx.width, "height": ctx.height, "fps": ctx.fps,
                     "steps": p.steps, "model": p.model, "lora": p.lora,
                     "audio_policy": "generate", "master_track": ctx.recording, **extra},
        "subjects": {s: {"kind": e.get("kind", "character"), "sheet": e.get("sheet", ""),
                         "voice_sample": e.get("voice_sample", "")}
                     for s, e in series_cfg["subjects"].items()},
        "shots": shots_out,
    }
    for intent, ids in sorted(ctx.fallbacks.items()):
        ctx.warnings.append(f"{len(ids)} shot(s) ask for audio {intent}, which {target.short} "
                            f"can't render ({(target.recipe.get('policy_fallback') or {}).get('why', '')}): "
                            f"they render with generate: {', '.join(ids[:8])}"
                            + (" …" if len(ids) > 8 else ""))
    for sh in shots_out:
        for k in ("model", "lora"):
            if k in sh:
                ctx.warnings.append(f"{sh['id']} overrides {k}: {sh[k]}")
        if len(sh["panels"]) > 5:
            ctx.warnings.append(f"{sh['id']}: {len(sh['panels'])} panels on one reference "
                                f"sheet; small panels carry over worse")
    if ctx.off_bucket:
        n = len(ctx.off_bucket)
        ctx.warnings.append(f"the IC-LoRA was trained at {BUCKET} frames; identity may weaken "
                            f"at other lengths ({n} shot{'s' if n > 1 else ''}, frames): "
                            + ", ".join(ctx.off_bucket[:12]) + (" …" if n > 12 else ""))
    fps = ctx.fps
    report = {
        "episode": story.id, "title": story.title,
        "mode": "proxy" if pass_ == "proxy" else "final",
        "resolution": f"{ctx.width}x{ctx.height}", "steps": p.steps,
        "model": p.model, "lora": p.lora or "none", "fps": fps,
        "shots": len(shots_out), "sequences": seqs,
        "target_s": ctx.total_req / fps, "delivered_s": ctx.total_raw / fps,
        "pad_frames": ctx.total_raw - ctx.total_req,
        "policies": {pol: sum(1 for s in shots_out if s["audio_policy"] == pol)
                     for pol in sorted({s["audio_policy"] for s in shots_out})},
        "needed": ctx.needed, "blocked_shots": ctx.blocked, "warnings": ctx.warnings,
        "target": target.id, "size_hints": dict(RECIPE.get("size_hints") or {}),
    }
    return doc, report


def compile_episode(target, story: ir.Episode, series_cfg: dict, pass_: str,
                    only: set[str] | None = None) -> tuple[dict, dict]:
    return _episode(target, story, series_cfg, pass_, only)


def compile_shot(target, shot_ir: ir.Shot, series_cfg: dict, preset, ctx=None) -> dict:
    """One shot's entry, as compile_episode builds it. `ctx` needs
    {"episode": ir.Episode}, and may give {"absent": [subject ids, "plate"]}."""
    ctx = dict(ctx or {})
    story = ctx.get("episode")
    if story is None:
        raise ValueError("compile_shot needs ctx['episode'], the shot's ir.Episode")
    pass_ = preset if isinstance(preset, str) else preset.name
    doc, _ = _episode(target, story, series_cfg, pass_, {shot_ir.id}, ctx.get("absent"))
    if not doc["shots"]:
        raise ValueError(f"{shot_ir.id} is not in episode {story.id}")
    return doc["shots"][0]


def compile_without(target, story: ir.Episode, series_cfg: dict, pass_: str,
                    entry: dict, missing: list[dict]) -> dict:
    """`entry` compiled again as if the `missing` refs (ref_slots dicts)
    didn't exist: they leave the sheet and the prompt's `Reference sheet:`
    half. ValueError when the build is out of date (h3jobs then renders the
    built entry, the sheet made of the files that exist)."""
    shot_ir = next((s for s in story.shots() if s.id == entry["id"]), None)
    if shot_ir is None:
        raise ValueError(f"{entry['id']} is not in shots.json")
    if compile_shot(target, shot_ir, series_cfg, pass_, {"episode": story}) != entry:
        raise ValueError(f"{entry['id']}: the build is out of date (rebuild the episode)")
    absent = {r["subject"] for r in missing if r.get("subject")}
    if any(r.get("location") for r in missing):
        absent.add(PLATE)
    return compile_shot(target, shot_ir, series_cfg, pass_, {"episode": story, "absent": absent})


def required_refs(target, shot_ir: ir.Shot, series_cfg: dict, ctx=None) -> list[TG.RefRequest]:
    """The refs a shot's sheet is made of, in panel order. `ctx` may give
    {"sequence": ir.Sequence} (the location when the shot names no plate)."""
    ctx = dict(ctx or {})
    book = series_cfg["subjects"]
    out = [subject_request(s, book[s], f"sheet panel {i}")
           for i, s in enumerate(_ordered(shot_ir, book), 1)]
    key = shot_ir.plate or (ctx["sequence"].location if ctx.get("sequence") else "")
    loc = series_cfg["locations"].get(key)
    if loc and loc.get("plate"):
        out.append(plate_request(key, loc, f"sheet panel {len(out) + 1}"))
    return out


def ref_slots(target, doc: dict, shot: dict) -> list[dict]:
    """One slot per sheet panel: {slot, kind "image", path, subject | location}.
    Required: a missing file blocks the shot unless rendering anyway."""
    out = []
    for i, p in enumerate(shot.get("panels") or [], 1):
        r = {"slot": f"sheet panel {i}", "kind": "image", "path": p.get("path", "")}
        if p.get("subject"):
            r["subject"] = p["subject"]
        else:
            r["location"] = p.get("location")
        out.append(r)
    return out


# ---------------------------------------------------------------------------
# queue time: the sheet
# ---------------------------------------------------------------------------

def _abs(root: str, p: str) -> str:
    return p if os.path.isabs(p) else os.path.join(root, p)


def sheet_panels(job) -> tuple[list[dict], list[dict]]:
    """(panels whose file is on disk, panels whose file isn't) of the entry
    the job renders (the recompile, rendering anyway)."""
    entry = job.recompiled or job.shot
    have, gone = [], []
    for p in entry.get("panels") or []:
        (have if p.get("path") and os.path.isfile(_abs(job.root, p["path"])) else gone).append(p)
    return have, gone


def sheet_spec(job, panels: list[dict]) -> dict:
    """What comfy_nodes/h3_refsheet.py composes: the render size, black, and
    each panel's file (a character's one view of its 4-panel strip)."""
    n = int(SHEET.get("sheet_panels", 4))
    out = []
    for p in panels:
        d = {"path": os.path.abspath(_abs(job.root, p["path"])),
             "fit": "cover" if p.get("kind") == "plate" else "figure"}
        if p.get("kind") == "character":
            d["crop"] = {"panels": n, "index": int(SHEET["views"][p.get("view", "body")])}
        out.append(d)
    return {"width": job.width, "height": job.height,
            "background": SHEET.get("background", "black"),
            "gap": SHEET.get("gap", 0.02), "panels": out}


def _label(p: dict) -> str:
    return p.get("subject") or f"the plate ({p.get('location')})"


def stage_inputs(target, job, comfy=None) -> dict:
    """Compose the job's reference sheet and upload it to ComfyUI's input
    folder: {"sheet": "h3pipe/<sha1>.png"}. It waits in the shot's folder
    until h3jobs.start_job moves it into the take (job.staged). Without
    `comfy` (a dry run) nothing is composed or written: the name is a stand-in.
    No panel on disk: {} and the job renders text-only."""
    import h3jobs as J
    import h3takes as T
    have, gone = sheet_panels(job)
    if gone and have:
        job.notes.append("the reference sheet leaves out " + ", ".join(_label(p) for p in gone)
                         + " (no file on disk)")
    if not have:
        job.notes.append(f"no reference sheet: nothing to put on it, so {target.short} "
                         f"renders this shot text-only, without the IC-LoRA")
        return {}
    if job.loras is not None and IC_LORA not in [lo.get("name") for lo in job.loras]:
        # a script line or profile written for another model named the LoRAs
        # (`lora: none` included): the sheet means nothing without this one
        job.loras = [{"name": IC_LORA, "strength": 1.0}] + list(job.loras)
        job.notes.append(f"{IC_LORA} was put first in the LoRA list, which didn't "
                         f"name it: the reference sheet needs it")
    spec = sheet_spec(job, have)
    if comfy is None:
        return {"sheet": f"{J.INPUT_SUBFOLDER}/dry_run_{T.safe_id(job.id)}_refsheet.png"}
    d = T.shot_dir(job.root, job.pass_, job.id, job.folder)
    os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, f".tmp_refsheet_{uuid.uuid4().hex}.png")
    try:
        S.compose(spec, tmp)
        name = J.input_name(tmp)
        comfy.upload_input(tmp, name)
    except BaseException:
        if os.path.isfile(tmp):
            os.remove(tmp)
        raise
    job.staged.append({"tmp": tmp, "suffix": SHEET["take_suffix"], "slot": "reference sheet",
                       "kind": "image", "role": "sheet"})
    return {"sheet": name}


# ---------------------------------------------------------------------------
# the graph
# ---------------------------------------------------------------------------

def _splice(g: dict, nid: str, through: dict[int, str]) -> None:
    """Delete node `nid`, wiring each consumer of its output `slot` to what
    fed its input `through[slot]`."""
    for slot, name in through.items():
        src = g[nid]["inputs"][name]
        for c, inp in _consumers(g, nid, slot):
            g[c]["inputs"][inp] = list(src)
    del g[nid]


def patch_graph(target, g: dict, job, inputs: dict) -> None:
    """`inputs["sheet"]` (the name LoadImage reads) is the reference sheet;
    RepeatImageBatch and ResizeAndPadImage (patched with the length and size)
    make it the static reference video LTXVAddGuide feeds the IC-LoRA. With
    no sheet the shot is text-only: the guide, its crop and the IC-LoRA come
    out and prune drops the rest of the reference path."""
    if inputs.get("sheet"):
        loads = [k for k in _of(g, "LoadImage")
                 if any(g[c]["class_type"] == "RepeatImageBatch" for c, _ in _consumers(g, k))]
        if len(loads) != 1:
            raise ValueError(f"the {target.id} workflow must have one LoadImage feeding "
                             f"RepeatImageBatch (found {len(loads)})")
        g[loads[0]]["inputs"]["image"] = inputs["sheet"]
        return
    have, _ = sheet_panels(job)
    if have:
        raise ValueError(f"{job.id}: its reference sheet wasn't staged "
                         f"(h3jobs.stage_inputs composes and uploads it)")
    for k in _of(g, "LTXVAddGuide"):
        _splice(g, k, {0: "positive", 1: "negative", 2: "latent"})
    for k in _of(g, "LTXVCropGuides"):
        _splice(g, k, {0: "positive", 1: "negative", 2: "latent"})
    for k in _of(g, "LoraLoaderModelOnly"):
        if g[k]["inputs"].get("lora_name") == IC_LORA:
            _splice(g, k, {0: "model"})
