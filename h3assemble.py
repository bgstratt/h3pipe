#!/usr/bin/env python3
"""
h3assemble.py — stitch the rendered shots into one cut.

Reads a shotlist and the episode's cut.json, picks a take for each shot and
concatenates them in cut order.

    python3 h3assemble.py -o .                              # the final cut
    python3 h3assemble.py -o . --shotlist shotlist/shotlist_proxy.json
    python3 h3assemble.py -o . --partial                    # assemble what exists
    python3 h3assemble.py -o . --check                      # report, write nothing

The pass comes from the shotlist's name (`_proxy` in it means proxy, anything
else final); `--pass` overrides it. Takes are read from the pass's folder
(renders/ or renders_proxy/, or `--subfolder`).

cut.json (next to shotlist/) holds one ordered list per pass:

    {"final": [{"shot": "sh010", "take": 2},
               {"shot": "sh030"},
               {"shot": "sh020", "trim_in": 4, "trim_out": 2},
               {"shot": "sh040", "pass": "proxy", "take": 1}],
     "proxy": []}

- Order is the list's order. A script shot the list doesn't name goes after
  its script predecessor; with no cut.json at all that is plain script order.
- `take: N` uses exactly take N; if that take is queued, failed or has no mp4
  the shot counts as missing. No take means the latest usable take: sidecar
  status `ok` and the mp4 is there (takes from before sidecars count as ok).
  `--take N` forces N for every shot and beats the cut.
- An entry whose shot is no longer in the script is an orphan: skipped, noted.
- `pass` naming the other pass makes a placeholder, e.g. a proxy take standing
  in for a final that isn't rendered yet. It is scaled to this cut's size.
- `trim_in`/`trim_out` drop frames from the head/tail, after the dialogue-window
  trim below.

Shots timed against recorded dialogue (`audio_in`/`audio_out` in the
shotlist, written by h3align) are trimmed to their exact window, because H3
renders them rounded up to its frame grid. The trimmed cut lines up with the
recording end to end; `--audio master` lays the recording under it to check
sync. Trimming re-encodes every clip (x264, CRF 16) so they concat cleanly;
so do cut.json trims and placeholders.

This is a review cut, not a conform. It exists so you can watch the episode as
one file the moment enough shots exist — the real edit happens in an NLE against
the dialogue track, where you can trim the grid padding and slip cuts.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

# Sibling modules: ComfyUI's embedded Python (a ._pth install) doesn't put a
# script's own folder on sys.path, so do it here.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import h3jobs  # noqa: E402
import h3takes  # noqa: E402

NOT_RENDERED = "not rendered"


def run(cmd: list[str], timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, timeout=timeout)


def have(tool: str) -> bool:
    return shutil.which(tool) is not None


def has_audio(path: str) -> bool:
    r = run(["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=index", "-of", "csv=p=0", path], timeout=60)
    return bool(r.stdout.strip())


def frame_count(path: str) -> int:
    r = run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
             "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", path],
            timeout=300)
    try:
        return int(r.stdout.decode().strip())
    except (ValueError, AttributeError):
        return -1


def video_size(path: str) -> tuple[int, int] | None:
    r = run(["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", path],
            timeout=60)
    try:
        w, h = r.stdout.decode().strip().split("x")[:2]
        return int(w), int(h)
    except (ValueError, AttributeError):
        return None


def normalise(src: str, dst: str, audio_wav: str | None, fps: float,
              frames: int = 0) -> None:
    """Give every clip an audio track so the concat demuxer can copy streams.

    The concat demuxer refuses a mixed set where some inputs have audio and
    some do not — and `dub`/`clone` shots write a deliberately mute mp4, so a
    real episode is always mixed. Muxing in the shot's own `_h3.wav`, or
    silence, makes the set uniform without re-encoding the video.

    The silence is bounded with `-t`. `anullsrc` is an infinite source and
    ffmpeg has no reason to stop reading it, so without a duration this hangs
    forever instead of writing a file. `-shortest` would also stop it, but that
    is the flag that silently drops the last video frame when H3's audio runs a
    few milliseconds short, so it is not welcome anywhere in this pipeline.
    """
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", src]
    if audio_wav and os.path.isfile(audio_wav):
        cmd += ["-i", audio_wav]
    else:
        if frames <= 0:
            frames = frame_count(src)
        cmd += ["-f", "lavfi", "-t", f"{max(1, frames) / fps:.6f}",
                "-i", "anullsrc=channel_layout=mono:sample_rate=44100"]
    cmd += ["-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-fps_mode", "passthrough", dst]
    r = run(cmd)
    if r.returncode != 0:
        raise RuntimeError(f"normalise failed for {os.path.basename(src)}: "
                           f"{r.stderr.decode('utf-8', 'replace')[-300:]}")


def conform(src: str, dst: str, audio: str | None, fps: float, frames: int,
            start: int = 0, size: tuple[int, int] | None = None) -> None:
    """Re-encode one clip to exactly `frames` frames with a matching audio track.

    `audio` is the clip itself (use its own sound), a wav path, or None for
    silence. Every clip in a trimmed cut goes through here so the concat
    demuxer sees identical stream parameters.

    `start` drops that many leading frames (a cut entry's `trim_in`); the
    clip's own sound or wav is cut by the same amount so it stays in sync.
    `size` scales the picture to (width, height), letterboxing if the aspect
    differs: a placeholder from the other pass has the other pass's size, and
    the concat demuxer needs one size for the whole cut. `-frames:v` and the
    bounded, padded audio keep the exact-frame-count guarantee either way.
    """
    dur = f"{frames / fps:.6f}"
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", src]
    silent = False
    if audio == src:
        amap = "0:a:0"
    elif audio and os.path.isfile(audio):
        cmd += ["-i", audio]
        amap = "1:a:0"
    else:
        cmd += ["-f", "lavfi", "-t", dur, "-i", "anullsrc=channel_layout=mono:sample_rate=44100"]
        amap = "1:a:0"
        silent = True
    vf: list[str] = []
    af = ["apad"]
    if start > 0:
        # Frame-exact head trim: `trim` counts decoded frames (seeking with -ss
        # lands on timestamps, not frames), and `setpts` restarts the clock at
        # zero so the concat sees a clip that begins at 0. The sound is cut by
        # the same duration.
        vf += [f"trim=start_frame={start}:end_frame={start + frames}",
               "setpts=PTS-STARTPTS"]
        if not silent:
            af = [f"atrim=start={start / fps:.6f}", "asetpts=PTS-STARTPTS", "apad"]
    if size:
        w, h = size
        vf += [f"scale={w}:{h}:force_original_aspect_ratio=decrease",
               f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2", "setsar=1"]
    cmd += ["-map", "0:v:0", "-map", amap]
    if vf:
        cmd += ["-vf", ",".join(vf)]
    cmd += ["-frames:v", str(frames),
            "-c:v", "libx264", "-crf", "16", "-preset", "medium", "-pix_fmt", "yuv420p",
            "-r", f"{fps:g}", "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
            "-af", ",".join(af), "-t", dur, dst]
    r = run(cmd)
    if r.returncode != 0:
        raise RuntimeError(f"trim failed for {os.path.basename(src)}: "
                           f"{r.stderr.decode('utf-8', 'replace')[-300:]}")


def tc(seconds: float) -> str:
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h):02d}:{int(m):02d}:{s:06.3f}"


def why_unusable(t: h3takes.Take | None, n: int) -> str:
    if t is None:
        return f"t{n:02d} does not exist"
    if t.status == "queued":
        return f"t{n:02d} is still queued"
    if t.status == "failed":
        return f"t{n:02d} failed"
    if not t.has_video:
        return f"t{n:02d} has no mp4"
    return f"t{n:02d} is {t.status}"


def choose_take(root: str, e: h3takes.CutEntry, folder: str | None,
                forced: int | None) -> tuple[h3takes.Take | None, str | None]:
    """The take a cut entry uses, or (None, why not)."""
    n = forced if forced is not None else e.take
    if n is None:
        takes = h3takes.list_takes(root, e.pass_, e.shot, folder)
        t = h3takes.latest_usable(takes)
        if t is not None:
            return t, None
        if not takes:
            return None, NOT_RENDERED
        return None, "no usable take (" + ", ".join(
            why_unusable(x, x.take) for x in takes) + ")"
    try:
        n = int(n)
    except (TypeError, ValueError):
        return None, f"take {n!r} in cut.json is not a number"
    t = h3takes.get_take(root, e.pass_, e.shot, n, folder)
    if t is not None and t.usable:
        return t, None
    src = "--take" if forced is not None else "cut.json"
    return None, f"{why_unusable(t, n)} (picked by {src})"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-o", "--out", default=".", help="project root")
    ap.add_argument("--shotlist", default="shotlist/shotlist.json",
                    help="shotlist to follow (default %(default)s)")
    ap.add_argument("--pass", dest="pass_", choices=h3takes.PASSES, default=None,
                    help="which pass's cut and takes to use (default: proxy if the "
                         "shotlist's name contains _proxy, else final)")
    ap.add_argument("--subfolder", default=None,
                    help="render folder to read this pass's takes from "
                         "(default renders/ or renders_proxy/ by pass)")
    ap.add_argument("--name", default=None, help="output filename (default from shotlist)")
    ap.add_argument("--take", type=int, default=None,
                    help="force a specific take number for every shot (beats cut.json)")
    ap.add_argument("--audio", choices=["auto", "mp4", "h3", "none", "master"], default="auto",
                    help="auto: the mp4's own audio, falling back to the shot's "
                         "_h3.wav when the mp4 is mute. master: the recorded dialogue "
                         "track from the shotlist, laid under the whole cut")
    ap.add_argument("--no-trim", action="store_true",
                    help="keep the grid padding on shots that have an audio window")
    ap.add_argument("--partial", action="store_true",
                    help="assemble the shots that exist instead of refusing")
    ap.add_argument("--check", action="store_true", help="report only")
    args = ap.parse_args()

    pass_ = args.pass_ or ("proxy" if "_proxy" in os.path.basename(args.shotlist)
                           else "final")
    other = "final" if pass_ == "proxy" else "proxy"
    sub = args.subfolder or h3takes.pass_subfolder(pass_)

    def folder_for(src_pass: str) -> str | None:
        # An explicit --subfolder is this pass's folder. A placeholder's take
        # lives in the other pass's standard folder.
        return args.subfolder if src_pass == pass_ else None

    root = os.path.abspath(args.out)
    sl = os.path.join(root, args.shotlist)
    if not os.path.isfile(sl):
        print(f"error: no shotlist at {sl}", file=sys.stderr)
        return 1
    sl = os.path.abspath(sl)

    # A shotlist lives at <project>/shotlist/<name>.json, and renders at
    # <project>/renders/. Pointing --shotlist deep into a project while leaving
    # -o at a parent directory finds the shotlist and then looks for renders in
    # the wrong place — which reports every shot missing rather than saying the
    # root is wrong. Trust the shotlist's own location when it disagrees.
    implied = os.path.dirname(os.path.dirname(sl))
    if implied != root and os.path.isdir(os.path.join(implied, sub)) \
            and not os.path.isdir(os.path.join(root, sub)):
        print(f"\n  note: --shotlist points into {implied}\n"
              f"        but -o is {root}, which has no {sub}/ .\n"
              f"        Using the shotlist's own project root.")
        root = implied
    with open(sl, encoding="utf-8") as fh:
        doc = json.load(fh)
    shots = doc.get("shots", [])
    if os.path.normcase(os.path.abspath(sl)) == os.path.normcase(
            os.path.abspath(os.path.join(root, h3jobs.shotlist_rel(pass_)))):
        # an episode that mixes targets: the other targets' shots are in
        # shotlist.<target>[_proxy].json; the cut follows script order
        shots = [d["shots"][i] for d, i in h3jobs.episode_shots(root, pass_)]
    by_id = {s["id"]: s for s in shots}
    fps = 24.0
    width = doc.get("defaults", {}).get("width")
    height = doc.get("defaults", {}).get("height")

    for tool in ("ffmpeg", "ffprobe"):
        if not have(tool):
            print(f"error: {tool} not found on PATH", file=sys.stderr)
            return 1

    cut_data = h3takes.load_cut(root)
    has_cut = bool(cut_data.get(pass_))
    entries = h3takes.resolve_cut(cut_data, pass_, [s["id"] for s in shots])

    # ---- resolve each cut entry to a file -------------------------------
    windows = [s["audio_in"] for s in shots if "audio_in" in s]
    base_in = min(windows) if windows else 0.0
    plan, missing, orphans, bad = [], [], [], []
    rows = []              # (kind, entry, plan item or reason), in cut order
    for e in entries:
        if e.orphan:
            orphans.append(e.shot)
            rows.append(("orphan", e, None))
            continue
        s = by_id[e.shot]
        if e.pass_ not in h3takes.PASSES:
            t, why = None, f"unknown pass {e.pass_!r} in cut.json"
        else:
            t, why = choose_take(root, e, folder_for(e.pass_), args.take)
        if t is None:
            missing.append((e.shot, why))
            rows.append(("missing", e, why))
            continue
        keep = 0
        if "audio_in" in s and not args.no_trim:
            keep = (round((s["audio_out"] - base_in) * fps)
                    - round((s["audio_in"] - base_in) * fps))
        trim_in, trim_out = max(0, e.trim_in), max(0, e.trim_out)

        # Both passes share lengths, so a placeholder is checked against the
        # same shot's length.
        n = frame_count(t.paths.mp4)
        # a take rendered on another target (retargeted) has that target's
        # length, which its sidecar records
        want = int((t.sidecar or {}).get("length") or s["length"])
        if n > 0 and n != want:
            bad.append(f"{e.shot}: {n} frames on disk, shotlist says {want}")
        on_disk = n if n > 0 else want
        if keep and keep > on_disk:
            bad.append(f"{e.shot}: window needs {keep} frames but the clip has {on_disk}")
            keep = on_disk
        span = keep or on_disk
        used = span - trim_in - trim_out
        if used < 1:
            why = (f"trim_in {trim_in} + trim_out {trim_out} leave nothing of "
                   f"t{t.take:02d}'s {span} frames")
            missing.append((e.shot, why))
            rows.append(("missing", e, why))
            continue
        p = {"id": e.shot, "take": t.take, "src": e.pass_, "path": t.paths.mp4,
             "placeholder": e.pass_ != pass_, "listed": e.in_cut_file,
             "keep": keep, "trim_in": trim_in, "trim_out": trim_out,
             "frames": n, "used": used, "audio_in": s.get("audio_in"),
             "wav": t.paths.h3_wav if os.path.isfile(t.paths.h3_wav) else None,
             "expected": s["length"], "policy": s.get("audio_policy", "?")}
        plan.append(p)
        rows.append(("ok", e, p))

    print(f"\n  {width}x{height}   {pass_} pass   "
          f"{len(plan)}/{len(shots)} shots rendered"
          f"{'   (cut.json)' if has_cut else ''}")

    if orphans:
        print(f"  orphaned in cut.json, not in the script, skipped ({len(orphans)}): "
              f"{', '.join(orphans)}")
    if missing:
        ids = [m[0] for m in missing]
        print(f"  missing ({len(missing)}): "
              f"{', '.join(ids[:10])}{' …' if len(ids) > 10 else ''}")
        for sid, why in missing:
            if why != NOT_RENDERED:
                print(f"    {sid}: {why}")
        if not args.partial and not args.check:
            print("\n  refusing to assemble an incomplete cut. Re-run with --partial "
                  "to stitch what exists.\n", file=sys.stderr)
            return 1
    if not plan:
        print(f"\n  nothing rendered yet — looked for "
              f"{os.path.join(root, sub, '<shot_id>', '<shot_id>_t*.mp4')}\n"
              f"  If your renders are elsewhere, point -o at the project root "
              f"(the folder that holds {sub}/ and shotlist/).\n")
        return 1

    # ---- timings, frame-count report -------------------------------------
    total = 0
    print()
    for p in plan:
        p["start"] = total / fps
        total += p["used"]
    windowed = [p for p in plan if p["keep"]]
    trimmed = [p for p in plan if p["trim_in"] or p["trim_out"]]
    placeholders = [p for p in plan if p["placeholder"]]
    if windowed:
        print(f"  trimming {len(windowed)} shot(s) to their dialogue "
              f"windows ({sum(p['frames'] - p['keep'] for p in windowed if p['frames'] > 0)} "
              f"padding frames dropped)")
    if trimmed:
        print(f"  trimming {len(trimmed)} shot(s) per cut.json "
              f"({sum(p['trim_in'] + p['trim_out'] for p in trimmed)} frames dropped)")
    if placeholders:
        print(f"  {len(placeholders)} placeholder(s) from the {other} pass, "
              f"scaled to {width}x{height}")
    if bad:
        print("  frame-count mismatches (the edit will drift):")
        for b in bad:
            print(f"    ! {b}")
        print()
    if args.audio == "master":
        drift = [p["id"] for p in trimmed if p["audio_in"] is not None]
        if drift:
            print(f"  ! --audio master: {', '.join(drift)} have a dialogue window and a "
                  f"cut.json trim; the master track will drift from there on")
        order = [p["id"] for p in plan]
        if order != [s["id"] for s in shots if s["id"] in set(order)]:
            print("  ! --audio master: the cut is not in script order; the master "
                  "track only lines up with script order")

    print(f"  assembled runtime {tc(total / fps)}  ({total} frames)")
    if args.check:
        print()
        print(f"    {'in':>12}  {'shot':10} {'take':4}  {'pass':5}  {'used/disk':>10}  "
              f"{'trim in/out':11}  policy / flags")
        for kind, e, p in rows:
            if kind == "orphan":
                print(f"    {'--':>12}  {e.shot:10} {'--':4}  {'--':5}  {'--':>10}  "
                      f"{'--':11}  orphan: in cut.json, not in the script (skipped)")
                continue
            if kind == "missing":
                n = args.take if args.take is not None else e.take
                take = f"t{n:02d}" if isinstance(n, int) else "--"
                print(f"    {'--':>12}  {e.shot:10} {take:4}  {e.pass_:5}  {'--':>10}  "
                      f"{'--':11}  MISSING: {p}")
                continue
            flags = []
            if p["placeholder"]:
                flags.append(f"placeholder({p['src']})")
            if has_cut and not p["listed"]:
                flags.append("not in cut.json")
            if not p["wav"]:
                flags.append("no _h3.wav")
            trim = f"{p['trim_in']}/{p['trim_out']}" if (p["trim_in"] or p["trim_out"]) else "-"
            disk = p["frames"] if p["frames"] > 0 else "?"
            print(f"    {tc(p['start']):>12}  {p['id']:10} {'t%02d' % p['take']:4}  {p['src']:5}  "
                  f"{str(p['used']) + '/' + str(disk):>10}  {trim:11}  {p['policy']}"
                  + (f"   [{', '.join(flags)}]" if flags else ""))
        print()
        return 0

    # ---- normalise + concat ---------------------------------------------
    base = args.name or (os.path.splitext(os.path.basename(sl))[0]
                         .replace("shotlist", doc.get("episode", "cut")) + ".mp4")
    if not base.endswith(".mp4"):
        base += ".mp4"
    out_path = os.path.join(root, sub, base)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    # Dialogue windows, cut.json trims and placeholders all need a re-encode;
    # once one clip is re-encoded every clip is, so the concat demuxer sees one
    # set of stream parameters.
    reencode = bool(windowed or trimmed or placeholders)
    size = None
    if placeholders:
        if width and height:
            size = (int(width), int(height))
        else:
            native = next((p for p in plan if not p["placeholder"]), None)
            size = video_size(native["path"]) if native else None
            if size is None:
                print("  ! no width/height in the shotlist to scale placeholders to")

    with tempfile.TemporaryDirectory(prefix="h3asm_") as tmp:
        listing = os.path.join(tmp, "concat.txt")
        with open(listing, "w", encoding="utf-8") as fh:
            for i, p in enumerate(plan):
                src = p["path"]
                if args.audio == "none":
                    wav = None
                elif args.audio == "h3":
                    wav = p["wav"]
                elif args.audio == "mp4":
                    wav = None if has_audio(src) else p["wav"]
                else:                                   # auto
                    wav = None if has_audio(src) else p["wav"]
                norm = os.path.join(tmp, f"{i:04d}.mp4")
                if reencode:
                    conform(src, norm, None if args.audio in ("none", "master") else
                            (p["wav"] if args.audio == "h3" or not has_audio(src) else src),
                            fps, p["used"], start=p["trim_in"],
                            size=size if p["placeholder"] else None)
                elif args.audio == "h3" or not has_audio(src) or args.audio in ("none", "master"):
                    normalise(src, norm, wav, fps, p.get("frames", 0))
                else:
                    shutil.copy(src, norm)
                fh.write(f"file '{norm.replace(os.sep, '/')}'\n")

        r = run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
                 "-i", listing, "-c", "copy", out_path], timeout=1800)
        if r.returncode == 0 and args.audio == "master":
            track = doc.get("defaults", {}).get("master_track", "")
            track = track if os.path.isabs(track) else os.path.join(root, track)
            if not track or not os.path.isfile(track):
                print(f"  ! --audio master: no recorded track at {track or '<unset>'}; "
                      f"cut left silent")
            else:
                if missing:
                    print("  ! --audio master with missing shots: the recording will run "
                          "out of sync after the first gap")
                start = plan[0]["audio_in"] if plan[0]["audio_in"] is not None else base_in
                start += plan[0]["trim_in"] / fps
                tmp_out = os.path.join(tmp, "master.mp4")
                r = run(["ffmpeg", "-y", "-v", "error", "-i", out_path,
                         "-ss", f"{start:.6f}", "-t", f"{total / fps:.6f}", "-i", track,
                         "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
                         "-c:a", "aac", "-b:a", "192k", "-af", "apad",
                         "-t", f"{total / fps:.6f}", tmp_out], timeout=1800)
                if r.returncode == 0:
                    shutil.move(tmp_out, out_path)
        if r.returncode != 0:
            print(f"\n  concat failed: "
                  f"{r.stderr.decode('utf-8', 'replace')[-400:]}\n", file=sys.stderr)
            return 1

    # ---- shot boundary list, for finding things in the timeline ----------
    edl = os.path.splitext(out_path)[0] + "_shots.txt"
    with open(edl, "w", encoding="utf-8") as fh:
        fh.write(f"# {base}   {len(plan)} shots   {tc(total / fps)}   {pass_} pass\n")
        fh.write(f"# {'in':>12}  {'shot':10} {'take':>4} {'frames':>7}  policy\n")
        for p in plan:
            fh.write(f"{tc(p['start']):>14}  {p['id']:10} {p['take']:>4} "
                     f"{p['used']:>7}  {p['policy']}"
                     + (f"  rec {p['audio_in']:.3f}" if p["audio_in"] is not None else "")
                     + (f"  placeholder({p['src']})" if p["placeholder"] else "")
                     + (f"  trim {p['trim_in']}/{p['trim_out']}"
                        if p["trim_in"] or p["trim_out"] else "")
                     + "\n")

    got = frame_count(out_path)
    print(f"\n  -> {out_path}")
    print(f"  -> {edl}")
    print(f"  output holds {got} frames"
          f"{'' if got == total else f' — expected {total}, check the mismatches above'}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
