#!/usr/bin/env python3
"""
h3edit.py — look at and steer an episode's takes: list them, pick one for the
cut, set per-shot overrides. The command-line face of what the editor does;
`episode_status` is what its routes will serve.

    python h3.py takes    Shows\\ep05 [--proxy] [--only sh020,sh030] [--no-sweep]
    python h3.py pick     Shows\\ep05 sh020 3 [--proxy] [--from proxy]
    python h3.py pick     Shows\\ep05 sh020 latest [--proxy]
    python h3.py override Shows\\ep05 sh020 --show [--proxy]
    python h3.py override Shows\\ep05 sh020 --seed 1234 --steps 10 [--proxy | --both]
    python h3.py override Shows\\ep05 sh020 --lora a.safetensors --lora b.safetensors:0.6
    python h3.py override Shows\\ep05 sh020 --prompt-file sh020.txt
    python h3.py override Shows\\ep05 sh020 --dump-prompt > sh020.txt
    python h3.py override Shows\\ep05 sh020 --clear prompt seed
    python h3.py override Shows\\ep05 sh020 --clear

`takes` shows every take with its status, why it is stale (script / ref /
preset), and which take the cut uses. `pick` writes cut.json; `latest` puts a
shot back on its newest usable take. `override` writes overrides.json: the
next render or redo of that shot uses it (see h3render.py). Model, LoRAs and
steps are per pass (final unless --proxy; --both sets both); prompt and seed
apply to both passes.

Stdlib only.
"""
from __future__ import annotations

import argparse
import os
import sys

import h3jobs as J
import h3takes as T


# ---------------------------------------------------------------------------
# status: what the editor's shot bin shows
# ---------------------------------------------------------------------------

def episode_status(root: str, pass_: str, folder: str | None = None) -> dict:
    """Every shot in cut order with its takes, the take the cut uses, and its
    override. Plain data, ready to serve as JSON."""
    doc = J.load_shotlist(root, pass_)
    shots = {s["id"]: s for s in doc["shots"]}
    ov = T.load_overrides(root)
    cut = T.load_cut(root)
    out = []
    for e in T.resolve_cut(cut, pass_, list(shots)):
        shot = shots.get(e.shot)
        takes = T.list_takes(root, pass_, e.shot, folder) if shot else []
        if e.placeholder:
            src = T.list_takes(root, e.pass_, e.shot)
        else:
            src = takes
        if e.take is not None:
            chosen = next((t for t in src if t.take == e.take), None)
            chosen_take = e.take
            chosen_ok = bool(chosen and chosen.usable)
        else:
            chosen = T.latest_usable(src)
            chosen_take = chosen.take if chosen else None
            chosen_ok = chosen is not None
        o = T.shot_override(ov, e.shot, pass_)
        out.append({
            "shot": e.shot,
            "orphan": e.orphan,
            "length": shot.get("length") if shot else None,
            "cut": {"take": chosen_take, "picked": e.take is not None, "pass": e.pass_,
                    "placeholder": e.placeholder, "usable": chosen_ok,
                    "trim_in": e.trim_in, "trim_out": e.trim_out, "locked": e.locked,
                    "note": e.note, "in_cut_file": e.in_cut_file},
            "override": {"fields": sorted(k for k in o if k != "base_hash"),
                         "stale": bool(o.get("base_hash"))
                         and bool(shot) and o["base_hash"] != J.story_hash(shot)},
            "takes": [{
                "take": t.take, "status": t.status, "has_video": t.has_video,
                "seed": (t.sidecar or {}).get("seed"),
                "seed_source": (t.sidecar or {}).get("seed_source"),
                "note": (t.sidecar or {}).get("note", ""),
                "overrides": (t.sidecar or {}).get("overrides", []),
                "stale": J.stale_reasons(root, doc, shot, t.sidecar) if shot else [],
                "thumb": os.path.basename(t.paths.thumb) if os.path.isfile(t.paths.thumb)
                else None,
                "strip": os.path.basename(t.paths.strip) if os.path.isfile(t.paths.strip)
                else None,
                "queued": (t.sidecar or {}).get("queued"),
                "finished": (t.sidecar or {}).get("finished"),
                "save_notes": (t.sidecar or {}).get("save_notes", ""),
            } for t in takes],
        })
    return {"episode": doc.get("episode", os.path.basename(root)), "pass": pass_,
            "shots": out}


def sweep(root: str, pass_: str, comfy_url: str, folder: str | None = None) -> int:
    as_of = T.now()
    try:
        alive = J.Comfy(comfy_url).alive()
    except Exception:
        return -1
    doc = J.load_shotlist(root, pass_)
    return sum(len(T.sweep_queued(T.list_takes(root, pass_, s["id"], folder), alive,
                                  as_of=as_of)) for s in doc["shots"])


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------

