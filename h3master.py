#!/usr/bin/env python3
"""
h3master.py — Phase 13e (docs/PLAN.md): an episode's master, in one action.

    python h3.py master <episode> [<episode> ...] [--check] [--wait] [--conform]
                        [--allow-gaps] [--prores] [--proxy]

Every shot of the pass's cut (final by default) is upscaled by the series
config's recipe (`upscale.master`: each take through its own target's section,
its shot's own recipe over it), then the cut is assembled from the upscales at
the recipe's size and quality into `<episode>/master/`:
`<ep>_master_<WxH>.mp4` (and `.mov`, ProRes 422 HQ, with --prores), with
`<ep>_master.json` / `.md` saying what each shot is.

What it does with each shot of the cut (`plan_master`):

- `upscale`: no upscale, one made from an older take, or one that failed:
  queued by the recipe.
- `ok`: a fresh upscale made with the recipe's settings.
- `kept`: a fresh upscale marked Keep, or made with other settings (a
  different recipe, or before upscales recorded theirs). Left alone;
  `--conform` redoes the unmarked ones. Never a Keep.
- `queued`: an upscale already on its way.
- `gap`: nothing it can use: no usable pick (or the cut plays the other pass's
  take), the recipe covers no section for its target, or a kept upscale at
  another size than the master's.

The cut is never changed: `master` works from the picks as they are. Without
--wait, a run queues what needs it and a later run (or the editor) assembles
once they've landed. The assembly is strict: every clip from an upscale at the
master size, no gaps, unless --allow-gaps (a gap's clip is its take scaled up,
as the review cut does it, and the report says so).

Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from dataclasses import dataclass, field

import h3jobs as J
import h3takes as T
import h3upscale as U

STATUSES = ("upscale", "ok", "kept", "queued", "gap")


class MasterError(ValueError):
    pass


@dataclass
class Row:
    shot: str
    take: T.Take | None
    status: str                          # STATUSES
    why: str = ""
    job: U.UpscaleJob | None = None      # an `upscale` row's planned job
    recipe: str = ""                     # the shot's recipe, in words

    def view(self) -> dict:
        rec = T.upscale_of(self.take) if self.take else None
        return {"shot": self.shot, "take": self.take.take if self.take else None,
                "target": ((self.take.sidecar or {}).get("target") if self.take else None),
                "status": self.status, "why": self.why, "recipe": self.recipe,
                "upscale": ({"width": rec.get("width"), "height": rec.get("height"),
                             "status": rec.get("status"), "keep": bool(rec.get("keep"))}
                            if rec else None)}


@dataclass
class Plan:
    root: str
    pass_: str
    recipe: dict
    size: tuple
    rows: list = field(default_factory=list)

    def of(self, status: str) -> list:
        return [r for r in self.rows if r.status == status]

    @property
    def ready(self) -> bool:
        """Every shot has its upscale: nothing to queue, nothing on its way, no gap."""
        return all(r.status in ("ok", "kept") for r in self.rows)

    def view(self) -> dict:
        return {"pass": self.pass_, "size": list(self.size), "fit": self.recipe.get("fit") or "crop",
                "quality": self.recipe.get("quality") or "review",
                "counts": {s: len(self.of(s)) for s in STATUSES}, "ready": self.ready,
                "rows": [r.view() for r in self.rows]}


def plan_master(root: str, pass_: str = "final", conform: bool = False) -> Plan:
    """What mastering the pass's cut would do, shot by shot (see the module's
    docstring). MasterError when the series has no recipe, or it names no size."""
    recipe = U.master_recipe(root)
    if not recipe:
        raise MasterError("the series config has no upscale.master recipe (docs/AUTHORING.md, Masters)")
    try:
        size = U.parse_deliver(recipe.get("deliver"))
    except U.UpscaleError as e:
        raise MasterError(f"upscale.master: {e}")
    if not size:
        raise MasterError("upscale.master needs a deliver size (1080p, 1440p, 4k or WxH): "
                          "a master is one size")
    shots = U.shot_recipes(root)
    plan = Plan(root, pass_, recipe, size)
    for shot, t, why in U.cut_takes(root, None, None, pass_=pass_):
        if t is None:
            plan.rows.append(Row(shot, None, "gap", why))
            continue
        target = (t.sidecar or {}).get("target") or T.DEFAULT_TARGET
        key, section = U.recipe_section(recipe, target)
        if key is None:
            plan.rows.append(Row(shot, t, "gap", f"upscale.master has no section for {target}"))
            continue
        text = U.describe_recipe({**(recipe.get("finish") or {}), **section, **(shots.get(shot) or {})})
        rec = T.upscale_of(t)
        row = Row(shot, t, "upscale", "", recipe=text)
        if rec and rec.get("status") == "queued":
            row.status, row.why = "queued", "its upscale is on its way"
        elif rec and rec.get("status") == "ok" and rec.get("fresh"):
            made = (rec.get("width"), rec.get("height"))
            if U.kept(t, rec):
                row.status, row.why = "kept", "marked Keep"
            else:
                match = U.recipe_status(root, t, recipe, shots, rec)
                if match == "same":
                    row.status = "ok"
                elif not conform:
                    row.status = "kept"
                    row.why = ("made before upscales recorded their settings" if match == "unknown"
                               else "made with other settings than the recipe's")
                else:
                    row.why = "--conform: made with other settings than the recipe's"
            if row.status in ("ok", "kept") and tuple(made) != tuple(size):
                row.status = "gap"
                row.why = (f"its upscale is {made[0]}x{made[1]}, the master {size[0]}x{size[1]}"
                           + (": unkeep it, or redo it" if U.kept(t, rec) else ": --conform redoes it"))
        elif rec and rec.get("status") == "ok":
            row.why = "its upscale was made from an older take"
        elif rec and rec.get("status") == "failed":
            row.why = "its last upscale failed: trying again"
        else:
            row.why = "not upscaled yet"
        if row.status == "upscale":
            try:
                job = U.plan_upscale(root, t, redo=True, **U.recipe_for(root, t, recipe, shots))
            except U.UpscaleError as e:
                row.status, row.why = "gap", str(e)
            else:
                if job.action == "error":
                    row.status, row.why = "gap", job.why
                else:
                    row.job = job
        plan.rows.append(row)
    if not plan.rows:
        raise MasterError(f"the {pass_} cut is empty")
    return plan


def queue_master(plan: Plan, comfy, comfy_url: str) -> tuple[list, list]:
    """Queue the plan's `upscale` rows (in cut order) on ComfyUI. Returns
    (queued rows, [(row, error)]). MasterError when this ComfyUI can't run them."""
    rows = plan.of("upscale")
    if not rows:
        return [], []
    try:
        info = comfy.object_info()
    except Exception as e:
        raise MasterError(f"ComfyUI didn't answer: {e}")
    missing = U.not_ready([r.job for r in rows], info)
    if missing:
        raise MasterError("this ComfyUI can't run these upscales: " + "; ".join(missing))
    bases: dict = {}
    queued, errors = [], []
    for r in rows:
        try:
            g = U.graph_of(r.job, bases, comfy_url)
            U.start(r.job)
            U.mark_queued(r.job, comfy.queue(g))
            r.status, r.why = "queued", "queued by this run"
            queued.append(r)
        except Exception as e:
            if os.path.isfile(r.job.take.paths.up_sidecar):
                U.mark_failed(r.job, str(e)[:800])
            errors.append((r, str(e)))
    return queued, errors


