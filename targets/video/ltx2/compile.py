"""
LTX-2.5 (distilled): the story IR compiled into shotlist entries that h3jobs
patches straight into ComfyUI's LTX-2.5 image-to-video workflow (target.json
beside this file). There is no loader node: every render value is a widget.

    compile_episode(target, story, series_cfg, pass_, only=None) -> (doc, report)
    compile_shot(target, shot_ir, series_cfg, preset, ctx) -> one entry
    required_refs(target, shot_ir, series_cfg, ctx) -> []   (nothing is required)
    ref_slots(target, doc, shot) -> the optional keyframes and sheet panels
    stage_inputs(target, job, comfy) -> the reference sheet, when there is one
    patch_graph(target, graph, job, inputs) -> keyframes, the reference sheet
                                               and the sampling schedule

Two things are decided at QUEUE time, not at build time, so a shotlist is
exactly what it always was:

  * **The reference sheet** (recipe.reference_sheet). When the shot's subjects
    and plate have picked ref files on disk AND the LTX-2.5 ingredients
    IC-LoRA is installed (`reference_lora`, an optional model param), the
    panels are composed into one sheet exactly as ltx2_ingredients composes
    its own, the sheet conditions the clip through that IC-LoRA, and the
    prompt becomes the IC-LoRA's two labelled parts (the ingredients prompt
    writer). Without the files or the LoRA the shot renders as it always did:
    subjects in words, no sheet. `lora: none` (a script line, a profile or the
    series config's pass block) turns the sheet off for a shot.
  * **The sampling schedule** (recipe.sampling), from the transformer the job
    loads: the distilled file keeps the workflow's fixed 8 + 3 step schedule
    at cfg 1, the dev (non-distilled) transformer gets an LTXVScheduler at the
    shot's `steps` and real guidance — the quality profile, selected by naming
    the dev file as the shot's / sequence's / episode's model.

A shotlist entry carries the same neutral keys as H3's (the editor's shot bin
reads them): id, sequence, subjects, background, size, length, seed, steps,
audio_policy, prompt, plus duration / audio_in-audio_out. LTX's own: negative,
keyframes {first, last} (the conventional paths; optional), audio_intent and
audio_note when the script asked for a policy LTX can't render, and for
`dur: model` `length_estimated` (its `duration`/`length` are the build's
estimate) plus `duration_predict` {min_seconds, max_seconds} (the duration
head's range, used at queue time by h3jobs when the head is installed). `background`
is the plate the script names; LTX never reads it (the location is described
in words), it is there so every target's entries look alike.

Stdlib only.
"""
from __future__ import annotations

import os
import re
import uuid

import targets as TG
from h3core import ir
from h3core.ir import stable_seed
from h3core.speech import RATE_CEILING, SPEECH_RATE, forced_rate, pacing, speech_seconds

from .prompt import build_prompt

ID = "ltx2"
TARGET = TG.load_target(ID, "video")
RECIPE = TARGET.recipe
KEYFRAMES = tuple(RECIPE.get("keyframes") or ())
SHEET = dict(RECIPE.get("reference_sheet") or {})
SAMPLING = dict(RECIPE.get("sampling") or {})


def keyframe_path(shot_id: str, end: str) -> str:
    """Where a shot's keyframe lives (h3refs' `shot:<id>:<end>` ref)."""
    return RECIPE["keyframe_path"].format(shot=re.sub(r"[^A-Za-z0-9_.-]", "_", shot_id),
                                          end=end)


def _lines(shot: ir.Shot) -> list[dict]:
    return [{"who": d.speaker, "line": d.line} for d in shot.dialogue]


class Ctx:
    """One pass of one episode: the preset, the legal size, and the report."""

    def __init__(self, target, series_cfg: dict, pass_: str):
        self.target, self.series_cfg, self.pass_ = target, series_cfg, pass_
        self.preset = target.preset(pass_, series_cfg)
        self.fps = target.template.fps_for(series_cfg)
        self.warnings: list[str] = []
        w, h = int(self.preset.width), int(self.preset.height)
        self.width, self.height = target.template.fit_size(w, h)
        if (self.width, self.height) != (w, h):
            self.warnings.append(f"{pass_} size {w}x{h} is not legal on {target.short}; "
                                 f"rendering {self.width}x{self.height}")
        self.total_req = self.total_raw = 0
        self.fallbacks: dict[str, list[str]] = {}
        self.profiles = TG.series_profiles(series_cfg)
        audio_cfg = series_cfg.get("audio", {})
        self.recording = audio_cfg.get("track", "")


