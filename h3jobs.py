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

LoRAs are a list of {"name", "strength"}. The first goes into the workflow's
LoraLoaderModelOnly; the rest are chained after it by adding nodes to the API
graph, so no workflow needs a fixed number of LoRA slots. An empty list keeps
the loader wired at strength 0, which renders on the base model. The shotlist's
single `lora` (the turbo LoRA; "none" means no LoRA) is the default list.

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

LOADER = "H3ShotListLoader"
SAVER = "H3SaveShot"
LORA = "LoraLoaderModelOnly"
UNET = "UNETLoader"
WORKFLOW_NAME = "H3_Ref2VA_Shotlist_v1.json"
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
    # $H3_WORKFLOW wins, then beside this file, then $COMFYUI_PATH's workflows folder
    cands = [os.environ.get("H3_WORKFLOW", ""),
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
    None); $COMFYUI_PATH's workflows folder; the copy in this repo (workflows/)
    or beside it. `prefer_repo` puts the repo copy ahead of $COMFYUI_PATH, for
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
    cands = repo + saved if prefer_repo else saved + repo
    for cand in cands:
        if os.path.isfile(cand):
            cand = os.path.normpath(cand)
            return load_graph(cand), cand
    if not required:
        return None, ""
    raise FileNotFoundError(f"{name} not found in ComfyUI's saved workflows or beside "
                            f"this script — pass --workflow, or set {env}")


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


def story_hash(shot: dict) -> str:
    return T.content_hash({k: v for k, v in shot.items() if k not in PRESET_KEYS})


def preset_hash(doc: dict, shot: dict) -> str:
    d = doc.get("defaults", {})
    return T.content_hash({"defaults": {k: d.get(k) for k in PRESET_DEFAULTS},
                           "shot": {k: shot.get(k) for k in PRESET_KEYS}})


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

    @property
    def id(self) -> str:
        return self.shot["id"]

    @property
    def frames(self) -> int:
        return int(self.shot.get("length", 0))

    @property
    def runs(self) -> bool:
        return self.action not in ("skip", "busy")


def plan_job(root: str, pass_: str, doc: dict, index: int, req: RenderRequest,
             overrides: dict | None = None, folder: str | None = None,
             rng: random.Random | None = None) -> Job:
    shot = doc["shots"][index]
    sid = shot["id"]
    dflt = doc.get("defaults", {})
    ov = T.shot_override(overrides or {}, sid, pass_)
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

    model = pick("model", shot.get("model") or dflt.get("model", ""), req.model)
    built_lora = shot.get("lora") or dflt.get("lora", "")
    loras = pick("loras", parse_lora(built_lora) if built_lora else None, req.loras)
    steps = int(pick("steps", int(shot.get("steps", dflt.get("steps", 4))), req.steps))
    prompt = pick("prompt", shot.get("prompt", ""), req.prompt)

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
    return Job(root=root, pass_=pass_, index=index, shot=shot, doc=doc, folder=folder,
               action=action, take=take, seed=seed, seed_source=source, model=model,
               loras=None if loras is None else [dict(l) for l in loras],
               steps=steps, prompt=prompt, forced=bool(req.take),
               overridden=sorted(set(overridden)),
               override_stale=bool(base_hash) and base_hash != shot_hash,
               shot_hash=shot_hash, parent_take=req.parent_take, note=req.note)


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
    shot = copy.deepcopy(job.shot)
    shot["seed"] = job.seed
    shot["steps"] = job.steps
    shot["prompt"] = copy.deepcopy(job.prompt)
    if job.model:
        shot["model"] = job.model
    if job.loras is not None:
        shot["loras"] = copy.deepcopy(job.loras)
        shot.pop("lora", None)
    doc = {k: copy.deepcopy(v) for k, v in job.doc.items() if k != "shots"}
    doc["shots"] = [shot]
    return doc


