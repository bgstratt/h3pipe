// The mock's targets (docs/API.md "Targets (Phase 7)"): the H3 target the
// fixtures were rendered on, and an LTX-2 target to retarget shots to. LTX-2
// here binds its model through a different loader node and has no LoRA widget,
// so the dev page shows the pickers filtering by the chosen target.

import type { MissingFile, ModelFile, ModelList, Pass, Readiness, Resolution, TargetList } from "../types";

export const H3 = "minimax_h3_ref2va";
export const LTX = "ltx2";
export const LTX_REFS = "ltx2_ingredients";
export const WAN_I2V = "wan22_i2v";
export const WAN_VACE = "wan22_vace";
export const H3_FL2V = "minimax_h3_fl2va";
export const KREA2 = "krea2";
export const Z_IMAGE = "z_image_turbo";
export const FLUX2 = "flux2_klein";
export const FLUX2_EDIT = "flux2_klein_edit";
export const KONTEXT = "flux_kontext";
export const SDXL = "illustrious_sdxl";
/** Phase 9c-B: the audio target that generates voice samples. */
export const LTX_VOICE = "ltx2_voice";

export const MOCK_TARGETS: TargetList = {
  targets: [
    {
      id: H3, kind: "video", label: "MiniMax H3 Ref2VA", default: true, short: "H3",
      capabilities: { keyframes: [], policies: ["generate", "dub", "dub_keep_foley", "clone"], voice_reference: true, subject_refs: true, prompt: "sections" },
      presets: {
        final: {
          model: "minimax_h3_ref2va_pruned_int8_convrot.safetensors",
          lora: "minimax_h3_ref2v_lightx2v_turbo_4step_v0.1_resized_avg_rank_20_bf16.safetensors",
          steps: 8, width: 1344, height: 768,
        },
        proxy: {
          model: null,
          lora: "minimax_h3_ref2v_lightx2v_turbo_4step_v0.1_resized_avg_rank_20_bf16.safetensors",
          steps: 4, width: 448, height: 256,
        },
      },
      widgets: {
        model: { class_type: "UNETLoader", field: "unet_name" },
        loras: { class_type: "LoraLoaderModelOnly", name: "lora_name", strength: "strength_model", input: "model", chain: true },
        steps: { via: "loader" },
        seed: { via: "loader" },
      },
      workflow: "H3_Ref2VA_Shotlist_v1.json", loader: "H3ShotListLoader", saver: "H3SaveShot",
      template: { fps: 24, frames: { step: 17, base: 5, max: 3592 }, size_multiple: 32 },
      models: {
        model: { family: "minimax-h3-ref2va", label: "MiniMax H3 Ref2VA", patterns: ["*h3*ref2v*", "*ref2va*"], folder: "diffusion_models", tier: "required" },
        loras: { family: "minimax-h3-ref2v-turbo", label: "H3 turbo LoRA", patterns: ["*h3_ref2v*turbo*"], folder: "loras", tier: "accelerator" },
      },
    },
    {
      id: LTX, kind: "video", label: "LTX-2", short: "LTX-2",
      capabilities: { keyframes: ["first", "last"], policies: ["generate"], voice_reference: false, subject_refs: false, prompt: "prose", negative_prompt: true, duration: "predict" },
      presets: {
        final: { model: "ltx-2-19b-dev-fp8.safetensors", lora: null, steps: 20, width: 1280, height: 704 },
        proxy: { model: "ltx-2-19b-distilled-fp8.safetensors", lora: null, steps: 8, width: 640, height: 352 },
      },
      widgets: {
        model: { class_type: "CheckpointLoaderSimple", field: "ckpt_name" },
        steps: { via: "loader" },
        seed: { via: "loader" },
      },
      workflow: "LTX2_Shotlist_v1.json", loader: "H3ShotListLoader", saver: "H3SaveShot",
      template: { fps: 24, frames: { step: 8, base: 1, max: 257 }, size_multiple: 32 },
      models: {
        duration_head: { family: "ltx2.5-duration-head", label: "duration head", folder: "checkpoints", tier: "optional" },
      },
    },
    {
      id: LTX_REFS, kind: "video", label: "LTX-2.3 ingredients (character/plate refs)", short: "LTX+refs",
      capabilities: { keyframes: [], policies: ["generate"], voice_reference: false, subject_refs: true, prompt: "reference sheet + prose" },
      presets: {
        final: { model: "ltx-2.3-22b-distilled-fp8.safetensors", lora: "ltx-2.3-22b-ic-lora-ingredients-0.9.safetensors", steps: 8, width: 768, height: 448 },
        proxy: { model: "ltx-2.3-22b-distilled-fp8.safetensors", lora: "ltx-2.3-22b-ic-lora-ingredients-0.9.safetensors", steps: 8, width: 768, height: 448 },
      },
      widgets: { model: { via: "loader" }, steps: { via: "loader" }, seed: { via: "loader" } },
      workflow: "h3pipe_ltx2_3_ingredients.json", loader: "H3ShotListLoader", saver: "H3SaveShot",
      template: { fps: 24, frames: { step: 8, base: 49, max: 481 }, size_multiple: 32 },
      models: {
        model: { family: "ltx2.3", label: "LTX 2.3", patterns: ["ltx-2.3*"], folder: "checkpoints", tier: "required" },
        loras: { family: "ltx2.3-ic-lora-ingredients", label: "ingredients IC-LoRA", patterns: ["*ic-lora-ingredients*"], folder: "loras", tier: "required" },
        text_encoder: { family: "gemma3-12b", label: "Gemma 3 12B", patterns: ["gemma*3*12b*"], folder: "text_encoders", tier: "required" },
      },
    },
    {
      id: WAN_I2V, kind: "video", label: "Wan 2.2 14B I2V", short: "Wan I2V",
      capabilities: { keyframes: ["first", "last"], requires_first: true, policies: ["silent"], voice_reference: false, subject_refs: false, prompt: "prose", negative_prompt: true },
      presets: {
        final: { model: "wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors", model_low: "wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors", steps: 4, width: 832, height: 480, cfg: 1 },
        proxy: { model: "wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors", model_low: "wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors", steps: 4, width: 640, height: 352, cfg: 1 },
      },
      widgets: { model: { via: "loader" }, steps: { via: "loader" }, seed: { via: "loader" } },
      workflow: "h3pipe_wan22_i2v.json", loader: "H3ShotListLoader", saver: "H3SaveShot",
      template: { fps: 16, frames: { step: 4, base: 5, max: 161 }, size_multiple: 16 },
      models: {
        model: { family: "wan2.2-i2v-14b-high", label: "Wan 2.2 I2V 14B high-noise", patterns: ["*wan2.2*i2v*high*"], folder: "diffusion_models", tier: "required" },
        model_low: { family: "wan2.2-i2v-14b-low", label: "Wan 2.2 I2V 14B low-noise", patterns: ["*wan2.2*i2v*low*"], folder: "diffusion_models", tier: "required" },
        loras: { family: "wan2.2-i2v-lightx2v", label: "lightx2v 4-step turbo LoRA", folder: "loras", tier: "accelerator" },
      },
    },
    {
      id: WAN_VACE, kind: "video", label: "Wan 2.2 14B VACE (refs)", short: "Wan+refs",
      capabilities: { keyframes: ["first", "last"], policies: ["silent"], voice_reference: false, subject_refs: true, prompt: "prose", negative_prompt: true },
      presets: {
        final: { model: "wan2.2_fun_vace_high_noise_14B_fp8_scaled.safetensors", model_low: "wan2.2_fun_vace_low_noise_14B_fp8_scaled.safetensors", steps: 20, width: 832, height: 480, cfg: 3.5 },
        proxy: { model: "wan2.2_fun_vace_high_noise_14B_fp8_scaled.safetensors", model_low: "wan2.2_fun_vace_low_noise_14B_fp8_scaled.safetensors", steps: 10, width: 640, height: 352, cfg: 3.5 },
      },
      widgets: { model: { via: "loader" }, steps: { via: "loader" }, seed: { via: "loader" } },
      workflow: "h3pipe_wan22_vace.json", loader: "H3ShotListLoader", saver: "H3SaveShot",
      template: { fps: 16, frames: { step: 4, base: 5, max: 161 }, size_multiple: 16 },
      models: {
        model: { family: "wan2.2-vace-14b-high", label: "Wan 2.2 VACE 14B high-noise", folder: "diffusion_models", tier: "required" },
        model_low: { family: "wan2.2-vace-14b-low", label: "Wan 2.2 VACE 14B low-noise", patterns: ["*wan2.2*vace*low*"], folder: "diffusion_models", tier: "required" },
        loras: { family: "wan2.2-lightx2v", label: "lightx2v turbo LoRA", folder: "loras", tier: "accelerator" },
      },
    },
    {
      id: H3_FL2V, kind: "video", label: "H3 FL2VA (first/last frame)", short: "H3 FL2V",
      capabilities: { keyframes: ["first", "last"], policies: ["generate"], voice_reference: false, subject_refs: false, prompt: "fields" },
      presets: {
        final: { model: "minimax_h3_fl2va_int8.safetensors", steps: 8, width: 1344, height: 768 },
        proxy: { model: null, steps: 4, width: 448, height: 256 },
      },
      widgets: { model: { via: "loader" }, steps: { via: "loader" }, seed: { via: "loader" } },
      workflow: "h3pipe_h3_fl2va.json", loader: "H3ShotListLoader", saver: "H3SaveShot",
      template: { fps: 24, frames: { step: 17, base: 5, max: 3592 }, size_multiple: 32 },
      models: { model: { family: "minimax-h3-fl2va", label: "MiniMax H3 FL2VA", patterns: ["*h3*fl2v*"], folder: "diffusion_models", tier: "required" } },
    },
    // ---- image targets (Phase 8.5) ----
    {
      id: KREA2, kind: "image", label: "Krea 2", default: true,
      capabilities: { mode: "t2i", negative_prompt: true },
      presets: { final: { model: "krea2.safetensors", steps: 28, width: 1024, height: 1024 } },
      widgets: { model: { class_type: "UNETLoader", field: "unet_name" } },
      models: { model: { family: "flux2-krea", label: "Krea 2", patterns: ["krea2*"], folder: "diffusion_models", tier: "required" } },
    },
    {
      id: Z_IMAGE, kind: "image", label: "Z-Image Turbo",
      capabilities: { mode: "t2i", negative_prompt: false },
      presets: { final: { model: "z_image_turbo_bf16.safetensors", steps: 8, width: 1024, height: 1024, cfg: 1 } },
      widgets: { model: { via: "loader" } },
      models: { model: { family: "z-image-turbo", label: "Z-Image Turbo", patterns: ["z_image_turbo*"], folder: "diffusion_models", tier: "required" } },
    },
    {
      id: FLUX2, kind: "image", label: "Flux 2 Klein",
      capabilities: { mode: "t2i", negative_prompt: false },
      presets: { final: { model: "flux2_klein_9b_fp8.safetensors", steps: 4, width: 1024, height: 1024 } },
      widgets: { model: { via: "loader" } },
      models: { model: { family: "flux2-klein", label: "Flux 2 Klein", patterns: ["flux2_klein*"], folder: "diffusion_models", tier: "required" } },
    },
    {
      id: FLUX2_EDIT, kind: "image", label: "Flux 2 Klein edit",
      capabilities: { mode: "edit", max_refs: 4, negative_prompt: false },
      presets: { final: { model: "flux2_klein_9b_fp8.safetensors", steps: 4, width: 1024, height: 1024 } },
      widgets: { model: { via: "loader" } },
      models: { model: { family: "flux2-klein", label: "Flux 2 Klein", patterns: ["flux2_klein*"], folder: "diffusion_models", tier: "required" } },
    },
    {
      id: KONTEXT, kind: "image", label: "Flux Kontext",
      capabilities: { mode: "edit", max_refs: 1, negative_prompt: false },
      presets: { final: { model: "flux1-kontext-dev-fp8.safetensors", steps: 20, width: 1024, height: 1024 } },
      widgets: { model: { via: "loader" } },
      models: { model: { family: "flux1-kontext", label: "Flux Kontext", patterns: ["*kontext*"], folder: "diffusion_models", tier: "required" } },
    },
    {
      id: SDXL, kind: "image", label: "Illustrious (SDXL)",
      capabilities: { mode: "t2i", negative_prompt: true },
      presets: { final: { model: "illustriousXL_v01.safetensors", steps: 8, width: 1024, height: 1024, cfg: 2 } },
      widgets: { model: { via: "loader" } },
      models: {
        model: { family: "sdxl", label: "SDXL", patterns: ["*illustrious*"], folder: "checkpoints", tier: "required" },
        loras: { family: "sdxl-dmd2", label: "DMD2 4-step LoRA", patterns: ["*dmd2*"], folder: "loras", tier: "accelerator" },
      },
    },
    // ---- audio targets (Phase 9c-B) ----
    {
      id: LTX_VOICE, kind: "audio", label: "LTX-2.5 audio-only (voice sample)", short: "LTX-2 voice", default: true,
      // as built: reference_audio is FALSE on purpose (the ID-LoRA weights
      // aren't installed, so it can't copy a voice; the wording makes it)
      capabilities: { mode: "t2a", reference_audio: false, max_seconds: 20, negative_prompt: true },
      presets: {
        final: { model: "ltx-2.5-22b-distilled-fp8.safetensors", steps: 8, cfg: 1 },
        proxy: { model: "ltx-2.5-22b-distilled-fp8.safetensors", steps: 8, cfg: 1 },
      },
      widgets: { model: { class_type: "UNETLoader", field: "unet_name" }, steps: { via: "loader" }, seed: { via: "loader" } },
      workflow: "h3pipe_ltx2_voice.json", loader: "H3ShotListLoader", saver: "H3SaveRefAudio",
      template: { fps: 24, frames: { step: 8, base: 1, max: 481 } },
      models: {
        model: { family: "ltx2.5-distilled", label: "LTX-2.5 distilled", patterns: ["ltx-2.5*distilled*"], folder: "diffusion_models", tier: "accelerator" },
        text_encoder: { family: "gemma3-12b", label: "Gemma 3 12B", patterns: ["gemma*3*12b*"], folder: "text_encoders", tier: "required" },
        audio_vae: { family: "ltx2-audio-vae", label: "LTX-2 audio VAE", patterns: ["*audio*vae*"], folder: "vae", tier: "required" },
      },
    },
  ],
  default: { video: H3, image: "krea2", audio: LTX_VOICE },
};

