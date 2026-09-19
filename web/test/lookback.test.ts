// Phase 8.6 (look-back): the client's new routes (discard, refs discard,
// generate-missing, the multipart upload), the text helpers, the mock's
// behaviour, and the per-view ref override (PUT /h3pipe/refs/override with `view`).
import { describe, expect, it } from "vitest";
import { ApiError, createHttpApi, type Transport } from "../src/api";
import { discardRefText, discardTakeText, dragHasFiles, fileKindProblem, missingResultText, uploadText } from "../src/lib/lookback";
import { createMockApi } from "../src/mock/mockApi";

function recorder(answer: (path: string, init?: RequestInit) => { status?: number; body: string }) {
  const calls: { method: string; path: string; body: unknown }[] = [];
  const t: Transport = {
    fetch: async (path, init) => {
      calls.push({ method: String(init?.method), path, body: init?.body });
      const a = answer(path, init);
      return new Response(a.body, { status: a.status ?? 200 });
    },
    url: (p) => p,
  };
  return { calls, api: createHttpApi(t) };
}

describe("Phase 8.6 client", () => {
  it("POST /h3pipe/discard and /h3pipe/refs/discard", async () => {
    const { calls, api } = recorder((p) => ({ body: p === "/h3pipe/discard" ? `{"shot":"sh020","take":3,"pass":"proxy","moved":[],"cut_changed":true}` : `{"id":"location:kitchen"}` }));
    const r = await api.discard({ ep: "E", pass: "proxy", shot: "sh020", take: 3 });
    expect(r.cut_changed).toBe(true);
    expect(calls[0]).toMatchObject({ method: "POST", path: "/h3pipe/discard" });
    expect(JSON.parse(String(calls[0].body))).toEqual({ ep: "E", shot: "sh020", take: 3, pass: "proxy" });
    await api.refsDiscard({ ep: "E", ref: "location:kitchen", view: null, take: 2 });
    expect(JSON.parse(String(calls[1].body))).toEqual({ ep: "E", ref: "location:kitchen", take: 2 }); // no view for a non-character
    await api.refsDiscard({ ep: "E", ref: "subject:ada", view: "02_side", take: 1 });
    expect(JSON.parse(String(calls[2].body)).view).toBe("02_side");
  });

  it("POST /h3pipe/refs/generate-missing sends only what's set", async () => {
    const { calls, api } = recorder(() => ({ body: `{"queued":[],"picked":[],"skipped":[],"errors":[]}` }));
    await api.refsGenerateMissing({ ep: "E", pass: "proxy", dry_run: true });
    expect(JSON.parse(String(calls[0].body))).toEqual({ ep: "E", pass: "proxy", dry_run: true });
    await api.refsGenerateMissing({ ep: "E", kinds: ["keyframe"], keyframe_target: "flux_kontext", target: null });
    expect(JSON.parse(String(calls[1].body))).toEqual({ ep: "E", kinds: ["keyframe"], keyframe_target: "flux_kontext" });
  });

  it("uploads a ref as multipart (pick=1, view only when set), through fetch without an upload transport", async () => {
    const { calls, api } = recorder(() => ({ body: `{"take":4,"view":"04_face","status":"ok","source":"imported","original_name":"face.png"}` }));
    const file = new File([new Uint8Array([1, 2, 3])], "face.png", { type: "image/png" });
    const t = await api.refsUpload({ ep: "E", ref: "subject:ada", view: "04_face", pick: true, file });
    expect(t).toMatchObject({ take: 4, original_name: "face.png" });
    const form = calls[0].body as FormData;
    expect(form).toBeInstanceOf(FormData);
    expect([form.get("ep"), form.get("ref"), form.get("view"), form.get("pick")]).toEqual(["E", "subject:ada", "04_face", "1"]);
    expect((form.get("file") as File).name).toBe("face.png");
    await api.refsUpload({ ep: "E", ref: "location:kitchen", file, name: "k.png" });
    const f2 = calls[1].body as FormData;
    expect([f2.has("view"), f2.has("pick"), (f2.get("file") as File).name]).toEqual([false, false, "k.png"]);
  });

  it("an upload transport reports progress; a 413 without a message says the limit", async () => {
    const seen: number[] = [];
    const api = createHttpApi({
      fetch: async () => new Response("{}"),
      url: (p) => p,
      upload: async (_p, _f, onProgress) => {
        onProgress?.(50, 100);
        onProgress?.(100, 100);
        return { status: 413, statusText: "Payload Too Large", text: "" };
      },
    });
    const err = await api.refsUpload({ ep: "E", ref: "location:kitchen", file: new Blob(["x"]), name: "big.png" }, (s) => seen.push(s)).catch((e) => e);
    expect(seen).toEqual([50, 100]);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ status: 413, message: expect.stringMatching(/64 MB/) });
  });

  it("the JSON import takes pick only when set", async () => {
    const { calls, api } = recorder(() => ({ body: `{"take":1}` }));
    await api.refsImport({ ep: "E", ref: "location:kitchen", source_path: "D:\\a.png" });
    expect(JSON.parse(String(calls[0].body))).not.toHaveProperty("pick");
    await api.refsImport({ ep: "E", ref: "location:kitchen", source_path: "D:\\a.png", pick: true });
    expect(JSON.parse(String(calls[1].body)).pick).toBe(true);
  });

  it("PUT /h3pipe/refs/defaults and a view's override", async () => {
    const { calls, api } = recorder(() => ({ body: `{"defaults":{"target":"krea2"},"override":{"stale":false}}` }));
    await api.putRefDefaults("E", { keyframe_target: null });
    expect(calls[0]).toMatchObject({ method: "PUT", path: "/h3pipe/refs/defaults" });
    expect(JSON.parse(String(calls[0].body))).toEqual({ ep: "E", keyframe_target: null });
    await api.putRefOverride({ ep: "E", ref: "subject:ada", view: "04_face", fields: { prompt: "close" } });
    expect(JSON.parse(String(calls[1].body))).toEqual({ ep: "E", ref: "subject:ada", view: "04_face", fields: { prompt: "close" } });
    await api.deleteRefOverride("E", "subject:ada", "04_face");
    expect(calls[2].path).toBe("/h3pipe/refs/override?ep=E&ref=subject%3Aada&view=04_face");
  });
});

