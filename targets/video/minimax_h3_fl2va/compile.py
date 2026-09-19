"""
MiniMax H3 FL2VA (first/last frame to video + audio): the story IR compiled
into shotlist entries that h3jobs patches straight into ComfyUI's MiniMax H3
image-to-video workflow (target.json beside this file). There is no loader
node: every render value is a widget, as with ltx2.

    compile_episode(target, story, series_cfg, pass_, only=None) -> (doc, report)
    compile_shot(target, shot_ir, series_cfg, preset, ctx) -> one entry
    compile_without(target, story, series_cfg, pass_, entry, missing) -> entry
    required_refs(target, shot_ir, series_cfg, ctx) -> []   (nothing to make)
    ref_slots(target, doc, shot) -> the optional keyframes, and the recording
                                    a dub shot anchors
    stage_inputs(target, job, comfy) -> the keyframe alignment in the prompt,
                                    and the dub slice uploaded
    patch_graph(target, graph, job, inputs) -> keyframes, the audio anchor,
                                    or text-to-video

A shotlist entry carries the neutral keys every target's entries have (id,
sequence, subjects, background, size, length, seed, steps, audio_policy,
prompt, duration or audio_in / audio_out) plus `keyframes` {first, last} (the
conventional paths; optional) and audio_intent / audio_note when the script
asked for clone, which renders with generate here. `background` is the plate
the script names; FL2VA never reads it (the location is described in words).

Audio: generate is H3's own soundtrack. dub / dub_keep_foley anchor the
shot's slice of the recording at frame 0 with MiniMaxH3AddGuide (the model
packs it as conditioning audio on the target's time axis, as it does a
keyframe's latent); the prompt says the dialogue is the recording's, and dub
adds nothing else while dub_keep_foley keeps the soundscape. H3 FL2VA has no
voice-reference slot, so clone renders with generate and says so.

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

from .prompt import build_prompt, with_keyframes

ID = "minimax_h3_fl2va"
TARGET = TG.load_target(ID, "video")
RECIPE = TARGET.recipe
KEYFRAMES = tuple(RECIPE.get("keyframes") or ())
ANCHOR = dict(RECIPE.get("audio_anchor") or {})
DUB = ("dub", "dub_keep_foley")


def keyframe_path(shot_id: str, end: str) -> str:
    """Where a shot's keyframe lives (h3refs' `shot:<id>:<end>` ref)."""
    return RECIPE["keyframe_path"].format(shot=re.sub(r"[^A-Za-z0-9_.-]", "_", shot_id),
                                          end=end)


def _lines(shot: ir.Shot) -> list[dict]:
    return [{"who": d.speaker, "line": d.line} for d in shot.dialogue]


class Ctx:
    """One pass of one episode: the preset, the legal size, and the report.
    `absent` is what a render-anyway recompile leaves out ("audio")."""

    def __init__(self, target, series_cfg: dict, pass_: str, absent=()):
        self.target, self.series_cfg, self.pass_ = target, series_cfg, pass_
        self.preset = target.preset(pass_, series_cfg)
        self.fps = target.template.fps_for(series_cfg)
        self.warnings: list[str] = []
        self.width, self.height = int(self.preset.width), int(self.preset.height)
        target.template.validate_size(self.width, self.height)
        self.total_req = self.total_raw = 0
        self.fallbacks: dict[str, list[str]] = {}
        self.long: list[str] = []
        self.profiles = TG.series_profiles(series_cfg)
        self.recording = series_cfg.get("audio", {}).get("track", "")
        self.absent = set(absent or ())


def _duration(shot: ir.Shot, pace: str) -> tuple[float, bool]:
    """(seconds, from a recording window) as the script asks."""
    t = shot.timing or {}
    if "audio_in" in t:
        return t["audio_out"] - t["audio_in"], True
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
    dur, windowed = _duration(shot, pace)
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
    trained = int(template.spec.get("trained_frames") or 0)
    if trained and raw > trained:
        ctx.long.append(shot.id)
    ctx.total_req += req
    ctx.total_raw += raw

    intent = TG.audio_intent(shot, series_cfg, ctx.pass_)
    policy, note = ctx.target.audio_policy(intent)
    if note:
        ctx.fallbacks.setdefault(intent, []).append(shot.id)
    if policy in DUB:
        if "audio" in ctx.absent:
            # render anyway without the recording: H3 voices the lines itself
            policy, note = "generate", (f"audio {intent} renders as generate: the "
                                        f"recording is missing")
        elif not windowed:
            raise ValueError(f"shot {shot.id}: policy {policy} needs an `audio: in-out` "
                             f"window on the recorded track (h3align.py writes these).")
        elif not ctx.recording:
            raise ValueError(f"shot {shot.id}: uses an `audio:` window but series.json has "
                             f"no audio.track set.")

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
        "prompt": build_prompt(shot, sq, series_cfg, policy),
        "keyframes": {end: keyframe_path(shot.id, end) for end in KEYFRAMES},
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
    return entry


def compile_episode(target, story: ir.Episode, series_cfg: dict, pass_: str,
                    only: set[str] | None = None, absent=()) -> tuple[dict, dict]:
    ctx = Ctx(target, series_cfg, pass_, absent)
    shots_out, seqs = [], 0
    for sq in story.sequences:
        mine = [s for s in sq.shots if only is None or s.id in only]
        if mine:
            seqs += 1
        for s in mine:
            shots_out.append(_compile(ctx, sq, s, story.id))
    p = ctx.preset
    extra = {k: v for k, v in p.extra.items() if not k.startswith("_")}
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
    if ctx.long:
        ctx.warnings.append(f"{len(ctx.long)} shot(s) are longer than {target.short}'s trained "
                            f"range ({target.template.spec.get('trained_frames')} frames): "
                            f"{', '.join(ctx.long[:8])}" + (" …" if len(ctx.long) > 8 else ""))
    for sh in shots_out:
        for k in ("model", "lora"):
            if k in sh:
                ctx.warnings.append(f"{sh['id']} overrides {k}: {sh[k]}")
    m = re.search(r"(\d+)step", p.lora or "")
    if m and int(m.group(1)) != int(p.steps):
        ctx.warnings.append(f"{pass_} steps is {p.steps} but the LoRA {p.lora} is distilled "
                            f"for {m.group(1)} steps")
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
    {"episode": ir.Episode}; {"absent": ["audio"]} compiles it without the
    recording."""
    ctx = dict(ctx or {})
    story = ctx.get("episode")
    if story is None:
        raise ValueError("compile_shot needs ctx['episode'], the shot's ir.Episode")
    pass_ = preset if isinstance(preset, str) else preset.name
    doc, _ = compile_episode(target, story, series_cfg, pass_, only={shot_ir.id},
                             absent=ctx.get("absent") or ())
    if not doc["shots"]:
        raise ValueError(f"{shot_ir.id} is not in episode {story.id}")
    return doc["shots"][0]


