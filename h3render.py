#!/usr/bin/env python3
"""
h3render.py — queue every shot of one or more episodes on a local ComfyUI,
using the H3 Ref2VA shot-list workflow, and wait for each render.

    python h3render.py path/to/project/ep05
    python h3render.py Shows\\ep05 --proxy
    python h3render.py Shows\\ep05 --only sh040,sh050 --redo
    python h3render.py --all Shows --proxy          # every ep* folder
    python h3render.py Shows\\ep05 --list           # what would run
    python h3render.py Shows\\ep05 --dry-run        # write the API graph, queue nothing

This is the command-line twin of H3_Ref2VA_Shotlist_v1.json. It loads that
workflow, sets Shot List Loader (project_root, shotlist_file, index) and
Save Shot (project_root, subfolder, take) for each shot, and queues one shot
at a time — the same thing the canvas does with `increment` and a batch count.

The script must run on the machine that runs ComfyUI: it checks
<project>/<subfolder>/<shot>/ on disk to skip finished shots and to confirm
each render landed.

Takes and skipping
    A shot with any <shot>_tNN.mp4 already on disk is skipped.
    --redo renders it again as the NEXT take (t02, t03 ...), so nothing is
    overwritten. --take N forces a take number (and overwrites that take).

Proxy vs final
    --proxy reads shotlist/shotlist_proxy.json and writes to renders_proxy/,
    so the animatic never shadows or blocks the final pass. Assemble it with
        python h3assemble.py -o <project> --shotlist shotlist/shotlist_proxy.json --subfolder renders_proxy

Workflow file
    Default: H3_Ref2VA_Shotlist_v1.json in the folder above this script (the
    ComfyUI workflows folder), or beside it. Either the normal UI save or an
    API export (Workflow > Export (API)) works; the UI save is converted here.
    If ComfyUI rejects a converted graph, export the API version once and pass
    it with --workflow.
"""
from __future__ import annotations

import argparse
import copy
import glob
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

LOADER = "H3ShotListLoader"
SAVER = "H3SaveShot"
SKIP_UI = {"MarkdownNote", "Note", "H3ShotInfo", "Reroute"}
PRIMITIVES = {"PrimitiveInt", "PrimitiveFloat", "PrimitiveString",
              "PrimitiveBoolean", "PrimitiveNode", "PrimitiveStringMultiline"}
CONTROL_WORDS = {"fixed", "increment", "decrement", "randomize"}


# ---------------------------------------------------------------------------
# workflow loading

