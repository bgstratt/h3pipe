"""
h3inspect — read a ComfyUI workflow and propose the target.json that drives it.

A target is data: which node widget takes the prompt, the size, the length, the
seed, the model and the LoRAs; what the legal frame counts are; which files it
loads and what family they must be. Almost all of it is in the graph already,
so this works it out and says what it could not (docs/PLAN.md Phase 12b).

    inspect_graph(graph, object_info=..., model_list=...) ->
        {"proposal": {...target.json...},   what to save (draft: true)
         "matched": {param: how it was found},
         "ambiguous": [{param, candidates, ask}],   the author must choose
         "warnings": [...],                 guessed, and worth checking
         "models": [{param, file, family, folder, ...}],
         "nodes": {class: python_module} of anything outside ComfyUI core,
         "problems": [...]}                 the graph itself is wrong

Nothing here writes: the routes save the proposal (once the author has
confirmed it) and `h3.py target-from-workflow` prints it.

What is inferred, and from where:

    saver          the terminal node, matched against the savers the shipped
                   targets replace (CreateVideo / SaveVideo / SaveImage)
    prompt         the text widget the sampler's `positive` input reaches;
                   `negative` the same through `negative`
    widgets        by name: seed / noise_seed, steps, cfg, sampler_name,
                   scheduler, shift, denoise, width, height, length, fps
    models         every combo widget whose choices are a models folder's
                   files; the family from the file it currently loads
    template       size_multiple and frames.step from the widgets' own `step`
                   (/object_info), frames.base from the length default,
                   fps from the video node, max_size from the current size
    audio          whether anything in the graph makes sound
    keyframes      one LoadImage, wired declaratively (binding.inputs)

Stdlib only.
"""
from __future__ import annotations

import copy
import json
import os
import re

import h3jobs as J
import targets as TG

KIND = "video"                       # the only kind this proposes, for now
CORE_MODULES = ("nodes", "comfy_extras.", "comfy_api_nodes.")
MODEL_EXTS = (".safetensors", ".ckpt", ".pt", ".pth", ".sft", ".gguf", ".bin")
# the models folders a widget's choices may come from, most specific first
MODEL_FOLDERS = ("diffusion_models", "unet", "checkpoints", "text_encoders", "clip",
                 "clip_vision", "vae", "loras", "upscale_models", "controlnet",
                 "model_patches", "style_models", "gligen", "photomaker")
# a param name for the loader class that takes it (the widget's own name decides
# when the class is unknown)
MODEL_PARAMS = {"UNETLoader": "model", "CheckpointLoaderSimple": "model",
                "UNETLoaderGGUF": "model", "ImageOnlyCheckpointLoader": "model",
                "CLIPLoader": "text_encoder", "DualCLIPLoader": "text_encoder",
                "TripleCLIPLoader": "text_encoder", "CLIPLoaderGGUF": "text_encoder",
                "VAELoader": "vae", "LoraLoaderModelOnly": "loras", "LoraLoader": "loras",
                "UpscaleModelLoader": "upscaler", "ModelPatchLoader": "model_patch"}
# widget name -> the render param it is, in the order they are looked for
WIDGET_PARAMS = (
    ("seed", ("seed", "noise_seed")),
    ("steps", ("steps",)),
    ("cfg", ("cfg", "cfg_scale", "guidance_scale")),
    ("sampler", ("sampler_name",)),
    ("scheduler", ("scheduler",)),
    ("shift", ("shift",)),
    ("denoise", ("denoise",)),
    ("width", ("width",)),
    ("height", ("height",)),
    ("length", ("length", "num_frames", "video_frames")),
    ("fps", ("fps", "frame_rate")),
)
TEXT_FIELDS = ("text", "prompt", "positive_prompt", "string")
# inputs that carry conditioning a prompt reaches
POSITIVE_INPUTS = ("positive", "conditioning", "cond")
NEGATIVE_INPUTS = ("negative", "negative_conditioning")


class InspectError(ValueError):
    """The graph can't be read, or isn't one this can propose a target for."""


# ---------------------------------------------------------------------------
# reading /object_info
# ---------------------------------------------------------------------------

def class_info(object_info: dict, ctype: str) -> dict:
    return (object_info or {}).get(ctype) or {}


def widget_specs(object_info: dict, ctype: str) -> dict:
    """{widget name: (type, meta)} of one class, required then optional. `type`
    is "INT" / "FLOAT" / "STRING" / "BOOLEAN" / "COMBO"; a combo's meta has
    `choices`."""
    info = class_info(object_info, ctype).get("input") or {}
    out = {}
    for group in ("required", "optional"):
        for name, spec in (info.get(group) or {}).items():
            if not isinstance(spec, list) or not spec:
                continue
            kind, meta = spec[0], (spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {})
            # a combo is either [[choices], {...}] (a file list) or
            # ["COMBO", {"options": [...]}] (an enum), as h3jobs.choices_in reads
            if isinstance(kind, list):
                out[name] = ("COMBO", dict(meta, choices=[str(c) for c in kind]))
            elif kind == "COMBO":
                out[name] = ("COMBO", dict(meta,
                                           choices=[str(c) for c in meta.get("options") or []]))
            elif kind in ("INT", "FLOAT", "STRING", "BOOLEAN"):
                out[name] = (kind, dict(meta))
    return out


