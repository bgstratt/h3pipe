#!/usr/bin/env python3
"""
h3jobs.py — turn "render this shot" into a queued ComfyUI job with a take record.

Shared by h3render (the CLI) and, later, the ComfyUI routes behind the editor.
The flow for one shot:

    doc  = load_shotlist(root, pass_)                  # the built shotlist
    job  = plan_job(root, pass_, doc, index, request, overrides)
    take = start_job(job)          # reserve the take, write its frozen shotlist + sidecar
    graph = graph_for(base, job, take)                 # patch the workflow
    pid  = comfy.queue(graph);  mark_queued(take, pid)
    ... wait ...;  finish_job(take)                    # only if the saver didn't

What a job renders is decided here, in this order (later wins):

    built shotlist entry  ->  overrides.json  ->  the request (CLI flags / redo dialog)

Seeds: a shot's first take uses the built seed (`stable_seed`, from the ids).
A redo gets a NEW seed unless asked for the same one. A seed pinned in
overrides.json, or typed in the request, beats both. The seed actually used is
recorded in the sidecar with where it came from (`seed_source`).

Which workflow, loader, saver and widgets a job uses is the shot's target's
`binding` (targets/<kind>/<id>/target.json): a built shotlist belongs to one
video target (`shotlist_target`, the default one until Phase 8 writes
per-target shotlists), and the frozen shotlist and the sidecar record it.

LoRAs are a list of {"name", "strength"}. The first goes into the workflow's
LoRA loader (the binding's `loras` class, LoraLoaderModelOnly for H3); the rest
are chained after it by adding nodes to the API graph, so no workflow needs a
fixed number of LoRA slots. An empty list keeps the loader wired at strength 0,
which renders on the base model. The shotlist's `loras` (a profile's list) or
single `lora` (the turbo LoRA; "none" means no LoRA) is the default list.

Rendering anyway (a shot whose reference files are missing, allowed by the
request): a target that can (`compile_without`, H3 does) recompiles the shot
from shotlist/shots.json and the series config as if those refs didn't exist,
so the prompt never names them; the frozen shotlist carries that entry. Any
other target, or a build that is out of date, falls back to the loader's grey
stand-ins with the built prompt. The sidecar's `missing_mode` says which.

Stdlib only.
"""
from __future__ import annotations

import copy
import json
import os
import random
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

import h3takes as T
import targets as TG

# The default video target's binding, under the names h3render and the routes
# have always imported. Job code reads the job's own target instead.
_DEFAULT = TG.load_target(TG.DEFAULT_VIDEO_TARGET, "video").binding
LOADER = _DEFAULT.loader_class
SAVER = _DEFAULT.saver_class
LORA = _DEFAULT.param("loras")["class_type"]
UNET = _DEFAULT.param("model")["class_type"]
WORKFLOW_NAME = _DEFAULT.workflow_name
SKIP_UI = {"MarkdownNote", "Note", "H3ShotInfo", "Reroute"}
PRIMITIVES = {"PrimitiveInt", "PrimitiveFloat", "PrimitiveString",
              "PrimitiveBoolean", "PrimitiveNode", "PrimitiveStringMultiline"}
CONTROL_WORDS = {"fixed", "increment", "decrement", "randomize"}
LORA_OFF = {"none", "off", "-", ""}

# New seeds stay below 2**53 so a browser can hold them as a plain number.
# (Built seeds from stable_seed use 63 bits; the editor must treat those as
# strings.)
NEW_SEED_BITS = 53


# ---------------------------------------------------------------------------
# workflow loading
# ---------------------------------------------------------------------------

def find_workflow(explicit: str | None, name: str = WORKFLOW_NAME) -> str:
    if explicit:
        return explicit
    here = os.path.dirname(os.path.abspath(__file__))
    # $H3_WORKFLOW wins, then the target's copy, then beside this file, then
    # $COMFYUI_PATH's workflows folder
    cands = [os.environ.get("H3_WORKFLOW", ""), TG.repo_workflow(name) or "",
             os.path.join(here, "workflows", name),
             os.path.join(here, name),
             os.path.join(here, "..", name)]
    comfy = os.environ.get("COMFYUI_PATH", "")
    if comfy:
        cands.append(os.path.join(comfy, "user", "default", "workflows", name))
    for cand in cands:
        if cand and os.path.isfile(cand):
            return os.path.normpath(cand)
    raise FileNotFoundError(f"{name} not found — pass --workflow, or set "
                            f"H3_WORKFLOW to its path")


