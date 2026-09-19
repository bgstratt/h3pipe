"""
ComfyUI-H3-Shotlist
===================

Shot-list driven production nodes for MiniMax H3 Ref2VA.

One shot per queue. Set the Shot Index primitive to `increment` and set
batch count to the shot count to run a whole sequence unattended.

Nodes
-----
H3ShotListLoader   reads shotlist.json + the project's reference assets and
                   emits everything MiniMaxH3ReferenceToVideo needs
H3ShotInfo         readout of the current shot, for the canvas
H3SaveShot         writes frames / audio / mp4 under a strict shot naming
                   convention, honouring the shot's audio policy
H3SaveRefTake      writes one reference-image take (h3refs) and closes its
                   sidecar

Project layout expected
-----------------------
    <project_root>/
        shotlist/shotlist.json
        refs/<character>/<name>_sheet_4panel.png   horizontal 4-panel strip
        refs/props/<name>.png                      single clean object image
        refs/_bg/<location>.png                    background plate -> <Picture 4>
        audio/master_dialogue.wav                  (source_track mode)
        audio/voices/<character>_sample.wav        (clone mode)
        renders/<shot_id>/...                      written by H3SaveShot

Reference slots
---------------
    <Picture 1..3>  the shot's subjects, characters first then props/vehicles
    <Picture 4>     the location plate, always

There is no style reference image. Style is already carried by every sheet and
plate (all drawn in it) and stated in the prompt text, so slot 4 buys concrete
layout, palette, lighting direction and depth instead -- and keeps one location
looking like one place across every cut in a sequence.
"""

from __future__ import annotations

import datetime
import json
import math
import os
import re
import subprocess
import tempfile

import numpy as np
import torch

# ---------------------------------------------------------------------------
# H3 constants
# ---------------------------------------------------------------------------

GRID_STEP, GRID_BASE, GRID_MAX = 17, 5, 3592

# Take thumbnails. STRIP_FRAMES must match h3takes.STRIP_FRAMES: the editor
# reads the strip as that many equal-width cells.
STRIP_FRAMES = 8
THUMB_LONG_SIDE = 480
STRIP_CELL_WIDTH = 192
JPEG_QUALITY = 85
AUDIO_POLICIES = ["generate", "dub", "dub_keep_foley", "clone"]
PANEL_MODES = ["auto", "full", "pair", "face", "body"]


def snap_up(frames: int) -> int:
    """Round up onto H3's 17k+5 frame grid."""
    if frames <= GRID_BASE:
        return GRID_BASE
    value = GRID_STEP * math.ceil((frames - GRID_BASE) / GRID_STEP) + GRID_BASE
    if value > GRID_MAX:
        raise ValueError(
            f"{frames} frames exceeds H3's maximum of {GRID_MAX} "
            f"({GRID_MAX / 24:.2f}s at 24fps). Split this shot."
        )
    return value


def _blank(h: int = 64, w: int = 64) -> torch.Tensor:
    return torch.zeros((1, h, w, 3), dtype=torch.float32)


def _neutral(h: int, w: int) -> torch.Tensor:
    """Flat mid-grey: the stand-in for a missing reference picture when the
    shot is rendered anyway (`missing_refs: blank`). It carries no layout,
    colour or identity, so the prompt does the work -- close to text-to-video."""
    return torch.full((1, h, w, 3), 0.5, dtype=torch.float32)


# ---------------------------------------------------------------------------
# Image / audio IO
#
# Audio deliberately avoids torchaudio. As of 2.13 it routes both load and save
# through torchcodec and raises ImportError when that is absent, which turns
# "write a wav" into an install problem. PCM wav needs no library, so the
# stdlib handles it, torchaudio is tried only as a middle option, and ffmpeg
# covers mp3/m4a/flac. Nothing here adds a dependency.
# ---------------------------------------------------------------------------

def load_image(path: str) -> torch.Tensor:
    """Load an image as a ComfyUI IMAGE tensor, [1, H, W, 3] float32 0..1."""
    from PIL import Image, ImageOps

    img = Image.open(path)
    img = ImageOps.exif_transpose(img).convert("RGB")
    arr = np.asarray(img, dtype=np.float32) / 255.0
    return torch.from_numpy(arr)[None, ...]


def crop_panels(sheet: torch.Tensor, panels: int, keep: list[int]) -> torch.Tensor:
    """Take a horizontal N-panel strip and keep a subset of panels.

    The strip's short side is the vertical one, which is what H3 scales to
    2048 in `max` mode — so dropping panels narrows the image without
    costing per-panel resolution. It is a pure reference-cost reduction.
    """
    _, h, w, _ = sheet.shape
    if panels <= 1 or not keep:
        return sheet
    pw = w // panels
    keep = [i for i in keep if 0 <= i < panels]
    if not keep:
        return sheet
    # contiguous runs stay contiguous so the result is still a clean strip
    slices = [sheet[:, :, i * pw:(i + 1) * pw, :] for i in sorted(keep)]
    return torch.cat(slices, dim=2)


def _read_pcm_wav(path: str) -> tuple[torch.Tensor, int]:
    """Read an integer PCM wav with the stdlib. Raises for anything exotic."""
    import wave

    with wave.open(path, "rb") as w:
        ch, width, sr, frames = (w.getnchannels(), w.getsampwidth(),
                                 w.getframerate(), w.getnframes())
        raw = w.readframes(frames)
    if width == 2:
        a = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 4:
        a = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
    elif width == 1:
        a = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    else:
        raise ValueError(f"unsupported sample width {width}")
    return torch.from_numpy(a.reshape(-1, ch).T.copy()), sr


