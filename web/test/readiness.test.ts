// Readiness and the episode target: the badge logic, the tier words and the
// What's missing grouping, the episode target's source labels, counts and
// series.json snippet, render skips for a missing file, the HTTP client's new
// routes, and the mock's readiness mix.
import { afterEach, describe, expect, it } from "vitest";
import { renderReport } from "../src/actions";
import { createHttpApi, type Transport } from "../src/api";
import {
  downloadOf, episodeTargetSourceLabel, folderPath, isMissingFileSkip, missingCountText, missingGroups, normReadiness,
  notReadyBanner, pickWarning, readinessBadge, readinessOf, resolutionNotes, searchText, seriesSnippet, seriesTarget,
  targetCounts, targetCountsText, targetOptionText, tierText,
} from "../src/lib/readiness";
import { overrideTargetValue } from "../src/lib/targets";
import { createMockApi } from "../src/mock/mockApi";
import { LTX_REFS, MOCK_TARGETS, WAN_VACE, installMock, resetMockInstalls } from "../src/mock/mockTargets";
import type { MissingFile, Readiness } from "../src/types";

const req = (want: string, extra: Partial<MissingFile> = {}): MissingFile =>
  ({ param: "model", tier: "required", want, folder: "diffusion_models", url: null, source: null, ...extra });

const R = (status: Readiness["status"], missing: MissingFile[] = [], more: Partial<Readiness> = {}): Readiness =>
  ({ status, missing, resolved: {}, features_off: [], nodes_missing: [], ...more });

describe("readiness badge", () => {
  it("maps each status to an icon, a label and a class", () => {
    expect(readinessBadge(R("ready"))).toMatchObject({ icon: "✓", label: "ready", cls: "ready" });
    expect(readinessBadge(R("degraded", [req("t.safetensors", { tier: "accelerator" })]))).toMatchObject({ icon: "◐", label: "degraded", cls: "degraded" });
    expect(readinessBadge(R("not_ready", [req("m.safetensors")]))).toMatchObject({ icon: "✗", label: "not ready", cls: "not-ready" });
    expect(readinessBadge(R("unknown"))).toMatchObject({ icon: "?", cls: "unknown" });
  });

  it("shows nothing without readiness, and reads an odd status as unknown", () => {
    expect(readinessBadge(undefined)).toBeNull();
    expect(readinessBadge(null)).toBeNull();
    expect(readinessBadge({ status: "weird" } as unknown as Readiness)?.status).toBe("unknown");
  });

  it("says what's missing in its tooltip", () => {
    expect(readinessBadge(R("not_ready", [req("a"), req("b")]))!.title).toBe("Can't render yet: 2 files missing");
    const d = readinessBadge(R("degraded", [req("h", { tier: "optional" })], { features_off: ["dur: model (duration head)"] }))!;
    expect(d.title).toBe("Renders, but degraded: 1 file missing; off: dur: model (duration head)");
  });

  it("counts files and custom nodes", () => {
    expect(missingCountText(R("not_ready", [req("a")], { nodes_missing: ["X"] }))).toBe("1 file and 1 custom node missing");
    expect(missingCountText(R("ready"))).toBe("nothing missing");
    expect(missingCountText(R("degraded", [req("a"), req("b", { tier: "optional" })]), ["required"])).toBe("1 file missing");
  });

  it("prefixes picker options with the badge icon", () => {
    expect(targetOptionText({ id: "a", label: "A", readiness: R("ready") }, true)).toBe("✓ A (default)");
    expect(targetOptionText({ id: "b", label: "B", readiness: R("not_ready", [req("x")]) })).toBe("✗ B (not ready)");
    expect(targetOptionText({ id: "c", label: "C", readiness: R("degraded") })).toBe("◐ C (degraded)");
    expect(targetOptionText({ id: "d", label: "" })).toBe("d");
  });

  it("warns where a not-ready or degraded target is picked", () => {
    expect(pickWarning("LTX+refs", R("not_ready", [req("ic.safetensors", { param: "loras" })]))).toEqual({ severity: "err", text: "LTX+refs can't render yet: 1 file missing." });
    expect(pickWarning("Wan+refs", R("degraded", [req("t", { tier: "accelerator" })]))!.text).toBe("Wan+refs renders degraded (slower base settings): 1 file missing.");
    expect(pickWarning("H3", R("ready"))).toBeNull();
    expect(pickWarning("H3", undefined)).toBeNull();
  });

  it("makes the Shots tab banner only for a not-ready target", () => {
    expect(notReadyBanner("LTX+refs", R("not_ready", [req("a"), req("b"), req("c", { tier: "optional" })]))).toBe("LTX+refs can't render yet: 2 files missing");
    expect(notReadyBanner("Wan", R("degraded", [req("t", { tier: "accelerator" })]))).toBeNull();
    expect(notReadyBanner("X", undefined)).toBeNull();
  });

  it("finds a target's readiness in the list, filling in what's left out", () => {
    const list = { targets: [{ id: "a", kind: "video" as const, label: "A", readiness: { status: "ready" } as Readiness }], default: {} };
    expect(readinessOf(list, "a")).toEqual(R("ready"));
    expect(readinessOf(list, "b")).toBeUndefined();
    expect(normReadiness({ status: "degraded", missing: [{ param: "p", want: "w" } as MissingFile] }).missing[0].tier).toBe("required");
  });
});

