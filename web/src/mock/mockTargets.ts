// The mock's targets (docs/API.md "Targets (Phase 7)"): the H3 target the
// fixtures were rendered on, and an LTX-2 target to retarget shots to. LTX-2
// here binds its model through a different loader node and has no LoRA widget,
// so the dev page shows the pickers filtering by the chosen target.

import type { Pass, TargetList } from "../types";

export const H3 = "minimax_h3_ref2va";
export const LTX = "ltx2";

export const MOCK_TARGETS: TargetList = {
  targets: [
    {
      id: H3, kind: "video", label: "MiniMax H3 Ref2VA", default: true,
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
    },
    {
      id: LTX, kind: "video", label: "LTX-2",
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
    },
    {
      id: "krea2", kind: "image", label: "Krea 2",
      presets: { final: { model: "krea2.safetensors", steps: 28, width: 1024, height: 1024 } },
      widgets: { model: { class_type: "UNETLoader", field: "unet_name" } },
    },
  ],
  default: { video: H3, image: "krea2" },
};

/** ComfyUI's /object_info choices for the combo widgets the targets bind. */
export const MOCK_WIDGET_CHOICES: Record<string, string[]> = {
  "UNETLoader|unet_name": [
    "minimax_h3_ref2va_pruned_int8_convrot.safetensors",
    "minimax_h3_ref2va_bf16.safetensors",
    "wan2.2_t2v_14B_fp8.safetensors",
    "krea2.safetensors",
  ],
  "LoraLoaderModelOnly|lora_name": [
    "minimax_h3_ref2v_lightx2v_turbo_4step_v0.1_resized_avg_rank_20_bf16.safetensors",
    "storybook_style_v2.safetensors",
    "film_grain_subtle.safetensors",
  ],
  "CheckpointLoaderSimple|ckpt_name": ["ltx-2-19b-dev-fp8.safetensors", "ltx-2-19b-distilled-fp8.safetensors"],
};

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
