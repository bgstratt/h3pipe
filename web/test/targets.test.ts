// Phase 8 target picker logic: widget filtering, the retargeted label, the
// badge rules, run sizes, render warnings, redo planning with a target, the
// HTTP client's target routes and the mock's retargeting.
import { describe, expect, it, vi } from "vitest";
import { planRedo, type RedoPlan } from "../src/actions";
import { createHttpApi, type Transport } from "../src/api";
import {
  comboChoices, isRetargeted, pickerChoices, pickerSpec, renderWarnings, retargetNote, runSize, seriesDefaultTarget,
  shotTarget, shotTargetBadge, takeTargetBadge, targetBadges, targetShort, videoTargets,
} from "../src/lib/targets";
import { createMockApi } from "../src/mock/mockApi";
import { MOCK_TARGETS } from "../src/mock/mockTargets";
import type { RenderResult, ShotDetail, TargetList } from "../src/types";

const H3 = "minimax_h3_ref2va";
const LTX = "ltx2";
const list: TargetList = MOCK_TARGETS;

describe("target list", () => {
  it("lists video targets only, and finds the series default", () => {
    expect(videoTargets(list).map((t) => t.id)).toEqual([H3, LTX, "ltx2_ingredients", "wan22_i2v", "wan22_vace", "minimax_h3_fl2va"]);
    expect(seriesDefaultTarget(list)).toBe(H3);
    expect(seriesDefaultTarget(list, { target: LTX })).toBe(LTX);
    expect(seriesDefaultTarget(null)).toBe(H3);
    expect(seriesDefaultTarget({ targets: [{ id: "x", kind: "video", label: "X", default: true }], default: {} })).toBe("x");
  });

  it("makes short badge labels", () => {
    expect(targetShort(list, LTX)).toBe("LTX");
    expect(targetShort(list, H3)).toBe("MiniMax");
    expect(targetShort(null, "wan22_t2v")).toBe("WAN");
    expect(targetShort(list, null)).toBe("");
  });
});

describe("widget filtering", () => {
  const h3 = list.targets.find((t) => t.id === H3)!;
  const ltx = list.targets.find((t) => t.id === LTX)!;

  it("reads the binding: a node widget, the loader, or none", () => {
    expect(pickerSpec(h3)).toEqual({
      model: { kind: "node", class_type: "UNETLoader", field: "unet_name" },
      loras: { kind: "node", class_type: "LoraLoaderModelOnly", field: "lora_name" },
    });
    // LTX-2 has no LoRA widget: hide the LoRA rows
    expect(pickerSpec(ltx).loras).toEqual({ kind: "none" });
    expect(pickerSpec(ltx).model).toEqual({ kind: "node", class_type: "CheckpointLoaderSimple", field: "ckpt_name" });
    expect(pickerSpec({ ...h3, widgets: { model: { via: "loader" } } }).model).toEqual({ kind: "loader" });
    // no target info: the old, unfiltered pickers
    expect(pickerSpec(undefined)).toEqual({ model: { kind: "unknown" }, loras: { kind: "unknown" } });
    expect(pickerSpec({ ...h3, widgets: undefined }).loras).toEqual({ kind: "unknown" });
  });

  it("offers the widget's own choices, the generic list, or nothing", () => {
    const choices = { "CheckpointLoaderSimple|ckpt_name": ["ltx-a.safetensors", "ltx-b.safetensors"] };
    const s = pickerSpec(ltx);
    expect(pickerChoices(s.model, choices)).toEqual(["ltx-a.safetensors", "ltx-b.safetensors"]);
    expect(pickerChoices(s.loras, choices)).toBeNull();
    // node widget whose choices aren't loaded (or /object_info failed): generic
    expect(pickerChoices(pickerSpec(h3).model, choices)).toBeUndefined();
    expect(pickerChoices({ kind: "loader" }, choices)).toBeUndefined();
    expect(pickerChoices({ kind: "unknown" }, choices)).toBeUndefined();
  });

  it("parses /object_info combos, old and new forms", () => {
    const old = { UNETLoader: { input: { required: { unet_name: [["a.safetensors", "b.safetensors"]], weight_dtype: [["default"]] } } } };
    expect(comboChoices(old, "UNETLoader", "unet_name")).toEqual(["a.safetensors", "b.safetensors"]);
    const combo = { LoraLoaderModelOnly: { input: { required: { lora_name: ["COMBO", { options: ["x.safetensors"] }] } } } };
    expect(comboChoices(combo, "LoraLoaderModelOnly", "lora_name")).toEqual(["x.safetensors"]);
    const opt = { N: { input: { optional: { f: [["o"]] } } } };
    expect(comboChoices(opt, "N", "f")).toEqual(["o"]);
    expect(comboChoices(old, "UNETLoader", "missing")).toBeNull();
    expect(comboChoices({ N: { input: { required: { f: ["INT", {}] } } } }, "N", "f")).toBeNull();
    expect(comboChoices(null, "UNETLoader", "unet_name")).toBeNull();
    expect(comboChoices({}, "UNETLoader", "unet_name")).toBeNull();
  });
});