def compile_without(target, story: ir.Episode, series_cfg: dict, pass_: str,
                    entry: dict, missing: list[dict]) -> dict:
    """Render anyway: the only ref a shot needs is a dub shot's recording;
    without it the shot is compiled as generate (H3 voices the lines from
    the prompt). ValueError when the build is out of date."""
    shot_ir = next((s for s in story.shots() if s.id == entry["id"]), None)
    if shot_ir is None:
        raise ValueError(f"{entry['id']} is not in shots.json")
    if compile_shot(target, shot_ir, series_cfg, pass_, {"episode": story}) != entry:
        raise ValueError(f"{entry['id']}: the build is out of date (rebuild the episode)")
    absent = {"audio"} if any(r.get("kind") == "audio" for r in missing) else set()
    return compile_shot(target, shot_ir, series_cfg, pass_,
                        {"episode": story, "absent": absent})


def required_refs(target, shot_ir: ir.Shot, series_cfg: dict, ctx=None) -> list:
    """Nothing to make: subjects and the location are written in words, and
    keyframes are optional (a shot without them is text-to-video). A dub
    shot's recording is the series config's track, not a ref request."""
    return []


def _recording(doc: dict, shot: dict) -> str:
    return shot.get("audio_file") or doc.get("defaults", {}).get("master_track", "")


def ref_slots(target, doc: dict, shot: dict) -> list[dict]:
    """The keyframes a render reads when they exist (`optional`: missing ones
    never block a render), and for a dub shot the recording it anchors. The
    recording has no `role`: h3jobs.stage_inputs doesn't upload the whole
    file; this target's stage_inputs cuts and uploads the shot's slice."""
    out = [{"slot": f"{end} frame", "kind": "image", "path": p, "role": end,
            "optional": True}
           for end, p in (shot.get("keyframes") or {}).items()]
    if shot.get("audio_policy") in DUB:
        out.append({"slot": ANCHOR.get("slot", "dialogue recording"), "kind": "audio",
                    "path": _recording(doc, shot)})
    return out


# ---------------------------------------------------------------------------
# queue time: the keyframes in the prompt, the dub slice
# ---------------------------------------------------------------------------

def _abs(root: str, p: str) -> str:
    return p if os.path.isabs(p) else os.path.join(root, p)


def present_keyframes(job) -> tuple[str, ...]:
    """The keyframe roles on disk for this job, in first/last order."""
    have = set()
    for r in ref_slots(None, job.doc, job.shot):
        if r.get("role") in KEYFRAMES and r.get("path") \
                and os.path.isfile(_abs(job.root, r["path"])):
            have.add(r["role"])
    return tuple(e for e in KEYFRAMES if e in have)


