"""
Phase 13b (docs/PLAN.md): the nodes an upscale of a take runs through.

An upscale re-samples a take's latent at twice the size, from late in the
schedule, under the take's own frozen shotlist (prompt, references, seed), so
it adds detail without re-inventing the shot. h3upscale.py builds the graph
from the target's render graph; these are the pieces that graph doesn't have:

    H3LoadTakeLatent   the take's <stem>.latent.safetensors (Phase 13a) back as
                       the joint AV latent the sampler made
    H3LoadTakeVideo    the VAE route, for a take with no latent: its frames, and
                       the audio its lips were sampled against (H3's own mix,
                       <stem>_h3.wav, when there is one; the mp4's otherwise)
    H3HoldAudio        a noise mask that re-samples the picture and keeps the
                       audio stream exactly as it is, so the mouth is re-drawn
                       to the finished line
    H3SaveUpscale      <stem>.up.mp4: the picture, with the take's own audio
                       stream copied on unchanged, and <stem>.up.json closed
    H3PixelUpscale     the pixel method (any target): an upscale model
                       (RealESRGAN, UltraSharp, ...) over the frames a few at a
                       time, each batch resized straight to the target size

Paths are relative to `project_root` (the episode), like H3SaveShot's.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time

import numpy as np
import torch

try:
    from .h3_shotlist import H3SaveShot, _now, _write_json_atomic, load_audio, load_latent
except ImportError:                                      # imported on its own (tests)
    from h3_shotlist import H3SaveShot, _now, _write_json_atomic, load_audio, load_latent


def _abs(root: str, rel: str) -> str:
    return rel if os.path.isabs(rel) else os.path.join(os.path.normpath(root), rel)


# ---------------------------------------------------------------------------
# H3LoadTakeLatent
# ---------------------------------------------------------------------------

class H3LoadTakeLatent:
    """A take's kept latent (H3SaveShot's `latent`), as the sampler made it."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "project_root": ("STRING", {"default": "path/to/project/ep01"}),
            "latent_file": ("STRING", {"default": "renders/sh010/sh010_t01.latent.safetensors"}),
        }}

    RETURN_TYPES = ("LATENT",)
    FUNCTION = "load"
    CATEGORY = "H3/upscale"

    def load(self, project_root, latent_file):
        return (load_latent(_abs(project_root, latent_file)),)


# ---------------------------------------------------------------------------
# H3LoadTakeVideo
# ---------------------------------------------------------------------------

def _probe(path: str) -> tuple[int, int]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height", "-of", "csv=p=0:s=x", path],
        capture_output=True, text=True, check=True, timeout=60).stdout.strip()
    w, h = out.split("x")[:2]
    return int(w), int(h)


def read_frames(path: str) -> torch.Tensor:
    """Every frame of a video as a float IMAGE batch [T, H, W, 3] in 0..1."""
    w, h = _probe(path)
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-f", "rawvideo",
                          "-pix_fmt", "rgb24", "-"],
                         capture_output=True, check=True, timeout=900).stdout
    arr = np.frombuffer(raw, dtype=np.uint8).reshape(-1, h, w, 3)
    return torch.from_numpy(arr.astype(np.float32) / 255.0)


