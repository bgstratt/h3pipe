#!/usr/bin/env python3
"""
h3render.py — queue every shot of one or more episodes on a local ComfyUI,
each with its video target's workflow, and wait for each render.

    python h3render.py path/to/project/ep05
    python h3render.py Shows\\ep05 --proxy
    python h3render.py Shows\\ep05 --only sh040,sh050 --redo
    python h3render.py Shows\\ep05 --only sh040 --redo --lora a.safetensors --lora b.safetensors:0.6
    python h3render.py --all Shows --proxy          # every ep* folder
    python h3render.py Shows\\ep05 --list           # what would run
    python h3render.py Shows\\ep05 --dry-run        # write the API graph, queue nothing
    python h3render.py Shows\\ep05 --proxy --only sh040 --target ltx2 --dry-run --check-nodes

This is the command-line twin of the targets' workflows (H3's is
H3_Ref2VA_Shotlist_v1.json). For H3 it points Shot List Loader at a frozen
one-shot shotlist for each take; for a target with no loader (ltx2) it
patches the values straight into the workflow's widgets. Save Shot writes the
take either way, and one shot is queued at a time. The work of deciding what
a take renders lives in h3jobs.py, which the editor shares.

Targets
    Each shot renders on the target its build chose (shotlist.json, or
    shotlist.<target>.json in an episode that mixes targets), unless
    overrides.json retargets it (h3.py override --target) or --target does
    for this run: the shot's IR (shotlist/shots.json) is then compiled for that
    target now, without a rebuild. Keyframes a target reads (ltx2's
    refs/shots/<shot>/first.png, last.png) are uploaded into ComfyUI's
    input/h3pipe/ folder first, and so is ltx2_ingredients' reference sheet,
    composed from the shot's refs (it needs PIL in this Python) and kept in
    the take as <shot>_tNN_refsheet.png. A dry run composes nothing.

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
    Per target. --workflow replaces the workflow of --target's target (else
    the series target's). Otherwise, for H3:
    $H3_WORKFLOW, else H3_Ref2VA_Shotlist_v1.json as saved in
    the running ComfyUI (its user workflows, fetched over the API, so the graph
    always matches that ComfyUI's node versions), else $COMFYUI_PATH's
    workflows folder, else the copy in this repo (the target's, at
    targets/video/minimax_h3_ref2va/workflow.json). It prints which. Either the
    normal UI save or an API export (Workflow > Export (API)) works; the UI save
    is converted here. If ComfyUI rejects a converted graph, export the API
    version once and pass it with --workflow.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import h3jobs as J  # noqa: E402
import h3takes as T  # noqa: E402
import targets as TG  # noqa: E402
# Re-exported: kreagen imports load_graph from here.
from h3jobs import (  # noqa: E402,F401
    LOADER, SAVER, LORA, UNET, WORKFLOW_NAME, Comfy, RenderRequest, find_workflow,
    finish_job, graph_for, load_graph, load_shotlist, mark_failed, mark_queued, node_of,
    parse_lora, plan_episode, resolve_workflow, start_job, ui_to_api)


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
            shots = J.episode_shots(root, pass_)
        except FileNotFoundError:
            continue
        for d, i in shots:
            n += len(T.sweep_queued(T.list_takes(root, pass_, d["shots"][i]["id"], folder),
                                    alive, as_of=as_of))
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
    ap.add_argument("--target", metavar="TARGET",
                    help="render on this video target for this run (e.g. ltx2): the shot's "
                         "IR is compiled for it now, no rebuild needed. overrides.json's "
                         "`target` (h3.py override --target) does the same for every run")
    ap.add_argument("--check-nodes", action="store_true",
                    help="with --dry-run: ask the running ComfyUI (/object_info) whether it "
                         "knows every node class and input of the graph")
    ap.add_argument("--allow-missing-refs", action="store_true",
                    help="render shots even when references are missing: a missing picture "
                         "becomes flat grey, a missing voice/recording no audio reference "
                         "(without it such shots are skipped)")
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
    if args.target:
        try:
            J.check_video_target(args.target)
        except Exception as e:
            ap.error(str(e))
    template = RenderRequest(
        shot_id="", take=args.take, redo=args.redo, seed=args.seed,
        seed_mode="new" if args.new_seed else "same" if args.same_seed else "auto",
        model=args.model or None, loras=loras, steps=args.steps, note=args.note,
        allow_missing_refs=args.allow_missing_refs, target=args.target)
    only = {s.strip() for s in args.only.split(",")} if args.only else None

    # Each job renders with its own target's workflow. --workflow replaces the
    # workflow of --target's target if given, else the series target's.
    workflows: dict = {}
    wf_target = args.target or None

    def base_for(target_id: str):
        if target_id not in workflows:
            t = TG.load_target(target_id, "video")
            b = t.binding
            explicit = args.workflow if (args.workflow and (
                target_id == wf_target or (wf_target is None and target_id == default_id))) \
                else None
            g, where = J.target_workflow(t, explicit, args.comfy)
            if b.loader_class:
                node_of(g, b.loader_class)                # fail early on the wrong workflow
            if not (b.saver.get("replace") and any(
                    v["class_type"] == b.saver["replace"]["class_type"] for v in g.values())):
                node_of(g, b.saver_class)
            workflows[target_id] = (g, where)
        return workflows[target_id]

    comfy = Comfy(args.comfy)
    if not args.dry_run:
        swept = sweep(comfy, roots, pass_, folder)
        if swept:
            print(f"\n  marked {swept} take(s) failed: queued, but ComfyUI no longer has the job")

    plans, total_frames = [], 0
    default_id = TG.DEFAULT_VIDEO_TARGET
    for root in roots:
        try:
            jobs = plan_episode(root, pass_, default=template, folder=folder, only=only)
            default_id = J.shotlist_target(load_shotlist(root, pass_)).id \
                if root == roots[0] else default_id
        except FileNotFoundError as e:
            print(f"  ! {e}")
            continue
        todo = [j for j in jobs if j.runs]
        busy = sum(1 for j in jobs if j.action == "busy")
        blocked = [j for j in jobs if j.action == "blocked"]
        errors = [j for j in jobs if j.action == "error"]
        plans.append((root, jobs))
        total_frames += sum(j.frames for j in todo)
        print(f"\n  {os.path.basename(root)}  ·  {len(todo)} to render, "
              f"{len(jobs) - len(todo) - busy - len(blocked) - len(errors)} done"
              + (f", {busy} already queued" if busy else "")
              + (f", {len(blocked)} blocked (missing refs)" if blocked else "")
              + (f", {len(errors)} can't be planned" if errors else "")
              + f"  ·  {pass_}  ->  {shown_folder}/")
        for key, vals in (("target", sorted({j.target for j in todo})),
                          ("model", sorted({j.model for j in todo if j.model})),
                          ("lora", sorted({lora_label(j.loras) for j in todo}))):
            if vals:
                print(f"    {key} {', '.join(vals)}")
            if len(vals) > 1:
                print(f"    ! {len(vals)} different {key}s here — ComfyUI reloads on "
                      f"every change, so expect a pause at those shots")
        for j in blocked[:8]:
            print(f"    ! {j.id}: " + (j.blocked_reason() if j.no_anyway
                                       else f"missing {j.missing_note()}"))
        if any(not j.no_anyway for j in blocked):
            print("    ! make the refs (h3.py refs), or --allow-missing-refs to render "
                  "those shots with flat grey stand-ins")
        for j in errors:
            print(f"    !! {j.error}")
        for j in todo:
            if j.retargeted:
                print(f"    ~ {j.id}: retargeted {j.built_target} -> {j.target}")
            for n in j.notes:
                print(f"    ~ {j.id}: {n}")
            if j.missing:
                how = ("prompt rewritten without them" if j.missing_mode == "recompiled"
                       else "grey stand-ins" + (f" ({j.missing_why})" if j.missing_why else ""))
                print(f"    ~ {j.id}: rendering WITHOUT {', '.join(r['slot'] for r in j.missing)}"
                      f": {how}")
            if j.override_stale:
                print(f"    ! {j.id}: overrides.json was written against an older build of "
                      f"this shot ({', '.join(j.overridden)} still applied)")
        if args.list or args.dry_run:
            for j in jobs:
                extra = f"  seed {j.seed} ({j.seed_source})" if j.runs else ""
                ov = f"  override: {','.join(j.overridden)}" if j.overridden and j.runs else ""
                print(f"    [{j.action:>9}] #{j.index:<3} {j.id:<8} t{j.take:02d}"
                      f"  {j.frames:>4}f  {j.target:<18} {j.shot.get('audio_policy', '')}"
                      f"{extra}{ov}")
    n_todo = sum(1 for _, js in plans for j in js if j.runs)
    need = sorted({j.target for _, js in plans for j in js if j.runs}) or [default_id]
    try:
        for tid in need:
            print(f"\n  workflow {tid}: {base_for(tid)[1]}")
    except Exception as e:
        print(f"\n  !! {e}")
        return 1
    fps = next((j.fps for _, js in plans for j in js if j.runs), 24.0)
    print(f"  {n_todo} shot(s), {total_frames / fps:.1f}s of video · comfy {args.comfy}\n")

    gkw = dict(panel_mode=args.panel_mode, save_frames=args.save_frames,
               review_copy=not args.no_review_copy, strip_meta=args.strip_meta)
    if args.list:
        return 0
    if args.dry_run:
        # Nothing is reserved, written or uploaded: the graph points at where
        # the take WOULD go, and at the names its keyframes WOULD get.
        for root, jobs in plans:
            for j in (j for j in jobs if j.runs):
                take = T.Take(j.id, j.take, pass_,
                              T.take_paths(root, pass_, j.id, j.take, folder))
                # --check-nodes: ComfyUI is asked (not written to) whether a
                # `dur: model` shot's duration head is installed
                J.stage_inputs(j, probe=comfy if args.check_nodes else None)
                if j.duration_note:
                    print(f"    ~ {j.id}: {j.duration_note}")
                g = graph_for(base_for(j.target)[0], j, take, **gkw)
                out = os.path.join(os.getcwd(), "h3render_graph.json")
                with open(out, "w", encoding="utf-8") as fh:
                    json.dump(g, fh, indent=2)
                print(f"  wrote {out} ({j.id} of {os.path.basename(root)}, {j.target}"
                      + (f", inputs {j.inputs}" if j.inputs else "") + ")")
                if args.check_nodes:
                    try:
                        info = comfy.object_info()
                    except Exception as e:
                        print(f"  !! cannot read /object_info from {args.comfy}: {e}")
                        return 1
                    problems = J.check_graph(g, info)
                    print(f"  {len(g)} nodes, {len({v['class_type'] for v in g.values()})} "
                          f"classes checked against {args.comfy}/object_info: "
                          + ("all known, every link and required input in place"
                             if not problems else f"{len(problems)} problem(s)"))
                    for p in problems:
                        print(f"    ! {p}")
                    return 1 if problems else 0
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
                label = f"{ep}/{j.id}"
                try:
                    J.stage_inputs(j, comfy)
                except Exception as e:
                    failed += 1
                    failures.append(label)
                    print(f"  !! {label}: couldn't stage its input images in ComfyUI: {e}",
                          flush=True)
                    continue
                current = take = start_job(j)
                label = f"{ep}/{j.id} t{take.take:02d}"
                frames = "predicted" if j.length_source == "predicted" else f"{j.frames}f"
                print(f"  .. {label}  ({frames}, {j.target}, {j.shot.get('audio_policy', '')}, "
                      f"seed {j.seed} {j.seed_source})", flush=True)
                if j.duration_note:
                    print(f"     {j.duration_note}", flush=True)
                try:
                    pid = comfy.queue(graph_for(base_for(j.target)[0], j, take, **gkw))
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