def model_duration(ctx, shot: ir.Shot, pace: str) -> tuple[float, dict | None, str]:
    """`dur: model`: (the estimate, the predictor's range or None, a warning
    or ""); see targets.duration_estimate. The range never passes the
    template's longest shot."""
    speech = speech_seconds(_lines(shot), pace) if shot.dialogue else None
    est, predict, note = TG.duration_estimate(ctx.target, shot.timing or {}, ctx.preset, speech)
    if predict:
        top = round(ctx.target.template.max / ctx.fps, 2)
        predict["max_seconds"] = min(predict["max_seconds"], top)
        # LTXVDurationPredictor takes 0.5 s at least
        predict["min_seconds"] = max(0.5, min(predict["min_seconds"], predict["max_seconds"]))
        est = min(max(est, predict["min_seconds"]), predict["max_seconds"])
    return est, predict, note


def put_model_duration(ctx, shot: ir.Shot, pace: str, entry: dict) -> None:
    """A `dur: model` entry's own keys: `length_estimated` (its length is the
    build's estimate) and, on a target that predicts, `duration_predict`
    {"min_seconds", "max_seconds"}; elsewhere a warning that the estimate is
    what renders."""
    if not (shot.timing or {}).get("model"):
        return
    _, predict, note = model_duration(ctx, shot, pace)
    entry["length_estimated"] = True
    if predict:
        entry["duration_predict"] = predict
    if note:
        ctx.warnings.append(f"{shot.id}: {note}")


def shotlist_extra(preset) -> dict:
    """The preset values a shotlist's `defaults` carries (not the `dur: model`
    ones, which h3jobs reads from the target)."""
    return {k: v for k, v in preset.extra.items()
            if not k.startswith("_") and k not in TG.DURATION_PRESET_KEYS}


def _duration(ctx: Ctx, shot: ir.Shot, pace: str) -> tuple[float, bool]:
    """(seconds, from a recording window) as the script asks."""
    t = shot.timing or {}
    if "audio_in" in t:
        return t["audio_out"] - t["audio_in"], True
    if t.get("model"):
        return model_duration(ctx, shot, pace)[0], False
    if t.get("auto"):
        if not shot.dialogue:
            raise ValueError(f"shot {shot.id}: `dur: auto` needs dialogue to measure. "
                             f"Give a silent shot an explicit `dur:`.")
        return speech_seconds(_lines(shot), pace), False
    if "seconds" in t:
        return float(t["seconds"]), False
    raise ValueError(f"shot {shot.id}: needs `audio: 3.10-7.40`, `dur: 3.04`, or `dur: auto`")


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
    raw = template.snap(req)
    if shot.dialogue:
        held = raw / fps
        rate = forced_rate(_lines(shot), held)
        syl, _ = pacing(_lines(shot))
        if rate > RATE_CEILING:
            fits = template.snap(max(1, round(speech_seconds(_lines(shot), pace) * fps))) / fps
            ctx.warnings.append(f"{shot.id}: CRAMMED — {syl} syllables in {held:.2f}s forces "
                                f"{rate:.1f} syl/s (ceiling {RATE_CEILING}). `dur: {fits:.2f}` "
                                f"gives it room.")
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
        "prompt": build_prompt(shot, sq, series_cfg),
        "negative": ctx.preset.extra.get("negative", ""),
        "keyframes": {end: keyframe_path(shot.id, end)
                      for end in TG.keyframe_ends(RECIPE, shot, sq)},
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


def compile_episode(target, story: ir.Episode, series_cfg: dict, pass_: str,
                    only: set[str] | None = None) -> tuple[dict, dict]:
    ctx = Ctx(target, series_cfg, pass_)
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
        "needed": {}, "blocked_shots": {}, "warnings": ctx.warnings,
        "target": target.id, "size_hints": dict(RECIPE.get("size_hints") or {}),
    }
    return doc, report


def compile_shot(target, shot_ir: ir.Shot, series_cfg: dict, preset, ctx=None) -> dict:
    """One shot's entry, as compile_episode builds it. `ctx` needs
    {"episode": ir.Episode}."""
    story = (ctx or {}).get("episode")
    if story is None:
        raise ValueError("compile_shot needs ctx['episode'], the shot's ir.Episode")
    pass_ = preset if isinstance(preset, str) else preset.name
    doc, _ = compile_episode(target, story, series_cfg, pass_, only={shot_ir.id})
    if not doc["shots"]:
        raise ValueError(f"{shot_ir.id} is not in episode {story.id}")
    return doc["shots"][0]