def ref_files(job: Job) -> list[dict]:
    """Every reference file the loader will read, with its sha1."""
    shot, book = job.shot, job.doc.get("subjects", {})
    out = []
    for i, sid in enumerate((shot.get("subjects") or [])[:3], start=1):
        out.append({"slot": f"Picture {i}", "subject": sid,
                    "path": book.get(sid, {}).get("sheet", "")})
    out.append({"slot": "Picture 4", "path": shot.get("background", "")})
    policy = shot.get("audio_policy", "")
    if policy == "clone":
        for i, r in enumerate(shot.get("voice_refs") or [], start=1):
            out.append({"slot": f"Audio {i}", "subject": r.get("subject", ""),
                        "path": r.get("sample", "")})
    elif policy in ("dub", "dub_keep_foley"):
        out.append({"slot": "Audio 1", "path": shot.get("audio_file")
                    or job.doc.get("defaults", {}).get("master_track", "")})
    for r in out:
        p = r["path"]
        r["sha1"] = T.file_sha1(p if os.path.isabs(p) else os.path.join(job.root, p)) if p else None
    return out


def sidecar_for(job: Job) -> dict:
    d = job.doc.get("defaults", {})
    return {
        "target": T.DEFAULT_TARGET, "status": "queued", "queued": T.now(),
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

def apply_loras(g: dict, loras: list[dict]) -> None:
    """Put `loras` into the graph: the first in the workflow's LoRA loader, the
    rest chained after it. No LoRAs: the loader stays wired at strength 0."""
    ids = [k for k, v in g.items() if v["class_type"] == LORA]
    if len(ids) != 1:
        if loras or ids:
            raise ValueError(f"LoRAs need exactly one {LORA} node in the workflow "
                             f"(found {len(ids)})")
        return
    first = ids[0]
    if not loras:
        g[first]["inputs"]["strength_model"] = 0.0
        return
    g[first]["inputs"]["lora_name"] = loras[0]["name"]
    g[first]["inputs"]["strength_model"] = float(loras[0].get("strength", 1.0))
    consumers = [(k, name) for k, v in g.items() for name, val in v["inputs"].items()
                 if val == [first, 0]]
    prev = first
    next_id = max(int(k) for k in g if k.isdigit()) + 1 if any(k.isdigit() for k in g) else 1000
    for i, lo in enumerate(loras[1:], start=2):
        nid = str(next_id)
        next_id += 1
        g[nid] = {"class_type": LORA,
                  "inputs": {"model": [prev, 0], "lora_name": lo["name"],
                             "strength_model": float(lo.get("strength", 1.0))},
                  "_meta": {"title": f"LoRA {i} (h3jobs)"}}
        prev = nid
    for k, name in consumers:
        g[k]["inputs"][name] = [prev, 0]


def graph_for(base: dict, job: Job, take: T.Take, *, panel_mode: str | None = None,
              save_frames: bool | None = None, review_copy: bool = True,
              strip_meta: bool = False) -> dict:
    g = copy.deepcopy(base)
    loader, saver = node_of(g, LOADER), node_of(g, SAVER)
    li = g[loader]["inputs"]
    li["project_root"] = job.root
    li["shotlist_file"] = os.path.relpath(take.paths.shotlist, job.root)
    li["index"] = 0
    if panel_mode:
        li["panel_mode"] = panel_mode
    if job.model:
        g[node_of(g, UNET)]["inputs"]["unet_name"] = job.model
    if job.loras is not None:
        apply_loras(g, job.loras)
    si = g[saver]["inputs"]
    si["project_root"] = job.root
    si["subfolder"] = os.path.relpath(os.path.dirname(take.paths.dir), job.root)
    si["take"] = take.take
    si["sidecar"] = os.path.relpath(take.paths.sidecar, job.root)
    if save_frames is not None:
        si["save_frames"] = save_frames
    if not review_copy:
        for k in [k for k, v in g.items() if v["class_type"] in ("SaveVideo", "CreateVideo")]:
            del g[k]
    if strip_meta:
        for v in g.values():
            v.pop("_meta", None)
    return g
