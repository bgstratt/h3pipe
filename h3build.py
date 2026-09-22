#!/usr/bin/env python3
"""
h3build.py — script.md + series.json  ->  shots.json (story IR) + shotlist.json

One command. You write a screenplay-flavoured script and the series config
(series.json); this produces the shotlist the ComfyUI loader reads, a list of any reference
assets you still need to make, and a timing report.

    python3 h3build.py series.json script.md -o <project_root>
    python3 h3build.py series.json script.md -o <project_root> --proxy
    python3 h3build.py series.json script.md --check

It runs in three steps: parse the script into the model-free story IR
(h3core.story, written to shotlist/shots.json), pick each shot's video target
(targets.episode_targets: the series config's `series.target`, then every
`target:` and `profile:` the script names), and compile each target's shots
with it (`target.compile_episode`). The series target's shots go to
shotlist.json (always written); another target's to shotlist.<target>.json
(`_proxy` likewise), and refs_todo merges what they all need. Everything
model-specific -- the frame grid, reference slots, the prompt format, audio
policy, presets -- is the target's (targets/video/<id>/). This file only
parses, reports and writes.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

# Sibling modules: ComfyUI's embedded Python (a ._pth install) doesn't put a
# script's own folder on sys.path, so do it here.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import targets as TG  # noqa: E402
from h3core import ir  # noqa: E402,F401
from h3core.series_config import (character_ids, load_series_config,  # noqa: E402
                                  series_info, subject_ids, variant_of)
# Model-neutral pieces, re-exported under their old names: h3align and others
# import them from here.
from h3core.ir import stable_seed  # noqa: E402,F401
from h3core.speech import (GAP_SAME, GAP_SPEAKER, HEAD_AIR, RATE_CEILING,  # noqa: E402,F401
                           SPEECH_RATE, TAIL_AIR, forced_rate, pacing,
                           speech_seconds, syllables)
from h3core.story import (META_KEYS, OS_TOKENS, SIZES, VO_TOKENS,  # noqa: E402,F401
                          ScriptError, parse_script, parse_story,
                          split_parenthetical)

# ---------------------------------------------------------------------------
# Compatibility: the H3 names h3build used to define, now the default video
# target's. h3align snaps with snap_up and the tests read RETENTIONS and
# legacy_episode; new code should go through a Target instead.
# ---------------------------------------------------------------------------

from targets.video.minimax_h3_ref2va import compile as _h3  # noqa: E402

DEFAULT_TARGET = TG.load_target(TG.DEFAULT_VIDEO_TARGET, "video")
snap_up = DEFAULT_TARGET.template.snap
GRID_STEP, GRID_BASE, GRID_MAX = (DEFAULT_TARGET.template.step, DEFAULT_TARGET.template.base,
                                  DEFAULT_TARGET.template.max)
RETENTIONS = _h3.RETENTIONS
RETENTION_DEFAULT = _h3.RETENTION_DEFAULT
RETENTION_OF = _h3.RETENTION_OF
legacy_episode = _h3.legacy_episode
build_prompt = _h3.build_prompt
FINAL_MODEL = DEFAULT_TARGET.presets["final"].model
FINAL_LORA = DEFAULT_TARGET.presets["final"].lora
FINAL_STEPS = DEFAULT_TARGET.presets["final"].steps
PROXY_LORA = DEFAULT_TARGET.presets["proxy"].lora
PROXY_STEPS = DEFAULT_TARGET.presets["proxy"].steps
SIZE_HINT = dict(_h3.RECIPE["size_hints"])


def compile_episode(ep: dict, series_cfg: dict, proxy: bool) -> tuple[dict, dict]:
    """The parser-shaped episode compiled for the default target (the old
    signature; h3build itself compiles the IR through the episode's target)."""
    return _h3.compile_legacy(DEFAULT_TARGET, ep, series_cfg, "proxy" if proxy else "final")


def compile_groups(story: ir.Episode, series_cfg: dict, pass_: str,
                   groups=None) -> list[tuple[TG.Target, dict, dict]]:
    """[(target, shotlist doc, report)] for one pass: each target the episode
    uses compiles its own shots (TG.episode_targets), the series target first.
    What a build writes and what --check reports (and the editor's check).

    Story warnings -- the ones that are about the script rather than any
    model -- are added to the first report, which is the one `--check` prints
    and the editor reads (h3source.check_text)."""
    groups = TG.episode_targets(story, series_cfg) if groups is None else groups
    check_story(story, series_cfg)
    built = [(t, *t.compile_episode(story, series_cfg, pass_, only=ids)) for t, ids in groups]
    if built:
        built[0][2]["warnings"] = (list(built[0][2].get("warnings") or [])
                                   + story_warnings(story, series_cfg))
    return built


def check_story(story: ir.Episode, series_cfg: dict) -> None:
    """ValueError for what the script asks of the series config and doesn't get.

    A subject with no `design` is legal -- a character who is only ever a voice
    needs none -- but putting one on screen leaves every prompt writer with
    nothing to say about them, so it is caught here by name rather than deep in
    one as a KeyError."""
    book = series_cfg.get("subjects") or {}
    for sq in story.sequences:
        for sh in sq.shots:
            for sid in list(sh.cast) + list(sh.props):
                e = book.get(sid) or {}
                if not str(e.get("design") or "").strip():
                    raise ValueError(
                        f"shot {sh.id}: '{sid}' is on screen but has no `design` in "
                        f"series.json, so nothing can describe them. Give them one, or "
                        f"keep them off screen: a voice-only character speaks with "
                        f"(V.O.) or (O.S.) and never appears in `who:` or `with:`.")


def story_warnings(story: ir.Episode, series_cfg: dict | None = None) -> list[str]:
    """What the script says that no target can fix.

    One plate is one picture. Two people who each get their own shot in a
    sequence, both built on the same plate, are drawn against the same
    background from the same view: the cut then reads as one camera with
    people appearing and disappearing in it, rather than as a reverse angle.
    Nothing in a prompt undoes that, because the plate is a picture and the
    prompt is words -- it wants an angle per speaker in the series config
    (docs/AUTHORING.md, "A location is one angle, not one place")."""
    # a wardrobe variant is the same person: Dana in a towel and Dana in an
    # afghan taking turns on one plate is one character changing clothes, not
    # two people talking
    variants = variant_of(series_cfg or {})
    book = (series_cfg or {}).get("subjects") or {}

    def person(sid: str) -> str:
        return variants.get(sid, sid)

    def name(sid: str) -> str:
        return (book.get(sid) or {}).get("name", sid)

    out = []
    for sq in story.sequences:
        by_plate: dict[str, list[str]] = {}
        for sh in sq.shots:
            if len(sh.cast) == 1:
                by_plate.setdefault(sh.plate or sq.location, []).append(person(sh.cast[0]))
        for plate, cast in by_plate.items():
            who = sorted(set(cast))
            if len(who) < 2:
                continue
            names = [name(w) for w in who]
            listed = (" and ".join(names) if len(names) == 2
                      else ", ".join(names[:-1]) + " and " + names[-1])
            out.append(
                f"sequence {sq.id}: {listed} each get their own shot on the same plate "
                f"({plate}), so they are drawn against one background from one view and "
                f"the cut reads as a single camera rather than a reverse angle. Give the "
                f"location an angle per speaker and name it with `plate:`.")
    return out


# ---------------------------------------------------------------------------

def mmss(sec: float) -> str:
    m, s = divmod(sec, 60)
    return f"{int(m)}:{s:05.2f}"


def render_todo(report: dict, root: str) -> str:
    """The asset work order.

    Every prompt here is built from the series config, so the wording that describes a
    character in their sheet prompt is the exact wording that goes into all of
    their shot prompts. Generating from a paraphrase is how a character ends up
    subtly wrong in every shot.
    """
    need = report["needed"]
    # the size each kind of ref should be made at: the target recipe's
    hints = report.get("size_hints", SIZE_HINT)
    missing = [(p, v) for p, v in need.items()
               if not os.path.isfile(os.path.join(root, p))]
    have = len(need) - len(missing)
    blocked = report.get("blocked_shots", {})

    lines = [f"# Asset work order — {report['episode']} {report['title']}", "",
             f"{have}/{len(need)} on disk. Generate each missing asset, save it to the "
             f"path shown, then re-run h3build.", ""]
    if not missing:
        lines += ["Everything the episode needs is already on disk.", ""]
        return "\n".join(lines)

    lines += ["| # | Path | Kind | Size hint | Shots blocked |",
              "|---|---|---|---|---|"]
    for i, (path, v) in enumerate(missing, start=1):
        lines.append(f"| {i} | `{path}` | {v['kind']} | "
                     f"{hints.get(v['kind'], '—')} | {len(blocked.get(path, []))} |")
    lines += ["", "---", ""]

    for i, (path, v) in enumerate(missing, start=1):
        sh = blocked.get(path, [])
        lines += [f"## {i}. `{path}`",
                  f"**{v['kind']}** · size hint {hints.get(v['kind'], 'see prompt')}"
                  + (f" · blocks {len(sh)} shot(s): {', '.join(sh[:8])}"
                     f"{' …' if len(sh) > 8 else ''}" if sh else ""), "",
                  "```", v["prompt"].strip(), "```", ""]
    return "\n".join(lines)


def print_report(r: dict, root: str) -> None:
    print(f"\n  {r['episode']}  {r['title']}   [{r['mode']}]  "
          f"{r['resolution']}  {r['steps']} steps")
    print(f"  model        {r.get('model', '')}")
    print(f"  lora         {r.get('lora', '')}")
    print(f"  {'-' * 60}")
    print(f"  {r['sequences']} sequences, {r['shots']} shots")
    print(f"  runtime      {mmss(r['delivered_s'])}  "
          f"(scripted {mmss(r['target_s'])}, +{r['pad_frames']}f grid pad)")
    print(f"  audio        " + ", ".join(f"{n}x {p}" for p, n in r["policies"].items()))
    missing = [p for p in r["needed"] if not os.path.isfile(os.path.join(root, p))]
    have = len(r["needed"]) - len(missing)
    print(f"  references   {have}/{len(r['needed'])} on disk"
          + (f", {len(missing)} to make" if missing else ""))
    for w in r["warnings"]:
        print(f"    ! {w}")
    print()


def print_pacing(story: ir.Episode, series_cfg: dict, fps: float = 24.0,
                 template: TG.Template | None = None,
                 templates: dict[str, TG.Template] | None = None) -> int:
    """Every dialogue shot measured against the rate it forces on the delivery,
    in the window the target will actually render (`template`, or per shot
    `templates` in an episode that mixes targets)."""
    default_snap = (template or DEFAULT_TARGET.template).snap
    default = series_cfg.get("speech", {}).get("pace", "normal")
    rows, crammed, tight, gain = [], 0, 0, 0.0
    for seq in story.sequences:
        for shot in seq.shots:
            dialogue = [{"who": d.speaker, "line": d.line} for d in shot.dialogue]
            if not dialogue:
                continue
            snap = (templates[shot.id].snap if templates and shot.id in templates
                    else default_snap)
            pace = shot.pace or default
            t = shot.timing or {}
            if "audio_in" in t:
                dur = t["audio_out"] - t["audio_in"]
            elif t.get("auto") or t.get("model"):
                # `dur: model`: the build's estimate is the dialogue's length
                dur = speech_seconds(dialogue, pace)
            else:
                dur = t.get("seconds", 0.0)
            held = snap(max(1, round(dur * fps))) / fps
            syl, _ = pacing(dialogue)
            rate = forced_rate(dialogue, held)
            fits = snap(max(1, round(speech_seconds(dialogue, pace) * fps))) / fps
            if rate > RATE_CEILING:
                verdict, mark = "CRAMMED", "!!"
                crammed += 1
                gain += fits - held
            elif rate > SPEECH_RATE[pace] + 0.15:
                verdict, mark = "tight", " !"
                tight += 1
                gain += max(0.0, fits - held)
            else:
                verdict, mark = "ok", "  "
            rows.append((mark, shot.id, held, syl, rate, fits, verdict))

    if not rows:
        print("\n  no dialogue in this episode.\n")
        return 0
    print(f"\n  {'shot':10} {'held':>6} {'syl':>4} {'forced':>7} {'fits':>6}  verdict")
    print("  " + "-" * 52)
    for mark, sid, held, syl, rate, fits, verdict in rows:
        print(f"{mark}{sid:10} {held:6.2f} {syl:4} {rate:7.1f} {fits:6.2f}  {verdict}")
    print(f"\n  {crammed} crammed, {tight} tight, {len(rows) - crammed - tight} ok "
          f"of {len(rows)} dialogue shots")
    print(f"  pace '{default}' = {SPEECH_RATE[default]} syl/s, ceiling {RATE_CEILING}")
    if gain > 0:
        print(f"  giving every one of them room adds {gain:.2f}s of runtime")
    print("\n  Fix by lengthening (`dur:` or `dur: auto`), cutting syllables, or\n"
          "  splitting the shot. `pace: fast` marks a rush that is deliberate.\n")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("series", help="the series config, e.g. series.json")
    ap.add_argument("script", help="the episode script, e.g. ep01.md")
    ap.add_argument("-o", "--out", default=".", help="project root (where shotlist/ lives)")
    ap.add_argument("--proxy", action="store_true", help="emit the low-res animatic pass")
    ap.add_argument("--check", action="store_true", help="validate and report, write nothing")
    ap.add_argument("--pace", action="store_true",
                    help="report dialogue pacing per shot and write nothing")
    args = ap.parse_args()
    # a show's own targets (<show>/targets/<id>/target.json) count for this
    # build: `target:` lines and profiles may name them (Phase 12)
    TG.add_thread_root(args.out)

    try:
        # the series config first, and on its own, so a problem in it is
        # reported against series.json rather than against the script
        try:
            series_cfg = load_series_config(args.series)
        except ValueError as exc:
            print(f"\n  error in {os.path.basename(args.series)}: {exc}\n", file=sys.stderr)
            return 1
        with open(args.script, encoding="utf-8") as fh:
            # parse -> story IR
            story = parse_story(fh.read(), subject_ids(series_cfg), character_ids(series_cfg),
                                series_info(series_cfg), variant_of(series_cfg))
        # -> the video target each shot renders on (profiles and target:
        # lines checked here) -> each target compiles its own shots
        groups = TG.episode_targets(story, series_cfg)
        target = groups[0][0]
        if args.pace:
            per_shot = {sid: t.template for t, ids in groups[1:] for sid in ids}
            return print_pacing(story, series_cfg, template=target.template,
                                templates=per_shot)
        built = compile_groups(story, series_cfg, "proxy" if args.proxy else "final", groups)
        _, doc, report = built[0]
    except (ScriptError, ValueError, KeyError) as exc:
        print(f"\n  error in {os.path.basename(args.script)}: {exc}\n", file=sys.stderr)
        return 1
    except FileNotFoundError as exc:
        print(f"\n  {exc}\n", file=sys.stderr)
        return 1

    print_report(report, args.out)
    for t, _, rep in built[1:]:
        print(f"  target {t.id} ({t.short}): {rep['shots']} shot(s) of this episode")
        print_report(rep, args.out)
    if len(built) > 1:
        # one work order for the episode: every target's refs, the series
        # target's first (a later target only adds what it alone needs)
        report = dict(report, needed=dict(report["needed"]),
                      blocked_shots={k: list(v) for k, v in report["blocked_shots"].items()},
                      size_hints=dict(report.get("size_hints", SIZE_HINT)))
        for _, _, rep in built[1:]:
            for p, v in rep["needed"].items():
                report["needed"].setdefault(p, v)
            for p, ids in rep.get("blocked_shots", {}).items():
                have = report["blocked_shots"].setdefault(p, [])
                have += [i for i in ids if i not in have]
            for k, v in (rep.get("size_hints") or {}).items():
                report["size_hints"].setdefault(k, v)
    if args.check:
        return 0

    os.makedirs(os.path.join(args.out, "shotlist"), exist_ok=True)

    # One shotlist per pass for the series target, and one per other target
    # that some shot renders on (shotlist.<target>[_proxy].json).
    sfx = "_proxy" if args.proxy else ""
    sl = os.path.join(args.out, "shotlist", f"shotlist{sfx}.json")
    written = [sl]
    with open(sl, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=2)
    for t, tdoc, _ in built[1:]:
        p = os.path.join(args.out, "shotlist", f"shotlist.{t.id}{sfx}.json")
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(tdoc, fh, ensure_ascii=False, indent=2)
        written.append(p)
    # a target no shot uses any more leaves no stale file behind
    keep = {os.path.basename(p) for p in written}
    for n in os.listdir(os.path.join(args.out, "shotlist")):
        if (n.startswith("shotlist.") and n.endswith(f"{sfx}.json") and n not in keep
                and (args.proxy or not n.endswith("_proxy.json"))):
            os.remove(os.path.join(args.out, "shotlist", n))
    # The story IR: model-free and pass-free, so both passes write the same file.
    ir_path = os.path.join(args.out, "shotlist", "shots.json")
    with open(ir_path, "w", encoding="utf-8") as fh:
        json.dump(story.to_json(), fh, ensure_ascii=False, indent=2)
    # Suffix the proxy's work order the way the shotlist is suffixed. A proxy
    # pass can legitimately need fewer assets (generated voices need no
    # samples), and letting it overwrite the canonical list would quietly drop
    # items the final pass still requires.
    sfx = "_proxy" if args.proxy else ""
    todo = os.path.join(args.out, f"refs_todo{sfx}.md")
    with open(todo, "w", encoding="utf-8") as fh:
        fh.write(render_todo(report, args.out))

    # machine-readable twin, for driving a batch image run
    todo_json = os.path.join(args.out, f"refs_todo{sfx}.json")
    with open(todo_json, "w", encoding="utf-8") as fh:
        json.dump([
            {"path": p, "kind": v["kind"], "prompt": v["prompt"],
             "size_hint": report.get("size_hints", SIZE_HINT).get(v["kind"], ""),
             "exists": os.path.isfile(os.path.join(args.out, p)),
             "blocks_shots": report.get("blocked_shots", {}).get(p, [])}
            for p, v in report["needed"].items()
        ], fh, ensure_ascii=False, indent=2)

    for p in written:
        print(f"  -> {p}")
    print(f"  -> {ir_path}")
    print(f"  -> {todo}")
    print(f"  -> {todo_json}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
