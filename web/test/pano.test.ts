// P5: aiming a camera into a 360 panorama.
import { describe, expect, it } from "vitest";
import { clampAim, renderView, sourcePoint, viewDetail } from "../src/lib/pano";

const PW = 360, PH = 180;
const centre = (aim: { yaw: number; pitch: number; fov: number }) => sourcePoint(aim, 100, 50, 49.5, 24.5, PW, PH);

describe("360 view", () => {
  it("looks at the panorama's centre straight ahead", () => {
    const [u, v] = centre({ yaw: 0, pitch: 0, fov: 70 });
    expect(u).toBeCloseTo(PW / 2 - 0.5, 0);
    expect(v).toBeCloseTo(PH / 2 - 0.5, 0);
  });

  it("turns right with yaw and up with pitch", () => {
    expect(centre({ yaw: 90, pitch: 0, fov: 70 })[0]).toBeCloseTo((PW * 3) / 4 - 0.5, 0);
    expect(centre({ yaw: -90, pitch: 0, fov: 70 })[0]).toBeCloseTo(PW / 4 - 0.5, 0);
    expect(centre({ yaw: 0, pitch: 45, fov: 70 })[1]).toBeCloseTo(PH / 4 - 0.5, 0);
  });

  it("keeps an aim legal", () => {
    expect(clampAim({ yaw: 190, pitch: 100, fov: 5 })).toEqual({ yaw: -170, pitch: 85, fov: 20 });
    expect(clampAim({ yaw: -540, pitch: 0, fov: 70 }).yaw).toBe(180 - 360);
  });

  it("draws a view with the panorama's colours, wrapping round", () => {
    // left half red, right half blue
    const src = { width: 8, height: 4, data: new Uint8ClampedArray(8 * 4 * 4) };
    for (let y = 0; y < 4; y++) for (let x = 0; x < 8; x++) {
      const i = (y * 8 + x) * 4;
      src.data[i] = x < 4 ? 255 : 0; src.data[i + 2] = x < 4 ? 0 : 255; src.data[i + 3] = 255;
    }
    const out = { width: 4, height: 2, data: new Uint8ClampedArray(4 * 2 * 4) };
    renderView(src, out, { yaw: -90, pitch: 0, fov: 30 });    // the middle of the left half
    expect(out.data[0]).toBe(255);
    expect(out.data[2]).toBe(0);
    expect(out.data[3]).toBe(255);
    renderView(src, out, { yaw: 90, pitch: 0, fov: 30 });
    expect(out.data[2]).toBe(255);
  });

  it("says how soft a cut is", () => {
    expect(viewDetail({ yaw: 0, pitch: 0, fov: 60 }, 1344, 1536)).toBeCloseTo(256 / 1344, 3);
  });
});