describe("Phase 8.6 helpers", () => {
  it("says what a discard does", () => {
    expect(discardTakeText("sh020", 3, "proxy", "renders_proxy", true)).toMatch(/renders_proxy\/_trash\/sh020\/[\s\S]*cut picks this take/);
    expect(discardTakeText("sh020", 3, "final", null, false)).not.toMatch(/cut picks/);
    expect(discardRefText("kitchen", 2, true)).toMatch(/live file: the ref is cleared/);
    expect(discardRefText("sh020 first frame", 1, true, "keyframe")).toMatch(/as Clear does/);
  });
  it("checks a dropped file's kind, and the drag's contents", () => {
    expect(fileKindProblem({ name: "a.PNG" }, "image")).toBe("");
    expect(fileKindProblem({ name: "a.webp" }, "image")).toBe("");
    expect(fileKindProblem({ name: "a.wav" }, "image")).toMatch(/isn't an image/);
    expect(fileKindProblem({ name: "a.m4a" }, "audio")).toBe("");
    expect(fileKindProblem({ name: "a.png" }, "audio")).toMatch(/isn't an audio file/);
    expect(dragHasFiles({ types: ["text/plain", "Files"] })).toBe(true);
    expect(dragHasFiles({ types: ["text/uri-list"] })).toBe(false);
    expect(dragHasFiles(null)).toBe(false);
    expect(uploadText({ sent: 25, total: 100 })).toBe("25%");
  });
  it("sums up Generate missing: continuity frames come back in picked", () => {
    const r = missingResultText({
      queued: [{ ref: "subject:ada", view: null, take: 3, prompt_id: "p", method: "generate" }],
      picked: [{ ref: "shot:sh030:first", take: 1, method: "continuity" }, { ref: "shot:sh090:last", take: 1, method: "import" }],
      skipped: [{ ref: "voice:ada", reason: "nothing generates voices" }],
    });
    expect(r.summary).toBe("Generate missing: queued 1, picked 2 (1 from neighbouring takes), skipped 1");
    expect(r.detail).toMatch(/skipped voice:ada: nothing generates voices/);
    expect(missingResultText({ queued: [], picked: [], skipped: [] }).summary).toBe("Generate missing: nothing to do");
  });
});

describe("mock: Phase 8.6", () => {
  it("discards a take: its files go to _trash, the cut falls back, a queued one is refused", async () => {
    const api = createMockApi(() => {}, { latency: 0 });
    const ep = (await api.episodes())[0].ep;
    // the smoke episode's cut picks sh020 t01 in proxy
    let st = await api.episode(ep, "proxy");
    const sh020 = st.shots.find((s) => s.shot === "sh020")!;
    expect(sh020.cut).toMatchObject({ take: 1, picked: true });
    const r = await api.discard({ ep, pass: "proxy", shot: "sh020", take: 1 });
    expect(r).toMatchObject({ shot: "sh020", take: 1, pass: "proxy", cut_changed: true });
    for (const m of r.moved) expect(m).toMatch(/_trash\//);
    st = await api.episode(ep, "proxy");
    expect(st.shots.find((s) => s.shot === "sh020")!.takes.some((t) => t.take === 1)).toBe(false);
    expect(st.shots.find((s) => s.shot === "sh020")!.cut.picked).toBe(false);
    const q = await api.render({
      ep, pass: "proxy", shots: ["sh020"], redo: true, seed_mode: "new", seed: null, model: null, loras: null, steps: null, prompt: null,
      parent_take: null, note: "", allow_missing_refs: true,
    });
    await expect(api.discard({ ep, pass: "proxy", shot: "sh020", take: q.queued[0].take })).rejects.toMatchObject({ status: 409 });
  });

  it("discards a ref candidate: a live one clears the ref (discarded, then cleared)", async () => {
    const events: [string, unknown][] = [];
    const api = createMockApi((e, d) => events.push([e, d]), { latency: 0 });
    const ep = (await api.episodes())[0].ep;
    const kit = await api.refsDiscard({ ep, ref: "location:kitchen", view: null, take: 1 });
    expect(kit).toMatchObject({ exists: false, picked: null, cleared: true });
    expect(kit.takes.map((t) => t.take)).toEqual([2]);
    expect(events.filter(([e]) => e === "h3pipe.ref").map(([, d]) => (d as { status: string }).status)).toEqual(["discarded", "cleared"]);
    const other = await api.refsDiscard({ ep, ref: "location:kitchen_window", view: null, take: 2 });
    expect(other.takes.map((t) => t.take)).toEqual([1]);
    // numbers aren't reused
    const g = await api.refsGenerate({ ep, ref: "location:kitchen_window", view: null, count: 1, seed_mode: "new", seed: null, prompt: null, model: null, loras: null, steps: null, note: "" });
    expect(g.queued[0].take).toBe(3);
    await expect(api.refsDiscard({ ep, ref: "subject:ada", view: "01_threequarter", take: 9 })).rejects.toMatchObject({ status: 404 });
  });

  it("uploads with pick: live at once, with its file name; a wrong type is 400", async () => {
    const api = createMockApi(() => {}, { latency: 0 });
    const ep = (await api.episodes())[0].ep;
    const seen: number[] = [];
    const t = await api.refsUpload({ ep, ref: "subject:bo", view: "03_back", pick: true, file: new Blob(["png"]), name: "bo_back.png" }, (s, total) => seen.push(s / total));
    expect(t).toMatchObject({ view: "03_back", source: "imported", original_name: "bo_back.png" });
    expect(seen.at(-1)).toBe(1);
    const bo = (await api.refs(ep)).refs.find((r) => r.id === "subject:bo")!;
    expect(bo.views!.find((v) => v.view === "03_back")!.picked).toBe(t.take);
    await expect(api.refsUpload({ ep, ref: "voice:ada", file: new Blob(["x"]), name: "x.png" })).rejects.toMatchObject({ status: 400 });
    // a keyframe slot takes one too (a shot in the build, listed or not)
    const k = await api.refsUpload({ ep, ref: "shot:sh060:first", pick: true, file: new Blob(["png"]), name: "sh060.png" });
    expect(k.take).toBe(1);
    expect((await api.refs(ep)).refs.find((r) => r.id === "shot:sh060:first")).toMatchObject({ exists: true, picked: 1 });
  });

  it("generate-missing: a dry run plans, the real run queues and picks the same", async () => {
    {
      const api = createMockApi(() => {}, { latency: 0 });
      const ep = (await api.episodes())[0].ep;
      const dry = await api.refsGenerateMissing({ ep, pass: "proxy", dry_run: true });
      expect(dry.queued.length).toBeGreaterThan(0);
      expect(dry.queued.every((q) => q.take == null && q.prompt_id == null)).toBe(true);
      expect(dry.picked).toEqual([{ ref: "shot:sh030:first", take: null, method: "continuity" }]);
      // a character missing all four views is one generate (view null)
      expect(dry.queued.find((q) => q.ref === "subject:cy")).toMatchObject({ view: null });
      const real = await api.refsGenerateMissing({ ep, pass: "proxy" });
      expect(real.queued.length).toBeGreaterThanOrEqual(dry.queued.length); // a view-null character queues four
      expect(real.picked).toEqual([{ ref: "shot:sh030:first", take: 1, method: "continuity" }]);
      // nothing left to do: everything is queued or live
      const again = await api.refsGenerateMissing({ ep, pass: "proxy", dry_run: true });
      expect(again.queued).toEqual([]);
      expect(again.skipped.length).toBeGreaterThan(0);
    }
  });

  it("serves the real listing's shapes: views [] off characters, per-view settings, a voice-only character", async () => {
    const api = createMockApi(() => {}, { latency: 0 });
    const ep = (await api.episodes())[0].ep;
    const { refs, defaults } = await api.refs(ep);
    expect(defaults).toMatchObject({ target_source: "default", keyframe_target_source: "default" });
    const byId = (id: string) => refs.find((r) => r.id === id)!;
    expect(byId("location:kitchen").views).toEqual([]);
    expect(byId("subject:narrator")).toMatchObject({ path: null, can_generate: false, effective: null });
    expect(byId("subject:ada").effective).toEqual({ target: "krea2" });
    const face = byId("subject:ada").views!.find((v) => v.view === "04_face")!;
    expect(face.override!.fields).toEqual(["prompt"]);
    expect(face.prompt).toMatch(/glasses catching the light/);
    expect(byId("voice:ada").takes[0]).toMatchObject({ image: null, audio: expect.stringMatching(/\.wav$/) });

    // a view's own setting merges over the character's
    await api.putRefOverride({ ep, ref: "subject:ada", view: null, fields: { steps: 12 } });
    const r = await api.putRefOverride({ ep, ref: "subject:ada", view: "02_side", fields: { prompt: "side, arms folded" } });
    expect(r.override).toMatchObject({ steps: 12, prompt: "side, arms folded", stale: false });
    const side = (await api.refs(ep)).refs.find((x) => x.id === "subject:ada")!.views!.find((v) => v.view === "02_side")!;
    expect(side.override!.fields).toEqual(["prompt", "steps"]);
    expect(side.effective).toMatchObject({ prompt: "side, arms folded", steps: 12 });
    await expect(api.putRefOverride({ ep, ref: "subject:ada", view: null, fields: { prompt: "x" } })).rejects.toMatchObject({ status: 400 });
    const back = await api.deleteRefOverride(ep, "subject:ada", "02_side");
    expect(back.override).toMatchObject({ steps: 12 });
    expect(back.override).not.toHaveProperty("prompt");

    // the episode's image targets
    const d = await api.putRefDefaults(ep, { keyframe_target: "flux_kontext" });
    expect(d.defaults).toMatchObject({ keyframe_target: "flux_kontext", keyframe_target_source: "editor", target_source: "default" });
    expect((await api.refs(ep)).refs.find((x) => x.id === "shot:sh060:first")!.effective!.target).toBe("flux_kontext");
  });
});
