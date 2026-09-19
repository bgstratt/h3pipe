// The mock is what the dev page runs on: check it serves the real episode's
// shapes (seeds as strings) and that a simulated render goes queued -> ok.
import { describe, expect, it, vi } from "vitest";
import { createMockApi } from "../src/mock/mockApi";

describe("mock API", () => {
  it("serves the smoke episode with string seeds", async () => {
    const api = createMockApi(() => {}, { latency: 0 });
    const [ep] = await api.episodes();
    expect(ep.name).toBe("ep05");
    const st = await api.episode(ep.ep, "proxy");
    const seeds = st.shots.flatMap((s) => s.takes.map((t) => t.seed)).filter((s) => s != null);
    expect(seeds.length).toBeGreaterThan(0);
    for (const s of seeds) expect(typeof s).toBe("string");
    expect(seeds).toContain("6430499148929255544");
    const d = await api.shot(ep.ep, "proxy", "sh030");
    expect(typeof d.effective.seed).toBe("string");
    expect(d.override.prompt).toBeTruthy();
  });

  it("simulates a render and emits the live events", async () => {
    vi.useFakeTimers();
    try {
      const events: string[] = [];
      const api = createMockApi((e) => events.push(e), { latency: 0 });
      const ep = (await api.episodes())[0].ep;
      const r = await api.render({
        ep, pass: "proxy", shots: ["sh050"], redo: false, seed_mode: "auto", seed: null, model: null, loras: null,
        steps: null, prompt: null, parent_take: null, note: "", allow_missing_refs: true, // sh050 is missing a voice
      });
      expect(r.queued).toHaveLength(1);
      expect((await api.episode(ep, "proxy")).shots.find((s) => s.shot === "sh050")!.takes[0].status).toBe("queued");
      await vi.runAllTimersAsync();
      const s = (await api.episode(ep, "proxy")).shots.find((x) => x.shot === "sh050")!;
      expect(s.takes[0].status).toBe("ok");
      expect(s.cut.take).toBe(1);
      expect(events).toEqual(expect.arrayContaining(["h3pipe.take", "execution_start", "progress", "execution_success"]));
    } finally {
      vi.useRealTimers();
    }
  });

  it("pick refuses an unusable take with 409", async () => {
    const api = createMockApi(() => {}, { latency: 0 });
    const ep = (await api.episodes())[0].ep;
    await expect(api.pick({ ep, pass: "proxy", shot: "sh040", take: 1, from_pass: null })).rejects.toMatchObject({ status: 409 });
  });

  it("skips shots with missing refs unless allow_missing_refs", async () => {
    const api = createMockApi(() => {}, { latency: 0 });
    const ep = (await api.episodes())[0].ep;
    const st = await api.episode(ep, "proxy");
    const blocked = st.shots.find((s) => s.missing_refs?.length)!;
    const ready = st.shots.find((s) => !s.missing_refs?.length && !s.takes.length)!;
    expect(blocked && ready).toBeTruthy();
    const base = { ep, pass: "proxy" as const, redo: false, seed_mode: "auto" as const, seed: null, model: null, loras: null, steps: null, prompt: null, parent_take: null, note: "" };
    const r = await api.render({ ...base, shots: [blocked.shot, ready.shot] });
    expect(r.queued.map((q) => q.shot)).toEqual([ready.shot]);
    expect(r.skipped).toEqual([expect.objectContaining({ shot: blocked.shot, missing_refs: blocked.missing_refs })]);
    const r2 = await api.render({ ...base, shots: [blocked.shot], allow_missing_refs: true });
    expect(r2.queued.map((q) => q.shot)).toEqual([blocked.shot]);
  });
});

