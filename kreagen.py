#!/usr/bin/env python3
"""
kreagen.py — generate every missing reference image on a local ComfyUI, as
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

The image target (--target, else the episode's: the editor's choice, the series
config's refs.target, else krea2) decides the graph, and the banner names it. For
krea2 it is krea2_refs_t2i.json (the copy saved in the running ComfyUI, else
this repo's), or a built-in krea2 turbo graph; any other target uses its own
workflow (targets/image/<id>/). Its SaveImage is replaced by the
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


def collect(s: R.Series, todo: list | None, only: set[str] | None,
            voices: bool = False) -> list[dict]:
    """One entry per ref to consider, most-blocking first. `todo` is
    refs_todo.json (None: every image ref in the series config). `voices`
    adds the voice refs (an audio target generates them: --voices)."""
    refs = [r for r in R.series_refs(s) if not r.is_audio]
    heard = [r for r in R.series_refs(s) if r.is_audio] if voices else []
    usage = R.used_by(s, refs + heard)
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
    picked += [(r, len(usage[r.id]["final"])) for r in heard]
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
            f"voice:{r.subject}" if r.is_audio else \
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
    None. A voice ref takes the audio target (--voice-target) and its own
    length (--voice-seconds); the image flags are not its."""
    if j["ref"].is_audio:
        return R.GenRequest(ref=j["ref"].id, steps=args.steps, cfg=args.cfg,
                            negative=negative, target=args.voice_target or None,
                            seconds=args.voice_seconds)
    loras = ([{"name": args.lora, "strength": args.lora_strength}] if args.lora else None)
    return R.GenRequest(ref=j["ref"].id, model=args.unet or None, loras=loras,
                        steps=args.steps, cfg=args.cfg, negative=negative,
                        view_size=parse_size(args.view_size, R.VIEW_SIZE),
                        target=args.target or None)


def parse_from_take(spec: str) -> tuple[str, int, float, float]:
    """SHOT:TAKE:START-END -> (shot, take, start, end) in seconds
    (--from-take). ValueError if malformed."""
    m = re.fullmatch(r"\s*([^:]+):[tT]?(\d+):(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*", spec or "")
    if not m:
        raise ValueError(f"--from-take takes SHOT:TAKE:START-END in seconds "
                         f"(e.g. sh020:3:1.2-6.4), not {spec!r}")
    return m.group(1), int(m.group(2)), float(m.group(3)), float(m.group(4))


