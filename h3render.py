#!/usr/bin/env python3
"""
h3render.py — queue every shot of one or more episodes on a local ComfyUI,
using the H3 Ref2VA shot-list workflow, and wait for each render.

    python h3render.py path/to/project/ep05
    python h3render.py Shows\\ep05 --proxy
    python h3render.py Shows\\ep05 --only sh040,sh050 --redo
    python h3render.py Shows\\ep05 --only sh040 --redo --lora a.safetensors --lora b.safetensors:0.6
    python h3render.py --all Shows --proxy          # every ep* folder
    python h3render.py Shows\\ep05 --list           # what would run
    python h3render.py Shows\\ep05 --dry-run        # write the API graph, queue nothing

This is the command-line twin of H3_Ref2VA_Shotlist_v1.json. It loads that
workflow, points Shot List Loader at a frozen one-shot shotlist for each take
and Save Shot at the take's folder and sidecar, and queues one shot at a time.
The work of deciding what a take renders lives in h3jobs.py, which the editor
will share.

The script must run on the machine that runs ComfyUI: it checks
<project>/<subfolder>/<shot>/ on disk to skip finished shots and to confirm
each render landed.

Takes and skipping
    A shot with a finished take (or one still queued) is skipped. --redo
    renders it again as the NEXT take (t02, t03 ...), so nothing is
    overwritten. --take N forces a take number (and overwrites that take).
    Every take gets a sidecar (<shot>_tNN.json: settings, seed, status) and a
    frozen copy of the shotlist entry it rendered (<shot>_tNN.shotlist.json),
    so any take can be reproduced. See h3takes.py and h3jobs.py.

Seeds
    The first take of a shot uses the shotlist's seed (derived from the ids).
    --redo picks a NEW seed, so a redo is actually a different attempt.
    --same-seed keeps the shotlist's seed; --seed N uses N; --new-seed forces
    a fresh one even on a first render. A seed pinned in overrides.json beats
    everything except --seed and --new-seed.

Overrides
    <episode>/overrides.json (written by `h3.py override` or the editor) can
    replace a shot's prompt, seed, model, LoRAs and steps without touching the
    script. The flags here beat it for this run.

Proxy vs final
    --proxy reads shotlist/shotlist_proxy.json and writes to renders_proxy/,
    so the animatic never shadows or blocks the final pass. Assemble it with
        python h3assemble.py -o <project> --shotlist shotlist/shotlist_proxy.json

Workflow file
    Default: H3_Ref2VA_Shotlist_v1.json in the folder above this script (the
    ComfyUI workflows folder), or beside it. Either the normal UI save or an
    API export (Workflow > Export (API)) works; the UI save is converted here.
    If ComfyUI rejects a converted graph, export the API version once and pass
    it with --workflow.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import h3takes as T  # noqa: E402
# Re-exported: kreagen imports load_graph from here.
from h3jobs import (  # noqa: E402,F401
    LOADER, SAVER, LORA, UNET, WORKFLOW_NAME, Comfy, RenderRequest, find_workflow,
    finish_job, graph_for, load_graph, load_shotlist, mark_failed, mark_queued, node_of,
    parse_lora, plan_episode, start_job, ui_to_api)


def fmt(sec: float) -> str:
    sec = int(sec)
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def lora_label(loras) -> str:
    if loras is None:
        return "(as in the workflow)"
    if not loras:
        return "none"
    return " + ".join(l["name"] + (f"@{l['strength']:g}" if l.get("strength", 1) != 1 else "")
                      for l in loras)


def sweep(comfy: Comfy, roots: list[str], pass_: str, folder: str | None) -> int:
    """Mark takes left `queued` by a job ComfyUI no longer has as failed."""
    as_of = T.now()                  # before the fetch: the snapshot is at least this old
    try:
        alive = comfy.alive()
    except Exception:
        return 0                     # ComfyUI down: can't tell, touch nothing
    n = 0
    for root in roots:
        try:
            doc = load_shotlist(root, pass_)
        except FileNotFoundError:
            continue
        for s in doc["shots"]:
            n += len(T.sweep_queued(T.list_takes(root, pass_, s["id"], folder), alive,
                                    as_of=as_of))
    return n


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
    sd = ap.add_mutually_exclusive_group()
    sd.add_argument("--seed", type=int, help="render with this seed")
    sd.add_argument("--same-seed", action="store_true",
                    help="keep the shotlist's seed on a redo")
    sd.add_argument("--new-seed", action="store_true",
                    help="pick a fresh seed even on a first render")
    ap.add_argument("--lora", action="append", metavar="NAME[:STRENGTH]",
                    help="LoRA for this run; repeat to stack them; 'none' for no LoRA. "
                         "Replaces the shotlist's and overrides.json's LoRAs")
    ap.add_argument("--model", help="H3 unet file name; overrides the shotlist")
    ap.add_argument("--steps", type=int, help="sampler steps; overrides the shotlist")
    ap.add_argument("--note", default="", help="free text stored in each take's sidecar")
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
    pass_ = "proxy" if args.proxy else "final"
    folder = args.subfolder
    shown_folder = folder or T.pass_subfolder(pass_)

    roots = [os.path.abspath(p) for p in args.projects]
    if args.all:
        roots += sorted(p for p in glob.glob(os.path.join(os.path.abspath(args.all), "ep*"))
                        if os.path.isdir(os.path.join(p, "shotlist")))
    if not roots:
        ap.error("give one or more episode folders, or --all DIR")

    loras = [l for spec in args.lora for l in parse_lora(spec)] if args.lora else None
    template = RenderRequest(
        shot_id="", take=args.take, redo=args.redo, seed=args.seed,
        seed_mode="new" if args.new_seed else "same" if args.same_seed else "auto",
        model=args.model or None, loras=loras, steps=args.steps, note=args.note)
    only = {s.strip() for s in args.only.split(",")} if args.only else None

    wf = find_workflow(args.workflow)
    base = load_graph(wf)
    node_of(base, LOADER), node_of(base, SAVER)       # fail early on the wrong workflow

    comfy = Comfy(args.comfy)
    if not args.dry_run:
        swept = sweep(comfy, roots, pass_, folder)
        if swept:
            print(f"\n  marked {swept} take(s) failed: queued, but ComfyUI no longer has the job")

    plans, total_frames = [], 0
    for root in roots:
        try:
            jobs = plan_episode(root, pass_, default=template, folder=folder, only=only)
        except FileNotFoundError as e:
            print(f"  ! {e}")
            continue
        todo = [j for j in jobs if j.runs]
        busy = sum(1 for j in jobs if j.action == "busy")
        plans.append((root, jobs))
        total_frames += sum(j.frames for j in todo)
        print(f"\n  {os.path.basename(root)}  ·  {len(todo)} to render, "
              f"{len(jobs) - len(todo) - busy} done"
              + (f", {busy} already queued" if busy else "")
              + f"  ·  {pass_}  ->  {shown_folder}/")
        for key, vals in (("model", sorted({j.model for j in todo if j.model})),
                          ("lora", sorted({lora_label(j.loras) for j in todo}))):
            if vals:
                print(f"    {key} {', '.join(vals)}")
            if len(vals) > 1:
                print(f"    ! {len(vals)} different {key}s here — ComfyUI reloads on "
                      f"every change, so expect a pause at those shots")
        for j in todo:
            if j.override_stale:
                print(f"    ! {j.id}: overrides.json was written against an older build of "
                      f"this shot ({', '.join(j.overridden)} still applied)")
        if args.list or args.dry_run:
            for j in jobs:
                extra = f"  seed {j.seed} ({j.seed_source})" if j.runs else ""
                ov = f"  override: {','.join(j.overridden)}" if j.overridden and j.runs else ""
                print(f"    [{j.action:>9}] #{j.index:<3} {j.id:<8} t{j.take:02d}"
                      f"  {j.frames:>4}f  {j.shot.get('audio_policy', '')}{extra}{ov}")
    n_todo = sum(1 for _, js in plans for j in js if j.runs)
    print(f"\n  workflow {wf}\n  {n_todo} shot(s), {total_frames / 24:.1f}s of video"
          f" · comfy {args.comfy}\n")

    gkw = dict(panel_mode=args.panel_mode, save_frames=args.save_frames,
               review_copy=not args.no_review_copy, strip_meta=args.strip_meta)
    if args.list:
        return 0
    if args.dry_run:
        # Nothing is reserved or written in the episode: the graph points at
        # where the take WOULD go.
        for root, jobs in plans:
            for j in (j for j in jobs if j.runs):
                take = T.Take(j.id, j.take, pass_,
                              T.take_paths(root, pass_, j.id, j.take, folder))
                g = graph_for(base, j, take, **gkw)
                out = os.path.join(os.getcwd(), "h3render_graph.json")
                with open(out, "w", encoding="utf-8") as fh:
                    json.dump(g, fh, indent=2)
                print(f"  wrote {out} ({j.id} of {os.path.basename(root)})")
                return 0
        return 0
    if not n_todo:
        print("  nothing to do — pass --redo to render new takes.\n")
        return 0

    try:
        comfy.ping()
    except Exception as e:
        print(f"  !! cannot reach ComfyUI at {args.comfy}: {e}")
        return 1

    done = failed = 0
    failures = []
    t_start = time.time()
    frames_done = 0
    current = None
    try:
        for root, jobs in plans:
            ep = os.path.basename(root)
            for j in (j for j in jobs if j.runs):
                t0 = time.time()
                current = take = start_job(j)
                label = f"{ep}/{j.id} t{take.take:02d}"
                print(f"  .. {label}  ({j.frames}f, {j.shot.get('audio_policy', '')}, "
                      f"seed {j.seed} {j.seed_source})", flush=True)
                try:
                    pid = comfy.queue(graph_for(base, j, take, **gkw))
                    mark_queued(take, pid)
                    comfy.wait(pid, args.timeout)
                    if finish_job(take) != "ok":
                        raise RuntimeError((take.sidecar or {}).get("save_notes")
                                           or f"{take.paths.mp4} is missing")
                    current = None
                    done += 1
                    frames_done += j.frames
                    rate = (time.time() - t_start) / max(frames_done, 1)
                    left = (total_frames - frames_done) * rate
                    print(f"  -> {take.paths.mp4}  [{fmt(time.time() - t0)}, ~{fmt(left)} left]",
                          flush=True)
                except TimeoutError as e:
                    # Still in ComfyUI's hands: leave it queued; a later run sweeps it.
                    current = None
                    failed += 1
                    failures.append(label)
                    print(f"  !! {label}: {e} (left queued in ComfyUI)", flush=True)
                except Exception as e:
                    current = None
                    mark_failed(take, str(e)[:800])
                    failed += 1
                    failures.append(label)
                    print(f"  !! {label}: {e}", flush=True)
                    if args.stop_on_error:
                        raise KeyboardInterrupt
    except KeyboardInterrupt:
        print("\n  stopping — interrupting the running ComfyUI job")
        comfy.interrupt()
        if current is not None:
            mark_failed(current, "interrupted")

    print(f"\n  done in {fmt(time.time() - t_start)}: {done} rendered"
          + (f", {failed} failed: {', '.join(failures)}" if failed else ""))
    print("  assemble with: python h3assemble.py -o <episode>"
          + (" --shotlist shotlist/shotlist_proxy.json" if args.proxy else "")
          + "\n")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
