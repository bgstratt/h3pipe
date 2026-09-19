"""
targets — everything model-specific, one folder per model.

    targets/<kind>/<id>/target.json   data: template, recipe, binding, presets
    targets/<kind>/<id>/*.py          code: the prompt writer and the compile
    targets/<kind>/<id>/workflow.json the ComfyUI graph the binding patches

`kind` is "video" (a shot model: story IR -> shotlist entries) or "image" (a
reference model: a ref request -> a picture). The core (h3core, h3build,
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

or, for an image target:

    ref_prompt(request, series_cfg)                 -> the wording of one ref

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

import importlib
import json
import math
import os
from dataclasses import dataclass, field

HERE = os.path.dirname(os.path.abspath(__file__))
KINDS = ("video", "image")
DEFAULT_VIDEO_TARGET = "minimax_h3_ref2va"
DEFAULT_IMAGE_TARGET = "krea2"
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

    def to_json(self) -> dict:
        d = {"model": self.model, "lora": self.lora, "steps": self.steps,
             "width": self.width, "height": self.height}
        d.update(self.extra)
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
            self.presets[name] = Preset(name, p.pop("model", None), p.pop("lora", None),
                                        p.pop("steps", None), p.pop("width", None),
                                        p.pop("height", None), p)
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

    def capabilities(self) -> dict:
        """What a picker or the core may need to know without asking which
        model this is (GET /h3pipe/targets)."""
        r = self.recipe
        return {"policies": self.policies,
                "policy_fallback": (r.get("policy_fallback") or {}).get("to"),
                "voice_reference": bool(r.get("voice_slots")),
                "subject_refs": bool(r.get("subject_slots")),
                "keyframes": list(r.get("keyframes") or []),
                "prompt": r.get("prompt", "sections" if r.get("subject_slots") else "prose"),
                "negative_prompt": bool(self.binding.specs("negative"))}

    # image
    def ref_prompt(self, req: RefRequest, series_cfg: dict) -> str:
        return self._need("ref_prompt")(self, req, series_cfg)

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
                      extra=dict(base.extra))

    # -- description (GET /h3pipe/targets) ----------------------------------

    def describe(self) -> dict:
        return {"id": self.id, "kind": self.kind, "label": self.label,
                "default": self.id == (DEFAULT_VIDEO_TARGET if self.kind == "video"
                                       else DEFAULT_IMAGE_TARGET),
                "presets": {k: p.to_json() for k, p in self.presets.items()},
                "widgets": self.binding.widgets(),
                "workflow": self.binding.workflow_name,
                "loader": self.binding.loader_class or None,
                "saver": self.binding.saver_class or None,
                "template": self.template.to_json(),
                "short": self.short,
                "capabilities": self.capabilities() if self.kind == "video" else {}}


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


def image_target(series_cfg: dict | None = None) -> Target:
    """The image target that makes this series' refs (only krea2 so far)."""
    return load_target(DEFAULT_IMAGE_TARGET, "image")


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