def stage_inputs(target, job, comfy=None) -> dict:
    """Before the take is reserved: the prompt gets the H3 alignment line for
    the keyframes this render has (prompt.with_keyframes; none: the text-to-
    video prompt), and a dub shot's slice of the recording is cut, uploaded
    to ComfyUI's input folder and kept in the take ({"audio": its name}).
    A dry run (`comfy` None) cuts nothing: the name is a stand-in."""
    import h3jobs as J
    import h3takes as T
    from . import audio as A
    roles = present_keyframes(job)
    job.prompt = with_keyframes(J._prompt_text(job.prompt), roles, job.frames, job.fps)
    out = {}
    shot = job.recompiled or job.shot
    if shot.get("audio_policy") not in DUB:
        return out
    rec = _recording(job.doc, shot)
    src = _abs(job.root, rec) if rec else ""
    if not src or not os.path.isfile(src):
        # only reached rendering anyway with the build's own prompt (the
        # "blank" fallback): no anchor, H3 voices the lines itself
        job.notes.append(f"no dialogue anchor: the recording {rec or '(none named)'} "
                         f"is missing")
        return out
    start = end = None
    if not shot.get("audio_file") and "audio_in" in shot:
        start, end = float(shot["audio_in"]), float(shot["audio_out"])
    if comfy is None:
        return {"audio": f"{J.INPUT_SUBFOLDER}/dry_run_{T.safe_id(job.id)}_dub.wav"}
    d = T.shot_dir(job.root, job.pass_, job.id, job.folder)
    os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, f".tmp_dub_{uuid.uuid4().hex}.wav")
    try:
        A.cut(src, tmp, start, end)
        name = J.input_name(tmp)
        comfy.upload_input(tmp, name)
    except BaseException:
        if os.path.isfile(tmp):
            os.remove(tmp)
        raise
    job.staged.append({"tmp": tmp, "suffix": ANCHOR.get("take_suffix", "_dub.wav"),
                       "slot": "dialogue slice", "kind": "audio", "role": "audio"})
    out["audio"] = name
    return out


# ---------------------------------------------------------------------------
# the graph
# ---------------------------------------------------------------------------

def _consumers(g: dict, nid: str, slot: int = 0) -> list[tuple[str, str]]:
    return [(k, name) for k, v in g.items() for name, val in v["inputs"].items()
            if val == [nid, slot]]


def _of(g: dict, ctype: str) -> list[str]:
    return [k for k, v in g.items() if v["class_type"] == ctype]


def _new_id(g: dict, stem: str) -> str:
    i = 1
    while f"{stem}{i}" in g:
        i += 1
    return f"{stem}{i}"


def _one(g: dict, ctype: str) -> str:
    ids = _of(g, ctype)
    if len(ids) != 1:
        raise ValueError(f"the {ID} workflow must have one {ctype} (found {len(ids)})")
    return ids[0]


def _load_image(g: dict, name: str, title: str, reuse: str | None = None) -> str:
    if reuse:
        g[reuse]["inputs"]["image"] = name
        return reuse
    nid = _new_id(g, "h3_keyframe_")
    g[nid] = {"class_type": "LoadImage", "inputs": {"image": name},
              "_meta": {"title": f"{title} (h3pipe)"}}
    return nid


def patch_graph(target, g: dict, job, inputs: dict) -> None:
    """Keyframes: `inputs["first"]` / `inputs["last"]` (names LoadImage reads)
    go straight into MiniMaxH3ImageToVideo's first_frame / last_frame; an end
    without one is disconnected (neither: text-to-video). `inputs["audio"]`
    (a name LoadAudio reads) is anchored at frame 0 by a MiniMaxH3AddGuide
    between the node's conditioning and the guider."""
    i2v = _one(g, "MiniMaxH3ImageToVideo")
    ii = g[i2v]["inputs"]
    # the template's LoadImage (the first frame), reused for it
    loads = [k for k in _of(g, "LoadImage")]
    for end in ("first", "last"):
        ii.pop(f"{end}_frame", None)
    if inputs.get("first"):
        nid = _load_image(g, inputs["first"], "first frame", loads[0] if len(loads) == 1 else None)
        ii["first_frame"] = [nid, 0]
    if inputs.get("last"):
        nid = _load_image(g, inputs["last"], "last frame")
        ii["last_frame"] = [nid, 0]
    if inputs.get("audio"):
        dec = _one(g, "VAEDecodeAudio")
        audio_vae = g[dec]["inputs"]["vae"]
        load = _new_id(g, "h3_dub_load_")
        g[load] = {"class_type": "LoadAudio", "inputs": {"audio": inputs["audio"]},
                   "_meta": {"title": "dialogue slice (h3pipe)"}}
        guide = _new_id(g, "h3_dub_guide_")
        consumers = _consumers(g, i2v, 0)
        g[guide] = {"class_type": "MiniMaxH3AddGuide",
                    "inputs": {"positive": [i2v, 0], "latent": [i2v, 1],
                               "audio_vae": list(audio_vae), "audio": [load, 0],
                               "frame_idx": int(ANCHOR.get("frame_idx", 0))},
                    "_meta": {"title": "dialogue anchor at frame 0 (h3pipe)"}}
        for k, name in consumers:
            g[k]["inputs"][name] = [guide, 0]
