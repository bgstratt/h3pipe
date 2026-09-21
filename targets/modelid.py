"""
targets.modelid — which model family a weights file is, from its header.

A `.safetensors` file starts with an 8-byte little-endian header length and a
JSON header: every tensor's name, dtype, shape and offsets, plus an optional
`__metadata__` of strings. That is enough to tell the model families h3pipe
renders with apart without loading a single weight:

    read_header(path)            -> {"metadata": {...}, "tensors": {name: {"dtype", "shape"}}}
    identify(path, names=None)   -> {"family", "confidence", "detail", "base"?, "label"}
    name_matches(file, patterns) -> bool  (case-insensitive globs on the file name)
    relation(found, wanted)      -> "same" | "variant" | "ambiguous" | "different" | "unregistered"

`confidence` says how the family was found:

    metadata   the file says so (`model_version` on LTX files, the MiniMax H3
               VAEs' own config key, `modelspec.architecture`)
    tensors    the tensor names and a few key shapes match a signature
    name       only the file name (a family whose variants share every tensor:
               H3 Ref2VA vs FL2VA, Wan 2.2 high vs low noise, the LTX upscalers)
    unknown    no signature matches (or not a .safetensors file)

The signatures below were built from the headers of the files installed on the
dev machine (docs/PLAN.md "Model families"), nothing else. What a header can't
tell apart is said in the family's `detail` and settled by the name: `identify`
narrows a family to one of its variants when exactly one variant's name
patterns match the file (`names`: {family: [globs]}, from the targets, the
series config and the builtin hints here), and says so. Quantized conversions
(int8 "convrot", fp8 "scaled", nvfp4, "comfy" repacks) keep the tensor names;
shapes are only compared on dimensions packing leaves alone.

Results depend only on the file, so they are cached by (path, size, mtime) in a
JSON file (ModelIdCache): the routes keep it in ComfyUI's user folder, the CLI
in the temp folder. Stdlib only.
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import struct
import tempfile
import threading

MAX_HEADER = 256 * 1024 * 1024          # a header larger than this isn't safetensors
CACHE_VERSION = 1

# ---------------------------------------------------------------------------
# families
# ---------------------------------------------------------------------------

# id -> {"label", "parent"?, "names"?: builtin name hints for telling variants
# apart (used only among the children of a family the header identified)}
FAMILIES: dict[str, dict] = {
    # MiniMax H3
    "minimax-h3": {"label": "MiniMax H3"},
    "minimax-h3-ref2va": {"label": "MiniMax H3 Ref2VA", "parent": "minimax-h3",
                          "names": ["*ref2v*", "*ref_2v*"]},
    "minimax-h3-fl2va": {"label": "MiniMax H3 FL2VA", "parent": "minimax-h3",
                         "names": ["*fl2v*", "*fl_2v*"]},
    "minimax-h3-video-vae": {"label": "MiniMax H3 video VAE"},
    "minimax-h3-audio-vae": {"label": "MiniMax H3 audio VAE"},
    "qwen3vl-32b": {"label": "Qwen3-VL 32B (MiniMax H3's text encoder)"},
    # LTX-2 (the audio+video transformer; a checkpoint also carries its VAEs)
    "ltx2": {"label": "LTX-2"},
    "ltx2.3": {"label": "LTX 2.3", "parent": "ltx2", "names": ["*2.3*", "*2_3*", "*ltx23*"]},
    "ltx2.5": {"label": "LTX 2.5", "parent": "ltx2", "names": ["*2.5*", "*2_5*", "*ltx25*"]},
    "ltx2-video-vae": {"label": "LTX-2 video VAE"},
    "ltx2.3-video-vae": {"label": "LTX 2.3 video VAE", "parent": "ltx2-video-vae"},
    "ltx2.5-video-vae": {"label": "LTX 2.5 video VAE", "parent": "ltx2-video-vae"},
    "ltx2-audio-vae": {"label": "LTX-2 audio VAE"},
    "ltx2.3-audio-vae": {"label": "LTX 2.3 audio VAE", "parent": "ltx2-audio-vae",
                         "names": ["*2.3*", "*2_3*", "*ltx23*"]},
    "ltx2.5-audio-vae": {"label": "LTX 2.5 audio VAE", "parent": "ltx2-audio-vae",
                         "names": ["*2.5*", "*2_5*", "*ltx25*"]},
    "ltx2-latent-upscaler": {"label": "LTX-2 latent upscaler"},
    "ltx2.3-latent-upscaler": {"label": "LTX 2.3 latent upscaler",
                               "parent": "ltx2-latent-upscaler",
                               "names": ["*2.3*", "*2_3*", "*ltx23*"]},
    "ltx2.5-latent-upscaler": {"label": "LTX 2.5 latent upscaler",
                               "parent": "ltx2-latent-upscaler",
                               "names": ["*2.5*", "*2_5*", "*ltx25*"]},
    "ltx2-text-projection": {"label": "LTX-2 text projection"},
    "ltx2.3-text-projection": {"label": "LTX 2.3 text projection",
                               "parent": "ltx2-text-projection"},
    "ltx2.5-text-encoder": {"label": "Gemma 4 12B with the LTX 2.5 projection"},
    "gemma3-12b": {"label": "Gemma 3 12B (LTX 2.3's text encoder)"},
    "ltx2.5-duration-head": {"label": "LTX 2.5 duration head"},
    # Wan 2.2
    "wan2.2-i2v-14b": {"label": "Wan 2.2 I2V 14B"},
    "wan2.2-i2v-14b-high": {"label": "Wan 2.2 I2V 14B high-noise", "parent": "wan2.2-i2v-14b",
                            "names": ["*high*"]},
    "wan2.2-i2v-14b-low": {"label": "Wan 2.2 I2V 14B low-noise", "parent": "wan2.2-i2v-14b",
                           "names": ["*low*"]},
    "wan2.2-vace-14b": {"label": "Wan 2.2 Fun VACE 14B"},
    "wan2.2-vace-14b-high": {"label": "Wan 2.2 Fun VACE 14B high-noise",
                             "parent": "wan2.2-vace-14b", "names": ["*high*"]},
    "wan2.2-vace-14b-low": {"label": "Wan 2.2 Fun VACE 14B low-noise",
                            "parent": "wan2.2-vace-14b", "names": ["*low*"]},
    "wan2.2-ti2v-5b": {"label": "Wan 2.2 TI2V 5B"},
    "wan2.2-fun-inpaint-5b": {"label": "Wan 2.2 Fun Inpaint 5B"},
    "wan2.1-vae": {"label": "Wan 2.1 VAE"},
    "wan2.2-vae": {"label": "Wan 2.2 VAE"},
    "umt5-xxl": {"label": "UMT5-XXL (Wan's text encoder)"},
    # Krea 2
    "krea2": {"label": "Krea 2"},
    "qwen3vl-4b": {"label": "Qwen3-VL 4B (Krea 2's text encoder)"},
    # the image targets of Phase 8.5: Z-Image, FLUX.2 Klein, FLUX.1 Kontext
    "z-image": {"label": "Z-Image"},
    "z-image-turbo": {"label": "Z-Image Turbo", "parent": "z-image", "names": ["*turbo*"]},
    "qwen3-4b": {"label": "Qwen3 4B (Z-Image's text encoder)"},
    # Qwen-Image 2.1 (text to image and edit, targets/image/qwen_image_21)
    "qwen-image-2.1": {"label": "Qwen-Image 2.1"},
    "qwen-image-2.1-vae": {"label": "Qwen-Image 2.1 VAE"},
    "qwen3vl-8b": {"label": "Qwen3-VL 8B (Qwen-Image 2.1's text encoder)"},
    "flux2-klein-9b": {"label": "FLUX.2 Klein 9B"},
    "qwen3-8b": {"label": "Qwen3 8B (FLUX.2 Klein 9B's text encoder)"},
    "flux2-vae": {"label": "FLUX.2 VAE"},
    "flux1": {"label": "FLUX.1"},
    "flux1-dev": {"label": "FLUX.1 dev", "parent": "flux1"},
    "flux1-schnell": {"label": "FLUX.1 schnell", "parent": "flux1"},
    "flux1-kontext-dev": {"label": "FLUX.1 Kontext dev", "parent": "flux1-dev",
                          "names": ["*kontext*"]},
    "flux1-vae": {"label": "FLUX.1 VAE (ae)"},
    "clip-l": {"label": "CLIP-L"},
    "t5-xxl": {"label": "T5-XXL (FLUX.1's text encoder)"},
    # LoRAs and other add-ons: no header signature (a LoRA's tensors are the
    # layers it patches, not a model), so only their names identify them
    "minimax-h3-ref2v-turbo-lora": {"label": "MiniMax H3 Ref2V turbo LoRA"},
    "minimax-h3-fl2v-turbo-lora": {"label": "MiniMax H3 FL2V turbo LoRA"},
    "ltx2.3-ic-lora-ingredients": {"label": "LTX 2.3 ingredients IC-LoRA"},
    "ltx2.5-ic-lora-ingredients": {"label": "LTX 2.5 ingredients IC-LoRA"},
    "wan2.2-i2v-lightx2v-lora": {"label": "Wan 2.2 I2V lightx2v 4-step LoRA"},
}

# Signatures, first match wins (so a checkpoint, which carries VAEs too, is
# the transformer it holds). Keys are compared after stripping a leading
# "model.diffusion_model." or "diffusion_model."; a key ending in "." is a
# prefix. "all": every key present; "none": none of them; "shapes": {key: the
# leading dims} (None = any). "variants": narrower families the tensors CAN
# tell apart (first match). "version": metadata `model_version` ("2.3.0") ->
# a family id ("{v}" = "2.3"). "same": what the header can't tell apart (said
# in the detail; the name decides among the children).
SIGNATURES: list[dict] = [
    {"family": "minimax-h3",
     "all": ["adaln_t_table", "video_patch_proj.weight", "audio_patch_proj.weight",
             "condition_proj.weight", "token_refiner.final_norm.weight",
             "final_layer.video_out.weight", "blocks.0."],
     "same": "H3 Ref2VA and FL2VA have the same tensors (names, shapes and dtypes)"},
    {"family": "minimax-h3-video-vae", "meta_key": "minimax_h3_video_vae",
     "all": ["decoder.register_tokens", "decoder.mask_token", "quant_conv.weight",
             "latents_mean"]},
    {"family": "minimax-h3-audio-vae", "meta_key": "minimax_h3_audio_vae",
     "all": ["pre_block.proj.weight", "dec_in_proj.weight", "logs_proj.weight", "latents_mean"]},
    {"family": "qwen3vl-32b",
     "all": ["visual.deepstack_merger_list.0.", "visual.merger.linear_fc2.weight",
             "model.layers.0.self_attn.q_proj.weight"],
     "shapes": {"visual.merger.linear_fc2.weight": [5120],
                "model.layers.0.self_attn.q_proj.weight": [8192]}},
    {"family": "ltx2", "version": "ltx{v}",
     "all": ["patchify_proj.weight", "audio_patchify_proj.weight", "adaln_single.linear.weight",
             "av_ca_a2v_gate_adaln_single.linear.weight",
             "transformer_blocks.0.audio_ff.net.0.proj.weight"],
     "variants": [
         {"family": "ltx2.5", "all": ["keyframes_abs_pos_embedding"],
          "none": ["transformer_blocks.0.ff.net.0.proj.bias"]},
         {"family": "ltx2.3", "all": ["transformer_blocks.0.ff.net.0.proj.bias"],
          "none": ["keyframes_abs_pos_embedding"]}]},
    {"family": "ltx2-video-vae", "version": "ltx{v}-video-vae",
     "all": ["per_channel_statistics.mean-of-means", "encoder.conv_in.conv.weight",
             "encoder.down_blocks.0."],
     "variants": [
         {"family": "ltx2.5-video-vae", "all": ["decoder.det_stages.0.", "decoder.diff_blocks.0."]},
         {"family": "ltx2.3-video-vae", "all": ["decoder.up_blocks.0.", "decoder.conv_in.conv.weight"],
          "none": ["decoder.det_stages.0."]}]},
    {"family": "ltx2-audio-vae", "version": "ltx{v}-audio-vae",
     "all": ["audio_vae.encoder.conv_in.conv.weight", "audio_vae.decoder.conv_in.conv.weight",
             "vocoder."],
     "same": "the LTX 2.3 and 2.5 audio VAEs have the same tensors; their metadata tells them apart"},
    {"family": "ltx2-latent-upscaler",
     "all": ["initial_conv.weight", "initial_norm.weight", "res_blocks.0.",
             "post_upsample_res_blocks.0.", "upsampler.0.", "final_conv.weight"],
     "shapes": {"final_conv.weight": [128]},
     "same": "the LTX 2.3 and 2.5 latent upscalers have the same tensors and no metadata"},
    {"family": "ltx2.5-text-encoder",
     "all": ["text_embedding_projection.video_aggregate_embed.weight", "model.layers.0.",
             "model.embed_tokens.weight"],
     "shapes": {"model.embed_tokens.weight": [262144, 3840]}},
    {"family": "ltx2-text-projection", "version": "ltx{v}-text-projection",
     "all": ["text_embedding_projection.video_aggregate_embed.weight",
             "text_embedding_projection.audio_aggregate_embed.weight"],
     "none": ["model.layers.0."]},
    {"family": "gemma3-12b",
     "all": ["model.embed_tokens.weight", "model.layers.47.", "vision_model.embeddings.patch_embedding.weight",
             "multi_modal_projector.mm_input_projection_weight"],
     "none": ["model.layers.48."],
     "shapes": {"model.embed_tokens.weight": [262208, 3840]}},
    {"family": "wan2.2-vace-14b",
     "all": ["vace_patch_embedding.weight", "vace_blocks.0.", "patch_embedding.weight",
             "head.modulation", "blocks.39."],
     "none": ["blocks.40."],
     "shapes": {"patch_embedding.weight": [5120, 16]},
     "same": "the Wan 2.2 Fun VACE high- and low-noise experts have the same tensors"},
    {"family": "wan2.2-i2v-14b",
     "all": ["patch_embedding.weight", "head.modulation", "text_embedding.0.", "blocks.39."],
     "none": ["blocks.40.", "vace_patch_embedding.weight", "img_emb."],
     "shapes": {"patch_embedding.weight": [5120, 36]},
     "same": "the Wan 2.2 I2V high- and low-noise experts have the same tensors"},
    {"family": "wan2.2-ti2v-5b",
     "all": ["patch_embedding.weight", "head.modulation", "blocks.29."],
     "none": ["blocks.30.", "vace_patch_embedding.weight"],
     "shapes": {"patch_embedding.weight": [3072, 48]}},
    {"family": "wan2.2-fun-inpaint-5b",
     "all": ["patch_embedding.weight", "head.modulation", "blocks.29."],
     "none": ["blocks.30.", "vace_patch_embedding.weight"],
     "shapes": {"patch_embedding.weight": [3072, 100]}},
    {"family": "wan2.2-vae",
     "all": ["encoder.conv1.weight", "decoder.conv1.weight", "conv1.weight", "conv2.weight",
             "decoder.middle.0."],
     "shapes": {"conv2.weight": [48], "decoder.conv1.weight": [1024, 48]}},
    {"family": "wan2.1-vae",
     "all": ["encoder.conv1.weight", "decoder.conv1.weight", "conv1.weight", "conv2.weight",
             "decoder.middle.0."],
     "shapes": {"conv2.weight": [16], "decoder.conv1.weight": [384, 16]},
     "same": "Qwen-Image's VAE has the same tensors"},
    {"family": "umt5-xxl",
     "all": ["shared.weight", "encoder.block.23.", "encoder.final_layer_norm.weight"],
     "none": ["encoder.block.24.", "decoder.block.0."],
     "shapes": {"shared.weight": [256384, 4096]}},
    {"family": "krea2",
     "all": ["txtfusion.projector.weight", "txtfusion.refiner_blocks.0.", "first.weight",
             "last.linear.weight", "tmlp.0.weight", "tproj.1.weight", "blocks.0."],
     "shapes": {"first.weight": [6144]}},
    # Phase 8.5 image targets (headers of the files installed 2026-09-19)
    {"family": "z-image",
     "all": ["x_embedder.weight", "cap_embedder.1.weight", "noise_refiner.0.",
             "context_refiner.0.", "layers.29.", "t_embedder.mlp.0.weight"],
     "none": ["layers.30."],
     "shapes": {"x_embedder.weight": [3840, 64], "cap_embedder.1.weight": [3840, 2560]},
     "same": "Z-Image base and Turbo have the same tensors"},
    {"family": "flux2-klein-9b",
     "all": ["img_in.weight", "txt_in.weight", "double_stream_modulation_img.",
             "single_stream_modulation.", "double_blocks.7.", "single_blocks.23."],
     "none": ["double_blocks.8.", "single_blocks.24."],
     "shapes": {"img_in.weight": [4096, 128], "txt_in.weight": [4096, 12288]},
     "same": "FLUX.2 Klein 9B base, distilled and KV have the same tensors"},
    {"family": "flux1",
     "all": ["img_in.weight", "txt_in.weight", "vector_in.in_layer.weight",
             "double_blocks.18.", "single_blocks.37."],
     "none": ["double_blocks.19."],
     "shapes": {"img_in.weight": [3072, 64], "txt_in.weight": [3072, 4096]},
     "variants": [
         {"family": "flux1-dev", "all": ["guidance_in.in_layer.weight"]},
         {"family": "flux1-schnell", "none": ["guidance_in.in_layer.weight"]}]},
    {"family": "qwen3vl-4b",
     "all": ["model.embed_tokens.weight", "model.layers.35.", "model.visual.merger."],
     "none": ["model.layers.36."],
     "shapes": {"model.embed_tokens.weight": [151936, 2560]}},
    {"family": "qwen3-4b",
     "all": ["model.embed_tokens.weight", "model.layers.35.", "model.norm.weight"],
     "none": ["model.layers.36.", "model.visual."],
     "shapes": {"model.embed_tokens.weight": [151936, 2560]}},
    {"family": "qwen3-8b",
     "all": ["model.embed_tokens.weight", "model.layers.35.", "model.norm.weight"],
     "none": ["model.layers.36.", "model.visual."],
     "shapes": {"model.embed_tokens.weight": [151936, 4096]}},
    {"family": "clip-l",
     "all": ["text_model.embeddings.token_embedding.weight", "text_model.encoder.layers.11.",
             "text_model.final_layer_norm.weight"],
     "none": ["text_model.encoder.layers.12."],
     "shapes": {"text_model.embeddings.token_embedding.weight": [49408, 768]}},
    {"family": "t5-xxl",
     "all": ["shared.weight", "encoder.block.23.", "encoder.final_layer_norm.weight"],
     "none": ["encoder.block.24.", "decoder.block.0."],
     "shapes": {"shared.weight": [32128, 4096]}},
    {"family": "flux2-vae",
     # the full VAE is saved with diffusers names (decoder.mid_block...), the
     # small-decoder one with BFL's (decoder.mid.block_1...): only shared keys
     "all": ["bn.running_mean", "encoder.conv_in.weight", "decoder.conv_in.weight",
             "encoder.conv_out.weight"],
     "shapes": {"decoder.conv_in.weight": [None, 32], "encoder.conv_out.weight": [64]},
     "same": "the full FLUX.2 VAE and the small-decoder one share the encoder and latent"},
    {"family": "flux1-vae",
     "all": ["encoder.conv_in.weight", "decoder.conv_in.weight",
             "decoder.mid.block_1.norm1.weight"],
     "none": ["bn.running_mean", "quant_conv.weight"],
     "shapes": {"decoder.conv_in.weight": [512, 16], "encoder.conv_out.weight": [32]}},
]

_STRIP = ("model.diffusion_model.", "diffusion_model.")


def family_label(fid: str | None) -> str:
    if not fid:
        return ""
    return (FAMILIES.get(fid) or {}).get("label", fid)


def ancestors(fid: str) -> list[str]:
    """The parents of a registered family, nearest first."""
    out, seen = [], {fid}
    p = (FAMILIES.get(fid) or {}).get("parent")
    while p and p not in seen:
        out.append(p)
        seen.add(p)
        p = (FAMILIES.get(p) or {}).get("parent")
    return out


def children(fid: str) -> list[str]:
    return [k for k, v in FAMILIES.items() if v.get("parent") == fid]


def has_signature(fid: str) -> bool:
    """True if a file's header can say it is `fid` (the family, one of its
    parents, or a variant a signature tells apart). A family without one
    (a LoRA) is only ever known by its name, so reading headers to find one
    is pointless."""
    fams = {fid, *ancestors(fid)}
    for sig in SIGNATURES:
        if sig["family"] in fams or any(v["family"] in fams for v in sig.get("variants") or []):
            return True
    return False


def relation(found: str | None, wanted: str) -> str:
    """How an identified family `found` stands to the family a target wants:

    same          the same family
    variant       found is a variant of wanted (wanted is generic)
    ambiguous     found is a parent of wanted: the header can't tell the variant
    different     another family
    unregistered  this module doesn't know `wanted` (or found is None): no verdict
    """
    if not found or wanted not in FAMILIES or found not in FAMILIES:
        return "unregistered"
    if found == wanted:
        return "same"
    if wanted in ancestors(found):
        return "variant"
    if found in ancestors(wanted):
        return "ambiguous"
    return "different"


# ---------------------------------------------------------------------------
# names
# ---------------------------------------------------------------------------

def name_matches(filename: str, patterns) -> bool:
    """True if the file name (or its path as ComfyUI lists it, e.g.
    "sub\\x.safetensors") matches one of the case-insensitive globs."""
    if not filename:
        return False
    full = filename.replace("\\", "/").lower()
    base = full.rsplit("/", 1)[-1]
    for p in patterns or []:
        p = str(p).replace("\\", "/").lower()
        if fnmatch.fnmatchcase(base, p) or fnmatch.fnmatchcase(full, p):
            return True
    return False


def name_family(filename: str, names: dict | None) -> list[str]:
    """The families whose patterns (`names`: {family: [globs]}) match the file."""
    return [f for f, pats in (names or {}).items() if name_matches(filename, pats)]


def _variant_by_name(filename: str, base: str, names: dict | None) -> str | None:
    """The one child of `base` the file name points at (None: none or several)."""
    kids = children(base)
    if not kids:
        return None
    hits = []
    for k in kids:
        pats = list((FAMILIES[k].get("names") or [])) + list((names or {}).get(k) or [])
        if name_matches(filename, pats):
            hits.append(k)
    return hits[0] if len(hits) == 1 else None


# ---------------------------------------------------------------------------
# headers
# ---------------------------------------------------------------------------

def read_header(path: str) -> dict:
    """{"metadata": {...}, "tensors": {name: {"dtype", "shape"}}} of a
    .safetensors file, reading only its header. ValueError if it isn't one."""
    with open(path, "rb") as fh:
        head = fh.read(8)
        if len(head) < 8:
            raise ValueError(f"{os.path.basename(path)} is too short to be safetensors")
        n = struct.unpack("<Q", head)[0]
        if n <= 1 or n > MAX_HEADER:
            raise ValueError(f"{os.path.basename(path)} is not a safetensors file "
                             f"(header length {n})")
        raw = fh.read(n)
    if len(raw) < n:
        raise ValueError(f"{os.path.basename(path)}: truncated header")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise ValueError(f"{os.path.basename(path)}: unreadable safetensors header ({e})")
    if not isinstance(data, dict):
        raise ValueError(f"{os.path.basename(path)}: the header is not a JSON object")
    meta = data.pop("__metadata__", None)
    tensors = {k: {"dtype": v.get("dtype"), "shape": list(v.get("shape") or [])}
               for k, v in data.items() if isinstance(v, dict)}
    return {"metadata": dict(meta) if isinstance(meta, dict) else {}, "tensors": tensors}


