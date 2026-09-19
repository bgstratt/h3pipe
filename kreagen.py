#!/usr/bin/env python3
"""
kreagen.py — generate every missing H3 reference image on a local ComfyUI, as
ref takes (h3refs.py), and put each one at the path the series config names.

    python3 kreagen.py --project-root .
    python3 kreagen.py --project-root . --only sam,core_wide --redo
    python3 kreagen.py --project-root . --all            # every ref in the series config
    python3 kreagen.py --project-root . --list
    python3 kreagen.py --project-root . --dry-run
    python3 kreagen.py --project-root . --clear location:kitchen     # unpick a ref
    python3 kreagen.py --project-root . --discard subject:ada:02_side:3
                                                     # a candidate to refs/_takes/_trash/

Reads  <project_root>/refs_todo.json (what this episode uses; --all: every ref)
       and series.json, the series config (in the episode folder or its parent)
Writes refs/_takes/<ref>/…_tNN.png + .json    every candidate, kept
       refs/_bg/<location>.png          1344x768   }  the picked take, at the
       refs/props/<name>.png            1024x1024  }  path the series config names
       refs/<char>/<char>_sheet_4panel.png   4096x1024
       refs/_picks.json                 which take is live

A ref with no file yet gets its first successful take picked, so a plain run
ends as it always did: every image at the path series.json names. --redo makes a new take
of refs that already have a file, and leaves the live file alone unless you
also pass --pick (pick in the editor's Refs tab otherwise). Nothing is ever
overwritten: every take stays in refs/_takes.

Character sheets are NOT generated as one 4:1 strip — a 4096x1024 canvas is
far outside any diffusion model's training distribution and comes back as
smeared repetition. Each of the four views is generated square and separately,
then stitched by mksheet.py when all four are picked. The four views of one
character share a seed so they stay on model. The character is the series config
subject whose `sheet` is the path, whatever the file is called.

Voice samples are skipped; they are not images.

The graph is krea2_refs_t2i.json (the copy saved in the running ComfyUI, else
this repo's), or a built-in krea2 turbo graph. Its SaveImage is replaced by the
node pack's H3SaveRefTake, which writes the take and closes its sidecar; on a
ComfyUI whose node pack predates that node, SaveImage stays and kreagen fetches
the image over HTTP. When a LoRA is given, the text encoder reads the LoRA's
CLIP rather than the raw CLIPLoader output, so strength_clip is not inert;
--no-lora-clip reproduces the original wiring of the built-in graph. The model
file names are the krea2 image target's (targets/image/krea2/target.json, re-exported
by h3refs) — point them at whatever image model you have, and pass --lora/--unet to
override per run.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import h3jobs as J  # noqa: E402
import h3refs as R  # noqa: E402
# The graph code and wording moved to h3refs; re-exported under the old names.
from h3refs import (CFG, CLIP, CLIPTYPE, LORA, LORA_C, LORA_M, PREFIX,  # noqa: E402,F401
                    REFS_WORKFLOW, SAMPLER, SCHED, STEPS, UNET, VAE, VIEW_TMPL, VIEWS,
                    build_graph, parse_size, patch_workflow, seed_for)


def _norm(p: str) -> str:
    return os.path.normcase(os.path.normpath(p))


def collect(s: R.Series, todo: list | None, only: set[str] | None) -> list[dict]:
    """One entry per ref to consider, most-blocking first. `todo` is
    refs_todo.json (None: every image ref in the series config)."""
    refs = [r for r in R.series_refs(s) if not r.is_audio]
    usage = R.used_by(s, refs)
    by_path = {_norm(r.path): r for r in refs if r.path}
    picked: list[tuple[R.Ref, int]] = []
    if todo is None:
        picked = [(r, len(usage[r.id]["final"])) for r in refs]
    else:
        for item in todo:
            if item["kind"] == "voice sample":
                continue
            r = by_path.get(_norm(item["path"]))
            if r is None:
                print(f"  ! nothing in series.json names {item['path']} — skipping it")
                continue
            picked.append((r, len(item.get("blocks_shots", []))))
    out = []
    for r, blocks in picked:
        why = R.can_generate(s, r)
        if why:
            if r.kind == "character":
                print(f"  ! no design in series.json for '{r.subject}' — skipping {r.path}")
            else:
                print(f"  ! {why} — skipping {r.path or r.id}")
            continue
        name = r.subject if r.kind == "character" else \
            os.path.splitext(os.path.basename(r.path))[0] if r.path else r.id
        out.append({"ref": r, "name": name, "blocks": blocks})
    out.sort(key=lambda j: -j["blocks"])            # most-blocking first
    if only:
        out = [j for j in out if j["name"] in only or j["ref"].id in only
               or any(k in (j["ref"].path or "") for k in only)]
    return out


def request(j: dict, args, negative: str | None) -> R.GenRequest:
    """One ref's generate request. `negative` is --negative-file's text (it
    beats negative.txt, the series config and the preset for this run), or
    None."""
    loras = ([{"name": args.lora, "strength": args.lora_strength}] if args.lora else None)
    return R.GenRequest(ref=j["ref"].id, model=args.unet or None, loras=loras,
                        steps=args.steps, cfg=args.cfg, negative=negative,
                        view_size=parse_size(args.view_size, R.VIEW_SIZE),
                        target=args.target or None)


def job_name(j: dict, job: R.GenJob) -> str:
    return f"{j['name']}:{job.view}" if job.view else j["name"]


def parse_discard(spec: str) -> tuple[str, str | None, int]:
    """REF[:VIEW]:TAKE -> (ref id, view or None, take). ValueError if malformed."""
    rid, _, tk = spec.rpartition(":")
    if not rid or not re.fullmatch(r"[tT]?\d+", tk):
        raise ValueError(f"--discard takes REF[:VIEW]:TAKE (e.g. location:kitchen:3), "
                         f"not {spec!r}")
    view = None
    m = re.fullmatch(r"(subject:[^:]+):(\d\d_[a-z]+)", rid)
    if m:
        rid, view = m.group(1), m.group(2)
    return rid, view, int(tk.lstrip("tT"))


def discard(s, ep: str, specs: list[str]) -> int:
    """kreagen --discard: move each candidate to refs/_takes/_trash/."""
    failed = 0
    for spec in specs:
        try:
            rid, view, take = parse_discard(spec)
            res = R.discard_take(s, R.find_ref(s, rid), view, take)
        except (ValueError, R.UnknownRef, R.T.StillQueued) as e:
            print(f"  !! {spec}: {e}")
            failed += 1
            continue
        where = os.path.relpath(os.path.dirname(res.moved[0]), ep) if res.moved else "the trash"
        print(f"  {spec}: moved {len(res.moved)} file(s) to {where}"
              + ("; it was the pick, so the ref is cleared" if res.cleared else ""))
    return 1 if failed else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project-root", default=".",
                    help="episode folder holding refs_todo.json (series.json here or "
                         "in its parent)")
    ap.add_argument("--comfy", default="http://127.0.0.1:8188",
                    help="ComfyUI address (default %(default)s)")
    ap.add_argument("--only", help="comma-separated filter: asset names (sam, core_wide), "
                                   "ref ids (subject:sam) or path fragments")
    ap.add_argument("--all", action="store_true",
                    help="every ref in series.json, not only what refs_todo.json lists "
                         "(no build needed)")
    ap.add_argument("--redo", action="store_true",
                    help="make a new take of refs already on disk (the live file is "
                         "kept unless --pick)")
    ap.add_argument("--pick", action="store_true",
                    help="put every new take live, even over an existing file")
    ap.add_argument("--workflow", help=f"reference-image workflow to drive "
                    f"(default: $KREA_WORKFLOW, else {REFS_WORKFLOW} as saved in the "
                    f"running ComfyUI, else this repo's copy, else the built-in graph)")
    ap.add_argument("--no-workflow", action="store_true",
                    help="ignore any workflow file and use the built-in graph")
    ap.add_argument("--list", action="store_true", help="show the job list and exit")
    ap.add_argument("--dry-run", action="store_true", help="print prompts, queue nothing")
    ap.add_argument("--steps", type=int, default=None,
                    help=f"sampler steps (default: the ref's override, else {STEPS}; "
                         f"match a step-distilled LoRA)")
    ap.add_argument("--cfg", type=float, default=None,
                    help="1.0 = turbo, no guidance (negative prompt inert). >1 enables it. "
                         "Default: the image target's preset (krea2: 1.0)")
    ap.add_argument("--target", default="",
                    help="the image target (z_image_turbo, flux2_klein, flux_kontext, ...; "
                         "default: each ref's override, the episode's choice, the series "
                         "config's refs.target, else krea2)")
    ap.add_argument("--clear", metavar="REF[:VIEW]", action="append",
                    help="unpick a ref (e.g. location:kitchen, subject:ada:02_side): its file "
                         "goes, the takes stay, and nothing re-picks it until you pick; "
                         "repeatable")
    ap.add_argument("--discard", metavar="REF[:VIEW]:TAKE", action="append",
                    help="move a candidate to refs/_takes/_trash/ (e.g. location:kitchen:3, "
                         "subject:ada:02_side:2, shot:sh020:first:1); nothing is deleted, and "
                         "if it was the pick the ref is cleared as --clear does; repeatable")
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
    ep = os.path.abspath(root)
    try:
        s = R.load_series(ep)
    except FileNotFoundError:
        print(f"error: {os.path.join(root, 'series.json')} not found (nor in the parent "
              f"folder).", file=sys.stderr)
        return 1
    except ValueError as e:
        print(f"error: series.json: {e}", file=sys.stderr)
        return 1
    if args.discard:
        return discard(s, ep, args.discard)

    todo = None
    if not args.all and not args.clear:
        todo_p = os.path.join(root, "refs_todo.json")
        if not os.path.isfile(todo_p):
            print(f"error: {todo_p} not found. Run h3build first (or pass --all to work "
                  f"from series.json alone).", file=sys.stderr)
            return 1
        with open(todo_p, encoding="utf-8") as fh:
            todo = json.load(fh)

    if args.clear:
        failed = 0
        for spec in args.clear:
            rid, view = spec, None
            m = re.fullmatch(r"(subject:[^:]+):(\d\d_[a-z]+)", spec)
            if m:
                rid, view = m.group(1), m.group(2)
            try:
                res = R.clear_pick(s, R.find_ref(s, rid), view)
            except (R.RefError, R.UnknownRef) as e:
                print(f"  !! {spec}: {e}")
                failed += 1
                continue
            print(f"  {spec}: cleared" + (f"; removed {os.path.relpath(res.removed[0], ep)}"
                                          if res.removed else "; there was no live file"))
        return 1 if failed else 0

    negative = None
    if args.negative_file:
        with open(args.negative_file, encoding="utf-8") as fh:
            negative = fh.read().strip()
        if args.cfg is not None and args.cfg <= 1.0:
            print("  ! --negative-file given but cfg is 1.0, where there is no guidance\n"
                  "    branch to apply it to, so it does nothing. Turbo checkpoints want\n"
                  "    cfg 1; for real suppression there use a NAG or negpip node in your\n"
                  "    own workflow. Otherwise try --cfg 1.5 --steps 16 on a model that\n"
                  "    expects guidance, or fold the exclusions into the positive text.\n")

    vw, vh = parse_size(args.view_size, R.VIEW_SIZE)
    only = {k.strip() for k in args.only.split(",")} if args.only else None
    jobs = collect(s, todo, only)

    base, wf = None, ""
    if not args.no_workflow:
        try:
            # Same lookup as h3render: the copy saved in the running ComfyUI
            # first, so the graph matches its node versions. Keep that copy
            # LoRA-free (experiment under another name): references must follow
            # series.json's look, and --lora is how a run adds one.
            base, wf = R.resolve_workflow(args.comfy, args.workflow)
        except Exception as e:
            print(f"  ! {REFS_WORKFLOW} could not be read ({e}) — using the built-in graph")
            base, wf = None, ""

    print(f"\n  {len(jobs)} asset(s) · comfy {args.comfy} · "
          f"{args.steps or 'preset'} steps · cfg "
          f"{args.cfg if args.cfg is not None else 'preset'}"
          f"{' · ' + args.target if args.target else ''}\n"
          f"  graph {wf if wf else 'built-in (' + UNET + ')'}\n"
          f"  {'-' * 62}")
    todo_now = []
    for j in jobs:
        r = j["ref"]
        j["out"] = os.path.join(root, os.path.relpath(r.file, ep)) if r.file else r.id
        have = bool(r.file) and os.path.isfile(r.file)
        j["had"] = have
        mark = "have" if have and not args.redo else ("redo" if have else " -- ")
        w, h = R.gen_size(r, (vw, vh))
        size = f"{vw}x{vh} x4" if r.has_views else f"{w}x{h}"
        print(f"  [{mark}] {j['out']:<44} {size:>12}  blocks {j['blocks']}")
        if not have or args.redo:
            todo_now.append(j)
    print()

    if args.list:
        return 0
    if args.dry_run:
        for j in todo_now:
            for job in R.plan_generate(s, request(j, args, negative)):
                seed = (f"{job.seed}" if job.seed_source != "new"
                        else "new (drawn when queued)")
                print(f"--- {job_name(j, job)}  seed {seed}\n{job.prompt}\n")
        return 0
    if not todo_now:
        print("  nothing to do — pass --redo to make new takes.\n")
        return 0

    comfy = J.Comfy(args.comfy, client_id="kreagen")
    try:
        use_node = comfy.has_node(R.SAVER)
    except Exception as e:
        print(f"  !! can't reach ComfyUI at {args.comfy}: {e}\n")
        return 1
    if not use_node:
        print(f"  ! this ComfyUI has no {R.SAVER} node (update the h3pipe node pack and\n"
              f"    restart ComfyUI). Saving through SaveImage and fetching each image\n"
              f"    over HTTP instead.\n")

    listing = J.model_lister(comfy)
    cache = R.TG.modelid.temp_cache()
    graphs: dict = {}
    failed = []
    for j in todo_now:
        r = j["ref"]
        try:
            made = []
            for job in R.plan_generate(s, request(j, args, negative),
                                       ready=R.target_ready(listing)):
                print(f"  .. {job_name(j, job)}"
                      + ("" if job.is_krea2 else f"  ({job.target.id})"))
                blocked = R.resolve_job_models(job, listing, J.model_resolver(), cache)
                if blocked:
                    raise RuntimeError("model files not installed: " + "; ".join(
                        R.TG.missing_message(m) for m in blocked))
                g0 = base
                if not job.is_krea2:
                    if job.target.id not in graphs:
                        graphs[job.target.id], _ = R.resolve_workflow(args.comfy, None,
                                                                      job.target)
                    g0 = graphs[job.target.id]
                    R.stage_references(s, job, comfy)
                take = R.start_gen(s, job)
                try:
                    pid = comfy.queue(R.graph_for(g0, job, take, save_node=use_node,
                                                  lora_clip=not args.no_lora_clip))
                except Exception as e:
                    R.mark_failed(take, str(e)[:800])
                    raise
                R.mark_queued(take, pid)
                status = R.wait_take(comfy, take, pid, args.timeout)
                if status != "ok":
                    raise RuntimeError(f"t{take.take:02d} {status}: "
                                       f"{(take.sidecar or {}).get('save_notes', '')}"[:400])
                made.append((job, take))
            if j["had"] and not args.pick:
                print(f"  -> {len(made)} new take(s) in "
                      f"{os.path.relpath(R.takes_dir(r), ep)} ({j['out']} kept; pick in "
                      f"the editor, or pass --pick)")
                continue
            for job, take in made:
                res = R.pick_take(s, r, job.view, take.take, panel_height=args.panel_height,
                                  mksheet=args.mksheet)
                if res.report:
                    sys.stdout.write(res.report)
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
