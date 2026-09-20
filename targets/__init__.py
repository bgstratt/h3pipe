"""
targets — everything model-specific, one folder per model.

    targets/<kind>/<id>/target.json   data: template, recipe, binding, presets
    targets/<kind>/<id>/*.py          code: the prompt writer and the compile
    targets/<kind>/<id>/workflow.json the ComfyUI graph the binding patches

`kind` is "video" (a shot model: story IR -> shotlist entries), "image" (a
reference model: a ref request -> a picture) or "audio" (a voice model: a
voice ref -> a wav). The core (h3core, h3build,
h3jobs, h3refs, the routes) only talks to a target through this module:

    load_target(id)            -> Target            (cached; TargetError if unknown)
    list_targets(kind=None)    -> [Target]          (video, then image; by id)
    episode_target(story, series_cfg) -> Target     (validates target:/profile:)
    shot_targets(story, series_cfg)   -> {shot id: target id}
    episode_targets(story, series_cfg) -> [(Target, shot ids)]  (how a build splits)

A Target has four parts, as docs/PLAN.md sketches them:

    template   legal lengths and sizes: snap(frames), frames(seconds, fps),
               validate_size(w, h), continuous_warning(shot, frames)
    recipe     how subjects, plates and voices become inputs (target.json
               "recipe"; the packing itself is the target's code)
    binding    the workflow file, the loader/saver node classes, and which
               node widget takes the model, LoRAs, steps and seed
    presets    "final" / "proxy": model, lora, steps, width, height
    models     the model family each model param needs ({"family", "patterns"});
               check_model(target, param, file, ...) checks a file by name, then
               by its safetensors header (targets/modelid.py)

and, for a video target, the code behind it:

    compile_episode(story, series_cfg, pass_, only=None) -> (shotlist doc, report)
    compile(shot_ir, series_cfg, preset, ctx)       -> one shotlist entry
    required_refs(shot_ir, series_cfg)              -> [RefRequest]
    ref_slots(doc, shot)                            -> what the loader reads
    compile_without(story, series_cfg, pass_, entry, missing)   (optional)
                                                    -> the entry without some refs
    patch_graph(graph, job, inputs)                 (optional) graph surgery the
                                                    binding's data can't express
                                                    (LTX: keyframe conditioning)
    stage_inputs(job, comfy)                        (optional) inputs the target
                                                    makes at queue time (the
                                                    ingredients reference sheet)

or, for an image target:

    ref_prompt(request, series_cfg)                 -> the wording of one ref

or, for an audio target:

    voice_prompt(name, voice, design, line, seconds) -> the brief for one voice
    patch_graph(graph, job, inputs)                 (optional) the reference-audio
                                                    chain, when the ref has a
                                                    sample to copy

Render settings (model, LoRAs, steps, target) layer, later wins:

    target preset -> the series config's pass block (`series` / `proxy`)
      -> sequence profile -> sequence lines -> shot profile -> shot lines
      -> overrides.json -> the request (CLI flags / redo dialog)

A profile is a named bundle in the series config's `profiles` block, picked
with `profile:` on a sequence or a shot. It sits just below the explicit lines
at the level that names it, so `profile: dialogue_close` plus `steps: 9` on a
shot renders dialogue_close's model at 9 steps.

Stdlib only.
"""
from __future__ import annotations

import copy
import importlib
import json
import math
import os
import re
from dataclasses import dataclass, field

from . import modelid

HERE =os.path.dirname(os.path.abspath(__file__))
KINDS = ("video", "image", "audio")
TIERS = ("required", "accelerator", "optional")
DEFAULT_VIDEO_TARGET = "minimax_h3_ref2va"
DEFAULT_IMAGE_TARGET = "krea2"
DEFAULT_AUDIO_TARGET = "ltx2_voice"
DEFAULT_TARGETS = {"video": DEFAULT_VIDEO_TARGET, "image": DEFAULT_IMAGE_TARGET,
                   "audio": DEFAULT_AUDIO_TARGET}
PASSES = ("final", "proxy")


class TargetError(ValueError):
    """An unknown target, or one used for something it can't do."""


# ---------------------------------------------------------------------------
# the parts
# ---------------------------------------------------------------------------

