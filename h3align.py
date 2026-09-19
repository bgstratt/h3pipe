#!/usr/bin/env python3
"""
h3align.py — time a script against recorded dialogue and write the `audio:`
windows h3build needs for lip-synced (dub) shots.

    python h3align.py DeanStories\\ep05 audio\\ep05_dialogue.wav --dry-run
    python h3align.py DeanStories\\ep05 audio\\ep05_dialogue.wav
    python h3align.py DeanStories\\ep05                     # re-align; track from the series config

What it does
    1. Transcribes the recording with word timestamps (faster-whisper), and
       caches the result beside it as <recording>.words.json.
    2. Matches the transcript to the script's dialogue lines, in order, so each
       speaking shot gets the time span of its own lines.
    3. Cuts the recording into one continuous run of windows, one per shot,
       with every cut placed at the quietest point of the pause between lines.
       Silent shots between two lines get the pause time, stretched or squeezed
       from their scripted `dur:` (warned when the recording's pause is far off).
       With --snap (default) a speaking shot's window is lengthened to the
       frame grid of the target it renders on (H3's 17k+5, LTX's 8k+1) when
       the pause has room, so nothing is padded.
    4. Rewrites the script: `audio: in-out` on every shot it placed, old `dur:`
       lines kept as comments. Points the series config at the recording
       (audio.mode = source_track). Both files are written the way the editor
       writes them (h3source): the old one is copied to <episode>/_history/
       first and the write is atomic, so the editor and the command line keep
       one history.

Because the windows are contiguous, the assembled picture lines up with the
recording end to end: h3assemble trims each clip to its window, and the
recording from the first window's start is the episode's dialogue track.

Recording tips
    Record lines in script order, dry (no music), with real pauses where the
    script has silent shots. Mixed-in music buries the voice for H3: run
    `python -m demucs --two-stems vocals file.wav` and use the vocals stem.

Needs
    ffmpeg on PATH, numpy, and one of:
        pip install faster-whisper        (recommended; uses GPU if it can, else CPU)
        pip install openai-whisper        (fallback)
    The Whisper model downloads once on first use (--model, default medium.en).

For the editor
    --progress prints one `##h3align {json}` line per stage (transcribe,
    match, write) and --json PATH writes the result (the windows it placed,
    the notes, the report and the files' new hashes) as JSON. Everything else
    is unchanged: h3track.py runs this script with both and turns the lines
    into `h3pipe.align` events (docs/API.md, "Phase 9c").
"""
from __future__ import annotations

import argparse
import difflib
import glob
import json
import math
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import h3build  # noqa: E402  (parse_script, parse_story)
import h3source as HS  # noqa: E402  (the editor's read/save: _history, atomic writes)

SR = 16000
LEAD = 0.25      # seconds of air kept before a line's first word
TAIL = 0.30      # and after its last word
MIN_SILENT = 1.0
SCALE_OK = (0.6, 1.8)   # silent-shot stretch the pause may impose without a warning


# ---------------------------------------------------------------------------
# inputs

def episode_files(ep: str) -> tuple[str, str]:
    series_cfg = os.path.join(ep, "series.json")
    if not os.path.isfile(series_cfg):
        sys.exit(f"  !! {series_cfg} not found")
    base = os.path.basename(os.path.normpath(ep))
    md = os.path.join(ep, f"{base}.md")
    if not os.path.isfile(md):
        cands = [p for p in glob.glob(os.path.join(ep, "*.md"))
                 if not os.path.basename(p).lower().startswith(("refs_todo", "readme",
                                                                "notes", "align_report"))]
        if len(cands) != 1:
            sys.exit(f"  !! can't tell which script to use in {ep}: "
                     f"{[os.path.basename(c) for c in cands]}")
        md = cands[0]
    return series_cfg, md


def shot_grids(series_cfg_path: str, script_text: str):
    """shot id -> the snap of the frame grid that shot renders on: its video
    target's template (targets.shot_targets: series.target, profiles and
    `target:` lines), H3's 17k+5 or LTX's 8k+1. A script the targets can't
    resolve falls back to the series target for every shot."""
    import targets as TG
    from h3core.series_config import (character_ids, load_series_config, series_info,
                                      subject_ids)
    cfg = load_series_config(series_cfg_path)
    default = TG.video_target(cfg)
    try:
        story = h3build.parse_story(script_text, subject_ids(cfg), character_ids(cfg),
                                    series_info(cfg))
        by_shot = TG.shot_targets(story, cfg)
    except Exception:
        by_shot = {}
    return lambda sid: TG.load_target(by_shot.get(sid, default.id), "video").template.snap