def _ffmpeg_decode(path: str) -> tuple[torch.Tensor, int]:
    """Last resort: let ffmpeg turn anything into a PCM wav we can read."""
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "decoded.wav")
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-i", path,
                 "-acodec", "pcm_s16le", out],
                check=True, capture_output=True, timeout=300)
        except FileNotFoundError:
            raise RuntimeError(
                f"cannot decode {os.path.basename(path)}: it is not a plain PCM wav "
                f"and ffmpeg is not on PATH. Convert it to 16-bit wav, or install "
                f"ffmpeg.") from None
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                f"ffmpeg could not decode {os.path.basename(path)}: "
                f"{exc.stderr.decode('utf-8', 'replace')[-200:]}") from None
        return _read_pcm_wav(out)


def _decode_audio(path: str) -> tuple[torch.Tensor, int]:
    """Decode to [C, T] float32 in -1..1, without depending on torchaudio.

    torchaudio 2.13 delegates both load and save to torchcodec and raises
    ImportError when it is absent — which is a hard failure for something as
    ordinary as reading a wav. The stdlib handles PCM wav natively, so try that
    first, then torchaudio if it happens to work, then ffmpeg for mp3/m4a/flac.
    """
    if path.lower().endswith(".wav"):
        try:
            return _read_pcm_wav(path)
        except Exception:
            pass                      # float or compressed wav — fall through
    try:
        import torchaudio
        return torchaudio.load(path)
    except Exception:
        pass
    return _ffmpeg_decode(path)


def load_audio(path: str, start: float | None = None,
               end: float | None = None) -> dict:
    """Load audio as a ComfyUI AUDIO dict, optionally slicing [start, end)."""
    waveform, sr = _decode_audio(path)          # [C, T]
    if start is not None:
        a = max(0, int(start * sr))
        b = int(end * sr) if end is not None else waveform.shape[-1]
        b = min(b, waveform.shape[-1])
        if b <= a:
            raise ValueError(
                f"empty audio slice {start}..{end} from {os.path.basename(path)} "
                f"(the file is only {waveform.shape[-1] / sr:.2f}s long)")
        waveform = waveform[:, a:b]
    return {"waveform": waveform.unsqueeze(0), "sample_rate": sr}


def silent_audio(seconds: float = 0.1, sr: int = 44100) -> dict:
    n = max(1, int(seconds * sr))
    return {"waveform": torch.zeros((1, 1, n), dtype=torch.float32), "sample_rate": sr}


def save_audio(audio: dict, path: str) -> None:
    """Write a 16-bit PCM wav with the stdlib.

    Deliberately not torchaudio.save: as of 2.13 it routes through torchcodec
    and raises ImportError without it. Writing the wav directly removes a
    dependency rather than adding one, and cannot break when torchaudio
    changes backends again.
    """
    import wave

    wav = audio["waveform"]
    if wav.dim() == 3:
        wav = wav[0]                                   # [B, C, T] -> [C, T]
    if wav.dim() == 1:
        wav = wav.unsqueeze(0)
    arr = wav.detach().cpu().float().clamp(-1.0, 1.0).numpy()
    pcm = np.round(arr.T * 32767.0).astype("<i2")      # [T, C], interleaved
    with wave.open(path, "wb") as w:
        w.setnchannels(int(pcm.shape[1]))
        w.setsampwidth(2)
        w.setframerate(int(audio["sample_rate"]))
        w.writeframes(pcm.tobytes())


# ---------------------------------------------------------------------------
# H3ShotListLoader
# ---------------------------------------------------------------------------