def required_refs(target, shot_ir: ir.Shot, series_cfg: dict, ctx=None) -> list:
    """Nothing is required: subjects and the location are written in words,
    and keyframes are optional (a shot without them is text-to-video)."""
    return []


def sheet_panels(doc: dict, shot: dict) -> list[dict]:
    """The reference sheet this shot WOULD have, in panel order, from its
    shotlist entry alone: characters (the shot's cast and props, in the order
    the entry lists them), then the plate. Each is ltx2_ingredients' panel
    dict ({"subject" | "location", "kind", "path", "view"?}), so the sheet and
    the prompt can be made with that target's code. A panel whose file the
    series config doesn't name is left out; whether the file is on disk is
    decided at queue time (sheet_for)."""
    if not SHEET:
        return []
    book = doc.get("subjects") or {}
    ids = [s for s in (shot.get("subjects") or []) if s in book]
    chars = [s for s in ids if (book[s].get("kind") or "character") == "character"]
    ordered = chars + [s for s in ids if s not in chars]
    view = "face" if (len(ordered) == 1 and ordered[0] in chars
                      and shot.get("size") in (SHEET.get("face_sizes") or ())) else "body"
    out = []
    for s in ordered:
        e = book[s]
        if not e.get("sheet"):
            continue
        kind = e.get("kind") or "character"
        p = {"subject": s, "kind": kind, "path": e["sheet"]}
        if kind == "character":
            p["view"] = view
        out.append(p)
    if shot.get("background"):
        out.append({"location": None, "kind": "plate", "path": shot["background"]})
    return out


def ref_slots(target, doc: dict, shot: dict) -> list[dict]:
    """The keyframes a render reads when they exist, then the panels of the
    reference sheet it would draw. All `optional`: a missing one never blocks
    a render (h3jobs.missing_refs skips them), it only leaves that element off
    the sheet. The panels carry no `role`, so h3jobs.stage_inputs doesn't
    upload them one by one — stage_inputs below composes them into one sheet;
    their sha1 in the sidecar is what makes a take `ref`-stale when a view is
    re-picked."""
    out = [{"slot": f"{end} frame", "kind": "image", "path": p, "role": end,
            "optional": True}
           for end, p in (shot.get("keyframes") or {}).items()]
    for i, p in enumerate(sheet_panels(doc, shot), 1):
        r = {"slot": f"sheet panel {i}", "kind": "image", "path": p.get("path", ""),
             "optional": True}
        if p.get("subject"):
            r["subject"] = p["subject"]
        out.append(r)
    return out


# ---------------------------------------------------------------------------
# queue time: the sampling schedule, then the reference sheet
# ---------------------------------------------------------------------------

def _abs(root: str, p: str) -> str:
    return p if os.path.isabs(p) else os.path.join(root, p)


def sampling_mode(model: str, sheet: bool = False) -> tuple[str, dict]:
    """(the name of the sampling mode `model` wants, its settings) from
    recipe.sampling: the first mode whose `match` globs the file name, else
    the recipe's `default`. With `sheet` the mode's `with_sheet` block is
    laid over it — what the same transformer wants when the IC-LoRA is also
    reading a reference sheet in context."""
    modes = SAMPLING.get("modes") or {}
    found = None
    for mode, spec in modes.items():
        if mode.startswith("_") or not isinstance(spec, dict):
            continue
        if TG.modelid.name_matches(model or "", spec.get("match") or []):
            found = (mode, dict(spec))
            break
    if found is None:
        dflt = SAMPLING.get("default") or "distilled"
        found = (dflt, dict(modes.get(dflt) or {}))
    mode, spec = found
    over = spec.pop("with_sheet", None)
    if sheet and isinstance(over, dict):
        spec.update({k: v for k, v in over.items() if not k.startswith("_")})
    return mode, spec


def _patches(spec: dict) -> bool:
    """Whether a sampling mode changes the workflow's own schedule."""
    return bool(spec.get("steps") or spec.get("video_cfg") or spec.get("scheduler"))


