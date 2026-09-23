// A show's own targets (Phase 12c): drafts stay out of the pickers, the wizard's
// answers land in the target.json it saves, and the mock plays the whole flow.
import { describe, expect, it } from "vitest";
import { planRedo } from "../src/actions";
import { createHttpApi, type Transport } from "../src/api";
import { customTargets, videoTargets } from "../src/lib/targets";
import { createMockApi } from "../src/mock/mockApi";
import type { Target, TargetList, TargetProposal } from "../src/types";

const list: TargetList = {
  default: { video: "minimax_h3_ref2va" },
  targets: [
    { id: "minimax_h3_ref2va", kind: "video", label: "MiniMax H3 Ref2VA" },
    { id: "ready_one", kind: "video", label: "Mine, proved", custom: true },
    { id: "draft_one", kind: "video", label: "Mine, a draft", custom: true, draft: true },
    { id: "krea2", kind: "image", label: "Krea 2" },
  ] as Target[],
};

describe("drafts and a show's own targets", () => {
  it("leaves a draft out of the pickers", () => {
    expect(videoTargets(list).map((t) => t.id)).toEqual(["minimax_h3_ref2va", "ready_one"]);
  });

  it("offers drafts when the panel asks for them", () => {
    expect(videoTargets(list, true).map((t) => t.id))
      .toEqual(["minimax_h3_ref2va", "ready_one", "draft_one"]);
  });

  it("lists the show's own, drafts included", () => {
    expect(customTargets(list).map((t) => t.id)).toEqual(["ready_one", "draft_one"]);
  });
});

describe("the wizard's calls", () => {
  const calls: { method: string; path: string; body?: unknown }[] = [];
  const transport: Transport = {
    async fetch(path, init) {
      const method = init?.method ?? "GET";
      const body = init?.body ? JSON.parse(String(init.body)) : undefined;
      calls.push({ method, path, body });
      const answer = path.startsWith("/h3pipe/workflows")
        ? { workflows: [{ name: "mine.json" }] }
        : path.startsWith("/h3pipe/targets/inspect")
          ? { proposal: { id: "my_wan" }, matched: {}, ambiguous: [], warnings: [], models: [], nodes: {}, problems: [] }
          : { ok: true, id: "my_wan", draft: true };
      return new Response(JSON.stringify(answer),
                          { status: 200, headers: { "Content-Type": "application/json" } });
    },
    url: (p) => `http://comfy${p}`,
  };
  const api = createHttpApi(transport);

  it("asks ComfyUI for its workflows", async () => {
    expect(await api.workflows()).toEqual([{ name: "mine.json" }]);
    expect(calls.at(-1)).toMatchObject({ method: "GET", path: "/h3pipe/workflows" });
  });

  it("inspects a workflow by name", async () => {
    const r = await api.inspectTarget({ ep: "C:\\Shows\\ep01", workflow: "mine.json", id: "my_wan" });
    expect((r as TargetProposal).proposal).toMatchObject({ id: "my_wan" });
    expect(calls.at(-1)).toMatchObject({
      method: "POST", path: "/h3pipe/targets/inspect",
      body: { ep: "C:\\Shows\\ep01", workflow: "mine.json", id: "my_wan" },
    });
  });

  it("saves, flips the draft flag and deletes", async () => {
    await api.saveCustomTarget("C:\\Shows\\ep01", { id: "my_wan" });
    expect(calls.at(-1)).toMatchObject({ method: "PUT", path: "/h3pipe/targets/custom" });
    await api.setTargetDraft("C:\\Shows\\ep01", "my_wan", false);
    expect(calls.at(-1)?.body).toMatchObject({ id: "my_wan", draft: false });
    await api.deleteCustomTarget("C:\\Shows\\ep01", "my_wan");
    expect(calls.at(-1)?.method).toBe("DELETE");
    expect(calls.at(-1)?.path).toContain("id=my_wan");
  });
});

describe("the mock plays the flow", () => {
  it("inspects, saves a draft, enables it, then removes it", async () => {
    const api = createMockApi(() => {}, { latency: 0 });
    const eps = await api.episodes();
    const ep = eps[0].ep;
    const files = await api.workflows();
    expect(files.some((f) => f.name.endsWith(".json"))).toBe(true);
    const r = await api.inspectTarget({ ep, workflow: files[0].name, id: "my_wan", label: "My Wan" });
    expect(r.can_save).toBe(true);
    expect(r.ambiguous.length).toBeGreaterThan(0);          // something to confirm
    expect(r.warnings.length).toBeGreaterThan(0);           // and something guessed

    const saved = await api.saveCustomTarget(ep, r.proposal);
    expect(saved).toMatchObject({ ok: true, id: "my_wan", draft: true });
    let mine = customTargets(await api.targets("video"));
    expect(mine.map((t) => t.id)).toContain("my_wan");
    expect(videoTargets(await api.targets("video")).map((t) => t.id)).not.toContain("my_wan");

    await api.setTargetDraft(ep, "my_wan", false);
    expect(videoTargets(await api.targets("video")).map((t) => t.id)).toContain("my_wan");

    await api.deleteCustomTarget(ep, "my_wan");
    mine = customTargets(await api.targets("video"));
    expect(mine.map((t) => t.id)).not.toContain("my_wan");
  });

  it("refuses a built-in's name", async () => {
    const api = createMockApi(() => {}, { latency: 0 });
    const eps = await api.episodes();
    await expect(api.saveCustomTarget(eps[0].ep, { id: "minimax_h3_ref2va" }))
      .rejects.toThrow(/built-in/);
  });
});

describe("keep frames (a per-render option)", () => {
  it("is sent only when asked for", () => {
    const d = {
      shot: "sh010", pass: "proxy" as const, takes: [], override: { fields: [], stale: false, values: {} },
    } as unknown as Parameters<typeof planRedo>[2];
    const base = {
      shot: "sh010", pass: "proxy" as const, parent: null, seed: { mode: "new" as const, seed: null },
      model: "", loras: null, steps: 4, prompt: "", note: "", saveAsOverride: false,
    };
    expect(planRedo(base, "C:\Shows\ep01", d).render.save_frames).toBeUndefined();
    expect(planRedo({ ...base, keepFrames: true }, "C:\Shows\ep01", d).render.save_frames).toBe(true);
  });
});
