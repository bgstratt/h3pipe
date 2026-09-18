#!/usr/bin/env python3
"""
kreagen.py — generate every H3 reference image from refs_todo.json on a
local ComfyUI, and write each result straight to the path h3build expects.

    python3 kreagen.py --project-root .
    python3 kreagen.py --project-root . --only sam,core_wide --redo
    python3 kreagen.py --project-root . --list
    python3 kreagen.py --project-root . --dry-run

Reads  <project_root>/refs_todo.json  and  <project_root>/series.json
Writes <project_root>/refs/_bg/<location>.png          1344x768
       <project_root>/refs/props/<name>.png            1024x1024
       <project_root>/refs/<char>/<char>_sheet_4panel.png   4096x1024

Character sheets are NOT generated as one 4:1 strip — a 4096x1024 canvas is
far outside any diffusion model's training distribution and comes back as
smeared repetition. Each of the four views is generated square and separately,
then stitched by mksheet.py, which is exactly what mksheet was written for.
The four views of one character share a seed so they stay on model.

Voice samples in refs_todo.json are skipped; they are not images.

Graph is a krea2 turbo image graph in API form. When a LoRA is given, the text
encoder reads the LoRA's CLIP rather than the raw CLIPLoader output, so
strength_clip is not inert; --no-lora-clip reproduces the original wiring.
The model file names are constants at the top of this file — point them at
whatever image model you have, and pass --lora/--unet to override per run.
"""
from __future__ import annotations

import argparse, hashlib, json, os, re, subprocess, sys, time, urllib.parse, urllib.request

# h3jobs already knows how to find a workflow (including the one saved in the
# running ComfyUI) and turn a canvas save into an API graph; reuse it rather
# than keeping a second converter in step with ComfyUI's format.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from h3jobs import resolve_workflow as _resolve_workflow
except Exception:                                    # h3jobs missing or broken
    _resolve_workflow = None

REFS_WORKFLOW = "krea2_refs_t2i.json"

# ---- the model stack: point these at your own krea2 turbo files -------------
UNET    = "krea2_turbo_fp8_scaled.safetensors"
CLIP    = "qwen3vl_4b_fp8_scaled.safetensors"
CLIPTYPE= "krea2"
VAE     = "wan_2.1_vae.safetensors"
# No style LoRA by default: the reference sheets have to match whatever look
# series.json asks for, and a realism LoRA fights a storybook one (and vice
# versa). Pass --lora to add one, e.g. a Krea2 realism LoRA for live action.
LORA    = ""
LORA_M  = 1.0
LORA_C  = 1.0
STEPS   = 12
CFG     = 1.0
SAMPLER = "euler"
SCHED   = "beta"

PREFIX  = "h3refs/tmp"      # ComfyUI-side staging prefix; we fetch over HTTP

VIEWS = [
    ("01_threequarter", "a three-quarter view of the full figure from head to feet, "
                        "turned slightly toward the viewer's left, standing straight "
                        "with arms relaxed at the sides"),
    ("02_side",         "a direct side profile of the full figure from head to feet, "
                        "facing the viewer's right, standing straight with arms "
                        "relaxed at the sides"),
    ("03_back",         "the full figure seen from directly behind, head to feet, "
                        "standing straight with arms relaxed at the sides"),
    ("04_face",         "a head-and-shoulders close-up, facing the viewer, "
                        "neutral expression"),
]

VIEW_TMPL = ("A single character reference view on a plain flat neutral background, "
             "no scene and no props, the whole figure inside the frame with margin "
             "on every side: {view}. {design}. Drawn as {look}. Output {w}x{h}.")


def seed_for(key: str) -> int:
    return int(hashlib.sha1(key.encode()).hexdigest()[:12], 16)


def parse_size(target: str, default=(1024, 1024)) -> tuple[int, int]:
    m = re.search(r"(\d+)\s*[x×]\s*(\d+)", target or "")
    return (int(m.group(1)), int(m.group(2))) if m else default


