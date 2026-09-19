"""
What the three Wan 2.2 targets share: the compile from the story IR to
shotlist entries, the ref slots, and the graph helpers. Each target's own
compile.py (wan22_i2v, wan22_ti2v, wan22_vace) binds these to its target.json
and adds its graph surgery (patch_graph) and, for VACE, the reference image.

An entry carries the neutral keys every target's entries do (id, sequence,
subjects, background, size, length, seed, steps, audio_policy, prompt, plus
duration / audio_in-audio_out), `negative` (the preset's), `keyframes`
{first[, last]} (the conventional paths, as ltx2's), and for a target whose
recipe has a `reference_image` (VACE) `panels`: the shot's subjects, in order,
each {"subject", "kind", "path", "view"?} (the plate only when the recipe
asks for it). Paths are the series config's (the picked, live files).

Audio: Wan makes none (target.json `capabilities.audio: "none"`). Every
policy renders `silent` (recipe.policies ["silent"], the fallback from any
other), so each entry has audio_intent / audio_note and each take says so;
dialogue shots get one `--check` warning (no audio or lip-sync on Wan: the
lines are acted silently, prompt.acting).

Sizes: a Wan target renders its own presets' sizes unless it is the series
target: another model's pass block (H3's 448x256 proxy) is far below the
480p/720p Wan was trained at, so it lends no size.

Stdlib only.
"""
from __future__ import annotations

import os
import re

import targets as TG
from h3core import ir
from h3core.ir import stable_seed
from h3core.speech import RATE_CEILING, SPEECH_RATE, forced_rate, pacing, speech_seconds
from targets.video.ltx2.compile import _duration, _lines, put_model_duration, shotlist_extra

from .prompt import build_prompt

SILENT = "silent"
PLATE = "plate"                      # the absent-set name of the plate


def keyframe_path(recipe: dict, shot_id: str, end: str) -> str:
    """Where a shot's keyframe lives (h3refs' `shot:<id>:<end>` ref)."""
    return recipe["keyframe_path"].format(shot=re.sub(r"[^A-Za-z0-9_.-]", "_", shot_id),
                                          end=end)


def is_series_target(target, series_cfg: dict) -> bool:
    return ((series_cfg.get("series") or {}).get("target")
            or TG.DEFAULT_VIDEO_TARGET) == target.id


class Ctx:
    """One pass of one episode: the preset, the size, the report, and (VACE)
    the refs the shots need. `absent` names refs to compile WITHOUT (render
    anyway): subject ids and/or "plate"."""

    def __init__(self, target, series_cfg: dict, pass_: str, absent=None):
        self.target, self.series_cfg, self.pass_ = target, series_cfg, pass_
        self.recipe = target.recipe
        self.preset = target.preset(pass_, series_cfg)
        self.fps = target.template.fps_for(series_cfg)
        self.warnings: list[str] = []
        base = self.preset if is_series_target(target, series_cfg) else target.presets[pass_]
        w, h = int(base.width), int(base.height)
        self.width, self.height = target.template.fit_size(w, h)
        if (self.width, self.height) != (w, h):
            self.warnings.append(f"{pass_} size {w}x{h} is not legal on {target.short}; "
                                 f"rendering {self.width}x{self.height}")
        self.total_req = self.total_raw = 0
        self.long: list[str] = []                 # "<shot> <frames>" past the trained length
        self.talking: list[str] = []              # shots with dialogue
        self.intents: dict[str, int] = {}         # audio intent -> shots (all render silent)
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


# ---------------------------------------------------------------------------
# the reference image's panels (a recipe with `reference_image`: VACE)
# ---------------------------------------------------------------------------

def _hint(recipe: dict, kind: str) -> str:
    return (recipe.get("size_hints") or {}).get(kind, "")


def subject_request(recipe: dict, sid: str, entry: dict, slot: str | None = None) -> TG.RefRequest:
    if entry.get("kind", "character") == "character":
        r = recipe["refs"]["character"]
        label, views = r["kind"], int(r.get("views", 1))
    else:
        r = recipe["refs"]["object"]
        label, views = r["kind"].format(kind=entry.get("kind", "prop")), 1
    return TG.RefRequest(entry.get("sheet") or "", label, r["shape"], subject=sid,
                         views=views, slot=slot, entry=entry, size_hint=_hint(recipe, label))