def sampling_plan(job, sheet: bool = False) -> None:
    """Settle the job's sampling before its take is reserved: the mode the
    transformer it loads wants (sampling_mode, with the `with_sheet` layer
    when this shot also draws a reference sheet). The distilled file keeps
    the workflow's fixed schedule, so a `steps` set for the shot only earns a
    note saying it can't bite. The dev transformer takes the mode's own step
    count unless the shot asked for another, and its guidance and sampler go
    into `job.values`, so the take's frozen shotlist and sidecar record what
    really rendered (patch_graph puts the same numbers in the graph)."""
    mode, spec = sampling_mode(job.model or "", sheet)
    built = int((job.doc.get("defaults") or {}).get("steps") or 0)
    if not _patches(spec):
        if job.steps != built:
            job.notes.append(f"steps {job.steps} isn't used by "
                             f"{TG.model_stem(job.model) or 'this model'}: its {mode} schedule "
                             f"is fixed in the workflow. The quality profile (the LTX-2.5 dev "
                             f"transformer) is the one that takes steps.")
        return
    if job.steps == built and spec.get("steps"):
        job.steps = int(spec["steps"])
    refine = dict(spec.get("refine") or {})
    job.values.update({k: v for k, v in
                       {"cfg": spec.get("video_cfg"), "audio_cfg": spec.get("audio_cfg"),
                        "sampler": spec.get("sampler"),
                        "refine_cfg": refine.get("video_cfg"),
                        "refine_sampler": refine.get("sampler")}.items() if v is not None})
    job.notes.append(f"{spec.get('label') or mode}: {job.steps} steps, video_cfg "
                     f"{spec.get('video_cfg')}, audio_cfg {spec.get('audio_cfg')}, "
                     f"{spec.get('sampler')}")


def lora_off(job) -> bool:
    """`lora: none` (a script line, a profile's empty list, or the series
    config's pass block) — the one switch that turns the sheet off."""
    return job.loras is not None and not job.loras


def reference_lora(job) -> str | None:
    """The ingredients IC-LoRA file this job would load, or None when it isn't
    installed. h3jobs.resolve_models has already resolved the `reference_lora`
    param against what ComfyUI lists (`how: "off"` means nothing of the family
    is installed); with nothing resolved (a dry run, or ComfyUI not asked) the
    target's own file is assumed, as every other unresolved param is."""
    r = (job.resolved or {}).get("reference_lora")
    if r is None:
        return (TARGET.models.get("reference_lora") or {}).get("default")
    return r.get("using") or None


def sheet_for(job) -> tuple[list[dict], list[dict]]:
    """(panels whose file is on disk, panels whose file isn't) of the shot the
    job renders. ([], []) when the sheet is off for this job."""
    if not SHEET or lora_off(job):
        return [], []
    entry = job.recompiled or job.shot
    have, gone = [], []
    for p in sheet_panels(job.doc, entry):
        (have if p.get("path") and os.path.isfile(_abs(job.root, p["path"])) else gone).append(p)
    return have, gone


def _sheet_prompt(job, panels: list[dict]) -> str | None:
    """The IC-LoRA's two-part prompt for this shot (ltx2_ingredients' writer:
    `Reference sheet: …` then `Generated video: …`, with a subject that is on
    the sheet named but no longer described). None when it can't be written
    here — the build is out of date, or the script and series config aren't
    readable — and then the built prose prompt renders as it is."""
    import h3jobs as J

    from targets.video.ltx2_ingredients.prompt import build_prompt as sheet_prompt
    try:
        story, series_cfg = J.episode_story(job.root)
    except Exception:
        return None
    for sq in story.sequences:
        for s in sq.shots:
            if s.id != job.id:
                continue
            named = [dict(p, location=(s.plate or sq.location)) if p.get("kind") == "plate"
                     else dict(p) for p in panels]
            try:
                return sheet_prompt(s, sq, series_cfg, named)
            except Exception:
                return None
    return None


def stage_inputs(target, job, comfy=None) -> dict:
    """Everything ltx2 settles before its take is reserved: the reference
    sheet (_stage_sheet) and then the sampling schedule (sampling_plan,
    which may raise the job's step count for the quality profile, and which
    needs to know whether a sheet was staged)."""
    out = _stage_sheet(target, job, comfy)
    sampling_plan(job, sheet=bool(out.get("sheet")))
    return out


