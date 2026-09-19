"""
LTX-2.5 (distilled): the story IR compiled into shotlist entries that h3jobs
patches straight into ComfyUI's LTX-2.5 image-to-video workflow (target.json
beside this file). There is no loader node: every render value is a widget.

    compile_episode(target, story, series_cfg, pass_, only=None) -> (doc, report)
    compile_shot(target, shot_ir, series_cfg, preset, ctx) -> one entry
    required_refs(target, shot_ir, series_cfg, ctx) -> []   (nothing is required)
    ref_slots(target, doc, shot) -> the optional keyframes
    patch_graph(target, graph, job, inputs) -> keyframe conditioning, or text-to-video

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

import re

import targets as TG
from h3core import ir
from h3core.ir import stable_seed
from h3core.speech import RATE_CEILING, SPEECH_RATE, forced_rate, pacing, speech_seconds

from .prompt import build_prompt

ID = "ltx2"
TARGET = TG.load_target(ID, "video")
RECIPE = TARGET.recipe
KEYFRAMES = tuple(RECIPE.get("keyframes") or ())


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


def ref_slots(target, doc: dict, shot: dict) -> list[dict]:
    """The keyframes a render reads when they exist. They are `optional`:
    missing ones never block a render (h3jobs.missing_refs skips them)."""
    return [{"slot": f"{end} frame", "kind": "image", "path": p, "role": end,
             "optional": True}
            for end, p in (shot.get("keyframes") or {}).items()]


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
    sep1 = _src(g, up, "samples")
    samplers = _of(g, "SamplerCustomAdvanced")
    s1 = _src(g, sep1, "av_latent")
    s2 = next((s for s in samplers if s != s1), None)
    if s1 not in samplers or s2 is None:
        raise ValueError("the ltx2 workflow must have a base and a refine SamplerCustomAdvanced")
    sep2 = next(k for k, _ in _consumers(g, s2) if g[k]["class_type"] == "LTXVSeparateAVLatent")
    for n, (s, sep) in {1: (s1, sep1), 2: (s2, sep2)}.items():
        out[n] = {"sampler": s, "guider": _src(g, s, "guider"),
                  "concat": _src(g, s, "latent_image"), "sep": sep}
    out["decode"] = next(k for k, name in _consumers(g, sep2) if name == "samples"
                         and g[k]["class_type"].startswith("VAEDecode"))
    return out


def patch_graph(target, g: dict, job, inputs: dict) -> None:
    """Keyframes: `inputs["first"]` (a name LoadImage reads) conditions frame 0
    through the workflow's two LTXVImgToVideoInplace nodes; without it they are
    spliced out, so the graph is plain text-to-video and needs no image.
    `inputs["last"]` adds an LTXVAddGuide at the last frame of each stage, and
    an LTXVCropGuides after each sampler, as LTX's keyframe workflows do."""
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
    if inputs.get("last"):
        st = _stages(g)
        vae = g[st["upsampler"]]["inputs"]["vae"]
        load = _new_id(g, "h3_last_load")
        g[load] = {"class_type": "LoadImage", "inputs": {"image": inputs["last"]},
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
            crop = _new_id(g, f"h3_last_crop{n}_")
            g[crop] = {"class_type": "LTXVCropGuides",
                       "inputs": {"positive": [ag, 0], "negative": [ag, 1],
                                  "latent": [s["sep"], 0]},
                       "_meta": {"title": f"crop guides, stage {n} (h3pipe)"}}
            # the video latent after this stage, without the guide frames
            if n == 1:
                g[st["upsampler"]]["inputs"]["samples"] = [crop, 2]
            else:
                g[st["decode"]]["inputs"]["samples"] = [crop, 2]