describe("the What's missing panel", () => {
  const t = { models: { duration_head: { family: "ltx2.5-duration-head", label: "duration head" } } };

  it("explains each tier in plain words", () => {
    expect(tierText(req("m"))).toBe("needed");
    expect(tierText(req("t", { tier: "accelerator" }))).toBe("speeds it up; without it, renders use slower base settings");
    expect(tierText(req("h", { tier: "optional", param: "duration_head" }), t)).toBe("enables duration head");
    expect(tierText(req("h", { tier: "optional", param: "voice_id_lora" }))).toBe("enables voice id lora");
    expect(tierText(req("h", { tier: "optional", feature: "voice ID" }))).toBe("enables voice ID");
  });

  it("groups missing files by tier, required first, and drops empty groups", () => {
    const r = R("not_ready", [req("opt", { tier: "optional" }), req("req1"), req("acc", { tier: "accelerator" }), req("req2")]);
    const g = missingGroups(r);
    expect(g.map((x) => x.tier)).toEqual(["required", "accelerator", "optional"]);
    expect(g[0].files.map((f) => f.want)).toEqual(["req1", "req2"]);
    expect(missingGroups(R("degraded", [req("acc", { tier: "accelerator" })])).map((x) => x.tier)).toEqual(["accelerator"]);
    expect(missingGroups(R("ready"))).toEqual([]);
    expect(missingGroups(undefined)).toEqual([]);
  });

  it("links a URL only when there is one, else says what to search for", () => {
    expect(downloadOf(req("a", { url: "https://huggingface.co/x/a" }))).toEqual({ url: "https://huggingface.co/x/a" });
    expect(downloadOf(req("a", { url: "javascript:alert(1)", source: "foo" }))).toEqual({ search: "foo" });
    expect(searchText(req("a.safetensors"))).toBe("search for a.safetensors");
    expect(searchText(req("a", { source: "search for the Wan turbo LoRA" }))).toBe("search for the Wan turbo LoRA");
    expect(searchText(req("a", { source: "Wan turbo LoRA" }))).toBe("search for Wan turbo LoRA");
    expect(searchText(req("a", { url: "https://x/a" }))).toBe("");
  });

  it("puts the folder under models/", () => {
    expect(folderPath("loras")).toBe("models/loras");
    expect(folderPath("models/loras")).toBe("models/loras");
    expect(folderPath(null)).toBe("");
  });

  it("words substitutions: family, base, off; exact says nothing", () => {
    expect(resolutionNotes({
      model: { want: "ltx-2.3-22b-distilled-fp8.safetensors", using: "ltx-2.3-22b-distilled-int8.safetensors", how: "family" },
      loras: { want: "turbo.safetensors", using: null, how: "base" },
      duration_head: { want: "head.safetensors", using: null, how: "off" },
      text_encoder: { want: "te", using: "te", how: "exact" },
    }, t)).toEqual([
      "using ltx-2.3-22b-distilled-int8.safetensors instead of ltx-2.3-22b-distilled-fp8.safetensors (same family)",
      "rendered with base settings: turbo.safetensors missing",
      "duration head off: head.safetensors missing",
    ]);
    expect(resolutionNotes(null)).toEqual([]);
  });
});