def ui_to_api(ui: dict) -> dict:
    """Convert a ComfyUI canvas save into the /prompt API graph.

    Handles what this workflow uses: linked inputs, widget values (including
    the hidden control_after_generate value after seed-style INTs), primitive
    nodes feeding a widget, and note/readout nodes that do no work.
    """
    nodes = {n["id"]: n for n in ui["nodes"]}
    links = {l[0]: (l[1], l[2]) for l in ui["links"]}   # id -> (from_node, from_slot)

    def primitive_value(node):
        vals = node.get("widgets_values") or []
        return vals[0] if vals else None

    api = {}
    for nid, n in nodes.items():
        ctype = n["type"]
        if ctype in SKIP_UI or ctype in PRIMITIVES:
            continue
        mode = n.get("mode", 0)
        if mode == 2:                       # muted
            continue
        if mode == 4:
            raise ValueError(f"node {nid} ({ctype}) is bypassed; the converter does not "
                             "handle bypass — export the workflow as API and use --workflow")

        inputs = {}
        vals = list(n.get("widgets_values") or [])
        if isinstance(n.get("widgets_values"), dict):   # some nodes save a dict
            vals = []
            inputs.update(n["widgets_values"])
        vi = 0
        for inp in n.get("inputs", []):
            name = inp["name"]
            is_widget = "widget" in inp
            val = None
            if is_widget and vi < len(vals):
                val = vals[vi]
                vi += 1
                # seed-like INTs carry a hidden control_after_generate value
                if (vi < len(vals) and isinstance(val, (int, float))
                        and not isinstance(val, bool)
                        and isinstance(vals[vi], str) and vals[vi] in CONTROL_WORDS):
                    vi += 1
            link = inp.get("link")
            if link is not None and link in links:
                src, slot = links[link]
                srcnode = nodes.get(src)
                if srcnode and srcnode["type"] in PRIMITIVES:
                    inputs[name] = primitive_value(srcnode)
                elif srcnode and srcnode["type"] in SKIP_UI:
                    continue
                else:
                    inputs[name] = [str(src), slot]
            elif is_widget:
                inputs[name] = val
        api[str(nid)] = {"class_type": ctype, "inputs": inputs,
                         "_meta": {"title": n.get("title", ctype)}}

    # drop outputs that feed only nodes we removed, nothing else to do
    return api