def _one(g: dict, *ctypes: str) -> str:
    ids = [k for k, v in g.items() if v["class_type"] in ctypes]
    if len(ids) != 1:
        raise ValueError(f"the workflow needs exactly one {' / '.join(ctypes)} node "
                         f"(found {len(ids)})")
    return ids[0]


def patch_workflow(base: dict, prompt: str, negative: str, w: int, h: int, seed: int,
                   steps: int, cfg: float, prefix: str,
                   unet: str = "", lora: str = "", lora_m: float = 1.0,
                   lora_c: float = 1.0) -> dict:
    """Set this job's values on a loaded workflow, leaving its wiring alone.

    Positive and negative prompts are found by following the sampler's own
    links, because the two CLIPTextEncode nodes are otherwise identical.
    """
    import copy
    g = copy.deepcopy(base)
    ks = _one(g, "KSampler", "KSamplerAdvanced")
    ki = g[ks]["inputs"]
    for key, val in (("seed", seed), ("noise_seed", seed), ("steps", steps),
                     ("cfg", cfg), ("denoise", 1.0)):
        if key in ki:
            ki[key] = val

    def text_node(slot: str) -> str | None:
        link = ki.get(slot)
        return link[0] if isinstance(link, list) else None

    pos, neg = text_node("positive"), text_node("negative")
    if pos and g[pos]["class_type"] == "CLIPTextEncode":
        g[pos]["inputs"]["text"] = prompt
    else:
        raise ValueError("the workflow's sampler has no CLIPTextEncode on `positive`")
    # The negative side is left exactly as the workflow wires it — a krea2 turbo
    # graph zeroes it, a guided model's graph carries its own text, and a NAG or
    # negpip setup is untouched. Only an explicit --negative-file overrides that.
    if neg and negative and cfg > 1.0:
        g[neg] = {"class_type": "CLIPTextEncode",
                  "inputs": {"clip": g[pos]["inputs"]["clip"], "text": negative}}

    lat = _one(g, "EmptyLatentImage", "EmptySD3LatentImage")
    g[lat]["inputs"]["width"], g[lat]["inputs"]["height"] = w, h
    g[_one(g, "SaveImage")]["inputs"]["filename_prefix"] = prefix

    if unet:
        g[_one(g, "UNETLoader")]["inputs"]["unet_name"] = unet
    if lora:
        ids = [k for k, v in g.items() if v["class_type"] in ("LoraLoader",
                                                              "LoraLoaderModelOnly")]
        if len(ids) > 1:
            raise ValueError(f"--lora needs one LoraLoader in the workflow (found {len(ids)})")
        if ids:
            gi = g[ids[0]]["inputs"]
            gi["lora_name"] = lora
            if "strength_model" in gi:
                gi["strength_model"] = lora_m
            if "strength_clip" in gi:
                gi["strength_clip"] = lora_c
        else:
            # the workflow has no LoRA node: splice one in between the loaders
            # and everything that reads them, so --lora works on any graph
            lid = "kreagen_lora"
            model_src, clip_src = ki["model"], g[pos]["inputs"]["clip"]
            g[lid] = {"class_type": "LoraLoader",
                      "inputs": {"model": model_src, "clip": clip_src,
                                 "lora_name": lora, "strength_model": lora_m,
                                 "strength_clip": lora_c}}
            ki["model"] = [lid, 0]
            for node in g.values():
                if node["class_type"] == "CLIPTextEncode" and \
                        node["inputs"].get("clip") == clip_src:
                    node["inputs"]["clip"] = [lid, 1]
    return g


