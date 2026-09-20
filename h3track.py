#!/usr/bin/env python3
"""
h3track.py — the episode's dialogue recording: attaching one, and timing the
script against it (docs/API.md, "Phase 9c A"). The work behind
`GET /h3pipe/align/ready`, `POST /h3pipe/track` and `POST /h3pipe/align`.

    align_ready()                      -> what h3align needs, and what is missing
    attach_track(ep, source)           -> copy a recording in, point the series config at it
    clear_track(ep)                    -> forget it (the file stays)
    align(ep, ..., progress=...)       -> run h3align in a subprocess, stage by stage

The series config is written through the Phase 9a save path (h3source: a copy
in <ep>/_history/, an atomic write that keeps the file's line endings and BOM),
which is also how h3align itself writes now, so the editor and the command line
keep one history.

Nothing here transcribes: h3align does, in the Python that runs the pipeline
(the readiness check reports that interpreter and the pip line for whatever it
is missing).

Stdlib only.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import h3edit as E  # noqa: E402
import h3source as H  # noqa: E402
import h3takes as T  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ALIGN_SCRIPT = os.path.join(HERE, "h3align.py")
AUDIO_DIR = "audio"
AUDIO_EXT = (".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac", ".opus")
DEFAULT_MODEL = "medium.en"
MODELS = ("tiny.en", "base.en", "small.en", "medium.en", "large-v3")
SOURCE_TRACK = "source_track"
NO_TRACK_MODE = "generate"          # the mode a cleared track falls back to
ALIGN_TIMEOUT = 3600
PROBE_TTL = 30.0                    # seconds a readiness probe is reused


class TrackError(H.SourceError):
    """A request that can't be done; `status` is the answer's HTTP status
    (h3source.SourceError, so the API's handler already knows it)."""


# ---------------------------------------------------------------------------
# readiness: what h3align needs, in the Python that would run it
# ---------------------------------------------------------------------------

# (pip name, import name) — one of the two whispers is enough
PACKAGES = (("numpy", "numpy"), ("faster-whisper", "faster_whisper"),
            ("openai-whisper", "whisper"))
WHISPER_NAMES = ("faster-whisper", "openai-whisper")     # what `missing` may call them

PROBE = r"""
import json
import importlib.util as u
try:
    from importlib.metadata import version
except Exception:
    version = None
out = {}
for dist, mod in (("numpy", "numpy"), ("faster-whisper", "faster_whisper"),
                  ("openai-whisper", "whisper")):
    try:
        found = u.find_spec(mod) is not None
    except Exception:
        found = False
    v = ""
    if found and version is not None:
        try:
            v = version(dist) or ""
        except Exception:
            v = ""
    out[dist] = v if found else None
print(json.dumps(out))
"""

_probe_cache: dict[str, tuple[float, dict]] = {}


def probe(python: str) -> dict:
    """{pip name: version string (maybe "") or None} in `python`, asked in a
    subprocess so it is what h3align would really see. Falls back to this
    interpreter's own view if the probe can't run."""
    hit = _probe_cache.get(python)
    if hit and time.monotonic() - hit[0] < PROBE_TTL:
        return dict(hit[1])
    found = None
    try:
        r = subprocess.run([python, "-c", PROBE], capture_output=True, timeout=120,
                           env=dict(os.environ, **E.TOOL_ENV))
        out = r.stdout.decode("utf-8", "replace").strip().splitlines()
        if out:
            got = json.loads(out[-1])
            if isinstance(got, dict):
                found = {k: got.get(k) for k, _ in PACKAGES}
    except (OSError, ValueError, subprocess.SubprocessError):
        found = None
    if found is None:                                     # this interpreter, then
        import importlib.util
        found = {}
        for dist, mod in PACKAGES:
            try:
                found[dist] = "" if importlib.util.find_spec(mod) is not None else None
            except (ImportError, ValueError):
                found[dist] = None
    _probe_cache[python] = (time.monotonic(), dict(found))
    return found


def has_deps(pkgs: dict) -> bool:
    """numpy and one of the whispers, from a `probe` result."""
    return pkgs.get("numpy") is not None and any(
        pkgs.get(n) is not None for n in WHISPER_NAMES)


def candidates() -> list[str]:
    """Interpreters that could run h3align, best first: H3PIPE_ALIGN_PYTHON,
    the one running this (ComfyUI's embedded Python in the editor), then a
    `python` on PATH. The editor's Python often has no Whisper while the
    system one does, and h3align is a subprocess either way."""
    out = [os.environ.get("H3PIPE_ALIGN_PYTHON") or "", sys.executable]
    out += [shutil.which(n) or "" for n in ("python", "python3")]
    seen, keep = set(), []
    for p in out:
        real = os.path.normcase(os.path.abspath(p)) if p else ""
        if p and real not in seen and os.path.isfile(p):
            seen.add(real)
            keep.append(p)
    return keep or [sys.executable]


def align_python() -> str:
    """The interpreter h3align runs in: the first candidate that has numpy and
    a Whisper, else the first (so readiness reports against it)."""
    cands = candidates()
    for py in cands:
        if has_deps(probe(py)):
            return py
    return cands[0]


def align_ready(python: str | None = None) -> dict:
    """GET /h3pipe/align/ready: ffmpeg, numpy and a Whisper, checked against
    the Python that would run h3align (`align_python`), with the pip line for
    what is missing."""
    py = python or align_python()
    pkgs = probe(py)
    ffmpeg = shutil.which("ffmpeg")
    missing = []
    if not ffmpeg:
        missing.append("ffmpeg")
    if pkgs.get("numpy") is None:
        missing.append("numpy")
    if pkgs.get("faster-whisper") is None and pkgs.get("openai-whisper") is None:
        missing.append("faster-whisper")
    pip = [m for m in missing if m != "ffmpeg"]
    return {
        "ready": not missing,
        "ffmpeg": ffmpeg,
        "missing": missing,
        "python": py,
        "install": (f'"{py}" -m pip install ' + " ".join(pip)) if pip else "",
        "ffmpeg_hint": ("install ffmpeg and put it on PATH (winget install "
                        "Gyan.FFmpeg)" if not ffmpeg else ""),
        "packages": pkgs,
        "models": {"default": DEFAULT_MODEL, "choices": list(MODELS)},
    }


# ---------------------------------------------------------------------------
# the recording file
# ---------------------------------------------------------------------------

def audio_name(name: str) -> str:
    """A file name safe to write inside <ep>/audio/, or TrackError 400 for
    something that isn't an audio file."""
    base = os.path.basename((name or "").replace("\\", "/").rstrip("/")).strip()
    base = re.sub(r"[^A-Za-z0-9 ._()\-]+", "_", base).lstrip(".")
    stem, ext = os.path.splitext(base)
    if not stem:
        raise TrackError(400, f"{name!r} has no file name")
    if ext.lower() not in AUDIO_EXT:
        raise TrackError(400, f"{base} is not a recording: the track must be one of "
                              f"{', '.join(AUDIO_EXT)}")
    return stem[:120] + ext.lower()


def _sha1(path: str) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _same_file(a: str, b: str) -> bool:
    try:
        if os.path.samefile(a, b):
            return True
    except OSError:
        pass
    try:
        return os.path.getsize(a) == os.path.getsize(b) and _sha1(a) == _sha1(b)
    except OSError:
        return False


def inside_episode(ep: str, path: str) -> str | None:
    """`path` relative to the episode with forward slashes, or None when it
    is outside it (or on another drive)."""
    try:
        r = os.path.relpath(os.path.abspath(path), os.path.abspath(ep))
    except ValueError:                                    # another drive on Windows
        return None
    r = r.replace(os.sep, "/")
    return None if r == ".." or r.startswith("../") else r


def place_recording(ep: str, source: str, name: str | None = None) -> tuple[str, bool]:
    """Put `source` at <ep>/audio/<name>: (its path relative to the episode,
    whether it was copied). A file already inside the episode is used where it
    is; a name already taken by another recording gets -2, -3..."""
    if not os.path.isfile(source):
        raise TrackError(404, f"no file at {source}")
    here = inside_episode(ep, source)
    if here is not None:
        audio_name(here)                                  # the type check, on the real name
        return here, False
    dest_dir = os.path.join(ep, AUDIO_DIR)
    base = audio_name(name or source)
    stem, ext = os.path.splitext(base)
    dest, k = os.path.join(dest_dir, base), 1
    while os.path.exists(dest):
        if _same_file(source, dest):
            return inside_episode(ep, dest), False
        k += 1
        dest = os.path.join(dest_dir, f"{stem}-{k}{ext}")
    os.makedirs(dest_dir, exist_ok=True)
    shutil.copy2(source, dest)
    return inside_episode(ep, dest), True


def import_audio(ep: str, source: str, name: str | None = None) -> dict:
    """Put a media file in <ep>/audio/ for a cut entry's audio source (Phase
    9d) and say where it landed: {"path" (relative to the episode, ready for
    `audio.path`), "copied"}. The naming is place_recording's — sanitised,
    `-2` on a clash, identical bytes reused, a file already inside the episode
    left where it is.

    This is only a file in the episode: it does not become the episode's
    dialogue recording (the series config isn't touched and nothing is
    rebuilt) and it is not a ref candidate.
    """
    rel, copied = place_recording(ep, source, name)
    return {"path": rel, "copied": copied}


# ---------------------------------------------------------------------------
# the series config's audio block
# ---------------------------------------------------------------------------

def _remembered_mode(ep: str) -> str | None:
    """The `audio.mode` the series config had before the editor attached a
    track (overrides.json's `audio.mode_before_track`: "" when it had none)."""
    audio = T.load_overrides(ep).get("audio")
    mode = audio.get("mode_before_track") if isinstance(audio, dict) else None
    return mode if isinstance(mode, str) else None


def _remember_mode(ep: str, mode: str | None) -> None:
    data = T.load_overrides(ep)
    audio = dict(data.get("audio") or {}) if isinstance(data.get("audio"), dict) else {}
    if mode is None:
        audio.pop("mode_before_track", None)
    else:
        audio["mode_before_track"] = mode
    if audio:
        data["audio"] = audio
    else:
        data.pop("audio", None)
    T.save_overrides(ep, data)


def set_track(ep: str, rel: str | None) -> dict:
    """Point the series config's `audio` block at `rel` (mode source_track),
    or, with None, forget the track and put the mode back to what it was
    before (else `generate`). Written through the Phase 9a save path.
    Returns {"hash", "changed", "reformatted", "mode"}."""
    src = H.read_source(ep, "series")
    try:
        raw = json.loads(src.text)
    except ValueError as e:
        raise TrackError(400, f"series.json is not valid JSON ({e}): fix it first") from None
    if not isinstance(raw, dict):
        raise TrackError(400, "series.json must be a JSON object")
    block = raw.get("audio")
    audio = dict(block) if isinstance(block, dict) else {}
    if rel is None:
        audio.pop("track", None)
        back = _remembered_mode(ep)
        if back is None:
            audio["mode"] = NO_TRACK_MODE
        elif back == "":
            audio.pop("mode", None)
        else:
            audio["mode"] = back
        _remember_mode(ep, None)
    else:
        was = audio.get("mode")
        if was != SOURCE_TRACK:
            _remember_mode(ep, was if isinstance(was, str) else "")
        audio["track"] = rel
        audio["mode"] = SOURCE_TRACK
    if audio:
        raw["audio"] = audio
    else:
        raw.pop("audio", None)
    text = H.dump_series(raw, src.text)
    was_formatted = H.formatted(src.text)
    new_hash = H.write_source(src, text)
    return {"hash": new_hash, "changed": new_hash != src.hash,
            "reformatted": bool(not was_formatted and new_hash != src.hash),
            "mode": audio.get("mode")}


def recording_path(ep: str, track: str | None = None) -> str | None:
    """The recording h3align would use: `track` (absolute, or relative to the
    episode), else the series config's `audio.track`. None when there is
    none, or the file isn't there."""
    if not track:
        cfg = E.episode_series_config(ep)
        try:
            raw = (T.read_json(cfg) or {}) if cfg else {}
        except ValueError:
            raw = {}
        audio = raw.get("audio") if isinstance(raw, dict) else None
        track = audio.get("track") if isinstance(audio, dict) else None
        if not isinstance(track, str) or not track.strip():
            return None
    full = track if os.path.isabs(track) else os.path.join(ep, track)
    return os.path.normpath(full) if os.path.isfile(full) else None


def attach_track(ep: str, source: str, name: str | None = None) -> dict:
    """POST /h3pipe/track with a file: put it in <ep>/audio/, point the series
    config at it. Returns set_track's answer plus {"path", "copied"}."""
    rel, copied = place_recording(ep, source, name)
    out = set_track(ep, rel)
    out.update({"path": rel, "copied": copied})
    return out


def clear_track(ep: str) -> dict:
    """POST /h3pipe/track with `track: null`: the series config forgets the
    recording (the file stays where it is)."""
    out = set_track(ep, None)
    out.update({"path": None, "copied": False})
    return out


# ---------------------------------------------------------------------------
# running h3align
# ---------------------------------------------------------------------------

PROGRESS_PREFIX = "##h3align "       # h3align --progress; h3align.PROGRESS writes them


def _stream(cmd: list[str], cwd: str, timeout: float, on_progress) -> tuple[int, str]:
    """Run `cmd`, calling on_progress({"stage", "pct", "text"}) for every
    h3align progress line; (exit code, everything else it printed)."""
    log: list[str] = []
    stopped: list[bool] = []
    p = subprocess.Popen(cmd, cwd=cwd, env=dict(os.environ, **E.TOOL_ENV),
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    def kill():
        stopped.append(True)
        p.kill()

    killer = threading.Timer(timeout, kill)
    killer.start()
    try:
        for raw in p.stdout:
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if line.startswith(PROGRESS_PREFIX):
                try:
                    ev = json.loads(line[len(PROGRESS_PREFIX):])
                except ValueError:
                    ev = None
                if isinstance(ev, dict) and on_progress is not None:
                    on_progress({"stage": str(ev.get("stage") or ""),
                                 "pct": ev.get("pct"), "text": str(ev.get("text") or "")})
                continue
            log.append(line)
    finally:
        p.stdout.close()
        rc = p.wait()
        killer.cancel()
    if stopped:
        log.append(f"  !! h3align took longer than {int(timeout)}s and was stopped")
    return rc, "\n".join(log).strip()


def why(log: str, rc: int) -> str:
    """The line worth showing when h3align fails."""
    lines = [ln.strip() for ln in log.splitlines() if ln.strip()]
    for ln in reversed(lines):
        if ln.startswith("!!") or ln.startswith("  !!") or "Error" in ln:
            return ln.lstrip("! ").strip() or ln
    return lines[-1] if lines else f"h3align exited {rc}"


def align(ep: str, track: str | None = None, model: str | None = None, snap: bool = True,
          dry_run: bool = False, words: str | None = None, progress=None,
          timeout: float | None = None, python: str | None = None,
          check_deps: bool = True) -> dict:
    """Run h3align on `ep` in a subprocess and return what it wrote:
    {"ok", "report", "changes", "notes", "script_hash", "series_hash", ...}
    (h3align --json). `progress(event)` is called per stage. TrackError 409
    with `missing` when a dependency isn't installed (a Whisper isn't one
    when the recording already has a transcript), 500 when h3align fails."""
    py = python or align_python()
    timeout = ALIGN_TIMEOUT if timeout is None else timeout
    if check_deps:
        ready = align_ready(py)
        # a transcript already beside the recording (or handed over) means no
        # Whisper is needed: h3align reads the cache
        rec = recording_path(ep, track)
        cached = bool(words) or bool(rec and E.has_words(rec))
        need = [m for m in ready["missing"] if not (cached and m in WHISPER_NAMES)]
        if need:
            raise TrackError(409, "h3align needs " + ", ".join(need)
                             + " (install them for the Python that runs the pipeline)",
                             missing=need, install=ready["install"],
                             python=ready["python"], ffmpeg=ready["ffmpeg"],
                             words=cached)
    with tempfile.TemporaryDirectory(prefix="h3align_") as tmp:
        out = os.path.join(tmp, "align.json")
        args = [ep]
        if track:
            args.append(track)
        args += ["--json", out, "--progress"]
        if model:
            args += ["--model", model]
        if not snap:
            args.append("--no-snap")
        if dry_run:
            args.append("--dry-run")
        if words:
            args += ["--words", words]
        cmd = [py, "-c", E.RUN_SCRIPT, ALIGN_SCRIPT, *args]
        rc, log = _stream(cmd, ep, timeout, progress)
        if rc != 0 or not os.path.isfile(out):
            raise TrackError(500, why(log, rc), log=log)
        with open(out, encoding="utf-8") as fh:
            result = json.load(fh)
    result["log"] = log
    result.setdefault("ok", True)
    return result