def _stage_sheet(target, job, comfy=None) -> dict:
    """Compose the job's reference sheet and upload it to ComfyUI's input
    folder: {"sheet": "h3pipe/<sha1>.png"}, or {} when this shot renders
    without one (the LoRA isn't installed, `lora: none`, no panel's file is on
    disk, or the sheet can't be composed — the behaviour ltx2 always had).
    The sheet is composed by ltx2_ingredients' code from this target's own
    `reference_sheet` recipe; it waits in the shot's folder until
    h3jobs.start_job moves it into the take. Without `comfy` (a dry run)
    nothing is composed or written.

    Composing never stops the render here, unlike on ltx2_ingredients where
    the sheet is the whole point: a ref file that isn't a readable image, or
    a Python without PIL, leaves the shot rendering from the prompt alone
    with a note. When there is a sheet the job also gets the IC-LoRA's
    two-part prompt and a `panels` list in the take's frozen shotlist, so the
    take records exactly what was on the sheet."""
    import h3jobs as J
    import h3takes as T

    from targets.video.ltx2_ingredients import compile as ING
    from targets.video.ltx2_ingredients import sheet as S
    have, gone = sheet_for(job)
    if not have:
        return {}
    lora = reference_lora(job)
    if not lora:
        job.notes.append(f"no reference sheet: the ingredients IC-LoRA isn't installed, so "
                         f"{target.short} renders this shot from the prompt alone")
        return {}
    if gone:
        job.notes.append("the reference sheet leaves out "
                         + ", ".join(p.get("subject") or "the plate" for p in gone)
                         + " (no file on disk)")
    spec = ING.sheet_spec(job, have, SHEET)
    if comfy is None:
        name = f"{J.INPUT_SUBFOLDER}/dry_run_{T.safe_id(job.id)}_refsheet.png"
    else:
        d = T.shot_dir(job.root, job.pass_, job.id, job.folder)
        os.makedirs(d, exist_ok=True)
        tmp = os.path.join(d, f".tmp_refsheet_{uuid.uuid4().hex}.png")
        try:
            S.compose(spec, tmp)
            name = J.input_name(tmp)
            comfy.upload_input(tmp, name)
        except Exception as e:
            if os.path.isfile(tmp):
                os.remove(tmp)
            job.notes.append(f"no reference sheet ({e.__class__.__name__}: "
                             f"{str(e)[:200]}): {target.short} renders this shot from the "
                             f"prompt alone")
            return {}
        except BaseException:
            if os.path.isfile(tmp):
                os.remove(tmp)
            raise
        job.staged.append({"tmp": tmp, "suffix": SHEET["take_suffix"], "slot": "reference sheet",
                           "kind": "image", "role": "sheet"})
    entry = job.recompiled or job.shot
    job.recompiled = dict(entry, panels=[dict(p) for p in have])
    built = entry.get("prompt", "")
    if job.prompt == built:                      # an override's prompt is rendered as written
        text = _sheet_prompt(job, have)
        if text:
            job.prompt = text
        else:
            job.notes.append("the reference sheet is used with the built prompt: the "
                             "two-part IC-LoRA prompt needs a current build")
    job.notes.append(f"reference sheet ({len(have)} panel{'s' if len(have) > 1 else ''}) "
                     f"with {TG.model_stem(lora)}")
    floor = int(SHEET.get("min_frames") or 0)
    if floor and int(job.frames or 0) < floor:
        job.notes.append(f"the reference video is {job.frames} frames, under the IC-LoRA's "
                         f"{floor}: identity may weaken (a guide can't be longer than the "
                         f"clip, so only a longer shot reaches the bucket)")
    return {"sheet": name}


# ---------------------------------------------------------------------------
# the graph: keyframes in, or the image path out
# ---------------------------------------------------------------------------

def _consumers(g: dict, nid: str, slot: int = 0) -> list[tuple[str, str]]:
    return [(k, name) for k, v in g.items() for name, val in v["inputs"].items()
            if val == [nid, slot]]


def _of(g: dict, ctype: str) -> list[str]:
    return [k for k, v in g.items() if v["class_type"] == ctype]


def _src(g: dict, nid: str, name: str):
    v = g[nid]["inputs"].get(name)
    return v[0] if isinstance(v, list) and len(v) == 2 else None


def _new_id(g: dict, stem: str) -> str:
    i = 1
    while f"{stem}{i}" in g:
        i += 1
    return f"{stem}{i}"