def is_core(object_info: dict, ctype: str) -> bool:
    """Whether a node class comes with ComfyUI (its `python_module`)."""
    mod = str(class_info(object_info, ctype).get("python_module") or "")
    return any(mod == m or mod.startswith(m) for m in CORE_MODULES)


# ---------------------------------------------------------------------------
# walking the graph
# ---------------------------------------------------------------------------

def nodes_of(graph: dict, ctype: str) -> list[str]:
    return [k for k, v in graph.items() if v.get("class_type") == ctype]


def title_of(graph: dict, nid: str) -> str:
    return ((graph[nid].get("_meta") or {}).get("title") or "").strip()


def links_into(graph: dict, nid: str) -> list[tuple[str, str]]:
    """(input name, source node) of every link into a node."""
    return [(name, v[0]) for name, v in (graph[nid].get("inputs") or {}).items()
            if J.is_link(v)]


def consumers(graph: dict, nid: str) -> list[tuple[str, str]]:
    """(node, input name) of everything a node's output feeds."""
    return [(k, name) for k, v in graph.items()
            for name, val in (v.get("inputs") or {}).items()
            if J.is_link(val) and val[0] == nid]


def text_widget(object_info: dict, graph: dict, nid: str) -> str | None:
    """The name of the node's own text widget, if it has one."""
    have = graph[nid].get("inputs") or {}
    specs = widget_specs(object_info, graph[nid]["class_type"])
    for field in TEXT_FIELDS:
        spec = specs.get(field)
        if spec and spec[0] == "STRING" and not J.is_link(have.get(field)):
            return field
    for field, spec in specs.items():
        if spec[0] == "STRING" and spec[1].get("multiline") and not J.is_link(have.get(field)):
            return field
    return None


def walk_to_text(object_info: dict, graph: dict, start: str, depth: int = 8):
    """(node, field) of the text widget behind a conditioning input: follow the
    links back until a node holds one. None when nothing does."""
    seen, queue = set(), [(start, 0)]
    while queue:
        nid, d = queue.pop(0)
        if nid in seen or d > depth or nid not in graph:
            continue
        seen.add(nid)
        field = text_widget(object_info, graph, nid)
        if field:
            return nid, field
        for _name, src in links_into(graph, nid):
            queue.append((src, d + 1))
    return None


def samplers(object_info: dict, graph: dict) -> list[str]:
    """Nodes that take conditioning: a sampler, a guider, or a conditioning
    node that a prompt reaches (WanImageToVideo, MiniMaxH3ReferenceToVideo)."""
    out = []
    for nid, v in graph.items():
        have = v.get("inputs") or {}
        if any(name in have for name in POSITIVE_INPUTS + NEGATIVE_INPUTS):
            out.append(nid)
    return out


# ---------------------------------------------------------------------------
# the saver: what a target's H3SaveShot takes the place of
# ---------------------------------------------------------------------------

def saver_recipes(kind: str = KIND) -> dict:
    """{the class a shipped target's saver replaces: that saver spec}, so a
    stock graph ending in CreateVideo / SaveVideo / SaveImage is recognised the
    same way the shipped targets are. Taken from the targets themselves, so it
    can't drift from them."""
    out = {}
    for t in TG.list_targets(kind):
        sv = t.binding.saver
        rep = (sv or {}).get("replace") or {}
        if rep.get("class_type") and rep["class_type"] not in out:
            out[rep["class_type"]] = json.loads(json.dumps(sv))
    return out


def find_saver(graph: dict, kind: str = KIND) -> tuple[dict, str]:
    """(the binding's saver spec, the node it replaces). InspectError when the
    graph ends in nothing this knows."""
    recipes = saver_recipes(kind)
    for ctype, spec in recipes.items():
        ids = nodes_of(graph, ctype)
        if len(ids) == 1:
            return spec, ids[0]
        if len(ids) > 1:
            raise InspectError(f"the graph has {len(ids)} {ctype} nodes; leave one, or the "
                              f"take's video can't be told from a preview")
    saver = TG.load_target(TG.DEFAULT_VIDEO_TARGET, "video").binding.saver_class
    if nodes_of(graph, saver):
        return {"class_type": saver}, nodes_of(graph, saver)[0]
    known = ", ".join(sorted(recipes) + [saver])
    raise InspectError(f"the graph ends in nothing h3pipe can save from: it needs one of "
                       f"{known}")


# ---------------------------------------------------------------------------
# params
# ---------------------------------------------------------------------------

def _spec(graph: dict, nid: str, field: str, many: list[str] | None = None) -> dict:
    """A widget spec for target.json, narrowed by title when the class repeats."""
    out = {"class_type": graph[nid]["class_type"], "field": field}
    if many and len(many) > 1 and title_of(graph, nid):
        out["title"] = title_of(graph, nid)
    return out