describe("the retargeted label", () => {
  it("names the built target when the shot moved", () => {
    const s = { target: LTX, built_target: H3 };
    expect(isRetargeted(s)).toBe(true);
    expect(retargetNote(list, s)).toBe("(retargeted from MiniMax H3 Ref2VA)");
    expect(shotTarget(s, H3)).toBe(LTX);
  });
  it("says nothing on the built target, or when the server doesn't say", () => {
    expect(retargetNote(list, { target: H3, built_target: H3 })).toBeNull();
    expect(retargetNote(list, { target: LTX })).toBeNull(); // a Phase 7 server: no built_target
    expect(retargetNote(list, null)).toBeNull();
    expect(shotTarget({ built_target: LTX }, H3)).toBe(LTX);
    expect(shotTarget({}, H3)).toBe(H3);
  });
  it("falls back to the id for an unknown target", () => {
    expect(retargetNote(list, { target: LTX, built_target: "old_model" })).toBe("(retargeted from old_model)");
  });
});

describe("badge rules", () => {
  it("badges a shot whose target isn't the series default", () => {
    expect(shotTargetBadge({ target: H3, built_target: H3 }, list, H3)).toBeNull();
    const b = shotTargetBadge({ target: LTX, built_target: H3 }, list, H3)!;
    expect(b).toMatchObject({ kind: "target", label: "LTX" });
    expect(b.title).toMatch(/retargeted from MiniMax/);
    // built for LTX by the script (not retargeted) still differs from the series default
    expect(shotTargetBadge({ target: LTX, built_target: LTX }, list, H3)?.title).not.toMatch(/retargeted/);
    // a series whose default is LTX: an LTX shot has no badge, an H3 one does
    expect(shotTargetBadge({ target: LTX, built_target: LTX }, list, LTX)).toBeNull();
    expect(shotTargetBadge({ target: H3, built_target: H3 }, list, LTX)?.label).toBe("MiniMax");
    // an older server: no target fields at all
    expect(shotTargetBadge({}, list, H3)).toBeNull();
  });

  it("badges a take rendered on another target than the shot's current one", () => {
    expect(takeTargetBadge({ target: H3 }, H3, list)).toBeNull();
    expect(takeTargetBadge({ target: null }, LTX, list)).toBeNull(); // before sidecars
    expect(takeTargetBadge({}, LTX, list)).toBeNull();
    expect(takeTargetBadge({ target: H3 }, LTX, list)).toMatchObject({ kind: "target", label: "MiniMax" });
    expect(takeTargetBadge({ target: LTX }, H3, list)?.title).toMatch(/Rendered on LTX-2; the shot now renders on MiniMax/);
  });

  it("combines shot and shown-take badges for a row or clip", () => {
    const retargeted = { target: LTX, built_target: H3 };
    expect(targetBadges(retargeted, list, H3, { take: 2, target: H3 }).map((b) => b.label)).toEqual(["LTX", "t02 MiniMax"]);
    expect(targetBadges(retargeted, list, H3, { take: 3, target: LTX }).map((b) => b.label)).toEqual(["LTX"]);
    expect(targetBadges({ target: H3, built_target: H3 }, list, H3, { take: 2, target: LTX }).map((b) => b.label)).toEqual(["t02 LTX"]);
    expect(targetBadges({ target: H3, built_target: H3 }, list, H3, null)).toEqual([]);
  });
});

describe("runSize", () => {
  const d = {
    pass: "proxy" as const, target: H3, built_target: H3,
    effective: { prompt: "", seed: "1", seed_source: "stable", model: "", loras: null, steps: 4, target: H3, width: 448, height: 256, length: 107 },
  };
  it("is exact for the shot's own target", () => {
    expect(runSize(d, H3, H3, list)).toEqual({ text: "448×256 · 107 frames", exact: true });
  });
  it("says the target sets it for another target, with its preset size", () => {
    expect(runSize(d, LTX, H3, list)).toEqual({ text: "size set by the target (its proxy preset is 640×352)", exact: false });
    expect(runSize(undefined, LTX, H3, list).text).toBe("size set by the target");
    expect(runSize({ ...d, effective: { ...d.effective, width: null, height: null } }, H3, H3, null).text).toBe("size set by the target");
  });
});

