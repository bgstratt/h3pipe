// Model families (GET /h3pipe/models): the picker's groups and warning, the
// render flag, the toast for a model-mismatch skip, the HTTP client's route
// and the mock's.
import { describe, expect, it } from "vitest";
import { baseRender, planRedo, renderReport } from "../src/actions";
import { createHttpApi, type Transport } from "../src/api";
import { isModelMismatch, MODEL_MISMATCH_LABEL, modelGroups, modelWarning } from "../src/lib/targets";
import { createMockApi } from "../src/mock/mockApi";
import type { ModelList, ShotDetail } from "../src/types";

const list: ModelList = {
  target: "ltx2", param: "model", family: "ltx2.5", label: "LTX 2.5", patterns: ["ltx-2.5*"], fingerprint: true,
  files: [
    { name: "ltx-2.5-distilled.safetensors", match: "name", mismatch: false, family: "ltx2.5", label: "LTX 2.5", confidence: "name", detail: "" },
    { name: "renamed.safetensors", match: "fingerprint", mismatch: false, family: "ltx2.5", label: "LTX 2.5", confidence: "metadata", detail: "model renamed isn't named like LTX 2.5, but its header says LTX 2.5 (metadata)" },
    { name: "wan_low.safetensors", match: "other", mismatch: true, family: "wan2.2-i2v-14b-low", label: "Wan 2.2 I2V 14B low-noise", confidence: "name", detail: "wan_low is Wan 2.2 I2V 14B low-noise (tensors + name), but this LTX-2 target's model must be LTX 2.5" },
    { name: "flux.safetensors", match: "other", mismatch: false, family: null, label: "", confidence: "unknown", detail: "model flux isn't named like LTX 2.5 and its header matches no family h3pipe knows" },
  ],
};

describe("model picker groups", () => {
  it("puts the family first and the rest under other", () => {
    const g = modelGroups(list)!;
    expect(g.matching.map((f) => f.name)).toEqual(["ltx-2.5-distilled.safetensors", "renamed.safetensors"]);
    expect(g.other.map((f) => f.name)).toEqual(["wan_low.safetensors", "flux.safetensors"]);
    expect(modelGroups(undefined)).toBeNull();
  });

  it("warns for other files; a mismatch names the render-anyway box", () => {
    expect(modelWarning(list, "ltx-2.5-distilled.safetensors")).toBeNull();
    expect(modelWarning(list, "renamed.safetensors")).toBeNull();
    expect(modelWarning(list, "")).toBeNull();
    expect(modelWarning(list, "not-listed.safetensors")).toBeNull();
    expect(modelWarning(list, "wan_low.safetensors")).toContain(MODEL_MISMATCH_LABEL);
    expect(modelWarning(list, "wan_low.safetensors")).toContain("must be LTX 2.5");
    expect(modelWarning(list, "flux.safetensors")).toMatch(/^Unverified: /);
    expect(isModelMismatch(list, "wan_low.safetensors")).toBe(true);
    expect(isModelMismatch(list, "flux.safetensors")).toBe(false);
    expect(isModelMismatch(undefined, "wan_low.safetensors")).toBe(false);
  });
});

describe("render anyway (model mismatch)", () => {
  it("sends allow_model_mismatch only when set", () => {
    expect(baseRender("ep", "proxy", ["sh010"])).not.toHaveProperty("allow_model_mismatch");
    expect(baseRender("ep", "proxy", ["sh010"], false, null, true)).toMatchObject({ allow_model_mismatch: true });
    const d = { target: "ltx2", built_target: "ltx2", effective: { prompt: "p", model: "m", loras: null, steps: 8 }, built_prompt: "p", override: {} } as unknown as ShotDetail;
    const plan = planRedo({ shot: "sh010", pass: "proxy", parent: null, seed: { mode: "new" }, model: "wan_low.safetensors", loras: null, steps: 8, prompt: "p", note: "", saveAsOverride: false, allowModelMismatch: true }, "ep", d);
    expect(plan.render).toMatchObject({ allow_model_mismatch: true, model: "wan_low.safetensors" });
  });

  it("toasts a model-mismatch skip with its reason", () => {
    const t = renderReport({
      queued: [], errors: [],
      skipped: [{ shot: "sh010", reason: "model mismatch: …", model_mismatch: [{ param: "model", file: "wan_low.safetensors", family: "ltx2.5", label: "LTX 2.5", message: "wan_low is Wan 2.2 I2V 14B low-noise, but this LTX-2 target's model must be LTX 2.5" }] }],
    });
    expect(t).toHaveLength(1);
    expect(t[0]).toMatchObject({ severity: "warn", summary: "Skipped 1 shot: model mismatch" });
    expect(t[0].detail).toContain("sh010: wan_low is Wan 2.2");
    expect(t[0].detail).toContain(MODEL_MISMATCH_LABEL);
  });
});

describe("HTTP client: /h3pipe/models", () => {
  it("GETs the route with target, param and ep, and fills in a thin answer", async () => {
    const calls: string[] = [];
    const t: Transport = {
      async fetch(path) {
        calls.push(path);
        return new Response(JSON.stringify({ family: "ltx2.5", files: [list.files[0]] }), { status: 200 });
      },
      url: (p) => p,
    };
    const r = await createHttpApi(t).modelFiles("ltx2", "model", "C:/Shows/ep01");
    expect(calls[0]).toBe("/h3pipe/models?target=ltx2&param=model&ep=C%3A%2FShows%2Fep01");
    expect(r).toMatchObject({ target: "ltx2", param: "model", family: "ltx2.5", label: "ltx2.5", patterns: [], fingerprint: false });
    expect(r.files).toHaveLength(1);
  });
});

describe("mock: model families", () => {
  it("groups the H3 picker and skips a mismatched render unless allowed", async () => {
    const api = createMockApi(() => {}, { latency: 0 });
    const m = await api.modelFiles("minimax_h3_ref2va", "model");
    expect(m.files.map((f) => [f.name, f.match])).toEqual([
      ["minimax_h3_ref2va_pruned_int8_convrot.safetensors", "name"],
      ["minimax_h3_ref2va_bf16.safetensors", "name"],
      ["my_h3_merge_v2.safetensors", "fingerprint"],
      ["wan2.2_t2v_14B_fp8.safetensors", "other"],
      ["krea2.safetensors", "other"],
    ]);
    await expect(api.modelFiles("ltx2", "model")).rejects.toThrow(/no model family/);
    const ep = (await api.episodes())[0].ep;
    const base = { ep, pass: "final" as const, shots: ["sh010"], redo: true, seed_mode: "new" as const, seed: null, loras: null, steps: null, prompt: null, parent_take: null, note: "", allow_missing_refs: true, model: "wan2.2_t2v_14B_fp8.safetensors" };
    const r = await api.render(base);
    expect(r.queued).toEqual([]);
    expect(r.skipped[0].model_mismatch?.[0]).toMatchObject({ param: "model", file: "wan2.2_t2v_14B_fp8.safetensors" });
    const r2 = await api.render({ ...base, allow_model_mismatch: true });
    expect(r2.queued).toHaveLength(1);
  });
});
