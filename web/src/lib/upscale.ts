// Phase 13: what an upscale of a take would make, for the Upscale dialog. The
// server decides (h3upscale.plan_upscale); this mirrors its rules so the
// dialog can grey out a scale before anything is queued:
//   - pixel: any scale above 1 up to the limit whose sides land on even numbers
//   - a re-sample (resample mode, H3): the sides land on the target's `align` (32)
//   - a re-sample with a fixed upsampler (LTX: 2x): only the target's fixed scale
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
  if (latent.fixed_scale != null && scale !== latent.fixed_scale) {
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

/** Delivery sizes by name (the server's DELIVER); any "WxH" works too. */
export const DELIVERS = [
  { id: "1080p", w: 1920, h: 1080, label: "1080p (1920×1080)" },
  { id: "1440p", w: 2560, h: 1440, label: "1440p / 2K (2560×1440)" },
  { id: "4k", w: 3840, h: 2160, label: "4K UHD (3840×2160)" },
];

/** {w, h} of a delivery ("4k", "2048x1080"), null for none, "bad" when it isn't one. */
export function parseDeliver(v: string | null | undefined): { w: number; h: number } | null | "bad" {
  if (!v) return null;
  const p = DELIVERS.find((d) => d.id === v.toLowerCase());
  if (p) return { w: p.w, h: p.h };
  const m = /^\s*(\d+)\s*[x×]\s*(\d+)\s*$/i.exec(v);
  if (!m) return "bad";
  const w = Number(m[1]), h = Number(m[2]);
  return w % 2 || h % 2 || w < 64 || h < 64 || w > 8192 || h > 8192 ? "bad" : { w, h };
}

/** What w×h is scaled to, aspect kept, before it is cropped to fill W×H ("crop")
 * or padded to fit inside it ("pad"); even sides (h3upscale.fit_size). */
export function fitSize(w: number, h: number, W: number, H: number, fit: "crop" | "pad"): [number, number] {
  if (fit === "crop") {
    const s = Math.max(W / w, H / h);
    return [Math.max(W, 2 * Math.ceil((w * s) / 2 - 1e-9)), Math.max(H, 2 * Math.ceil((h * s) / 2 - 1e-9))];
  }
  const s = Math.min(W / w, H / h);
  return [Math.min(W, 2 * Math.floor((w * s) / 2 + 1e-9)), Math.min(H, 2 * Math.floor((h * s) / 2 + 1e-9))];
}

/** How a frame the last step makes (mw×mh) becomes W×H, in words. */
export function fitWords(mw: number, mh: number, W: number, H: number, fit: "crop" | "pad"): string {
  if (mw === W && mh === H) return "";
  const [iw, ih] = fitSize(mw, mh, W, H, fit);
  const resized = iw !== mw || ih !== mh ? "resized, " : "";
  if (fit === "crop") {
    const cut = iw > W ? `${iw - W} columns` : `${ih - H} rows`;
    return `${resized}${cut} cropped (${(100 * ((iw - W) / iw + (ih - H) / ih)).toFixed(1)}%)`;
  }
  const bars = iw < W ? `bars left and right (${(W - iw) / 2} px each)` : `bars top and bottom (${(H - ih) / 2} px each)`;
  return `${resized}${bars}`;
}

/** "Best for each take": a re-sample where the take's target has one that's ready. */
export function methodFor(choice: "auto" | "latent" | "pixel" | "seedvr2", latent: LatentInfo | null): "latent" | "pixel" | "seedvr2" {
  if (choice !== "auto") return choice;
  return latent && latent.status === "ready" ? "latent" : "pixel";
}
