import { describe, expect, it } from "vitest";
import { STRIP_FRAMES, cellTime, stripCell, stripStyle } from "../src/lib/strip";

describe("stripCell", () => {
  it("maps the pointer's x to one of 8 equal cells", () => {
    const w = 160; // 20 px per cell
    expect(stripCell(0, w)).toBe(0);
    expect(stripCell(19.9, w)).toBe(0);
    expect(stripCell(20, w)).toBe(1);
    expect(stripCell(79, w)).toBe(3);
    expect(stripCell(80, w)).toBe(4);
    expect(stripCell(159.99, w)).toBe(7);
  });
  it("clamps outside the box and survives a zero width", () => {
    expect(stripCell(-5, 100)).toBe(0);
    expect(stripCell(100, 100)).toBe(STRIP_FRAMES - 1);
    expect(stripCell(1e6, 100)).toBe(7);
    expect(stripCell(10, 0)).toBe(0);
    expect(stripCell(Number.NaN, 100)).toBe(0);
  });
  it("works for other cell counts", () => {
    expect(stripCell(50, 100, 4)).toBe(2);
    expect(stripCell(50, 100, 1)).toBe(0);
  });
});

describe("stripStyle", () => {
  it("scales the strip to N box widths and positions cell i at i/(N-1)", () => {
    const s0 = stripStyle("u.jpg", 0);
    expect(s0.backgroundSize).toBe("800% 100%");
    expect(s0.backgroundPosition).toBe("0% 0%");
    expect(stripStyle("u.jpg", 7).backgroundPosition).toBe("100% 0%");
    expect(stripStyle("u.jpg", 1).backgroundPosition).toBe("14.286% 0%");
    expect(stripStyle("u.jpg", 1).backgroundImage).toBe('url("u.jpg")');
  });
  it("puts cell i's left edge at x = -i * box width", () => {
    // CSS: offset = p * (box - image), image = 8 * box
    const box = 100;
    for (let i = 0; i < 8; i++) {
      const p = parseFloat(stripStyle("u", i).backgroundPosition) / 100;
      expect(p * (box - 8 * box)).toBeCloseTo(-i * box, 1);
    }
  });
  it("clamps a bad cell", () => {
    expect(stripStyle("u", 99).backgroundPosition).toBe("100% 0%");
    expect(stripStyle("u", -3).backgroundPosition).toBe("0% 0%");
  });
});

describe("cellTime", () => {
  it("is the middle of the cell's slice", () => {
    expect(cellTime(0, 8)).toBe(0.5);
    expect(cellTime(7, 8)).toBe(7.5);
    expect(cellTime(3, 0)).toBe(0);
  });
});