class H3ShotListLoader:
    """Emit one shot's worth of Ref2VA conditioning from shotlist.json."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "project_root": ("STRING", {"default": "path/to/project/ep01"}),
                "shotlist_file": ("STRING", {"default": "shotlist/shotlist.json"}),
                "index": ("INT", {"default": 0, "min": 0, "max": 100000}),
                "panel_mode": (PANEL_MODES, {"default": "auto"}),
                "fps": ("FLOAT", {"default": 24.0, "min": 1.0, "max": 60.0, "step": 1.0}),
            },
            "optional": {
                "resolution_override": ("STRING", {"default": ""}),
            },
        }

    RETURN_TYPES = (
        "STRING", "STRING",
        "IMAGE", "IMAGE", "IMAGE", "IMAGE",
        "AUDIO", "AUDIO", "AUDIO",
        "INT", "INT", "INT", "INT", "INT",
        "STRING", "STRING", "INT",
    )
    RETURN_NAMES = (
        "shot_id", "prompt",
        "ref_1", "ref_2", "ref_3", "ref_bg",
        "ref_audio_1", "ref_audio_2", "ref_audio_3",
        "width", "height", "length", "steps", "seed",
        "audio_policy", "info", "shot_count",
    )
    FUNCTION = "load"
    CATEGORY = "H3/shotlist"

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _resolve(root: str, path: str) -> str:
        if not path:
            return ""
        return path if os.path.isabs(path) else os.path.join(root, path)

    @staticmethod
    def _panels_for(mode: str, cast_n: int, shot_size: str) -> tuple[str, list[int]]:
        """Decide which panels of a 4-panel strip (3/4, side, back, face) to keep.

        Reference cost scales with strip width, so crowded shots drop to the
        two panels that actually carry identity.
        """
        if mode == "full":
            return "full", [0, 1, 2, 3]
        if mode == "pair":
            return "pair", [0, 3]
        if mode == "face":
            return "face", [3]
        if mode == "body":
            return "body", [0]
        # auto -- one panel, so the reference never contains a second figure
        # for H3 to duplicate into frame.
        if cast_n <= 1 and shot_size in ("close", "cu"):
            return "face", [3]
        return "body", [0]

    # -- cache key ---------------------------------------------------------

    @classmethod
    def IS_CHANGED(cls, project_root, shotlist_file, index, panel_mode, fps,
                   resolution_override=""):
        """Re-read when the shotlist or this shot's references change on disk.

        Without this, ComfyUI caches the node's output against its widget
        values, which do not change when you hand-edit a prompt in the
        shotlist or regenerate a character sheet — the queue then renders the
        stale shot and nothing you edited appears. Any exception here means
        "assume changed", which is the safe direction.
        """
        def stamp(path: str) -> str:
            try:
                st = os.stat(path)
                return f"{st.st_mtime_ns}:{st.st_size}"
            except OSError:
                return "-"

        root = os.path.normpath(project_root)
        sl_path = cls._resolve(root, shotlist_file)
        parts = [stamp(sl_path), str(index), str(panel_mode), str(fps),
                 str(resolution_override)]
        try:
            with open(sl_path, encoding="utf-8") as fh:
                doc = json.load(fh)
            shot = doc["shots"][int(index)]
            book = doc.get("subjects", {})
            files = [book.get(sid, {}).get("sheet", "") for sid in shot.get("subjects", [])]
            files.append(shot.get("background", ""))
            files += [r.get("sample", "") for r in shot.get("voice_refs", [])]
            files.append(doc.get("defaults", {}).get("master_track", ""))
            parts += [stamp(cls._resolve(root, f)) for f in files if f]
        except Exception:
            return float("nan")     # unreadable: always re-run
        return "|".join(parts)

    # -- main --------------------------------------------------------------

    def load(self, project_root, shotlist_file, index, panel_mode, fps,
             resolution_override=""):
        root = os.path.normpath(project_root)
        sl_path = self._resolve(root, shotlist_file)
        if not os.path.isfile(sl_path):
            raise FileNotFoundError(f"shotlist not found: {sl_path}")

        with open(sl_path, encoding="utf-8") as fh:
            doc = json.load(fh)

        shots = doc.get("shots", [])
        if not shots:
            raise ValueError(f"{sl_path} contains no shots")
        shot = shots[index % len(shots)]

        defaults = doc.get("defaults", {})
        book = doc.get("subjects", doc.get("cast", {}))

        # ---- geometry ----------------------------------------------------
        if resolution_override.strip():
            m = re.match(r"^\s*(\d+)\s*[xX*]\s*(\d+)\s*$", resolution_override)
            if not m:
                raise ValueError(
                    f"resolution_override must look like 1344x768, got {resolution_override!r}"
                )
            width, height = int(m.group(1)), int(m.group(2))
        else:
            width = int(shot.get("width", defaults.get("width", 1344)))
            height = int(shot.get("height", defaults.get("height", 768)))
        for name, v in (("width", width), ("height", height)):
            if v % 32:
                raise ValueError(
                    f"{name}={v} is not a multiple of 32. H3 rejects it. "
                    f"Nearest legal: {v // 32 * 32} or {(v // 32 + 1) * 32}."
                )

        # ---- duration ----------------------------------------------------
        if "audio_in" in shot and "audio_out" in shot:
            duration = float(shot["audio_out"]) - float(shot["audio_in"])
            if duration <= 0:
                raise ValueError(f"shot {shot.get('id')}: audio_out must follow audio_in")
        else:
            duration = float(shot.get("duration", defaults.get("duration", 3.04)))
        length = snap_up(max(1, round(duration * fps)))

        # ---- references --------------------------------------------------
        # Slots 1-3 are the shot's subjects (characters first, then props and
        # vehicles). Slot 4 is ALWAYS the location plate. There is no style
        # reference: the style is already carried by every sheet and plate,
        # which are all drawn in it, and by the text prefix. Spending slot 4 on
        # the background instead buys concrete layout, palette, lighting
        # direction and depth for this specific shot -- and keeps the same
        # location looking like the same place across every cut in a sequence.
        subject_ids = shot.get("subjects") or shot.get("cast") or []
        size = (shot.get("size") or "medium").lower()
        panels_total = int(defaults.get("sheet_panels", 4))

        # `missing_refs: blank` (set by h3jobs when a render is asked for anyway)
        # turns a missing picture into flat grey and a missing audio reference
        # into none, instead of failing the shot.
        render_anyway = shot.get("missing_refs") == "blank"
        blanked: list[str] = []

        # Background first: it is also what fills any unused subject socket.
        bg_path = self._resolve(root, shot.get("background", ""))
        if not bg_path or not os.path.isfile(bg_path):
            if not render_anyway:
                raise FileNotFoundError(
                    f"shot {shot.get('id')}: background plate not found at "
                    f"{bg_path or '<unset>'}. Every shot needs one -- it is <Picture 4>."
                )
            ref_bg = _neutral(height, width)
            blanked.append("Picture 4")
        else:
            ref_bg = load_image(bg_path)

        # Characters get the panel-crop cost lever; props are single images.
        n_chars = sum(1 for s in subject_ids
                      if book.get(s, {}).get("kind", "character") == "character")
        declared = shot.get("panels")
        if panel_mode != "auto":
            mode_used, keep = self._panels_for(panel_mode, 99, size)
        elif declared:
            d = int(declared)
            if d == 1:
                view = shot.get("panel_view", "body")
                keep = [3] if view == "face" else [0]
                mode_used = view
            elif d == 4:
                keep, mode_used = [0, 1, 2, 3], "full"
            else:
                keep, mode_used = [0, 3], "pair"
        else:
            mode_used, keep = self._panels_for("auto", len(subject_ids), size)

        refs: list[torch.Tensor] = []
        ref_notes: list[str] = []
        for sid in subject_ids[:3]:
            entry = book.get(sid, {})
            kind = entry.get("kind", "character")
            path = self._resolve(root, entry.get("sheet", ""))
            if not path or not os.path.isfile(path):
                if not render_anyway:
                    raise FileNotFoundError(
                        f"shot {shot.get('id')}: reference for '{sid}' not found at "
                        f"{path or '<unset>'}"
                    )
                refs.append(_neutral(1024, 1024))
                blanked.append(f"Picture {len(refs)}")
                ref_notes.append(f"{sid} ({kind}) MISSING -> flat grey")
                continue
            img = load_image(path)
            if kind == "character":
                img = crop_panels(img, panels_total, keep)
                note = f"{sid} ({kind}) {os.path.basename(path)} [{img.shape[2]}x{img.shape[1]}, {mode_used}]"
            else:
                note = f"{sid} ({kind}) {os.path.basename(path)} [{img.shape[2]}x{img.shape[1]}, whole]"
            refs.append(img)
            ref_notes.append(note)

        if not refs:
            # Plate-only shot: an establishing view with voiceover over it. The
            # location fills every slot and becomes <Subject 1> in the prompt.
            refs = [ref_bg]
            ref_notes.append("no subjects — background fills all four slots")
        if len(subject_ids) > 3:
            raise ValueError(
                f"shot {shot.get('id')}: {len(subject_ids)} subjects but only 3 slots "
                f"-- slot 4 is the background. Drop {len(subject_ids) - 3}."
            )

        n_sub = len(refs)
        while len(refs) < 3:
            refs.append(ref_bg)
        ref_notes.append(f"background {os.path.basename(bg_path or '') or '(none)'}"
                         f"{' MISSING -> flat grey' if 'Picture 4' in blanked else ''} -> <Picture 4>"
                         + (f" (also fills slots {n_sub + 1}-3)" if n_sub < 3 else ""))

        # ---- audio -------------------------------------------------------
        policy = (shot.get("audio_policy") or defaults.get("audio_policy") or "generate")
        if policy not in AUDIO_POLICIES:
            raise ValueError(
                f"shot {shot.get('id')}: audio_policy {policy!r} not one of {AUDIO_POLICIES}"
            )

        audio_note = "none"
        audio_refs: list[dict | None] = [None, None, None]

        if policy == "generate":
            # Deliberately None, not silence. ref_audio is a *reference* socket:
            # handing it a silent clip tells H3 "match this audio", which biases
            # it toward generating silence.
            audio_note = "generate (no audio reference)"

        elif policy == "clone":
            # One timbre sample PER SPEAKER. A two-hander needs two, or the
            # second character gets voiced from the first one's sample.
            # `vrefs`, not `refs` -- `refs` is the image reference list above and
            # shadowing it silently truncates the picture slots.
            vrefs = shot.get("voice_refs") or []
            if not vrefs and shot.get("voice_subject"):
                vrefs = [{"subject": shot["voice_subject"],
                          "sample": book.get(shot["voice_subject"], {}).get("voice_sample", "")}]
            if len(vrefs) > 3:
                raise ValueError(
                    f"shot {shot.get('id')}: {len(vrefs)} voice references but H3 takes "
                    f"at most 3. Split the shot."
                )
            names = []
            for i, r in enumerate(vrefs[:3]):
                p = self._resolve(root, r.get("sample", ""))
                if (not p or not os.path.isfile(p)) and render_anyway:
                    blanked.append(f"Audio {i + 1}")
                    names.append(f"<Audio {i + 1}>={r.get('subject')} MISSING (none)")
                    continue
                if not p or not os.path.isfile(p):
                    raise FileNotFoundError(
                        f"shot {shot.get('id')}: voice sample for '{r.get('subject')}' "
                        f"not found at {p or '<unset>'}. clone mode needs one per speaker."
                    )
                audio_refs[i] = load_audio(p)
                names.append(f"<Audio {i + 1}>={r.get('subject')}")
            audio_note = "clone <- " + ", ".join(names) if names else "clone (no refs)"

        else:  # dub / dub_keep_foley
            # ONE clip: the slice of the recorded mix, which already contains
            # every voice in the shot.
            src = shot.get("audio_file") or defaults.get("master_track", "")
            src = self._resolve(root, src)
            if (not src or not os.path.isfile(src)) and render_anyway:
                blanked.append("Audio 1")
                src = ""
            elif not src or not os.path.isfile(src):
                raise FileNotFoundError(
                    f"shot {shot.get('id')}: audio_policy '{policy}' needs the recorded "
                    f"mix; looked for {src or '<unset>'}"
                )
            if not src:
                audio_note = f"{policy} <- recording MISSING (no audio reference)"
            elif "audio_in" in shot and not shot.get("audio_file"):
                audio_refs[0] = load_audio(src, float(shot["audio_in"]),
                                           float(shot["audio_out"]))
                audio_note = (f"{policy} <- {os.path.basename(src)} "
                              f"[{shot['audio_in']:.2f}..{shot['audio_out']:.2f}]")
            else:
                audio_refs[0] = load_audio(src)
                audio_note = f"{policy} <- {os.path.basename(src)}"

        # ---- prompt / seed -----------------------------------------------
        prompt = shot.get("prompt", "")
        if isinstance(prompt, list):
            prompt = "\n\n".join(prompt)
        prefix = doc.get("prompt_prefix", "")
        if prefix:
            prompt = f"{prefix}\n\n{prompt}"
        seed = int(shot.get("seed", 0)) & 0x7FFF_FFFF_FFFF_FFFF
        # Sampler steps travel with the shot so the proxy pass actually renders
        # at its own step count instead of inheriting whatever the workflow's
        # scheduler widget happens to say.
        steps = int(shot.get("steps", defaults.get("steps", 4)))

        pad = length - round(duration * fps)
        # 1-based, and stated as "of N" rather than "/ N-1". The Shot Index
        # widget is NOT a progress readout: ComfyUI serializes the whole batch
        # up front, incrementing the widget once per prompt at queue time, so it
        # reads 36 while shot 1 is still rendering. This line updates per
        # execution, so it is the one that actually tracks the batch.
        pos = index % len(shots)
        info = "\n".join([
            f"shot {pos + 1} of {len(shots)}   —   {shot.get('id', '?')}",
            f"{width}x{height}   {length} frames   {length / fps:.2f}s   {steps} steps"
            f"   (asked {duration:.2f}s, pad {pad}f)",
            f"subjects: {', '.join(subject_ids) if subject_ids else '- (plate only)'}   size: {size}",
            f"voices: {', '.join(shot.get('voices', [])) or '-'}",
            f"audio: {audio_note}",
            *([f"RENDERED WITHOUT: {', '.join(blanked)} (missing refs)"] if blanked else []),
            "refs:",
            *[f"  {n}" for n in ref_notes],
            f"seed: {seed}",
        ])

        print(f"[h3] shot {pos + 1}/{len(shots)}  {shot.get('id', '?')}  "
              f"{width}x{height}  {length}f ({length / fps:.2f}s)  {steps} steps  "
              f"{policy}", flush=True)

        return (
            str(shot.get("id", f"shot_{index:04d}")), prompt,
            refs[0], refs[1], refs[2], ref_bg,
            audio_refs[0], audio_refs[1], audio_refs[2],
            width, height, length, steps, seed,
            policy, info, len(shots),
        )


# ---------------------------------------------------------------------------
# H3ShotInfo
# ---------------------------------------------------------------------------

class H3ShotInfo:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"info": ("STRING", {"forceInput": True})}}

    RETURN_TYPES = ()
    FUNCTION = "show"
    OUTPUT_NODE = True
    CATEGORY = "H3/shotlist"

    def show(self, info):
        return {"ui": {"text": [info]}}


# ---------------------------------------------------------------------------
# H3SaveShot
# ---------------------------------------------------------------------------

def _write_json_atomic(path: str, data) -> None:
    """Same bytes and atomicity as h3takes.write_json (not importable here):
    temp file in the same folder, then os.replace; LF, indent 2, trailing newline."""
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp_", suffix=".json", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _now() -> str:
    """ISO-8601 local time with offset, seconds precision (as h3takes.now)."""
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


class H3SaveShot:
    """Write one shot's deliverables under a strict naming convention.

    <project_root>/renders/<shot_id>/
        <shot_id>_t<take>.mp4          picture + the audio the policy selects
        <shot_id>_t<take>_h3.wav       H3's generated mix, always kept
        <shot_id>_t<take>_foley.wav    vocal-stripped bed (dub_keep_foley only)
        <shot_id>_t<take>.jpg          middle frame, longest side 480 px
        <shot_id>_t<take>_strip.jpg    STRIP_FRAMES frames side by side, 192 px
                                       wide each, for hover scrub
        <shot_id>_t<take>.json         the take's sidecar, when `sidecar` is set:
                                       status, finished, frames, fps, mp4,
                                       thumb, strip and save_notes are filled in,
                                       every other field is left alone
        frames/<shot_id>_t<take>_%06d.png   optional PNG sequence

    `sidecar` is the path the queuer wrote the take's `queued` record to
    (absolute, or relative to project_root); empty means there is none. The
    rules are h3takes.py's (not importable inside ComfyUI, so followed here by
    hand). The mp4 comes first: a failure writing thumbnails or the sidecar is
    reported in the status string and never raised.
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "images": ("IMAGE",),
                "shot_id": ("STRING", {"forceInput": True}),
                "audio_policy": ("STRING", {"forceInput": True}),
                "project_root": ("STRING", {"default": "path/to/project/ep01"}),
                "subfolder": ("STRING", {"default": "renders"}),
                "take": ("INT", {"default": 1, "min": 1, "max": 999}),
                "fps": ("FLOAT", {"default": 24.0, "min": 1.0, "max": 60.0, "step": 1.0}),
                "save_frames": ("BOOLEAN", {"default": False}),
            },
            "optional": {
                "audio": ("AUDIO",),
                # Last, so saved workflows keep their widget order.
                "sidecar": ("STRING", {"default": ""}),
            },
        }

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("shot_dir", "status")
    FUNCTION = "save"
    OUTPUT_NODE = True
    CATEGORY = "H3/shotlist"

    # -- vocal stripping ---------------------------------------------------

    @staticmethod
    def _strip_vocals(wav_path: str, out_path: str) -> str:
        """Demucs the H3 mix and keep everything except the vocal stem."""
        try:
            with tempfile.TemporaryDirectory() as tmp:
                subprocess.run(
                    ["python", "-m", "demucs", "--two-stems", "vocals",
                     "-o", tmp, wav_path],
                    check=True, capture_output=True, timeout=600,
                )
                stem = os.path.splitext(os.path.basename(wav_path))[0]
                for dirpath, _, files in os.walk(tmp):
                    if os.path.basename(dirpath) == stem and "no_vocals.wav" in files:
                        import shutil
                        shutil.copy(os.path.join(dirpath, "no_vocals.wav"), out_path)
                        return "foley bed written"
            return "demucs ran but produced no no_vocals.wav"
        except FileNotFoundError:
            return "demucs not installed - foley bed skipped (pip install demucs)"
        except subprocess.CalledProcessError as exc:
            return f"demucs failed: {exc.stderr.decode('utf-8', 'replace')[:160]}"
        except subprocess.TimeoutExpired:
            return "demucs timed out - foley bed skipped"

    # -- main --------------------------------------------------------------

    def save(self, images, shot_id, audio_policy, project_root, subfolder,
             take, fps, save_frames, audio=None, sidecar=""):
        from PIL import Image

        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", shot_id) or "shot"
        shot_dir = os.path.join(os.path.normpath(project_root), subfolder, safe)
        os.makedirs(shot_dir, exist_ok=True)
        stem = f"{safe}_t{take:02d}"
        notes: list[str] = []

        # ---- frames ------------------------------------------------------
        frame_dir = os.path.join(shot_dir, "frames")
        if save_frames:
            os.makedirs(frame_dir, exist_ok=True)
            arr = (images.clamp(0, 1).cpu().numpy() * 255.0 + 0.5).astype(np.uint8)
            for i, frame in enumerate(arr):
                Image.fromarray(frame).save(
                    os.path.join(frame_dir, f"{stem}_{i:06d}.png"), compress_level=4
                )
            notes.append(f"{len(arr)} frames -> frames/")

        # ---- audio -------------------------------------------------------
        h3_wav = os.path.join(shot_dir, f"{stem}_h3.wav")
        mux_audio = None
        if audio is not None:
            save_audio(audio, h3_wav)
            notes.append("H3 mix -> _h3.wav")
            if audio_policy == "generate":
                mux_audio = h3_wav
            elif audio_policy == "dub_keep_foley":
                foley = os.path.join(shot_dir, f"{stem}_foley.wav")
                notes.append(self._strip_vocals(h3_wav, foley))
                mux_audio = foley if os.path.isfile(foley) else None
            # 'dub' and 'clone' leave the mp4 mute: the real vocal is laid in
            # at conform, and muxing H3's competing vocal only gets in the way.

        # ---- mp4 ---------------------------------------------------------
        mp4 = os.path.join(shot_dir, f"{stem}.mp4")
        status = self._encode(images, mp4, fps, mux_audio, frame_dir if save_frames else None)
        notes.append(status)
        mp4_ok = status.startswith("mp4 written") and os.path.isfile(mp4)

        # ---- thumbnails --------------------------------------------------
        # After the mp4, and never fatal: the render is the valuable thing.
        thumb = strip = None
        try:
            thumb = self._write_thumb(images, os.path.join(shot_dir, f"{stem}.jpg"))
        except Exception as exc:
            notes.append(f"thumbnail failed: {exc}")
        try:
            strip = self._write_strip(images, os.path.join(shot_dir, f"{stem}_strip.jpg"))
        except Exception as exc:
            notes.append(f"strip failed: {exc}")

        # ---- sidecar -----------------------------------------------------
        sidecar = (sidecar or "").strip()
        if sidecar:
            try:
                self._finish_sidecar(
                    sidecar, os.path.normpath(project_root), shot_id, take, notes,
                    stem=stem, status="ok" if mp4_ok else "failed",
                    frames=int(images.shape[0]), fps=float(fps),
                    mp4=os.path.basename(mp4) if mp4_ok else None,
                    thumb=thumb, strip=strip)
            except Exception as exc:
                notes.append(f"sidecar update failed: {exc}")
            else:
                self._notify_take(project_root, subfolder, shot_dir, shot_id, take,
                                  "ok" if mp4_ok else "failed", thumb)

        return (shot_dir, f"{stem}: " + "; ".join(notes))

    # -- live update -------------------------------------------------------

    @staticmethod
    def _notify_take(project_root: str, subfolder: str, shot_dir: str, shot_id: str,
                     take: int, status: str, thumb) -> None:
        """Tell open h3pipe editors this take is closed (the `h3pipe.take`
        event of docs/API.md). Only inside ComfyUI; never affects saving."""
        try:
            from server import PromptServer
            server = getattr(PromptServer, "instance", None)
            if server is None:
                return
            ep = os.path.abspath(project_root)
            last = os.path.basename(os.path.normpath(subfolder or ""))
            thumb_rel = (os.path.relpath(os.path.join(shot_dir, thumb), ep).replace(os.sep, "/")
                         if thumb else None)
            server.send_sync("h3pipe.take", {
                "ep": ep, "pass": "proxy" if last == "renders_proxy" else "final",
                "shot": shot_id, "take": int(take), "status": status, "thumb": thumb_rel})
        except Exception:
            pass

    # -- sidecar -----------------------------------------------------------

    @staticmethod
    def _finish_sidecar(sidecar: str, root: str, shot_id: str, take: int,
                        notes: list[str], *, stem: str, status: str, frames: int,
                        mp4, thumb, strip, fps: float | None = None) -> None:
        """Close the take's record: set the saver's fields, leave the rest alone.

        Warnings go into `notes` first, so they reach both save_notes and the
        node's returned status string.
        """
        path = sidecar if os.path.isabs(sidecar) else os.path.join(root, sidecar)
        name = os.path.basename(path)
        data = None
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, dict):
                notes.append(f"sidecar {name} is not a JSON object; rewritten")
                data = None
        except FileNotFoundError:
            notes.append(f"sidecar {name} was missing; created")
        except (OSError, ValueError) as exc:
            notes.append(f"sidecar {name} unreadable ({exc.__class__.__name__}); rewritten")
        if data is None:
            data = {"shot": shot_id, "take": take}
        elif data.get("shot") != shot_id or data.get("take") != take:
            notes.append(f"warning: sidecar {name} is for shot {data.get('shot')!r} "
                         f"take {data.get('take')!r}, but this is {shot_id!r} take {take}")
        data.update(status=status, finished=_now(), frames=frames,
                    mp4=mp4, thumb=thumb, strip=strip,
                    save_notes=f"{stem}: " + "; ".join(notes))
        if fps:
            # the mp4's frame rate (the target's: Wan 14B saves 16 fps)
            data["fps"] = float(fps)
        _write_json_atomic(path, data)

    # -- thumbnails --------------------------------------------------------

    @staticmethod
    def _frame(images, i: int):
        """Frame i as an RGB PIL image, converting only that frame."""
        from PIL import Image

        arr = (images[i].detach().float().clamp(0, 1).cpu().numpy() * 255.0 + 0.5)
        arr = arr.astype(np.uint8)
        if arr.ndim == 3 and arr.shape[2] == 1:
            arr = arr[:, :, 0]
        return Image.fromarray(arr).convert("RGB")

    @staticmethod
    def _save_jpeg(img, path: str) -> str:
        """Write a JPEG atomically (temp file, then os.replace); return its name."""
        fd, tmp = tempfile.mkstemp(prefix=".tmp_", suffix=".jpg",
                                   dir=os.path.dirname(os.path.abspath(path)))
        try:
            with os.fdopen(fd, "wb") as fh:
                img.save(fh, format="JPEG", quality=JPEG_QUALITY)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise
        return os.path.basename(path)

    @classmethod
    def _write_thumb(cls, images, path: str) -> str:
        """The middle frame, scaled so its longest side is THUMB_LONG_SIDE."""
        from PIL import Image

        img = cls._frame(images, int(images.shape[0]) // 2)
        w, h = img.size
        k = THUMB_LONG_SIDE / max(w, h)
        img = img.resize((max(1, round(w * k)), max(1, round(h * k))), Image.LANCZOS)
        return cls._save_jpeg(img, path)

    @staticmethod
    def strip_indices(n: int) -> list[int]:
        """The frame at the centre of each of STRIP_FRAMES equal segments of an
        n-frame clip; every frame when there are fewer than STRIP_FRAMES."""
        k = min(STRIP_FRAMES, n)
        return [min(n - 1, int((i + 0.5) * n / k)) for i in range(k)]

    @classmethod
    def _write_strip(cls, images, path: str) -> str:
        """The strip_indices frames, STRIP_CELL_WIDTH wide each, left to right."""
        from PIL import Image

        cells = []
        for i in cls.strip_indices(int(images.shape[0])):
            img = cls._frame(images, i)
            w, h = img.size
            cells.append(img.resize(
                (STRIP_CELL_WIDTH, max(1, round(h * STRIP_CELL_WIDTH / w))), Image.LANCZOS))
        out = Image.new("RGB", (STRIP_CELL_WIDTH * len(cells), cells[0].size[1]))
        for j, cell in enumerate(cells):
            out.paste(cell, (j * STRIP_CELL_WIDTH, 0))
        return cls._save_jpeg(out, path)

    @staticmethod
    def _encode(images, mp4, fps, audio_path, existing_frames):
        from PIL import Image

        tmpdir = existing_frames
        cleanup = False
        if tmpdir is None:
            tmpdir = tempfile.mkdtemp(prefix="h3shot_")
            cleanup = True
            arr = (images.clamp(0, 1).cpu().numpy() * 255.0 + 0.5).astype(np.uint8)
            for i, frame in enumerate(arr):
                Image.fromarray(frame).save(os.path.join(tmpdir, f"f_{i:06d}.png"))
            pattern = os.path.join(tmpdir, "f_%06d.png")
        else:
            first = sorted(os.listdir(tmpdir))[0]
            pattern = os.path.join(tmpdir, re.sub(r"\d{6}", "%06d", first))

        n_frames = int(images.shape[0])
        cmd = ["ffmpeg", "-y", "-framerate", str(fps), "-i", pattern]
        if audio_path:
            cmd += ["-i", audio_path, "-c:a", "aac", "-b:a", "192k"]
        # -frames:v pins the exact count. Never use -shortest here: H3's audio
        # is often a few milliseconds shorter than length/fps, and -shortest
        # would silently drop the final frame -- which breaks conform, because
        # the edit assumes every shot is exactly `length` frames long.
        cmd += ["-frames:v", str(n_frames),
                "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", mp4]

        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=900)
            result = f"mp4 written ({'with audio' if audio_path else 'mute'})"
        except FileNotFoundError:
            result = "ffmpeg not found - mp4 skipped"
        except subprocess.CalledProcessError as exc:
            result = f"ffmpeg failed: {exc.stderr.decode('utf-8', 'replace')[-160:]}"
        except subprocess.TimeoutExpired:
            result = "ffmpeg timed out - mp4 skipped"
        finally:
            if cleanup:
                import shutil
                shutil.rmtree(tmpdir, ignore_errors=True)
        return result


