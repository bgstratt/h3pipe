"""
A shot's slice of the dialogue recording, as a stereo PCM wav ComfyUI's
LoadAudio reads (the dub anchor, see compile.stage_inputs).

A PCM wav is cut with the stdlib `wave` module; anything else (mp3, m4a,
flac, float wav) goes through ffmpeg, as h3assemble does. Mono becomes stereo:
H3's audio VAE encodes two channels.

Stdlib only.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import wave


class AudioError(ValueError):
    """The recording can't be read or the window is outside it."""


def _stereo(raw: bytes, width: int) -> bytes:
    """Mono PCM frames -> the same samples on both channels."""
    out = bytearray(len(raw) * 2)
    for i in range(0, len(raw), width):
        s = raw[i:i + width]
        j = 2 * i
        out[j:j + width] = s
        out[j + width:j + 2 * width] = s
    return bytes(out)


def _wave_slice(src: str, dst: str, start: float | None, end: float | None) -> float:
    with wave.open(src, "rb") as w:
        ch, width, sr, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        a = 0 if start is None else max(0, int(round(start * sr)))
        b = n if end is None else min(n, int(round(end * sr)))
        if b <= a:
            raise AudioError(f"{os.path.basename(src)} is {n / sr:.2f}s long: nothing between "
                             f"{start:.2f}s and {end:.2f}s")
        w.setpos(a)
        raw = w.readframes(b - a)
    if ch == 1:
        raw, ch = _stereo(raw, width), 2
    with wave.open(dst, "wb") as o:
        o.setnchannels(ch)
        o.setsampwidth(width)
        o.setframerate(sr)
        o.writeframes(raw)
    return (b - a) / sr


def _ffmpeg_slice(src: str, dst: str, start: float | None, end: float | None) -> float:
    if not shutil.which("ffmpeg"):
        raise AudioError(f"{os.path.basename(src)} is not a PCM wav and ffmpeg is not on "
                         f"PATH to cut it: convert it to a 16-bit wav")
    cmd = ["ffmpeg", "-y", "-v", "error"]
    if start is not None:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", src]
    if end is not None:
        cmd += ["-t", f"{end - (start or 0.0):.3f}"]
    cmd += ["-ac", "2", "-acodec", "pcm_s16le", dst]
    r = subprocess.run(cmd, capture_output=True, timeout=300)
    if r.returncode or not os.path.isfile(dst):
        raise AudioError(f"ffmpeg could not cut {os.path.basename(src)}: "
                         f"{r.stderr.decode('utf-8', 'replace')[-200:]}")
    with wave.open(dst, "rb") as w:
        return w.getnframes() / w.getframerate()


def cut(src: str, dst: str, start: float | None = None, end: float | None = None) -> float:
    """Write `src` from `start` to `end` seconds (None: its start / end) to
    `dst` as a stereo wav; the seconds written."""
    if not os.path.isfile(src):
        raise AudioError(f"no recording at {src}")
    try:
        return _wave_slice(src, dst, start, end)
    except (wave.Error, EOFError):
        return _ffmpeg_slice(src, dst, start, end)