/** ComfyUI's /object_info choices for the combo widgets the targets bind. */
export const MOCK_WIDGET_CHOICES: Record<string, string[]> = {
  "UNETLoader|unet_name": [
    "minimax_h3_ref2va_pruned_int8_convrot.safetensors",
    "minimax_h3_ref2va_bf16.safetensors",
    "wan2.2_t2v_14B_fp8.safetensors",
    "my_h3_merge_v2.safetensors",
    "krea2.safetensors",
  ],
  "LoraLoaderModelOnly|lora_name": [
    "minimax_h3_ref2v_lightx2v_turbo_4step_v0.1_resized_avg_rank_20_bf16.safetensors",
    "storybook_style_v2.safetensors",
    "film_grain_subtle.safetensors",
  ],
  "CheckpointLoaderSimple|ckpt_name": ["ltx-2-19b-dev-fp8.safetensors", "ltx-2-19b-distilled-fp8.safetensors"],
};

/**
 * What GET /h3pipe/models says about the mock's files: the names a family's
 * patterns match, the "headers" of a few others (a renamed H3 merge passes,
 * the Wan file is another family), the rest unknown.
 */
export const MOCK_MODEL_HEADERS: Record<string, { family: string; label: string; confidence: ModelFile["confidence"] }> = {
  "my_h3_merge_v2.safetensors": { family: "minimax-h3", label: "MiniMax H3", confidence: "tensors" },
  "wan2.2_t2v_14B_fp8.safetensors": { family: "wan2.2-t2v-14b", label: "Wan 2.2 T2V 14B", confidence: "tensors" },
  "wan2.2_fun_vace_low_noise_14B_fp8_scaled.safetensors": { family: "wan2.2-vace-14b-low", label: "Wan 2.2 VACE 14B low-noise", confidence: "tensors" },
  "wan2.2_t2v_low_noise_14B_fp8_scaled.safetensors": { family: "wan2.2-t2v-14b-low", label: "Wan 2.2 T2V 14B low-noise", confidence: "tensors" },
  "my_wan_low_merge.safetensors": { family: "wan2.2-i2v-14b-low", label: "Wan 2.2 I2V 14B low-noise", confidence: "tensors" },
};