def load_graph(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    return graph_from(data, path)


def graph_from(data: dict, where: str = "workflow") -> dict:
    """A UI save or an API export, as an API graph."""
    if "nodes" in data and "links" in data:
        return ui_to_api(data)
    if all(isinstance(v, dict) and "class_type" in v for v in data.values()):
        return data
    raise ValueError(f"{where} is neither a ComfyUI workflow save nor an API export")


def resolve_workflow(explicit: str | None, name: str = WORKFLOW_NAME,
                     comfy_url: str | None = None, env: str = "H3_WORKFLOW",
                     required: bool = True,
                     prefer_repo: bool = False) -> tuple[dict | None, str]:
    """(API graph, where it came from) for the workflow called `name`.

    In order: an explicit path; $`env`; the workflow as saved in the RUNNING
    ComfyUI at `comfy_url` (its user workflows, fetched over the API, so it
    always matches that ComfyUI's node versions; skipped when comfy_url is
    None); $COMFYUI_PATH's workflows folder; the copy in this repo (the
    target's workflow.json whose binding names `name`, else workflows/ or
    beside it). `prefer_repo` puts the repo copy ahead of $COMFYUI_PATH, for
    callers whose saved canvas holds experiments that must not leak in (a
    style LoRA on the reference-image graph). With `required=False`, finding
    nothing returns (None, "") instead of raising.
    """
    if explicit:
        return load_graph(explicit), explicit
    envp = os.environ.get(env, "")
    if envp and os.path.isfile(envp):
        return load_graph(envp), envp
    if comfy_url:
        try:
            data = Comfy(comfy_url).userdata(f"workflows/{name}")
        except Exception:
            data = None
        if data is not None:
            where = f"{comfy_url.rstrip('/')} (user workflows/{name})"
            return graph_from(data, where), where
    here = os.path.dirname(os.path.abspath(__file__))
    comfy = os.environ.get("COMFYUI_PATH", "")
    saved = [os.path.join(comfy, "user", "default", "workflows", name)] if comfy else []
    repo = [os.path.join(here, "workflows", name), os.path.join(here, name),
            os.path.join(here, "..", name)]
    mine = TG.repo_workflow(name)
    if mine:
        repo.insert(0, mine)
    cands = repo + saved if prefer_repo else saved + repo
    for cand in cands:
        if os.path.isfile(cand):
            cand = os.path.normpath(cand)
            return load_graph(cand), cand
    if not required:
        return None, ""
    raise FileNotFoundError(f"{name} not found in ComfyUI's saved workflows or beside "
                            f"this script — pass --workflow, or set {env}")


def target_workflow(target: "TG.Target", explicit: str | None = None,
                    comfy_url: str | None = None, required: bool = True,
                    prefer_repo: bool = False) -> tuple[dict | None, str]:
    """resolve_workflow for a target's binding (its saved name and env var)."""
    b = target.binding
    return resolve_workflow(explicit, b.workflow_name, comfy_url, env=b.env or "H3_WORKFLOW",
                            required=required, prefer_repo=prefer_repo)


def node_of(graph: dict, ctype: str) -> str:
    ids = [k for k, v in graph.items() if v["class_type"] == ctype]
    if len(ids) != 1:
        raise ValueError(f"workflow must contain exactly one {ctype} node (found {len(ids)})")
    return ids[0]


# ---------------------------------------------------------------------------
# ComfyUI client
# ---------------------------------------------------------------------------

class Comfy:
    def __init__(self, base: str, client_id: str = "h3render"):
        self.base = base.rstrip("/")
        self.client_id = client_id

    def _json(self, path: str, payload=None, timeout=60):
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(f"{self.base}{path}", data=data,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            raise RuntimeError(f"ComfyUI {e.code} on {path}: {body[:1500]}") from None

    def ping(self):
        self._json("/system_stats", timeout=10)

    def userdata(self, path: str) -> dict | None:
        """A JSON file from ComfyUI's user folder (e.g. workflows/x.json), or None."""
        from urllib.parse import quote
        try:
            return self._json(f"/api/userdata/{quote(path, safe='')}", timeout=10)
        except RuntimeError as e:
            if "ComfyUI 404" in str(e):
                return None
            raise

    def queue(self, graph: dict) -> str:
        r = self._json("/prompt", {"prompt": graph, "client_id": self.client_id})
        if r.get("node_errors"):
            raise RuntimeError(json.dumps(r["node_errors"])[:1500])
        return r["prompt_id"]

    def queue_ids(self) -> tuple[set[str], set[str]]:
        """(running, pending) prompt ids, from one /queue snapshot."""
        q = self._json("/queue", timeout=10)
        return tuple({item[1] for item in q.get(key, []) if len(item) > 1}
                     for key in ("queue_running", "queue_pending"))

    def alive(self) -> set[str]:
        """Prompt ids ComfyUI still has pending or running."""
        running, pending = self.queue_ids()
        return running | pending

    def history(self, pid: str) -> dict | None:
        """ComfyUI's history entry for one prompt, or None if it has none (yet)."""
        return self._json(f"/history/{pid}", timeout=10).get(pid)

    def has_node(self, ctype: str) -> bool:
        """True if the running ComfyUI knows the node class `ctype`."""
        from urllib.parse import quote
        return ctype in (self._json(f"/object_info/{quote(ctype, safe='')}", timeout=30) or {})

    def view(self, img: dict) -> bytes:
        """The bytes of an output image, as /history lists it ({filename,
        subfolder, type})."""
        from urllib.parse import urlencode
        q = urlencode({"filename": img["filename"], "subfolder": img.get("subfolder", ""),
                       "type": img.get("type", "output")})
        with urllib.request.urlopen(f"{self.base}/view?{q}", timeout=120) as r:
            return r.read()

    def delete_queued(self, pids) -> None:
        """Drop pending prompts from the queue (a running one is not affected)."""
        self._json("/queue", {"delete": list(pids)}, timeout=10)

    def wait(self, pid: str, timeout: int) -> dict:
        t0 = time.time()
        while time.time() - t0 < timeout:
            hist = self._json(f"/history/{pid}")
            if pid in hist:
                st = hist[pid].get("status", {})
                if st.get("status_str") == "error":
                    msgs = [m for m in st.get("messages", []) if m and m[0] == "execution_error"]
                    detail = msgs[-1][1].get("exception_message", "") if msgs else json.dumps(st)
                    raise RuntimeError(detail[:800])
                if st.get("completed", True):
                    return hist[pid].get("outputs", {})
            time.sleep(3)
        raise TimeoutError(f"no result after {timeout}s")

    def interrupt(self, prompt_id: str | None = None):
        """Stop the running job (only if it is `prompt_id`, when given)."""
        try:
            self._json("/interrupt", {"prompt_id": prompt_id} if prompt_id else {})
        except Exception:
            pass


def execution_error(entry: dict | None) -> str | None:
    """The exception message of a failed job's /history entry; None if it didn't fail."""
    st = (entry or {}).get("status") or {}
    if st.get("status_str") != "error":
        return None
    msgs = [m for m in st.get("messages", []) if m and m[0] == "execution_error"]
    detail = (msgs[-1][1].get("exception_message", "") if msgs else "") or json.dumps(st)
    return detail.strip()[:800]


def execution_done(entry: dict | None) -> bool:
    """True if a /history entry says the job finished without an error."""
    st = (entry or {}).get("status") or {}
    return st.get("status_str") == "success" and st.get("completed", True)


# ---------------------------------------------------------------------------
# planning
# ---------------------------------------------------------------------------

def shotlist_rel(pass_: str) -> str:
    return os.path.join("shotlist", "shotlist_proxy.json" if pass_ == "proxy"
                        else "shotlist.json")


def shotlist_target(doc: dict) -> "TG.Target":
    """The video target a built shotlist is for. Today's shotlists carry no
    `target` (the goldens predate targets), so that means the default one."""
    return TG.load_target(doc.get("target") or TG.DEFAULT_VIDEO_TARGET, "video")


def load_shotlist(root: str, pass_: str) -> dict:
    path = os.path.join(root, shotlist_rel(pass_))
    if not os.path.isfile(path):
        raise FileNotFoundError(f"{path} not found — run h3build first"
                                + (" with --proxy" if pass_ == "proxy" else ""))
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


# What a shot IS (story, framing, timing, refs, prompt) versus how it is
# rendered (model, LoRA, steps, size). Splitting them lets a take say WHY it is
# stale, and keeps a series-wide steps change from flagging every prompt
# override.
PRESET_KEYS = ("model", "lora", "steps")
PRESET_DEFAULTS = ("model", "lora", "steps", "width", "height")
# Render settings a shot entry carries only when a profile set them. They join
# the preset hash only when present, so the hash of every shot without them is
# what it always was.
PRESET_EXTRA = ("loras", "profile")


def story_hash(shot: dict) -> str:
    return T.content_hash({k: v for k, v in shot.items()
                           if k not in PRESET_KEYS and k not in PRESET_EXTRA})


def preset_hash(doc: dict, shot: dict) -> str:
    d = doc.get("defaults", {})
    part = {k: shot.get(k) for k in PRESET_KEYS}
    part.update({k: shot[k] for k in PRESET_EXTRA if k in shot})
    return T.content_hash({"defaults": {k: d.get(k) for k in PRESET_DEFAULTS},
                           "shot": part})


def stale_reasons(root: str, doc: dict, shot: dict, sidecar: dict | None) -> list[str]:
    """Why a take no longer matches what rendering this shot now would use:
    'script' (the shot changed), 'ref' (a reference file changed), 'preset'
    (model/LoRA/steps/size defaults changed). Empty: current. A take with no
    sidecar has no provenance and is reported as ['unknown']."""
    if not sidecar:
        return ["unknown"]
    out = []
    if sidecar.get("shot_hash") and sidecar["shot_hash"] != story_hash(shot):
        out.append("script")
    for r in sidecar.get("refs") or []:
        p = r.get("path")
        if p and r.get("sha1") and T.file_sha1(
                p if os.path.isabs(p) else os.path.join(root, p)) != r["sha1"]:
            out.append("ref")
            break
    if sidecar.get("preset_hash") and sidecar["preset_hash"] != preset_hash(doc, shot):
        out.append("preset")
    return out


def parse_lora(spec: str) -> list[dict]:
    """`name`, `name:0.7`, or none/off/- (no LoRA) -> a LoRA list."""
    spec = spec.strip()
    if spec.lower() in LORA_OFF:
        return []
    name, _, strength = spec.rpartition(":")
    if name and _is_float(strength):
        return [{"name": name, "strength": float(strength)}]
    return [{"name": spec, "strength": 1.0}]


def _is_float(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


@dataclass
class RenderRequest:
    """What the caller asks for, on top of the shotlist and overrides.json."""
    shot_id: str
    take: int | None = None            # force this number (overwrites it)
    redo: bool = False                 # render again even if a usable take exists
    seed: int | None = None            # typed seed
    seed_mode: str = "auto"            # auto | new | same
    model: str | None = None
    loras: list[dict] | None = None
    steps: int | None = None
    prompt: str | list | None = None
    parent_take: int | None = None
    note: str = ""
    allow_missing_refs: bool = False   # render anyway: blank images / no audio ref


@dataclass
class Job:
    root: str
    pass_: str
    index: int
    shot: dict                         # the built shotlist entry, untouched
    doc: dict                          # the whole built shotlist
    folder: str | None
    action: str                        # render | redo | retry | overwrite | skip | busy
    take: int                          # predicted; reserve_take has the final say
    seed: int
    seed_source: str                   # stable | new | same | override | typed
    model: str                         # "" leaves the workflow's model alone
    loras: list[dict] | None           # None leaves the workflow's LoRA alone
    steps: int
    prompt: str | list
    overridden: list[str] = field(default_factory=list)
    override_stale: bool = False
    shot_hash: str = ""
    parent_take: int | None = None
    note: str = ""
    forced: bool = False               # take number given explicitly
    missing: list[dict] = field(default_factory=list)   # refs not on disk
    allow_missing: bool = False
    target: str = T.DEFAULT_TARGET                      # the video target's id
    recompiled: dict | None = None     # render anyway: the shot compiled without them
    missing_mode: str = ""             # render anyway: "recompiled" | "blank"
    missing_why: str = ""              # why it fell back to "blank"

    @property
    def id(self) -> str:
        return self.shot["id"]

    @property
    def frames(self) -> int:
        return int(self.shot.get("length", 0))

    @property
    def runs(self) -> bool:
        return self.action not in ("skip", "busy", "blocked")

    def missing_note(self) -> str:
        return ", ".join(f"{r['slot']} {r['path'] or '(none named)'}" for r in self.missing)


def plan_job(root: str, pass_: str, doc: dict, index: int, req: RenderRequest,
             overrides: dict | None = None, folder: str | None = None,
             rng: random.Random | None = None) -> Job:
    shot = doc["shots"][index]
    sid = shot["id"]
    dflt = doc.get("defaults", {})
    target = shotlist_target(doc)
    ov = T.shot_override(overrides or {}, sid, pass_, target.id)
    shot_hash = story_hash(shot)

    takes = T.list_takes(root, pass_, sid, folder)
    usable = [t for t in takes if t.usable]
    queued = [t for t in takes if t.status == "queued"]
    last = takes[-1].take if takes else 0
    if req.take:
        take, action = req.take, ("overwrite" if any(t.take == req.take for t in takes)
                                  else "render")
    elif queued and not req.redo:
        take, action = queued[-1].take, "busy"
    elif usable and not req.redo:
        take, action = usable[-1].take, "skip"
    else:
        take = last + 1
        action = "redo" if usable else ("retry" if takes else "render")

    overridden = []

    def pick(name, built, from_req):
        """request beats override beats built"""
        if from_req is not None:
            return from_req
        if name in ov:
            overridden.append(name)
            return ov[name]
        return built

    # A shot missing a reference fails in the loader. Unless the caller says to
    # render anyway, don't queue it at all; rendering anyway, let the target
    # write the shot without those refs when it can.
    missing = missing_refs(root, doc, shot)
    recompiled, missing_mode, missing_why = None, "", ""
    if missing and req.allow_missing_refs and action not in ("skip", "busy"):
        recompiled, missing_why = recompile_without(root, pass_, doc, shot, missing)
        missing_mode = "recompiled" if recompiled is not None else "blank"
    source = recompiled or shot

    model = pick("model", shot.get("model") or dflt.get("model", ""), req.model)
    if "loras" in shot:                                # a profile's list
        built_loras = [dict(lo) for lo in shot["loras"]]
    else:
        built_lora = shot.get("lora") or dflt.get("lora", "")
        built_loras = parse_lora(built_lora) if built_lora else None
    loras = pick("loras", built_loras, req.loras)
    steps = int(pick("steps", int(shot.get("steps", dflt.get("steps", 4))), req.steps))
    prompt = pick("prompt", source.get("prompt", ""), req.prompt)

    # seed: typed > pinned in overrides > (same | new | first take: built)
    built_seed = int(shot.get("seed", 0))
    if req.seed is not None:
        seed, source = int(req.seed), "typed"
    elif "seed" in ov and req.seed_mode != "new":
        seed, source = int(ov["seed"]), "override"
        overridden.append("seed")
    elif req.seed_mode == "same":
        seed, source = built_seed, "same"
    elif req.seed_mode == "new" or (req.seed_mode == "auto" and action == "redo"):
        seed, source = (rng or random.SystemRandom()).getrandbits(NEW_SEED_BITS), "new"
    else:
        seed, source = built_seed, "stable"

    base_hash = ov.get("base_hash")
    if missing and not req.allow_missing_refs and action not in ("skip", "busy"):
        action = "blocked"
    return Job(root=root, pass_=pass_, index=index, shot=shot, doc=doc, folder=folder,
               action=action, take=take, seed=seed, seed_source=source, model=model,
               loras=None if loras is None else [dict(l) for l in loras],
               steps=steps, prompt=prompt, forced=bool(req.take),
               overridden=sorted(set(overridden)),
               override_stale=bool(base_hash) and base_hash != shot_hash,
               shot_hash=shot_hash, parent_take=req.parent_take, note=req.note,
               missing=missing, allow_missing=req.allow_missing_refs, target=target.id,
               recompiled=recompiled, missing_mode=missing_mode, missing_why=missing_why)


def episode_story(root: str):
    """(the story IR from shotlist/shots.json, the series config) of a built
    episode. The series config is looked up as h3edit does: the episode
    folder, then its parent."""
    from h3core import ir
    from h3core.series_config import load_series_config
    with open(os.path.join(root, "shotlist", "shots.json"), encoding="utf-8") as fh:
        story = ir.Episode.loads(fh.read())
    for d in (root, os.path.dirname(os.path.normpath(root))):
        p = os.path.join(d, "series.json")
        if os.path.isfile(p):
            return story, load_series_config(p)
    raise FileNotFoundError(f"no series.json in {root} or its parent folder")


def recompile_without(root: str, pass_: str, doc: dict, shot: dict,
                      missing: list[dict]) -> tuple[dict | None, str]:
    """(the shot recompiled without the `missing` refs, "") when the shot's
    target can write it that way and the build is current; else (None, why)."""
    t = shotlist_target(doc)
    if not t.supports("compile_without"):
        return None, f"{t.id} can't write a shot without its references"
    try:
        story, series_cfg = episode_story(root)
        return t.compile_without(story, series_cfg, pass_, shot, missing), ""
    except Exception as e:                                # fall back to grey stand-ins
        return None, f"{e.__class__.__name__}: {e}"[:400]


def plan_episode(root: str, pass_: str, reqs: dict[str, RenderRequest] | None = None,
                 default: RenderRequest | None = None, folder: str | None = None,
                 only: set[str] | None = None) -> list[Job]:
    """A Job per shot (or per shot in `only`), in shotlist order."""
    doc = load_shotlist(root, pass_)
    ov = T.load_overrides(root)
    jobs = []
    for i, s in enumerate(doc["shots"]):
        if only and s["id"] not in only:
            continue
        req = (reqs or {}).get(s["id"]) or copy.copy(default or RenderRequest(s["id"]))
        req.shot_id = s["id"]
        jobs.append(plan_job(root, pass_, doc, i, req, ov, folder))
    return jobs


# ---------------------------------------------------------------------------
# starting and finishing
# ---------------------------------------------------------------------------

def frozen_shotlist(job: Job) -> dict:
    """A complete one-shot shotlist: what the loader reads for this take."""
    # rendering anyway, the target's recompile of the shot without the refs
    shot = copy.deepcopy(job.recompiled or job.shot)
    shot["seed"] = job.seed
    shot["steps"] = job.steps
    shot["prompt"] = copy.deepcopy(job.prompt)
    if job.model:
        shot["model"] = job.model
    if job.loras is not None:
        shot["loras"] = copy.deepcopy(job.loras)
        shot.pop("lora", None)
    if job.missing and job.allow_missing:
        # the loader substitutes a blank image for a missing picture and drops
        # a missing audio reference, instead of failing
        shot["missing_refs"] = "blank"
    doc = {k: copy.deepcopy(v) for k, v in job.doc.items() if k != "shots"}
    doc["target"] = job.target
    doc["shots"] = [shot]
    return doc


def ref_slots(doc: dict, shot: dict) -> list[dict]:
    """Every reference the loader will read for `shot`: {slot, kind ("image" |
    "audio"), path, subject?}. Paths as the shotlist writes them (relative to
    the episode unless absolute); an empty path means the series config names none.
    The slots are the target's recipe (H3: Picture 1-3 subjects, Picture 4 the
    plate, Audio 1-3)."""
    return shotlist_target(doc).ref_slots(doc, shot)


def _abs(root: str, p: str) -> str:
    return p if os.path.isabs(p) else os.path.join(root, p)


def missing_refs(root: str, doc: dict, shot: dict) -> list[dict]:
    """The references `shot` needs that aren't on disk (see ref_slots)."""
    return [r for r in ref_slots(doc, shot)
            if not r["path"] or not os.path.isfile(_abs(root, r["path"]))]


def ref_files(job: Job) -> list[dict]:
    """Every reference file the loader will read, with its sha1."""
    out = ref_slots(job.doc, job.shot)
    for r in out:
        p = r["path"]
        r["sha1"] = T.file_sha1(_abs(job.root, p)) if p else None
    return out


def sidecar_for(job: Job) -> dict:
    d = job.doc.get("defaults", {})
    return {
        "target": job.target, "status": "queued", "queued": T.now(),
        "comfy_prompt_id": None,
        "seed": job.seed, "seed_source": job.seed_source,
        "model": job.model, "loras": job.loras, "steps": job.steps,
        "width": int(job.shot.get("width", d.get("width", 0))),
        "height": int(job.shot.get("height", d.get("height", 0))),
        "length": job.frames,
        "shot_hash": job.shot_hash,
        "preset_hash": preset_hash(job.doc, job.shot),
        "overrides": job.overridden, "override_stale": job.override_stale,
        "parent_take": job.parent_take, "note": job.note,
        "refs": ref_files(job),
        "missing_refs": [r["slot"] for r in job.missing] if job.allow_missing else [],
        # only when rendering anyway, so every other sidecar is as it was
        **({"missing_mode": job.missing_mode} if job.missing_mode else {}),
        **({"missing_note": job.missing_why} if job.missing_why else {}),
    }


def start_job(job: Job) -> T.Take:
    """Reserve the take and write its sidecar and frozen shotlist. No ComfyUI yet."""
    take = T.reserve_take(job.root, job.pass_, job.id, sidecar_for(job),
                          take=job.take if job.forced else None, folder=job.folder)
    job.take = take.take
    T.write_json(take.paths.shotlist, frozen_shotlist(job))
    return take


def mark_queued(take: T.Take, prompt_id: str) -> None:
    take.sidecar = T.update_sidecar(take.paths.sidecar, comfy_prompt_id=prompt_id)


def mark_failed(take: T.Take, why: str) -> None:
    take.sidecar = T.update_sidecar(take.paths.sidecar, status="failed",
                                    finished=T.now(), save_notes=why)


def finish_job(take: T.Take) -> str:
    """After ComfyUI reports success: make sure the sidecar says so.

    The saver normally does this itself. A workflow or node pack from before
    sidecars leaves it `queued`, so close it here from what is on disk.
    """
    sc = T.read_sidecar(take.paths.sidecar) or {}
    if sc.get("status") != "queued":
        take.sidecar = sc
        return sc.get("status", "?")
    if os.path.isfile(take.paths.mp4):
        take.sidecar = T.update_sidecar(
            take.paths.sidecar, status="ok", finished=T.now(),
            mp4=os.path.basename(take.paths.mp4),
            thumb=os.path.basename(take.paths.thumb) if os.path.isfile(take.paths.thumb) else None,
            strip=os.path.basename(take.paths.strip) if os.path.isfile(take.paths.strip) else None,
            save_notes="closed by h3jobs: the saver did not update the sidecar")
        return "ok"
    mark_failed(take, "ComfyUI finished but the mp4 is missing")
    return "failed"


# ---------------------------------------------------------------------------
# graph patching
# ---------------------------------------------------------------------------

DEFAULT_LORA_SPEC = {"class_type": LORA, "name": "lora_name", "strength": "strength_model",
                     "input": "model", "chain": True}


def apply_loras(g: dict, loras: list[dict], spec: dict | None = None) -> None:
    """Put `loras` into the graph: the first in the workflow's LoRA loader, the
    rest chained after it. No LoRAs: the loader stays wired at strength 0.

    `spec` is the binding's `loras` param: the loader's class_type, its `name`
    and `strength` widgets, the `input` it chains through (output 0 of one
    feeds that input of the next) and whether `chain` is allowed (without it
    more than one LoRA is an error). Default: the default target's."""
    spec = dict(DEFAULT_LORA_SPEC, **(spec or {}))
    ctype, name_w, str_w, link = (spec["class_type"], spec["name"], spec["strength"],
                                  spec["input"])
    ids = [k for k, v in g.items() if v["class_type"] == ctype]
    if len(ids) != 1:
        if loras or ids:
            raise ValueError(f"LoRAs need exactly one {ctype} node in the workflow "
                             f"(found {len(ids)})")
        return
    first = ids[0]
    if not loras:
        g[first]["inputs"][str_w] = 0.0
        return
    if len(loras) > 1 and not spec.get("chain"):
        raise ValueError(f"this workflow takes one LoRA ({ctype}), not {len(loras)}")
    g[first]["inputs"][name_w] = loras[0]["name"]
    g[first]["inputs"][str_w] = float(loras[0].get("strength", 1.0))
    consumers = [(k, name) for k, v in g.items() for name, val in v["inputs"].items()
                 if val == [first, 0]]
    prev = first
    next_id = max(int(k) for k in g if k.isdigit()) + 1 if any(k.isdigit() for k in g) else 1000
    for i, lo in enumerate(loras[1:], start=2):
        nid = str(next_id)
        next_id += 1
        g[nid] = {"class_type": ctype,
                  "inputs": {link: [prev, 0], name_w: lo["name"],
                             str_w: float(lo.get("strength", 1.0))},
                  "_meta": {"title": f"LoRA {i} (h3jobs)"}}
        prev = nid
    for k, name in consumers:
        g[k]["inputs"][name] = [prev, 0]


def job_target(job: Job) -> "TG.Target":
    return TG.load_target(job.target, "video")


def _set_widget(g: dict, spec: dict | None, value) -> None:
    """Patch a {"class_type", "field"} param; a {"via": "loader"} one (or none)
    needs nothing, the frozen shotlist carries it."""
    if spec and spec.get("class_type") and spec.get("field"):
        g[node_of(g, spec["class_type"])]["inputs"][spec["field"]] = value


def graph_for(base: dict, job: Job, take: T.Take, *, panel_mode: str | None = None,
              save_frames: bool | None = None, review_copy: bool = True,
              strip_meta: bool = False) -> dict:
    """`base` (the target's workflow as an API graph) patched for one take,
    following the job's target's binding: the loader reads the take's frozen
    shotlist, the saver writes into the take, and the model, LoRA, steps and
    seed widgets the binding names get the job's values."""
    b = job_target(job).binding
    g = copy.deepcopy(base)
    loader, saver = node_of(g, b.loader_class), node_of(g, b.saver_class)
    lin = dict({"root": "project_root", "shotlist": "shotlist_file", "index": "index"},
               **(b.loader.get("inputs") or {}))
    li = g[loader]["inputs"]
    li[lin["root"]] = job.root
    li[lin["shotlist"]] = os.path.relpath(take.paths.shotlist, job.root)
    li[lin["index"]] = 0
    if panel_mode:
        li["panel_mode"] = panel_mode
    if job.model:
        _set_widget(g, b.param("model"), job.model)
    if job.loras is not None:
        apply_loras(g, job.loras, b.param("loras"))
    _set_widget(g, b.param("steps"), job.steps)
    _set_widget(g, b.param("seed"), job.seed)
    sin = dict({"root": "project_root", "subfolder": "subfolder", "take": "take",
                "sidecar": "sidecar"}, **(b.saver.get("inputs") or {}))
    si = g[saver]["inputs"]
    si[sin["root"]] = job.root
    si[sin["subfolder"]] = os.path.relpath(os.path.dirname(take.paths.dir), job.root)
    si[sin["take"]] = take.take
    si[sin["sidecar"]] = os.path.relpath(take.paths.sidecar, job.root)
    if save_frames is not None:
        si["save_frames"] = save_frames
    if not review_copy:
        for k in [k for k, v in g.items() if v["class_type"] in b.review_nodes]:
            del g[k]
    if strip_meta:
        for v in g.values():
            v.pop("_meta", None)
    return g