def _normalize(tensors: dict) -> dict:
    out = {}
    for k, v in tensors.items():
        for s in _STRIP:
            if k.startswith(s):
                k = k[len(s):]
                break
        out.setdefault(k, v)
    return out


class _Keys:
    def __init__(self, tensors: dict):
        self.t = tensors
        self.sorted = sorted(tensors)

    def has(self, key: str) -> bool:
        if key.endswith("."):
            import bisect
            i = bisect.bisect_left(self.sorted, key)
            return i < len(self.sorted) and self.sorted[i].startswith(key)
        return key in self.t

    def shape_ok(self, key: str, dims) -> bool:
        v = self.t.get(key)
        if v is None:
            return False
        shape = v.get("shape") or []
        if len(shape) < len(dims):
            return False
        return all(d is None or shape[i] == d for i, d in enumerate(dims))


def _matches(sig: dict, keys: _Keys) -> bool:
    return (all(keys.has(k) for k in sig.get("all") or [])
            and not any(keys.has(k) for k in sig.get("none") or [])
            and all(keys.shape_ok(k, d) for k, d in (sig.get("shapes") or {}).items()))


def _version(meta: dict) -> str | None:
    """"2.3" from model_version "2.3.0" / "2.3.rc1" / "2.3"."""
    m = re.match(r"\s*(\d+)\.(\d+)", str(meta.get("model_version") or ""))
    return f"{m.group(1)}.{m.group(2)}" if m else None


