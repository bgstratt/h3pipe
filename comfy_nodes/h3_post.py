"""
h3_post.py — the post pass's nodes (docs/POST_PROCESSING.md).

Face tracks in and out of a file. The face detailer (ComfyUI-Temporal-Face-
Detailer) detects and tracks faces in one node and details them in another,
passing FACE_TRACKS, a plain dict, between them. Between the two, h3post decides
whose face each track is: these nodes write the tracks to a JSON file and read
back the ones a pass should detail (one character's, with that character's
reference and prompt).

FACE_TRACKS: {"width", "height", "num_frames", "tracks": [{"track_id",
"seed_offset", "frames": {frame_idx: {"bbox", "kps", "score", "crop",
"interpolated", ...}}}]}. JSON keys are strings; frame indices come back as ints.

The enhance step's joiner (H3FramesToBatch: a per-frame model like SUPIR runs
on RebatchImages' list, one small batch at a time, and this puts the clip back
together) and the motion blur (H3MotionBlur).
"""

from __future__ import annotations

import json
import os


def _abs(root: str, rel: str) -> str:
    return rel if os.path.isabs(rel) else os.path.join(os.path.normpath(root), rel)


def _ids(text: str) -> set[int] | None:
    """"1, 3" -> {1, 3}; "" -> None (every track)."""
    parts = [p.strip() for p in (text or "").replace(";", ",").split(",") if p.strip()]
    return {int(p) for p in parts} if parts else None


def tracks_from_json(doc: dict, keep: set[int] | None = None) -> dict:
    """FACE_TRACKS from its JSON form, only the tracks in `keep` (None: all)."""
    tracks = []
    for tr in doc.get("tracks", []):
        if keep is not None and int(tr["track_id"]) not in keep:
            continue
        tracks.append({**tr, "track_id": int(tr["track_id"]),
                       "frames": {int(k): v for k, v in tr["frames"].items()}})
    return {**doc, "tracks": tracks}


def _plain(v):
    """numpy scalars and arrays (a detector's boxes) as JSON values."""
    if hasattr(v, "tolist"):
        return v.tolist()
    if isinstance(v, dict):
        return {str(k): _plain(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    return v


class H3SaveFaceTracks:
    """Write FACE_TRACKS to a JSON file under the project."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "face_tracks": ("FACE_TRACKS",),
            "project_root": ("STRING", {"default": "path/to/project/ep01"}),
            "tracks_file": ("STRING", {"default": "renders/sh010/sh010_t01.faces.json"}),
        }}

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("status",)
    FUNCTION = "save"
    OUTPUT_NODE = True
    CATEGORY = "H3/post"

    def save(self, face_tracks, project_root, tracks_file):
        path = _abs(project_root, tracks_file)
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_plain(face_tracks), f)
        os.replace(tmp, path)
        return (f"{len(face_tracks.get('tracks', []))} track(s)",)


class H3LoadFaceTracks:
    """Read FACE_TRACKS from a JSON file: every track, or the ids listed."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "project_root": ("STRING", {"default": "path/to/project/ep01"}),
            "tracks_file": ("STRING", {"default": "renders/sh010/sh010_t01.faces.json"}),
            "track_ids": ("STRING", {"default": ""}),
        }}

    RETURN_TYPES = ("FACE_TRACKS",)
    RETURN_NAMES = ("face_tracks",)
    FUNCTION = "load"
    CATEGORY = "H3/post"

    def load(self, project_root, tracks_file, track_ids=""):
        with open(_abs(project_root, tracks_file), encoding="utf-8") as f:
            doc = json.load(f)
        return (tracks_from_json(doc, _ids(track_ids)),)


class H3FramesToBatch:
    """A list of image batches (RebatchImages' output, run through a per-frame
    model) back into one batch, in order."""

    INPUT_IS_LIST = True

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"images": ("IMAGE",)}}

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("images",)
    FUNCTION = "join"
    CATEGORY = "H3/post"

    def join(self, images):
        import torch
        parts = [b if b.dim() == 4 else b[None] for b in images]
        return (torch.cat([p.to(parts[0].dtype) for p in parts], dim=0),)


def _grid(h: int, w: int, device):
    """A (1, h, w, 2) sampling grid of pixel coordinates (x, y)."""
    import torch
    ys, xs = torch.meshgrid(torch.arange(h, device=device, dtype=torch.float32),
                            torch.arange(w, device=device, dtype=torch.float32), indexing="ij")
    return torch.stack((xs, ys), dim=-1)[None]


def _sample(img, pix, h: int, w: int):
    """img (n, c, h, w) sampled at pixel coordinates pix (n, h, w, 2)."""
    import torch.nn.functional as F
    norm = pix.clone()
    norm[..., 0] = norm[..., 0] * (2.0 / max(1, w - 1)) - 1.0
    norm[..., 1] = norm[..., 1] * (2.0 / max(1, h - 1)) - 1.0
    return F.grid_sample(img, norm, mode="bilinear", padding_mode="border", align_corners=True)