def widget_candidates(object_info: dict, graph: dict, names: tuple,
                      linked: bool = False) -> list[tuple[str, str]]:
    """(node, field) of every widget with one of these names. `linked` asks for
    the ones fed by another node instead: h3pipe patches those too (it cuts
    whatever fed them, as LTX's ResolutionSelector is cut), so they are
    candidates when nothing holds the value itself."""
    out = []
    for nid, v in graph.items():
        specs = widget_specs(object_info, v["class_type"])
        have = v.get("inputs") or {}
        for field in names:
            if field not in specs:
                continue
            if J.is_link(have.get(field)) == bool(linked):
                out.append((nid, field))
    return out


def detect_params(object_info: dict, graph: dict, saver_node: str) -> tuple[dict, dict, list]:
    """(binding params, how each was found, ambiguities) for the widget params.

    One candidate is the answer. Several of one class with the same value are
    `all: true` (both stages' seeds). Several that disagree, or of different
    classes, are an ambiguity for the author to settle — by picking one, or by
    titling the node in ComfyUI and inspecting again.
    """
    params, matched, ambiguous, warnings = {}, {}, [], []
    for param, names in WIDGET_PARAMS:
        cands = [(n, f) for n, f in widget_candidates(object_info, graph, names)
                 if n != saver_node]
        if not cands:
            # nothing holds the value: a node feeds it (a resolution picker, a
            # primitive). h3pipe cuts that link and writes the value itself.
            cands = [(n, f) for n, f in widget_candidates(object_info, graph, names, True)
                     if n != saver_node]
            if cands:
                warnings.append(
                    f"{param}: {', '.join(sorted({graph[n]['class_type'] for n, _ in cands}))}"
                    f".{cands[0][1]} is fed by another node; h3pipe will cut that link and "
                    f"write the shot's value.")
        if not cands:
            continue
        if len(cands) == 1:
            nid, field = cands[0]
            params[param] = _spec(graph, nid, field)
            matched[param] = f"{graph[nid]['class_type']}.{field}"
            continue
        classes = {graph[n]["class_type"] for n, _ in cands}
        values = {json.dumps((graph[n].get("inputs") or {}).get(f)) for n, f in cands}
        if len(classes) == 1 and len(values) == 1:
            nid, field = cands[0]
            params[param] = dict(_spec(graph, nid, field), all=True)
            matched[param] = f"{graph[nid]['class_type']}.{field} (all {len(cands)})"
            continue
        ambiguous.append({
            "param": param,
            "candidates": [{"node": n, "class_type": graph[n]["class_type"], "field": f,
                            "title": title_of(graph, n),
                            "value": (graph[n].get("inputs") or {}).get(f)} for n, f in cands],
            "ask": (f"{len(cands)} nodes could take {param}: "
                    + ", ".join(sorted(f"{graph[n]['class_type']}.{f}" for n, f in cands))
                    + ". Pick one, or give it a title in ComfyUI and inspect again."),
        })
    return params, matched, ambiguous, warnings


def detect_prompt(object_info: dict, graph: dict) -> tuple[dict, dict, list]:
    """The prompt and negative widgets: walk each sampler's positive / negative
    conditioning back to the text they came from."""
    params, matched, warnings = {}, {}, []
    found: dict[str, set] = {"prompt": set(), "negative": set()}
    for nid in samplers(object_info, graph):
        have = graph[nid].get("inputs") or {}
        for param, inputs in (("prompt", POSITIVE_INPUTS), ("negative", NEGATIVE_INPUTS)):
            for name in inputs:
                link = have.get(name)
                if not J.is_link(link):
                    continue
                hit = walk_to_text(object_info, graph, link[0])
                if hit:
                    found[param].add(hit)
                break
    # a node with only `conditioning` (no negative) is a positive-only chain
    for param in ("prompt", "negative"):
        hits = sorted(found[param])
        if not hits:
            continue
        # one text node may feed several samplers: that is still one param
        nodes = {n for n, _ in hits}
        if len(nodes) == 1:
            nid, field = hits[0]
            many = nodes_of(graph, graph[nid]["class_type"])
            params[param] = _spec(graph, nid, field, many)
            matched[param] = f"{graph[nid]['class_type']}.{field}"
        else:
            warnings.append(f"{param}: {len(nodes)} different text nodes feed the samplers "
                            f"({', '.join(sorted(graph[n]['class_type'] for n in nodes))}); "
                            f"the first is used. Title the one h3pipe should write to.")
            nid, field = hits[0]
            params[param] = _spec(graph, nid, field, nodes_of(graph, graph[nid]["class_type"]))
            matched[param] = f"{graph[nid]['class_type']}.{field} (of {len(nodes)})"
    if "prompt" not in params:
        warnings.append("no prompt widget found: the graph's text doesn't reach a sampler's "
                        "conditioning through nodes this can follow. Name it by hand "
                        "(binding.params.prompt).")
    return params, matched, warnings


# ---------------------------------------------------------------------------
# model files
# ---------------------------------------------------------------------------

