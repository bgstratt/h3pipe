#!/usr/bin/env python3
"""
h3publish.py — the episode as it goes out: the series intro, the cut, the outro.

    python h3.py publish Shows\\ep05              # the final review cut
    python h3.py publish Shows\\ep05 --proxy      # the proxy review cut
    python h3.py publish Shows\\ep05 --input some.mp4 --check

`h3.py master` (and the editor's Master) run this on the master they make, so a
master is the finished episode, titles included, whenever the clips are there.

The intro and outro are clips made once for the whole series: _titles/INTRO.mp4
and _titles/OUTRO.mp4 in the show folder (or the episode's), unless the series
config's `publish` block names others (paths relative to the episode folder). The
episode's title, from the script's `= ep05  Title` line, is written under the
series title the clips already carry: faded in on the intro, faded out with the
title on the outro. One font file, size and colour for every episode, so the
titles match. Output: <episode>/publish/<input name>.mp4.

The cut's picture is copied, not re-encoded. Only the two title clips are
encoded, scaled to the cut's size and rate with the same libx264 settings
h3assemble used for the cut (its `master` or `review` quality, whichever gives
the cut's own H.264 headers), and the three are joined with the concat
demuxer. The sound is re-encoded as one track, so there are no gaps at the
joins. Before it is kept, the result is checked: the frame count adds up and
the frames on both sides of each join decode to exactly the frames that went
in. When the cut can't be copied (not an h3assemble encode, another codec, a
check fails), the whole thing is re-encoded instead and the reason is printed;
`--reencode` asks for that directly.

    "publish": {
      "intro": "../_titles/INTRO.mp4",
      "outro": "../_titles/OUTRO.mp4",
      "subtitle": {
        "font": "C:/Windows/Fonts/segoescb.ttf",
        "size": 0.058,           # text height, a fraction of the frame height
        "y": 0.56,               # top of the text, a fraction of the frame height
        "color": "#F4E6D0", "glow": "#FFB45A@0.8", "glow_size": 0.008,
        "intro_in": [4.0, 5.0],  # seconds into the intro: fade starts, fully on
        "outro_in": [0.0, 1.0],  # seconds into the outro: fade in ...
        "outro_out": [4.5, 5.5]  # ... and out
      }
    }

Every `subtitle` key is optional; the values above are the defaults, and
`intro` / `outro` default to the _titles files. Stdlib only; needs ffmpeg and
ffprobe on PATH.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import h3assemble  # noqa: E402
import h3edit  # noqa: E402
import h3takes  # noqa: E402

SUBTITLE = {
    "font": "C:/Windows/Fonts/segoescb.ttf",
    "size": 0.058,
    "y": 0.56,
    "color": "#F4E6D0",
    "glow": "#FFB45A@0.8",
    "glow_size": 0.008,
    "intro_in": [4.0, 5.0],
    "outro_in": [0.0, 1.0],
    "outro_out": [4.5, 5.5],
}
AUDIO = ["-c:a", "aac", "-b:a", "256k"]
JOIN_FRAMES = 12      # frames checked on each side of a join
# --progress: `##h3publish {"stage", "done", "total", "text"}` per step (titles,
# join, verify, reencode with a frame count), then one `done` line saying how the
# picture got there: {"stage": "done", "picture": "copied" | "re-encoded", "why"}.
# h3master reads it into the master's report.
PROGRESS = "##h3publish "
# what has to agree for the cut's picture to be copied next to the title clips
MATCH = ("codec_name", "profile", "pix_fmt", "width", "height", "r_frame_rate",
         "field_order", "color_range", "color_space", "color_primaries", "color_transfer")


class PublishError(Exception):
    pass


def run(cmd: list[str], cwd: str | None = None, timeout: int = 3600) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)


_progress = False


def say(stage: str, done: int | None = None, total: int | None = None, text: str = "",
        **more) -> None:
    """A progress line, when --progress asked for them."""
    if _progress:
        h3edit.progress_line(PROGRESS, {"stage": stage, "done": done, "total": total,
                                        "text": text, **more})


def run_counted(cmd: list[str], total: int, stage: str, cwd: str | None = None,
                timeout: int = 3600) -> subprocess.CompletedProcess:
    """`run`, with ffmpeg's frame count sent as progress lines as it goes (only
    when --progress asked for them; otherwise it is `run`)."""
    if not _progress:
        return run(cmd, cwd, timeout)
    import threading
    cmd = cmd[:1] + ["-progress", "pipe:1", "-nostats"] + cmd[1:]
    p = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    err: list[str] = []
    drain = threading.Thread(target=lambda: err.append(p.stderr.read()), daemon=True)
    drain.start()
    killer = threading.Timer(timeout, p.kill)
    killer.start()
    last = -1
    try:
        for line in p.stdout:
            if line.startswith("frame="):
                try:
                    n = int(line[6:].strip())
                except ValueError:
                    continue
                if n // 240 != last // 240:            # every 10 s of picture or so
                    say(stage, n, total)
                last = n
    finally:
        p.stdout.close()
        rc = p.wait()
        killer.cancel()
        drain.join(5)
    return subprocess.CompletedProcess(cmd, rc, "", "".join(err))


# ---------------------------------------------------------------------------
# what goes in
# ---------------------------------------------------------------------------

def episode_title(script: str) -> str:
    """The title on the script's `= ep05  Title` line ('' when it has none)."""
    with open(script, encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if s.startswith("="):
                parts = s[1:].strip().split(None, 1)
                return parts[1].strip() if len(parts) > 1 else ""
    return ""


def publish_config(series_cfg: dict) -> dict:
    """The series config's `publish` block with the subtitle defaults filled in."""
    block = dict(series_cfg.get("publish") or {})
    block["subtitle"] = {**SUBTITLE, **(block.get("subtitle") or {})}
    return block


def default_input(root: str, pass_: str) -> str | None:
    """The pass's review cut. (A master gets its titles from h3master itself.)"""
    name = os.path.basename(os.path.normpath(root))
    cut = os.path.join(root, h3takes.pass_subfolder(pass_), f"{name}.mp4")
    return cut if os.path.isfile(cut) else None


def probe(path: str) -> dict:
    """The picture's stream (plus `extradata`, its H.264 headers), frame count,
    duration and whether there is sound."""
    r = run(["ffprobe", "-v", "error", "-count_packets", "-show_streams", "-show_data",
             "-show_entries", "format=duration", "-of", "json", path], timeout=600)
    if r.returncode != 0:
        raise PublishError(f"ffprobe can't read {path}: {r.stderr.strip()}")
    d = json.loads(r.stdout)
    streams = d.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    if not v:
        raise PublishError(f"{path} has no picture")
    num, _, den = v["r_frame_rate"].partition("/")
    tb = v.get("time_base", "1/12288").partition("/")[2]
    return {**{k: v.get(k) for k in MATCH},
            "width": int(v["width"]), "height": int(v["height"]),
            "fps": float(num) / float(den or 1),
            "frames": int(v.get("nb_read_packets") or 0),
            "start": float(v.get("start_time") or 0),
            "timescale": int(tb or 12288),
            "sar": v.get("sample_aspect_ratio") or "N/A",
            "extradata": (v.get("extradata") or "").strip(),
            "duration": float(d.get("format", {}).get("duration") or 0),
            "picture_s": float(v.get("duration") or 0),
            "audio": any(s.get("codec_type") == "audio" for s in streams)}


# ---------------------------------------------------------------------------
# the filter graphs
# ---------------------------------------------------------------------------

def alpha_expr(fade_in: list | None, fade_out: list | None = None) -> str:
    """drawtext's alpha over t: 0 before the fade in, 1 held, 0 after the fade out."""
    def ramp(a: float, b: float) -> str:  # 0 at a, 1 at b
        return f"clip((t-{a:g})/{max(b - a, 1e-3):g},0,1)" if b > a else f"gte(t,{a:g})"
    up = ramp(*fade_in) if fade_in else "1"
    if not fade_out:
        return up
    down = f"(1-{ramp(*fade_out)})"
    return f"{up}*{down}"


def _text(sub: dict, h: int, color: str, alpha: str) -> str:
    # textfile/fontfile are bare names in ffmpeg's working folder: no drive
    # colons or backslashes to escape on Windows.
    return (f"drawtext=textfile=title.txt:fontfile=font{os.path.splitext(sub['font'])[1]}"
            f":fontsize={max(8, round(sub['size'] * h))}:fontcolor={color}"
            f":x=(w-text_w)/2:y={round(sub['y'] * h)}:alpha='{alpha}'")


def titled(label: str, out: str, sub: dict, w: int, h: int, fps: float,
           dur: float, alpha: str, tags: str = "setsar=1") -> list[str]:
    """[label] scaled to the cut, the subtitle drawn with a soft glow under it -> [out]v.
    `tags` sets the picture's aspect and colour tags (see cut_tags)."""
    glow = max(0.5, sub["glow_size"] * h)
    return [
        f"[{label}]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
        f"fps={fps:g},format=yuv420p[{out}b]",
        f"color=c=black@0:s={w}x{h}:r={fps:g}:d={dur:.3f},format=rgba,"
        f"{_text(sub, h, sub['glow'], alpha)},gblur=sigma={glow:.2f}[{out}g]",
        f"[{out}b][{out}g]overlay=format=auto,{_text(sub, h, sub['color'], alpha)},"
        f"format=yuv420p,{tags}[{out}v]",
    ]


def sound_graph(pieces: list[tuple[int, bool, float]], silence: int, first: int = 0) -> list[str]:
    """(input, has sound, seconds) per piece -> each cut or padded to its picture,
    in one rate and layout, joined into [a]."""
    fmt = "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo"
    lines, labels = [], ""
    for n, (idx, has, secs) in enumerate(pieces, first):
        src = idx if has else silence
        lines.append(f"[{src}:a]{fmt},apad,atrim=0:{secs:.6f},asetpts=PTS-STARTPTS[a{n}]")
        labels += f"[a{n}]"
    lines.append(f"{labels}concat=n={len(pieces)}:v=0:a=1[a]")
    return lines


def filter_graph(cut: dict, intro: dict | None, outro: dict | None, sub: dict) -> str:
    """The full re-encode. Inputs in order: [intro], cut, [outro], silence. Output [v] [a]."""
    w, h, fps = cut["width"], cut["height"], cut["fps"]
    lines, parts, pieces, i = [], [], [], 0
    silence = int(intro is not None) + 1 + int(outro is not None)
    if intro:
        lines += titled(f"{i}:v", "in", sub, w, h, fps, intro["duration"],
                        alpha_expr(sub["intro_in"]))
        parts.append("[inv]")
        pieces.append((i, intro["audio"], intro["duration"]))
        i += 1
    lines.append(f"[{i}:v]setsar=1,format=yuv420p[cv]")
    parts.append("[cv]")
    pieces.append((i, cut["audio"], cut["duration"]))
    i += 1
    if outro:
        lines += titled(f"{i}:v", "out", sub, w, h, fps, outro["duration"],
                        alpha_expr(sub["outro_in"], sub["outro_out"]))
        parts.append("[outv]")
        pieces.append((i, outro["audio"], outro["duration"]))
    lines.append(f"{''.join(parts)}concat=n={len(parts)}:v=1:a=0[v]")
    lines += sound_graph(pieces, silence)
    return ";\n".join(lines)


# ---------------------------------------------------------------------------
# copying the cut's picture
# ---------------------------------------------------------------------------

def cut_tags(cut: dict) -> str:
    """Filters giving a title clip the cut's pixel aspect and colour tags: x264
    writes both into the stream's headers, which must match the cut's. A clip
    from ComfyUI is tagged BT.709; an h3assemble cut is untagged."""
    sar = "setsar=1" if cut["sar"] not in ("N/A", "0:1") else "setsar=0"
    tag = {"range": "color_range", "color_primaries": "color_primaries",
           "color_trc": "color_transfer", "colorspace": "color_space"}
    return sar + ",setparams=" + ":".join(f"{k}={cut[v] or 'unknown'}" for k, v in tag.items())


def x264_crf(path: str) -> float | None:
    """The crf x264 wrote into the file's settings message, if it is there."""
    with open(path, "rb") as f:
        head = f.read(4 << 20)
    m = re.search(rb"x264 - core.*?crf=([0-9.]+)", head, re.S)
    return float(m.group(1)) if m else None


def quality_order(path: str) -> list[str]:
    """h3assemble's qualities, the one the cut's crf names first."""
    crf = x264_crf(path)
    order = sorted(h3assemble.QUALITY_ARGS)
    for q in order:
        args = h3assemble.QUALITY_ARGS[q]
        if crf is not None and float(args[args.index("-crf") + 1]) == crf:
            order.remove(q)
            return [q] + order
    return order


def copy_blocker(cut: dict) -> str | None:
    """Why the cut's picture can't be copied as it is, or None."""
    if cut["codec_name"] != "h264":
        return f"the cut is {cut['codec_name']}, not H.264"
    if cut["pix_fmt"] != "yuv420p":
        return f"the cut is {cut['pix_fmt']}, not yuv420p"
    if cut["sar"] not in ("N/A", "1:1", "0:1"):
        return f"the cut's pixels aren't square ({cut['sar']})"
    if not cut["extradata"]:
        return "the cut has no H.264 headers to match"
    if not cut["frames"]:
        return "the cut's frames can't be counted"
    return None


def encode_title(clip: str, dst: str, tmp: str, cut: dict, sub: dict, alpha: str,
                 quality: str) -> None:
    """The title clip with the subtitle drawn on, picture only, encoded the way
    h3assemble.conform encodes a clip at `quality`."""
    w, h, fps = cut["width"], cut["height"], cut["fps"]
    src = probe(clip)
    frames = src["frames"] or round(src["duration"] * fps)
    graph = ";\n".join(titled("0:v", "t", sub, w, h, fps, frames / fps, alpha,
                                cut_tags(cut)))
    with open(os.path.join(tmp, "title_graph.txt"), "w", encoding="utf-8") as f:
        f.write(graph)
    r = run(["ffmpeg", "-y", "-v", "error", "-i", clip, "-/filter_complex", "title_graph.txt",
             "-map", "[tv]", "-frames:v", str(frames),
             "-c:v", "libx264", *h3assemble.QUALITY_ARGS[quality], "-pix_fmt", "yuv420p",
             "-r", f"{fps:g}", "-video_track_timescale", str(cut["timescale"]), "-an", dst],
            cwd=tmp)
    if r.returncode != 0:
        raise PublishError(f"encoding {os.path.basename(clip)} failed:\n{r.stderr.strip()}")


def frame_hashes(path: str, first: int, count: int, info: dict) -> list[str]:
    """framemd5 of `count` decoded frames from frame `first` on."""
    at = h3assemble.picture_offset(path) + (first - 0.5) / info["fps"]
    r = run(["ffmpeg", "-v", "error", "-ss", f"{max(0.0, at):.6f}", "-i", path,
             "-map", "0:v:0", "-frames:v", str(count), "-f", "framemd5", "-"], timeout=600)
    if r.returncode != 0 or r.stderr.strip():
        raise PublishError(f"{os.path.basename(path)} doesn't decode cleanly at frame {first}: "
                           f"{r.stderr.strip()[-300:]}")
    return [ln.rsplit(",", 1)[1].strip() for ln in r.stdout.splitlines()
            if ln and not ln.startswith("#")]


def check_joins(out: str, pieces: list[tuple[str, dict]]) -> str | None:
    """None when the output holds every frame and each join decodes to the
    frames that went in; else what is wrong."""
    got = probe(out)
    want = sum(p["frames"] for _, p in pieces)
    if got["frames"] != want:
        return f"the output has {got['frames']} frames, the pieces {want}"
    # every frame there, but a hole in their timing shows only in the length:
    # each frame after it plays late, by less than a frame-content check sees
    if got["picture_s"] and abs(got["picture_s"] - want / got["fps"]) > 0.5 / got["fps"]:
        return (f"the picture runs {got['picture_s'] - want / got['fps']:+.3f}s from its "
                f"{want} frames: a gap in its timing")
    n, at = JOIN_FRAMES, 0
    for (a, pa), (b, pb) in zip(pieces, pieces[1:]):
        at += pa["frames"]
        k = min(n, pa["frames"], pb["frames"])
        expect = frame_hashes(a, pa["frames"] - k, k, pa) + frame_hashes(b, 0, k, pb)
        seen = frame_hashes(out, at - k, 2 * k, got)
        if seen != expect:
            return (f"the frames around the join at frame {at} "
                    f"({os.path.basename(a)} -> {os.path.basename(b)}) don't match what went in")
    return None


def publish_copy(src: str, cut: dict, intro: str | None, outro: str | None, sub: dict,
                 out: str, tmp: str) -> str | None:
    """Intro + cut + outro with the cut's picture copied. Writes `out` and
    returns None, or returns why it couldn't (and writes nothing)."""
    why = copy_blocker(cut)
    if why:
        return why
    titles = [(intro, "intro", alpha_expr(sub["intro_in"])),
              (outro, "outro", alpha_expr(sub["outro_in"], sub["outro_out"]))]
    made: dict[str, str] = {}
    used = None
    for quality in quality_order(src):
        for clip, kind, alpha in titles:
            if clip:
                made[kind] = os.path.join(tmp, f"{kind}.mp4")
                say("titles", text=kind)
                encode_title(clip, made[kind], tmp, cut, sub, alpha, quality)
        if all(probe(p)["extradata"] == cut["extradata"] for p in made.values()):
            used = quality
            break
    if not used:
        return "the title clips can't be encoded with the cut's H.264 headers " \
               "(the cut wasn't encoded by h3assemble)"
    for kind, path in made.items():
        p = probe(path)
        diff = [k for k in MATCH if p[k] != cut[k]]
        if diff:
            return f"the {kind} doesn't match the cut: {', '.join(diff)}"

    # the cut's picture alone goes into the join: the concat demuxer starts the
    # next clip where a clip's longer stream ends, and a cut's sound runs a few
    # milliseconds past its picture (its last clip's AAC), which pushed the outro
    # late and failed check_joins on every real master (ep01, 2026-10-04). Its
    # sound is laid separately below either way.
    pic_only = os.path.join(tmp, "cut_picture.mp4")
    r = run(["ffmpeg", "-y", "-v", "error", "-i", src, "-map", "0:v:0", "-c", "copy",
             pic_only], cwd=tmp)
    if r.returncode != 0:
        return f"the cut's picture couldn't be copied out: {r.stderr.strip()[-300:]}"
    order = ([made["intro"]] if intro else []) + [pic_only] + ([made["outro"]] if outro else [])
    sounds = ([intro] if intro else []) + [src] + ([outro] if outro else [])
    infos = {p: (cut if p == pic_only else probe(p)) for p in order}
    with open(os.path.join(tmp, "join.txt"), "w", encoding="utf-8") as f:
        for p in order:
            f.write(f"file '{p.replace(os.sep, '/')}'\n")
    pieces = []
    for i, (pic, snd) in enumerate(zip(order, sounds), 1):
        pieces.append((i, probe(snd)["audio"] if snd != src else cut["audio"],
                       infos[pic]["frames"] / cut["fps"]))
    silence = len(order) + 1
    with open(os.path.join(tmp, "sound_graph.txt"), "w", encoding="utf-8") as f:
        f.write(";\n".join(sound_graph(pieces, silence)))
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", "join.txt"]
    for s in sounds:
        cmd += ["-i", s]
    joined = os.path.join(tmp, "joined.mp4")
    cmd += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
            "-/filter_complex", "sound_graph.txt", "-map", "0:v:0", "-map", "[a]",
            "-c:v", "copy", *AUDIO, "-video_track_timescale", str(cut["timescale"]),
            "-movflags", "+faststart", joined]
    say("join")
    r = run(cmd, cwd=tmp)
    if r.returncode != 0:
        return f"joining failed: {r.stderr.strip()[-300:]}"
    say("verify", text="checking the joins")
    try:
        why = check_joins(joined, [(p, infos[p]) for p in order])
    except PublishError as e:
        why = str(e)
    if why:
        return why
    shutil.move(joined, out)
    print(f"    picture copied; title clips encoded at h3assemble's {used} quality, "
          f"sound re-encoded")
    return None