def _stages(g: dict) -> dict:
    """The two sampling stages of the LTX-2.5 graph, found by structure:
    stage 1 feeds the latent upsampler, stage 2 samples what comes out of it.
    {1: {sampler, guider, concat, sep}, 2: {...}, "upsampler": id, "decode": id}."""
    ups = _of(g, "LTXVLatentUpsampler")
    if len(ups) != 1:
        raise ValueError(f"the ltx2 workflow must have one LTXVLatentUpsampler (found {len(ups)})")
    up = ups[0]
    out = {"upsampler": up}

    def separator(nid):
        """The LTXVSeparateAVLatent behind a stage's output, through an
        LTXVCropGuides this function has already put there (so it can be run
        again on a graph it has patched)."""
        while nid is not None and g[nid]["class_type"] == "LTXVCropGuides":
            nid = _src(g, nid, "latent")
        return nid

    sep1 = separator(_src(g, up, "samples"))
    samplers = _of(g, "SamplerCustomAdvanced")
    s1 = _src(g, sep1, "av_latent")
    s2 = next((s for s in samplers if s != s1), None)
    if s1 not in samplers or s2 is None:
        raise ValueError("the ltx2 workflow must have a base and a refine SamplerCustomAdvanced")
    sep2 = next(k for k, _ in _consumers(g, s2) if g[k]["class_type"] == "LTXVSeparateAVLatent")
    for n, (s, sep) in {1: (s1, sep1), 2: (s2, sep2)}.items():
        concat = _src(g, s, "latent_image")
        out[n] = {"sampler": s, "guider": _src(g, s, "guider"),
                  "concat": concat, "sep": sep,
                  # the stage's own video latent, before any guide widens it:
                  # what the shot's size and length actually are
                  "latent": list(g[concat]["inputs"]["video_latent"])}
    dec = [k for k, name in _consumers(g, sep2) if name == "samples"]
    while dec and g[dec[0]]["class_type"] == "LTXVCropGuides":
        dec = [k for k, name in _consumers(g, dec[0], 2) if name == "samples"]
    out["decode"] = next(k for k in dec if g[k]["class_type"].startswith("VAEDecode"))
    return out


def _crop_stage(g: dict, st: dict, n: int, guide: str) -> str:
    """The LTXVCropGuides that takes stage `n`'s guide frames back off its
    output latent, made on first use and then reused: several patches (a last
    keyframe and the reference sheet) can guide the same stage, and only the
    last guide's conditioning tells the crop how many frames to take off."""
    for k in _of(g, "LTXVCropGuides"):
        if g[k]["inputs"].get("latent") == [st[n]["sep"], 0]:
            g[k]["inputs"]["positive"], g[k]["inputs"]["negative"] = [guide, 0], [guide, 1]
            return k
    crop = _new_id(g, f"h3_crop{n}_")
    g[crop] = {"class_type": "LTXVCropGuides",
               "inputs": {"positive": [guide, 0], "negative": [guide, 1],
                          "latent": [st[n]["sep"], 0]},
               "_meta": {"title": f"crop guides, stage {n} (h3pipe)"}}
    # the video latent after this stage, without the guide frames
    g[st["upsampler"] if n == 1 else st["decode"]]["inputs"]["samples"] = [crop, 2]
    return crop


def _first_frame(g: dict, inputs: dict) -> None:
    """`inputs["first"]` (a name LoadImage reads) conditions frame 0 through
    the workflow's two LTXVImgToVideoInplace nodes; without it they are
    spliced out, so the graph is plain text-to-video and needs no image."""
    i2v = _of(g, "LTXVImgToVideoInplace")
    strength = RECIPE.get("keyframe_strength") or {}
    if inputs.get("first"):
        loads = [k for k in _of(g, "LoadImage")]
        if len(loads) != 1:
            raise ValueError(f"the ltx2 workflow must have one LoadImage for the first "
                             f"frame (found {len(loads)})")
        g[loads[0]]["inputs"]["image"] = inputs["first"]
        for k in i2v:
            g[k]["inputs"]["bypass"] = False
            base = g[_src(g, k, "latent")]["class_type"] == "EmptyLTXVLatentVideo"
            g[k]["inputs"]["strength"] = float(strength.get("first" if base else "first_refine",
                                                            g[k]["inputs"].get("strength", 1.0)))
    else:
        for k in i2v:
            latent = g[k]["inputs"]["latent"]
            for c, name in _consumers(g, k):
                g[c]["inputs"][name] = list(latent)
            del g[k]