def load_pcm(path: str) -> "np.ndarray":
    import numpy as np
    if not shutil.which("ffmpeg"):
        sys.exit("  !! ffmpeg not found on PATH")
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", str(SR),
                        "-f", "f32le", "-"], capture_output=True)
    if r.returncode != 0:
        sys.exit(f"  !! could not decode {path}: {r.stderr.decode('utf-8', 'replace')[-300:]}")
    return np.frombuffer(r.stdout, dtype=np.float32)


def transcribe(path: str, model: str, device: str, language: str, prompt: str) -> list[dict]:
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        WhisperModel = None
    if WhisperModel is not None:
        tries = [("cuda", "float16"), ("cpu", "int8")] if device == "auto" else \
                [(device, "float16" if device == "cuda" else "int8")]
        last = None
        for dev, ct in tries:
            try:
                print(f"  transcribing with faster-whisper {model} on {dev} ...", flush=True)
                wm = WhisperModel(model, device=dev, compute_type=ct)
                segs, _ = wm.transcribe(path, language=language, word_timestamps=True,
                                        initial_prompt=prompt or None, vad_filter=False,
                                        condition_on_previous_text=False)
                words = []
                for seg in segs:
                    for w in seg.words or []:
                        words.append({"word": w.word.strip(), "start": round(w.start, 3),
                                      "end": round(w.end, 3)})
                return words
            except Exception as e:          # CUDA libs missing, OOM, ...
                last = e
                print(f"  .. {dev} failed: {str(e)[:160]}")
        sys.exit(f"  !! faster-whisper failed: {last}")
    try:
        import whisper
    except ImportError:
        sys.exit("  !! no Whisper installed. Run:  pip install faster-whisper\n"
                 "     (or pass --words with an existing word-timing JSON)")
    print(f"  transcribing with openai-whisper {model} ...", flush=True)
    m = whisper.load_model(model)
    res = m.transcribe(path, language=language, word_timestamps=True,
                       initial_prompt=prompt or None)
    return [{"word": w["word"].strip(), "start": round(w["start"], 3), "end": round(w["end"], 3)}
            for s in res["segments"] for w in s.get("words", [])]


def norm(tok: str) -> str:
    return re.sub(r"[^a-z0-9']", "", tok.lower().replace("’", "'")).strip("'")


def tokens(text: str) -> list[str]:
    return [t for t in (norm(x) for x in re.split(r"[\s\-–—]+", text)) if t]


# ---------------------------------------------------------------------------
# alignment

def align_lines(lines: list[dict], words: list[dict]) -> None:
    """Give each script line a start/end from the transcript, in order."""
    script_toks, owner = [], []
    for li, ln in enumerate(lines):
        for t in tokens(ln["text"]):
            script_toks.append(t)
            owner.append(li)
    trans_toks = [norm(w["word"]) for w in words]
    sm = difflib.SequenceMatcher(None, script_toks, trans_toks, autojunk=False)
    hit: dict[int, list[int]] = {}
    for a, b, size in sm.get_matching_blocks():
        for k in range(size):
            hit.setdefault(owner[a + k], []).append(b + k)
    for li, ln in enumerate(lines):
        n = len(tokens(ln["text"]))
        idx = sorted(hit.get(li, []))
        ln["coverage"] = len(idx) / n if n else 0.0
        if idx:
            ln["start"] = words[idx[0]]["start"]
            ln["end"] = words[idx[-1]]["end"]
            ln["heard"] = " ".join(words[i]["word"] for i in range(idx[0], idx[-1] + 1))
    # lines nobody could hear: place them between their neighbours
    for li, ln in enumerate(lines):
        if "start" in ln:
            continue
        prev = next((lines[j]["end"] for j in range(li - 1, -1, -1) if "end" in lines[j]), 0.0)
        nxt = next((lines[j]["start"] for j in range(li + 1, len(lines)) if "start" in lines[j]),
                   None)
        guess = max(0.4, len(tokens(ln["text"])) / 2.6)
        if nxt is None:
            nxt = prev + guess + 0.4
        ln["start"], ln["end"] = prev + 0.2, max(prev + 0.3, min(nxt - 0.2, prev + 0.2 + guess))
        ln["estimated"] = True