def publish_reencode(src: str, cut: dict, intro: str | None, outro: str | None, sub: dict,
                     out: str, tmp: str, quality: str) -> None:
    pi, po = (probe(intro) if intro else None), (probe(outro) if outro else None)
    with open(os.path.join(tmp, "graph.txt"), "w", encoding="utf-8") as f:
        f.write(filter_graph(cut, pi, po, sub))
    cmd = ["ffmpeg", "-y", "-v", "error"]
    for p in (intro, src, outro):
        if p:
            cmd += ["-i", p]
    cmd += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
            "-/filter_complex", "graph.txt", "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", *h3assemble.QUALITY_ARGS[quality],
            *AUDIO, "-movflags", "+faststart", os.path.join(tmp, "out.mp4")]
    total = sum((i["frames"] or round(i["duration"] * cut["fps"])) for i in (pi, cut, po) if i)
    r = run_counted(cmd, total, "reencode", cwd=tmp)
    if r.returncode != 0:
        raise PublishError(f"ffmpeg failed:\n{r.stderr.strip()}")
    shutil.move(os.path.join(tmp, "out.mp4"), out)
    print(f"    re-encoded at {quality} quality")


# ---------------------------------------------------------------------------
# the command
# ---------------------------------------------------------------------------

TITLES = {"intro": "INTRO.mp4", "outro": "OUTRO.mp4"}


