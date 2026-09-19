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
`binding` (targets/<kind>/<id>/target.json). A built shotlist belongs to one
video target (`shotlist_target`): shotlist.json is the series target's, and an
episode that mixes targets has shotlist.<target>.json beside it
(`load_shotlists`, `find_shot`, `episode_shots` find a shot in whichever holds
it). A shot can render on another target than its build's: overrides.json's
`target` or the request's (`retarget` compiles its IR from shots.json for that
target at queue time). The frozen shotlist and the sidecar record the target.

A target with no loader node (ltx2) gets every value patched into the
workflow's widgets (`graph_for`: prompt, size, length, fps, seeds, ...), its
saver put in place of the workflow's own (`prepare_saver`), its own graph code
run (`patch_graph`: keyframes) and the graph pruned to what the saver needs.
Images it conditions on are uploaded to ComfyUI's input folder first
(`stage_inputs`). Canvas saves are converted here, subgraphs included
(`ui_to_api`); `check_graph` validates a graph against /object_info.

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

Model files (`check_models`, before the take is reserved): each file a job
loads for a param its target declares a family for (target.json `models`) is
checked by name, then by its safetensors header (targets/modelid.py). A file
of another family stops the job (action "mismatch") unless the request says
`allow_model_mismatch`; a fingerprint-only match or an unknown file renders
with a note in the sidecar.

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


class WorkflowError(ValueError):
    """A workflow the converter can't turn into an API graph faithfully. The
    fallback is an API export (Workflow > Export (API)) as the workflow file."""


SUBGRAPH_IN, SUBGRAPH_OUT = -10, -20      # a subgraph's own input / output node ids
_MISSING = object()

# Frontend-only primitives: they hold a value for a widget and never reach the
# server. The rest of PRIMITIVES (PrimitiveInt, ...) are real server nodes.
FRONTEND_PRIMITIVES = {"PrimitiveNode"}


def _link_tuple(l) -> tuple:
    """(id, origin, origin_slot, target, target_slot, type) from a top-level
    link (a list) or a subgraph link (a dict)."""
    if isinstance(l, dict):
        return (l["id"], l["origin_id"], l["origin_slot"], l["target_id"], l["target_slot"],
                l.get("type"))
    l = list(l)
    return tuple(l[:6]) if len(l) >= 6 else tuple(l) + (None,) * (6 - len(l))


def flatten_subgraphs(ui: dict) -> dict:
    """A canvas save with every subgraph instance replaced by the nodes inside
    it, so the rest of the converter sees one flat graph.

    A subgraph is a node whose `type` is the id of an entry in
    `definitions.subgraphs`. Its inner nodes get ids "<instance>:<inner>" (as
    ComfyUI names them), inner links "<instance>:<link>". Each subgraph input
    is wired to whatever feeds the instance's input of the same name. An input
    nothing feeds takes the value the instance shows for it: a promoted widget
    saved in the instance's own `widgets_values`, else (`properties.proxyWidgets`,
    where the instance shows the inner widget itself) the inner node's widget
    value. Each subgraph output becomes the inner node that feeds it. Nested
    subgraphs expand the same way; a muted instance drops out with everything
    it fed. A bypassed instance is refused (WorkflowError)."""
    defs = {sg["id"]: sg for sg in ((ui.get("definitions") or {}).get("subgraphs") or [])}
    if not defs or not any(n.get("type") in defs for n in ui["nodes"]):
        return ui
    nodes = [dict(n) for n in ui["nodes"]]
    links = {}
    for l in ui["links"]:
        t = _link_tuple(l)
        links[t[0]] = list(t)
    guard = 0
    while True:
        inst = next((n for n in nodes if n.get("type") in defs), None)
        if inst is None:
            break
        guard += 1
        if guard > 1000:
            raise WorkflowError("subgraphs nest too deeply (or contain themselves)")
        nodes.remove(inst)
        nodes += _expand_subgraph(inst, defs[inst["type"]], links)
    out = {k: v for k, v in ui.items() if k not in ("nodes", "links", "definitions")}
    out["nodes"] = nodes
    out["links"] = list(links.values())
    return out


def _expand_subgraph(inst: dict, sg: dict, links: dict) -> list[dict]:
    """The inner nodes of one subgraph instance, rewired; `links` is updated in place."""
    x = inst["id"]
    name = sg.get("name") or sg["id"]
    mode = inst.get("mode", 0)
    if mode == 4:
        raise WorkflowError(f"subgraph node {x} ({name}) is bypassed; the converter can't "
                            f"tell what passes through it. Unbypass it, or export the "
                            f"workflow as API")
    if mode == 2:                      # muted: nothing inside runs, nothing comes out
        for k in [k for k, l in links.items() if l[1] == x]:
            del links[k]
        return []
    inner_links = {}
    for l in sg.get("links") or []:
        t = _link_tuple(l)
        inner_links[t[0]] = t
    sg_inputs = sg.get("inputs") or []
    by_name = {i.get("name"): i for i in inst.get("inputs") or []}
    # promoted widget values the instance saved, in the order of its widget inputs
    wv = inst.get("widgets_values")
    shown = {}
    if isinstance(wv, list) and wv:
        widget_inputs = [i for i in inst.get("inputs") or [] if "widget" in i]
        if len(widget_inputs) != len(wv):
            raise WorkflowError(
                f"subgraph node {x} ({name}) saves {len(wv)} widget values for "
                f"{len(widget_inputs)} promoted widgets; re-save it, or export as API")
        shown = {i["name"]: v for i, v in zip(widget_inputs, wv)}

    inner_by_id = {n["id"]: n for n in sg.get("nodes") or []}

    def inner_widget(slot: int):
        """The value an unfed subgraph input shows when the instance keeps no
        value of its own (proxyWidgets): the widget of the first inner widget
        input it feeds. The frontend sends that value to every input it feeds,
        sockets included."""
        for lid, o, os_, t, ts, typ in inner_links.values():
            if o != SUBGRAPH_IN or os_ != slot or t not in inner_by_id:
                continue
            node = inner_by_id[t]
            ins = node.get("inputs") or []
            if ts is not None and ts < len(ins) and "widget" in ins[ts]:
                wv = widget_values(node)
                if ins[ts]["name"] in wv:
                    return wv[ins[ts]["name"]]
        return _MISSING

    def outer_of(slot: int):
        """(outer link id or None, shown value or _MISSING) for subgraph input `slot`."""
        if slot is None or slot >= len(sg_inputs):
            raise WorkflowError(f"subgraph {name}: a link uses input {slot}, which it "
                                f"doesn't have")
        nm = sg_inputs[slot].get("name")
        ii = by_name.get(nm) or {}
        if ii.get("link") is not None:
            return ii["link"], _MISSING
        if nm in shown:
            return None, shown[nm]
        return None, inner_widget(slot)

    out_src = {}                       # subgraph output slot -> (inner origin, slot)
    for lid, o, os_, t, ts, typ in inner_links.values():
        if t == SUBGRAPH_OUT:
            out_src[ts] = (o, os_)
        elif o != SUBGRAPH_IN:
            links[f"{x}:{lid}"] = [f"{x}:{lid}", f"{x}:{o}", os_, f"{x}:{t}", ts, typ]

    inner = []
    for n in sg.get("nodes") or []:
        nn = copy.deepcopy(n)
        nn["id"] = f"{x}:{n['id']}"
        for inp in nn.get("inputs") or []:
            lid = inp.get("link")
            if lid is None:
                continue
            l = inner_links.get(lid)
            if l is None:
                inp["link"] = None
            elif l[1] == SUBGRAPH_IN:
                outer, value = outer_of(l[2])
                inp["link"] = outer
                if outer is None and value is not _MISSING:
                    inp["_value"] = value
            else:
                inp["link"] = f"{x}:{lid}"
        inner.append(nn)

    # what the instance's outputs fed now comes from inside it
    for k, l in list(links.items()):
        if l[1] != x:
            continue
        src = out_src.get(l[2])
        if src is None:
            del links[k]
            continue
        o, os_ = src
        if o == SUBGRAPH_IN:           # an input passed straight through
            outer, _ = outer_of(os_)
            if outer is None or outer not in links:
                del links[k]
                continue
            l[1], l[2] = links[outer][1], links[outer][2]
        else:
            l[1], l[2] = f"{x}:{o}", os_
    return inner