def build_graph(prompt: str, negative: str, w: int, h: int, seed: int,
                steps: int, cfg: float, prefix: str, lora_clip: bool,
                unet: str = "", lora: str = "", lora_m: float = LORA_M,
                lora_c: float = LORA_C) -> dict:
    lora = lora or LORA
    model_src = ["4", 0] if lora else ["1", 0]
    clip_src = (["4", 1] if lora_clip else ["2", 0]) if lora else ["2", 0]
    g = {
        "1": {"class_type": "UNETLoader",
              "inputs": {"unet_name": unet or UNET, "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader",
              "inputs": {"clip_name": CLIP, "type": CLIPTYPE, "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": VAE}},

        "5": {"class_type": "CLIPTextEncode",
              "inputs": {"clip": clip_src, "text": prompt}},
        "7": {"class_type": "EmptyLatentImage",
              "inputs": {"width": w, "height": h, "batch_size": 1}},
        "8": {"class_type": "KSampler",
              "inputs": {"model": model_src, "positive": ["5", 0], "negative": ["6", 0],
                         "latent_image": ["7", 0], "seed": seed, "steps": steps,
                         "cfg": cfg, "sampler_name": SAMPLER, "scheduler": SCHED,
                         "denoise": 1.0}},
        "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["3", 0]}},
        "10": {"class_type": "SaveImage",
               "inputs": {"images": ["9", 0], "filename_prefix": prefix}},
    }
    if lora:
        g["4"] = {"class_type": "LoraLoader",
                  "inputs": {"model": ["1", 0], "clip": ["2", 0], "lora_name": lora,
                             "strength_model": lora_m, "strength_clip": lora_c}}
    # At cfg 1.0 there is no guidance, so a negative prompt is inert and
    # ConditioningZeroOut is the cheap correct thing. Above 1.0 it bites.
    if cfg > 1.0 and negative:
        g["6"] = {"class_type": "CLIPTextEncode",
                  "inputs": {"clip": clip_src, "text": negative}}
    else:
        g["6"] = {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["5", 0]}}
    return g


class Comfy:
    def __init__(self, base: str):
        self.base = base.rstrip("/")

    def _json(self, path: str, payload=None):
        url = f"{self.base}{path}"
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(url, data=data,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())

    def queue(self, graph: dict) -> str:
        return self._json("/prompt", {"prompt": graph})["prompt_id"]

    def wait(self, pid: str, timeout: int) -> dict:
        t0 = time.time()
        while time.time() - t0 < timeout:
            hist = self._json(f"/history/{pid}")
            if pid in hist:
                st = hist[pid].get("status", {})
                if st.get("status_str") == "error" or st.get("completed") is False:
                    raise RuntimeError(json.dumps(st)[:400])
                return hist[pid].get("outputs", {})
            time.sleep(1.5)
        raise TimeoutError(f"no result after {timeout}s")

    def fetch(self, img: dict) -> bytes:
        q = urllib.parse.urlencode({"filename": img["filename"],
                                    "subfolder": img.get("subfolder", ""),
                                    "type": img.get("type", "output")})
        with urllib.request.urlopen(f"{self.base}/view?{q}", timeout=120) as r:
            return r.read()


def collect_jobs(todo, bible, root, view_size):
    """One job per image to generate. A sheet fans out into four view jobs."""
    look = bible.get("style", {}).get("look", "")
    jobs = []
    for item in todo:
        kind, path = item["kind"], item["path"]
        if kind == "voice sample":
            continue
        out = os.path.join(root, path)
        if kind == "character sheet":
            char = os.path.basename(path).split("_sheet")[0]
            design = (bible.get("subjects", {}).get(char, {}) or {}).get("design")
            if not design:
                print(f"  ! no design in series.json for '{char}' — skipping {path}")
                continue
            vw, vh = view_size
            views = []
            for tag, desc in VIEWS:
                views.append({
                    "name": f"{char}:{tag}",
                    "prompt": VIEW_TMPL.format(view=desc, design=design, look=look,
                                               w=vw, h=vh),
                    "w": vw, "h": vh,
                    "seed": seed_for(char),          # shared across the four views
                    "out": os.path.join(root, "views", char, f"{tag}.png"),
                })
            jobs.append({"kind": "sheet", "name": char, "out": out,
                         "views": views, "blocks": len(item.get("blocks_shots", []))})
        else:
            w, h = parse_size(item["target"],
                              (1024, 1024) if kind == "prop reference" else (1344, 768))
            jobs.append({"kind": "single",
                         "name": os.path.splitext(os.path.basename(path))[0],
                         "out": out, "prompt": item["prompt"], "w": w, "h": h,
                         "seed": seed_for(path),
                         "blocks": len(item.get("blocks_shots", []))})
    jobs.sort(key=lambda j: -j["blocks"])            # most-blocking first
    return jobs