class H3LoadTakeVideo:
    """The VAE route's input: a take's frames and the audio its lips were made
    against. `audio_file` (H3's own mix) wins over the video's audio stream."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "project_root": ("STRING", {"default": "path/to/project/ep01"}),
            "video_file": ("STRING", {"default": "renders/sh010/sh010_t01.mp4"}),
            "audio_file": ("STRING", {"default": ""}),
        }}

    RETURN_TYPES = ("IMAGE", "AUDIO")
    RETURN_NAMES = ("images", "audio")
    FUNCTION = "load"
    CATEGORY = "H3/upscale"

    def load(self, project_root, video_file, audio_file=""):
        video = _abs(project_root, video_file)
        images = read_frames(video)
        src = _abs(project_root, audio_file) if audio_file else video
        try:
            audio = load_audio(src)
        except Exception:
            # a mute take (dub / clone, or a Wan target): silence of its length
            seconds = images.shape[0] / 24.0
            audio = {"waveform": torch.zeros(1, 1, max(1, int(44100 * seconds))),
                     "sample_rate": 44100}
        return (images, audio)


# ---------------------------------------------------------------------------
# H3PixelUpscale
# ---------------------------------------------------------------------------

class H3PixelUpscale:
    """Upscale frames with an upscale model (UpscaleModelLoader's), `chunk`
    frames at a time, resizing each batch to width x height (lanczos) as it
    goes: a 4x model over a hundred frames at once would need tens of GB, this
    holds one batch at the model's size and the rest at the target size."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "images": ("IMAGE",),
            "upscale_model": ("UPSCALE_MODEL",),
            "width": ("INT", {"default": 1920, "min": 16, "max": 16384, "step": 2}),
            "height": ("INT", {"default": 1088, "min": 16, "max": 16384, "step": 2}),
            "chunk": ("INT", {"default": 4, "min": 1, "max": 64}),
        }}

    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "upscale"
    CATEGORY = "H3/upscale"

    def upscale(self, images, upscale_model, width, height, chunk=4):
        # What core's ImageUpscaleWithModel does, but with the model loaded once
        # for the whole clip: calling that node per batch re-ran its load (and
        # logged "prepared for dynamic VRAM loading") for every batch.
        import comfy.model_management as mm
        import comfy.utils

        n, chunk = int(images.shape[0]), max(1, int(chunk))
        width, height = int(width), int(height)
        scale = max(float(getattr(upscale_model, "scale", 1.0)), 1.0)
        per_batch = images[:chunk].nelement() * images.element_size()
        mm.load_models_gpu([upscale_model.patcher],
                           memory_required=(512 * 512 * 3) * images.element_size() * scale * 384.0
                           + per_batch, force_full_load=True)
        device = upscale_model.patcher.load_device
        pbar = comfy.utils.ProgressBar(n)
        out = torch.empty((n, height, width, 3), dtype=torch.float16)
        tile, overlap = 512, 32
        for i in range(0, n, chunk):
            batch = images[i:i + chunk].movedim(-1, -3).to(device)
            while True:
                try:
                    big = comfy.utils.tiled_scale(batch, lambda a: upscale_model(a.float()),
                                                  tile_x=tile, tile_y=tile, overlap=overlap,
                                                  upscale_amount=upscale_model.scale,
                                                  output_device=mm.intermediate_device())
                    break
                except Exception as e:
                    mm.raise_non_oom(e)            # only out-of-memory gets a smaller tile
                    tile //= 2
                    if tile < 128:
                        raise
            big = big.clamp(0, 1)
            if big.shape[-2] != height or big.shape[-1] != width:
                big = comfy.utils.common_upscale(big, width, height, "lanczos", "disabled")
            out[i:i + big.shape[0]] = big.movedim(1, -1).to("cpu", torch.float16)
            pbar.update(big.shape[0])
        return (out,)


# ---------------------------------------------------------------------------
# H3HoldAudio
# ---------------------------------------------------------------------------