def widget_values(node: dict) -> dict:
    """{input name: saved widget value} of a canvas node, read positionally
    from `widgets_values` (skipping the hidden control_after_generate value
    after seed-style INTs) and overridden by a subgraph's `_value`."""
    raw = node.get("widgets_values")
    if isinstance(raw, dict):
        return dict(raw)
    vals, out, vi = list(raw or []), {}, 0
    for inp in node.get("inputs") or []:
        if "widget" in inp and vi < len(vals):
            val = vals[vi]
            vi += 1
            if (vi < len(vals) and isinstance(val, (int, float)) and not isinstance(val, bool)
                    and isinstance(vals[vi], str) and vals[vi] in CONTROL_WORDS):
                vi += 1
            out[inp["name"]] = val
        if "_value" in inp:
            out[inp["name"]] = inp["_value"]
    return out


def _types_match(a, b) -> bool:
    if not a or not b:
        return False
    if a == "*" or b == "*":
        return True
    return bool(set(str(a).split(",")) & set(str(b).split(",")))


def ui_to_api(ui: dict) -> dict:
    """Convert a ComfyUI canvas save into the /prompt API graph.

    Handles: subgraphs (flattened first, see flatten_subgraphs); linked inputs;
    widget values, including the hidden control_after_generate value after
    seed-style INTs; primitives -- the frontend PrimitiveNode, and the server's
    PrimitiveInt / PrimitiveFloat / PrimitiveBoolean / PrimitiveString[Multiline]:
    feeding a widget, their value is written into it; feeding a socket (a math
    node's input, a switch's on_false) the node is kept; Reroute and bypassed
    nodes are passed through (a bypassed node's output takes its first input of
    the same type); muted and note/readout nodes are dropped, with every input
    they fed. A node it can't convert faithfully raises WorkflowError, whose
    message says why. Nodes keep ComfyUI's ids ("398:384" inside a subgraph).
    """
    ui = flatten_subgraphs(ui)
    nodes = {n["id"]: n for n in ui["nodes"]}
    links = {}
    for l in ui["links"]:
        t = _link_tuple(l)
        links[t[0]] = (t[1], t[2], t[5])            # id -> (from_node, from_slot, type)

    def out_type(node, slot):
        outs = node.get("outputs") or []
        return outs[slot].get("type") if slot is not None and slot < len(outs) else None

    def resolve(link_id, seen=()):
        """What a link carries: ("link", node, slot) | ("value", v) | None."""
        if link_id is None or link_id not in links or link_id in seen:
            return None
        src_id, slot, typ = links[link_id]
        src = nodes.get(src_id)
        if src is None:
            return None
        seen = seen + (link_id,)
        mode = src.get("mode", 0)
        ctype = src["type"]
        if mode == 2:
            return None
        if ctype == "Reroute" or mode == 4:
            want = out_type(src, slot) or typ
            ins = [i for i in src.get("inputs") or [] if "widget" not in i]
            cand = ins if ctype == "Reroute" else [
                i for i in ins if _types_match(i.get("type"), want)]
            if not cand:
                if ctype == "Reroute":
                    return None
                raise WorkflowError(
                    f"node {src_id} ({ctype}) is bypassed and has no {want} input to pass "
                    f"through; unbypass or remove it, or export the workflow as API")
            return resolve(cand[0].get("link"), seen)
        if ctype in SKIP_UI:
            return None
        if ctype in FRONTEND_PRIMITIVES:
            vals = src.get("widgets_values") or []
            return ("value", vals[0] if vals else None)
        return ("link", src_id, slot)

    def is_prim(nid) -> bool:
        return nodes[nid]["type"] in PRIMITIVES and nodes[nid]["type"] not in FRONTEND_PRIMITIVES

    def primitive_value(node, seen=()):
        """A server primitive's value: what feeds its `value` input, else its widget."""
        for inp in node.get("inputs") or []:
            if inp.get("name") != "value":
                continue
            if inp.get("link") is not None and inp["link"] not in seen:
                r = resolve(inp["link"])
                if r is not None:
                    if r[0] == "link" and is_prim(r[1]):
                        return primitive_value(nodes[r[1]], seen + (inp["link"],))
                    return r
            if "_value" in inp:
                return ("value", inp["_value"])
        vals = node.get("widgets_values") or []
        return ("value", vals[0] if isinstance(vals, list) and vals else None)

    def emitted(n) -> bool:
        return (n.get("mode", 0) not in (2, 4) and n["type"] not in SKIP_UI
                and n["type"] not in FRONTEND_PRIMITIVES and n["type"] != "Reroute")

    # A server primitive is kept only when something reads it through a socket.
    keep = set()
    for nid, n in nodes.items():
        if not emitted(n) or n["type"] in PRIMITIVES:
            continue
        for inp in n.get("inputs") or []:
            if "widget" in inp:
                continue
            r = resolve(inp.get("link"))
            if r and r[0] == "link" and is_prim(r[1]):
                keep.add(r[1])

    def value_in(r):
        """An API input value for a resolved source."""
        if r[0] == "value":
            return r[1]
        if is_prim(r[1]) and r[1] not in keep:
            pv = primitive_value(nodes[r[1]])
            return pv[1] if pv[0] == "value" else [str(pv[1]), pv[2]]
        return [str(r[1]), r[2]]

    api = {}
    for nid, n in nodes.items():
        ctype = n["type"]
        if not emitted(n) or (ctype in PRIMITIVES and nid not in keep):
            continue
        inputs = {}
        wv = widget_values(n)
        if isinstance(n.get("widgets_values"), dict):   # some nodes save a dict
            inputs.update(n["widgets_values"])
        for inp in n.get("inputs", []):
            name = inp["name"]
            is_widget = "widget" in inp
            val = wv.get(name)
            link = inp.get("link")
            if link is not None and link in links:
                r = resolve(link)
                if r is not None:
                    inputs[name] = value_in(r)
                elif is_widget and "_value" in inp:
                    inputs[name] = val
            elif is_widget or "_value" in inp:
                inputs[name] = val
        if ctype in PRIMITIVES:                       # a kept server primitive
            inputs = {"value": value_in(primitive_value(n))}
        api[str(nid)] = {"class_type": ctype, "inputs": inputs,
                         "_meta": {"title": n.get("title", ctype)}}
    return api


