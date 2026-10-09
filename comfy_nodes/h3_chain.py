"""
`continuous: latent` (docs/CONTINUOUS.md, phase b): a shot that carries straight
on from the previous take holds that take's last frames at its own head, in
latent space, and H3 generates the rest from there.

H3ChainLatent   between the empty joint AV latent (MiniMaxH3ReferenceToVideo's,
                `hold` frames longer than the shot: the loader adds them) and
                the sampler. Its first video latent steps (and audio steps, with
                `hold_audio`) become the previous take's last ones, masked out
                of the denoise (noise mask 0). The previous take is found when
                the graph RUNS, as the cut uses it then (h3refs.chain_source),
                so a whole chain can be queued at once: its kept
                <stem>.latent.safetensors, else its last frames and sound
                through the VAEs. The take's sidecar records which
                (`continued_from`).
H3ChainTrim     after decoding: the held frames and their sound off the front.

H3's grids (comfy_extras/nodes_minimax_h3.py; comfyui-obvpm-timeline frames.py):
a clip of 17k+5 frames is 5k+2 video latent steps, the steps covering
(1, 4, 4, 4, 4) frames; a hold of 17j+5 frames is the last 5j+2 steps of a
take, sliceable at phase 0. Audio is 40 latent steps a second, so only holds
whose length is a whole number of audio steps line up exactly: 39 frames is
65 steps, 22 frames is 36.7 (rounded). Prototype (2026-10-09, Porchlights ep02
sh520 -> sh530): 39 frames with the audio held carried the action on; 22
frames of video alone popped a subject in.
"""
from __future__ import annotations

import logging
import os

import torch

try:
    from .h3_shotlist import load_audio, load_latent
    from .h3_upscale import read_frames
except ImportError:                                      # imported on its own (tests)
    from h3_shotlist import load_audio, load_latent
    from h3_upscale import read_frames

log = logging.getLogger("h3pipe")

FPS = 24
AUDIO_STEPS_PER_SECOND = 40