def quietest(pcm, a: float, b: float) -> float:
    """Time of lowest short-term energy in [a, b]."""
    import numpy as np
    if b - a < 0.06:
        return (a + b) / 2
    i0, i1 = int(a * SR), int(b * SR)
    seg = pcm[i0:i1]
    hop = int(0.01 * SR)
    if len(seg) < hop * 3:
        return (a + b) / 2
    n = len(seg) // hop
    rms = np.sqrt(np.mean(seg[:n * hop].reshape(n, hop) ** 2, axis=1) + 1e-12)
    k = max(1, 5)
    sm = np.convolve(rms, np.ones(k) / k, mode="same")
    # prefer the middle of a flat quiet stretch: small bias toward the centre
    centre = np.abs(np.arange(n) - n / 2) / max(n, 1)
    j = int(np.argmin(sm * (1 + 0.15 * centre)))
    return a + (j + 0.5) * hop / SR


# ---------------------------------------------------------------------------
# timeline

def build_windows(shots: list[dict], pcm, duration: float, fps: float, snap: bool,
                  notes: list[str]) -> None:
    speaking = [i for i, s in enumerate(shots) if s["lines"]]
    if not speaking:
        sys.exit("  !! the script has no dialogue to align")
    for i in speaking:
        s = shots[i]
        s["span"] = (min(l["start"] for l in s["lines"]), max(l["end"] for l in s["lines"]))
    for p, q in zip(speaking, speaking[1:]):
        if shots[q]["span"][0] < shots[p]["span"][1] - 0.05:
            notes.append(f"!! {shots[q]['id']} starts ({shots[q]['span'][0]:.2f}s) before "
                         f"{shots[p]['id']} ends ({shots[p]['span'][1]:.2f}s). The recording's "
                         f"line order differs from the script, or two shots share one breath.")

    def fill_silent(ids: list[int], a: float, b: float, where: str) -> None:
        """Lay silent shots back to back across [a, b]."""
        want = sum(shots[i]["dur"] or 3.04 for i in ids)
        have = b - a
        scale = have / want if want else 1
        if have < MIN_SILENT * len(ids):
            notes.append(f"!! {where}: the recording has {max(have, 0):.2f}s for "
                         f"{', '.join(shots[i]['id'] for i in ids)} (scripted {want:.2f}s). "
                         f"Those shots keep their `dur:` and the picture will run "
                         f"{want - max(have, 0):.2f}s longer than the recording here — "
                         f"add a pause of ~{want:.2f}s to the recording.")
            for i in ids:
                shots[i]["window"] = None
            return
        if not SCALE_OK[0] <= scale <= SCALE_OK[1]:
            notes.append(f"!  {where}: silent shots {', '.join(shots[i]['id'] for i in ids)} "
                         f"stretched x{scale:.2f} to fill a {have:.2f}s pause "
                         f"(scripted {want:.2f}s).")
        t = a
        for i in ids:
            d = (shots[i]["dur"] or 3.04) * scale
            shots[i]["window"] = [t, t + d]
            t += d

    # leading silent shots
    first = speaking[0]
    lead_ids = list(range(0, first))
    a0 = shots[first]["span"][0]
    if lead_ids:
        want = sum(shots[i]["dur"] or 3.04 for i in lead_ids)
        start = max(0.0, a0 - LEAD - want)
        fill_silent(lead_ids, start, a0 - LEAD, "opening")
        cut = a0 - LEAD
    else:
        cut = max(0.0, a0 - LEAD)
    prev_cut = cut

    for n, i in enumerate(speaking):
        s = shots[i]
        a, b = s["span"]
        if n + 1 < len(speaking):
            j = speaking[n + 1]
            na = shots[j]["span"][0]
            between = list(range(i + 1, j))
            lo = min(b + TAIL, (b + na) / 2)
            hi = max(na - LEAD, (b + na) / 2)
            if between:
                end = lo
                if snap:
                    want = s["snap"](max(1, round((end - prev_cut) * fps))) / fps
                    if prev_cut + want <= hi - MIN_SILENT * len(between):
                        end = prev_cut + want
                s["window"] = [prev_cut, end]
                fill_silent(between, end, hi, f"between {s['id']} and {shots[j]['id']}")
                prev_cut = hi
            else:
                end = quietest(pcm, lo, hi) if hi > lo else (b + na) / 2
                if snap:
                    want = s["snap"](max(1, round((end - prev_cut) * fps))) / fps
                    if lo <= prev_cut + want <= hi:
                        end = prev_cut + want
                s["window"] = [prev_cut, end]
                prev_cut = end
        else:
            tail_ids = list(range(i + 1, len(shots)))
            end = min(duration, b + TAIL)
            if snap and not tail_ids:
                want = s["snap"](max(1, round((end - prev_cut) * fps))) / fps
                if prev_cut + want <= duration:
                    end = prev_cut + want
            s["window"] = [prev_cut, end]
            if tail_ids:
                want = sum(shots[k]["dur"] or 3.04 for k in tail_ids)
                room = duration - end
                if room >= MIN_SILENT * len(tail_ids):
                    fill_silent(tail_ids, end, min(duration, end + want), "ending")
                else:
                    notes.append(f"!  ending: {', '.join(shots[k]['id'] for k in tail_ids)} run "
                                 f"past the end of the recording; they keep their `dur:`.")
                    for k in tail_ids:
                        shots[k]["window"] = None

    for s in shots:
        w = s.get("window")
        if w and w[1] - w[0] > 10.5 and s["lines"]:
            notes.append(f"!  {s['id']}: {w[1] - w[0]:.2f}s window — long for one H3 render. "
                         f"Consider splitting the shot.")