/** A models folder's files, for params the targets read through their loader
 * (no combo widget to ask ComfyUI about): Wan's `model_low`. */
export const MOCK_FOLDER_FILES: Record<string, string[]> = {
  diffusion_models: [
    "wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors",
    "wan2.2_i2v_low_noise_14B_fp16.safetensors",
    "wan2.2_fun_vace_low_noise_14B_fp8_scaled.safetensors",
    "wan2.2_t2v_low_noise_14B_fp8_scaled.safetensors",
    "my_wan_low_merge.safetensors",
  ],
};

function glob(pattern: string, name: string): boolean {
  const re = new RegExp(`^${pattern.toLowerCase().replace(/[.+^${}()|[\]\\]/g, "\\$&").replace(/\*/g, ".*").replace(/\?/g, ".")}$`);
  return re.test(name.toLowerCase());
}

export function mockModelFiles(target: string, param: string): ModelList | null {
  const t = MOCK_TARGETS.targets.find((x) => x.id === target);
  const m = t?.models?.[param];
  const w = t?.widgets?.[param];
  if (!t || !m) return null;
  // a combo widget: ComfyUI's choices; a loader-read param (model_low): the folder's files
  let names: string[];
  if (w && "class_type" in w) names = MOCK_WIDGET_CHOICES[`${w.class_type}|${w.field ?? ""}`] ?? [];
  else if (param === "model_low" && m.folder) names = MOCK_FOLDER_FILES[m.folder] ?? [];
  else return null;
  const label = m.label ?? m.family;
  const files: ModelFile[] = names.map((name) => {
    if ((m.patterns ?? []).some((p) => glob(p, name))) {
      return { name, match: "name", mismatch: false, family: m.family, label, confidence: "name", detail: "" };
    }
    const h = MOCK_MODEL_HEADERS[name];
    if (h && m.family.startsWith(h.family)) {
      return { name, match: "fingerprint", mismatch: false, family: h.family, label: h.label, confidence: h.confidence, detail: `${param} ${name} isn't named like ${label}, but its header says ${h.label} (${h.confidence})` };
    }
    if (h) {
      return { name, match: "other", mismatch: true, family: h.family, label: h.label, confidence: h.confidence, detail: `${name.replace(/\.safetensors$/, "")} is ${h.label} (${h.confidence}), but this ${t.short ?? t.id} target's ${param} must be ${label}` };
    }
    return { name, match: "other", mismatch: false, family: null, label: "", confidence: "unknown", detail: `${param} ${name} isn't named like ${label} and its header matches no family h3pipe knows` };
  });
  const rank = { name: 0, fingerprint: 1, other: 2 };
  files.sort((a, b) => rank[a.match] - rank[b.match]);
  return { target, param, family: m.family, label, patterns: m.patterns ?? [], fingerprint: true, files };
}