def from_take(s: R.Series, ep: str, jobs: list[dict], spec: str, pass_: str,
              pick: bool | None) -> int:
    """kreagen --from-take: cut a span out of a shot take's sound into a new
    voice candidate. Exactly one voice ref must be selected (--only)."""
    try:
        shot, take, start, end = parse_from_take(spec)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    voices = [j for j in jobs if j["ref"].is_audio]
    if len(voices) != 1:
        which = ", ".join(j["ref"].id for j in voices) or "none"
        print(f"error: --from-take makes one voice sample: narrow the run to one voice ref "
              f"with --only (selected: {which})", file=sys.stderr)
        return 1
    ref = voices[0]["ref"]
    try:
        res = R.voice_from_take(s, ref, shot, take, pass_, start, end, pick=pick,
                                note=f"{start:g}-{end:g}s of {shot} {pass_} t{take:02d}")
    except (R.RefError, R.UnknownRef, R.NotUsable, R.FfmpegMissing) as e:
        print(f"  !! {ref.id}: {e}")
        return 1
    print(f"  {ref.id}: t{res.take.take:02d} <- {start:g}-{end:g}s of {shot} {pass_} "
          f"t{take:02d}")
    if res.picked:
        print(f"  -> picked: {R.ep_rel(ep, ref.file)}")
    return 0


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
    ap.add_argument("--voices", action="store_true",
                    help="also generate the voice refs (voice:<subject>) on the audio "
                         "target; off by default, so an image run is unchanged")
    ap.add_argument("--voice-target", default="",
                    help="audio target for the voice refs (default: the episode's, then "
                         "series.json's refs.voice_target, then ltx2_voice)")
    ap.add_argument("--voice-seconds", type=float, default=None,
                    help="how long a generated voice sample is (default: the target's)")
    ap.add_argument("--from-take", metavar="SHOT:TAKE:START-END", default="",
                    help="no model: cut START-END seconds out of a rendered take's sound "
                         "into a voice candidate (narrow to one voice ref with --only)")
    ap.add_argument("--pass", dest="pass_", choices=("final", "proxy"), default="proxy",
                    help="which pass --from-take reads (default proxy)")
    ap.add_argument("--clear", metavar="REF[:VIEW]", action="append",
                    help="unpick a ref (e.g. location:kitchen, subject:ada:02_side): its file "
                         "goes, the takes stay, and nothing re-picks it until you pick; "
                         "repeatable")
    ap.add_argument("--discard", metavar="REF[:VIEW]:TAKE", action="append",
                    help="move a candidate to refs/_takes/_trash/ (e.g. location:kitchen:3, "
                         "subject:ada:02_side:2, shot:sh020:first:1); nothing is deleted, and "
                         "if it was the pick the ref is cleared as --clear does; repeatable")
    ap.add_argument("--yes", action="store_true",
                    help="don't stop when --clear would remove a live file other episodes "
                         "read (they are blocked until something is picked again)")
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

    voices = bool(args.voices or args.from_take)
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
        shared = R.shared_context(ep)
        for spec in args.clear:
            rid, view = spec, None
            m = re.fullmatch(r"(subject:[^:]+):(\d\d_[a-z]+)", spec)
            if m:
                rid, view = m.group(1), m.group(2)
            try:
                ref = R.find_ref(s, rid)
            except (R.RefError, R.UnknownRef) as e:
                print(f"  !! {spec}: {e}")
                failed += 1
                continue
            # clearing removes the live file, and with `../refs/...` paths that
            # file is one every other episode of the show reads
            others = shared["shared"].get(R._real_path(ref.file)) if ref.file else None
            if others and not args.yes:
                live = ref.file and os.path.isfile(ref.file)
                owner = shared["owner"].get(R.T.file_sha1(ref.file)) if live else None
                print(f"  !! {spec}: {os.path.relpath(ref.file, ep)} is read by "
                      f"{', '.join(others)}. Clearing removes it and blocks them until "
                      f"something is picked."
                      + (f" {owner} can re-pick its own take." if owner
                         else " Nothing has a candidate for it: it can't be got back.")
                      + " Pass --yes to do it anyway.")
                failed += 1
                continue
            try:
                res = R.clear_pick(s, ref, view)
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
    jobs = collect(s, todo, only, voices)
    if args.from_take:
        return from_take(s, ep, jobs, args.from_take, args.pass_,
                         True if args.pick else None)

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
    # The banner names the graph the refs actually render with: the image
    # target --target names, else the episode's (the editor's choice, the
    # series config's refs.target, else krea2). A ref's own override can
    # still pick another; its job line says so.
    graphs: dict = {}
    try:
        tgt = R.TG.load_target(args.target or R.image_defaults(s)["target"], "image")
    except (R.RefError, R.TG.TargetError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if tgt.id == R.TG.DEFAULT_IMAGE_TARGET:
        graph = wf if wf else f"built-in ({UNET})"
    else:
        try:
            graphs[tgt.id], twf = R.resolve_workflow(args.comfy, None, tgt)
            graph = twf
        except Exception as e:
            graph = f"{tgt.id}: its workflow could not be read ({e})"

    voice_line = ""
    if voices:
        try:
            vt = R.TG.load_target(args.voice_target or R.image_defaults(s)["voice_target"],
                                  "audio")
            voice_line = f"  voices on {vt.id}\n"
        except (R.RefError, R.TG.TargetError, ValueError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
    print(f"\n  {len(jobs)} asset(s) · comfy {args.comfy} · "
          f"{args.steps or 'preset'} steps · cfg "
          f"{args.cfg if args.cfg is not None else 'preset'} · {tgt.id}\n"
          f"  graph {graph}\n" + voice_line +
          f"  {'-' * 62}")
    todo_now = []
    for j in jobs:
        r = j["ref"]
        live = r.file or (R.ref_file(r.home, R.voice_sample_path(r)) if r.is_audio else None)
        j["out"] = os.path.join(root, os.path.relpath(live, ep)) if live else r.id
        have = bool(r.file) and os.path.isfile(r.file)
        j["had"] = have
        mark = "have" if have and not args.redo else ("redo" if have else " -- ")
        if r.is_audio:
            size = f"{args.voice_seconds:g}s" if args.voice_seconds else "preset s"
        else:
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
                    R.stage_voice_reference(s, job, comfy)
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
