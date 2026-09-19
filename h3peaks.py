#!/usr/bin/env python3
"""
h3peaks.py — what the editor needs to know about a media file's sound: does it
have any, how long is it, and its waveform peaks.

- `has_audio(path)`: an mp4/mov/m4a is read by its boxes (moov > trak > mdia >
  hdlr of type `soun`), a wav by its header; anything else asks ffprobe. The
  answer is cached per (path, size, mtime) for this process.
- `clip_audio(mp4, wav)`: the file whose sound a take plays in the cut: the mp4
  if it has an audio stream, else its `_h3.wav`, else None (silence). This is
  h3assemble's `--audio auto` rule; assemble calls it too.
- `media_info(path)`: {"duration", "rate"} (seconds, sample rate), from the wav
  header or the mp4's boxes, else ffprobe; None values when it can't tell.
- `peaks(ep, path, rel, bins, start, end)`: the max absolute amplitude per bin
  (0..255, mono), for GET /h3pipe/peaks. Computed once per file at RATE bins a
  second (stdlib `wave` for PCM wav, else `ffmpeg -ac 1 -ar 8000 -f s16le`),
  cached in <ep>/_cache/peaks/<sha1 of path|size|mtime>.json, then resampled.

Stdlib only (ffmpeg/ffprobe through subprocess, when a file needs them).
"""
from __future__ import annotations

import array
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import wave

RATE = 200                       # cached peaks per second
FF_RATE = 8000                   # ffmpeg decodes to mono s16le at this rate
CACHE_DIR = os.path.join("_cache", "peaks")
CACHE_VERSION = 1
MP4_EXTS = (".mp4", ".mov", ".m4a", ".m4v", ".3gp")
MAX_BINS = 100_000

_audio_cache: dict = {}
_info_cache: dict = {}


class PeaksError(Exception):
    """A file whose sound can't be read (ffmpeg missing, or it failed)."""


def _key(path: str):
    st = os.stat(path)
    return (os.path.normcase(os.path.abspath(path)), st.st_size, st.st_mtime_ns)


# ---------------------------------------------------------------------------
# mp4 boxes
# ---------------------------------------------------------------------------

def _boxes(fh, start: int, end: int):
    """(type, payload start, payload end) of each box between start and end."""
    pos = start
    while pos + 8 <= end:
        fh.seek(pos)
        head = fh.read(8)
        if len(head) < 8:
            return
        size, kind = struct.unpack(">I4s", head)
        hdr = 8
        if size == 1:
            big = fh.read(8)
            if len(big) < 8:
                return
            size = struct.unpack(">Q", big)[0]
            hdr = 16
        elif size == 0:
            size = end - pos
        if size < hdr:
            return
        yield kind.decode("latin-1"), pos + hdr, min(pos + size, end)
        pos += size


def mp4_info(path: str) -> dict | None:
    """{"audio": bool, "duration": seconds | None, "rate": the first sound
    track's sample rate | None} from an ISO media file's boxes, or None when
    it isn't one (or has no moov)."""
    try:
        with open(path, "rb") as fh:
            end = fh.seek(0, os.SEEK_END)
            first = next(_boxes(fh, 0, end), None)
            if first is None or first[0] not in ("ftyp", "moov", "mdat", "free", "skip",
                                                 "wide", "pdin", "styp"):
                return None
            moov = next(((s, e) for k, s, e in _boxes(fh, 0, end) if k == "moov"), None)
            if moov is None:
                return None
            out = {"audio": False, "duration": None, "rate": None}
            for kind, s, e in _boxes(fh, *moov):
                if kind == "mvhd":
                    fh.seek(s)
                    v = fh.read(1)
                    if v == b"\x01":
                        fh.seek(s + 20)
                        scale, dur = struct.unpack(">IQ", fh.read(12))
                    else:
                        fh.seek(s + 12)
                        scale, dur = struct.unpack(">II", fh.read(8))
                    if scale:
                        out["duration"] = dur / scale
                elif kind == "trak":
                    for k2, s2, e2 in _boxes(fh, s, e):
                        if k2 != "mdia":
                            continue
                        handler, scale = None, None
                        for k3, s3, e3 in _boxes(fh, s2, e2):
                            if k3 == "hdlr":
                                fh.seek(s3 + 8)
                                handler = fh.read(4)
                            elif k3 == "mdhd":
                                fh.seek(s3)
                                v = fh.read(1)
                                fh.seek(s3 + (20 if v == b"\x01" else 12))
                                scale = struct.unpack(">I", fh.read(4))[0]
                        if handler == b"soun":
                            out["audio"] = True
                            if out["rate"] is None and scale:
                                out["rate"] = scale
            return out
    except (OSError, struct.error):
        return None


# ---------------------------------------------------------------------------
# does it have sound, how long is it
# ---------------------------------------------------------------------------