def identify_header(header: dict) -> dict:
    """The family of a parsed header, name aside: {"family", "confidence",
    "detail", "same"?} ("same": what the header can't tell apart)."""
    meta = header.get("metadata") or {}
    keys = _Keys(_normalize(header.get("tensors") or {}))
    arch = str(meta.get("modelspec.architecture") or "").strip()
    for sig in SIGNATURES:
        if not _matches(sig, keys):
            continue
        fam, conf, why = sig["family"], "tensors", [f"tensors match {family_label(sig['family'])}"]
        for v in sig.get("variants") or []:
            if _matches(v, keys):
                fam = v["family"]
                why = [f"tensors match {family_label(fam)}"]
                break
        ver = _version(meta)
        if sig.get("version") and ver:
            by_meta = sig["version"].replace("{v}", ver)
            if by_meta in FAMILIES:
                if by_meta != fam and fam != sig["family"]:
                    why.append(f"but its metadata says model_version {meta.get('model_version')}")
                fam, conf = by_meta, "metadata"
                why.insert(0, f"metadata model_version {meta.get('model_version')}")
        if sig.get("meta_key") and sig["meta_key"] in meta:
            conf = "metadata"
            why.insert(0, f"metadata key {sig['meta_key']}")
        out = {"family": fam, "confidence": conf, "detail": "; ".join(why)}
        if fam == sig["family"] and sig.get("same"):
            out["same"] = sig["same"]
        return out
    if arch:
        for fid in FAMILIES:
            if arch.lower() in (fid.lower(), family_label(fid).lower()):
                return {"family": fid, "confidence": "metadata",
                        "detail": f"modelspec.architecture {arch}"}
        return {"family": None, "confidence": "unknown",
                "detail": f"modelspec.architecture {arch!r} is not a family h3pipe knows"}
    return {"family": None, "confidence": "unknown",
            "detail": f"no known signature ({len(keys.t)} tensors)"}