def check_graph(api: dict, object_info: dict | None = None) -> list[str]:
    """Problems with an API graph (empty: well-formed). Every link must name a
    node in the graph. With ComfyUI's /object_info (or a trimmed copy, {class:
    {"input": {"required": {...}}, "output": [...]}}), every class must be
    known, every linked slot must exist, and every required input must be set
    (a dynamic input like ComfyMathExpression's `values` is set by `values.a`)."""
    out = []
    for nid, node in api.items():
        ctype = node.get("class_type")
        info = (object_info or {}).get(ctype)
        if object_info is not None and info is None:
            out.append(f"{nid}: unknown node class {ctype}")
        have = node.get("inputs") or {}
        for name, v in have.items():
            if (isinstance(v, list) and len(v) == 2 and isinstance(v[0], str)
                    and isinstance(v[1], int) and not isinstance(v[1], bool)):
                src = api.get(v[0])
                if src is None:
                    out.append(f"{nid} ({ctype}).{name}: links to missing node {v[0]}")
                    continue
                sinfo = (object_info or {}).get(src.get("class_type"))
                if sinfo is not None and v[1] >= len(sinfo.get("output") or []):
                    out.append(f"{nid} ({ctype}).{name}: {src['class_type']} has no "
                               f"output {v[1]}")
        if info is not None:
            for req in (info.get("input") or {}).get("required") or {}:
                if req not in have and not any(k.startswith(req + ".") for k in have):
                    out.append(f"{nid} ({ctype}): required input {req!r} is not set")
    return out


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

    def choices(self, ctype: str, field_name: str) -> list[str] | None:
        """The values a combo widget offers in the running ComfyUI (e.g. the
        files ModelPatchLoader can load), or None when the node isn't known."""
        from urllib.parse import quote
        info = (self._json(f"/object_info/{quote(ctype, safe='')}", timeout=30) or {}).get(ctype)
        if info is None:
            return None
        inputs = info.get("input") or {}
        spec = (inputs.get("required") or {}).get(field_name) \
            or (inputs.get("optional") or {}).get(field_name) or []
        if spec and isinstance(spec[0], list):             # [[choices], {...}]
            return [str(c) for c in spec[0]]
        if len(spec) > 1 and isinstance(spec[1], dict):    # ["COMBO", {"options": [...]}]
            return [str(c) for c in spec[1].get("options") or []]
        return []

    def view(self, img: dict) -> bytes:
        """The bytes of an output image, as /history lists it ({filename,
        subfolder, type})."""
        from urllib.parse import urlencode
        q = urlencode({"filename": img["filename"], "subfolder": img.get("subfolder", ""),
                       "type": img.get("type", "output")})
        with urllib.request.urlopen(f"{self.base}/view?{q}", timeout=120) as r:
            return r.read()

    def upload_input(self, path: str, name: str) -> str:
        """Copy a local image into ComfyUI's input folder as `name` (e.g.
        "h3pipe/<sha1>.png"), overwriting; returns the name LoadImage reads.
        POST /upload/image, so it works however ComfyUI was installed, and
        from the routes as well as the CLI."""
        import uuid
        sub, _, fname = name.rpartition("/")
        boundary = f"----h3pipe{uuid.uuid4().hex}"
        with open(path, "rb") as fh:
            data = fh.read()
        parts = []
        for k, v in (("subfolder", sub), ("type", "input"), ("overwrite", "true")):
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n'
                         f'{v}\r\n'.encode())
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="image"; '
                     f'filename="{fname}"\r\nContent-Type: application/octet-stream\r\n\r\n'
                     .encode() + data + b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        req = urllib.request.Request(
            f"{self.base}/upload/image", data=b"".join(parts),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                got = json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"ComfyUI {e.code} on /upload/image: "
                               f"{e.read().decode('utf-8', 'replace')[:400]}") from None
        return "/".join(x for x in (got.get("subfolder") or sub, got.get("name") or fname) if x)

    def object_info(self) -> dict:
        """/object_info: every node class the running ComfyUI knows."""
        return self._json("/object_info", timeout=120)

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

def shotlist_rel(pass_: str, target: str | None = None) -> str:
    """The shotlist of a pass: shotlist.json / shotlist_proxy.json for the
    series target (target None), shotlist.<target>.json /
    shotlist.<target>_proxy.json for the shots of an episode that render on
    another target."""
    sfx = "_proxy" if pass_ == "proxy" else ""
    if target:
        return os.path.join("shotlist", f"shotlist.{target}{sfx}.json")
    return os.path.join("shotlist", f"shotlist{sfx}.json")


def shotlist_target(doc: dict) -> "TG.Target":
    """The video target a built shotlist is for. Today's shotlists carry no
    `target` (the goldens predate targets), so that means the default one."""
    return TG.load_target(doc.get("target") or TG.DEFAULT_VIDEO_TARGET, "video")


def load_shotlist(root: str, pass_: str, target: str | None = None) -> dict:
    """A built shotlist: the series target's (shotlist.json), or with `target`
    that target's own file in an episode that mixes targets."""
    path = os.path.join(root, shotlist_rel(pass_, target))
    if not os.path.isfile(path):
        raise FileNotFoundError(f"{path} not found — run h3build first"
                                + (" with --proxy" if pass_ == "proxy" else ""))
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def extra_shotlist_targets(root: str, pass_: str) -> list[str]:
    """The targets with their own shotlist.<target>[_proxy].json for this pass."""
    d = os.path.join(root, "shotlist")
    try:
        names = sorted(os.listdir(d))
    except OSError:
        return []
    out = []
    for n in names:
        if not (n.startswith("shotlist.") and n.endswith(".json")):
            continue
        t = n[len("shotlist."):-len(".json")]
        if not t:                                         # shotlist.json itself
            continue
        if pass_ == "proxy":
            if t.endswith("_proxy") and t[:-len("_proxy")]:
                out.append(t[:-len("_proxy")])
        elif not t.endswith("_proxy"):
            out.append(t)
    return out


def load_shotlists(root: str, pass_: str) -> list[dict]:
    """Every shotlist of a pass: the series target's first (FileNotFoundError
    if there is none: the episode isn't built), then each other target's."""
    docs = [load_shotlist(root, pass_)]
    for t in extra_shotlist_targets(root, pass_):
        docs.append(load_shotlist(root, pass_, t))
    return docs


def script_order(root: str) -> list[str]:
    """Shot ids in script order, from shotlist/shots.json ([] if it isn't there)."""
    try:
        with open(os.path.join(root, "shotlist", "shots.json"), encoding="utf-8") as fh:
            ep = json.load(fh)
    except (OSError, ValueError):
        return []
    return [s["id"] for sq in ep.get("sequences", []) for s in sq.get("shots", [])]


def episode_shots(root: str, pass_: str,
                  docs: list[dict] | None = None) -> list[tuple[dict, int]]:
    """(shotlist doc, index) of every built shot of a pass, whichever target's
    file holds it, in script order (shots.json; a shot it doesn't list goes
    last, in file order)."""
    docs = load_shotlists(root, pass_) if docs is None else docs
    found = [(d, i) for d in docs for i in range(len(d.get("shots", [])))]
    order = {sid: n for n, sid in enumerate(script_order(root))}
    return sorted(found, key=lambda di: order.get(di[0]["shots"][di[1]]["id"], len(order)))