def _ffprobe(path: str) -> dict | None:
    if not shutil.which("ffprobe"):
        return None
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                            "format=duration:stream=codec_type,sample_rate",
                            "-of", "json", path], capture_output=True, timeout=60)
        data = json.loads(r.stdout.decode("utf-8", "replace") or "{}")
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    audio = [s for s in data.get("streams", []) if s.get("codec_type") == "audio"]
    try:
        dur = float((data.get("format") or {}).get("duration"))
    except (TypeError, ValueError):
        dur = None
    try:
        rate = int(audio[0].get("sample_rate")) if audio else None
    except (TypeError, ValueError):
        rate = None
    return {"audio": bool(audio), "duration": dur, "rate": rate}


def _wav_info(path: str) -> dict | None:
    try:
        with wave.open(path, "rb") as w:
            n, rate = w.getnframes(), w.getframerate()
            return {"audio": True, "duration": n / rate if rate else None, "rate": rate}
    except (wave.Error, EOFError, OSError):
        return None


def media_info(path: str) -> dict:
    """{"audio": bool, "duration": float | None, "rate": int | None}; cached
    per (path, size, mtime). A file nothing can read has no audio."""
    try:
        key = _key(path)
    except OSError:
        return {"audio": False, "duration": None, "rate": None}
    if key in _info_cache:
        return _info_cache[key]
    info = None
    ext = os.path.splitext(path)[1].lower()
    if key[1] == 0:
        info = {"audio": False, "duration": None, "rate": None}
    elif ext == ".wav":
        info = _wav_info(path)
    elif ext in MP4_EXTS:
        info = mp4_info(path)
    if info is None:
        info = _ffprobe(path) or {"audio": False, "duration": None, "rate": None}
    _info_cache[key] = info
    return info


def has_audio(path: str) -> bool:
    """True when the file has an audio stream (see the module docstring)."""
    return bool(media_info(path)["audio"])


def clip_audio(mp4: str, wav: str | None) -> str | None:
    """The file whose sound a take plays in the cut: the mp4 if it has an audio
    stream, else the take's `_h3.wav` if it exists, else None (silence).
    h3assemble's `--audio auto`."""
    if mp4 and os.path.isfile(mp4) and has_audio(mp4):
        return mp4
    if wav and os.path.isfile(wav):
        return wav
    return None


# ---------------------------------------------------------------------------
# peaks
# ---------------------------------------------------------------------------