# ---------------------------------------------------------------------------
# the cache
# ---------------------------------------------------------------------------

class ModelIdCache:
    """identify_header results by (path, size, mtime), in a JSON file. Safe to
    share between threads; a file that can't be read or written just means no
    cache."""

    def __init__(self, path: str | None):
        self.path = path
        self.lock = threading.Lock()
        self.data: dict | None = None

    @staticmethod
    def key(path: str) -> str | None:
        try:
            st = os.stat(path)
        except OSError:
            return None
        return f"{os.path.normcase(os.path.abspath(path))}|{st.st_size}|{st.st_mtime_ns}"

    def _load(self) -> dict:
        if self.data is None:
            self.data = {}
            if self.path and os.path.isfile(self.path):
                try:
                    with open(self.path, encoding="utf-8") as fh:
                        raw = json.load(fh)
                    if isinstance(raw, dict) and raw.get("version") == CACHE_VERSION:
                        self.data = dict(raw.get("files") or {})
                except (OSError, ValueError):
                    self.data = {}
        return self.data

    def get(self, key: str) -> dict | None:
        with self.lock:
            v = self._load().get(key)
            return dict(v) if isinstance(v, dict) else None

    def put(self, key: str, value: dict) -> None:
        with self.lock:
            data = self._load()
            data[key] = dict(value)
            if not self.path:
                return
            try:
                os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
                tmp = f"{self.path}.{os.getpid()}.{threading.get_ident()}.tmp"
                with open(tmp, "w", encoding="utf-8") as fh:
                    json.dump({"version": CACHE_VERSION, "files": data}, fh, indent=1)
                os.replace(tmp, self.path)
            except OSError:
                pass