def find_shot(root: str, pass_: str, shot_id: str,
              docs: list[dict] | None = None) -> tuple[dict, int]:
    """(shotlist doc, index) of `shot_id` in whichever file of the pass holds
    it. KeyError if none does."""
    for d in (load_shotlists(root, pass_) if docs is None else docs):
        for i, s in enumerate(d.get("shots", [])):
            if s["id"] == shot_id:
                return d, i
    raise KeyError(f"{shot_id} is not in {shotlist_rel(pass_).replace(os.sep, '/')}")


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


# Preset defaults of a two-stage target (Wan 2.2 14B): the second model and
# the per-stage LoRA list. In the hash only when the shotlist has them.
PRESET_DEFAULTS_EXTRA = ("model_low", "loras")


def preset_hash(doc: dict, shot: dict) -> str:
    d = doc.get("defaults", {})
    part = {k: shot.get(k) for k in PRESET_KEYS}
    part.update({k: shot[k] for k in PRESET_EXTRA if k in shot})
    dd = {k: d.get(k) for k in PRESET_DEFAULTS}
    dd.update({k: d[k] for k in PRESET_DEFAULTS_EXTRA if k in d})
    return T.content_hash({"defaults": dd, "shot": part})


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
        if not p or not (r.get("sha1") or r.get("optional")):
            continue
        now = T.file_sha1(p if os.path.isabs(p) else os.path.join(root, p))
        # a file that changed; or an optional one (an LTX keyframe) the take
        # rendered without that exists now: a render would use it
        if (r.get("sha1") and now != r["sha1"]) or (r.get("optional") and not r.get("sha1")
                                                    and now):
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
    target: str | None = None          # render on this video target, this run only
    allow_model_mismatch: bool = False  # render even if a model file is another family


@dataclass
class Job:
    root: str
    pass_: str
    index: int
    shot: dict                         # the entry it renders (built, or retargeted)
    doc: dict                          # its shotlist (built, or the retarget's)
    folder: str | None
    action: str                        # render | redo | retry | overwrite | skip | busy | blocked | error | mismatch
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
    built_target: str = ""             # the target the build compiled it for
    notes: list[str] = field(default_factory=list)      # said in the sidecar
    inputs: dict = field(default_factory=dict)          # role -> name ComfyUI loads
    error: str = ""                    # action "error": why it can't be planned
    # files a target made for this take at stage_inputs (the ingredients
    # reference sheet), waiting for start_job to move them into the take:
    # [{"tmp", "suffix", "slot", "kind", "role"}]
    staged: list = field(default_factory=list)
    # how the take's length is decided: "script" (the shotlist's), "estimate"
    # (`dur: model` rendered at the build's estimate) or "predicted" (the
    # model's duration head, `duration_head` the file ModelPatchLoader loads)
    length_source: str = "script"
    duration_head: str = ""
    duration_note: str = ""            # plan_duration's note (replaced on a re-plan)
    # check_models: each model param's check (targets.check_model), and
    # whether the request lets a mismatch render anyway
    allow_model_mismatch: bool = False
    model_checks: list = field(default_factory=list)
    model_notes: list = field(default_factory=list)     # check_models' notes (replaced on a re-check)
    mismatch_from: str = ""            # the action a "mismatch" job had before the check

    @property
    def id(self) -> str:
        return self.shot["id"]

    @property
    def frames(self) -> int:
        return int(self.shot.get("length", 0))

    @property
    def runs(self) -> bool:
        return self.action not in ("skip", "busy", "blocked", "error", "mismatch")

    @property
    def retargeted(self) -> bool:
        return bool(self.built_target) and self.built_target != self.target

    @property
    def width(self) -> int:
        return int(self.shot.get("width", self.doc.get("defaults", {}).get("width", 0)))

    @property
    def height(self) -> int:
        return int(self.shot.get("height", self.doc.get("defaults", {}).get("height", 0)))

    @property
    def fps(self) -> float:
        return float(self.shot.get("fps", self.doc.get("defaults", {}).get("fps", 24)))

    def missing_note(self) -> str:
        return ", ".join(f"{r['slot']} {r['path'] or '(none named)'}" for r in self.missing)

    def mismatch_note(self) -> str:
        """Why check_models stopped the job ("" if it didn't)."""
        return "; ".join(c["message"] for c in self.model_checks if c.get("block"))

    @property
    def no_anyway(self) -> list[dict]:
        """Missing refs the target can't render without, even when asked to
        render anyway (a ref slot with `anyway: false`: Wan 14B I2V's first
        frame). Their `why` says what to do instead."""
        return [r for r in self.missing if r.get("anyway") is False]

    def blocked_reason(self) -> str:
        """Why a blocked job isn't queued, as the CLI and routes say it."""
        hard = self.no_anyway
        if hard:
            return "; ".join(dict.fromkeys(r.get("why") or f"needs {r['slot']}" for r in hard))
        return ("missing refs: " + self.missing_note()
                + " (pass allow_missing_refs: true to render anyway)")


def check_video_target(target_id: str) -> str:
    """`target_id` if it names a video target, else TargetError naming the known ones."""
    TG.load_target(target_id, "video")
    return target_id


def effective_target(overrides: dict | None, shot_id: str, built: str,
                     requested: str | None = None) -> str:
    """The video target a shot's next render uses: the request, then the
    shot's `target` in overrides.json, then what the build compiled it for."""
    return requested or T.shot_target(overrides or {}, shot_id) or built