def frames_to_latents(frames: int) -> int:
    """Video latent steps for a 17k+5-frame clip (core's video_latent_t)."""
    return max(1, (int(frames) - 5) // 17 * 5 + 2) if frames > 5 else 2


def audio_steps(frames: int, fps: float = FPS) -> int:
    """Audio latent steps covering `frames` video frames (rounded)."""
    return round(int(frames) / fps * AUDIO_STEPS_PER_SECOND)


def chain(latent: dict, v_old, a_old, overlap: int, hold_audio: bool,
          fps: float = FPS) -> tuple[dict, dict]:
    """`latent` (the new shot's empty joint AV latent) with the previous take's
    last `overlap` frames (its video latent `v_old`, audio latent `a_old` or
    None) written into its head and masked out of the denoise. Returns (the
    latent, a record of what was held)."""
    import comfy.nested_tensor

    new = latent["samples"]
    if not getattr(new, "is_nested", False):
        raise ValueError("H3ChainLatent needs H3's joint audio/video latent")
    v_new, a_new = [t.clone() for t in new.unbind()[:2]]
    if v_new.shape[1] != v_old.shape[1] or v_new.shape[3:] != v_old.shape[3:]:
        raise ValueError(f"the previous take's latent is {tuple(v_old.shape)}, this shot's "
                         f"{tuple(v_new.shape)}")
    steps = frames_to_latents(overlap)
    if steps >= v_new.shape[2] or steps > v_old.shape[2]:
        raise ValueError(f"a hold of {overlap} frames ({steps} latent steps) doesn't fit "
                         f"(this shot {v_new.shape[2]} steps, the previous {v_old.shape[2]})")
    v_new[:, :, :steps] = v_old[:, :, -steps:].to(v_new)
    v_mask = torch.ones_like(v_new)
    v_mask[:, :, :steps] = 0.0
    a_mask = torch.ones_like(a_new)
    held_audio = 0
    if hold_audio and a_old is not None:
        held_audio = min(audio_steps(overlap, fps), a_old.shape[-1], a_new.shape[-1] - 1)
        a_new[..., :held_audio] = a_old[..., -held_audio:].to(a_new)
        a_mask[..., :held_audio] = 0.0
    mask = latent.get("noise_mask")
    if getattr(mask, "is_nested", False):
        vm, am = mask.unbind()[:2]
        v_mask, a_mask = v_mask * vm.to(v_mask), a_mask * am.to(a_mask)
    out = dict(latent)
    out["samples"] = comfy.nested_tensor.NestedTensor((v_new, a_new))
    out["noise_mask"] = comfy.nested_tensor.NestedTensor((v_mask, a_mask))
    return out, {"overlap": int(overlap), "video_steps": steps, "audio_steps": held_audio}


def kept_latent(path: str):
    """(video, audio) of a take's kept joint AV latent."""
    lat = load_latent(path)["samples"]
    if not getattr(lat, "is_nested", False):
        raise ValueError(f"{os.path.basename(path)} isn't an H3 joint audio/video latent")
    v, a = lat.unbind()[:2]
    return v, a


def tail_audio(video: str, frames: int, total: int, fps: float):
    """The sound under a video's last `frames` of `total` (None: it has none)."""
    try:
        return load_audio(video, (total - frames) / fps, total / fps)
    except Exception:
        return None


def encoded_tail(video: str, frames: int, width: int, height: int, video_vae,
                 audio_vae, fps: float):
    """(video, audio or None) latents of a video's last `frames` frames and their
    sound, through the VAEs, the picture fitted to width x height."""
    import comfy.utils

    images = read_frames(video)
    total = int(images.shape[0])
    if total < frames:
        raise ValueError(f"{os.path.basename(video)} has {total} frames, fewer than the "
                         f"{frames} to hold")
    pixels = images[total - frames:]
    if pixels.shape[1] != height or pixels.shape[2] != width:
        pixels = comfy.utils.common_upscale(pixels.movedim(-1, 1), width, height, "lanczos",
                                            "center").movedim(1, -1)
    v = video_vae.encode(pixels[:, :, :, :3])
    a = None
    audio = tail_audio(video, frames, total, fps) if audio_vae is not None else None
    if audio is not None:
        import comfy.audio
        sr = audio["sample_rate"]
        want = getattr(audio_vae, "audio_sample_rate", 44100)
        wave = audio["waveform"] if sr == want else comfy.audio.resample(audio["waveform"],
                                                                          sr, want)
        a = audio_vae.encode(wave.movedim(1, -1))
    return v, a


def pixel_size(v_new, video_vae) -> tuple[int, int]:
    """(width, height) of the picture a video latent decodes to."""
    f = 32
    try:
        f = int(video_vae.spacial_compression_encode())
    except Exception:
        pass
    return int(v_new.shape[-1]) * f, int(v_new.shape[-2]) * f


class H3ChainLatent:
    """Hold the previous take's last frames at this shot's head (see the module)."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "latent": ("LATENT",),
            "project_root": ("STRING", {"default": "path/to/project/ep01"}),
            "shot": ("STRING", {"default": ""}),
            "pass_": ("STRING", {"default": "final"}),
            "overlap": ("INT", {"default": 39, "min": 5, "max": 360, "step": 17}),
            "hold_audio": ("BOOLEAN", {"default": True}),
        }, "optional": {
            "video_vae": ("VAE",),
            "audio_vae": ("VAE",),
            "sidecar": ("STRING", {"default": ""}),
            "fps": ("FLOAT", {"default": 24.0, "min": 1.0, "max": 120.0}),
            "previous_latent": ("STRING", {"default": "", "tooltip": "Hold this latent "
                                "instead of the previous shot's take in the cut"}),
        }}

    RETURN_TYPES = ("LATENT",)
    FUNCTION = "chain"
    CATEGORY = "H3/continuity"

    @classmethod
    def IS_CHANGED(cls, **_kw):
        return float("nan")                              # the previous take may have changed

    def chain(self, latent, project_root, shot, pass_, overlap, hold_audio, video_vae=None,
              audio_vae=None, sidecar="", fps=24.0, previous_latent=""):
        ep = os.path.normpath(project_root)
        overlap, fps = int(overlap), float(fps)
        src, R = None, None
        if previous_latent:
            lat = previous_latent if os.path.isabs(previous_latent) else \
                os.path.join(ep, previous_latent)
            video = None
        else:
            from . import h3pipe_api as API
            if API.IMPORT_ERROR:
                raise RuntimeError(API.IMPORT_ERROR)
            R = API.R
            src = R.chain_source(ep, shot, pass_)
            lat, video = src["latent"], src["video"]
        out, rec, via, why = None, None, None, ""
        if lat:
            try:
                v_old, a_old = kept_latent(lat)
                out, rec = chain(latent, v_old, a_old, overlap, hold_audio, fps)
                via = "latent"
            except Exception as e:                       # another size or model: its frames
                if not video:
                    raise
                why = f"its latent wasn't usable ({e}); "
        if out is None:
            if video_vae is None:
                raise ValueError(f"{shot}: the previous take has no kept latent, and no VAE is "
                                 f"wired to encode its frames")
            v_new = latent["samples"].unbind()[0]
            w, h = pixel_size(v_new, video_vae)
            v_old, a_old = encoded_tail(video, overlap, w, h, video_vae,
                                        audio_vae if hold_audio else None, fps)
            out, rec = chain(latent, v_old, a_old, overlap, hold_audio, fps)
            via = "frames"
        if R is not None and sidecar:
            R.chain_record(ep, sidecar, dict(src["from"], via=via, **rec))
        what = (f"{src['from']['shot']} {src['from']['pass']} t{src['from']['take']:02d}"
                if src else os.path.basename(lat))
        log.info("h3pipe: %s: held %d video steps%s of %s (%d frames, from its %s)%s", shot,
                 rec["video_steps"],
                 f" and {rec['audio_steps']} audio steps" if rec["audio_steps"] else "",
                 what, overlap, via, f"; {why}" if why else "")
        return (out,)


class H3ChainTrim:
    """The held frames (and the sound under them) off the front of a decoded
    `continuous: latent` render."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "images": ("IMAGE",),
            "frames": ("INT", {"default": 39, "min": 0, "max": 4096}),
            "fps": ("FLOAT", {"default": 24.0, "min": 1.0, "max": 120.0}),
        }, "optional": {
            "audio": ("AUDIO",),
        }}

    RETURN_TYPES = ("IMAGE", "AUDIO")
    RETURN_NAMES = ("images", "audio")
    FUNCTION = "trim"
    CATEGORY = "H3/continuity"

    def trim(self, images, frames, fps, audio=None):
        frames = int(frames)
        if frames >= images.shape[0]:
            raise ValueError(f"H3ChainTrim: {images.shape[0]} frames, {frames} to trim")
        if audio is not None:
            cut = round(frames / float(fps) * audio["sample_rate"])
            audio = dict(audio, waveform=audio["waveform"][..., cut:])
        return (images[frames:], audio)


NODE_CLASS_MAPPINGS = {"H3ChainLatent": H3ChainLatent, "H3ChainTrim": H3ChainTrim}
NODE_DISPLAY_NAME_MAPPINGS = {"H3ChainLatent": "H3 Chain Latent (continuous: latent)",
                              "H3ChainTrim": "H3 Chain Trim"}