describe("mock refs", () => {
  it("lists the kitchen_sink series config, grouped by kind, with character views", async () => {
    const api = createMockApi(() => {}, { latency: 0 });
    const ep = (await api.episodes())[0].ep;
    const { refs } = await api.refs(ep);
    const ids = refs.map((r) => r.id);
    expect(ids).toEqual(expect.arrayContaining(["subject:ada", "subject:rex", "subject:kettle", "subject:van", "location:street", "voice:ada"]));
    expect(ids).not.toContain("subject:_note");
    expect(refs.find((r) => r.id === "subject:rex")!.kind).toBe("character"); // no kind in the series config
    expect(refs.find((r) => r.id === "subject:ada")!.views).toHaveLength(4);
    expect(refs.find((r) => r.id === "subject:van")!.kind).toBe("vehicle");
  });

  it("picking the last view stitches the sheet and unblocks shots", async () => {
    const api = createMockApi(() => {}, { latency: 0 });
    const ep = (await api.episodes())[0].ep;
    const before = await api.episode(ep, "proxy");
    const bo = (await api.refs(ep)).refs.find((r) => r.id === "subject:bo")!;
    expect(bo.exists).toBe(false);
    const blockedByBo = before.shots.filter((s) => s.missing_refs?.some((m) => m.path === bo.path)).map((s) => s.shot);
    expect(blockedByBo.length).toBeGreaterThan(0);
    await expect(api.refsPick({ ep, ref: bo.id, view: "04_face", take: 1 })).rejects.toMatchObject({ status: 404 });
    let r = await api.refsPick({ ep, ref: bo.id, view: "03_back", take: 1 });
    expect(r.exists).toBe(false); // still one view to go
    const imp = await api.refsImport({ ep, ref: bo.id, view: "04_face", source_path: "D:\\Stock\\portraits\\bo_drawn.png" });
    expect(imp).toMatchObject({ take: 1, source: "imported", status: "ok" });
    r = await api.refsPick({ ep, ref: bo.id, view: "04_face", take: imp.take });
    expect(r.exists).toBe(true);
    expect(api.refFileUrl(ep, r.path!, r.sha1)).toMatch(/^data:image\/svg/);
    const after = await api.episode(ep, "proxy");
    expect(after.shots.filter((s) => s.missing_refs?.some((m) => m.path === bo.path))).toEqual([]);
  });

  it("generates candidates: queued, progress, then ok with an image and an h3pipe.ref event", async () => {
    vi.useFakeTimers();
    try {
      const events: [string, unknown][] = [];
      const api = createMockApi((e, d) => events.push([e, d]), { latency: 0 });
      const ep = (await api.episodes())[0].ep;
      const res = await api.refsGenerate({ ep, ref: "subject:cy", view: null, count: 2, seed_mode: "auto", seed: null, prompt: null, model: null, loras: null, steps: null, note: "" });
      expect(res.queued).toHaveLength(8); // 4 views x 2 candidates
      expect(new Set(res.queued.map((q) => q.seed)).size).toBe(2); // views share a seed per candidate
      await vi.runAllTimersAsync();
      const cy = (await api.refs(ep)).refs.find((r) => r.id === "subject:cy")!;
      expect(cy.views!.every((v) => v.takes.length === 2 && v.takes.every((t) => t.status === "ok" && t.image))).toBe(true);
      expect(events.some(([e, d]) => e === "h3pipe.ref" && (d as { status: string }).status === "ok" && (d as { ep: string }).ep === ep)).toBe(true);
      await expect(api.refsGenerate({ ep, ref: "voice:ada", view: null, count: 1, seed_mode: "auto", seed: null, prompt: null, model: null, loras: null, steps: null, note: "" })).rejects.toMatchObject({ status: 400 });
    } finally {
      vi.useRealTimers();
    }
  });

  it("browses the fake folder tree, files on request", async () => {
    const api = createMockApi(() => {}, { latency: 0 });
    const start = await api.browse(null);
    expect(start.parent).toBeNull();
    const shows = await api.browse("C:\\Shows\\DeanStories");
    expect(shows.dirs.find((d) => d.name === "ep05")).toMatchObject({ episode: true });
    expect(shows.files).toBeUndefined();
    const drive = await api.browse("D:\\");
    expect(drive.parent).toBe("");
    const pics = await api.browse("D:\\Stock\\portraits", "image");
    expect(pics.files!.map((f) => f.name)).toContain("bo_drawn.png");
    await expect(api.browse("Q:\\nope")).rejects.toMatchObject({ status: 404 });
  });
});