def plate_request(recipe: dict, key: str, entry: dict, slot: str | None = None) -> TG.RefRequest:
    r = recipe["refs"]["location"]
    return TG.RefRequest(entry.get("plate") or "", r["kind"], r["shape"], location=key,
                         slot=slot, entry=entry, size_hint=_hint(recipe, r["kind"]))


def ordered(shot: ir.Shot, book: dict) -> list[str]:
    """The shot's subjects in panel order: characters, then props and
    vehicles, each in script order."""
    subjects = list(shot.cast) + [p for p in shot.props if p not in shot.cast]
    chars = [s for s in subjects if book[s].get("kind", "character") == "character"]
    return chars + [s for s in subjects if s not in chars]


def panel_view(ref: dict, subjects: list[str], book: dict, size: str) -> str:
    """Which panel of a character's 4-panel sheet goes on the reference: the
    face on a single-character close-up, else the three-quarter body (the H3
    loader's choice, as ltx2_ingredients)."""
    return ("face" if len(subjects) == 1
            and book[subjects[0]].get("kind", "character") == "character"
            and size in ref.get("face_sizes", ()) else "body")


def panels(ctx: Ctx, shot: ir.Shot, loc_key: str) -> list[dict]:
    ref = ctx.recipe.get("reference_image")
    if not ref:
        return []
    book, series_cfg = ctx.series_cfg["subjects"], ctx.series_cfg
    subjects = ordered(shot, book)
    view = panel_view(ref, subjects, book, shot.size)
    out = []
    for s in subjects:
        e = book[s]
        ctx.need(subject_request(ctx.recipe, s, e), shot.id)
        if s in ctx.absent:
            continue
        p = {"subject": s, "kind": e.get("kind", "character"), "path": e.get("sheet", "")}
        if p["kind"] == "character":
            p["view"] = view
        out.append(p)
    loc = series_cfg["locations"][loc_key]
    if ref.get("plate") and loc.get("plate"):
        ctx.need(plate_request(ctx.recipe, loc_key, loc), shot.id)
        if PLATE not in ctx.absent:
            out.append({"location": loc_key, "kind": "plate", "path": loc["plate"]})
    return out


# ---------------------------------------------------------------------------
# the compile
# ---------------------------------------------------------------------------

def compile_entry(ctx: Ctx, sq: ir.Sequence, shot: ir.Shot, ep_id: str) -> dict:
    series_cfg, target, template = ctx.series_cfg, ctx.target, ctx.target.template
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
        raise ValueError(f"shot {shot.id}: {dur:.2f}s is longer than {target.short}'s "
                         f"maximum of {template.max} frames ({template.max / fps:.2f}s at "
                         f"{fps:g} fps). Split the shot, or render it on another "
                         f"target.") from None
    if shot.dialogue:
        ctx.talking.append(shot.id)
        held = raw / fps
        rate = forced_rate(_lines(shot), held)
        syl, _ = pacing(_lines(shot))
        if rate > RATE_CEILING:
            fits = template.snap(max(1, round(speech_seconds(_lines(shot), pace) * fps))) / fps
            ctx.warnings.append(f"{shot.id}: CRAMMED — {syl} syllables in {held:.2f}s forces "
                                f"{rate:.1f} syl/s (ceiling {RATE_CEILING}). `dur: {fits:.2f}` "
                                f"gives it room.")
    trained = int(template.spec.get("trained_frames") or 0)
    if trained and raw > trained:
        ctx.long.append(f"{shot.id} {raw}")
    ctx.total_req += req
    ctx.total_raw += raw

    intent = TG.audio_intent(shot, series_cfg, ctx.pass_)
    policy, note = target.audio_policy(intent)
    ctx.intents[intent] = ctx.intents.get(intent, 0) + 1

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
        "keyframes": {end: keyframe_path(ctx.recipe, shot.id, end)
                      for end in ctx.recipe.get("keyframes") or ()},
    }
    if ctx.recipe.get("reference_image"):
        entry["panels"] = panels(ctx, shot, loc_key)
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