describe("the episode target", () => {
  it("labels where it comes from", () => {
    expect(episodeTargetSourceLabel("series")).toBe("from series.json");
    expect(episodeTargetSourceLabel("editor")).toBe("set in editor");
    expect(episodeTargetSourceLabel("default")).toBe("default");
    expect(episodeTargetSourceLabel(undefined)).toBe("");
  });

  it("counts shots that follow it and shots with their own", () => {
    const shots = [
      { orphan: false, target_source: "episode" as const },
      { orphan: false, target_source: "episode" as const },
      { orphan: false, target_source: "override" as const },
      { orphan: false, target_source: "script" as const },
      { orphan: true, target_source: "override" as const },
      { orphan: false },
    ];
    const c = targetCounts(shots);
    expect(c).toEqual({ episode: 2, own: 2, unknown: 1 });
    expect(targetCountsText(c)).toBe("2 shots follow it · 2 set their own");
    expect(targetCountsText({ episode: 1, own: 1, unknown: 0 })).toBe("1 shot follows it · 1 sets its own");
    expect(targetCountsText({ episode: 0, own: 3, unknown: 0 })).toBe("3 set their own");
  });

  it("makes the series.json snippet", () => {
    expect(seriesSnippet("ltx2")).toBe('"target": "ltx2"');
    expect(seriesSnippet('we"ird')).toBe('"target": "we\\"ird"');
  });

  it("finds series.json's target, else the list default", () => {
    expect(seriesTarget({ series_target: "ltx2" }, MOCK_TARGETS)).toBe("ltx2");
    expect(seriesTarget({ series_target: null }, MOCK_TARGETS)).toBe("minimax_h3_ref2va");
  });

  it("sends a shot's target only when it differs from what it would get anyway", () => {
    const series = { target: "h3", series_target: "h3", target_source: "series" as const };
    const editor = { target: "ltx", series_target: "h3", target_source: "editor" as const };
    expect(overrideTargetValue("ltx", "h3", series)).toBe("ltx");
    expect(overrideTargetValue("h3", "h3", series)).toBeNull();
    // the episode says LTX: pinning a shot to its built H3 has to be explicit
    expect(overrideTargetValue("h3", "h3", editor)).toBe("h3");
    expect(overrideTargetValue("ltx", "h3", editor)).toBeNull();
    // a shot whose script names Wan falls back to Wan, not the episode's
    expect(overrideTargetValue("wan", "wan", editor)).toBeNull();
    // an older server: the built target is the fallback
    expect(overrideTargetValue("h3", "h3", { target: "h3" })).toBeNull();
    expect(overrideTargetValue(null, "h3", editor)).toBeNull();
  });
});