def plan_job(root: str, pass_: str, doc: dict, index: int, req: RenderRequest,
             overrides: dict | None = None, folder: str | None = None,
             rng: random.Random | None = None) -> Job:
    built = doc["shots"][index]
    sid = built["id"]
    built_target = shotlist_target(doc)
    target_id = effective_target(overrides, sid, built_target.id, req.target)
    shot, sdoc, notes, error = built, doc, [], ""
    if target_id != built_target.id:
        # retarget: this shot's IR compiled for the other target, now
        try:
            sdoc, shot = retarget(root, pass_, doc, built, target_id)
        except Exception as e:
            error = f"can't render {sid} on {target_id}: {e}"
    dflt = sdoc.get("defaults", {})
    target = TG.load_target(target_id, "video") if not error else built_target
    ov = T.shot_override(overrides or {}, sid, pass_, target.id)
    shot_hash = story_hash(shot)

    takes = T.list_takes(root, pass_, sid, folder)
    # a take made on another target doesn't count as done: retargeting a shot
    # renders it anew (its number still follows the last take)
    here = [t for t in takes
            if ((t.sidecar or {}).get("target") or built_target.id) == target.id]
    usable = [t for t in here if t.usable]
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
        action = "redo" if usable else ("retry" if here else "render")

    overridden = []

    def pick(name, built_value, from_req):
        """request beats override beats built"""
        if from_req is not None:
            return from_req
        if name in ov:
            overridden.append(name)
            return ov[name]
        return built_value

    if target.id != built_target.id and (
            "prompt" in ov
            or "prompt" in T.shot_override(overrides or {}, sid, pass_, built_target.id)):
        # a prompt written for one model isn't a prompt for another: the
        # retargeted shot renders its new target's own prompt
        ov = {k: v for k, v in ov.items() if k != "prompt"}
        notes.append(f"the prompt override was ignored: {sid} is retargeted from "
                     f"{built_target.id} to {target.id}")

    # A shot missing a reference fails in the loader. Unless the caller says to
    # render anyway, don't queue it at all; rendering anyway, let the target
    # write the shot without those refs when it can.
    missing = missing_refs(root, sdoc, shot)
    # a ref the target can't do without (`anyway: false`) blocks even then
    anyway = req.allow_missing_refs and not any(r.get("anyway") is False for r in missing)
    recompiled, missing_mode, missing_why = None, "", ""
    if missing and anyway and action not in ("skip", "busy"):
        recompiled, missing_why = recompile_without(root, pass_, sdoc, shot, missing)
        missing_mode = "recompiled" if recompiled is not None else "blank"
    source = recompiled or shot

    model = pick("model", shot.get("model") or dflt.get("model", ""), req.model)
    if "loras" in shot:                                # a profile's list
        built_loras = [dict(lo) for lo in shot["loras"]]
    else:
        built_lora = shot.get("lora") or dflt.get("lora", "")
        built_loras = parse_lora(built_lora) if built_lora else None
        if built_loras is None and isinstance(dflt.get("loras"), list):
            # a preset that names a LoRA per stage (Wan 2.2 14B's turbo pair)
            built_loras = [dict(lo) for lo in dflt["loras"]]
    loras = pick("loras", built_loras, req.loras)
    steps = int(pick("steps", int(shot.get("steps", dflt.get("steps", 4))), req.steps))
    if steps != int(shot.get("steps", dflt.get("steps", 4))) and not target.binding.specs("steps"):
        notes.append(f"steps {steps} is recorded but not used: {target.short} has no steps "
                     f"setting (its sampling schedule is fixed in the workflow)")
    prompt = pick("prompt", source.get("prompt", ""), req.prompt)
    if shot.get("audio_note"):
        notes.append(shot["audio_note"])
    length_source = "estimate" if shot.get("length_estimated") else "script"
    if shot.get("length_estimated") and not shot.get("duration_predict"):
        notes.append(f"`dur: model` rendered at the estimate, {estimate_seconds(sdoc, shot)} s: "
                     f"{target.short} can't predict a shot's length")

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
    if missing and not anyway and action not in ("skip", "busy"):
        action = "blocked"
    if error and action not in ("skip", "busy"):
        action = "error"
    return Job(root=root, pass_=pass_, index=index, shot=shot, doc=sdoc, folder=folder,
               action=action, take=take, seed=seed, seed_source=source, model=model,
               loras=None if loras is None else [dict(l) for l in loras],
               steps=steps, prompt=prompt, forced=bool(req.take),
               overridden=sorted(set(overridden)),
               override_stale=bool(base_hash) and base_hash != shot_hash,
               shot_hash=shot_hash, parent_take=req.parent_take, note=req.note,
               missing=missing, allow_missing=req.allow_missing_refs, target=target.id,
               recompiled=recompiled, missing_mode=missing_mode, missing_why=missing_why,
               built_target=built_target.id, notes=notes, error=error,
               length_source=length_source, allow_model_mismatch=req.allow_model_mismatch)


def estimate_seconds(doc: dict, shot: dict) -> str:
    """A shot's built length in seconds, as notes print it ("5.04")."""
    fps = float(shot.get("fps", doc.get("defaults", {}).get("fps", 24)) or 24)
    return f"{int(shot.get('length', 0)) / fps:.2f}"


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


def retarget(root: str, pass_: str, doc: dict, shot: dict, target_id: str,
             cache: dict | None = None) -> tuple[dict, dict]:
    """(shotlist doc, entry) of `shot` compiled for `target_id` from
    shotlist/shots.json and the series config, as a build that put the shot on
    that target would write it. The build must be current: the shot's own
    target, compiling the same IR, has to give exactly the built entry, else
    ValueError (rebuild the episode). `cache` (a dict) keeps the result per
    (pass, shot, target) for callers that ask about many shots."""
    key = (pass_, shot["id"], target_id)
    if cache is not None and key in cache:
        hit = cache[key]
        if isinstance(hit, Exception):
            raise hit
        return hit
    try:
        new = TG.load_target(target_id, "video")
        story, series_cfg = episode_story(root)
        if not any(s.id == shot["id"] for s in story.shots()):
            raise ValueError(f"{shot['id']} is not in shots.json (rebuild the episode)")
        now, _ = shotlist_target(doc).compile_episode(story, series_cfg, pass_,
                                                      only={shot["id"]})
        if [shot] != now["shots"]:
            raise ValueError(f"the build of {shot['id']} is out of date (rebuild the episode)")
        rdoc, _ = new.compile_episode(story, series_cfg, pass_, only={shot["id"]})
        out = (rdoc, rdoc["shots"][0])
    except Exception as e:
        if cache is not None:
            cache[key] = e
        raise
    if cache is not None:
        cache[key] = out
    return out


def current_entry(root: str, pass_: str, doc: dict, shot: dict, target_id: str | None,
                  cache: dict | None = None) -> tuple[dict, dict] | None:
    """(doc, entry) a render on `target_id` would use now: the built one on
    its own target, else the retarget (None if it can't be compiled)."""
    if not target_id or target_id == shotlist_target(doc).id:
        return doc, shot
    try:
        return retarget(root, pass_, doc, shot, target_id, cache)
    except Exception:
        return None


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
    """A Job per shot (or per shot in `only`), in script order, whichever
    target's shotlist holds it."""
    ov = T.load_overrides(root)
    jobs = []
    for doc, i in episode_shots(root, pass_):
        s = doc["shots"][i]
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
    """The references `shot` needs that aren't on disk (see ref_slots). An
    `optional` one (an LTX keyframe) is never missing: without it the shot
    renders without that conditioning."""
    return [r for r in ref_slots(doc, shot) if not r.get("optional")
            and (not r["path"] or not os.path.isfile(_abs(root, r["path"])))]


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
        # the frame rate the take is rendered and saved at (the target's: Wan
        # 14B renders 16 fps in a 24 fps episode; assemble converts)
        "fps": job.fps,
        # predicted: `length` is the build's estimate, the saver's `frames` the truth
        "length_source": job.length_source,
        "shot_hash": job.shot_hash,
        "preset_hash": preset_hash(job.doc, job.shot),
        "overrides": job.overridden, "override_stale": job.override_stale,
        "parent_take": job.parent_take, "note": job.note,
        "refs": ref_files(job),
        "missing_refs": [r["slot"] for r in job.missing] if job.allow_missing else [],
        # only when rendering anyway, so every other sidecar is as it was
        **({"missing_mode": job.missing_mode} if job.missing_mode else {}),
        **({"missing_note": job.missing_why} if job.missing_why else {}),
        # only when there is something to say, likewise
        **({"built_target": job.built_target} if job.retargeted else {}),
        **({"inputs": dict(job.inputs)} if job.inputs else {}),
        **({"notes": list(job.notes)} if job.notes else {}),
    }