describe("renderWarnings", () => {
  const base: RenderResult = { queued: [], skipped: [], errors: [] };
  it("reads a top-level warnings array of strings or objects", () => {
    const r = { ...base, warnings: ["one", { shot: "sh010", warning: "audio downgraded to generate" }, { shot: "sh020", message: "m" }] };
    expect(renderWarnings(r)).toEqual([
      { text: "one", shot: undefined },
      { shot: "sh010", text: "audio downgraded to generate" },
      { shot: "sh020", text: "m" },
    ]);
  });
  it("reads warnings on queued, skipped and errored entries, and dedupes", () => {
    const r = {
      ...base,
      queued: [{ shot: "sh010", take: 1, prompt_id: "p", seed: "1", seed_source: "new", warnings: ["no voice ref"] }],
      skipped: [{ shot: "sh020", reason: "x", warning: "w" } as unknown as RenderResult["skipped"][number]],
      errors: [{ shot: "sh030", error: "e", warnings: [{ text: "t" }] }],
      warnings: [{ shot: "sh010", warning: "no voice ref" }],
    } as RenderResult;
    expect(renderWarnings(r)).toEqual([
      { shot: "sh010", text: "no voice ref" },
      { shot: "sh020", text: "w" },
      { shot: "sh030", text: "t" },
    ]);
  });
  it("never throws on odd shapes", () => {
    expect(renderWarnings(undefined)).toEqual([]);
    expect(renderWarnings({ warnings: "just one" } as unknown as RenderResult)).toEqual([{ text: "just one", shot: undefined }]);
    expect(renderWarnings({ queued: null, skipped: [null, 3], errors: "x", warnings: [null, 5, {}, ""] } as unknown as RenderResult)).toEqual([]);
  });
});

describe("planRedo with a target", () => {
  const d = {
    shot: "sh020", pass: "proxy", index: 0, built: {}, built_prompt: "BUILT", override: {}, override_stale: false, takes: [],
    target: H3, built_target: H3,
    effective: { prompt: "BUILT", seed: "1", seed_source: "stable", model: "m", loras: null, steps: 4 },
  } as ShotDetail;
  const plan: RedoPlan = {
    shot: "sh020", pass: "proxy", parent: null, seed: { mode: "new" }, model: "m", loras: null, steps: 4,
    prompt: "EDITED", note: "", saveAsOverride: true,
  };
  it("sends a one-off target, never saves it, and drops the prompt", () => {
    const r = planRedo({ ...plan, target: LTX, lockPrompt: true, model: "ltx.safetensors", steps: 8 }, "ep", d);
    expect(r.override).toBeNull();
    expect(r.render).toMatchObject({ target: LTX, prompt: null, model: "ltx.safetensors", steps: 8 });
  });
  it("doesn't send the shot's own target", () => {
    const r = planRedo({ ...plan, target: H3 }, "ep", d);
    expect("target" in r.render).toBe(false);
    expect(r.override).toEqual({ prompt: "EDITED" });
  });
  it("never saves a prompt for a retargeted shot", () => {
    const re = { ...d, target: LTX } as ShotDetail;
    const r = planRedo({ ...plan, lockPrompt: true, steps: 9 }, "ep", re);
    expect(r.override).toEqual({ steps: 9 });
    expect("target" in r.render).toBe(false);
  });
});

describe("HTTP client: targets", () => {
  function fake(routes: Record<string, () => Response>) {
    const calls: string[] = [];
    const t: Transport = {
      async fetch(path) {
        calls.push(path);
        const h = routes[path.split("?")[0]];
        return h ? h() : new Response("404", { status: 404 });
      },
      url: (p) => p,
    };
    return { api: createHttpApi(t), calls };
  }
  const json = (o: unknown) => new Response(JSON.stringify(o), { status: 200 });

  it("GETs /h3pipe/targets and tolerates a missing default", async () => {
    const { api, calls } = fake({ "/h3pipe/targets": () => json({ targets: [{ id: H3, kind: "video", label: "H3" }] }) });
    const r = await api.targets("video");
    expect(calls[0]).toBe("/h3pipe/targets?kind=video");
    expect(r).toEqual({ targets: [{ id: H3, kind: "video", label: "H3" }], default: {} });
    await api.targets();
    expect(calls[1]).toBe("/h3pipe/targets?");
  });

  it("reads a widget's choices from /object_info", async () => {
    const { api, calls } = fake({ "/object_info/UNETLoader": () => json({ UNETLoader: { input: { required: { unet_name: [["a", "b"]] } } } }) });
    expect(await api.widgetChoices("UNETLoader", "unet_name")).toEqual(["a", "b"]);
    expect(calls[0]).toBe("/object_info/UNETLoader");
  });

  it("sends target on a render only when set", async () => {
    const bodies: unknown[] = [];
    const t: Transport = {
      async fetch(_p, init) {
        bodies.push(JSON.parse(String(init?.body)));
        return new Response(JSON.stringify({ queued: [], skipped: [], errors: [] }), { status: 200 });
      },
      url: (p) => p,
    };
    const api = createHttpApi(t);
    const { baseRender } = await import("../src/actions");
    await api.render(baseRender("ep", "proxy", ["sh010"], false, LTX));
    await api.render(baseRender("ep", "proxy", ["sh010"]));
    expect(bodies[0]).toMatchObject({ target: LTX });
    expect(bodies[1]).not.toHaveProperty("target");
  });
});