def folder_of(model_list, choices: list, value: str) -> str:
    """Which ComfyUI models folder a combo widget lists, by asking for each
    folder's files until the current value is in one ("" when unknown)."""
    if model_list is None:
        return ""
    for folder in MODEL_FOLDERS:
        try:
            files = model_list(folder, None, None) or []
        except Exception:
            continue
        if value and value in files:
            return folder
    return ""


def looks_like_models(choices: list) -> bool:
    return bool(choices) and any(str(c).lower().endswith(MODEL_EXTS) for c in choices)


def detect_models(object_info: dict, graph: dict, model_list=None,
                  extra: dict | None = None, cache=None) -> tuple[dict, dict, list, list]:
    """(models block, binding params for the file widgets, [{param, ...}] for the
    report, warnings): every combo widget whose choices are model files.

    The family is what the file it loads now identifies as (targets/modelid.py),
    so the target checks a substitute the same way the shipped ones do.
    """
    models, params, report, warnings = {}, {}, [], []
    used: dict[str, int] = {}
    for nid, v in sorted(graph.items(), key=lambda kv: kv[0]):
        ctype = v["class_type"]
        for field, (kind, meta) in widget_specs(object_info, ctype).items():
            if kind != "COMBO" or not looks_like_models(meta.get("choices") or []):
                continue
            value = (v.get("inputs") or {}).get(field)
            if J.is_link(value) or not isinstance(value, str) or not value:
                continue
            folder = folder_of(model_list, meta.get("choices") or [], value)
            param = (MODEL_PARAMS.get(ctype) or _param_by_name(ctype)
                     or FOLDER_PARAMS.get(folder) or re.sub(r"_name$", "", field) or "model")
            feeds = _feeds(graph, nid) or _feeds_any(graph, nid)
            if param == "model" and len(nodes_of(graph, ctype)) > 1:
                # two model loaders: a two-stage graph (Wan 2.2 14B's high and
                # low noise experts). Their titles say which is which.
                title = title_of(graph, nid).lower()
                if "low" in title:
                    param = "model_low"
                elif "high" not in title and used.get("model"):
                    param = "model_2"
            if param == "vae" and len(nodes_of(graph, ctype)) > 1 and feeds:
                # a graph with a video VAE and an audio VAE: name each for the
                # decode it feeds, and select it by that (as ltx2 / H3 do)
                param = "audio_vae" if "audio" in feeds[0].lower() else "video_vae"
            n = used.get(param, 0) + 1
            used[param] = n
            name = param if n == 1 else f"{param}_{n}"
            family = family_of(value, extra)
            entry = {"family": family or f"{name}-of-{value.rsplit('.', 1)[0][:40]}",
                     "patterns": [_pattern(value)], "tier": "required",
                     "class_type": ctype, "field": field}
            if folder:
                entry["folder"] = folder
            if name == "loras":
                entry["tier"] = "accelerator"
            models[name] = entry
            spec = _spec(graph, nid, field, nodes_of(graph, ctype))
            if len(nodes_of(graph, ctype)) > 1 and "title" not in spec and _feeds(graph, nid):
                spec["feeds"] = list(_feeds(graph, nid))
            params.setdefault(name, spec)
            report.append({"param": name, "file": value, "family": family,
                           "folder": folder, "class_type": ctype, "field": field,
                           "label": TG.modelid.family_label(family) if family else ""})
            if not family:
                warnings.append(f"{name}: {value} isn't a family h3pipe knows, so a substitute "
                                f"can't be checked. It will be required exactly as named.")
            if not folder:
                warnings.append(f"{name}: couldn't tell which models folder {value} is in "
                                f"(readiness can't say where to put it).")
    return models, params, report, warnings


def known_families(extra: dict | None = None) -> dict:
    """{family: [name globs]} h3pipe already knows: every model param of every
    shipped target, plus a series config's own `model_families`. This is the
    same data the pipeline checks installed files against, so a proposal names
    families the rest of the code understands."""
    names: dict[str, list] = {}
    for t in TG.list_targets():
        for spec in t.models.values():
            fam, pats = spec.get("family"), list(spec.get("patterns") or [])
            if fam and pats:
                names.setdefault(fam, [])
                names[fam] += [p for p in pats if p not in names[fam]]
    for fam, pats in (extra or {}).items():
        names.setdefault(fam, [])
        names[fam] += [p for p in (pats if isinstance(pats, list) else [pats])
                       if p not in names[fam]]
    return names


def family_of(value: str, extra: dict | None = None) -> str:
    """The family a file name points at, when exactly one of the known families
    claims it ("" otherwise: it will be required exactly as named)."""
    hits = TG.modelid.name_family(value, known_families(extra))
    return hits[0] if len(hits) == 1 else ""


# the models folder a widget lists says what the param is, whatever the class
FOLDER_PARAMS = {"diffusion_models": "model", "unet": "model", "checkpoints": "model",
                 "text_encoders": "text_encoder", "clip": "text_encoder",
                 "clip_vision": "clip_vision", "vae": "vae", "loras": "loras",
                 "upscale_models": "upscaler", "controlnet": "controlnet",
                 "model_patches": "model_patch", "style_models": "style_model"}


