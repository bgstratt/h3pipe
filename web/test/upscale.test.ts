import { describe, expect, it } from "vitest";
import { fitSize, fitWords, methodFor, parseDeliver, thenSize, upscaleSize } from "../src/lib/upscale";

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

describe("delivery sizes (the Output size menu)", () => {
  it("names and WxH", () => {
    expect(parseDeliver("4K")).toEqual({ w: 3840, h: 2160 });
    expect(parseDeliver("2048x1080")).toEqual({ w: 2048, h: 1080 });
    expect(parseDeliver(null)).toBeNull();
    expect(parseDeliver("1921x1080")).toBe("bad");
    expect(parseDeliver("big")).toBe("bad");
  });
  it("cover or fit, as the server's fit_size", () => {
    expect(fitSize(1344, 768, 3840, 2160, "crop")).toEqual([3840, 2196]);
    expect(fitSize(1344, 768, 3840, 2160, "pad")).toEqual([3780, 2160]);
    expect(fitSize(1024, 576, 3840, 2160, "crop")).toEqual([3840, 2160]);
    expect(fitWords(3840, 2196, 3840, 2160, "crop")).toBe("36 rows cropped (1.6%)");
    expect(fitWords(3780, 2160, 3840, 2160, "pad")).toBe("bars left and right (30 px each)");
    expect(fitWords(2688, 1536, 3840, 2160, "pad")).toBe("resized, bars left and right (30 px each)");
  });
});