export function preset(target: string, pass: Pass) {
  return MOCK_TARGETS.targets.find((t) => t.id === target)?.presets?.[pass];
}

/** A shot's length snapped to the target's frame grid (base + n*step, capped). */
export function targetLength(target: string, frames: number): number {
  const f = MOCK_TARGETS.targets.find((t) => t.id === target)?.template?.frames;
  if (!f?.step) return frames;
  const base = f.base ?? 1;
  const n = Math.max(0, Math.round((frames - base) / f.step));
  const len = base + n * f.step;
  return f.max ? Math.min(len, f.max - ((f.max - base) % f.step)) : len;
}

/**
 * What the LTX-2 target would write for a shot: plain prose, no `<Picture N>`
 * slots (it takes no reference pictures), from the H3 prompt's description.
 */
export function ltxPrompt(h3: string): string {
  const m = /detailed_description:\n([\s\S]*?)(?:\n\n|$)/.exec(h3);
  const body = (m ? m[1] : h3)
    .replace(/<\/?d>/g, "\"")
    .replace(/<(Subject|Picture) \d+>/g, "")
    .replace(/\[Shot \d+\]\s*/g, "")
    .replace(/\(S\d+\)/g, "")
    .replace(/[ \t]+/g, " ")
    .trim();
  return `${body}\n\nStyle: warm 2D storybook cartoon. Camera and sound as described.`;
}

