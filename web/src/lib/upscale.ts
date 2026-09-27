// Phase 13: what an upscale of a take would make, for the Upscale dialog. The
// server decides (h3upscale.plan_upscale); this mirrors its rules so the
// dialog can grey out a scale before anything is queued:
//   - pixel: any scale above 1 up to the limit whose sides land on even numbers
//   - a re-sample (resample mode, H3): the sides land on the target's `align` (32)
//   - a re-sample (second_stage mode, LTX-2): only the target's fixed scale
//   - then pixel: the re-sample's size times its scale, on even sides

import type { UpscaleOptions } from "../types";

export const SCALES = [1.5, 2, 3, 4];

export type LatentInfo = NonNullable<UpscaleOptions["latent"][string]>;

export interface SizeCheck {
  ok: boolean;
  why?: string;
  w?: number;
  h?: number;
}

function onGrid(w: number, h: number, s: number, align: number): SizeCheck {
  const sw = w * s, sh = h * s;
  if (!Number.isInteger(sw) || !Number.isInteger(sh) || sw % align || sh % align) {
    return { ok: false, why: `${w}×${h} at ${s}x isn't on a ${align}-pixel grid` };
  }
  return { ok: true, w: sw, h: sh };
}

/** The size `method` makes of a take at `scale` (ok: false with `why` when it can't). */
export function upscaleSize(
  take: { width?: number | null; height?: number | null } | undefined,
  method: "latent" | "pixel" | "seedvr2",
  scale: number,
  latent: LatentInfo | null,
  maxScale = 4,
): SizeCheck {
  const w = take?.width ?? 0, h = take?.height ?? 0;
  // no size on record (an older server): the server judges it
  if (!w || !h) return { ok: true };
  if (!(scale > 1 && scale <= maxScale)) return { ok: false, why: `scale is more than 1, up to ${maxScale}` };
  // SeedVR2 pads what it needs: any even size, as the pixel method
  if (method === "pixel" || method === "seedvr2") return onGrid(w, h, scale, 2);
  if (!latent) return { ok: false, why: "its target has no re-sample" };
  if (latent.mode === "second_stage" && latent.fixed_scale != null && scale !== latent.fixed_scale) {
    return { ok: false, why: `its re-sample is fixed at ${latent.fixed_scale}x` };
  }
  return onGrid(w, h, scale, latent.align ?? 32);
}

/** The then-pixel step's size from the re-sample's. */
export function thenSize(first: SizeCheck, scale: number, maxScale = 4): SizeCheck {
  if (!first.ok || !first.w || !first.h) return first;
  if (!(scale > 1 && scale <= maxScale)) return { ok: false, why: `scale is more than 1, up to ${maxScale}` };
  return onGrid(first.w, first.h, scale, 2);
}

/** "Best for each take": a re-sample where the take's target has one that's ready. */
export function methodFor(choice: "auto" | "latent" | "pixel" | "seedvr2", latent: LatentInfo | null): "latent" | "pixel" | "seedvr2" {
  if (choice !== "auto") return choice;
  return latent && latent.status === "ready" ? "latent" : "pixel";
}