def start_job(job: Job) -> T.Take:
    """Reserve the take and write its sidecar and frozen shotlist. No ComfyUI yet."""
    take = T.reserve_take(job.root, job.pass_, job.id, sidecar_for(job),
                          take=job.take if job.forced else None, folder=job.folder)
    job.take = take.take
    if job.staged:
        # a file the target composed for this take (stage_inputs): into the
        # take as <stem><suffix>, listed in the sidecar's refs with its sha1
        made = []
        for f in job.staged:
            dst = os.path.join(take.paths.dir, take.paths.stem + f["suffix"])
            os.replace(f["tmp"], dst)
            made.append({"slot": f["slot"], "kind": f.get("kind", "image"),
                         "path": os.path.relpath(dst, job.root).replace(os.sep, "/"),
                         "role": f.get("role"), "sha1": T.file_sha1(dst)})
        job.staged = []
        take.sidecar = T.update_sidecar(take.paths.sidecar,
                                        refs=list((take.sidecar or {}).get("refs") or []) + made)
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


def _consumers(g: dict, nid: str, slot: int = 0) -> list[tuple[str, str]]:
    return [(k, name) for k, v in g.items() for name, val in v["inputs"].items()
            if val == [nid, slot]]


def _next_id(g: dict) -> int:
    return max(int(k) for k in g if k.isdigit()) + 1 if any(k.isdigit() for k in g) else 1000


def apply_loras(g: dict, loras: list[dict], spec: dict | None = None) -> None:
    """Put `loras` into the graph: the first in the workflow's LoRA loader, the
    rest chained after it. No LoRAs: the loader stays wired at strength 0.

    `spec` is the binding's `loras` param: the loader's class_type, its `name`
    and `strength` widgets, the `input` it chains through (output 0 of one
    feeds that input of the next) and whether `chain` is allowed (without it
    more than one LoRA is an error). A workflow with no LoRA loader at all
    gets one when the spec says where (`insert_after`: {"class_type",
    "output"}, e.g. the UNETLoader's MODEL): everything that read that output
    reads the last LoRA instead. Default spec: the default target's."""
    spec = dict(DEFAULT_LORA_SPEC, **(spec or {}))
    ctype, name_w, str_w, link = (spec["class_type"], spec["name"], spec["strength"],
                                  spec["input"])
    title = spec.get("title")
    # `title` picks one loader among several (a two-stage graph's per-stage LoRA)
    ids = [k for k, v in g.items() if v["class_type"] == ctype
           and (not title or (v.get("_meta") or {}).get("title") == title)]
    if not ids and loras and spec.get("insert_after"):
        at = spec["insert_after"]
        src = select_nodes(g, {k: v for k, v in at.items() if k in ("class_type", "title")})[0]
        slot = int(at.get("output", 0))
        consumers = _consumers(g, src, slot)
        nid = str(_next_id(g))
        g[nid] = {"class_type": ctype, "inputs": {link: [src, slot], name_w: "", str_w: 1.0},
                  "_meta": {"title": title or "LoRA 1 (h3jobs)"}}
        for k, name in consumers:
            g[k]["inputs"][name] = [nid, 0]
        ids = [nid]
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
    consumers = _consumers(g, first)
    prev = first
    next_id = _next_id(g)
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


def lora_stage(lora: dict) -> str | None:
    """The sampling stage a LoRA belongs to in a two-stage graph: its own
    `stage`, else from its name (Wan 2.2 LoRAs come in `*high_noise*` /
    `*low_noise*` pairs), else None (both stages)."""
    if lora.get("stage"):
        return str(lora["stage"])
    name = str(lora.get("name", "")).lower().replace("-", "_")
    for stage in ("high", "low"):
        if f"{stage}_noise" in name or f"{stage}noise" in name:
            return stage
    return None


def loras_for(loras: list[dict], spec: dict) -> list[dict]:
    """The LoRAs one loader spec takes: all of them, or for a spec with a
    `stage` (a binding with one LoRA chain per sampling stage) those of that
    stage and those of none."""
    stage = spec.get("stage")
    if not stage:
        return list(loras)
    return [lo for lo in loras if lora_stage(lo) in (None, stage)]


def job_target(job: Job) -> "TG.Target":
    return TG.load_target(job.target, "video")


def select_nodes(g: dict, spec: dict) -> list[str]:
    """The nodes a binding widget spec names (see targets.Binding): its
    class_type, narrowed by `title` and `feeds` ([class, input]: the node whose
    output feeds that input of a node of that class). Exactly one, unless
    `all` (then one or more). ValueError otherwise, naming the spec."""
    ctype = spec["class_type"]
    ids = [k for k, v in g.items() if v["class_type"] == ctype]
    if spec.get("title"):
        ids = [k for k in ids if (g[k].get("_meta") or {}).get("title") == spec["title"]]
    if spec.get("feeds"):
        cls, inp = spec["feeds"]
        fed = {v["inputs"].get(inp)[0] for v in g.values()
               if v["class_type"] == cls and isinstance(v["inputs"].get(inp), list)}
        ids = [k for k in ids if k in fed]
    what = ctype + (f" titled {spec['title']!r}" if spec.get("title") else "") + (
        f" feeding {spec['feeds'][0]}.{spec['feeds'][1]}" if spec.get("feeds") else "")
    if spec.get("all"):
        if not ids:
            raise ValueError(f"workflow must contain a {what} node")
        return ids
    if len(ids) != 1:
        raise ValueError(f"workflow must contain exactly one {what} node (found {len(ids)})")
    return ids


def _set_widget(g: dict, spec: dict | None, value) -> None:
    """Patch a {"class_type", "field"} param; a {"via": "loader"} one (or none)
    needs nothing, the frozen shotlist carries it."""
    if spec and spec.get("class_type") and spec.get("field"):
        if spec.get("scale") is not None and isinstance(value, (int, float)) \
                and not isinstance(value, bool):
            value = value * spec["scale"]
            value = int(round(value)) if isinstance(value, float) and value.is_integer() \
                else value
        for nid in select_nodes(g, spec):
            g[nid]["inputs"][spec["field"]] = value


def patch_param(g: dict, binding, name: str, value) -> None:
    """Every widget the binding's param `name` names gets `value`."""
    for spec in binding.specs(name):
        _set_widget(g, spec, value)


def _prompt_text(prompt) -> str:
    return "\n\n".join(prompt) if isinstance(prompt, list) else (prompt or "")


# Params graph_for sets from the job itself; any other param a binding names
# (a text encoder, a VAE, an upscaler) takes the shotlist's `defaults` value.
JOB_PARAMS = ("model", "loras", "steps", "seed")


def job_values(job: Job) -> dict:
    """The values of a job's other binding params (prompt, negative, size,
    length, fps, ...), for a target whose workflow takes them as widgets.
    None means "leave the workflow's value"."""
    d = job.doc.get("defaults", {})
    vals = {"prompt": _prompt_text(job.prompt),
            "negative": job.shot.get("negative", d.get("negative")),
            "width": job.width or None, "height": job.height or None,
            "length": job.frames or None, "fps": job.fps,
            "shot_id": job.id, "audio_policy": job.shot.get("audio_policy") or "generate"}
    for name in job_target(job).binding.params:
        if name not in vals and name not in JOB_PARAMS:
            vals[name] = job.shot.get(name, d.get(name))
    return vals