def temp_cache() -> ModelIdCache:
    """The CLI's cache: h3pipe_modelid_cache.json in the temp folder."""
    return ModelIdCache(os.path.join(tempfile.gettempdir(), "h3pipe_modelid_cache.json"))


def user_cache(user_dir: str) -> ModelIdCache:
    """The routes' cache: <ComfyUI user dir>/default/h3pipe/modelid_cache.json."""
    return ModelIdCache(os.path.join(user_dir, "default", "h3pipe", "modelid_cache.json"))


# ---------------------------------------------------------------------------
# identify
# ---------------------------------------------------------------------------

def identify_file(path: str, cache: ModelIdCache | None = None) -> dict:
    """identify_header of a file on disk, through the cache."""
    key = ModelIdCache.key(path) if cache is not None else None
    if key:
        hit = cache.get(key)
        if hit is not None:
            return hit
    if not os.path.isfile(path):
        return {"family": None, "confidence": "unknown", "detail": "file not found"}
    try:
        out = identify_header(read_header(path))
    except (OSError, ValueError) as e:
        out = {"family": None, "confidence": "unknown", "detail": str(e)[:300]}
    if key:
        cache.put(key, out)
    return out


def identify(path: str, names: dict | None = None, cache: ModelIdCache | None = None,
             filename: str | None = None) -> dict:
    """{"family", "confidence", "detail", "label", "base"?, "base_confidence"?}
    of a model file.

    The header decides (identify_header). When it can only name a family whose
    variants share every tensor (H3 Ref2VA / FL2VA, a Wan 2.2 expert), the
    variant comes from the name: exactly one child family's patterns (its
    builtin hints plus `names`, {family: [globs]}) must match `filename`
    (default: the path's base name); the confidence is then "name", and
    `base` / `base_confidence` keep what the header said.
    With no signature, a name that matches exactly one family's `names`
    patterns gives that family with confidence "name"."""
    fname = filename or os.path.basename(path)
    got = dict(identify_file(path, cache))
    fam = got.get("family")
    if fam:
        v = _variant_by_name(fname, fam, names)
        if v:
            # the header named the family, the name the variant
            got["base"], got["base_confidence"] = fam, got["confidence"]
            got["family"], got["confidence"] = v, "name"
            got["detail"] = (f"{got['detail']}; {got.pop('same', '') or 'the header stops there'}"
                             f": the name says {family_label(v)}")
        elif got.get("same"):
            got["detail"] = f"{got['detail']} ({got.pop('same')})"
    else:
        hits = name_family(fname, names)
        if len(hits) == 1:
            got = {"family": hits[0], "confidence": "name",
                   "detail": f"{got.get('detail')}; the name matches {family_label(hits[0])}"}
    got.pop("same", None)
    got["label"] = family_label(got.get("family"))
    return got