def _clip(root: str, block: dict, key: str) -> str | None:
    """The series config's publish.<key>, else _titles/INTRO.mp4 (OUTRO.mp4) in
    the episode folder or the show folder above it, else None."""
    rel = block.get(key)
    if rel:
        path = os.path.normpath(os.path.join(root, rel))
        if not os.path.isfile(path):
            raise PublishError(f"publish.{key} is {rel}, which isn't there ({path})")
        return path
    for d in (root, os.path.dirname(os.path.normpath(root))):
        path = os.path.join(d, "_titles", TITLES[key])
        if os.path.isfile(path):
            return path
    return None


def titles(root: str) -> tuple[str | None, str | None]:
    """The episode's (intro, outro) clips, either None when there isn't one:
    the series config's publish block, else the _titles files."""
    cfg = h3edit.episode_series_config(root)
    block = {}
    if cfg:
        with open(cfg, encoding="utf-8") as f:
            block = publish_config(json.load(f))
    return _clip(root, block, "intro"), _clip(root, block, "outro")


def publish(root: str, src: str | None = None, pass_: str = "final", out: str | None = None,
            quality: str | None = None, check: bool = False, reencode: bool = False) -> str:
    root = os.path.abspath(root)
    script = h3edit.episode_script(root)
    cfg_path = h3edit.episode_series_config(root)
    if not script or not cfg_path:
        raise PublishError(f"{root} needs its script and a series.json")
    with open(cfg_path, encoding="utf-8") as f:
        block = publish_config(json.load(f))
    sub = block["subtitle"]
    if not os.path.isfile(sub["font"]):
        raise PublishError(f"publish.subtitle.font {sub['font']} isn't there")
    src = os.path.abspath(src) if src else default_input(root, pass_)
    if not src or not os.path.isfile(src):
        raise PublishError("nothing to publish: assemble the episode first, "
                           "or name a file with --input")
    intro, outro = _clip(root, block, "intro"), _clip(root, block, "outro")
    if not intro and not outro:
        raise PublishError("no intro or outro: put INTRO.mp4 / OUTRO.mp4 in the show's _titles "
                           "folder, or name them in the series config's publish block")
    title = episode_title(script)
    name = os.path.splitext(os.path.basename(src))[0]
    out = os.path.abspath(out or os.path.join(root, "publish", f"{name}.mp4"))
    if quality is None:
        quality = "master" if os.path.basename(os.path.dirname(src)) == "master" else "review"

    cut = probe(src)
    print(f"  {os.path.basename(root)}  \"{title}\"")
    print(f"    intro  {intro or '(none)'}")
    print(f"    cut    {src}  {cut['width']}x{cut['height']} {cut['fps']:g}fps "
          f"{cut['duration']:.1f}s")
    print(f"    outro  {outro or '(none)'}")
    print(f"    font   {sub['font']}")
    print(f"    -> {out}")
    if check:
        why = copy_blocker(cut)
        print(f"    {'picture would be re-encoded: ' + why if why else 'picture can be copied'}")
        return out
    if not title:
        print("  ! the script's = line has no title: the intro and outro go out without one")

    os.makedirs(os.path.dirname(out), exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="h3publish_") as tmp:
        with open(os.path.join(tmp, "title.txt"), "w", encoding="utf-8") as f:
            f.write(title)
        shutil.copyfile(sub["font"], os.path.join(tmp, "font" + os.path.splitext(sub["font"])[1]))
        why = "--reencode" if reencode else publish_copy(src, cut, intro, outro, sub, out, tmp)
        if why:
            print(f"  ! re-encoding the whole cut: {why}")
            say("reencode", 0, None, why)
            publish_reencode(src, cut, intro, outro, sub, out, tmp, quality)
    got = probe(out)
    print(f"    {got['duration']:.1f}s, {got['frames']} frames")
    print(f"  -> {out}")
    say("done", picture="re-encoded" if why else "copied", why=why or "")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Intro + cut + outro, titled, into <episode>/publish/.")
    ap.add_argument("episode")
    ap.add_argument("--input", default=None,
                    help="the cut to publish (default: the review cut)")
    ap.add_argument("--proxy", dest="pass_", action="store_const", const="proxy", default="final",
                    help="the proxy review cut")
    ap.add_argument("--out", default=None, help="output file (default <episode>/publish/<input>.mp4)")
    ap.add_argument("--reencode", action="store_true",
                    help="re-encode the whole cut instead of copying its picture")
    ap.add_argument("--quality", choices=sorted(h3assemble.QUALITY_ARGS), default=None,
                    help="x264 settings when re-encoding (default: master for a master, "
                         "else review)")
    ap.add_argument("--check", action="store_true", help="report only")
    ap.add_argument("--progress", action="store_true",
                    help="print a ##h3publish progress line per step (for h3master)")
    a = ap.parse_args(argv)
    global _progress
    _progress = a.progress
    try:
        publish(a.episode, a.input, a.pass_, a.out, a.quality, a.check, a.reencode)
    except PublishError as e:
        print(f"  !! {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