def _last_frame(g: dict, st: dict, name: str) -> None:
    """`inputs["last"]`: an LTXVAddGuide at the last frame of each stage, and
    an LTXVCropGuides after each sampler, as LTX's keyframe workflows do."""
    strength = RECIPE.get("keyframe_strength") or {}
    vae = g[st["upsampler"]]["inputs"]["vae"]
    load = _new_id(g, "h3_last_load")
    g[load] = {"class_type": "LoadImage", "inputs": {"image": name},
               "_meta": {"title": "last frame (h3pipe)"}}
    prep = _new_id(g, "h3_last_prep")
    g[prep] = {"class_type": "LTXVPreprocess",
               "inputs": {"image": [load, 0], "img_compression": 18},
               "_meta": {"title": "last frame preprocess (h3pipe)"}}
    for n in (1, 2):
        s = st[n]
        gi, ci = g[s["guider"]]["inputs"], g[s["concat"]]["inputs"]
        ag = _new_id(g, f"h3_last_guide{n}_")
        g[ag] = {"class_type": "LTXVAddGuide",
                 "inputs": {"positive": gi["positive"], "negative": gi["negative"],
                            "vae": list(vae), "latent": ci["video_latent"],
                            "image": [prep, 0], "frame_idx": -1,
                            "strength": float(strength.get("last", 1.0))},
                 "_meta": {"title": f"last frame guide, stage {n} (h3pipe)"}}
        gi["positive"], gi["negative"] = [ag, 0], [ag, 1]
        ci["video_latent"] = [ag, 2]
        _crop_stage(g, st, n, ag)


def _stage_size(g: dict, st: dict, n: int, job) -> tuple[int, int]:
    """The pixel size stage `n` samples at: the base stage renders at half the
    output (the workflow's `scale: 0.5` on EmptyLTXVLatentVideo, then the x2
    latent upsampler), so a guide encoded at the output size wouldn't line up
    with its latent."""
    if n == 1:
        empties = _of(g, "EmptyLTXVLatentVideo")
        if len(empties) == 1:
            i = g[empties[0]]["inputs"]
            return int(i["width"]), int(i["height"])
    return int(job.width), int(job.height)


def _reference_sheet(g: dict, st: dict, job, name: str) -> None:
    """The reference sheet into the graph, wired as ComfyUI's LTX-2.5
    ingredients template wires it: LoadImage -> ResizeAndPadImage (the
    stage's own size, black) -> RepeatImageBatch (the clip's frame count) is
    the static reference video, and LTXAddVideoICLoRAGuide holds it in
    context for each stage in `reference_sheet.stages` (its guide frames come
    off again through LTXVCropGuides). The IC-LoRA itself is loaded by
    LTXICLoRALoaderModelOnly, whose second output is the reference downscale
    factor the guide wants, and only the guided stages' guiders read the
    patched model.

    The guide is exactly as long as the clip: LTXAddVideoICLoRAGuide refuses
    a guide longer than the latent ("Conditioning frames exceed the length of
    the latent sequence"), so the IC-LoRA's own floor of `min_frames` can
    only be met by a longer shot — a shorter one renders with a note that
    identity may weaken."""
    lora = reference_lora(job)
    if not lora:
        raise ValueError(f"{job.id}: a reference sheet was staged but the ingredients "
                         f"IC-LoRA isn't installed")
    vae = g[st["upsampler"]]["inputs"]["vae"]
    frames = max(int(job.frames or 1), 1)
    load = _new_id(g, "h3_sheet_load")
    g[load] = {"class_type": "LoadImage", "inputs": {"image": name},
               "_meta": {"title": "reference sheet (h3pipe)"}}
    stages = [int(n) for n in (SHEET.get("stages") or [1]) if int(n) in (1, 2)]
    ic = _new_id(g, "h3_sheet_lora")
    g[ic] = {"class_type": "LTXICLoRALoaderModelOnly",
             "inputs": {"model": list(g[st[stages[0]]["guider"]]["inputs"]["model"]),
                        "lora_name": lora,
                        "strength_model": float(SHEET.get("strength", 1.0))},
             "_meta": {"title": "ingredients IC-LoRA (h3pipe)"}}
    for n in stages:
        s = st[n]
        gi, ci = g[s["guider"]]["inputs"], g[s["concat"]]["inputs"]
        w, h = _stage_size(g, st, n, job)
        fit = _new_id(g, f"h3_sheet_fit{n}_")
        g[fit] = {"class_type": "ResizeAndPadImage",
                  "inputs": {"image": [load, 0], "target_width": w, "target_height": h,
                             "padding_color": "black", "interpolation": "lanczos"},
                  "_meta": {"title": f"reference sheet at {w}x{h}, stage {n} (h3pipe)"}}
        loop = _new_id(g, f"h3_sheet_loop{n}_")
        g[loop] = {"class_type": "RepeatImageBatch",
                   "inputs": {"image": [fit, 0], "amount": frames},
                   "_meta": {"title": f"reference sheet as a static video, stage {n} (h3pipe)"}}
        ag = _new_id(g, f"h3_sheet_guide{n}_")
        g[ag] = {"class_type": "LTXAddVideoICLoRAGuide",
                 "inputs": {"positive": gi["positive"], "negative": gi["negative"],
                            "vae": list(vae), "latent": ci["video_latent"],
                            "image": [loop, 0], "frame_idx": 0,
                            "strength": float(SHEET.get("guide_strength", 1.0)),
                            "latent_downscale_factor": [ic, 1], "crop": "disabled",
                            "use_tiled_encode": False, "tile_size": 256, "tile_overlap": 64},
                 "_meta": {"title": f"reference sheet guide, stage {n} (h3pipe)"}}
        gi["positive"], gi["negative"] = [ag, 0], [ag, 1]
        gi["model"] = [ic, 0]
        ci["video_latent"] = [ag, 2]
        _crop_stage(g, st, n, ag)