class Template:
    """Legal lengths and sizes. `frames` is a grid of `step * k + base`
    frames, capped at `max`; `size_multiple` applies to width and height.

    `fps` is a number, or "series": the target renders at whatever the series
    config's `series.fps` says (fps_for). `max_size` caps the picture:
    {"long_side": n} and/or {"pixels": n}. `size_fit` is how a size that isn't
    legal is treated: "error" (the default; the build names the nearest legal
    sizes) or "snap" (rounded down to the multiple, then shrunk under the
    maximum, keeping the aspect ratio; fit_size)."""

    def __init__(self, spec: dict, short: str):
        fr = spec.get("frames") or {"step": 1, "base": 1, "max": 1 << 30}
        self.step, self.base, self.max = int(fr["step"]), int(fr["base"]), int(fr["max"])
        raw_fps = spec.get("fps", 24)
        self.fps_from_series = raw_fps == "series"
        self.fps = float(spec.get("default_fps", 24) if self.fps_from_series else raw_fps)
        self.size_multiple = int(spec.get("size_multiple", 1))
        self.max_size = dict(spec.get("max_size") or {})
        self.size_fit = spec.get("size_fit", "error")
        self.continuous = dict(spec.get("continuous") or {})
        self.short = short
        self.spec = spec

    def fps_for(self, series_cfg: dict | None) -> float:
        """The frame rate this target renders at for a series."""
        if self.fps_from_series:
            return float(((series_cfg or {}).get("series") or {}).get("fps", self.fps))
        return self.fps

    def fit_size(self, width: int, height: int) -> tuple[int, int]:
        """(width, height) made legal: validate_size's rules, or with
        `size_fit: snap`, snapped down to the multiple and shrunk under the
        maximum size, keeping the aspect ratio as near as the grid allows."""
        if self.size_fit != "snap":
            self.validate_size(width, height)
            return width, height
        m = max(1, self.size_multiple)
        k = 1.0
        long_side = self.max_size.get("long_side")
        if long_side and max(width, height) > long_side:
            k = min(k, long_side / max(width, height))
        pixels = self.max_size.get("pixels")
        if pixels and width * height * k * k > pixels:
            k = min(k, math.sqrt(pixels / (width * height)))
        w, h = max(m, int(width * k) // m * m), max(m, int(height * k) // m * m)
        return w, h

    def validate_size(self, width: int, height: int) -> None:
        m = self.size_multiple
        for nm, v in (("width", width), ("height", height)):
            if m > 1 and v % m:
                raise ValueError(
                    f"{nm}={v} is not a multiple of {m} — {self.short} rejects it. "
                    f"Nearest legal: {v // m * m} or {(v // m + 1) * m}.")
        long_side = self.max_size.get("long_side")
        if long_side and max(width, height) > long_side:
            raise ValueError(f"{width}x{height} is larger than {self.short}'s maximum "
                             f"({long_side} on the long side)")
        pixels = self.max_size.get("pixels")
        if pixels and width * height > pixels:
            raise ValueError(f"{width}x{height} is more than {self.short}'s maximum of "
                             f"{pixels} pixels")

    def snap(self, frames: int) -> int:
        """Round a frame count UP onto the grid (ValueError past the maximum)."""
        if frames <= self.base:
            return self.base
        v = self.step * math.ceil((frames - self.base) / self.step) + self.base
        if v > self.max:
            raise ValueError(
                f"{frames} frames is longer than {self.short}'s maximum of {self.max} "
                f"({self.max / self.fps:.2f}s). Split this shot into two.")
        return v

    def frames(self, seconds: float, fps: float) -> int:
        """The frames a render of `seconds` at `fps` will actually have."""
        return self.snap(max(1, round(seconds * fps)))

    def continuous_warning(self, shot_id: str, frames: int) -> str | None:
        """The warning for a shot after the first in a `continuous: yes`
        sequence, when chaining eats too much of it (None: no warning)."""
        cost = self.continuous.get("chain_frames")
        if not cost:
            return None
        if cost / frames > float(self.continuous.get("warn_above", 0.15)):
            return (f"{shot_id}: continuous chaining costs {cost} of {frames} frames "
                    f"({cost / frames:.0%}). Shots this short are cheaper as separate cuts.")
        return None

    def to_json(self) -> dict:
        d = {"fps": "series" if self.fps_from_series else self.fps,
             "frames": {"step": self.step, "base": self.base, "max": self.max},
             "size_multiple": self.size_multiple}
        # only when set, so an H3 description is what it always was
        if self.max_size:
            d["max_size"] = dict(self.max_size)
        if self.size_fit != "error":
            d["size_fit"] = self.size_fit
        return d


@dataclass
class Binding:
    """Which workflow a target drives and which widgets take what.

    `params` maps a render parameter (model, loras, steps, seed, prompt,
    width, ...) to one widget spec or a list of them (one value patched into
    several widgets, e.g. both stages' seeds). A widget spec is
    {"class_type", "field"} plus optional selectors when the class occurs more
    than once: "title" (the node's title), "feeds": [class, input] (the node
    whose output feeds that input of a node of that class), or "all": true
    (every node of the class); and "scale" (the value is multiplied first:
    a two-stage graph's base latent is half the output size). A LoRA spec is
    {"class_type", "name", "strength", "input", "chain", "insert_after"?}
    (see h3jobs.apply_loras). {"via": "loader"} means the loader node reads it
    from the frozen shotlist, so the graph needs no patch.

    `loader` is optional: a target with none (LTX) gets every value patched
    into widgets. `saver.replace` names a node class the saver takes the place
    of ({"class_type", "inputs": {saver input: that node's input}}), with
    `saver.drop` (classes deleted with it) and `saver.set` (fixed saver
    inputs), so a stock workflow ending in CreateVideo/SaveVideo can feed
    H3SaveShot. `prune` drops every node the saver doesn't depend on."""
    workflow: str                       # the repo copy (absolute path), or ""
    workflow_name: str                  # its name among ComfyUI's saved workflows
    env: str = ""                       # environment variable naming a file to use
    loader: dict = field(default_factory=dict)
    saver: dict = field(default_factory=dict)
    params: dict = field(default_factory=dict)
    review_nodes: list = field(default_factory=list)
    prune: bool = False

    @property
    def loader_class(self) -> str:
        return self.loader.get("class_type", "")

    @property
    def saver_class(self) -> str:
        return self.saver.get("class_type", "")

    def param(self, name: str) -> dict | None:
        return self.params.get(name)

    def widgets(self) -> dict:
        """The render parameters this binding exposes, for pickers: one spec
        each (for a list of specs, the first; the rest take the same value)."""
        return {k: dict(v[0] if isinstance(v, list) else v) for k, v in self.params.items()}

    def specs(self, name: str) -> list[dict]:
        """Every widget spec of a param ([]: not a setting of this binding)."""
        v = self.params.get(name)
        if not v:
            return []
        return [dict(x) for x in (v if isinstance(v, list) else [v])]


@dataclass
class Preset:
    name: str
    model: str | None = None
    lora: str | None = None
    steps: int | None = None
    width: int | None = None
    height: int | None = None
    extra: dict = field(default_factory=dict)
    # target.json `presets.<pass>.base`: what the pass renders with when an
    # `accelerator` model file (a turbo / distilled LoRA or checkpoint) isn't
    # installed: steps, cfg, sampler, `loras` (usually []), a model... in the
    # target's own terms (resolve_models). Kept out of `extra`, so it never
    # reaches a shotlist.
    base: dict | None = None

    def to_json(self) -> dict:
        d = {"model": self.model, "lora": self.lora, "steps": self.steps,
             "width": self.width, "height": self.height}
        d.update(self.extra)
        if self.base is not None:
            d["base"] = copy.deepcopy(self.base)
        return d


@dataclass
class RefRequest:
    """One reference a video target needs, in its shape; the image target that
    makes it supplies the wording (`ref_prompt`)."""
    path: str                           # as the series config names it ("" if none)
    kind: str                           # the label refs_todo prints, e.g. "character sheet"
    shape: str                          # "sheet" | "object" | "plate" | "voice"
    subject: str | None = None          # series config subject id
    location: str | None = None         # series config location id
    views: int = 1                      # a sheet's panel count
    slot: str | None = None             # where the video target puts it, e.g. "Picture 4"
    entry: dict = field(default_factory=dict)
    size_hint: str = ""

    def to_json(self) -> dict:
        return {"path": self.path, "kind": self.kind, "shape": self.shape,
                "subject": self.subject, "location": self.location, "views": self.views,
                "slot": self.slot, "size_hint": self.size_hint}


GENERATE_MODES = ("generate", "generated", "generated_audio")


def audio_intent(shot_ir, series_cfg: dict, pass_: str) -> str:
    """What the script and series config ask of a shot's audio, before any
    target decides how: the shot's own `policy:`, else for a shot with
    dialogue the series config's default (`audio.default_policy`, else from
    `audio.mode`, which the proxy may replace with `proxy.audio_mode`:
    source_track -> dub_keep_foley, generate -> generate, anything else ->
    clone), else generate. A target then renders it (Target.audio_policy)."""
    if shot_ir.audio:
        return shot_ir.audio
    if not shot_ir.dialogue:
        return "generate"
    audio_cfg = series_cfg.get("audio", {})
    mode = audio_cfg.get("mode", "source_track")
    if pass_ == "proxy":
        mode = series_cfg.get("proxy", {}).get("audio_mode") or mode
    if mode == "source_track":
        speaking = "dub_keep_foley"
    elif mode in GENERATE_MODES:
        speaking = "generate"
    else:
        speaking = "clone"
    return audio_cfg.get("default_policy", speaking)


# `dur: model`: preset values a target may give (target.json presets). They are
# read at build and queue time from the target, not written into a shotlist's
# `defaults`, so a shotlist without `dur: model` shots is what it always was.
DURATION_PRESET_KEYS = ("duration_head", "default_seconds", "min_seconds", "max_seconds")
DEFAULT_SHOT_SECONDS = 5.0          # a silent `dur: model` shot's estimate without a preset
PREDICT_RANGE = (1.0, 20.0)         # the predictor's clamp without a script's or a preset's


def duration_estimate(target: "Target", timing: dict, preset: "Preset | None",
                      speech_s: float | None = None) -> tuple[float, dict | None, str]:
    """A `dur: model` shot ({"model": true, "min"?, "max"?}) at build time:
    (the estimate the shotlist writes, the predictor's range {"min_seconds",
    "max_seconds"} or None, a warning or ""). The estimate is `speech_s` (the
    dialogue's `dur: auto` length) for a shot with dialogue, else the preset's
    `default_seconds`; it keeps inside the script's clamp. A target without
    `capabilities.duration: "predict"` renders the estimate, and says so."""
    extra = preset.extra if preset is not None else {}
    lo, hi = timing.get("min"), timing.get("max")
    est = float(speech_s if speech_s is not None
                else extra.get("default_seconds", DEFAULT_SHOT_SECONDS))
    predict = None
    if target.duration == "predict":
        lo = float(lo if lo is not None else extra.get("min_seconds", PREDICT_RANGE[0]))
        hi = float(hi if hi is not None else extra.get("max_seconds", PREDICT_RANGE[1]))
        predict = {"min_seconds": lo, "max_seconds": hi}
    if lo is not None:
        est = min(max(est, float(lo)), float(hi))
    note = "" if predict else (f"`dur: model` renders the estimate, {est:.2f}s: "
                               f"{target.short} can't predict a shot's length")
    return est, predict, note


def keyframe_ends(recipe: dict, shot_ir, seq_ir=None) -> list[str]:
    """The keyframes a shot's entry names: the recipe's (`keyframes`), less
    any the script turns off with `first: none` / `last: none` (the shot's
    line, else its sequence's). A keyframe the target requires
    (`keyframe_required`: Wan 14B I2V's first frame) is always named: `none`
    can't make the target work without it, so the shot stays blocked."""
    hard = recipe.get("keyframe_required") or {}
    return [e for e in recipe.get("keyframes") or ()
            if e in hard or shot_ir.keyframe(e, seq_ir) != "none"]


def voice_prompt(name: str, voice: str) -> str:
    """What a voice sample should be. No audio target exists (nothing generates
    voices); this is the note refs_todo prints for a person recording one."""
    return (f"A 5-15 second clean recording of {name} speaking. "
            f"Voice: {voice}.")


# ---------------------------------------------------------------------------
# the target
# ---------------------------------------------------------------------------

class Target:
    def __init__(self, folder: str, spec: dict):
        self.folder = folder
        self.spec = spec
        self.id: str = spec["id"]
        self.kind: str = spec["kind"]
        self.label: str = spec.get("label", self.id)
        self.short: str = spec.get("short", self.label)
        self.template = Template(spec.get("template") or {}, self.short)
        self.recipe: dict = dict(spec.get("recipe") or {})
        b = dict(spec.get("binding") or {})
        wf = b.get("workflow", "")
        self.binding = Binding(
            workflow=os.path.join(folder, wf) if wf else "",
            workflow_name=b.get("workflow_name") or wf,
            env=b.get("env", ""), loader=dict(b.get("loader") or {}),
            saver=dict(b.get("saver") or {}), params=dict(b.get("params") or {}),
            review_nodes=list(b.get("review_nodes") or []), prune=bool(b.get("prune")))
        self.presets: dict[str, Preset] = {}
        for name, p in (spec.get("presets") or {}).items():
            if name.startswith("_"):                      # a comment
                continue
            p = dict(p)
            base = p.pop("base", None)
            self.presets[name] = Preset(name, p.pop("model", None), p.pop("lora", None),
                                        p.pop("steps", None), p.pop("width", None),
                                        p.pop("height", None), p,
                                        {k: v for k, v in base.items() if not k.startswith("_")}
                                        if isinstance(base, dict) else None)
        self._module = None

    def __repr__(self) -> str:
        return f"<Target {self.kind}/{self.id}>"

    # -- code --------------------------------------------------------------

    @property
    def module(self):
        """The target's code (target.json "code", default compile.py for a
        video target, prompt.py for an image target), imported on first use."""
        if self._module is None:
            code = self.spec.get("code") or ("compile.py" if self.kind == "video"
                                             else "prompt.py")
            self._module = importlib.import_module(
                f"{__name__}.{self.kind}.{self.id}.{os.path.splitext(code)[0]}")
        return self._module

    def _need(self, name: str):
        fn = getattr(self.module, name, None)
        if fn is None:
            raise TargetError(f"{self.kind} target {self.id} has no {name}()")
        return fn

    # video
    def compile_episode(self, story, series_cfg: dict, pass_: str, only=None):
        """(shotlist doc, report) for the episode's shots, or only the shots
        whose ids are in `only` (an episode that mixes targets compiles each
        target's own shots; a retarget compiles one)."""
        fn = self._need("compile_episode")
        if only is None:
            return fn(self, story, series_cfg, pass_)
        return fn(self, story, series_cfg, pass_, only=set(only))

    def compile(self, shot_ir, series_cfg: dict, preset: "Preset | str", ctx=None) -> dict:
        return self._need("compile_shot")(self, shot_ir, series_cfg, preset, ctx)

    def compile_without(self, story, series_cfg: dict, pass_: str, entry: dict,
                        missing: list[dict]) -> dict:
        """A built shot recompiled as if the `missing` refs didn't exist (render
        anyway). Only targets that can write such a prompt have it; check
        supports("compile_without")."""
        return self._need("compile_without")(self, story, series_cfg, pass_, entry, missing)

    def required_refs(self, shot_ir, series_cfg: dict, ctx=None) -> list[RefRequest]:
        return self._need("required_refs")(self, shot_ir, series_cfg, ctx)

    def ref_slots(self, doc: dict, shot: dict) -> list[dict]:
        return self._need("ref_slots")(self, doc, shot)

    def patch_graph(self, graph: dict, job, inputs: dict) -> None:
        """The target's own graph surgery for one job, after the binding's
        widgets are patched (only targets that have it: supports("patch_graph")).
        `inputs` maps an input role ("first", "last") to the name ComfyUI's
        LoadImage reads (see h3jobs.stage_inputs)."""
        self._need("patch_graph")(self, graph, job, inputs)

    def stage_inputs(self, job, comfy=None) -> dict:
        """Inputs the target makes itself for one job, before its take is
        reserved (only targets that have it: supports("stage_inputs")):
        {role: the name LoadImage reads}. ltx2_ingredients composes the shot's
        reference sheet and uploads it (`comfy`; None is a dry run). Called by
        h3jobs.stage_inputs."""
        return self._need("stage_inputs")(self, job, comfy)

    def supports(self, name: str) -> bool:
        return getattr(self.module, name, None) is not None

    # -- capabilities (target.json "recipe") ---------------------------------

    @property
    def policies(self) -> list[str]:
        """The audio policies this target renders (recipe.policies)."""
        return list(self.recipe.get("policies") or ["generate"])

    def audio_policy(self, intent: str) -> tuple[str, str]:
        """(policy it renders with, note) for a shot's audio intent. A policy
        the target doesn't declare falls back to `recipe.policy_fallback`
        ({"to", "why"}) with a note saying so, or is a ValueError when the
        target declares no fallback. This is the check the core relies on:
        no code outside a target asks which model it is."""
        if intent in self.policies:
            return intent, ""
        fb = self.recipe.get("policy_fallback") or {}
        if fb.get("to") in self.policies:
            why = fb.get("why") or f"{self.short} doesn't support it"
            return fb["to"], f"audio {intent} renders as {fb['to']} on {self.short}: {why}"
        raise ValueError(f"{self.short} can't render audio policy {intent!r} "
                         f"(it renders {', '.join(self.policies)})")

    @property
    def duration(self) -> str:
        """How a shot's length is decided: "predict" (target.json
        `capabilities.duration`: the model can predict it at render time, so
        `dur: model` means something) or "script" (the build's length)."""
        return (self.spec.get("capabilities") or {}).get("duration") or "script"

    # -- model families (target.json "models") -------------------------------

    @property
    def models(self) -> dict[str, dict]:
        """The params that take a model file, each with the family it must be
        (target.json `models`): {param: {"family", "patterns", "folder",
        "class_type", "field"}}. `folder` is ComfyUI's models folder the file
        lives in, `class_type`/`field` the loader widget whose choices list
        it: the target.json entry's own, else the binding's widget for the
        param and the folder that loader reads (MODEL_FOLDERS)."""
        out = {}
        for param, m in (self.spec.get("models") or {}).items():
            if param.startswith("_") or not isinstance(m, dict) or not m.get("family"):
                continue
            w = self.binding.specs(param)
            ct = m.get("class_type") or (w[0].get("class_type") if w else None)
            # a LoRA spec names its file widget `name`
            fld = m.get("field") or (w[0].get("field") or w[0].get("name") if w else None)
            tier = m.get("tier") or "required"
            if tier not in TIERS:
                raise TargetError(f"{self.id}: models.{param}.tier must be one of "
                                  f"{', '.join(TIERS)}, not {tier!r}")
            out[param] = {"family": m["family"],
                          "patterns": [str(p) for p in m.get("patterns") or []],
                          "folder": m.get("folder") or MODEL_FOLDERS.get((ct, fld)),
                          "class_type": ct, "field": fld,
                          # requirement tier (resolve_models): required |
                          # accelerator | optional
                          "tier": tier,
                          # what an optional file switches on
                          "feature": m.get("feature") or "",
                          # the file the workflow loads when nothing names one
                          "default": m.get("default") or None,
                          # regexes whose captured word a substitute must share
                          # with the wanted file (a step count, a noise stage,
                          # distilled vs dev), and globs it must not match
                          "keep": [str(k) for k in m.get("keep") or []],
                          "exclude": [str(x) for x in m.get("exclude") or []]}
        return out

    @property
    def downloads(self) -> dict[str, dict]:
        """target.json `downloads`: {file name: {"folder", "url" (None: no
        trustworthy record), "source"}}."""
        return {k: dict(v) for k, v in (self.spec.get("downloads") or {}).items()
                if not k.startswith("_") and isinstance(v, dict)}

    @property
    def nodes(self) -> dict[str, dict]:
        """Node classes the target needs beyond its workflow's own (target.json
        `nodes`): {class: {"tier": "required" | "optional", "feature"}}. A list
        names required ones."""
        raw = self.spec.get("nodes") or {}
        if isinstance(raw, list):
            raw = {n: {} for n in raw}
        return {k: {"tier": (v or {}).get("tier") or "required",
                    "feature": (v or {}).get("feature") or ""}
                for k, v in raw.items() if not k.startswith("_")}

    def named_files(self) -> dict[str, str]:
        """{file name: the param that loads it} for every model file this
        target names: each preset's (model, LoRAs, every model param's value),
        its `base`, and the models' defaults. Each should have a `downloads`
        entry."""
        out: dict[str, str] = {}

        def add(name, param):
            if isinstance(name, str) and name and name.lower() not in ("none", "off"):
                out.setdefault(name, param)

        def add_loras(v):
            for lo in (v or []) if isinstance(v, list) else []:
                add(lo.get("name") if isinstance(lo, dict) else lo, "loras")

        models = self.models
        for p in self.presets.values():
            for layer in (dict(p.extra, model=p.model, lora=p.lora), p.base or {}):
                add(layer.get("model"), "model")
                add(layer.get("lora"), "loras")
                add_loras(layer.get("loras"))
                for param in models:
                    if param not in ("model", "loras"):
                        add(layer.get(param), param)
        for param, m in models.items():
            add(m.get("default"), param)
        add(self.recipe.get("ic_lora"), "loras")
        return out

    def capabilities(self) -> dict:
        """What a picker or the core may need to know without asking which
        model this is (GET /h3pipe/targets). An image target: {"mode": "t2i"
        | "edit", "max_refs", "negative_prompt"}. An audio target:
        {"mode": "t2a" | "voice_clone", "reference_audio", "max_seconds",
        "negative_prompt"}."""
        r = self.recipe
        if self.kind == "image":
            caps = self.spec.get("capabilities") or {}
            return {"mode": caps.get("mode") or "t2i",
                    "max_refs": int(caps.get("max_refs") or 0),
                    "negative_prompt": bool(self.binding.specs("negative"))}
        if self.kind == "audio":
            caps = self.spec.get("capabilities") or {}
            return {"mode": caps.get("mode") or "t2a",
                    "reference_audio": bool(caps.get("reference_audio")),
                    "max_seconds": float(caps.get("max_seconds")
                                         or self.seconds_range()[1]),
                    "negative_prompt": bool(self.binding.specs("negative"))}
        return {"policies": self.policies, "duration": self.duration,
                # the first keyframe is required (Wan 14B I2V: the picture it animates)
                "requires_first": "first" in (r.get("keyframe_required") or {}),
                # "none": the model makes no sound (Wan 2.2): every take is silent
                "audio": (self.spec.get("capabilities") or {}).get("audio") or "generate",
                "policy_fallback": (r.get("policy_fallback") or {}).get("to"),
                "voice_reference": bool(r.get("voice_slots")),
                "subject_refs": bool(r.get("subject_slots") or r.get("reference_sheet")
                                     or r.get("reference_image")),
                "reference_sheet": bool(r.get("reference_sheet")),
                "keyframes": list(r.get("keyframes") or []),
                "prompt": r.get("prompt", "sections" if r.get("subject_slots") else "prose"),
                "negative_prompt": bool(self.binding.specs("negative"))}

    # image
    def ref_prompt(self, req: RefRequest, series_cfg: dict) -> str:
        return self._need("ref_prompt")(self, req, series_cfg)

    # audio
    def voice_prompt(self, name: str, voice: str = "", design: str = "",
                     line: str = "", seconds: float | None = None) -> str:
        """The brief for one generated voice sample (targets/audio/common.py's
        wording unless the target writes its own)."""
        return self._need("voice_prompt")(name, voice, design, line, seconds)

    def voice_line(self, story, subject: str) -> tuple[str, str]:
        """(the line a voice sample should say, "script" | "neutral")."""
        return self._need("voice_line")(story, subject)

    def seconds_range(self) -> tuple[float, float, float]:
        """(default, maximum, minimum) seconds an audio target generates: its
        template's, else its `final` preset's, else 8 / 20 / 2."""
        tpl = self.spec.get("template") or {}
        p = self.presets.get("final")
        extra = p.extra if p is not None else {}

        def val(key, default):
            v = tpl.get(key, extra.get(key, default))
            return float(v if v is not None else default)
        return val("default_seconds", 8.0), val("max_seconds", 20.0), \
            val("min_seconds", 2.0)

    def snap_seconds(self, seconds: float) -> tuple[float, int, float]:
        """(the length actually rendered, its frame count, the frame rate) for
        a request of `seconds` on this audio target: clamped into
        seconds_range, then snapped onto the template's frame grid."""
        default, hi, lo = self.seconds_range()
        s = float(default if seconds is None else seconds)
        s = min(max(s, lo), hi)
        fps = self.template.fps_for(None)
        p = self.presets.get("final")
        if p is not None and p.extra.get("fps"):
            fps = float(p.extra["fps"])
        frames = self.template.snap(max(1, round(s * fps)))
        return frames / fps, frames, fps

    # -- presets -----------------------------------------------------------

    def preset(self, pass_: str, series_cfg: dict | None = None) -> Preset:
        """The pass's preset with the series config's pass block over it:
        `series` for final, `proxy` for proxy. A proxy with no model of its own
        renders the series model, then the target's."""
        if pass_ not in self.presets:
            raise TargetError(f"target {self.id} has no {pass_!r} preset "
                              f"({', '.join(self.presets)})")
        # series_cfg["series"] is required, as it always was (a KeyError names it)
        s = series_cfg["series"] if series_cfg is not None else {}
        base = self.presets[pass_]
        final = self.presets.get("final", base)
        block = s if pass_ == "final" else (series_cfg or {}).get(pass_, {})
        if (s.get("target") or DEFAULT_VIDEO_TARGET) != self.id and self.kind == "video":
            # Not the series target (a shot or profile picked this one): the
            # pass block's model / LoRA / steps were written for another model,
            # so only its picture size carries over (made legal by fit_size).
            s = {k: v for k, v in s.items() if k in ("width", "height")}
            block = {k: v for k, v in block.items() if k in ("width", "height")}
        # evaluated in this order so a bad value is reported as it always was
        width, height = int(block.get("width", base.width)), int(block.get("height", base.height))
        steps = int(block.get("steps", base.steps))
        lora = block.get("lora", base.lora)
        if pass_ == "final":
            model = s.get("model", base.model or final.model)
        else:
            model = block.get("model", s.get("model", base.model or final.model))
        return Preset(pass_, model=model, lora=lora, steps=steps, width=width, height=height,
                      extra=dict(base.extra), base=copy.deepcopy(base.base))

    # -- description (GET /h3pipe/targets) ----------------------------------

    def describe(self) -> dict:
        return {"id": self.id, "kind": self.kind, "label": self.label,
                "default": self.id == DEFAULT_TARGETS.get(self.kind),
                "presets": {k: p.to_json() for k, p in self.presets.items()},
                "widgets": self.binding.widgets(),
                "workflow": self.binding.workflow_name,
                "loader": self.binding.loader_class or None,
                "saver": self.binding.saver_class or None,
                "template": self.template.to_json(),
                "short": self.short,
                "capabilities": self.capabilities(),
                "models": {k: {"family": v["family"], "label": modelid.family_label(v["family"]),
                               "patterns": list(v["patterns"]), "folder": v["folder"],
                               "tier": v["tier"],
                               **({"feature": v["feature"]} if v["feature"] else {})}
                           for k, v in self.models.items()},
                "downloads": self.downloads}


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------

_CACHE: dict[str, Target] = {}


def _folders() -> list[tuple[str, str, str]]:
    """(kind, id, folder) of every target on disk."""
    out = []
    for kind in KINDS:
        d = os.path.join(HERE, kind)
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            if os.path.isfile(os.path.join(d, name, "target.json")):
                out.append((kind, name, os.path.join(d, name)))
    return out


def load_target(target_id: str, kind: str | None = None) -> Target:
    """The target called `target_id` (of `kind`, when given)."""
    key = f"{kind or '*'}:{target_id}"
    if key in _CACHE:
        return _CACHE[key]
    for k, name, folder in _folders():
        if name == target_id and (kind is None or k == kind):
            with open(os.path.join(folder, "target.json"), encoding="utf-8") as fh:
                spec = json.load(fh)
            if spec.get("id") != name or spec.get("kind") != k:
                raise TargetError(f"{folder}/target.json must say id {name!r} and kind {k!r}")
            t = Target(folder, spec)
            _CACHE[key] = t
            return t
    known = ", ".join(t.id for t in list_targets(kind)) or "none"
    raise TargetError(f"no {kind + ' ' if kind else ''}target called {target_id!r} "
                      f"(known: {known})")


def list_targets(kind: str | None = None) -> list[Target]:
    return [load_target(name, k) for k, name, _ in _folders() if kind is None or k == kind]


def video_target(series_cfg: dict | None = None) -> Target:
    """The series config's video target (`series.target`, else the default)."""
    tid = ((series_cfg or {}).get("series") or {}).get("target") or DEFAULT_VIDEO_TARGET
    return load_target(tid, "video")


DEFAULT_KEYFRAME_TARGET = "flux2_klein_edit"   # when it is ready, else the refs target


REFS_TARGET_KINDS = {"target": "image", "keyframe_target": "image",
                     "voice_target": "audio"}


def refs_block(series_cfg: dict | None) -> dict:
    """The series config's `refs` block ({"target", "keyframe_target",
    "voice_target"}), validated: the first two must name an image target,
    `voice_target` an audio one. ValueError otherwise."""
    raw = (series_cfg or {}).get("refs") or {}
    if not isinstance(raw, dict):
        raise ValueError("series.json `refs` must be an object: {\"target\": \"<image target>\", "
                         "\"keyframe_target\": \"<image target>\", "
                         "\"voice_target\": \"<audio target>\"}")
    out = {}
    for k, kind in REFS_TARGET_KINDS.items():
        v = raw.get(k)
        if v in (None, ""):
            continue
        known = [t.id for t in list_targets(kind)]
        if not isinstance(v, str) or v not in known:
            raise ValueError(f"series.json refs.{k}: {v!r} is not an {kind} target "
                             f"(known: {', '.join(known)})")
        out[k] = v
    return out


def image_target(series_cfg: dict | None = None) -> Target:
    """The image target that makes this series' refs: the series config's
    `refs.target`, else krea2."""
    return load_target(refs_block(series_cfg).get("target") or DEFAULT_IMAGE_TARGET, "image")


def audio_target(series_cfg: dict | None = None) -> Target:
    """The audio target that makes this series' voice samples: the series
    config's `refs.voice_target`, else ltx2_voice."""
    return load_target(refs_block(series_cfg).get("voice_target") or DEFAULT_AUDIO_TARGET,
                       "audio")


def keyframe_target(series_cfg: dict | None = None, ready=None) -> Target:
    """The image target that makes shot keyframes: the series config's
    `refs.keyframe_target`, else flux2_klein_edit when `ready(target)` says
    it can render here (None: assume it can), else the refs target."""
    block = refs_block(series_cfg)
    if block.get("keyframe_target"):
        return load_target(block["keyframe_target"], "image")
    t = load_target(DEFAULT_KEYFRAME_TARGET, "image")
    if ready is None or ready(t):
        return t
    return image_target(series_cfg)


def repo_workflow(name: str) -> str | None:
    """The repo copy of the workflow a binding knows by `name` (its name among
    ComfyUI's saved workflows), or None."""
    for t in list_targets():
        if t.binding.workflow_name == name and t.binding.workflow \
                and os.path.isfile(t.binding.workflow):
            return t.binding.workflow
    return None


def target_for_workflow(name: str) -> Target | None:
    for t in list_targets():
        if t.binding.workflow_name == name:
            return t
    return None


# ---------------------------------------------------------------------------
# profiles and target selection
# ---------------------------------------------------------------------------

PROFILE_KEYS = ("target", "model", "loras", "steps")


def normalize_loras(value, where: str) -> list[dict]:
    """A LoRA list from a profile: "name", "name:0.6", {"name", "strength"}, or
    "none" for no LoRA at all."""
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        raise ValueError(f"{where}: loras must be a list of LoRA names")
    out = []
    for lo in value:
        if isinstance(lo, dict) and isinstance(lo.get("name"), str) and lo["name"]:
            s = lo.get("strength", 1.0)
            if isinstance(s, bool) or not isinstance(s, (int, float)):
                raise ValueError(f"{where}: LoRA {lo['name']} strength must be a number")
            out.append({"name": lo["name"], "strength": float(s)})
        elif isinstance(lo, str) and lo.strip():
            spec = lo.strip()
            if spec.lower() in ("none", "off", "-"):
                continue
            name, _, strength = spec.rpartition(":")
            try:
                out.append({"name": name, "strength": float(strength)} if name
                           else {"name": spec, "strength": 1.0})
            except ValueError:
                out.append({"name": spec, "strength": 1.0})
        else:
            raise ValueError(f"{where}: each LoRA is a name or {{\"name\", \"strength\"}}")
    return out


def series_profiles(series_cfg: dict) -> dict[str, dict]:
    """The series config's `profiles`, validated: {name: {target?, model?,
    loras?, steps?}} with only the keys the profile sets. Keys starting with
    `_` are comments, as in `subjects`."""
    raw = series_cfg.get("profiles") or {}
    if not isinstance(raw, dict):
        raise ValueError("series.json `profiles` must be an object of named profiles")
    out = {}
    for name, p in raw.items():
        if name.startswith("_"):
            continue
        where = f"series.json profile '{name}'"
        if not isinstance(p, dict):
            raise ValueError(f"{where} must be an object ({', '.join(PROFILE_KEYS)})")
        unknown = [k for k in p if k not in PROFILE_KEYS and not k.startswith("_")]
        if unknown:
            raise ValueError(f"{where}: unknown key(s) {', '.join(unknown)} "
                             f"(one of {', '.join(PROFILE_KEYS)})")
        prof = {}
        for k in ("target", "model"):
            if p.get(k) is not None:
                if not isinstance(p[k], str) or not p[k].strip():
                    raise ValueError(f"{where}: {k} must be a name")
                prof[k] = p[k].strip()
        if p.get("steps") is not None:
            if isinstance(p["steps"], bool) or not isinstance(p["steps"], int) or p["steps"] < 1:
                raise ValueError(f"{where}: steps must be a whole number of 1 or more")
            prof["steps"] = p["steps"]
        if p.get("loras") is not None:
            prof["loras"] = normalize_loras(p["loras"], where)
        out[name] = prof
    return out


def _profile(profiles: dict, name: str | None, where: str) -> dict:
    if not name:
        return {}
    if name not in profiles:
        raise ValueError(f"{where}: profile '{name}' is not in series.json's profiles "
                         f"({', '.join(sorted(profiles)) or 'there are none'})")
    return profiles[name]


def render_layers(series_cfg: dict, seq: dict, shot: dict,
                  profiles: dict | None = None) -> list[dict]:
    """A shot's render settings, most specific first: [shot lines, shot
    profile, sequence lines, sequence profile]. `seq` and `shot` are the
    parser's dicts (keys model / lora / steps / profile / target)."""
    profiles = series_profiles(series_cfg) if profiles is None else profiles
    return [shot, _profile(profiles, shot.get("profile"), f"shot {shot['id']}"),
            seq, _profile(profiles, seq.get("profile"), f"sequence {seq['id']}")]


def layered(layers: list[dict], key: str, default=None, present: bool = False):
    """The first layer's `key`: set means truthy, or merely present with
    `present` (how steps has always worked)."""
    for layer in layers:
        if (key in layer) if present else layer.get(key):
            return layer[key]
    return default


def layered_lora(layers: list[dict]) -> tuple[str, object] | None:
    """("lora", "name") from a script line or ("loras", [..]) from a profile,
    whichever layer sets one first; None if none does."""
    for layer in layers:
        if layer.get("lora"):
            return "lora", layer["lora"]
        if "loras" in layer:
            return "loras", layer["loras"]
    return None


def shot_targets(story, series_cfg: dict) -> dict[str, str]:
    """{shot id: the video target it renders on}, in script order, after
    checking every `profile:` and `target:` the script names. Each shot layers
    like any render setting: the series config's `series.target` -> sequence
    profile -> sequence `target:` -> shot profile -> shot `target:`. A target
    no video target is called is an error naming the known ones."""
    profiles = series_profiles(series_cfg)
    default = video_target(series_cfg)
    known = [x.id for x in list_targets("video")]
    out = {}
    for sq in story.sequences:
        seq = {"id": sq.id, "profile": sq.profile, "target": sq.target}
        for s in sq.shots:
            shot = {"id": s.id, "profile": s.profile, "target": s.target}
            tid = layered(render_layers(series_cfg, seq, shot, profiles), "target",
                          default.id)
            if tid not in known:
                raise ValueError(f"shot {s.id}: target '{tid}' is not a video target "
                                 f"(known: {', '.join(known)})")
            out[s.id] = tid
    return out


def episode_targets(story, series_cfg: dict) -> list[tuple[Target, set[str] | None]]:
    """How a build splits an episode: [(target, shot ids)], the series target
    first. The series target's ids are None when every shot is its own (the
    whole episode compiles as it always has, byte for byte); it is listed even
    when no shot uses it, because shotlist.json is always written."""
    by_shot = shot_targets(story, series_cfg)
    default = video_target(series_cfg)
    others: dict[str, set[str]] = {}
    for sid, tid in by_shot.items():
        if tid != default.id:
            others.setdefault(tid, set()).add(sid)
    if not others:
        return [(default, None)]
    mine = {sid for sid, tid in by_shot.items() if tid == default.id}
    return [(default, mine)] + [(load_target(t, "video"), ids)
                                for t, ids in sorted(others.items())]


def episode_target(story, series_cfg: dict) -> Target:
    """The series target, after checking every `profile:` and `target:` the
    script names (shot_targets). Shots on other targets are compiled into
    their own shotlists (episode_targets)."""
    shot_targets(story, series_cfg)
    return video_target(series_cfg)


# ---------------------------------------------------------------------------
# model families: is this file the model a target's param needs?
# ---------------------------------------------------------------------------

# (loader class, widget) -> the ComfyUI models folder its files come from
MODEL_FOLDERS: dict[tuple, str] = {
    ("UNETLoader", "unet_name"): "diffusion_models",
    ("UnetLoaderGGUF", "unet_name"): "diffusion_models",
    ("CheckpointLoaderSimple", "ckpt_name"): "checkpoints",
    ("LTXVAudioVAELoader", "ckpt_name"): "checkpoints",
    ("LTXAVTextEncoderLoader", "ckpt_name"): "checkpoints",
    ("LTXAVTextEncoderLoader", "text_encoder"): "text_encoders",
    ("CLIPLoader", "clip_name"): "text_encoders",
    ("DualCLIPLoader", "clip_name1"): "text_encoders",
    ("DualCLIPLoader", "clip_name2"): "text_encoders",
    ("VAELoader", "vae_name"): "vae",
    ("LatentUpscaleModelLoader", "model_name"): "latent_upscale_models",
    ("ModelPatchLoader", "name"): "model_patches",
    ("LoraLoaderModelOnly", "lora_name"): "loras",
    ("LoraLoader", "lora_name"): "loras",
}

MODEL_MATCHES = ("name", "fingerprint", "mismatch", "unknown", "unchecked")


def series_model_families(series_cfg: dict | None) -> dict[str, list[str]]:
    """The series config's `model_families`: {family: [globs]} that extend
    the targets' own name patterns (docs/AUTHORING.md). Keys starting with
    `_` are comments. ValueError on a malformed block."""
    raw = (series_cfg or {}).get("model_families")
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("series.json `model_families` must be an object: "
                         "{\"<family>\": [\"<glob>\", ...]}")
    out = {}
    for fam, pats in raw.items():
        if fam.startswith("_"):
            continue
        if isinstance(pats, str):
            pats = [pats]
        if not isinstance(pats, list) or not all(isinstance(p, str) and p.strip() for p in pats):
            raise ValueError(f"series.json model_families.{fam} must be a list of file name "
                             f"patterns, e.g. [\"my_merge*\"]")
        out[fam] = [p.strip() for p in pats]
    return out


def family_names(extra: dict | None = None) -> dict[str, list[str]]:
    """{family: [globs]}: every target's declared patterns, plus `extra` (the
    series config's model_families). Used to tell variants apart by name."""
    out: dict[str, list[str]] = {}
    for t in list_targets():
        for m in t.models.values():
            have = out.setdefault(m["family"], [])
            have += [p for p in m["patterns"] if p not in have]
    for fam, pats in (extra or {}).items():
        have = out.setdefault(fam, [])
        have += [p for p in pats if p not in have]
    return out


def model_stem(name: str) -> str:
    """A model file's name without its folder and .safetensors."""
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    return base[:-len(".safetensors")] if base.lower().endswith(".safetensors") else base


def check_model(target: Target, param: str, filename: str, resolve=None,
                extra: dict | None = None, cache=None) -> dict | None:
    """Is `filename` a model of the family `target` wants for `param`?
    None when the target declares no family for it (or no file is set).

    `resolve(folder, name)` finds the file on disk (None: not found); with
    `resolve` None only the name is checked. `extra` is the series config's
    model_families; `cache` a modelid.ModelIdCache. The answer is
    {"param", "file", "family", "label", "patterns", "match", "found",
    "message", "block"}, `match` one of

      name         the name matches the family's patterns (nothing is read)
      fingerprint  it doesn't, but the header says it is that family (or a
                   parent family the header can't narrow; `message` says so)
      mismatch     the header says another family: `block` is true (queueing
                   stops unless the request allows a model mismatch)
      unknown      the header says nothing h3pipe can compare, or the file
                   isn't found
      unchecked    no models folder to read, and the name alone didn't match
    """
    spec = target.models.get(param)
    if not spec or not filename:
        return None
    fam, label = spec["family"], modelid.family_label(spec["family"])
    pats = list(spec["patterns"]) + [p for p in (extra or {}).get(fam, [])
                                     if p not in spec["patterns"]]
    out = {"param": param, "file": filename, "family": fam, "label": label, "patterns": pats,
           "match": "name", "found": None, "message": "", "block": False}
    if modelid.name_matches(filename, pats):
        return out
    who = f"{param} {model_stem(filename)}"
    if resolve is None:
        out.update(match="unchecked", message=(
            f"{who} isn't named like {label} and wasn't fingerprinted (no ComfyUI models "
            f"folder to read: set COMFYUI_PATH)"))
        return out
    path = resolve(spec["folder"], filename) if spec["folder"] else None
    if not path:
        out.update(match="unknown", message=(
            f"{who} isn't named like {label} and isn't in ComfyUI's "
            f"{spec['folder'] or 'models'} folder, so it wasn't fingerprinted"))
        return out
    found = modelid.identify(path, family_names(extra), cache, filename=filename)
    out["found"] = found
    rel = modelid.relation(found.get("family"), fam)
    how = found.get("confidence")
    if found.get("base"):                                 # the header's family, the name's variant
        how = f"{found.get('base_confidence')} + name"
    if rel in ("same", "variant"):
        out.update(match="fingerprint", message=(
            f"{who} isn't named like {label}, but its header says {found['label']} ({how})"))
    elif rel == "ambiguous":
        out.update(match="fingerprint", message=(
            f"{who} isn't named like {label}; its header says {found['label']}, which can't "
            f"be told from {label} by its tensors, and the name doesn't say which it is"))
    elif rel == "different" and (found.get("confidence") in ("metadata", "tensors")
                                 or found.get("base")):
        out.update(match="mismatch", block=True, message=(
            f"{model_stem(filename)} is {found['label']} ({how}), but this {target.short} "
            f"target's {param} must be {label}"))
    elif found.get("family"):
        out.update(match="unknown", message=(
            f"{who} isn't named like {label}; it looks like {found['label']} ({how}), "
            f"which h3pipe can't verify against {fam}"))
    else:
        out.update(match="unknown", message=(
            f"{who} isn't named like {label} and its header matches no family h3pipe knows "
            f"({found.get('detail')})"))
    return out


# ---------------------------------------------------------------------------
# requirement tiers: which installed file each model param renders with
# ---------------------------------------------------------------------------
#
# A target's `models` params each have a tier (target.json `models.<param>.tier`):
#
#   required     the target can't render without it (the diffusion model, text
#                encoder, VAEs): its shots are blocked, naming the file and
#                where to get it (`downloads`)
#   accelerator  a turbo / distilled LoRA (or checkpoint): without it the pass
#                renders with its preset's `base` (more steps, real CFG, no LoRA)
#   optional     only a feature needs it (the LTX duration head: `dur: model`)
#
# Each wanted file resolves to an INSTALLED one (ComfyUI's list for the param's
# loader): the file itself, else the best installed file of the same family
# (resolve_file). Resolution never picks a file of another family.

PRECISION = re.compile(r"(?<![a-z0-9])(nvfp4|fp4|fp8|int8|int4|bf16|fp16|fp32)", re.I)


def precision(name: str) -> str | None:
    """The weight precision a file name says (fp8, int8, bf16, ...), or None."""
    m = PRECISION.search(model_stem(name).lower().replace("-", "_"))
    return m.group(1).lower() if m else None


def _captured(rx: str, name: str) -> str | None:
    m = re.search(rx, model_stem(name), re.I)
    if not m:
        return None
    return (m.group(1) if m.groups() else m.group(0)).lower()


def keeps(spec: dict, want: str, cand: str) -> bool:
    """A substitute `cand` keeps what `want` says in each of the spec's `keep`
    regexes (the same step count, noise stage, distilled vs dev): when the
    wanted name has a match, the candidate must match with the same word."""
    for rx in spec.get("keep") or []:
        w = _captured(rx, want)
        if w is not None and _captured(rx, cand) != w:
            return False
    return True


def _same_file(a: str, b: str) -> bool:
    """Two names of one file: equal, or equal once ComfyUI's subfolder is
    left off ("LTX-2.3\\x.safetensors" is "x.safetensors")."""
    def norm(s: str) -> str:
        return s.replace("\\", "/").rsplit("/", 1)[-1].lower()
    return a == b or norm(a) == norm(b)


def resolve_file(target: Target, param: str, want: str, installed: list[str],
                 resolve=None, extra: dict | None = None, cache=None,
                 spec: dict | None = None) -> dict | None:
    """The installed file a param renders with for `want`:
    {"using", "how": "exact" | "family", "match": "file" | "name" |
    "fingerprint", "detail"}, or None when nothing installed will do.

    1. `want` itself (also in a subfolder of the models folder);
    2. else the best installed file of the same family: a name match (the
       family's patterns, plus the series config's model_families `extra`)
       beats a fingerprint match (its header, read through `resolve(folder,
       name)`, says the family: only for families a header can tell); a tie
       goes to the file of the same precision as `want` (fp8 / int8 / bf16 /
       fp16), then the shortest name. A candidate must keep the spec's `keep`
       words and match none of its `exclude` globs. Never another family."""
    for name in installed:
        if _same_file(name, want):
            return {"using": name, "how": "exact", "match": "file", "detail": ""}
    spec = spec if spec is not None else target.models.get(param)
    if not spec or not spec.get("family"):
        return None
    fam = spec["family"]
    pats = list(spec["patterns"]) + [p for p in (extra or {}).get(fam, [])
                                     if p not in spec["patterns"]]
    cands = [n for n in installed if keeps(spec, want, n)
             and not modelid.name_matches(n, spec.get("exclude") or [])]
    want_p = precision(want)

    def best(names: list[str]) -> str:
        order = {n: i for i, n in enumerate(names)}
        return min(names, key=lambda n: (precision(n) != want_p, len(model_stem(n)), order[n]))

    named = [n for n in cands if modelid.name_matches(n, pats)]
    if named:
        using = best(named)
        return {"using": using, "how": "family", "match": "name",
                "detail": f"named like {modelid.family_label(fam)}"}
    if resolve is None or not spec.get("folder") or not modelid.has_signature(fam):
        return None
    names = family_names(extra)
    fp = []
    for n in cands:
        path = resolve(spec["folder"], n)
        if not path:
            continue
        found = modelid.identify(path, names, cache, filename=n)
        # the family or one of its variants; never a parent the header can't
        # narrow (that could be the wrong variant: H3 Ref2VA vs FL2VA)
        if modelid.relation(found.get("family"), fam) in ("same", "variant"):
            fp.append(n)
    if fp:
        using = best(fp)
        return {"using": using, "how": "family", "match": "fingerprint",
                "detail": f"its header says {modelid.family_label(fam)}"}
    return None


def lora_spec(target: Target, name: str, slot: bool = False) -> dict:
    """The models spec a LoRA file falls under: the target's `loras` entry
    when the name is of its family; else, for the LoRA in the pass's own
    slot (`slot`: the preset's or the series config's pass `lora`, which is
    the turbo LoRA where `loras` is an accelerator) that entry's tier with no
    family (only that file, else the base); else an anonymous required one
    (a style LoRA from a profile: only that exact file will do)."""
    m = target.models.get("loras")
    if m and modelid.name_matches(name, m["patterns"]):
        return m
    w = target.binding.specs("loras")
    ct = (m or {}).get("class_type") or (w[0].get("class_type") if w else "LoraLoaderModelOnly")
    fld = (m or {}).get("field") or (w[0].get("name") if w else "lora_name")
    return {"family": None, "patterns": [],
            "tier": m["tier"] if (m and slot) else "required", "feature": "",
            "folder": (m or {}).get("folder") or MODEL_FOLDERS.get((ct, fld), "loras"),
            "class_type": ct, "field": fld, "default": None, "keep": [], "exclude": []}


def wanted_files(target: Target, pass_: str, series_cfg: dict | None = None) -> dict:
    """What a pass loads with nothing but the target (and the series config's
    pass blocks) deciding: {param: file} for every model param, plus "loras"
    (a list of {"name", "strength"}; [] for none). For readiness; a job's own
    values come from h3jobs.model_values."""
    if series_cfg is not None and target.kind == "video":
        p = target.preset(pass_, series_cfg)
    else:
        p = target.presets[pass_]
    final = target.presets.get("final")
    out: dict = {}
    for param, m in target.models.items():
        if param == "loras":
            continue
        if param == "model":
            v = p.model or (final.model if final else None) or m["default"]
        else:
            v = p.extra.get(param) or (final.extra.get(param) if final else None) or m["default"]
        if isinstance(v, str) and v:
            out[param] = v
    lora = p.lora if isinstance(p.lora, str) else ""
    if lora.strip().lower() not in ("", "none", "off", "-"):
        out["loras"] = [{"name": lora, "strength": float(p.extra.get("lora_strength", 1.0))}]
    elif isinstance(p.extra.get("loras"), list):
        out["loras"] = [dict(lo) for lo in p.extra["loras"]]
    else:
        out["loras"] = []
    return out


def lora_slot(target: Target, pass_: str, series_cfg: dict | None = None,
              defaults: dict | None = None) -> set[str]:
    """The LoRA names in a pass's own `lora` slot: the target preset's, the
    series config's pass block's (through Target.preset), and a shotlist's
    `defaults` (its `lora`, or a preset's per-stage `loras`). Where the
    target's `loras` are an accelerator, these are its turbo LoRAs whatever
    they are called (lora_spec)."""
    out: set[str] = set()
    try:
        p = target.preset(pass_, series_cfg) if (series_cfg is not None
                                                and target.kind == "video") \
            else target.presets.get(pass_)
    except Exception:
        p = target.presets.get(pass_)
    layers = [dict(p.extra, lora=p.lora) if p is not None else {}, defaults or {}]
    for layer in layers:
        lo = layer.get("lora")
        if isinstance(lo, str) and lo.strip().lower() not in ("", "none", "off", "-"):
            out.add(lo.rpartition(":")[0] if re.search(r":\d*\.?\d+$", lo) else lo)
        for x in layer.get("loras") or [] if isinstance(layer.get("loras"), list) else []:
            if isinstance(x, dict) and x.get("name"):
                out.add(x["name"])
    return out


def download_for(target: Target, name: str, folder: str | None = None) -> dict:
    """{"folder", "url", "source"} of a file (target.json `downloads`, by
    name, ignoring a subfolder); url None when there is no record."""
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    d = target.downloads.get(base) or target.downloads.get(name) or {}
    return {"folder": d.get("folder") or folder, "url": d.get("url") or None,
            "source": d.get("source") or f"no download record: search for {base}"}


def _missing(target: Target, param: str, spec: dict, want: str, tier: str) -> dict:
    # an optional file always says what it switches on (features_off lists
    # the same words)
    feature = spec.get("feature") or (f"{param} {model_stem(want)}" if tier == "optional" else "")
    return {"param": param, "tier": tier, "want": want, "family": spec.get("family"),
            "label": modelid.family_label(spec.get("family")),
            **download_for(target, want, spec.get("folder")),
            **({"feature": feature} if feature else {})}


def missing_message(m: dict) -> str:
    """One missing file as a person reads it: the file, the folder, the URL."""
    where = f"models/{m['folder']}" if m.get("folder") else "ComfyUI's models folder"
    get = f"download {m['url']}" if m.get("url") else (m.get("source") or "no download record")
    return f"{m['param']} {m['want']} is not installed ({where}; {get})"


BASE_FILE_KEYS = ("model", "lora", "loras")


def resolve_models(target: Target, pass_: str, wanted: dict, listing, resolve=None,
                   extra: dict | None = None, cache=None, slot=()) -> dict:
    """Resolve every file of `wanted` ({param: file}, plus "loras": a list of
    {"name", "strength", ...} or None) against what is installed.

    `listing(spec)` gives the files ComfyUI offers for a models spec (its
    `folder` / `class_type` / `field`), or None when that can't be known (the
    param is then left alone, as before). `resolve`, `extra` and `cache` are
    resolve_file's. `slot` names the LoRAs in the pass's own `lora` slot
    (lora_spec: the turbo LoRA, whatever the series config calls it).
    Returns

      {"resolved": {param: {"want", "using", "how", "tier"}},
       "missing":  [{"param", "tier", "want", "family", "label", "folder",
                     "url", "source", "feature"?}],
       "blocked":  the missing entries that stop the pass (required files, and
                   an accelerator with no `base` to fall back to),
       "features_off": [feature, ...],
       "files":    {param: the file to load instead} (substitutes only),
       "loras":    the LoRA list to use (None: unchanged),
       "base":     the pass's `base` preset when an accelerator is missing,
       "values":   the base's settings beside its files (steps, cfg, ...),
       "notes":    [what a take's notes should say]}

    `how` is "exact", "family" (another installed file of the family),
    "base" (an accelerator's place taken by the base preset) or "off" (an
    optional file missing: its feature is off)."""
    out = {"resolved": {}, "missing": [], "blocked": [], "features_off": [], "files": {},
           "loras": None, "base": None, "values": {}, "notes": []}
    models = target.models
    accel: list[tuple[str, str]] = []                    # (param, file) not installed
    listed: dict = {}

    def one(param: str, spec: dict, want: str):
        """(resolve_file's answer or None, known): known is False when
        nothing lists the spec's folder."""
        key = (spec.get("folder"), spec.get("class_type"), spec.get("field"))
        if key not in listed:
            try:
                listed[key] = listing(spec)
            except Exception:
                listed[key] = None
        files = listed[key]
        if files is None:
            return None, False
        return resolve_file(target, param, want, list(files), resolve, extra, cache, spec), True

    def lost(param: str, spec: dict, want: str, tier: str) -> dict:
        m = _missing(target, param, spec, want, tier)
        out["missing"].append(m)
        if tier == "required":
            out["blocked"].append(m)
        return m

    for param, want in wanted.items():
        if param == "loras" or not isinstance(want, str) or not want:
            continue
        spec = models.get(param)
        if not spec:
            continue
        r, known = one(param, spec, want)
        if not known:
            continue
        if r:
            out["resolved"][param] = {"want": want, "using": r["using"], "how": r["how"],
                                      "tier": spec["tier"]}
            if r["using"] != want:
                out["files"][param] = r["using"]
            if r["how"] == "family":
                out["notes"].append(f"{param} {model_stem(want)} isn't installed: rendered "
                                    f"with {model_stem(r['using'])} ({r['detail']})")
            continue
        miss = lost(param, spec, want, spec["tier"])
        if spec["tier"] == "optional":
            out["resolved"][param] = {"want": want, "using": None, "how": "off",
                                      "tier": "optional"}
            if miss["feature"] not in out["features_off"]:
                out["features_off"].append(miss["feature"])
        elif spec["tier"] == "accelerator":
            accel.append((param, want))

    loras = wanted.get("loras")
    lora_list = [dict(lo) for lo in loras or [] if isinstance(lo, dict) and lo.get("name")] \
        if isinstance(loras, list) else None
    accel_loras: list[str] = []
    if lora_list:
        new, how, tier_of, known_any = [], "exact", "required", False
        for lo in lora_list:
            spec = lora_spec(target, lo["name"], lo["name"] in slot)
            r, known = one("loras", spec, lo["name"])
            if not known:
                new.append(lo)
                continue
            known_any = True
            if spec["tier"] != "required":
                tier_of = spec["tier"]
            if r:
                new.append(dict(lo, name=r["using"]))
                if r["how"] == "family":
                    how = "family"
                    out["notes"].append(f"LoRA {model_stem(lo['name'])} isn't installed: "
                                        f"rendered with {model_stem(r['using'])} ({r['detail']})")
                continue
            miss = lost("loras", spec, lo["name"], spec["tier"])
            if spec["tier"] == "accelerator":
                accel_loras.append(lo["name"])
            elif spec["tier"] == "optional" and miss["feature"] not in out["features_off"]:
                out["features_off"].append(miss["feature"])
        if known_any:
            out["resolved"]["loras"] = {"want": [lo["name"] for lo in lora_list],
                                        "using": [lo["name"] for lo in new
                                                  if lo["name"] not in accel_loras],
                                        "how": how, "tier": tier_of}
            if [lo["name"] for lo in new] != [lo["name"] for lo in lora_list]:
                out["loras"] = new
            lora_list = new

    if not (accel or accel_loras):
        return out
    preset = target.presets.get(pass_)
    base = copy.deepcopy(preset.base) if preset is not None and preset.base else None
    if base is None:
        # nothing to fall back to: this pass can't do without its accelerator
        out["blocked"] += [m for m in out["missing"] if m["tier"] == "accelerator"]
        return out
    out["base"] = base
    out["values"] = {k: v for k, v in base.items()
                     if k not in BASE_FILE_KEYS and k not in models}
    # the base's own files are required: it is the fallback
    for param, want in accel:
        bwant = base.get(param)
        using = None
        if bwant:
            spec = dict(models[param], tier="required")
            r, known = one(param, spec, bwant)
            if known and not r:
                lost(param, spec, bwant, "required")
            using = r["using"] if r else bwant
            out["files"][param] = using
        out["resolved"][param] = {"want": want, "using": using, "how": "base",
                                  "tier": "accelerator"}
    base_loras = base.get("loras")
    if base_loras is not None or accel_loras:
        cur = lora_list or []
        fams = {lora_spec(target, lo["name"]).get("family") for lo in base_loras or []}
        fams.discard(None)

        def replaced(lo: dict) -> bool:
            s = lora_spec(target, lo["name"], lo["name"] in slot)
            return (lo["name"] in accel_loras or s["tier"] == "accelerator"
                    or s.get("family") in fams)
        kept = [lo for lo in cur if not replaced(lo)]
        added = []
        for lo in base_loras or []:
            spec = dict(lora_spec(target, lo["name"]), tier="required")
            r, known = one("loras", spec, lo["name"])
            if known and not r:
                lost("loras", spec, lo["name"], "required")
            added.append(dict(lo, name=r["using"] if r else lo["name"]))
        out["loras"] = kept + added
        prev = out["resolved"].get("loras") or {}
        out["resolved"]["loras"] = {
            "want": prev.get("want") or [lo["name"] for lo in cur],
            "using": [lo["name"] for lo in out["loras"]],
            "how": "base" if accel_loras else prev.get("how", "exact"),
            "tier": "accelerator" if accel_loras else prev.get("tier", "required")}
    said = ", ".join(f"{k} {v}" for k, v in base.items()
                     if k not in ("loras", "lora") and not isinstance(v, (dict, list)))
    gone = [f for _, f in accel] + accel_loras
    out["notes"].append(
        f"accelerator not installed ({', '.join(model_stem(f) for f in gone)}): rendered "
        f"with the {pass_} base preset ({said}"
        + (", no LoRA" if base.get("loras") == [] else "") + "), which is slower")
    return out
