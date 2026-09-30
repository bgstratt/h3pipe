// A take's render size: typed, checked against the target's grid (H3 refuses a
// size off its 32 grid; LTX and Wan snap it), and the offered sizes that fit.
import { describe, expect, it } from "vitest";
import { parseSize, sizeCheck, sizeChoices, sizeRule } from "../src/lib/size";

const H3 = sizeRule({ size_multiple: 32 });
const LTX = sizeRule({ size_multiple: 64, size_fit: "snap" });

describe("render size", () => {
  it("parses WxH (x or ×, spaces) and refuses anything else", () => {
    expect(parseSize(" 1344 × 768 ")).toBe("1344x768");
    expect(parseSize("1600X896")).toBe("1600x896");
    expect(() => parseSize("big")).toThrow(/1344x768/);
    expect(() => parseSize("32x32")).toThrow(/64 to 8192/);
  });
  it("a size on the grid is fine; off a refusing grid names the nearest legal sides", () => {
    expect(sizeCheck("1600x896", H3)).toBeNull();
    expect(sizeCheck("", H3)).toBeNull();
    const bad = sizeCheck("1350x768", H3)!;
    expect(bad.level).toBe("err");
    expect(bad.text).toMatch(/multiple of 32/);
    expect(bad.text).toMatch(/1344 or 1376/);
    expect(bad.text).not.toMatch(/height/);
  });
  it("a snapping target says what the size becomes instead", () => {
    expect(sizeCheck("960x544", LTX)).toEqual({ level: "info", text: "snaps to 960x512 (a multiple of 64)" });
  });
  it("offers only the sizes that fit the grid as they are", () => {
    expect(sizeChoices(H3)).toEqual(["1344x768", "1024x576", "960x544"]);
    expect(sizeChoices(LTX)).toEqual(["1344x768", "1024x576"]);
    expect(sizeChoices(H3, "1600x896")).toContain("1600x896");
  });
});