# what a loader class's name says it loads, when the class isn't in the table
CLASS_HINTS = (("upscal", "upscaler"), ("textencoder", "text_encoder"), ("clip", "text_encoder"),
               ("audiovae", "audio_vae"), ("vae", "vae"), ("lora", "loras"))


def _param_by_name(ctype: str) -> str:
    low = ctype.lower().replace("_", "")
    for hint, param in CLASS_HINTS:
        if hint in low:
            return param
    return ""


def group_shared(models: dict, params: dict, report: list) -> tuple[dict, dict]:
    """One file loaded by several nodes is one param, not several: LTX 2.3 puts
    the same checkpoint in its transformer, VAE and text-encoder loaders, and
    the shipped target binds all three to `model`. Params whose file and family
    are the same are merged, keeping the first name and listing every widget."""
    by_file: dict[tuple, list] = {}
    for m in report:
        by_file.setdefault((m["file"], m["family"]), []).append(m["param"])
    for (_file, _fam), names in by_file.items():
        if len(names) < 2:
            continue
        keep, rest = names[0], names[1:]
        specs = [params[n] for n in names if n in params]
        if len(specs) < 2:
            continue
        params[keep] = specs
        for n in rest:
            params.pop(n, None)
            models.pop(n, None)
        for m in report:
            if m["param"] in rest:
                m["param"] = keep
                m["merged"] = True
    return models, params


def _feeds_any(graph: dict, nid: str) -> list:
    """[class, input] of one thing this node feeds, when several do: enough to
    tell a video VAE from an audio one."""
    fed = consumers(graph, nid)
    audio = [(n, name) for n, name in fed if "audio" in graph[n]["class_type"].lower()]
    pick = (audio or fed or [None])[0]
    return [graph[pick[0]]["class_type"], pick[1]] if pick else []


def _feeds(graph: dict, nid: str) -> list:
    """[class, input] of what this node's output feeds, when exactly one node
    takes it: the `feeds` selector that tells two loaders of a class apart."""
    fed = consumers(graph, nid)
    if len(fed) != 1:
        return []
    node, name = fed[0]
    return [graph[node]["class_type"], name]


def _pattern(value: str) -> str:
    """A glob for files like this one: its stem, wildcarded at the precision and
    the extension."""
    stem = re.sub(r"\.(safetensors|ckpt|pt|pth|sft|gguf|bin)$", "", value, flags=re.I)
    stem = re.sub(r"(fp8|fp16|bf16|int8|nvfp4|e4m3fn|scaled|awq)([_\-.]|$)", "*", stem,
                  flags=re.I)
    stem = re.sub(r"\*{2,}", "*", stem)
    return stem if stem.endswith("*") else stem + "*"


# ---------------------------------------------------------------------------
# the rest of target.json
# ---------------------------------------------------------------------------

def detect_template(object_info: dict, graph: dict, params: dict) -> tuple[dict, list]:
    """Legal sizes and lengths, from the widgets' own bounds. `frames.max` is
    never in a graph: the UI's maximum is a UI maximum."""
    warnings, tpl = [], {}
    def meta(param):
        spec = params.get(param)
        if not spec:
            return {}
        ids = nodes_of(graph, spec["class_type"])
        if not ids:
            return {}
        return widget_specs(object_info, spec["class_type"]).get(spec["field"], ("", {}))[1]

    w, h = meta("width"), meta("length")
    step = int(w.get("step") or 0)
    tpl["size_multiple"] = step if step > 1 else 16
    if step <= 1:
        warnings.append("size_multiple: the width widget declares no step, so 16 is assumed. "
                        "Check what sizes the model accepts.")
    tpl["size_fit"] = "snap"
    fstep = int(h.get("step") or 0) or 4
    default = int(h.get("default") or 0)
    base = (default % fstep) or fstep
    umax = int(h.get("max") or 0)
    tpl["frames"] = {"step": fstep, "base": base,
                     "max": umax if 0 < umax < 10000 else max(default * 3, fstep * 4 + base)}
    if not h:
        warnings.append("frames: no length widget was found, so the frame grid is a guess "
                        "(step 4, base 1). A wrong grid renders, but stutters: check it "
                        "against the model's own examples.")
    else:
        warnings.append(f"frames: step {fstep} and base {base} come from the length widget's "
                        f"own step and default ({default}); `max` "
                        f"({tpl['frames']['max']}) is a guess — no graph states how long the "
                        f"model can go. Probe-render the longest shot you mean to use.")
    return tpl, warnings


def detect_audio(object_info: dict, graph: dict, saver: dict, saver_node: str
                 ) -> tuple[str, list]:
    """Whether this graph makes sound: something has to produce AUDIO and reach
    the node the take is saved from. A CreateVideo with nothing plugged into its
    `audio` input is a mute video, whatever the class could take."""
    audio_nodes = {nid for nid, v in graph.items()
                   if "AUDIO" in (class_info(object_info, v["class_type"]).get("output") or [])}
    if not audio_nodes:
        return "none", []
    seen, queue = set(), [saver_node]
    while queue:
        nid = queue.pop()
        if nid in seen or nid not in graph:
            continue
        seen.add(nid)
        if nid in audio_nodes:
            return "generate", []
        queue += [src for _name, src in links_into(graph, nid)]
    return "none", [f"audio: {len(audio_nodes)} node(s) make sound but none reaches the "
                    f"saver, so the target is declared silent."]