class H3HoldAudio:
    """Re-sample the picture, keep the audio: a per-stream noise mask of ones on
    the video and zeros on the audio, on a joint AV latent. The sampler then
    sees the audio clean at every step and the lips follow it."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"latent": ("LATENT",)}}

    RETURN_TYPES = ("LATENT",)
    FUNCTION = "hold"
    CATEGORY = "H3/upscale"

    def hold(self, latent):
        import comfy.nested_tensor

        samples = latent["samples"]
        if not getattr(samples, "is_nested", False):
            raise ValueError("H3HoldAudio needs a joint audio/video latent (H3's), "
                             "not a video-only one")
        video, audio = samples.unbind()[:2]
        out = dict(latent)
        out["noise_mask"] = comfy.nested_tensor.NestedTensor(
            (torch.ones_like(video), torch.zeros_like(audio)))
        return (out,)


# ---------------------------------------------------------------------------
# H3SaveUpscale
# ---------------------------------------------------------------------------

def has_audio(path: str) -> bool:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a",
                          "-show_entries", "stream=index", "-of", "csv=p=0", path],
                         capture_output=True, text=True, timeout=60).stdout
    return bool(out.strip())


def copy_audio(picture: str, source: str, out: str) -> str:
    """`out`: `picture`'s video with `source`'s audio stream copied on, bit for
    bit (no re-encode), or the picture alone when the source is mute. Returns
    "copied" or "none"."""
    if has_audio(source):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", picture, "-i", source,
                        "-map", "0:v:0", "-map", "1:a:0", "-c", "copy", out],
                       capture_output=True, check=True, timeout=300)
        return "copied"
    os.replace(picture, out)
    return "none"


def _notify(root: str, shot, take, status: str) -> None:
    """Tell open h3pipe editors an upscale is done (the `h3pipe.upscale` event
    of docs/API.md). Only inside ComfyUI; never affects saving."""
    try:
        from server import PromptServer
        server = getattr(PromptServer, "instance", None)
        if server is None or not shot:
            return
        server.send_sync("h3pipe.upscale", {"ep": os.path.abspath(root), "shot": shot,
                                            "take": take, "status": status})
    except Exception:
        pass


class H3SaveUpscale:
    """Write an upscale beside its take: <stem>.up.mp4, whose audio is the
    take's own stream copied on (what plays is the take's, not a VAE round
    trip), and close <stem>.up.json (status, finished, frames, width, height,
    mp4, audio, save_ms, save_notes; every other field is the queuer's)."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "images": ("IMAGE",),
            "project_root": ("STRING", {"default": "path/to/project/ep01"}),
            "source_mp4": ("STRING", {"default": "renders/sh010/sh010_t01.mp4"}),
            "out_mp4": ("STRING", {"default": "renders/sh010/sh010_t01.up.mp4"}),
            "fps": ("FLOAT", {"default": 24.0, "min": 1.0, "max": 60.0, "step": 1.0}),
            "sidecar": ("STRING", {"default": ""}),
        }}

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("status",)
    FUNCTION = "save"
    OUTPUT_NODE = True
    CATEGORY = "H3/upscale"

    def save(self, images, project_root, source_mp4, out_mp4, fps, sidecar=""):
        root = os.path.normpath(project_root)
        out = _abs(root, out_mp4)
        source = _abs(root, source_mp4)
        notes, ms, clock = [], {}, time.perf_counter
        audio, ok = None, False
        fd, picture = tempfile.mkstemp(prefix=".tmp_", suffix=".mp4",
                                       dir=os.path.dirname(os.path.abspath(out)))
        os.close(fd)
        try:
            t0 = clock()
            status = H3SaveShot._encode(images, picture, fps, None, None)
            ms["mp4"] = round((clock() - t0) * 1000)
            notes.append(status)
            if status.startswith("mp4 written"):
                t0 = clock()
                audio = copy_audio(picture, source, out)
                ms["audio"] = round((clock() - t0) * 1000)
                notes.append("take's audio copied" if audio == "copied" else "the take is mute")
                ok = os.path.isfile(out)
        except Exception as exc:
            notes.append(f"failed: {exc}")
        finally:
            if os.path.exists(picture):
                os.remove(picture)
        stem = os.path.basename(out)
        if sidecar:
            ms["total"] = sum(ms.values())
            path = _abs(root, sidecar)
            try:
                with open(path, encoding="utf-8") as fh:
                    data = json.load(fh)
            except (OSError, ValueError):
                data = {}
            data.update(status="ok" if ok else "failed", finished=_now(),
                        frames=int(images.shape[0]), width=int(images.shape[2]),
                        height=int(images.shape[1]), mp4=stem if ok else None,
                        audio=audio, save_ms=ms, save_notes=f"{stem}: " + "; ".join(notes))
            _write_json_atomic(path, data)
            _notify(root, data.get("shot"), data.get("take"), "ok" if ok else "failed")
        return (f"{stem}: " + "; ".join(notes),)


NODE_CLASS_MAPPINGS = {
    "H3LoadTakeLatent": H3LoadTakeLatent,
    "H3LoadTakeVideo": H3LoadTakeVideo,
    "H3HoldAudio": H3HoldAudio,
    "H3SaveUpscale": H3SaveUpscale,
    "H3PixelUpscale": H3PixelUpscale,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "H3LoadTakeLatent": "H3 Load Take Latent",
    "H3LoadTakeVideo": "H3 Load Take Video",
    "H3HoldAudio": "H3 Hold Audio",
    "H3SaveUpscale": "H3 Save Upscale",
    "H3PixelUpscale": "H3 Pixel Upscale",
}
