import { describe, expect, it } from "vitest";
import { methodFor, thenSize, upscaleSize } from "../src/lib/upscale";

const h3 = { status: "ready" as const, missing: [], mode: "resample" as const, align: 32, fixed_scale: null };
const ltx = { status: "ready" as const, missing: [], mode: "second_stage" as const, align: 32, fixed_scale: 2 };
const take = (width: number, height: number) => ({ width, height });

describe("upscaleSize (Phase 13, the Upscale dialog)", () => {
  it("pixel: any scale on even sides, up to the limit", () => {
    expect(upscaleSize(take(1344, 768), "pixel", 4, null)).toEqual({ ok: true, w: 5376, h: 3072 });
    expect(upscaleSize(take(960, 544), "pixel", 1.5, null)).toEqual({ ok: true, w: 1440, h: 816 });
    expect(upscaleSize(take(448, 256), "pixel", 5, null).ok).toBe(false);
    expect(upscaleSize(take(448, 256), "pixel", 1, null).ok).toBe(false);
  });
  it("H3 re-sample: sides on the 32 grid", () => {
    expect(upscaleSize(take(1344, 768), "latent", 1.5, h3)).toEqual({ ok: true, w: 2016, h: 1152 });
    expect(upscaleSize(take(960, 544), "latent", 1.5, h3).ok).toBe(false);          // 816
    expect(upscaleSize(take(960, 544), "latent", 2, null).why).toMatch(/no re-sample/);
  });
  it("LTX re-sample: its fixed scale only", () => {
    expect(upscaleSize(take(960, 512), "latent", 2, ltx)).toEqual({ ok: true, w: 1920, h: 1024 });
    expect(upscaleSize(take(960, 512), "latent", 3, ltx).why).toMatch(/fixed at 2x/);
  });
  it("then pixel: from the re-sample's size", () => {
    expect(thenSize(upscaleSize(take(960, 544), "latent", 2, h3), 2)).toEqual({ ok: true, w: 3840, h: 2176 });
    expect(thenSize({ ok: false, why: "x" }, 2)).toEqual({ ok: false, why: "x" });
  });
  it("an unknown size is left to the server", () => {
    expect(upscaleSize({}, "pixel", 2, null).ok).toBe(true);
  });
  it("best for each take: a ready re-sample, else pixel", () => {
    expect(methodFor("auto", h3)).toBe("latent");
    expect(methodFor("auto", null)).toBe("pixel");
    expect(methodFor("auto", { ...h3, status: "not_ready" })).toBe("pixel");
    expect(methodFor("pixel", h3)).toBe("pixel");
  });
});