describe("mock: retargeting", () => {
  it("serves targets, a retargeted shot and mixed-target takes", async () => {
    const api = createMockApi(() => {}, { latency: 0 });
    const ep = (await api.episodes())[0].ep;
    const t = await api.targets();
    expect(videoTargets(t).map((x) => x.id)).toEqual([H3, LTX, "ltx2_ingredients", "wan22_i2v", "wan22_vace", "minimax_h3_fl2va"]);
    expect(t.default.video).toBe(H3);
    const st = await api.episode(ep, "proxy");
    expect(st.target).toBe(H3);
    const sh030 = st.shots.find((s) => s.shot === "sh030")!;
    expect(sh030).toMatchObject({ target: LTX, built_target: H3 });
    expect(sh030.takes.every((x) => x.target === H3)).toBe(true);
    const sh020 = st.shots.find((s) => s.shot === "sh020")!;
    expect(sh020.takes.map((x) => x.target)).toEqual([H3, LTX]);
    const d = await api.shot(ep, "proxy", "sh030");
    expect(d).toMatchObject({ target: LTX, built_target: H3 });
    expect(d.effective).toMatchObject({ target: LTX, width: 640, height: 352, loras: null });
    expect(d.effective.prompt).not.toMatch(/<Picture/);
    expect(d.override.prompt).toBeTruthy(); // kept, but ignored
    expect(await api.widgetChoices("CheckpointLoaderSimple", "ckpt_name")).toHaveLength(2);
  });

  it("sets and clears the target for both passes; revert of one pass keeps it", async () => {
    const api = createMockApi(() => {}, { latency: 0 });
    const ep = (await api.episodes())[0].ep;
    await api.putOverride({ ep, pass: "proxy", shot: "sh020", both: true, fields: { target: LTX } });
    expect((await api.shot(ep, "final", "sh020")).target).toBe(LTX);
    expect((await api.shot(ep, "proxy", "sh020")).effective.target).toBe(LTX);
    await api.deleteOverride(ep, "sh020", "proxy");
    expect((await api.shot(ep, "proxy", "sh020")).target).toBe(LTX);
    await api.putOverride({ ep, pass: "proxy", shot: "sh020", both: true, fields: { target: null } });
    const d = await api.shot(ep, "proxy", "sh020");
    expect(d.target).toBe(H3);
    expect(d.effective.width).toBe(448);
  });

  it("renders on a one-off target and warns about the voice", async () => {
    vi.useFakeTimers();
    try {
      const api = createMockApi(() => {}, { latency: 0 });
      const ep = (await api.episodes())[0].ep;
      const base = { ep, pass: "final" as const, redo: true, seed_mode: "new" as const, seed: null, model: null, loras: null, steps: null, prompt: null, parent_take: null, note: "", allow_missing_refs: true };
      const r = await api.render({ ...base, shots: ["sh010"], target: LTX });
      expect(r.queued[0]).toMatchObject({ shot: "sh010", target: LTX });
      expect(renderWarnings(r)).toEqual([expect.objectContaining({ shot: "sh010", text: expect.stringMatching(/audio downgraded/) })]);
      const s = (await api.episode(ep, "final")).shots.find((x) => x.shot === "sh010")!;
      expect(s.takes[s.takes.length - 1].target).toBe(LTX);
      expect(s.target).toBe(H3); // a one-off doesn't retarget the shot
      const r2 = await api.render({ ...base, shots: ["sh010"] });
      expect(renderWarnings(r2)).toEqual([]);
      await vi.runAllTimersAsync();
    } finally {
      vi.useRealTimers();
    }
  });
});