def detect_keyframes(object_info: dict, graph: dict) -> tuple[dict, dict, list]:
    """(recipe bits, binding.inputs, warnings) for a graph with one LoadImage:
    the first keyframe goes in it, and what it feeds is unwired when a shot
    hasn't got one."""
    ids = nodes_of(graph, "LoadImage")
    if len(ids) != 1:
        if len(ids) > 1:
            return {}, {}, [f"keyframes: the graph has {len(ids)} LoadImage nodes, so none is "
                           f"wired up. Name them in binding.inputs by hand."]
        return {}, {}, []
    nid = ids[0]
    fed = [(graph[n]["class_type"], name) for n, name in consumers(graph, nid)]
    spec = {"class_type": "LoadImage", "field": "image"}
    if title_of(graph, nid):
        spec["title"] = title_of(graph, nid)
    if len(fed) == 1:
        spec["disconnect"] = {"class_type": fed[0][0], "input": fed[0][1]}
    recipe = {"keyframes": ["first"], "keyframe_path": "refs/shots/{shot}/{end}.png"}
    note = (f"keyframes: the LoadImage feeds {fed[0][0]}.{fed[0][1]}, so a shot's first "
            f"keyframe goes there and text-to-video unwires it." if len(fed) == 1 else
            "keyframes: the LoadImage feeds several inputs; check binding.inputs.first's "
            "`disconnect`.")
    return recipe, {"first": spec}, [note]


def detect_presets(graph: dict, params: dict, models: dict, fps: float) -> dict:
    """final and proxy, from the values the graph is set to now. The proxy is
    the same at half the long side, snapped to the size multiple."""
    def value(param):
        spec = params.get(param)
        spec = spec[0] if isinstance(spec, list) else spec
        if not isinstance(spec, dict):
            return None
        field = spec.get("field") or spec.get("name")      # a LoRA's file widget
        if not field:
            return None
        ids = nodes_of(graph, spec["class_type"])
        if spec.get("title"):
            ids = [n for n in ids if title_of(graph, n) == spec["title"]]
        if not ids:
            return None
        v = (graph[ids[0]].get("inputs") or {}).get(field)
        return None if J.is_link(v) else v

    final = {}
    for param in ("model", "steps", "cfg", "sampler", "scheduler", "shift", "denoise",
                  "negative", "width", "height"):
        v = value(param)
        if v is not None and v != "":
            final[param] = v
    for name in models:
        if name in ("model", "loras"):
            continue
        v = value(name)
        if v:
            final[name] = v
    lora = value("loras")
    final["lora"] = lora if isinstance(lora, str) and lora else None
    return {"final": final, "proxy": dict(final, **_proxy_size(final))}