// ---------------------------------------------------------------------------
// readiness (GET /h3pipe/targets?ready=1)
// ---------------------------------------------------------------------------

/**
 * What the mock's ComfyUI is missing, per target, before anything is
 * installed: H3 and Wan I2V are ready; LTX+refs lacks its IC-LoRA (required,
 * with a URL) and runs on a same-family checkpoint; LTX-2 lacks the optional
 * duration head (and its node); Wan VACE lacks its turbo LoRA (an
 * accelerator: the base preset applies). Mock data: the URL is illustrative.
 */
const MOCK_MISSING: Record<string, MissingFile[]> = {
  [LTX_REFS]: [{
    param: "loras", tier: "required", want: "ltx-2.3-22b-ic-lora-ingredients-0.9.safetensors",
    family: "ltx2.3-ic-lora-ingredients", folder: "loras",
    url: "https://huggingface.co/Lightricks/LTX-2.3-22b-IC-LoRA-Ingredients/resolve/main/ltx-2.3-22b-ic-lora-ingredients-0.9.safetensors",
    source: "ComfyUI template: LTX-2.3 IC-LoRA ingredients",
  }],
  [LTX]: [{
    param: "duration_head", tier: "optional", want: "ltx-2.5-duration-head-bf16.safetensors",
    family: "ltx2.5-duration-head", folder: "checkpoints", url: null,
    source: "search for ltx-2.5-duration-head-bf16.safetensors (Lightricks on Hugging Face)",
  }],
  [WAN_VACE]: [{
    param: "loras", tier: "accelerator", want: "wan2.2_vace_lightx2v_4steps_lora_high_noise.safetensors",
    family: "wan2.2-lightx2v", folder: "loras", url: null,
    source: "Wan 2.2 lightx2v 4-step LoRA for VACE (high noise)",
  }],
  // image targets: Kontext isn't installed; Illustrious lacks its DMD2 speed-up
  [KONTEXT]: [{
    param: "model", tier: "required", want: "flux1-kontext-dev-fp8.safetensors",
    family: "flux1-kontext", folder: "diffusion_models", url: null,
    source: "search for flux1-kontext-dev (Black Forest Labs on Hugging Face)",
  }],
  [SDXL]: [{
    param: "loras", tier: "accelerator", want: "dmd2_sdxl_4step_lora.safetensors",
    family: "sdxl-dmd2", folder: "loras", url: null, source: "DMD2 SDXL 4-step LoRA",
  }],
  // the audio target: voice cloning is off (the ID-LoRA weights aren't installed)
  [LTX_VOICE]: [{
    param: "id_lora", tier: "optional", want: "ltx-2.5-audio-id-lora.safetensors",
    family: "ltx2.5-audio-id-lora", folder: "loras", url: null,
    feature: "voice cloning (LTXVReferenceAudio)",
    source: "no such file is recorded in ComfyUI-Manager's model list on this machine",
  }],
};