# ---------------------------------------------------------------------------
# writing

def rewrite_script(text: str, windows: dict[str, list[float] | None]) -> str:
    out, cur = [], None
    for line in text.splitlines():
        m = re.match(r"^##\s+(\S+)", line)
        if m:
            cur = m.group(1)
            out.append(line)
            w = windows.get(cur)
            if w:
                out.append(f"audio: {w[0]:.3f}-{w[1]:.3f}")
            continue
        if cur and windows.get(cur) and re.match(r"^\s*audio\s*:", line):
            continue                              # replaced above
        if cur and windows.get(cur) and re.match(r"^\s*(dur|duration)\s*:", line):
            out.append(f"// {line.strip()}   (h3align)")
            continue
        if re.match(r"^#\s", line):
            cur = None
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


PROGRESS = "##h3align "     # the prefix of a --progress line (h3track reads them)


def progress(on: bool, stage: str, pct: int, text: str) -> None:
    """One machine-readable progress line for the editor (--progress)."""
    if on:
        print(PROGRESS + json.dumps({"stage": stage, "pct": pct, "text": text}), flush=True)


def report_md(md_p: str, rec_rel: str, duration: float, start: float | None,
              rows: list, notes: list[str], lines: list[dict]) -> str:
    """align_report.md's text (written unless --dry-run; also in --json)."""
    out = [f"# Alignment — {os.path.basename(md_p)} against {rec_rel}\n\n"
           f"Recording {duration:.2f}s. Picture starts at "
           f"{'-' if start is None else f'{start:.3f}'}s into the recording.\n\n"
           f"| Shot | Window (s) | Length | Frames req/render | Speech |\n|---|---|---|---|---|\n"]
    for r in rows:
        out.append(f"| {r[0]} | {r[1].strip()} | {r[2]:.2f} | {r[3]} | {r[4]} |\n")
    out.append("\n" + ("\n".join(f"- {n}" for n in notes) or "- no problems found") + "\n")
    out.append("\n## Lines\n\n| Shot | Who | Start | End | Match | Line |\n"
               "|---|---|---|---|---|---|\n")
    for ln in lines:
        match = "guess" if ln.get("estimated") else f"{ln['coverage']:.0%}"
        out.append(f"| {ln['shot']} | {ln['who']} | {ln['start']:.2f} | {ln['end']:.2f} | "
                   f"{match} | {ln['text']} |\n")
    return "".join(out)


def changes_json(shots: list[dict], notes: list[str]) -> list[dict]:
    """One entry per shot: its window (seconds on the recording, null when it
    keeps its `dur:`) and the notes that name it."""
    out = []
    for s in shots:
        w = s.get("window")
        note = " ".join(n.strip() for n in notes if s["id"] in n)
        out.append({"shot": s["id"],
                    "audio_in": round(w[0], 3) if w else None,
                    "audio_out": round(w[1], 3) if w else None,
                    "note": note or ("keeps its `dur:`" if not w else "")})
    return out