describe("render skips for a missing file", () => {
  const files = [req("ic.safetensors", { param: "loras", folder: "loras" })];

  it("spots them by their missing_files (the settled shape), not missing refs or mismatches", () => {
    expect(isMissingFileSkip({ shot: "a", reason: "x", missing_files: files })).toBe(true);
    expect(isMissingFileSkip({ shot: "a", reason: "model files not installed: ic.safetensors", missing_files: [] })).toBe(false);

    expect(isMissingFileSkip({ shot: "a", reason: "has a usable take (pass redo: true)" })).toBe(false);
    expect(isMissingFileSkip({ shot: "a", reason: "missing refs", missing_refs: [{ slot: "Picture 1", kind: "image", path: "p" }] })).toBe(false);
  });

  it("makes a warning toast with a What's missing action", () => {
    const t = renderReport({
      queued: [],
      skipped: [
        { shot: "sh010", target: LTX_REFS, reason: "required file missing", missing_files: files },
        { shot: "sh020", target: LTX_REFS, reason: "required file missing", missing_files: files },
        { shot: "sh030", reason: "has a queued take" },
      ],
      errors: [],
    }, (id) => (id === LTX_REFS ? "LTX+refs" : id));
    expect(t[0]).toMatchObject({ severity: "warn", summary: "Skipped 2 shots: LTX+refs isn't ready", missingTargets: [LTX_REFS] });
    expect(t[0].detail).toContain("ic.safetensors (models/loras): sh010, sh020");
    expect(t[1]).toMatchObject({ summary: "Nothing queued" });
    expect(t[1].missingTargets).toBeUndefined();
  });
});

