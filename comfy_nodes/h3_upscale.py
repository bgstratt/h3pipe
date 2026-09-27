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

# ---- finishing a pixel upscale ------------------------------------------------
# Ideas from sajb0t/comfyui_ensemble_upscale (no licence, so nothing copied: the
# maths is the ordinary low/high band split), fitted to video: every
# measurement that could change from frame to frame is taken once per clip.

BAND_SIGMA = 1.5          # the band cutoff, in the source's pixels


def gaussian_blur(x, sigma: float):
    """A separable Gaussian blur of BCHW `x` (reflect padding, radius 3 sigma)."""
    import torch.nn.functional as F
    if sigma <= 0:
        return x
    r = max(1, int(-(-3.0 * sigma // 1)))
    t = torch.arange(-r, r + 1, device=x.device, dtype=torch.float32)
    k = torch.exp(-(t * t) / (2.0 * sigma * sigma))
    k = (k / k.sum()).to(x.dtype)
    c = x.shape[1]
    kx = k.view(1, 1, 1, -1).expand(c, 1, 1, -1)
    ky = k.view(1, 1, -1, 1).expand(c, 1, -1, 1)
    pr = min(r, x.shape[-1] - 1)
    pc = min(r, x.shape[-2] - 1)
    y = F.conv2d(F.pad(x, (pr, pr, 0, 0), mode="reflect"), kx[..., r - pr:r + pr + 1], groups=c)
    return F.conv2d(F.pad(y, (0, 0, pc, pc), mode="reflect"), ky[:, :, r - pc:r + pc + 1, :], groups=c)


def resize_to(x, height: int, width: int, lanczos):
    """BCHW `x` at height x width: a box average when it shrinks by a whole factor
    on both sides (a 4x model to 2x: no ringing), else `lanczos(x, w, h)`."""
    import torch.nn.functional as F
    h, w = x.shape[-2:]
    if (h, w) == (height, width):
        return x
    if h > height and h % height == 0 and w % width == 0 and h // height == w // width:
        return F.avg_pool2d(x, h // height)
    return lanczos(x, width, height)


def detail_map(src, sigma: float = BAND_SIGMA):
    """How much fine detail each pixel of BCHW `src` has (B1HW, unnormalised)."""
    high = (src - gaussian_blur(src, sigma)).abs().mean(dim=1, keepdim=True)
    return gaussian_blur(high, sigma)


def detail_scale(images, device, sigma: float = BAND_SIGMA) -> float:
    """One normalising level for the whole clip (IMAGE [T, H, W, C]): the 90th
    percentile of detail over about 8 frames. Per frame, it would flicker."""
    n = int(images.shape[0])
    pick = list(range(0, n, max(1, n // 8)))[:8]
    d = detail_map(images[pick].movedim(-1, 1).to(device).float(), sigma).flatten()
    d = d[:: max(1, d.numel() // (1 << 20))]
    return max(0.02, float(torch.quantile(d.float(), 0.9)))


def finish(src, up, *, frequency_split: bool = True, keep_soft: float = 0.0,
           level: float = 1.0, sigma: float = BAND_SIGMA):
    """The upscaled batch `up` (BCHW, target size) finished against its source
    frames `src` (BCHW): with `frequency_split`, colour and tone (the low band)
    from a bicubic enlarge of the source, only detail (the high band) from the
    model; with `keep_soft` (0-1), the model's detail faded where the source had
    none (bokeh, soft focus), `level` being the clip's detail_scale."""
    import torch.nn.functional as F
    if not frequency_split and keep_soft <= 0:
        return up
    h, w = up.shape[-2:]
    s = sigma * h / float(src.shape[-2])
    low_up = gaussian_blur(up, s)
    high = up - low_up
    if keep_soft > 0:
        d = (detail_map(src, sigma) / level).clamp(0, 1)
        d = F.interpolate(d, size=(h, w), mode="bilinear", align_corners=False).clamp(0, 1)
        high = high * (1.0 - float(keep_soft) * (1.0 - d))
    if frequency_split:
        base = F.interpolate(src, size=(h, w), mode="bicubic", align_corners=False)
        low = gaussian_blur(base, s)
    else:
        low = low_up
    return (low + high).clamp(0, 1)


def add_grain(x, amount: float, seed: int, first: int):
    """Monochrome Gaussian grain on BCHW `x`, a different pattern each frame
    (seeded by `seed` + the frame's index `first + b`, so a redo repeats it)."""
    if amount <= 0:
        return x
    out = x.clone()
    for b in range(x.shape[0]):
        g = torch.Generator().manual_seed(int(seed) * 100003 + first + b)
        noise = torch.randn((1, x.shape[-2], x.shape[-1]), generator=g)
        out[b] = (x[b] + float(amount) * noise.to(x.device, x.dtype)).clamp(0, 1)
    return out


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
            # fp16: the model under CUDA autocast (about twice as fast, what
            # RealESRGAN is normally run at); fp32: as core's node runs it
            "precision": (list(PRECISIONS), {"default": "fp16"}),
            # colour and tone from the source, detail from the model
            "frequency_split": ("BOOLEAN", {"default": True}),
            # 0-1: fade invented detail where the source was soft (bokeh)
            "keep_soft": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 1.0, "step": 0.05}),
            # film grain after, per frame; 0 is off
            "grain": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 0.2, "step": 0.005}),
            "grain_seed": ("INT", {"default": 0, "min": 0, "max": 0xFFFFFFFF}),
        }}

    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "upscale"
    CATEGORY = "H3/upscale"

    def upscale(self, images, upscale_model, width, height, chunk=4, precision="fp16",
                frequency_split=True, keep_soft=0.0, grain=0.0, grain_seed=0):
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
        half = precision == "fp16" and str(device).startswith("cuda")

        def model(a):
            if not half:
                return upscale_model(a.float())
            with torch.autocast("cuda", dtype=torch.float16):
                return upscale_model(a.float()).float()
        pbar = comfy.utils.ProgressBar(n)
        out = torch.empty((n, height, width, 3), dtype=torch.float16)
        level = detail_scale(images, device) if keep_soft > 0 else 1.0

        def lanczos(x, w, h):
            return comfy.utils.common_upscale(x, w, h, "lanczos", "disabled")
        tile, overlap = 512, 32
        for i in range(0, n, chunk):
            batch = images[i:i + chunk].movedim(-1, -3).to(device)
            while True:
                try:
                    big = comfy.utils.tiled_scale(batch, model,
                                                  tile_x=tile, tile_y=tile, overlap=overlap,
                                                  upscale_amount=upscale_model.scale,
                                                  output_device=mm.intermediate_device())
                    break
                except Exception as e:
                    mm.raise_non_oom(e)            # only out-of-memory gets a smaller tile
                    tile //= 2
                    if tile < 128:
                        raise
            big = resize_to(big.clamp(0, 1), height, width, lanczos)
            big = finish(batch.float().to(big.device), big.float(), frequency_split=frequency_split,
                         keep_soft=float(keep_soft), level=level)
            big = add_grain(big, float(grain), int(grain_seed), i)
            out[i:i + big.shape[0]] = big.movedim(1, -1).to("cpu", torch.float16)
            pbar.update(big.shape[0])
        return (out,)


class H3FinishUpscale:
    """The pixel method's finish (frequency split, keep soft, grain; see finish)
    for frames something else upscaled: SeedVR2's, say. `source` is the take's
    own frames, the same count, at any size."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "images": ("IMAGE",),
            "source": ("IMAGE",),
            "frequency_split": ("BOOLEAN", {"default": True}),
            "keep_soft": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 1.0, "step": 0.05}),
            "grain": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 0.2, "step": 0.005}),
            "grain_seed": ("INT", {"default": 0, "min": 0, "max": 0xFFFFFFFF}),
            "chunk": ("INT", {"default": 8, "min": 1, "max": 64}),
        }}

    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "finish"
    CATEGORY = "H3/upscale"

    def finish(self, images, source, frequency_split=True, keep_soft=0.0, grain=0.0,
               grain_seed=0, chunk=8):
        n = min(int(images.shape[0]), int(source.shape[0]))
        if not frequency_split and keep_soft <= 0 and grain <= 0:
            return (images,)
        device = "cuda" if torch.cuda.is_available() else "cpu"
        level = detail_scale(source, device) if keep_soft > 0 else 1.0
        h, w = int(images.shape[1]), int(images.shape[2])
        out = torch.empty((n, h, w, 3), dtype=torch.float16)
        for i in range(0, n, max(1, int(chunk))):
            up = images[i:i + chunk, :, :, :3].movedim(-1, 1).to(device).float()
            src = source[i:i + chunk, :, :, :3].movedim(-1, 1).to(device).float()
            x = finish(src, up, frequency_split=frequency_split, keep_soft=float(keep_soft),
                       level=level)
            x = add_grain(x, float(grain), int(grain_seed), i)
            out[i:i + x.shape[0]] = x.movedim(1, -1).to("cpu", torch.float16)
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


ENCODERS = ("auto", "nvenc", "x264")
PRECISIONS = ("fp16", "fp32")
_NVENC: set | None = None


def nvenc_encoders() -> set:
    """The NVENC encoders this ffmpeg has (h264_nvenc, hevc_nvenc, ...), asked once."""
    global _NVENC
    if _NVENC is None:
        try:
            out = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True,
                                 text=True, timeout=60).stdout
            _NVENC = {w for line in out.splitlines() for w in line.split() if w.endswith("_nvenc")}
        except Exception:
            _NVENC = set()
    return _NVENC


def x264_args(w: int, h: int) -> list:
    # a frame past 4K takes the faster preset: CRF 16 on "medium" is ~1 fps at 5376x3072
    return ["-c:v", "libx264", "-crf", "16", "-preset", "medium" if w * h <= 4096 * 2304 else "fast",
            "-pix_fmt", "yuv420p"]


def encoder_args(encoder: str, w: int, h: int) -> tuple[str, list]:
    """(the encoder used, its ffmpeg args). auto and nvenc use the GPU: H.264 up to
    4096 on a side (NVENC's H.264 limit), HEVC above; auto falls back to x264
    without NVENC, nvenc raises."""
    if encoder in ("auto", "nvenc"):
        have = nvenc_encoders()
        name = ("h264_nvenc" if w <= 4096 and h <= 4096 and "h264_nvenc" in have
                else "hevc_nvenc" if "hevc_nvenc" in have else None)
        if name:
            args = ["-c:v", name, "-preset", "p5", "-rc", "vbr", "-cq", "19", "-b:v", "0",
                    "-pix_fmt", "yuv420p"]
            return name, args + (["-tag:v", "hvc1"] if name == "hevc_nvenc" else [])
        if encoder == "nvenc":
            raise RuntimeError("this ffmpeg has no NVENC encoder (h264_nvenc / hevc_nvenc)")
    return "libx264", x264_args(w, h)


def encode_stream(images, path: str, fps: float, encoder: str = "auto") -> str:
    """Write `images` (IMAGE [T, H, W, 3]) to a mute mp4, one frame at a time into
    ffmpeg (no whole-clip byte copy: at 5376x3072 that was ~20 GB). Returns the
    encoder used; on auto, a failed NVENC encode is retried on x264."""
    n, h, w = int(images.shape[0]), int(images.shape[1]), int(images.shape[2])
    name, args = encoder_args(encoder, w, h)

    def run(args: list) -> None:
        cmd = ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
               "-s", f"{w}x{h}", "-framerate", str(fps), "-i", "-", "-frames:v", str(n),
               *args, path]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            for i in range(n):
                frame = (images[i].float().clamp(0, 1) * 255.0 + 0.5).to(torch.uint8)
                proc.stdin.write(frame.cpu().numpy().tobytes())
            proc.stdin.close()
            err = proc.stderr.read()
            if proc.wait(timeout=1800):
                raise RuntimeError(err.decode("utf-8", "replace")[-300:] or "ffmpeg failed")
        except BaseException:
            proc.kill()
            for fh in (proc.stdin, proc.stderr):
                try:
                    fh.close()
                except Exception:
                    pass
            proc.wait()
            raise

    try:
        run(args)
    except Exception:
        if encoder != "auto" or name == "libx264":
            raise
        name = "libx264"
        run(x264_args(w, h))
    return name


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
            # auto: NVENC when ffmpeg has it (H.264 up to 4096, HEVC above), else x264
            "encoder": (list(ENCODERS), {"default": "auto"}),
        }}

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("status",)
    FUNCTION = "save"
    OUTPUT_NODE = True
    CATEGORY = "H3/upscale"

    def save(self, images, project_root, source_mp4, out_mp4, fps, sidecar="", encoder="auto"):
        root = os.path.normpath(project_root)
        out = _abs(root, out_mp4)
        source = _abs(root, source_mp4)
        notes, ms, clock = [], {}, time.perf_counter
        audio, ok, used = None, False, None
        fd, picture = tempfile.mkstemp(prefix=".tmp_", suffix=".mp4",
                                       dir=os.path.dirname(os.path.abspath(out)))
        os.close(fd)
        try:
            t0 = clock()
            used = encode_stream(images, picture, float(fps), encoder)
            ms["mp4"] = round((clock() - t0) * 1000)
            notes.append(f"mp4 written ({used})")
            if os.path.isfile(picture):
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
                        audio=audio, encoder=used, save_ms=ms,
                        save_notes=f"{stem}: " + "; ".join(notes))
            _write_json_atomic(path, data)
            _notify(root, data.get("shot"), data.get("take"), "ok" if ok else "failed")
        return (f"{stem}: " + "; ".join(notes),)


NODE_CLASS_MAPPINGS = {
    "H3LoadTakeLatent": H3LoadTakeLatent,
    "H3LoadTakeVideo": H3LoadTakeVideo,
    "H3HoldAudio": H3HoldAudio,
    "H3SaveUpscale": H3SaveUpscale,
    "H3PixelUpscale": H3PixelUpscale,
    "H3FinishUpscale": H3FinishUpscale,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "H3LoadTakeLatent": "H3 Load Take Latent",
    "H3LoadTakeVideo": "H3 Load Take Video",
    "H3HoldAudio": "H3 Hold Audio",
    "H3SaveUpscale": "H3 Save Upscale",
    "H3PixelUpscale": "H3 Pixel Upscale",
    "H3FinishUpscale": "H3 Finish Upscale",
}
