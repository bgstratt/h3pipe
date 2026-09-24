#!/usr/bin/env python3
"""
h3.py — one front door for the H3 pipeline. Run it from the folder that holds
your projects, with the pipeline scripts beside it (see the README).

    python h3.py new      Shows\\ep02            # a new episode: series.json + ep02.md that build
    python h3.py supply   Shows\\ep05 C:\\sheets   # pictures you already have into their ref slots
                                                    # (a folder is matched by file name; --dry-run
                                                    #  prints the table, --ref id[:view] names one)
    python h3.py issues   Shows\\ep05 --proxy     # the notepad for the pass you are reviewing:
                                                    # --add sh0140 "the truck is on the wrong side",
                                                    # --export to paste into an assistant, --clear
    python h3.py align    Shows\\ep05 audio\\ep05_dialogue.wav   # h3align: time the script to a recording
    python h3.py build    Shows\\ep05            # h3build: shotlist + refs_todo (final AND proxy)
    python h3.py check    Shows\\ep05            # h3build --check and --pace, writes nothing
    python h3.py refs     Shows\\ep05 --list     # kreagen: generate missing reference images
    python h3.py render   Shows\\ep05 --proxy    # h3render: queue shots on ComfyUI
    python h3.py assemble Shows\\ep05 --proxy    # h3assemble: join renders into one mp4
    python h3.py all      Shows\\ep05 --proxy    # build -> refs -> render -> assemble
    python h3.py all      Shows\\ep05 --proxy --skip-build
                                                    # refs -> render -> assemble, using the
                                                    # shotlists already on disk (keeps hand edits)

    python h3.py takes    Shows\\ep05 [--proxy]      # every take: status, seed, why stale, which is in the cut
    python h3.py pick     Shows\\ep05 sh020 3        # the cut uses sh020 take 3 (cut.json)
    python h3.py pick     Shows\\ep05 sh020 latest   # back to the newest usable take
    python h3.py override Shows\\ep05 sh020 --seed 1234 --prompt-file p.txt
                                                    # overrides.json: used by the next render/redo
    python h3.py keyframe Shows\\ep05 sh020 --from-prev
                                                    # sh020's first keyframe = the previous
                                                    # shot's last frame (continuity)
    python h3.py override Shows\\ep05 --episode-target ltx2
                                                    # every shot the script gives no target
                                                    # renders on ltx2 (built: back to series.json's)
    python h3.py discard  Shows\\ep05 sh020 3 [--proxy]
                                                    # move sh020 take 3 to renders/_trash/sh020/
                                                    # (a cut pick of it goes back to latest)
    python h3.py cut      Shows\\ep05 [--proxy] --move sh050 --before sh020
                                                    # edit the cut: --show, --order, --move,
                                                    # --trim SH IN OUT, --lock/--unlock SH,
                                                    # --reset order|trims|audio|all,
                                                    # --copy-from final|proxy
                                                    #     [order|trims|audio|all],
                                                    # --audio SH take SH:N | file PATH
                                                    #     | none | own
                                                    #     [--from S] [--at S] [--gain G]
    (see h3edit.py for every takes/pick/override/keyframe/discard/cut flag)

    python h3.py promote  Shows\\ep05 [sh020]        # the plan: which overrides can move into
                                                    # the script / series config, the diffs
    python h3.py promote  Shows\\ep05 --all          # move them, drop those overrides, rebuild
    python h3.py promote  Shows\\ep05 --item shot:sh020:steps [--item ...] [--dry-run]
                                                    # (see h3promote.py for what maps where)

    python h3.py targets  [Shows\\ep05] [--json]     # which targets the running ComfyUI can
                                                    # render, what to download for the rest,
                                                    # and the workflow each one renders
    python h3.py targets  --install-workflow ltx2    # copy its graph into ComfyUI, to edit on
                                                    # the canvas (--revert-workflow undoes it)

The episode can be a folder (any name) holding series.json and one script .md,
or several folders at once, or a parent with --each:

    python h3.py build Shows --each              # every ep* folder under Shows

Anything after the episode is passed straight through to the underlying
script, so every flag those scripts take still works here. `--proxy` is also
understood by assemble (it picks the proxy shotlist and renders_proxy/).
"""
from __future__ import annotations