describe("the HTTP client", () => {
  function fake(respond: (path: string, init?: RequestInit) => unknown) {
    const calls: { path: string; method?: string; body?: unknown }[] = [];
    const t: Transport = {
      async fetch(path, init) {
        calls.push({ path, method: init?.method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
        return new Response(JSON.stringify(respond(path, init)), { status: 200 });
      },
      url: (p) => p,
    };
    return { api: createHttpApi(t), calls };
  }

  it("asks for readiness with ready=1", async () => {
    const { api, calls } = fake(() => ({ targets: [{ id: "a", kind: "video", label: "A", readiness: { status: "ready" } }], default: {} }));
    const r = await api.targets({ ready: true });
    expect(calls[0].path).toBe("/h3pipe/targets?ready=1");
    expect(r.targets[0].readiness?.status).toBe("ready");
    await api.targets({ kind: "video", ready: true });
    expect(calls[1].path).toBe("/h3pipe/targets?kind=video&ready=1");
    await api.targets("video");
    expect(calls[2].path).toBe("/h3pipe/targets?kind=video");
  });

  it("PUTs the episode target, null clearing it", async () => {
    const { api, calls } = fake(() => ({ target: "ltx2", target_source: "editor", series_target: "minimax_h3_ref2va" }));
    expect(await api.putEpisodeTarget("C:\\ep05", "ltx2")).toMatchObject({ target_source: "editor" });
    await api.putEpisodeTarget("C:\\ep05", null);
    expect(calls.map((c) => [c.method, c.path, c.body])).toEqual([
      ["PUT", "/h3pipe/episode-target", { ep: "C:\\ep05", target: "ltx2" }],
      ["PUT", "/h3pipe/episode-target", { ep: "C:\\ep05", target: null }],
    ]);
    await expect(api.putEpisodeTarget("ep", "" as unknown as string)).rejects.toThrow(/target id or null/);
  });
});

describe("mock readiness and the episode target", () => {
  afterEach(() => resetMockInstalls());

  it("serves a realistic mix", async () => {
    const api = createMockApi(() => {}, { latency: 0 });
    const t = await api.targets({ ready: true });
    const status = Object.fromEntries(t.targets.filter((x) => x.kind === "video").map((x) => [x.id, x.readiness?.status]));
    expect(status).toEqual({
      minimax_h3_ref2va: "ready", ltx2: "degraded", ltx2_ingredients: "not_ready", wan22_i2v: "ready", wan22_vace: "degraded", minimax_h3_fl2va: "ready",
    });
    const refs = readinessOf(t, LTX_REFS)!;
    expect(refs.missing[0]).toMatchObject({ tier: "required", folder: "loras" });
    expect(refs.missing[0].url).toMatch(/^https:\/\//);
    expect(refs.resolved.model.how).toBe("family");
    expect(readinessOf(t, "ltx2")!.features_off).toEqual(["dur: model (duration head)"]);
    expect(readinessOf(t, WAN_VACE)!.missing[0]).toMatchObject({ tier: "accelerator", url: null });
    // without ready=1, no readiness
    expect((await api.targets()).targets.every((x) => !x.readiness)).toBe(true);
  });

  it("turns green after a download and a refresh", async () => {
    const api = createMockApi(() => {}, { latency: 0 });
    installMock("ltx-2.3-22b-ic-lora-ingredients-0.9.safetensors");
    const t = await api.targets({ ready: true });
    expect(readinessOf(t, LTX_REFS)!.status).toBe("ready");
  });

  it("sets the episode target, and shots without their own follow it", async () => {
    const events: string[] = [];
    const api = createMockApi((e) => events.push(e), { latency: 0 });
    const ep = (await api.episodes())[0].ep;
    let st = await api.episode(ep, "proxy");
    expect(st).toMatchObject({ target: "minimax_h3_ref2va", target_source: "series", series_target: "minimax_h3_ref2va" });
    const src = (id: string) => st.shots.find((s) => s.shot === id)!.target_source;
    expect([src("sh010"), src("sh030"), src("sh040")]).toEqual(["series", "override", "script"]); // series.json names the target (as built: "series" | "default")

    const r = await api.putEpisodeTarget(ep, WAN_VACE);
    expect(r).toMatchObject({ target: WAN_VACE, target_source: "editor" });
    expect(events).toContain("h3pipe.episode");
    st = await api.episode(ep, "proxy");
    expect(st.target_source).toBe("editor");
    expect(st.shots.find((s) => s.shot === "sh010")!.target).toBe(WAN_VACE);
    expect(st.shots.find((s) => s.shot === "sh010")!.target_source).toBe("episode");
    expect(st.shots.find((s) => s.shot === "sh030")!.target).toBe("ltx2");
    expect(st.shots.find((s) => s.shot === "sh040")!.target).toBe("minimax_h3_ref2va");

    await api.putEpisodeTarget(ep, null);
    st = await api.episode(ep, "proxy");
    expect(st).toMatchObject({ target: "minimax_h3_ref2va", target_source: "series" });
    await expect(api.putEpisodeTarget(ep, "krea2")).rejects.toThrow(/video target/);
  });

  it("skips a shot on a not-ready target, naming the file; records resolved on a degraded one", async () => {
    const api = createMockApi(() => {}, { latency: 0 });
    const ep = (await api.episodes())[0].ep;
    const base = { ep, pass: "proxy" as const, redo: true, seed_mode: "auto" as const, seed: null, model: null, loras: null, steps: null, prompt: null, parent_take: null, note: "", allow_missing_refs: true };
    const r = await api.render({ ...base, shots: ["sh010"], target: LTX_REFS });
    expect(r.queued).toEqual([]);
    expect(r.skipped[0]).toMatchObject({ shot: "sh010", target: LTX_REFS });
    expect(isMissingFileSkip(r.skipped[0])).toBe(true);

    const ok = await api.render({ ...base, shots: ["sh010"], target: WAN_VACE });
    expect(ok.queued).toHaveLength(1);
    const d = await api.shot(ep, "proxy", "sh010");
    const sc = d.takes.find((x) => x.take === ok.queued[0].take)!.sidecar!;
    expect(resolutionNotes(sc.resolved)).toEqual(["rendered with base settings: wan2.2_vace_lightx2v_4steps_lora_high_noise.safetensors missing"]);
  });
});

// Regression (real ep38 data): shots on the episode default come back with
// target_source "default" or "series", not only "episode"; they follow it.
import { targetCounts as _tc } from "../src/lib/readiness";
describe("targetCounts with the server's default/series sources", () => {
  it("counts them as following the episode target", () => {
    const shots = [{ orphan: false, target_source: "default" }, { orphan: false, target_source: "series" },
      { orphan: false, target_source: "episode" }, { orphan: false, target_source: "script" }, { orphan: false, target_source: "override" }] as any;
    expect(_tc(shots)).toEqual({ episode: 3, own: 2, unknown: 0 });
  });
});