def write_series_track(series_cfg_p: str, ep: str, raw: dict, rec_rel: str,
                       policy: str | None, retention: str | None) -> str:
    """Point the series config at the recording (audio.mode source_track),
    through the editor's save path: a copy in <ep>/_history/ first, then an
    atomic write that keeps the file's line endings and BOM. The new hash."""
    audio = dict(raw.get("audio") or {})
    audio["mode"] = "source_track"
    audio["track"] = rec_rel
    if policy:
        audio["default_policy"] = policy
    if retention:
        audio["retention"] = retention
    raw["audio"] = audio
    src = HS.read_file(ep, "series", series_cfg_p)
    return HS.write_source(src, HS.dump_series(raw, src.text))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("episode", help="episode folder (holds series.json and the script)")
    ap.add_argument("recording", nargs="?",
                    help="dialogue recording (default: audio.track in series.json)")
    ap.add_argument("--script", help="script .md, if the folder holds more than one")
    ap.add_argument("--words", help="word-timing JSON to use instead of transcribing")
    ap.add_argument("--retranscribe", action="store_true", help="ignore the cached transcript")
    ap.add_argument("--model", default="medium.en",
                    help="Whisper model (tiny.en, base.en, small.en, medium.en, large-v3)")
    ap.add_argument("--device", default="auto", choices=["auto", "cuda", "cpu"],
                    help="where Whisper runs (default %(default)s)")
    ap.add_argument("--language", default="en",
                    help="spoken language of the recording (default %(default)s)")
    ap.add_argument("--no-snap", dest="snap", action="store_false",
                    help="don't lengthen windows to H3's frame grid")
    ap.add_argument("--policy", choices=["dub", "dub_keep_foley"],
                    help="set the default policy in series.json for speaking shots")
    ap.add_argument("--retention", choices=["fully_copy", "partially_copy", "reference"],
                    help="set audio.retention in series.json (default depends on policy)")
    ap.add_argument("--dry-run", action="store_true", help="report only; change no files")
    ap.add_argument("--progress", action="store_true",
                    help="print a `##h3align {json}` line per stage (for the editor)")
    ap.add_argument("--json", dest="json_out", metavar="PATH",
                    help="write the result (windows, notes, report, hashes) as JSON")
    args = ap.parse_args()
    prog = args.progress

    ep = args.episode
    series_cfg_p, md_p = episode_files(ep)
    if args.script:
        md_p = args.script
    series_cfg = json.load(open(series_cfg_p, encoding="utf-8"))
    fps = float(series_cfg.get("series", {}).get("fps", 24))

    rec = args.recording or series_cfg.get("audio", {}).get("track")
    if not rec:
        sys.exit("  !! give the recording path (none set in series.json)")
    rec_abs = rec if os.path.isabs(rec) else (
        rec if os.path.isfile(rec) else os.path.join(ep, rec))
    if not os.path.isfile(rec_abs):
        sys.exit(f"  !! recording not found: {rec_abs}")
    try:
        rec_rel = os.path.relpath(os.path.abspath(rec_abs), os.path.abspath(ep))
    except ValueError:                      # different drive on Windows
        rec_rel = os.path.abspath(rec_abs)
    if rec_rel.startswith(".."):
        rec_rel = os.path.abspath(rec_abs)
    rec_rel = rec_rel.replace("\\", "/")

    # script
    subjects = {k for k in series_cfg.get("subjects", {}) if not k.startswith("_")}
    chars = {k for k, v in series_cfg["subjects"].items()
             if not k.startswith("_") and isinstance(v, dict)
             and v.get("kind", "character") == "character"}
    script_src = HS.read_file(ep, "script", md_p)
    text = script_src.text
    epi = h3build.parse_script(text, subjects, chars)
    snap_of = shot_grids(series_cfg_p, text)
    shots, lines = [], []
    for seq in epi["sequences"]:
        for sh in seq["shots"]:
            entry = {"id": sh["id"], "lines": [], "dur": sh.get("duration"),
                     "snap": snap_of(sh["id"])}
            for d in sh["dialogue"]:
                ln = {"shot": sh["id"], "who": d["who"], "text": d["line"]}
                entry["lines"].append(ln)
                lines.append(ln)
            shots.append(entry)

    # transcript
    cache = rec_abs + ".words.json"
    if args.words:
        progress(prog, "transcribe", 10, f"reading {os.path.basename(args.words)}")
        words = json.load(open(args.words, encoding="utf-8"))
    elif os.path.isfile(cache) and not args.retranscribe \
            and os.path.getmtime(cache) >= os.path.getmtime(rec_abs):
        progress(prog, "transcribe", 10, f"using the cached transcript "
                                         f"{os.path.basename(cache)}")
        words = json.load(open(cache, encoding="utf-8"))
        print(f"  using cached transcript {cache}")
    else:
        progress(prog, "transcribe", 10, f"transcribing {os.path.basename(rec_abs)} with "
                                         f"Whisper {args.model} (this takes a while)")
        prompt = " ".join(l["text"] for l in lines)[:600]
        words = transcribe(rec_abs, args.model, args.device, args.language, prompt)
        json.dump(words, open(cache, "w", encoding="utf-8"), indent=1)
        print(f"  cached transcript -> {cache}")
    if not words:
        sys.exit("  !! the transcript is empty")
    progress(prog, "match", 65, f"matching {len(words)} words to {len(lines)} lines")

    pcm = load_pcm(rec_abs)
    duration = len(pcm) / SR
    align_lines(lines, words)
    notes: list[str] = []
    for ln in lines:
        if ln.get("estimated"):
            notes.append(f"!! {ln['shot']}: couldn't hear \"{ln['text'][:50]}\" — placed by "
                         f"guess at {ln['start']:.2f}s. Check the recording or the script.")
        elif ln["coverage"] < 0.6:
            notes.append(f"!  {ln['shot']}: only {ln['coverage']:.0%} of \"{ln['text'][:50]}\" "
                         f"matched (heard: \"{ln.get('heard', '')[:60]}\").")
    build_windows(shots, pcm, duration, fps, args.snap, notes)

    # report
    print(f"\n  {os.path.basename(md_p)}  ·  {os.path.basename(rec_abs)} "
          f"({duration:.2f}s, {len(words)} words)\n")
    print(f"  {'shot':8} {'window':>17} {'len':>6} {'frames':>7}  speech")
    rows = []
    for s in shots:
        w = s.get("window")
        if w:
            ln_s = w[1] - w[0]
            req = max(1, round(ln_s * fps))
            fr = f"{req}/{s['snap'](req)}"
            win = f"{w[0]:7.3f}-{w[1]:7.3f}"
        else:
            ln_s, fr, win = 0.0, "-", "  (keeps dur:)"
        sp = (f"{s['span'][0]:.2f}-{s['span'][1]:.2f}" if s.get("span") else "silent")
        cov = (f"  {min(l['coverage'] for l in s['lines']):.0%} matched" if s["lines"] else "")
        rows.append((s["id"], win, ln_s, fr, sp + cov))
        print(f"  {s['id']:8} {win:>17} {ln_s:6.2f} {fr:>7}  {sp}{cov}")
    print()
    for n in notes:
        print(f"  {n}")
    if not notes:
        print("  no problems found")

    windows = {s["id"]: s.get("window") for s in shots}
    starts = [w[0] for w in windows.values() if w]
    report = report_md(md_p, rec_rel, duration, min(starts) if starts else None,
                       rows, notes, lines)
    result = {"ok": True, "episode": os.path.abspath(ep), "script": md_p,
              "series": series_cfg_p, "recording": rec_rel, "duration": round(duration, 3),
              "words": len(words), "dry_run": bool(args.dry_run),
              "changes": changes_json(shots, notes), "notes": notes, "report": report,
              "report_path": None, "script_hash": None, "series_hash": None}

    if args.dry_run:
        print("\n  dry run — no files changed\n")
        progress(prog, "write", 100, "dry run — nothing written")
        write_result(args.json_out, result)
        return 0

    # write
    progress(prog, "write", 90, "writing the script and the series config")
    result["script_hash"] = HS.write_source(script_src, rewrite_script(text, windows))
    result["series_hash"] = write_series_track(series_cfg_p, ep, series_cfg, rec_rel,
                                               args.policy, args.retention)
    rep = os.path.join(ep, "align_report.md")
    with open(rep, "w", encoding="utf-8") as fh:
        fh.write(report)
    result["report_path"] = os.path.basename(rep)
    hist = os.path.join(ep, HS.HISTORY)
    print(f"\n  -> {md_p}  (old copy in {hist})\n  -> {series_cfg_p}  "
          f"(audio.mode source_track, track {rec_rel}; old copy in {hist})\n  -> {rep}\n"
          f"  next: python h3.py build {ep}\n")
    progress(prog, "write", 100, "written")
    write_result(args.json_out, result)
    return 0


def write_result(path: str | None, result: dict) -> None:
    """--json: the run as the editor reads it (h3track.align)."""
    if path:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(result, fh, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    raise SystemExit(main())