def _scale(v: int) -> int:
    """|sample| on a 16-bit scale -> 0..255."""
    return min(255, (v * 255 + 16383) // 32767)


def _wav_peaks(path: str) -> tuple[list[int], float] | None:
    """(peaks at RATE a second, duration) for a PCM wav the stdlib can read,
    else None. Every channel counts (max over them)."""
    try:
        w = wave.open(path, "rb")
    except (wave.Error, EOFError, OSError):
        return None
    with w:
        ch, width, rate, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        if not rate or width not in (1, 2, 3, 4):
            return None
        per_bin = rate / RATE
        nbins = int(-(-n * RATE // rate)) if n else 0
        out = []
        done = 0                                    # frames read so far
        for b in range(nbins):
            upto = min(n, round((b + 1) * per_bin))
            data = w.readframes(upto - done)
            done = upto
            if not data:
                out.append(0)
                continue
            if width == 1:                          # unsigned 8-bit
                hi, lo = max(data) - 128, 128 - min(data)
                out.append(_scale(max(hi, lo) * 256))
                continue
            if width == 3:                          # keep the top 16 bits
                cut = bytearray(len(data) // 3 * 2)
                cut[0::2] = data[1::3]
                cut[1::2] = data[2::3]
                data, shift = bytes(cut), 0
            else:
                shift = 16 if width == 4 else 0
            a = array.array("h" if width in (2, 3) else "i")
            a.frombytes(data[:len(data) - len(data) % a.itemsize])
            if sys.byteorder == "big":
                a.byteswap()
            if not a:
                out.append(0)
                continue
            m = max(max(a), -min(a))
            out.append(_scale(m >> shift))
        return out, (n / rate)


def _ffmpeg_peaks(path: str) -> list[int]:
    """Peaks at RATE a second through ffmpeg (mono s16le at FF_RATE)."""
    if not shutil.which("ffmpeg"):
        raise PeaksError(f"ffmpeg is not on PATH: it is needed to read the sound of "
                         f"{os.path.basename(path)}")
    per_bin = FF_RATE // RATE
    # stderr goes to a temp file, so a chatty ffmpeg can't block on a full pipe
    with tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(["ffmpeg", "-v", "error", "-nostdin", "-i", path, "-vn",
                                 "-ac", "1", "-ar", str(FF_RATE), "-f", "s16le", "-"],
                                stdout=subprocess.PIPE, stderr=err)
        try:
            out = s16_peaks(proc.stdout, per_bin)
        finally:
            proc.stdout.close()
            proc.wait(timeout=60)
        err.seek(0)
        err_text = err.read().decode("utf-8", "replace")
    if proc.returncode != 0:
        raise PeaksError(f"ffmpeg could not read the sound of {os.path.basename(path)}: "
                         f"{err_text.strip()[-300:]}")
    return out


def _peak16(data: bytes) -> int:
    a = array.array("h")
    a.frombytes(data[:len(data) - len(data) % 2])
    if sys.byteorder == "big":
        a.byteswap()
    return _scale(max(max(a), -min(a))) if a else 0


def s16_peaks(stream, per_bin: int) -> list[int]:
    """Peaks of a mono s16le stream, one per `per_bin` samples (a short last
    bin counts too)."""
    out: list[int] = []
    size = per_bin * 2
    rest = b""
    while True:
        chunk = stream.read(size * RATE)             # about a second at a time
        if not chunk:
            break
        data = rest + chunk
        usable = len(data) - len(data) % size
        rest = data[usable:]
        for i in range(0, usable, size):
            out.append(_peak16(data[i:i + size]))
    if len(rest) >= 2:
        out.append(_peak16(rest))
    return out


def cache_path(ep: str, rel: str, path: str) -> str:
    st = os.stat(path)
    h = hashlib.sha1(f"{rel}|{st.st_size}|{st.st_mtime_ns}".encode("utf-8")).hexdigest()
    return os.path.join(ep, CACHE_DIR, h + ".json")


def file_peaks(ep: str, path: str, rel: str) -> dict:
    """{"duration", "rate": RATE, "peaks": [0..255 ...], "silent"} for the
    whole file, from the cache or computed (and cached)."""
    cp = cache_path(ep, rel, path)
    try:
        with open(cp, encoding="utf-8") as fh:
            data = json.load(fh)
        if data.get("version") == CACHE_VERSION:
            return {"duration": data["duration"], "rate": data["rate"],
                    "peaks": list(bytes.fromhex(data["peaks"])), "silent": data["silent"],
                    "cached": True}
    except (OSError, ValueError, KeyError, TypeError):
        pass
    info = media_info(path)
    got = _wav_peaks(path) if os.path.splitext(path)[1].lower() == ".wav" else None
    if got is not None:
        pk, dur = got
        silent = False
    elif not info["audio"]:
        pk, dur, silent = [], info["duration"], True
    else:
        pk = _ffmpeg_peaks(path)
        dur = info["duration"] if info["duration"] is not None else len(pk) / RATE
        silent = False
    data = {"version": CACHE_VERSION, "path": rel, "duration": dur, "rate": RATE,
            "silent": silent, "peaks": bytes(pk).hex()}
    try:
        os.makedirs(os.path.dirname(cp), exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".tmp_", suffix=".json", dir=os.path.dirname(cp))
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp, cp)
    except OSError:
        pass                                      # a read-only episode still answers
    return {"duration": dur, "rate": RATE, "peaks": pk, "silent": silent, "cached": False}


def resample(pk: list[int], rate: float, bins: int, start: float, end: float) -> list[int]:
    """`bins` values over start..end seconds of peaks sampled `rate` a second:
    each is the max of the source bins it covers (the nearest one when it
    covers less than one)."""
    if bins <= 0 or not pk:
        return []
    a, b = start * rate, end * rate
    width = (b - a) / bins
    out = []
    last = len(pk) - 1
    for i in range(bins):
        lo = a + i * width
        hi = lo + width
        i0 = min(last, max(0, int(lo)))
        i1 = min(len(pk), max(i0 + 1, int(-(-hi // 1))))
        out.append(max(pk[i0:i1]))
    return out


def peaks(ep: str, path: str, rel: str, bins: int | None = None,
          start: float | None = None, end: float | None = None) -> dict:
    """GET /h3pipe/peaks: {"duration", "bins", "peaks", "start", "end"} (plus
    "silent": true and no peaks for a file without sound). `bins` defaults to
    RATE a second of the range. ValueError for a bad range."""
    whole = file_peaks(ep, path, rel)
    dur = whole["duration"]
    if whole["silent"]:
        return {"duration": dur, "bins": 0, "peaks": [], "silent": True,
                "start": start or 0.0, "end": end if end is not None else dur}
    total = dur if dur is not None else len(whole["peaks"]) / RATE
    s = 0.0 if start is None else max(0.0, float(start))
    e = total if end is None else min(total, float(end))
    if e <= s:
        raise ValueError(f"end ({e:g}) must be after start ({s:g}) and inside the file "
                         f"({total:.3f} s)")
    if bins is None:
        bins = max(1, round((e - s) * RATE))
    return {"duration": dur, "bins": bins, "start": s, "end": e,
            "peaks": resample(whole["peaks"], RATE, bins, s, e)}