def _pass(args) -> str:
    return "proxy" if args.proxy else "final"


def cmd_takes(root: str, argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="h3.py takes")
    ap.add_argument("--proxy", action="store_true")
    ap.add_argument("--only", help="comma-separated shot ids")
    ap.add_argument("--no-sweep", action="store_true",
                    help="don't ask ComfyUI which queued takes are still running")
    ap.add_argument("--comfy", default="http://127.0.0.1:8188")
    args = ap.parse_args(argv)
    pass_ = _pass(args)
    if not args.no_sweep:
        n = sweep(root, pass_, args.comfy)
        if n > 0:
            print(f"  marked {n} take(s) failed: queued, but ComfyUI no longer has the job")
    st = episode_status(root, pass_)
    only = {s.strip() for s in args.only.split(",")} if args.only else None
    picks = sum(1 for s in st["shots"] if s["cut"]["picked"])
    print(f"\n  {st['episode']}  ·  {pass_}  ·  {len(st['shots'])} shots"
          + (f", {picks} picked in cut.json" if picks else ""))
    counts = {"none": 0, "stale": 0}
    for s in st["shots"]:
        if only and s["shot"] not in only:
            continue
        head = s["shot"] + ("  (orphan: not in the script)" if s["orphan"] else "")
        ovf = s["override"]["fields"]
        if ovf:
            head += f"   override: {', '.join(ovf)}" + ("  (STALE)" if s["override"]["stale"] else "")
        print(f"\n  {head}")
        c = s["cut"]
        if not s["takes"] and not c["placeholder"]:
            counts["none"] += 1
            print("      (no takes)")
        for t in s["takes"]:
            mark = ""
            if not c["placeholder"] and t["take"] == c["take"]:
                mark = "<- cut" + (" (picked)" if c["picked"] else "")
            stale = [r for r in t["stale"] if r != "unknown"]
            if stale and t["status"] == "ok":
                counts["stale"] += 1
            seed = (f"seed {t['seed']} {t['seed_source'] or ''}".rstrip()
                    if t["seed"] is not None else "no sidecar")
            bits = [f"t{t['take']:02d}", f"{t['status']:<7}", f"{seed:<30}",
                    ("stale: " + ",".join(stale)) if stale else "", mark,
                    t["note"]]
            print("      " + "  ".join(b for b in bits if b))
        if c["placeholder"]:
            print(f"      <- cut uses {c['pass']} t{c['take']:02d} as a placeholder"
                  if c["take"] else f"      <- cut wants a {c['pass']} placeholder, none usable")
        elif c["picked"] and not c["usable"]:
            print(f"      ! cut.json picks t{c['take']:02d}, which is not usable")
    print(f"\n  {counts['none']} shot(s) with no takes, {counts['stale']} stale take(s)\n")
    return 0


def cmd_pick(root: str, argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="h3.py pick")
    ap.add_argument("shot")
    ap.add_argument("take", help="take number, or 'latest' to follow the newest usable take")
    ap.add_argument("--proxy", action="store_true", help="edit the proxy cut")
    ap.add_argument("--from", dest="src", choices=T.PASSES,
                    help="take comes from this pass (a placeholder)")
    ap.add_argument("--force", action="store_true", help="pick a take that isn't usable yet")
    args = ap.parse_args(argv)
    pass_ = _pass(args)
    src = args.src or pass_
    order = [s["id"] for s in J.load_shotlist(root, pass_)["shots"]]
    if args.take == "latest":
        take = None
    else:
        try:
            take = int(args.take.lstrip("tT"))
        except ValueError:
            print(f"  !! take must be a number or 'latest', not {args.take!r}")
            return 2
        t = T.get_take(root, src, args.shot, take)
        if t is None:
            print(f"  !! {args.shot} has no take {take} in {src}")
            return 1
        if not t.usable and not args.force:
            print(f"  !! {args.shot} t{take:02d} is {t.status}"
                  + ("" if t.has_video else " with no mp4") + "; --force to pick it anyway")
            return 1
    try:
        cut = T.pick(T.load_cut(root), pass_, order, args.shot, take, from_pass=src)
    except KeyError as e:
        print(f"  !! {e.args[0]}")
        return 1
    cut.setdefault("episode", J.load_shotlist(root, pass_).get("episode", ""))
    T.save_cut(root, cut)
    print(f"  {pass_} cut: {args.shot} -> "
          + (f"{src} t{take:02d}" if take is not None else "latest usable take"))
    return 0