def _proxy_size(final: dict) -> dict:
    w, h = int(final.get("width") or 0), int(final.get("height") or 0)
    if not (w and h):
        return {}
    return {"width": max(64, (w // 2) // 16 * 16), "height": max(64, (h // 2) // 16 * 16)}


def detect_downloads(ui_graph: dict | None) -> dict:
    """`downloads` from what a canvas save records about its own files
    (`properties.models`: ComfyUI's templates carry real URLs). Never a guess."""
    out = {}
    for node in ((ui_graph or {}).get("nodes") or []):
        for m in ((node.get("properties") or {}).get("models") or []):
            name, url = m.get("name"), m.get("url")
            if not name or name in out:
                continue
            out[name] = {"folder": m.get("directory") or "", "url": url or None,
                         "source": "the workflow's own properties.models"
                                   if url else "no URL in the workflow; search for its name"}
    return out


# ---------------------------------------------------------------------------
# the proposal
# ---------------------------------------------------------------------------

def inspect_graph(data: dict, object_info: dict, model_list=None, target_id: str = "",
                  label: str = "", workflow_name: str = "", kind: str = KIND,
                  extra: dict | None = None, series_cfg: dict | None = None) -> dict:
    """Propose the target.json for one workflow (`data` a canvas save or an API
    export). See the module docstring for the answer's shape."""
    if kind != KIND:
        raise InspectError(f"only {KIND} targets can be proposed from a workflow so far, "
                           f"not {kind!r}")
    ui = data if ("nodes" in data and "links" in data) else None
    graph = J.graph_from(data, "the workflow")
    problems = J.check_graph(graph, object_info or None)
    saver, saver_node = find_saver(graph, kind)
    params, matched, ambiguous, warnings0 = detect_params(object_info, graph, saver_node)
    pparams, pmatched, warnings = detect_prompt(object_info, graph)
    warnings = warnings0 + warnings
    params.update(pparams)
    matched.update(pmatched)
    models, mparams, report, mwarn = detect_models(object_info, graph, model_list, extra)
    models, mparams = group_shared(models, mparams, report)
    warnings += mwarn
    for name, spec in mparams.items():
        params.setdefault(name, spec)
    matched.update({k: ", ".join(f"{x['class_type']}.{x['field']}"
                                 for x in (v if isinstance(v, list) else [v]))
                    for k, v in mparams.items()})
    if "loras" in models:
        params["loras"] = _lora_spec(graph, params)
    elif "model" in params:
        params["loras"] = {"class_type": "LoraLoaderModelOnly", "name": "lora_name",
                           "strength": "strength_model", "input": "model", "chain": True,
                           "insert_after": {"class_type": params["model"]["class_type"],
                                            "output": 0}}
        warnings.append("loras: the graph has no LoRA loader, so one is inserted after the "
                        "model loader when a shot or profile names a LoRA.")
    tpl, twarn = detect_template(object_info, graph, params)
    warnings += twarn
    audio, awarn = detect_audio(object_info, graph, saver, saver_node)
    warnings += awarn
    kf_recipe, inputs, kwarn = detect_keyframes(object_info, graph)
    warnings += kwarn
    fps = _fps(graph, params, series_cfg)
    params.update(saver_params(object_info, saver["class_type"], params))
    tid = target_id or _id_from(workflow_name) or "my_target"
    binding = {"workflow_name": workflow_name or f"{tid}.json",
               "env": f"H3_{tid.upper()}_WORKFLOW", "saver": saver, "prune": True,
               "params": params}
    if inputs:
        binding["inputs"] = inputs
    recipe = {"prompt": "prose", **kf_recipe}
    if audio == "none":
        recipe["policies"] = ["silent"]
        recipe["policy_fallback"] = {"to": "silent",
                                    "why": "this graph makes no sound (lay it in at the edit)"}
    else:
        recipe["policies"] = ["generate"]
    unknown = {v["class_type"]: str(class_info(object_info, v["class_type"])
                                    .get("python_module") or "unknown")
               for v in graph.values() if not is_core(object_info, v["class_type"])}
    proposal = {
        "id": tid,
        "kind": kind,
        "label": label or tid.replace("_", " "),
        "short": (label or tid).split()[0][:12],
        "draft": True,
        "capabilities": {"audio": audio},
        "models": models,
        "template": dict(tpl, fps=fps, max_size=_max_size(graph, params)),
        "recipe": recipe,
        "binding": binding,
        "presets": detect_presets(graph, params, models, fps),
        "downloads": detect_downloads(ui),
    }
    if unknown:
        proposal["nodes"] = {c: {"tier": "required"} for c in sorted(unknown)
                             if c != saver["class_type"]}
    unbound = unbound_widgets(object_info, graph, params, saver_node)
    if unbound:
        warnings.append("these widgets keep the graph's own value for every shot: "
                        + ", ".join(sorted({f"{u['class_type']}.{u['field']}" for u in unbound}))
                        + ". Bind one in binding.params if a shot should set it.")
    return {"proposal": proposal, "matched": matched, "ambiguous": ambiguous,
            "warnings": warnings, "models": report, "nodes": unknown,
            "unbound": unbound, "problems": problems}


SAVER_PARAMS = ("fps", "shot_id", "audio_policy")


def saver_params(object_info: dict, saver_class: str, found: dict) -> dict:
    """The params h3pipe's own saver takes (fps, shot_id, audio_policy): every
    take records them, so they are bound whenever the saver has them and the
    graph hasn't already given them somewhere better."""
    specs = widget_specs(object_info, saver_class)
    out = {}
    for name in SAVER_PARAMS:
        if name in specs and name not in found:
            out[name] = {"class_type": saver_class, "field": name}
    return out


def unbound_widgets(object_info: dict, graph: dict, params: dict, saver_node: str) -> list:
    """The numeric and enum widgets no param covers: they keep whatever the
    graph is set to, for every shot. Most are meant to (a VACE strength, a
    shift, a CFG the author has tuned); the list is there so nothing is a
    surprise later."""
    bound = set()
    for spec in params.values():
        for one_ in (spec if isinstance(spec, list) else [spec]):
            if isinstance(one_, dict) and one_.get("class_type"):
                bound.add((one_["class_type"], one_.get("field") or one_.get("name")))
    out = []
    for nid, v in graph.items():
        if nid == saver_node:
            continue
        for field, (kind, meta) in widget_specs(object_info, v["class_type"]).items():
            if kind not in ("INT", "FLOAT", "COMBO") or (v["class_type"], field) in bound:
                continue
            value = (v.get("inputs") or {}).get(field)
            if J.is_link(value) or value is None or looks_like_models(meta.get("choices") or []):
                continue
            out.append({"class_type": v["class_type"], "field": field, "value": value,
                        "title": title_of(graph, nid)})
    return out


def _lora_spec(graph: dict, params: dict) -> dict:
    ids = nodes_of(graph, params["loras"]["class_type"]) if "loras" in params else []
    ctype = params["loras"]["class_type"] if ids else "LoraLoaderModelOnly"
    spec = {"class_type": ctype, "name": "lora_name", "strength": "strength_model",
            "input": "model", "chain": True}
    if len(ids) > 1 and title_of(graph, ids[0]):
        spec["title"] = title_of(graph, ids[0])
    return spec


def _fps(graph: dict, params: dict, series_cfg: dict | None) -> float | str:
    spec = params.get("fps")
    if spec:
        for nid in nodes_of(graph, spec["class_type"]):
            v = (graph[nid].get("inputs") or {}).get(spec["field"])
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                return float(v)
    return "series"


def _max_size(graph: dict, params: dict) -> dict:
    w = h = 0
    for param, keep in (("width", "w"), ("height", "h")):
        spec = params.get(param)
        if not spec:
            continue
        for nid in nodes_of(graph, spec["class_type"]):
            v = (graph[nid].get("inputs") or {}).get(spec["field"])
            if isinstance(v, int):
                if keep == "w":
                    w = max(w, v)
                else:
                    h = max(h, v)
    if not (w and h):
        return {}
    return {"long_side": max(w, h), "pixels": w * h}


def _id_from(workflow_name: str) -> str:
    stem = os.path.splitext(os.path.basename(workflow_name or ""))[0]
    stem = re.sub(r"[^A-Za-z0-9]+", "_", stem).strip("_").lower()
    stem = re.sub(r"^h3pipe_", "", stem)
    return stem[:48]


# ---------------------------------------------------------------------------
# saving a custom target
# ---------------------------------------------------------------------------

REQUIRED_KEYS = ("id", "kind", "binding")
ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,47}$")


def target_path(root: str, target_id: str) -> str:
    """Where a show's own target lives: <show>/targets/<id>/target.json."""
    return os.path.join(TG.show_folder(root), TG.CUSTOM_DIR, target_id, "target.json")


def validate_spec(spec: dict, graph: dict | None = None,
                  object_info: dict | None = None) -> list[str]:
    """What is wrong with a target.json before it is saved ([]: nothing).

    The id and kind are checked, then every binding param is resolved against
    the graph the target names — the same way a render resolves it
    (h3jobs.select_nodes), so a spec that would fail at queue time fails here
    instead.
    """
    out = []
    for key in REQUIRED_KEYS:
        if not spec.get(key):
            out.append(f"target.json needs a {key}")
    tid = spec.get("id") or ""
    if tid and not ID_RE.match(tid):
        out.append(f"id {tid!r} must be lowercase letters, digits and underscores, "
                   f"starting with a letter")
    if spec.get("kind") and spec["kind"] != KIND:
        out.append(f"kind must be {KIND!r} for a target made from a workflow, "
                   f"not {spec['kind']!r}")
    if tid in {t.id for t in TG.list_targets()}:
        out.append(f"{tid!r} is a built-in target's name: choose another")
    b = spec.get("binding") or {}
    if not b.get("workflow_name"):
        out.append("binding.workflow_name must name the workflow (as saved in ComfyUI)")
    if not (b.get("saver") or {}).get("class_type"):
        out.append("binding.saver.class_type must name the node that saves the take")
    tpl = (spec.get("template") or {}).get("frames") or {}
    if tpl and int(tpl.get("step", 1)) < 1:
        out.append("template.frames.step must be 1 or more")
    if graph is None:
        return out
    fake = TG.Target(os.path.join(TG.HERE, "video", "_proposal"), dict(spec, id=tid or "x"))
    # check the graph as a job leaves it: the saver takes its node's place first
    # (h3jobs.prepare_saver), so params on the saver itself resolve
    prepared = copy.deepcopy(graph)
    try:
        J.prepare_saver(prepared, fake.binding)
    except Exception as e:
        out.append(f"binding.saver: {e}")
    for name in fake.binding.params:
        for one_ in fake.binding.specs(name):
            if not (one_.get("class_type") and one_.get("field")):
                continue
            try:
                J.select_nodes(prepared, one_)
            except ValueError as e:
                out.append(f"binding.params.{name}: {e}")
    if object_info:                                       # empty: ComfyUI didn't say
        unknown = sorted({v["class_type"] for v in graph.values()
                          if v["class_type"] not in object_info})
        out += [f"the workflow uses {c}, which this ComfyUI hasn't got" for c in unknown]
    return out


def save_spec(root: str, spec: dict) -> str:
    """Write a show's own target and forget the cached targets. Returns the
    path. The caller validates first (validate_spec)."""
    path = target_path(root, spec["id"])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(spec, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)
    TG.forget_targets()
    return path


def delete_spec(root: str, target_id: str) -> bool:
    """Remove a show's own target (its folder, if nothing else is in it).
    False when there was none."""
    path = target_path(root, target_id)
    if not os.path.isfile(path):
        return False
    os.remove(path)
    folder = os.path.dirname(path)
    try:
        if not os.listdir(folder):
            os.rmdir(folder)
    except OSError:
        pass
    TG.forget_targets()
    return True


def custom_targets(root: str) -> list[dict]:
    """A show's own targets, as the editor lists them: {id, label, draft, path}."""
    out = []
    for t in TG.list_targets(root=root):
        if not t.custom:
            continue
        out.append({"id": t.id, "kind": t.kind, "label": t.label, "short": t.short,
                    "draft": bool(t.spec.get("draft")),
                    "workflow": t.binding.workflow_name,
                    "path": os.path.join(t.folder, "target.json")})
    return out