const MOCK_RESOLVED: Record<string, Record<string, Resolution>> = {
  [H3]: {
    model: { want: "minimax_h3_ref2va_pruned_int8_convrot.safetensors", using: "minimax_h3_ref2va_pruned_int8_convrot.safetensors", how: "exact" },
    loras: { want: "minimax_h3_ref2v_lightx2v_turbo_4step_v0.1_resized_avg_rank_20_bf16.safetensors", using: "minimax_h3_ref2v_lightx2v_turbo_4step_v0.1_resized_avg_rank_20_bf16.safetensors", how: "exact" },
  },
  [LTX_REFS]: {
    model: { want: "ltx-2.3-22b-distilled-fp8.safetensors", using: "ltx-2.3-22b-distilled-int8-convrot.safetensors", how: "family" },
  },
  [WAN_I2V]: {
    model: { want: "wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors", using: "wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors", how: "exact" },
    model_low: { want: "wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors", using: "wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors", how: "exact" },
  },
};

const FEATURES: Record<string, string> = {
  duration_head: "dur: model (duration head)",
  id_lora: "voice cloning (LTXVReferenceAudio)",
};
const NODES: Record<string, string[]> = { duration_head: ["LTXVDurationPredictor"] };

/** Files "downloaded" since the mock started (the dev page's h3mockInstall). */
const installed = new Set<string>();

/** Pretend a file was downloaded: the next readiness refresh sees it. */
export function installMock(name: string) {
  installed.add(name);
}

export function resetMockInstalls() {
  installed.clear();
}

/** A target's readiness as GET /h3pipe/targets?ready=1 would give it now. */
export function mockReadiness(id: string): Readiness {
  const all = MOCK_MISSING[id] ?? [];
  const missing = all.filter((m) => !installed.has(m.want));
  const resolved: Record<string, Resolution> = { ...(MOCK_RESOLVED[id] ?? {}) };
  for (const m of all) if (installed.has(m.want)) resolved[m.param] = { want: m.want, using: m.want, how: "exact" };
  const status = missing.some((m) => m.tier === "required") ? "not_ready" : missing.length ? "degraded" : "ready";
  return {
    status,
    missing,
    resolved,
    features_off: missing.filter((m) => m.tier === "optional").map((m) => FEATURES[m.param] ?? m.param),
    nodes_missing: missing.flatMap((m) => NODES[m.param] ?? []),
  };
}

/** A take's `resolved`, as the queuer would record it on this target now. */
export function mockTakeResolved(id: string): Record<string, Resolution> {
  const r = mockReadiness(id);
  const out: Record<string, Resolution> = { ...r.resolved };
  for (const m of r.missing) {
    if (m.tier === "accelerator") out[m.param] = { want: m.want, using: null, how: "base" };
    if (m.tier === "optional") out[m.param] = { want: m.want, using: null, how: "off" };
  }
  return out;
}
