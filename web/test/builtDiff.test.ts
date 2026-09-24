// P9: what the inspector says an override is deviating from. `built_values` comes
// from the same planner as `effective`, so only real differences show up here.
import { describe, expect, it } from "vitest";
import { builtDiff } from "../src/lib/overrideForm";
import type { Effective } from "../src/types";

const eff = (over: Partial<Effective> = {}): Effective => ({
  prompt: "a shot",
  seed: "42",
  seed_source: "stable",
  model: "h3.safetensors",
  loras: [{ name: "turbo.safetensors", strength: 1 }],
  steps: 4,
  target: "minimax_h3_ref2va",
  width: 448,
  height: 256,
  length: 73,
  negative: "",
  negative_source: "none",
  ...over,
}) as Effective;

describe("builtDiff", () => {
  it("says nothing when the two agree", () => {
    expect(builtDiff(eff(), eff())).toEqual([]);
  });

  it("says nothing without one side", () => {
    expect(builtDiff(eff(), undefined)).toEqual([]);
    expect(builtDiff(undefined, eff())).toEqual([]);
  });

  it("names the built value of each changed field", () => {
    const out = builtDiff(eff({ steps: 12 }), eff());
    expect(out).toEqual([{ field: "steps", built: "4" }]);
  });

  it("covers model, LoRAs, steps and seed in a fixed order", () => {
    const out = builtDiff(
      eff({ model: "other.safetensors", loras: [], steps: 20, seed: "999" }),
      eff(),
    );
    expect(out.map((x) => x.field)).toEqual(["model", "LoRAs", "steps", "seed"]);
    expect(out[1].built).toBe("turbo.safetensors@1");
  });

  it("reads an emptied LoRA list as none", () => {
    const out = builtDiff(eff({ loras: [{ name: "x.safetensors", strength: 0.8 }] }),
                          eff({ loras: [] }));
    expect(out).toEqual([{ field: "LoRAs", built: "none" }]);
  });

  it("compares a LoRA's strength, not only its name", () => {
    const out = builtDiff(eff({ loras: [{ name: "turbo.safetensors", strength: 0.5 }] }), eff());
    expect(out).toEqual([{ field: "LoRAs", built: "turbo.safetensors@1" }]);
  });

  it("ignores a built field the server left out", () => {
    expect(builtDiff(eff({ steps: 12 }), eff({ steps: null as never }))).toEqual([]);
    expect(builtDiff(eff({ model: "a" }), eff({ model: "" }))).toEqual([]);
  });

  it("treats a missing LoRA list as none on either side", () => {
    expect(builtDiff(eff({ loras: null }), eff({ loras: [] }))).toEqual([]);
  });
});