def cmd_override(root: str, argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="h3.py override")
    ap.add_argument("shot")
    p = ap.add_mutually_exclusive_group()
    p.add_argument("--proxy", action="store_true", help="model/LoRA/steps for the proxy pass")
    p.add_argument("--both", action="store_true", help="model/LoRA/steps for both passes")
    ap.add_argument("--seed", type=int)
    ap.add_argument("--steps", type=int)
    ap.add_argument("--model")
    ap.add_argument("--lora", action="append", metavar="NAME[:STRENGTH]",
                    help="repeat to stack; 'none' for no LoRA")
    ap.add_argument("--prompt-file", help="text file whose contents become the prompt")
    ap.add_argument("--note")
    ap.add_argument("--clear", nargs="*", metavar="FIELD",
                    help="remove these fields (all of this shot's override if none named)")
    ap.add_argument("--show", action="store_true", help="print the effective override")
    ap.add_argument("--dump-prompt", action="store_true",
                    help="print the prompt the next render would use, for editing")
    args = ap.parse_args(argv)
    pass_ = _pass(args)
    doc = J.load_shotlist(root, pass_)
    idx = next((i for i, s in enumerate(doc["shots"]) if s["id"] == args.shot), None)
    if idx is None:
        print(f"  !! {args.shot} is not in {J.shotlist_rel(pass_)}")
        return 1
    shot = doc["shots"][idx]
    ov = T.load_overrides(root)

    if args.dump_prompt:
        job = J.plan_job(root, pass_, doc, idx, J.RenderRequest(args.shot), ov)
        pr = job.prompt
        sys.stdout.write(("\n\n".join(pr) if isinstance(pr, list) else pr) + "\n")
        return 0

    # each pass's own build of the shot: overrides are stamped against it
    built = {}
    for ps in T.PASSES:
        try:
            d = doc if ps == pass_ else J.load_shotlist(root, ps)
        except FileNotFoundError:
            continue
        s = next((s for s in d["shots"] if s["id"] == args.shot), None)
        if s is not None:
            built[ps] = s

    passes = list(T.PASSES) if args.both else [pass_]
    missing = [ps for ps in passes if ps not in built]
    if missing:
        print(f"  !! {args.shot} has no {'/'.join(missing)} build — run h3.py build first")
        return 1
    user_fields = [f for f in T.SHOT_FIELDS + T.PASS_FIELDS if f != "base_hash"]
    changed = False
    if args.clear is not None:
        # bare --clear drops the whole override (both passes unless --proxy);
        # named pass fields follow the usual pass flags
        fields = args.clear or user_fields
        clear_passes = T.PASSES if (not args.clear and not args.proxy) else passes
        for f in fields:
            if f in T.SHOT_FIELDS:
                T.set_override(ov, args.shot, **{f: None})
            elif f in user_fields:
                for ps in clear_passes:
                    T.set_override(ov, args.shot, ps, **{f: None})
            else:
                print(f"  !! unknown field {f!r}: one of {', '.join(user_fields)}")
                return 2
        changed = True
    shot_fields = {}
    if args.seed is not None:
        shot_fields["seed"] = args.seed
    if args.note is not None:
        shot_fields["note"] = args.note
    pass_fields = {}
    if args.prompt_file:
        with open(args.prompt_file, encoding="utf-8") as fh:
            pass_fields["prompt"] = fh.read().strip()
    if args.steps is not None:
        pass_fields["steps"] = args.steps
    if args.model:
        pass_fields["model"] = args.model
    if args.lora:
        pass_fields["loras"] = [l for spec in args.lora for l in J.parse_lora(spec)]
    if shot_fields:
        T.set_override(ov, args.shot, **shot_fields)
        changed = True
    if pass_fields:
        for ps in passes:
            # written against the shot as this pass builds it now
            T.set_override(ov, args.shot, ps, base_hash=J.story_hash(built[ps]),
                           **pass_fields)
        changed = True
    if changed:
        ov.setdefault("episode", doc.get("episode", ""))
        T.save_overrides(root, ov)

    for ps in T.PASSES:
        eff = T.shot_override(ov, args.shot, ps)
        if not {k for k in eff if k not in T.SHOT_FIELDS} and ps != pass_:
            continue
        stale = (eff.get("base_hash") and ps in built
                 and eff["base_hash"] != J.story_hash(built[ps]))
        print(f"  {args.shot} [{ps}]" + ("  (written against an older build: STALE)"
                                          if stale else ""))
        if not {k for k in eff if k != "base_hash"}:
            print("      no override")
        for k in ("seed", "steps", "model", "loras", "note", "prompt"):
            if k not in eff:
                continue
            v = eff[k]
            if k == "loras":
                v = ", ".join(f"{l['name']}@{l.get('strength', 1):g}" for l in v) or "none"
            elif k == "prompt" and not args.show:
                text = "\n\n".join(v) if isinstance(v, list) else v
                v = f"{len(text)} chars (--show to print)"
            elif k == "prompt":
                v = "\n" + ("\n\n".join(v) if isinstance(v, list) else v)
            print(f"      {k:<6} {v}")
    return 0


COMMANDS = {"takes": cmd_takes, "pick": cmd_pick, "override": cmd_override}