def find_workflow(explicit: str | None) -> str:
    if explicit:
        return explicit
    here = os.path.dirname(os.path.abspath(__file__))
    # $H3_WORKFLOW wins, then beside this file, then $COMFYUI_PATH's workflows folder
    cands = [os.environ.get("H3_WORKFLOW", ""),
             os.path.join(here, "workflows", WORKFLOW_NAME),
             os.path.join(here, WORKFLOW_NAME),
             os.path.join(here, "..", WORKFLOW_NAME)]
    comfy = os.environ.get("COMFYUI_PATH", "")
    if comfy:
        cands.append(os.path.join(comfy, "user", "default", "workflows", WORKFLOW_NAME))
    for cand in cands:
        if cand and os.path.isfile(cand):
            return os.path.normpath(cand)
    raise FileNotFoundError(f"{WORKFLOW_NAME} not found — pass --workflow, or set "
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
    if "nodes" in data and "links" in data:
        return ui_to_api(data)
    if all(isinstance(v, dict) and "class_type" in v for v in data.values()):
        return data
    raise ValueError(f"{path} is neither a ComfyUI workflow save nor an API export")


def node_of(graph: dict, ctype: str) -> str:
    ids = [k for k, v in graph.items() if v["class_type"] == ctype]
    if len(ids) != 1:
        raise ValueError(f"workflow must contain exactly one {ctype} node (found {len(ids)})")
    return ids[0]


# ---------------------------------------------------------------------------
# ComfyUI client

class Comfy:
    def __init__(self, base: str):
        self.base = base.rstrip("/")

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

    def queue(self, graph: dict) -> str:
        r = self._json("/prompt", {"prompt": graph, "client_id": "h3render"})
        if r.get("node_errors"):
            raise RuntimeError(json.dumps(r["node_errors"])[:1500])
        return r["prompt_id"]

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

    def interrupt(self):
        try:
            self._json("/interrupt", {})
        except Exception:
            pass


# ---------------------------------------------------------------------------
# jobs

def existing_takes(shot_dir: str, sid: str) -> list[int]:
    takes = []
    for p in glob.glob(os.path.join(shot_dir, f"{sid}_t*.mp4")):
        m = re.search(rf"{re.escape(sid)}_t(\d+)\.mp4$", os.path.basename(p))
        if m:
            takes.append(int(m.group(1)))
    return sorted(takes)


WORKFLOW_NAME = "H3_Ref2VA_Shotlist_v1.json"
LORA = "LoraLoaderModelOnly"
UNET = "UNETLoader"


def plan_episode(root: str, args) -> list[dict]:
    sl_rel = "shotlist/shotlist_proxy.json" if args.proxy else "shotlist/shotlist.json"
    sl_path = os.path.join(root, sl_rel)
    if not os.path.isfile(sl_path):
        raise FileNotFoundError(f"{sl_path} not found — run h3build first"
                                + (" with --proxy" if args.proxy else ""))
    with open(sl_path, encoding="utf-8") as fh:
        doc = json.load(fh)
    shots = doc["shots"]
    # model and turbo LoRA: the flag beats the shot, the shot beats the pass
    # default, and an empty value leaves whatever the workflow already has
    dflt = doc.get("defaults", {})
    only = {s.strip() for s in args.only.split(",")} if args.only else None
    jobs = []
    for idx, s in enumerate(shots):
        sid = s["id"]
        if only and sid not in only:
            continue
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", sid) or "shot"
        shot_dir = os.path.join(root, args.subfolder, safe)
        takes = existing_takes(shot_dir, safe)
        if args.take:
            take, action = args.take, ("overwrite" if args.take in takes else "render")
        elif takes and not args.redo:
            take, action = takes[-1], "skip"
        else:
            take, action = (takes[-1] + 1 if takes else 1), ("redo" if takes else "render")
        jobs.append({"index": idx, "id": sid, "safe": safe, "take": take, "action": action,
                     "frames": s.get("length", 0), "policy": s.get("audio_policy", ""),
                     "mp4": os.path.join(shot_dir, f"{safe}_t{take:02d}.mp4"),
                     "sl_rel": sl_rel,
                     "lora": args.lora or s.get("lora") or dflt.get("lora", ""),
                     "model": args.model or s.get("model") or dflt.get("model", "")})
    return jobs


def graph_for(base: dict, loader: str, saver: str, root: str, job: dict, args) -> dict:
    g = copy.deepcopy(base)
    li = g[loader]["inputs"]
    li["project_root"] = root
    li["shotlist_file"] = job["sl_rel"]
    li["index"] = job["index"]
    if args.panel_mode:
        li["panel_mode"] = args.panel_mode
    for ctype, field, key in ((LORA, "lora_name", "lora"), (UNET, "unet_name", "model")):
        want = job.get(key)
        if not want:
            continue
        ids = [k for k, v in g.items() if v["class_type"] == ctype]
        if len(ids) != 1:
            raise ValueError(f"the shotlist names a {key} but the workflow has "
                             f"{len(ids)} {ctype} nodes")
        if key == "lora" and want.lower() in ("none", "off", "-"):
            # the node stays wired; strength 0 makes it a no-op, which is how a
            # shot renders on the base model without rebuilding the graph
            g[ids[0]]["inputs"]["strength_model"] = 0.0
        else:
            g[ids[0]]["inputs"][field] = want
    si = g[saver]["inputs"]
    si["project_root"] = root
    si["subfolder"] = args.subfolder
    si["take"] = job["take"]
    if args.save_frames is not None:
        si["save_frames"] = args.save_frames
    if args.no_review_copy:
        for k in [k for k, v in g.items() if v["class_type"] in ("SaveVideo", "CreateVideo")]:
            del g[k]
    if args.strip_meta:
        for v in g.values():
            v.pop("_meta", None)
    return g


def fmt(sec: float) -> str:
    sec = int(sec)
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("projects", nargs="*", help="episode folders (each holds shotlist/)")
    ap.add_argument("--all", metavar="DIR", help="render every ep* folder under DIR")
    ap.add_argument("--workflow", help="workflow .json (UI save or API export)")
    ap.add_argument("--comfy", default="http://127.0.0.1:8188",
                    help="ComfyUI address (default %(default)s)")
    ap.add_argument("--proxy", action="store_true", help="render shotlist_proxy.json")
    ap.add_argument("--subfolder", default=None,
                    help="render folder (default renders, or renders_proxy with --proxy)")
    ap.add_argument("--only", help="comma-separated shot ids, e.g. sh040,sh050")
    ap.add_argument("--redo", action="store_true", help="re-render finished shots as a new take")
    ap.add_argument("--take", type=int, help="force this take number (overwrites it)")
    ap.add_argument("--lora", help="turbo LoRA file name; overrides the shotlist")
    ap.add_argument("--model", help="H3 unet file name; overrides the shotlist")
    ap.add_argument("--panel-mode", choices=["auto", "full", "pair", "face", "body"])
    fr = ap.add_mutually_exclusive_group()
    fr.add_argument("--save-frames", dest="save_frames", action="store_true", default=None,
                    help="also write each shot as a PNG sequence (16-50 GB per episode)")
    fr.add_argument("--no-frames", dest="save_frames", action="store_false",
                    help="mp4 and wav only, no PNG sequence")
    ap.add_argument("--no-review-copy", action="store_true",
                    help="drop the CreateVideo/SaveVideo review copy in ComfyUI/output")
    ap.add_argument("--strip-meta", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--timeout", type=int, default=3600,
                    help="seconds to wait per shot (default %(default)s)")
    ap.add_argument("--list", action="store_true", help="show the job list and exit")
    ap.add_argument("--dry-run", action="store_true",
                    help="write the first shot's API graph to h3render_graph.json, queue nothing")
    ap.add_argument("--stop-on-error", action="store_true",
                    help="stop the run on the first failed shot instead of skipping it")
    args = ap.parse_args()
    if args.subfolder is None:
        args.subfolder = "renders_proxy" if args.proxy else "renders"

    roots = [os.path.abspath(p) for p in args.projects]
    if args.all:
        roots += sorted(p for p in glob.glob(os.path.join(os.path.abspath(args.all), "ep*"))
                        if os.path.isdir(os.path.join(p, "shotlist")))
    if not roots:
        ap.error("give one or more episode folders, or --all DIR")

    wf = find_workflow(args.workflow)
    base = load_graph(wf)
    loader, saver = node_of(base, LOADER), node_of(base, SAVER)

    plans, total_frames = [], 0
    for root in roots:
        try:
            jobs = plan_episode(root, args)
        except FileNotFoundError as e:
            print(f"  ! {e}")
            continue
        todo = [j for j in jobs if j["action"] != "skip"]
        plans.append((root, jobs))
        total_frames += sum(j["frames"] for j in todo)
        print(f"\n  {os.path.basename(root)}  ·  {len(todo)} to render, "
              f"{len(jobs) - len(todo)} done  ·  {'proxy' if args.proxy else 'final'}"
              f"  ->  {args.subfolder}/")
        for key in ("model", "lora"):
            vals = sorted({j[key] for j in jobs if j[key]})
            if vals:
                print(f"    {key} {', '.join(vals)}")
            if len(vals) > 1:
                print(f"    ! {len(vals)} different {key}s here — ComfyUI reloads on "
                      f"every change, so expect a pause at those shots")
        if args.list or args.dry_run:
            for j in jobs:
                print(f"    [{j['action']:>9}] #{j['index']:<3} {j['id']:<8} t{j['take']:02d}"
                      f"  {j['frames']:>4}f  {j['policy']}")
    n_todo = sum(1 for _, js in plans for j in js if j["action"] != "skip")
    print(f"\n  workflow {wf}\n  {n_todo} shot(s), {total_frames / 24:.1f}s of video · comfy {args.comfy}\n")

    if args.list:
        return 0
    if args.dry_run:
        for root, jobs in plans:
            for j in jobs:
                g = graph_for(base, loader, saver, root, j, args)
                out = os.path.join(os.getcwd(), "h3render_graph.json")
                json.dump(g, open(out, "w", encoding="utf-8"), indent=2)
                print(f"  wrote {out} ({j['id']} of {os.path.basename(root)})")
                return 0
        return 0
    if not n_todo:
        print("  nothing to do — pass --redo to render new takes.\n")
        return 0

    comfy = Comfy(args.comfy)
    try:
        comfy.ping()
    except Exception as e:
        print(f"  !! cannot reach ComfyUI at {args.comfy}: {e}")
        return 1

    done = failed = 0
    failures = []
    t_start = time.time()
    frames_done = 0
    try:
        for root, jobs in plans:
            ep = os.path.basename(root)
            for j in (j for j in jobs if j["action"] != "skip"):
                label = f"{ep}/{j['id']} t{j['take']:02d}"
                t0 = time.time()
                print(f"  .. {label}  ({j['frames']}f, {j['policy']})", flush=True)
                try:
                    pid = comfy.queue(graph_for(base, loader, saver, root, j, args))
                    comfy.wait(pid, args.timeout)
                    if not os.path.isfile(j["mp4"]):
                        raise RuntimeError(f"ComfyUI finished but {j['mp4']} is missing")
                    done += 1
                    frames_done += j["frames"]
                    rate = (time.time() - t_start) / max(frames_done, 1)
                    left = (total_frames - frames_done) * rate
                    print(f"  -> {j['mp4']}  [{fmt(time.time() - t0)}, ~{fmt(left)} left]",
                          flush=True)
                except Exception as e:
                    failed += 1
                    failures.append(label)
                    print(f"  !! {label}: {e}", flush=True)
                    if args.stop_on_error:
                        raise KeyboardInterrupt
    except KeyboardInterrupt:
        print("\n  stopping — interrupting the running ComfyUI job")
        comfy.interrupt()

    print(f"\n  done in {fmt(time.time() - t_start)}: {done} rendered"
          + (f", {failed} failed: {', '.join(failures)}" if failed else ""))
    print("  assemble with: python h3assemble.py -o <episode>"
          + (" --shotlist shotlist/shotlist_proxy.json --subfolder renders_proxy" if args.proxy else "")
          + "\n")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