def wait_master(plan: Plan, comfy, timeout: int = 3600) -> list:
    """Follow every queued row's upscale to its end (ComfyUI's history by its
    prompt id). Returns the rows whose upscale didn't finish ok."""
    bad = []
    for r in plan.of("queued"):
        rec = T.upscale_of(r.take) or {}
        pid = rec.get("comfy_prompt_id")
        t0 = time.time()
        try:
            if pid:
                comfy.wait(pid, timeout)
            while (T.upscale_of(r.take) or {}).get("status") == "queued" and time.time() - t0 < 60:
                time.sleep(0.5)                         # the saver's record lands just after
        except Exception as e:
            bad.append((r, str(e)))
            continue
        rec = T.upscale_of(r.take) or {}
        if rec.get("status") != "ok":
            bad.append((r, rec.get("save_notes") or f"its upscale is {rec.get('status')}"))
    return bad


def fwd(root: str, path: str) -> str:
    """`path` relative to the episode, with forward slashes (the report's paths)."""
    return os.path.relpath(path, root).replace(os.sep, "/")


def master_dir(root: str) -> str:
    return os.path.join(root, "master")


def master_name(root: str, size: tuple) -> str:
    return f"{os.path.basename(os.path.normpath(root))}_master_{size[0]}x{size[1]}"