class H3MotionBlur:
    """Shutter blur along each pixel's motion: optical flow (RAFT, ComfyUI's
    OpticalFlowLoader) between neighbouring frames gives a velocity per pixel,
    and each pixel is averaged over `samples` points along it, centred on the
    frame, across `amount` of the frame interval (0.5: a 180-degree shutter).
    Flow is measured at `flow_width` and scaled up. Where forward and backward
    flow disagree (occlusion, flow errors) the blur fades out, motion under
    `min_motion` pixels (at flow_width) gets none (RAFT's jitter on still
    areas would otherwise soften them a little differently every frame), and no
    pixel is smeared further than `max_motion` of the frame width per frame. The frame
    count and size don't change; amount 0 passes the frames through."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "images": ("IMAGE",),
            "optical_flow": ("OPTICAL_FLOW",),
            "amount": ("FLOAT", {"default": 0.25, "min": 0.0, "max": 1.0, "step": 0.05}),
            "samples": ("INT", {"default": 9, "min": 3, "max": 32}),
            "flow_width": ("INT", {"default": 960, "min": 256, "max": 2048, "step": 8}),
            "max_motion": ("FLOAT", {"default": 0.08, "min": 0.005, "max": 0.5, "step": 0.005}),
            "chunk": ("INT", {"default": 8, "min": 1, "max": 64}),
        }, "optional": {
            "min_motion": ("FLOAT", {"default": 0.75, "min": 0.0, "max": 8.0, "step": 0.05}),
        }}

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("images",)
    FUNCTION = "blur"
    CATEGORY = "H3/post"

    def blur(self, images, optical_flow, amount=0.25, samples=9, flow_width=960,
             max_motion=0.08, chunk=8, min_motion=0.75):
        import torch
        import torch.nn.functional as F
        import comfy.model_management as mm
        n, h, w = int(images.shape[0]), int(images.shape[1]), int(images.shape[2])
        if amount <= 0 or n < 2:
            return (images,)
        device = mm.get_torch_device()
        mm.load_model_gpu(optical_flow)
        raft = optical_flow.model
        fw = max(64, min(int(flow_width), w) // 8 * 8)
        fh = max(64, int(round(h * fw / w / 8)) * 8)
        step = max(1, int(chunk))

        def small(i0, i1):
            x = images[i0:i1, :, :, :3].movedim(-1, 1).to(device, torch.float32)
            return F.interpolate(x, size=(fh, fw), mode="bilinear", align_corners=False) * 2 - 1

        # flow i -> i+1 for every i < n-1, at fh x fw, on the CPU between uses
        fwd = torch.empty((n - 1, 2, fh, fw), dtype=torch.float16)
        bwd = torch.empty((n - 1, 2, fh, fw), dtype=torch.float16)   # i+1 -> i
        with torch.no_grad():
            for i in range(0, n - 1, step):
                j = min(n - 1, i + step)
                a, b = small(i, j), small(i + 1, j + 1)
                fwd[i:j] = raft(a, b)[-1].to("cpu", torch.float16)
                bwd[i:j] = raft(b, a)[-1].to("cpu", torch.float16)

            out = torch.empty_like(images[..., :3])
            base_s = _grid(fh, fw, device)
            base = _grid(h, w, device)
            cap = float(max_motion) * fw
            offsets = torch.linspace(-amount / 2.0, amount / 2.0, int(samples), device=device)
            for i in range(0, n, step):
                j = min(n, i + step)
                vel = []
                for t in range(i, j):
                    parts = []
                    if t < n - 1:
                        f = fwd[t:t + 1].to(device, torch.float32)
                        # forward-backward check: f, then the next frame's flow back
                        back = _sample(bwd[t:t + 1].to(device, torch.float32),
                                       base_s + f.movedim(1, -1), fh, fw)
                        err = (f + back).norm(dim=1, keepdim=True)
                        conf = torch.clamp(1.0 - err / (1.0 + 0.2 * f.norm(dim=1, keepdim=True)), 0, 1)
                        parts.append(f * conf)
                    if t > 0:
                        parts.append(-bwd[t - 1:t].to(device, torch.float32))
                    v = sum(parts) / len(parts)
                    mag = v.norm(dim=1, keepdim=True)
                    if min_motion > 0:      # fade in from min_motion to twice it
                        v = v * torch.clamp((mag - min_motion) / min_motion, 0.0, 1.0)
                    v = v * torch.clamp(cap / (mag + 1e-6), max=1.0)
                    vel.append(v)
                v = torch.cat(vel, dim=0)
                v = F.interpolate(v, size=(h, w), mode="bilinear", align_corners=False)
                v = (v * torch.tensor([w / fw, h / fh], device=device).view(1, 2, 1, 1)).movedim(1, -1)
                img = images[i:j, :, :, :3].movedim(-1, 1).to(device, torch.float32)
                acc = torch.zeros_like(img)
                for s in offsets:
                    acc += _sample(img, base + v * s, h, w)
                out[i:j] = (acc / len(offsets)).movedim(1, -1).to(out.device, out.dtype)
        return (out,)


NODE_CLASS_MAPPINGS = {
    "H3SaveFaceTracks": H3SaveFaceTracks,
    "H3LoadFaceTracks": H3LoadFaceTracks,
    "H3FramesToBatch": H3FramesToBatch,
    "H3MotionBlur": H3MotionBlur,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "H3SaveFaceTracks": "H3 Save Face Tracks",
    "H3LoadFaceTracks": "H3 Load Face Tracks",
    "H3FramesToBatch": "H3 Frames To Batch",
    "H3MotionBlur": "H3 Motion Blur",
}