def prepare_saver(g: dict, binding) -> None:
    """Put the binding's saver in the graph when the workflow ends in another
    node (`saver.replace`): it takes that node's inputs, the node and the
    `saver.drop` classes go, and `saver.set` fixes some of its inputs."""
    sv = binding.saver
    if not sv.get("replace") or any(v["class_type"] == binding.saver_class for v in g.values()):
        return
    rep = sv["replace"]
    old = node_of(g, rep["class_type"])
    inputs = {mine: g[old]["inputs"][theirs] for mine, theirs in (rep.get("inputs") or {}).items()
              if theirs in g[old]["inputs"]}
    inputs.update(sv.get("set") or {})
    for k, _ in _consumers(g, old):
        del g[k]
    del g[old]
    for k in [k for k, v in g.items() if v["class_type"] in (sv.get("drop") or [])]:
        del g[k]
    g["h3_saver"] = {"class_type": binding.saver_class, "inputs": inputs,
                     "_meta": {"title": f"{binding.saver_class} (h3pipe)"}}


def prune(g: dict, keep: str) -> None:
    """Drop every node `keep` doesn't depend on."""
    need, stack = set(), [keep]
    while stack:
        nid = stack.pop()
        if nid in need or nid not in g:
            continue
        need.add(nid)
        for v in g[nid]["inputs"].values():
            if isinstance(v, list) and len(v) == 2 and isinstance(v[0], str):
                stack.append(v[0])
    for k in [k for k in g if k not in need]:
        del g[k]


def graph_for(base: dict, job: Job, take: T.Take, *, panel_mode: str | None = None,
              save_frames: bool | None = None, review_copy: bool = True,
              strip_meta: bool = False, inputs: dict | None = None) -> dict:
    """`base` (the target's workflow as an API graph) patched for one take,
    following the job's target's binding: the loader (if the target has one)
    reads the take's frozen shotlist, the saver writes into the take, and the
    widgets the binding names get the job's values (model, LoRAs, steps, seed,
    and for a loader-less target the prompt, size, length, fps, ...). Then the
    target's own graph code, if any (keyframes: `inputs`, default
    job.inputs), and `prune`."""
    t = job_target(job)
    b = t.binding
    g = copy.deepcopy(base)
    prepare_saver(g, b)
    saver = node_of(g, b.saver_class)
    if b.loader_class:
        loader = node_of(g, b.loader_class)
        lin = dict({"root": "project_root", "shotlist": "shotlist_file", "index": "index"},
                   **(b.loader.get("inputs") or {}))
        li = g[loader]["inputs"]
        li[lin["root"]] = job.root
        li[lin["shotlist"]] = os.path.relpath(take.paths.shotlist, job.root)
        li[lin["index"]] = 0
        if panel_mode:
            li["panel_mode"] = panel_mode
    if job.model:
        patch_param(g, b, "model", job.model)
    if job.loras is not None:
        # one chain per spec: a two-stage binding lists one per stage
        for spec in b.specs("loras") or [None]:
            apply_loras(g, loras_for(job.loras, spec or {}), spec)
    patch_param(g, b, "steps", job.steps)
    patch_param(g, b, "seed", job.seed)
    for name, value in job_values(job).items():
        if value is not None and name in b.params and name not in JOB_PARAMS:
            patch_param(g, b, name, value)
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
    if t.supports("patch_graph"):
        t.patch_graph(g, job, dict(job.inputs if inputs is None else inputs))
    if job.duration_head:
        add_duration_predictor(g, t, job)
    if b.prune:
        prune(g, saver)
    if strip_meta:
        for v in g.values():
            v.pop("_meta", None)
    return g


def add_duration_predictor(g: dict, target: "TG.Target", job: Job) -> None:
    """`dur: model` with the duration head installed (plan_duration): add
    ModelPatchLoader (job.duration_head) and LTXVDurationPredictor, fed the
    model and positive conditioning the workflow already has (the binding's
    `duration_predictor`: {"model"|"positive": {"class_type", "input"}}, what
    feeds that input of that node), and link its num_frames into every widget
    the binding's `length` param names, in place of the estimate."""
    spec = (target.spec.get("binding") or {}).get("duration_predictor") or {}
    if not (spec.get("model") and spec.get("positive")):
        raise ValueError(f"{target.id}'s binding has no duration_predictor (model, positive)")

    def source(s: dict) -> list:
        ids = sorted(k for k, v in g.items() if v["class_type"] == s["class_type"]
                     and isinstance(v["inputs"].get(s["input"]), list))
        if not ids:
            raise ValueError(f"the {target.id} workflow has no {s['class_type']} with a "
                             f"linked {s['input']} for the duration predictor")
        return list(g[ids[0]]["inputs"][s["input"]])

    rng = job.shot.get("duration_predict") or {}
    lo, hi = TG.PREDICT_RANGE
    g["h3_duration_head"] = {"class_type": "ModelPatchLoader",
                             "inputs": {"name": job.duration_head},
                             "_meta": {"title": "duration head (h3pipe)"}}
    g["h3_duration"] = {"class_type": "LTXVDurationPredictor",
                        "inputs": {"model": source(spec["model"]),
                                   "positive": source(spec["positive"]),
                                   "duration_head": ["h3_duration_head", 0],
                                   "frame_rate": float(job.fps),
                                   "min_seconds": float(rng.get("min_seconds", lo)),
                                   "max_seconds": float(rng.get("max_seconds", hi))},
                        "_meta": {"title": "duration predictor (h3pipe)"}}
    lengths = target.binding.specs("length")
    if not lengths:
        raise ValueError(f"{target.id}'s binding names no length widget to drive")
    for s in lengths:
        for nid in select_nodes(g, s):
            g[nid]["inputs"][s["field"]] = ["h3_duration", 0]


# ---------------------------------------------------------------------------
# inputs: images a render loads through ComfyUI's input folder
# ---------------------------------------------------------------------------

DURATION_LOADER = ("ModelPatchLoader", "name")
DURATION_NODE = "LTXVDurationPredictor"


def duration_head_file(job: Job) -> str:
    """The duration head a job's target preset names ("" if none)."""
    p = job_target(job).presets.get(job.pass_)
    return str(job.shot.get("duration_head") or (p.extra.get("duration_head") if p else "")
               or "")


def plan_duration(job: Job, comfy=None) -> None:
    """`dur: model` on a target that predicts (the entry's `duration_predict`):
    ask ComfyUI whether the preset's duration head is installed (the choices
    of ModelPatchLoader, /object_info) and the predictor node known. Yes:
    job.duration_head is set, graph_for adds the predictor, and the take's
    length_source is "predicted". No (or no ComfyUI to ask): the take renders
    the build's estimate, with a note saying why. Never fails."""
    job.duration_head = ""
    old = job.duration_note
    if old in job.notes:
        job.notes.remove(old)
    job.duration_note = ""
    if not job.shot.get("duration_predict"):
        return
    t = job_target(job)
    est = estimate_seconds(job.doc, job.shot)
    head = duration_head_file(job)
    job.length_source = "estimate"
    why = ""
    if not head:
        why = f"{t.id}'s {job.pass_} preset names no duration_head"
    elif comfy is None:
        why = "the duration head wasn't checked (no ComfyUI to ask)"
    else:
        try:
            have = comfy.choices(*DURATION_LOADER)
            node = have is not None and comfy.has_node(DURATION_NODE)
        except Exception as e:
            have, node, why = None, False, f"couldn't ask ComfyUI for the duration head ({e})"
        if why:
            pass
        elif have is None or not node:
            why = (f"this ComfyUI has no {DURATION_LOADER[0] if have is None else DURATION_NODE} "
                   f"node (update ComfyUI)")
        elif head not in have:
            why = f"{t.short} duration head not installed (models/model_patches: {head})"
    if why:
        note = f"{why}; used the estimate {est} s"
    else:
        job.duration_head, job.length_source = head, "predicted"
        rng = job.shot["duration_predict"]
        note = (f"length predicted by {head} ({rng['min_seconds']:g}-{rng['max_seconds']:g} s); "
                f"the build's estimate was {est} s")
    job.notes.append(note)
    job.duration_note = note


