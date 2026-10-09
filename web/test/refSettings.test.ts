// P4: a ref's cfg and sampler knobs as override fields, and a take's
// settings made the ref's ("Use these settings").
import { describe, expect, it } from "vitest";
import { knobFields, knobsOf, takeSettings } from "../src/lib/refSettings";
import type { RefTake } from "../src/types";

describe("ref sampler settings", () => {
  const none = knobsOf({});

  it("reads an override as text", () => {
    const k = knobsOf({ cfg: 3.5, params: { sampler: "er_sde", denoise: 0.8 } });
    expect(k).toMatchObject({ cfg: "3.5", sampler: "er_sde", denoise: "0.8", scheduler: "" });
  });

  it("sends only what changed, params whole", () => {
    expect(knobFields(none, none)).toEqual({});
    expect(knobFields({ ...none, cfg: "2" }, none)).toEqual({ cfg: 2 });
    expect(knobFields({ ...none, sampler: "er_sde", scheduler: "beta" }, none))
      .toEqual({ params: { sampler: "er_sde", scheduler: "beta" } });
    const set = knobsOf({ cfg: 2, params: { sampler: "euler" } });
    expect(knobFields({ ...set, cfg: "", sampler: "" }, set)).toEqual({ cfg: null, params: null });
  });

  it("refuses a knob that isn't a number", () => {
    expect(() => knobFields({ ...none, denoise: "high" }, none)).toThrow(/denoise/);
  });

  it("a take's settings become the override (not its prompt)", () => {
    const t = {
      take: 2, status: "ok", source: "generated", seed: "77", model: "", loras: null, steps: 25,
      cfg: 1, params: { sampler: "euler" }, width: 1344, height: 768, prompt: "x", note: "", image: null,
    } as unknown as RefTake;
    expect(takeSettings(t, false)).toEqual({
      seed: "77", model: null, loras: null, steps: 25, cfg: 1, params: { sampler: "euler" }, size: "1344x768",
    });
    expect(takeSettings(t, true)).not.toHaveProperty("size");     // a keyframe's size is its shot's
  });
});