# ---------------------------------------------------------------------------
# H3SaveRefTake
# ---------------------------------------------------------------------------

class H3SaveRefTake:
    """Write one reference-image take and close its sidecar (h3refs.py).

    `sidecar` is the take's `queued` record, as h3refs reserved it (absolute
    path). The image goes next to it, named by the sidecar's `image` field
    (else <sidecar stem>.png). The sidecar then gets status, finished, image,
    width, height and save_notes; every other field is left alone, and it is
    rewritten atomically, as H3SaveShot does. Then the `h3pipe.ref` event
    (docs/API.md) goes to open editors. Only the first image of a batch is
    kept. A failure is reported in the sidecar and the status string, never
    raised, so a queue of refs keeps going.
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"images": ("IMAGE",),
                             "sidecar": ("STRING", {"default": ""})}}

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("status",)
    FUNCTION = "save"
    OUTPUT_NODE = True
    CATEGORY = "H3/refs"

    def save(self, images, sidecar):
        path = os.path.abspath((sidecar or "").strip())
        if not (sidecar or "").strip() or not path.lower().endswith(".json"):
            status = f"no sidecar given (got {sidecar!r}): nothing saved"
            return {"ui": {"text": [status]}, "result": (status,)}
        notes: list[str] = []
        data = None
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, dict):
                notes.append("sidecar was not a JSON object; rewritten")
                data = None
        except FileNotFoundError:
            notes.append("sidecar was missing; created")
        except (OSError, ValueError) as exc:
            notes.append(f"sidecar unreadable ({exc.__class__.__name__}); rewritten")
        data = data or {}
        stem = os.path.splitext(os.path.basename(path))[0]
        name = os.path.basename(data.get("image") or f"{stem}.png")
        if os.path.splitext(name)[1].lower() != ".png":
            name = os.path.splitext(name)[0] + ".png"
        out = os.path.join(os.path.dirname(path), name)
        n = int(images.shape[0])
        if n > 1:
            notes.append(f"{n} images in the batch; kept the first")
        width = height = None
        try:
            img = H3SaveShot._frame(images, 0)
            width, height = img.size
            fd, tmp = tempfile.mkstemp(prefix=".tmp_", suffix=".png",
                                       dir=os.path.dirname(out))
            try:
                with os.fdopen(fd, "wb") as fh:
                    img.save(fh, format="PNG", compress_level=4)
                os.replace(tmp, out)
            except BaseException:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                raise
            status = "ok"
            notes.insert(0, f"{name} {width}x{height}")
        except Exception as exc:
            status = "failed"
            notes.insert(0, f"image not written: {exc}")
        data.update(status=status, finished=_now(), image=name if status == "ok" else None,
                    width=width, height=height, save_notes=f"{stem}: " + "; ".join(notes))
        try:
            _write_json_atomic(path, data)
        except Exception as exc:
            notes.append(f"sidecar update failed: {exc}")
        else:
            self._notify(data, status)
        text = f"{stem}: {status}; " + "; ".join(notes)
        return {"ui": {"text": [text]}, "result": (text,)}

    @staticmethod
    def _notify(data: dict, status: str) -> None:
        """The `h3pipe.ref` event of docs/API.md. Only inside ComfyUI; never
        affects saving."""
        try:
            from server import PromptServer
            server = getattr(PromptServer, "instance", None)
            if server is None:
                return
            server.send_sync("h3pipe.ref", {
                "ep": data.get("ep"), "ref": data.get("ref"), "view": data.get("view"),
                "take": data.get("take"), "status": status})
        except Exception:
            pass


# ---------------------------------------------------------------------------

NODE_CLASS_MAPPINGS = {
    "H3ShotListLoader": H3ShotListLoader,
    "H3ShotInfo": H3ShotInfo,
    "H3SaveShot": H3SaveShot,
    "H3SaveRefTake": H3SaveRefTake,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "H3ShotListLoader": "H3 Shot List Loader",
    "H3ShotInfo": "H3 Shot Info",
    "H3SaveShot": "H3 Save Shot",
    "H3SaveRefTake": "H3 Save Ref Take",
}