def _ids(ids: list[str], n: int = 8) -> str:
    return ", ".join(ids[:n]) + (" …" if len(ids) > n else "")


def compile_episode(target, story: ir.Episode, series_cfg: dict, pass_: str,
                    only: set[str] | None = None, absent=None) -> tuple[dict, dict]:
    ctx = Ctx(target, series_cfg, pass_, absent)
    shots_out, seqs = [], 0
    for sq in story.sequences:
        mine = [s for s in sq.shots if only is None or s.id in only]
        if mine:
            seqs += 1
        for s in mine:
            shots_out.append(compile_entry(ctx, sq, s, story.id))
    p = ctx.preset
    extra = shotlist_extra(p)
    doc = {
        "episode": story.id,
        "title": story.title,
        "target": target.id,
        "defaults": {"width": ctx.width, "height": ctx.height, "fps": ctx.fps,
                     "steps": p.steps, "model": p.model, "lora": p.lora,
                     "audio_policy": SILENT, "master_track": ctx.recording, **extra},
        "subjects": {s: {"kind": e.get("kind", "character"), "sheet": e.get("sheet", ""),
                         "voice_sample": e.get("voice_sample", "")}
                     for s, e in series_cfg["subjects"].items()},
        "shots": shots_out,
    }
    r = target.recipe
    if shots_out:
        asked = ", ".join(f"{k} x{n}" for k, n in sorted(ctx.intents.items()))
        ctx.warnings.append(f"{target.short} makes no sound: {len(shots_out)} shot(s) render "
                            f"silent (asked for {asked}); lay the soundtrack in at the edit")
    if ctx.talking:
        ctx.warnings.append(f"{len(ctx.talking)} dialogue shot(s): no audio or lip-sync on Wan "
                            f"(the lines are acted silently): {_ids(ctx.talking)}")
    for end, why in (r.get("keyframe_required") or {}).items():
        if shots_out:
            ctx.warnings.append(f"{target.short} renders from a first frame: each of these "
                                f"{len(shots_out)} shot(s) is blocked until its "
                                f"{r['keyframe_path'].format(shot='<shot>', end=end)} exists "
                                f"({why.split(': ', 1)[-1]})")
    if ctx.long:
        ctx.warnings.append(f"{len(ctx.long)} shot(s) are longer than {target.short}'s trained "
                            f"{target.template.spec.get('trained_frames')} frames (motion may "
                            f"loop or drift; shot, frames): {_ids(ctx.long, 12)}")
    for sh in shots_out:
        for k in ("model", "lora"):
            if k in sh:
                ctx.warnings.append(f"{sh['id']} overrides {k}: {sh[k]}")
        if len(sh.get("panels") or []) > 4:
            ctx.warnings.append(f"{sh['id']}: {len(sh['panels'])} subjects on one reference "
                                f"image; small panels carry over worse")
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
        "target": target.id, "size_hints": dict(r.get("size_hints") or {}),
    }
    return doc, report


def compile_shot(target, shot_ir: ir.Shot, series_cfg: dict, preset, ctx=None) -> dict:
    """One shot's entry, as compile_episode builds it. `ctx` needs
    {"episode": ir.Episode}, and may give {"absent": [subject ids, "plate"]}."""
    ctx = dict(ctx or {})
    story = ctx.get("episode")
    if story is None:
        raise ValueError("compile_shot needs ctx['episode'], the shot's ir.Episode")
    pass_ = preset if isinstance(preset, str) else preset.name
    doc, _ = compile_episode(target, story, series_cfg, pass_, {shot_ir.id}, ctx.get("absent"))
    if not doc["shots"]:
        raise ValueError(f"{shot_ir.id} is not in episode {story.id}")
    return doc["shots"][0]