def run_one(comfy, spec, args, negative, base=None):
    if base is not None:
        graph = patch_workflow(base, spec["prompt"], negative, spec["w"], spec["h"],
                               spec["seed"], args.steps, args.cfg, PREFIX,
                               unet=args.unet, lora=args.lora,
                               lora_m=args.lora_strength, lora_c=args.lora_strength)
    else:
        graph = build_graph(spec["prompt"], negative, spec["w"], spec["h"], spec["seed"],
                            args.steps, args.cfg, PREFIX, not args.no_lora_clip,
                            unet=args.unet, lora=args.lora,
                            lora_m=args.lora_strength, lora_c=args.lora_strength)
    pid = comfy.queue(graph)
    outs = comfy.wait(pid, args.timeout)
    imgs = [i for o in outs.values() for i in o.get("images", [])]
    if not imgs:
        raise RuntimeError("no image in outputs")
    os.makedirs(os.path.dirname(os.path.abspath(spec["out"])) or ".", exist_ok=True)
    with open(spec["out"], "wb") as f:
        f.write(comfy.fetch(imgs[0]))
    return spec["out"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project-root", default=".",
                    help="episode folder holding refs_todo.json and series.json")
    ap.add_argument("--comfy", default="http://127.0.0.1:8188",
                    help="ComfyUI address (default %(default)s)")
    ap.add_argument("--only", help="comma-separated asset name filter, e.g. sam,core_wide")
    ap.add_argument("--redo", action="store_true", help="regenerate assets already on disk")
    ap.add_argument("--workflow", help=f"reference-image workflow to drive "
                    f"(default: $KREA_WORKFLOW, else this repo's {REFS_WORKFLOW}, "
                    f"else the built-in graph)")
    ap.add_argument("--no-workflow", action="store_true",
                    help="ignore any workflow file and use the built-in graph")
    ap.add_argument("--list", action="store_true", help="show the job list and exit")
    ap.add_argument("--dry-run", action="store_true", help="print prompts, queue nothing")
    ap.add_argument("--steps", type=int, default=STEPS,
                    help="sampler steps (default %(default)s; match a step-distilled LoRA)")
    ap.add_argument("--cfg", type=float, default=CFG,
                    help="1.0 = turbo, no guidance (negative prompt inert). >1 enables it.")
    ap.add_argument("--negative-file",
                    help="text file with a negative prompt; only bites at --cfg > 1, so "
                         "it is for non-distilled models, not krea2 turbo")
    ap.add_argument("--view-size", default="1024x1024",
                    help="per-panel generation size for sheets (default 1024x1024 "
                         "-> a 4096x1024 stitched strip)")
    ap.add_argument("--panel-height", type=int, default=1024, help="passed to mksheet.py")
    ap.add_argument("--mksheet", default=None, help="path to mksheet.py (default: beside this file)")
    ap.add_argument("--timeout", type=int, default=900,
                    help="seconds to wait per image (default %(default)s)")
    ap.add_argument("--unet", default="", help=f"image model file name (default {UNET})")
    ap.add_argument("--lora", default="",
                    help="optional style LoRA file name, e.g. krea2_style.safetensors")
    ap.add_argument("--lora-strength", type=float, default=LORA_M,
                    help="strength for --lora, model and clip (default %(default)s)")
    ap.add_argument("--no-lora-clip", action="store_true",
                    help="reproduce the base graph's unwired LoRA CLIP")
    args = ap.parse_args()

    root = args.project_root
    todo_p = os.path.join(root, "refs_todo.json")
    bible_p = os.path.join(root, "series.json")
    for p in (todo_p, bible_p):
        if not os.path.isfile(p):
            print(f"error: {p} not found. Run h3build first.", file=sys.stderr)
            return 1
    todo = json.load(open(todo_p, encoding="utf-8"))
    bible = json.load(open(bible_p, encoding="utf-8"))

    negative = ""
    if args.negative_file:
        negative = open(args.negative_file, encoding="utf-8").read().strip()
        if args.cfg <= 1.0:
            print("  ! --negative-file given but cfg is 1.0, where there is no guidance\n"
                  "    branch to apply it to, so it does nothing. Turbo checkpoints want\n"
                  "    cfg 1; for real suppression there use a NAG or negpip node in your\n"
                  "    own workflow. Otherwise try --cfg 1.5 --steps 16 on a model that\n"
                  "    expects guidance, or fold the exclusions into the positive text.\n")

    vw, vh = parse_size(args.view_size, (1024, 1024))
    jobs = collect_jobs(todo, bible, root, (vw, vh))
    if args.only:
        keys = {k.strip() for k in args.only.split(",")}
        jobs = [j for j in jobs if j["name"] in keys or
                any(k in j["out"] for k in keys)]

    mksheet = args.mksheet or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                           "mksheet.py")

    base, wf = None, ""
    if not args.no_workflow:
        if _resolve_workflow is None:
            print("  ! h3jobs.py is not importable — using the built-in graph")
        else:
            try:
                # Not the canvas saved in ComfyUI: that one is for trying
                # things by hand and may carry a style LoRA, and references
                # must follow series.json's look (see LORA above).
                base, wf = _resolve_workflow(args.workflow, REFS_WORKFLOW, None,
                                             env="KREA_WORKFLOW", required=False,
                                             prefer_repo=True)
            except Exception as e:
                print(f"  ! {REFS_WORKFLOW} could not be read ({e}) — using the built-in graph")
                base, wf = None, ""

    print(f"\n  {len(jobs)} asset(s) · comfy {args.comfy} · "
          f"{args.steps} steps · cfg {args.cfg}\n"
          f"  graph {wf if wf else 'built-in (' + UNET + ')'}\n"
          f"  {'-' * 62}")
    todo_now = []
    for j in jobs:
        have = os.path.isfile(j["out"])
        mark = "have" if have and not args.redo else ("redo" if have else " -- ")
        size = f"{vw}x{vh} x4" if j["kind"] == "sheet" else f"{j['w']}x{j['h']}"
        print(f"  [{mark}] {j['out']:<44} {size:>12}  blocks {j['blocks']}")
        if not have or args.redo:
            todo_now.append(j)
    print()

    if args.list:
        return 0
    if args.dry_run:
        for j in todo_now:
            for s in (j["views"] if j["kind"] == "sheet" else [j]):
                print(f"--- {s.get('name', j['name'])}  seed {s['seed']}\n{s['prompt']}\n")
        return 0
    if not todo_now:
        print("  nothing to do — pass --redo to regenerate.\n")
        return 0

    comfy, failed = Comfy(args.comfy), []
    for j in todo_now:
        try:
            if j["kind"] == "sheet":
                for s in j["views"]:
                    print(f"  .. {s['name']}")
                    run_one(comfy, s, args, negative, base)
                cmd = [sys.executable, mksheet] + [s["out"] for s in j["views"]] + \
                      ["-o", j["out"], "--panel-height", str(args.panel_height)]
                r = subprocess.run(cmd, capture_output=True, text=True)
                sys.stdout.write(r.stdout)
                if r.returncode != 0:
                    raise RuntimeError(r.stderr.strip()[:300] or "mksheet failed")
            else:
                print(f"  .. {j['name']}")
                run_one(comfy, j, args, negative, base)
                print(f"  -> {j['out']}")
        except Exception as e:
            print(f"  !! {j['name']}: {e}")
            failed.append(j["name"])

    print(f"\n  done. {len(todo_now) - len(failed)} written"
          + (f", {len(failed)} failed: {', '.join(failed)}" if failed else "")
          + "\n  re-run h3build to confirm the count.\n")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