import glob
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
STAGES = ("align", "build", "check", "refs", "render", "assemble", "all")
EDIT = ("takes", "pick", "override", "keyframe", "discard", "cut")   # in-process, see h3edit.py
# `targets` (readiness) runs in-process too, with the episode optional


def script(name: str) -> str:
    p = os.path.join(HERE, name)
    if not os.path.isfile(p):
        sys.exit(f"  !! {name} not found beside h3.py ({HERE})")
    return p


def episode_files(ep: str) -> tuple[str, str]:
    series_cfg = os.path.join(ep, "series.json")
    if not os.path.isfile(series_cfg):
        sys.exit(f"  !! {series_cfg} not found")
    base = os.path.basename(os.path.normpath(ep))
    md = os.path.join(ep, f"{base}.md")
    if not os.path.isfile(md):
        cands = [p for p in glob.glob(os.path.join(ep, "*.md"))
                 if not os.path.basename(p).lower().startswith(("refs_todo", "readme", "notes", "align_report"))]
        if len(cands) != 1:
            sys.exit(f"  !! can't tell which script to use in {ep}: "
                     f"{[os.path.basename(c) for c in cands] or 'no .md found'}")
        md = cands[0]
    return series_cfg, md


def run(cmd: list[str]) -> int:
    print("  $ " + " ".join(f'"{c}"' if " " in c else c for c in cmd), flush=True)
    return subprocess.call(cmd)


def stage(name: str, ep: str, extra: list[str], skip_build: bool = False) -> int:
    py = sys.executable
    proxy = "--proxy" in extra
    if name in ("build", "check"):
        series_cfg, md = episode_files(ep)
        base = [py, script("h3build.py"), series_cfg, md]
        if name == "check":
            return run(base + ["--check"]) or run(base + ["--pace"])
        rest = [a for a in extra if a != "--proxy"]
        return run(base + ["-o", ep] + rest) or run(base + ["-o", ep, "--proxy"] + rest)
    if name == "align":
        return run([py, script("h3align.py"), ep] + extra)
    if name == "refs":
        return run([py, script("kreagen.py"), "--project-root", ep] + extra)
    if name == "render":
        return run([py, script("h3render.py"), ep] + extra)
    if name == "assemble":
        rest = [a for a in extra if a != "--proxy"]
        if proxy:
            rest = ["--shotlist", "shotlist/shotlist_proxy.json",
                    "--subfolder", "renders_proxy"] + rest
        return run([py, script("h3assemble.py"), "-o", ep] + rest)
    if name == "all":
        # refs and build take no render flags; keep only --proxy for them
        steps = [("build", []), ("refs", []), ("build", []),
                 ("render", extra), ("assemble", ["--proxy"] if proxy else [])]
        if skip_build:
            # use the shotlists already on disk so hand edits (steps, sizes...) survive
            need = ["shotlist/shotlist.json", "refs_todo.json"]
            if proxy:
                need.append("shotlist/shotlist_proxy.json")
            missing = [n for n in need if not os.path.isfile(os.path.join(ep, n))]
            if missing:
                print(f"  !! --skip-build but {ep} has no {', '.join(missing)}; "
                      f"run `python h3.py build {ep}` once first")
                return 1
            print("  -- skipping build; using existing shotlists")
            steps = [st for st in steps if st[0] != "build"]
        for s, args in steps:
            rc = stage(s, ep, args)
            if rc:
                print(f"  !! {s} failed for {ep} (exit {rc}); stopping this episode")
                return rc
        return 0
    sys.exit(f"  !! unknown stage {name}")


