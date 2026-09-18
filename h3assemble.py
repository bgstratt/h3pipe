#!/usr/bin/env python3
"""
h3assemble.py — stitch the rendered shots into one cut.

Reads a shotlist, finds each shot's latest take under renders/, and concatenates
them in script order.

    python3 h3assemble.py -o .                              # the final cut
    python3 h3assemble.py -o . --shotlist shotlist/shotlist_proxy.json
    python3 h3assemble.py -o . --partial                    # assemble what exists
    python3 h3assemble.py -o . --check                      # report, write nothing

Shots timed against recorded dialogue (`audio_in`/`audio_out` in the
shotlist, written by h3align) are trimmed to their exact window, because H3
renders them rounded up to its frame grid. The trimmed cut lines up with the
recording end to end; `--audio master` lays the recording under it to check
sync. Trimming re-encodes every clip (x264, CRF 16) so they concat cleanly.

This is a review cut, not a conform. It exists so you can watch the episode as
one file the moment enough shots exist — the real edit happens in an NLE against
the dialogue track, where you can trim the grid padding and slip cuts.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

TAKE_RE = re.compile(r"_t(\d+)\.mp4$", re.I)


def run(cmd: list[str], timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, timeout=timeout)


def have(tool: str) -> bool:
    return shutil.which(tool) is not None


def takes_for(root: str, subfolder: str, shot_id: str) -> list[tuple[int, str]]:
    """Every rendered take of a shot, newest number last."""
    d = os.path.join(root, subfolder, shot_id)
    out = []
    for p in glob.glob(os.path.join(d, f"{shot_id}_t*.mp4")):
        m = TAKE_RE.search(p)
        if m:
            out.append((int(m.group(1)), p))
    return sorted(out)


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


def conform(src: str, dst: str, audio: str | None, fps: float, frames: int) -> None:
    """Re-encode one clip to exactly `frames` frames with a matching audio track.

    `audio` is the clip itself (use its own sound), a wav path, or None for
    silence. Every clip in a trimmed cut goes through here so the concat
    demuxer sees identical stream parameters.
    """
    dur = f"{frames / fps:.6f}"
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", src]
    if audio == src:
        amap = "0:a:0"
    elif audio and os.path.isfile(audio):
        cmd += ["-i", audio]
        amap = "1:a:0"
    else:
        cmd += ["-f", "lavfi", "-t", dur, "-i", "anullsrc=channel_layout=mono:sample_rate=44100"]
        amap = "1:a:0"
    cmd += ["-map", "0:v:0", "-map", amap, "-frames:v", str(frames),
            "-c:v", "libx264", "-crf", "16", "-preset", "medium", "-pix_fmt", "yuv420p",
            "-r", f"{fps:g}", "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
            "-af", "apad", "-t", dur, dst]
    r = run(cmd)
    if r.returncode != 0:
        raise RuntimeError(f"trim failed for {os.path.basename(src)}: "
                           f"{r.stderr.decode('utf-8', 'replace')[-300:]}")


def tc(seconds: float) -> str:
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h):02d}:{int(m):02d}:{s:06.3f}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-o", "--out", default=".", help="project root")
    ap.add_argument("--shotlist", default="shotlist/shotlist.json",
                    help="shotlist to follow (default %(default)s)")
    ap.add_argument("--subfolder", default="renders",
                    help="render folder to read takes from (default %(default)s)")
    ap.add_argument("--name", default=None, help="output filename (default from shotlist)")
    ap.add_argument("--take", type=int, default=None,
                    help="force a specific take number for every shot")
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
    if implied != root and os.path.isdir(os.path.join(implied, args.subfolder)) \
            and not os.path.isdir(os.path.join(root, args.subfolder)):
        print(f"\n  note: --shotlist points into {implied}\n"
              f"        but -o is {root}, which has no {args.subfolder}/ .\n"
              f"        Using the shotlist's own project root.")
        root = implied
    with open(sl, encoding="utf-8") as fh:
        doc = json.load(fh)
    shots = doc.get("shots", [])
    fps = 24.0

    for tool in ("ffmpeg", "ffprobe"):
        if not have(tool):
            print(f"error: {tool} not found on PATH", file=sys.stderr)
            return 1

    # ---- resolve each shot to a file ------------------------------------
    windows = [s["audio_in"] for s in shots if "audio_in" in s]
    base_in = min(windows) if windows else 0.0
    plan, missing = [], []
    for s in shots:
        ts = takes_for(root, args.subfolder, s["id"])
        if args.take is not None:
            ts = [t for t in ts if t[0] == args.take]
        if not ts:
            missing.append(s["id"])
            continue
        take, path = ts[-1]
        wav = os.path.join(root, args.subfolder, s["id"],
                           f"{s['id']}_t{take:02d}_h3.wav")
        keep = 0
        if "audio_in" in s and not args.no_trim:
            keep = (round((s["audio_out"] - base_in) * fps)
                    - round((s["audio_in"] - base_in) * fps))
        plan.append({"id": s["id"], "take": take, "path": path, "keep": keep,
                     "audio_in": s.get("audio_in"),
                     "wav": wav if os.path.isfile(wav) else None,
                     "expected": s["length"], "policy": s.get("audio_policy", "?")})

    print(f"\n  {doc.get('defaults', {}).get('width')}x"
          f"{doc.get('defaults', {}).get('height')}   "
          f"{len(plan)}/{len(shots)} shots rendered")

    if missing:
        print(f"  missing ({len(missing)}): "
              f"{', '.join(missing[:10])}{' …' if len(missing) > 10 else ''}")
        if not args.partial and not args.check:
            print("\n  refusing to assemble an incomplete cut. Re-run with --partial "
                  "to stitch what exists.\n", file=sys.stderr)
            return 1
    if not plan:
        print(f"\n  nothing rendered yet — looked for "
              f"{os.path.join(root, args.subfolder, '<shot_id>', '<shot_id>_t*.mp4')}\n"
              f"  If your renders are elsewhere, point -o at the project root "
              f"(the folder that holds {args.subfolder}/ and shotlist/).\n")
        return 1

    # ---- verify frame counts against the shotlist ------------------------
    total = 0
    bad = []
    print()
    for p in plan:
        n = frame_count(p["path"])
        p["frames"] = n
        p["start"] = total / fps
        if n > 0 and n != p["expected"]:
            bad.append(f"{p['id']}: {n} frames on disk, shotlist says {p['expected']}")
        on_disk = n if n > 0 else p["expected"]
        if p["keep"]:
            if p["keep"] > on_disk:
                bad.append(f"{p['id']}: window needs {p['keep']} frames but the clip has {on_disk}")
                p["keep"] = on_disk
            p["used"] = p["keep"]
        else:
            p["used"] = on_disk
        total += p["used"]
    trimming = any(p["keep"] for p in plan)
    if trimming:
        print(f"  trimming {sum(1 for p in plan if p['keep'])} shot(s) to their dialogue "
              f"windows ({sum(p['frames'] - p['used'] for p in plan if p['keep'] and p['frames'] > 0)} "
              f"padding frames dropped)")
    if bad:
        print("  frame-count mismatches (the edit will drift):")
        for b in bad:
            print(f"    ! {b}")
        print()

    print(f"  assembled runtime {tc(total / fps)}  ({total} frames)")
    if args.check:
        print()
        for p in plan:
            print(f"    {tc(p['start'])}  {p['id']:10} t{p['take']:02d}  "
                  f"{p['used']:4}/{p['frames']:<4}f  {p['policy']}"
                  f"{'' if p['wav'] else '   (no _h3.wav)'}")
        print()
        return 0

    # ---- normalise + concat ---------------------------------------------
    base = args.name or (os.path.splitext(os.path.basename(sl))[0]
                         .replace("shotlist", doc.get("episode", "cut")) + ".mp4")
    if not base.endswith(".mp4"):
        base += ".mp4"
    out_path = os.path.join(root, args.subfolder, base)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

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
                if trimming:
                    conform(src, norm, None if args.audio in ("none", "master") else
                            (p["wav"] if args.audio == "h3" or not has_audio(src) else src),
                            fps, p["used"])
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
        fh.write(f"# {base}   {len(plan)} shots   {tc(total / fps)}\n")
        fh.write(f"# {'in':>12}  {'shot':10} {'take':>4} {'frames':>7}  policy\n")
        for p in plan:
            fh.write(f"{tc(p['start']):>14}  {p['id']:10} {p['take']:>4} "
                     f"{p['used']:>7}  {p['policy']}"
                     + (f"  rec {p['audio_in']:.3f}" if p["audio_in"] is not None else "")
                     + "\n")

    got = frame_count(out_path)
    print(f"\n  -> {out_path}")
    print(f"  -> {edl}")
    print(f"  output holds {got} frames"
          f"{'' if got == total else f' — expected {total}, check the mismatches above'}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