def compile_without(target, story: ir.Episode, series_cfg: dict, pass_: str,
                    entry: dict, missing: list[dict]) -> dict:
    """`entry` compiled again as if the `missing` refs (ref_slots dicts)
    didn't exist: they leave the reference image (the prose describes every
    subject in words anyway). ValueError when the build is out of date."""
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
    """The refs a shot's reference image is made of, in panel order (VACE;
    [] for a target without one). Keyframes aren't made by the image target:
    they come from continuity or an import."""
    recipe = target.recipe
    ref = recipe.get("reference_image")
    if not ref:
        return []
    ctx = dict(ctx or {})
    book = series_cfg["subjects"]
    out = [subject_request(recipe, s, book[s], f"reference panel {i}")
           for i, s in enumerate(ordered(shot_ir, book), 1)]
    if ref.get("plate"):
        key = shot_ir.plate or (ctx["sequence"].location if ctx.get("sequence") else "")
        loc = series_cfg["locations"].get(key)
        if loc and loc.get("plate"):
            out.append(plate_request(recipe, key, loc, f"reference panel {len(out) + 1}"))
    return out


def ref_slots(target, doc: dict, shot: dict) -> list[dict]:
    """What a render reads: one slot per reference panel (required: a missing
    file blocks the shot unless rendering anyway), then the keyframes. A
    keyframe is optional unless the recipe's `keyframe_required` names it
    (Wan 14B I2V's first frame): then it is required and can't be rendered
    without (`anyway: false`, `why` says what to do)."""
    out = []
    for i, p in enumerate(shot.get("panels") or [], 1):
        r = {"slot": f"reference panel {i}", "kind": "image", "path": p.get("path", "")}
        if p.get("subject"):
            r["subject"] = p["subject"]
        else:
            r["location"] = p.get("location")
        out.append(r)
    hard = target.recipe.get("keyframe_required") or {}
    for end, p in (shot.get("keyframes") or {}).items():
        r = {"slot": f"{end} frame", "kind": "image", "path": p, "role": end}
        if end in hard:
            r.update(anyway=False, why=hard[end])
        else:
            r["optional"] = True
        out.append(r)
    return out


# ---------------------------------------------------------------------------
# the graph
# ---------------------------------------------------------------------------

def of(g: dict, ctype: str, title: str | None = None) -> list[str]:
    return [k for k, v in g.items() if v["class_type"] == ctype
            and (title is None or (v.get("_meta") or {}).get("title") == title)]


def one(g: dict, ctype: str, title: str | None = None, what: str = "the") -> str:
    ids = of(g, ctype, title)
    if len(ids) != 1:
        raise ValueError(f"{what} workflow must have one {ctype}"
                         + (f" titled {title!r}" if title else "") + f" (found {len(ids)})")
    return ids[0]


def consumers(g: dict, nid: str, slot: int = 0) -> list[tuple[str, str]]:
    return [(k, name) for k, v in g.items() for name, val in v["inputs"].items()
            if val == [nid, slot]]


def new_id(g: dict, stem: str) -> str:
    i = 1
    while f"{stem}{i}" in g:
        i += 1
    return f"{stem}{i}"


def split_stages(target, g: dict, job) -> int:
    """A two-stage graph (the 14B high/low noise models): the high noise
    sampler runs steps [0, split), the low noise one [split, end). `split`
    is the preset's fraction of the job's steps (target.json
    `binding.stages`: the two samplers' titles), at least one step each.
    Returns the split step."""
    st = (target.spec.get("binding") or {}).get("stages") or {}
    frac = float(job.shot.get("split", job.doc.get("defaults", {}).get("split", 0.5)))
    steps = int(job.steps)
    split = max(1, min(steps - 1, int(round(steps * frac)))) if steps > 1 else 1
    hi = one(g, "KSamplerAdvanced", st["high"]["sampler"], target.id)
    lo = one(g, "KSamplerAdvanced", st["low"]["sampler"], target.id)
    g[hi]["inputs"].update(start_at_step=0, end_at_step=split)
    g[lo]["inputs"].update(start_at_step=split, end_at_step=10000)
    return split


def abs_path(root: str, p: str) -> str:
    return p if os.path.isabs(p) else os.path.join(root, p)