def main() -> int:
    argv = sys.argv[1:]
    if argv and argv[0] == "new":
        # the folder is the show's, its last part the new episode's name; the
        # template is the newest episode already there, else examples/starter
        sys.path.insert(0, HERE)
        import h3source
        return h3source.cmd_new(argv[1:])
    if argv and argv[0] == "issues":
        if len(argv) < 2 or argv[1].startswith("-"):
            sys.exit("  !! usage: python h3.py issues <episode> [--proxy] "
                     "[--add <shot> <note> | --export | --clear | --resolve <id>]")
        sys.path.insert(0, HERE)
        import h3issues
        return h3issues.cmd_issues(os.path.abspath(argv[1]), argv[2:])
    if argv and argv[0] == "supply":
        if len(argv) < 2 or argv[1].startswith("-"):
            sys.exit("  !! usage: python h3.py supply <episode> <file-or-folder>... "
                     "[--ref id[:view]] [--no-pick] [--dry-run]")
        sys.path.insert(0, HERE)
        import h3refs
        return h3refs.cmd_supply(os.path.abspath(argv[1]), argv[2:])
    if argv and argv[0] == "targets":
        # the episode is optional: it adds its series config (pass blocks,
        # model_families) and marks its target
        sys.path.insert(0, HERE)
        import h3edit
        rest = argv[1:]
        ep = os.path.abspath(rest.pop(0)) if rest and not rest[0].startswith("-") else None
        return h3edit.cmd_targets(ep, rest)
    if argv and argv[0] == "target-from-workflow":
        sys.path.insert(0, HERE)
        import h3edit
        return h3edit.cmd_target_from_workflow(argv[1:])
    if argv and argv[0] == "promote":
        if len(argv) < 2 or argv[1].startswith("-"):
            sys.exit("  !! usage: python h3.py promote <episode> [<shot>] [--all | --item ID ...] "
                     "[--dry-run]")
        sys.path.insert(0, HERE)
        import h3promote
        return h3promote.cmd_promote(os.path.abspath(argv[1]), argv[2:])
    if argv and argv[0] in EDIT:
        if len(argv) < 2 or argv[1].startswith("-"):
            sys.exit(f"  !! usage: python h3.py {argv[0]} <episode> ...")
        sys.path.insert(0, HERE)
        import h3edit
        return h3edit.COMMANDS[argv[0]](os.path.abspath(argv[1]), argv[2:])
    if not argv or argv[0] in ("-h", "--help") or argv[0] not in STAGES:
        print(__doc__)
        return 0 if argv[:1] in (["-h"], ["--help"], []) else 2
    name, rest = argv[0], argv[1:]
    each = "--each" in rest
    skip_build = any(a in ("--skip-build", "--no-build") for a in rest)
    rest = [a for a in rest if a not in ("--each", "--skip-build", "--no-build")]
    if skip_build and name != "all":
        sys.exit("  !! --skip-build only applies to `all` (the other stages never build)")

    eps, extra = [], []
    if name == "align":
        # one episode, then the recording path and flags go straight to h3align
        if not rest or rest[0].startswith("-"):
            sys.exit("  !! usage: python h3.py align <episode> [recording.wav] [flags]")
        return 1 if stage(name, rest[0], rest[1:]) else 0
    for i, a in enumerate(rest):
        if a.startswith("-"):
            extra = rest[i:]
            break
        eps.append(a)
    if not eps:
        sys.exit("  !! give an episode folder, e.g. Shows\\ep05")
    if each:
        eps = sorted(p for parent in eps for p in glob.glob(os.path.join(parent, "ep*"))
                     if os.path.isfile(os.path.join(p, "series.json")))

    failed = []
    for ep in eps:
        print(f"\n=== {name}: {ep}")
        if stage(name, ep, extra, skip_build):
            failed.append(ep)
    if len(eps) > 1:
        print(f"\n  {len(eps) - len(failed)}/{len(eps)} ok"
              + (f"; failed: {', '.join(failed)}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