# ---------------------------------------------------------------------------
# model files: is each one the family its target needs? (targets/modelid.py)
# ---------------------------------------------------------------------------

# a models folder's older names, as ComfyUI still reads them
MODEL_FOLDER_ALIASES = {"diffusion_models": ("diffusion_models", "unet"),
                        "text_encoders": ("text_encoders", "clip")}
DEFAULT = object()                     # "work it out" (model_resolver, the temp cache)


def model_resolver():
    """How a model file's name becomes a path: inside ComfyUI (the routes),
    folder_paths.get_full_path; else under $COMFYUI_PATH/models (the CLI);
    else None (names only)."""
    try:
        import folder_paths                               # only inside ComfyUI
        get = folder_paths.get_full_path
    except Exception:
        get = None
    if get is not None:
        def resolve(folder: str, name: str) -> str | None:
            try:
                return get(folder, name)
            except Exception:
                return None
        return resolve
    comfy = os.environ.get("COMFYUI_PATH", "").strip()
    if not comfy:
        return None
    models = os.path.join(comfy, "models")

    def resolve(folder: str, name: str) -> str | None:
        for f in MODEL_FOLDER_ALIASES.get(folder, (folder,)):
            p = os.path.join(models, f, *name.replace("\\", "/").split("/"))
            if os.path.isfile(p):
                return p
        return None
    return resolve


def series_model_families(root: str) -> dict:
    """The model_families of the series config beside the episode (or in its
    parent folder), {} without one. ValueError on a malformed block."""
    for d in (root, os.path.dirname(os.path.normpath(root))):
        p = os.path.join(d, "series.json")
        if os.path.isfile(p):
            try:
                with open(p, encoding="utf-8") as fh:
                    cfg = json.load(fh)
            except (OSError, ValueError):
                return {}
            return TG.series_model_families(cfg if isinstance(cfg, dict) else {})
    return {}


def model_values(job: Job) -> dict[str, str]:
    """{param: file} for every model param the job's target declares a family
    for: the job's model, the shotlist's (else the preset's) text encoder,
    VAEs, upscaler, ..., and the duration head when the shot predicts its
    length. A param with no file set is left out."""
    t = job_target(job)
    vals = job_values(job)
    preset = t.presets.get(job.pass_) or t.presets.get("final")
    final = t.presets.get("final")
    out = {}
    for param in t.models:
        if param == "model":
            v = job.model
        elif param == "duration_head":
            v = duration_head_file(job) if job.shot.get("duration_predict") else ""
        else:
            v = vals.get(param) or job.shot.get(param) or job.doc.get("defaults", {}).get(param)
            for p in (preset, final):
                if not v and p is not None:
                    v = p.extra.get(param)
        if isinstance(v, str) and v:
            out[param] = v
    return out


def check_models(job: Job, resolve=DEFAULT, cache=DEFAULT,
                 extra: dict | None = None) -> list[dict]:
    """Check each model file a job would load against its target's families
    (targets.check_model), before its take is reserved.

    A name that matches passes silently. Otherwise the file's header decides
    (`resolve`, default model_resolver(): ComfyUI's folder_paths in the
    routes, $COMFYUI_PATH in the CLI, else names only; `cache`, default the
    temp folder's modelid cache): the right family passes with a note in the
    sidecar; unknown (or unchecked) passes with a note; ANOTHER family makes
    the job "mismatch" (it doesn't run), unless the request allowed a model
    mismatch, when it renders with a note. `extra` is the series config's
    model_families (default: read beside the episode). Replaces any earlier
    check's notes. Returns job.model_checks."""
    for n in job.model_notes:
        if n in job.notes:
            job.notes.remove(n)
    job.model_notes, job.model_checks = [], []
    if job.action == "mismatch":                          # a re-check starts over
        job.action = job.mismatch_from or "render"
    if job.action in ("skip", "busy", "blocked", "error"):
        return []
    if resolve is DEFAULT:
        resolve = model_resolver()
    if cache is DEFAULT:
        cache = TG.modelid.temp_cache()
    t = job_target(job)
    notes = []
    if extra is None:
        try:
            extra = series_model_families(job.root)
        except ValueError as e:
            extra = {}
            notes.append(f"series.json model_families ignored: {e}")
    for param, name in model_values(job).items():
        c = TG.check_model(t, param, name, resolve, extra, cache)
        if c is None:
            continue
        job.model_checks.append(c)
        if c["block"] and job.allow_model_mismatch:
            notes.append(f"model mismatch, rendered anyway: {c['message']}")
        elif c["message"] and not c["block"]:
            notes.append(c["message"])
    job.model_notes = notes
    job.notes.extend(notes)
    if any(c["block"] for c in job.model_checks) and not job.allow_model_mismatch:
        job.mismatch_from, job.action = job.action, "mismatch"
    return job.model_checks


INPUT_SUBFOLDER = "h3pipe"


def input_name(path: str) -> str:
    """The name a file gets in ComfyUI's input folder: input/h3pipe/<sha1>.<ext>.
    By content, so re-picking a keyframe never serves a stale copy and the
    same picture is uploaded once."""
    ext = os.path.splitext(path)[1].lower() or ".png"
    return f"{INPUT_SUBFOLDER}/{T.file_sha1(path)[:20]}{ext}"


def stage_inputs(job: Job, comfy=None, probe=None) -> dict:
    """Copy the images a job conditions on (a target's `role` ref slots that
    exist on disk: LTX's first / last keyframes) into ComfyUI's input folder
    and record their names in job.inputs ({role: "h3pipe/<sha1>.png"}).
    `comfy` uploads them (h3jobs.Comfy.upload_input: POST /upload/image, which
    works wherever ComfyUI runs); without it (a dry run) only the names are
    worked out. A target that makes an input of its own (ltx2_ingredients
    composes the shot's reference sheet) does it here too, in its
    `stage_inputs`; a file it keeps for the take waits in job.staged until
    start_job moves it into the take.

    `dur: model` is decided here too (plan_duration): `comfy`, or for a dry
    run `probe` (asked, never written to), says whether the duration head is
    installed."""
    plan_duration(job, comfy or probe)
    out = {}
    for r in ref_slots(job.doc, job.shot):
        role, p = r.get("role"), r.get("path")
        if not role or not p:
            continue
        full = _abs(job.root, p)
        if not os.path.isfile(full):
            continue
        name = input_name(full)
        if comfy is not None:
            comfy.upload_input(full, name)
        out[role] = name
    t = job_target(job)
    if t.supports("stage_inputs"):
        out.update(t.stage_inputs(job, comfy) or {})
    job.inputs = out
    return out