def assemble_master(plan: Plan, allow_gaps: bool = False, prores: bool = False,
                    timeout: int = 3600) -> dict:
    """The cut from its upscales at the master size into <episode>/master/, and
    the report beside it. Refused while anything is still to upscale or on its
    way, and with gaps unless `allow_gaps`. {"ok", "output", "mov", "report", "error"}."""
    waiting = plan.of("upscale") + plan.of("queued")
    if waiting:
        raise MasterError(f"{len(waiting)} shot(s) still to upscale: "
                          + ", ".join(r.shot for r in waiting[:10]))
    gaps = plan.of("gap")
    if gaps and not allow_gaps:
        raise MasterError(f"{len(gaps)} gap(s): " + "; ".join(f"{r.shot}: {r.why}" for r in gaps[:5])
                          + " (--allow-gaps masters them scaled up from their takes)")
    import h3edit
    name = master_name(plan.root, plan.size)
    sub = "renders_proxy" if plan.pass_ == "proxy" else "renders"
    args = ["-o", plan.root, "--upscaled", "--size", f"{plan.size[0]}x{plan.size[1]}",
            "--quality", "master" if (plan.recipe.get("quality") == "master") else "review",
            "--name", name + ".mp4"]
    if plan.pass_ == "proxy":
        args += ["--shotlist", "shotlist/shotlist_proxy.json", "--subfolder", sub]
    if gaps:
        args.append("--partial")
    if prores:
        args += ["--intermediate", "prores"]
    rc, out, err = h3edit.run_tool("h3assemble.py", args, plan.root, timeout)
    made = os.path.join(plan.root, sub, name + ".mp4")
    if rc != 0 or not os.path.isfile(made):
        return {"ok": False, "output": None, "mov": None, "report": out,
                "error": err.strip() or out.strip()[-400:] or "h3assemble wrote no cut"}
    dst = master_dir(plan.root)
    os.makedirs(dst, exist_ok=True)
    moved = {}
    for ext in (".mp4", "_shots.txt", ".mov"):
        src = os.path.join(plan.root, sub, name + ext)
        if os.path.isfile(src):
            shutil.move(src, os.path.join(dst, name + ext))
            moved[ext] = os.path.join(dst, name + ext)
    rep = write_report(plan, moved.get(".mp4"), moved.get(".mov"))
    return {"ok": True, "output": moved[".mp4"], "mov": moved.get(".mov"), "report": out,
            "report_md": rep, "error": ""}