def _sampling(g: dict, st: dict, job, sheet: bool = False) -> None:
    """The sampling schedule the job's transformer wants, into the graph
    (sampling_plan decided it, and already put the step count on the job).
    The distilled file patches nothing: the workflow's fixed 8 + 3 step
    ManualSigmas at cfg 1 is the schedule it was distilled for. The dev
    transformer is the quality profile: the base stage's sigmas become an
    LTXVScheduler at the job's `steps`, fed the base stage's own video latent
    (`scheduler.latent`) so its timestep shift follows the shot's token count
    — without it the node assumes 4096 tokens and shifts as if the latent
    were far bigger than it is, which spends the schedule at high noise and
    comes out soft. That is the latent BEFORE any guide widens it (a
    reference sheet doubles the sequence), which is the size and length the
    shot actually delivers. Both guiders then take real guidance and both
    samplers the mode's sampler."""
    mode, spec = sampling_mode(job.model or "", sheet)
    if not _patches(spec):
        return
    sch = dict(spec.get("scheduler") or {})
    latent = list(st[1]["latent"]) if sch.pop("latent", False) else None
    g[_src(g, st[1]["sampler"], "sigmas")] = {
        "class_type": sch.pop("class_type", "LTXVScheduler"),
        "inputs": {"steps": int(job.steps), **sch,
                   **({"latent": latent} if latent else {})},
        "_meta": {"title": f"{mode} schedule, {job.steps} steps (h3pipe)"}}
    refine = dict(spec.get("refine") or {})
    if refine.get("sigmas"):
        g[_src(g, st[2]["sampler"], "sigmas")] = {
            "class_type": "ManualSigmas", "inputs": {"sigmas": refine["sigmas"]},
            "_meta": {"title": f"{mode} refine schedule (h3pipe)"}}
    for n, layer in ((1, spec), (2, refine or spec)):
        gi = g[st[n]["guider"]]["inputs"]
        for field in ("video_cfg", "audio_cfg"):
            if layer.get(field) is not None:
                gi[field] = float(layer[field])
        if layer.get("sampler"):
            g[_src(g, st[n]["sampler"], "sampler")]["inputs"]["sampler_name"] = layer["sampler"]


def patch_graph(target, g: dict, job, inputs: dict) -> None:
    """The first frame (or its nodes spliced out), then the last frame, the
    reference sheet (`inputs["sheet"]`, staged by stage_inputs) and the
    sampling schedule the job's transformer wants. The two sampling stages
    are found once, before any of them rewires the graph."""
    _first_frame(g, inputs)
    sheet = bool(inputs.get("sheet"))
    if not (inputs.get("last") or sheet or _patches(sampling_mode(job.model or "", sheet)[1])):
        return
    st = _stages(g)
    if inputs.get("last"):
        _last_frame(g, st, inputs["last"])
    if sheet:
        _reference_sheet(g, st, job, inputs["sheet"])
    _sampling(g, st, job, sheet)