def write_report(plan: Plan, mp4: str | None, mov: str | None) -> str:
    """<ep>_master.json and .md beside the master: each shot, its take, target,
    recipe and upscale, and anything kept or missing. Returns the .md's path."""
    root, dst = plan.root, master_dir(plan.root)
    os.makedirs(dst, exist_ok=True)
    stem = os.path.join(dst, os.path.basename(os.path.normpath(root)) + "_master")
    data = {"episode": os.path.basename(os.path.normpath(root)), "made": T.now(),
            "output": fwd(root, mp4) if mp4 else None, "prores": fwd(root, mov) if mov else None,
            **plan.view()}
    for row, r in zip(plan.rows, data["rows"]):
        rec = T.upscale_of(row.take) if row.take else None
        if rec:
            r["upscale"].update(recipe=rec.get("recipe"), quality=rec.get("quality"),
                                finished=rec.get("finished"))
    T.write_json(stem + ".json", data)
    w, h = plan.size
    lines = [f"# {data['episode']} master", "",
             f"{w}x{h} ({data['fit']}), {data['quality']} quality, {plan.pass_} pass, "
             f"made {data['made']}", ""]
    if mp4:
        lines += [f"- `{fwd(root, mp4)}`"] + ([f"- `{fwd(root, mov)}` (ProRes 422 HQ)"] if mov else []) + [""]
    lines += ["| shot | take | target | status | recipe | note |", "|---|---|---|---|---|---|"]
    for r in data["rows"]:
        lines.append(f"| {r['shot']} | {r['take'] or ''} | {r['target'] or ''} | {r['status']} | "
                     f"{r['recipe']} | {r['why']} |")
    with open(stem + ".md", "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
    return stem + ".md"


def print_plan(plan: Plan) -> None:
    w, h = plan.size
    print(f"\n  {os.path.basename(os.path.normpath(plan.root))}: master {w}x{h} "
          f"({plan.recipe.get('fit') or 'crop'}, {plan.recipe.get('quality') or 'review'} quality), "
          f"{plan.pass_} cut, {len(plan.rows)} shots")
    mark = {"upscale": "..", "ok": "ok", "kept": "= ", "queued": "~~", "gap": "!!"}
    for r in plan.rows:
        tk = f"t{r.take.take:02d}" if r.take else "   "
        print(f"  {mark[r.status]} {r.shot:8} {tk}  {r.status:8} {r.recipe}"
              + (f"  ({r.why})" if r.why else ""))
    counts = ", ".join(f"{len(plan.of(s))} {s}" for s in STATUSES if plan.of(s))
    print(f"  {counts}")


def master_episode(root: str, args, comfy) -> int:
    """One episode of `h3.py master`: plan, queue, (wait), assemble."""
    try:
        plan = plan_master(root, args.pass_, args.conform)
    except MasterError as e:
        print(f"\n  !! {os.path.basename(root)}: {e}")
        return 1
    print_plan(plan)
    if args.check:
        return 0
    gaps = plan.of("gap")
    if gaps and not args.allow_gaps:
        print(f"  !! {len(gaps)} gap(s): not mastered (fix them, or --allow-gaps)")
        return 1
    if plan.of("upscale"):
        try:
            queued, errors = queue_master(plan, comfy, args.comfy)
        except MasterError as e:
            print(f"  !! {e}")
            return 1
        print(f"  queued {len(queued)} upscale(s)")
        for r, e in errors:
            print(f"  !! {r.shot}: {e}")
        if errors:
            return 1
    if plan.of("queued"):
        if not args.wait:
            print("  run it again once they've finished (or --wait) to assemble the master")
            return 0
        print(f"  waiting for {len(plan.of('queued'))} upscale(s)...", flush=True)
        bad = wait_master(plan, comfy, args.timeout)
        for r, e in bad:
            print(f"  !! {r.shot}: {e}")
        if bad:
            return 1
        plan = plan_master(root, args.pass_, args.conform)
    try:
        res = assemble_master(plan, args.allow_gaps, args.prores, args.timeout)
    except MasterError as e:
        print(f"  !! {e}")
        return 1
    if not res["ok"]:
        print(f"  !! assembling failed: {res['error']}")
        return 1
    print(f"  -> {res['output']}" + (f"\n  -> {res['mov']}" if res["mov"] else "")
          + f"\n  report: {res['report_md']}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="h3.py master", description=__doc__.split("\n\n")[0])
    ap.add_argument("episodes", nargs="+", help="episode folders (a show folder: every episode in it)")
    ap.add_argument("--proxy", dest="pass_", action="store_const", const="proxy", default="final",
                    help="the proxy cut (a check of the flow; masters are the final's)")
    ap.add_argument("--check", action="store_true", help="the plan only: queue and write nothing")
    ap.add_argument("--wait", action="store_true",
                    help="follow the queued upscales and assemble when they're done")
    ap.add_argument("--conform", action="store_true",
                    help="also redo upscales made with other settings than the recipe's (never a Keep)")
    ap.add_argument("--allow-gaps", action="store_true",
                    help="master a cut with gaps: their clips scaled up from their takes")
    ap.add_argument("--prores", action="store_true", help="also a ProRes 422 HQ .mov")
    ap.add_argument("--comfy", default="http://127.0.0.1:8188")
    ap.add_argument("--timeout", type=int, default=3600)
    args = ap.parse_args(argv)
    roots = episode_roots(args.episodes)
    if not roots:
        print("  !! no episodes found")
        return 2
    comfy = J.Comfy(args.comfy)
    results = {}
    for root in roots:
        results[root] = master_episode(root, args, comfy)
    if len(roots) > 1:
        print("\n  " + ", ".join(f"{os.path.basename(r)} {'ok' if rc == 0 else 'not done'}"
                                 for r, rc in results.items()))
    return 0 if all(rc == 0 for rc in results.values()) else 1


def episode_roots(paths: list) -> list:
    """Episode folders: each path that has a cut or a shotlist, or else the
    folders inside it that do (a show folder), sorted."""
    out = []
    for p in paths:
        p = os.path.abspath(p)
        if is_episode(p):
            out.append(p)
        elif os.path.isdir(p):
            out += sorted(os.path.join(p, d) for d in os.listdir(p)
                          if is_episode(os.path.join(p, d)))
    return list(dict.fromkeys(out))


def is_episode(p: str) -> bool:
    return os.path.isdir(os.path.join(p, "shotlist")) or os.path.isfile(os.path.join(p, T.CUT_FILE))


if __name__ == "__main__":
    sys.exit(main())
