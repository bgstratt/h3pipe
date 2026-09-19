// Phase 8.5: keyframes as needed refs (grouping, need/method labels, the
// missing filter, blocking, Generate missing's plan), image targets for refs,
// the inspector's refs-used strip, negative source labels, the ≈ rule, the
// new routes, and the mock that serves them.
import { describe, expect, it } from "vitest";
import { createHttpApi } from "../src/api";
import { ESTIMATE_TITLE, fmtShotSeconds, lengthEstimated } from "../src/lib/format";
import {
  editRefsFor, editRefsText, imageModeText, refDefaults, refTargetOf, refsSnippet,
} from "../src/lib/imageTargets";
import {
  clearKeyframeText, keyframeBlocks, keyframeGroups, keyframeOf, keyframePlan, keyframeWanted, methodLabel, needLabel,
} from "../src/lib/keyframes";
import { negativeNoEffect, negativeSourceLabel, takesNegative } from "../src/lib/negative";
import { formFromDetail, overrideFields } from "../src/lib/overrideForm";
import { blockedShots, canGenerate, groupRefs, missingPlan, refCounts } from "../src/lib/refs";
import { refTiles, refUsedStatus, refsUsedFallback, shotRefTiles } from "../src/lib/refsUsed";
import { createMockApi } from "../src/mock/mockApi";
import { MOCK_TARGETS } from "../src/mock/mockTargets";
import type { EpisodeStatus, Ref, RefTake, ShotStatus, TargetList } from "../src/types";

function ref(id: string, kind: Ref["kind"], over: Partial<Ref> = {}): Ref {
  return {
    id, scope: id.startsWith("shot:") ? "shot" : "series", kind, name: id.split(":").slice(1).join(" "), path: `refs/${id.replace(/:/g, "_")}.png`,
    exists: true, sha1: "x", used_by: { proxy: [], final: [] }, prompt: "", override: { fields: [], stale: false },
    takes: [], picked: null, ...over,
  };
}

const kf = (shot: string, which: "first" | "last", over: Partial<Ref> = {}) =>
  ref(`shot:${shot}:${which}`, "keyframe", { exists: false, shot, which, need: "optional", method: "continuity", target: "ltx2", ...over });

function shot(id: string, over: Partial<ShotStatus> = {}): ShotStatus {
  return {
    shot: id, orphan: false, sequence: "sq01", length: 24, seconds: 1, size: null, subjects: [], audio_policy: null,
    cut: { take: null, picked: false, pass: "proxy", placeholder: false, usable: false, trim_in: 0, trim_out: 0, locked: false, note: "", in_cut_file: false },
    override: { fields: [], stale: false }, takes: [], missing_refs: [], ...over,
  };
}
const usable = { take: 2, picked: false, pass: "proxy" as const, placeholder: false, usable: true, trim_in: 0, trim_out: 0, locked: false, note: "", in_cut_file: true };

describe("keyframes as needed refs", () => {
  const st = { shots: [shot("sh010", { cut: usable }), shot("sh020"), shot("sh030"), shot("sh040")] } as EpisodeStatus;

  it("groups keyframes per shot in cut order, first before last", () => {
    const refs = [kf("sh030", "last"), ref("subject:ada", "character"), kf("sh030", "first"), kf("sh020", "first"), kf("sh999", "first")];
    const g = keyframeGroups(refs, st);
    expect(g.map((x) => [x.shot, x.refs.map((r) => keyframeOf(r)!.which)])).toEqual([
      ["sh020", ["first"]], ["sh030", ["first", "last"]], ["sh999", ["first"]],
    ]);
    expect(g[1].target).toBe("ltx2");
    // the id is enough when the server leaves out shot/which
    expect(keyframeOf({ id: "shot:sh050:last" })).toEqual({ shot: "sh050", which: "last" });
    expect(keyframeOf({ id: "subject:ada" })).toBeNull();
  });

  it("labels need and method", () => {
    expect([needLabel("required"), needLabel("optional"), needLabel(null), needLabel(undefined)]).toEqual(["required", "optional", "", ""]);
    expect(methodLabel("continuity", "first")).toBe("from the previous shot");
    expect(methodLabel("continuity", "last")).toBe("from the next shot");
    expect(methodLabel("generate")).toBe("generate a still");
    expect(methodLabel("import")).toBe("import a file");
    expect(methodLabel("none")).toBe("none (don't use one)");
    expect(methodLabel("refs/shots/sh010/first.png")).toBe("file: refs/shots/sh010/first.png");
    expect(methodLabel(null)).toBe("");
  });

  it("counts a keyframe as missing when required or asked for; required ones block their shot", () => {
    const req = kf("sh020", "first", { need: "required", method: "continuity", target: "wan22_i2v" });
    const opt = kf("sh030", "first");
    const asked = kf("sh030", "last", { requested: true, method: "generate" });
    const none = kf("sh040", "first", { method: "none", requested: false });
    const old = ref("shot:sh040:last", "keyframe", { exists: false }); // an older server: no need
    expect([req, opt, asked, none, old].map(keyframeWanted)).toEqual([true, false, true, false, true]);
    const missing = groupRefs([req, opt, asked, none, old], "missing", "proxy").flatMap((g) => g.refs.map((r) => r.id));
    expect(missing).toEqual(["shot:sh020:first", "shot:sh030:last", "shot:sh040:last"]);
    // "this episode" lists them all
    expect(groupRefs([req, opt, asked, none], "episode", "proxy")[4].refs).toHaveLength(4);
    expect(keyframeBlocks(req)).toBe(true);
    expect(keyframeBlocks(asked)).toBe(false);
    expect(blockedShots(req, st, "proxy")).toEqual(["sh020"]);
    expect(blockedShots(req, undefined, "proxy")).toEqual(["sh020"]);
    expect(blockedShots(opt, st, "proxy")).toEqual([]);
    expect(refCounts([req, opt, asked], st, "proxy")).toEqual({ missing: 2, blocking: 1, shots: 1 });
    expect(clearKeyframeText(req)).toMatch(/sh020 needs one: it can't render/);
    expect(clearKeyframeText(opt)).toMatch(/sh030 then renders without a first keyframe/);
  });

  it("generates keyframes on a Phase 8.5 server only", () => {
    expect(canGenerate(kf("sh020", "first"))).toBe(true);
    expect(canGenerate(ref("shot:sh020:first", "keyframe"))).toBe(false); // no `need`: an older server
    expect(canGenerate(kf("sh020", "first", { can_generate: false }))).toBe(false);
    expect(canGenerate(ref("shot:sh020:first", "keyframe", { can_generate: true }))).toBe(true);
    // the series refs' plan leaves keyframes to keyframePlan
    expect(missingPlan([kf("sh020", "first", { need: "required", used_by: { proxy: ["sh020"] } })], "proxy")).toEqual([]);
  });

  it("plans Generate missing: continuity when the neighbour has a take, else a still", () => {
    const refs = [
      kf("sh020", "first", { need: "required", method: "continuity" }), // sh010 has a usable take
      kf("sh030", "first", { need: "required", method: "continuity" }), // sh020 has none: a still
      kf("sh030", "last", { requested: true, method: "generate" }),
      kf("sh040", "first"), // optional, not asked for
      kf("sh040", "last", { requested: true, method: "import" }), // an import: nothing to queue
      kf("sh010", "first", { need: "required", method: "generate", takes: [{ take: 1, status: "queued" } as RefTake] }), // in flight
      kf("sh010", "last", { need: "required", method: "generate", exists: true }),
    ];
    expect(keyframePlan(refs, st).map((k) => [k.ref, k.mode, k.source ?? null])).toEqual([
      ["shot:sh020:first", "continuity", "sh010"],
      ["shot:sh030:first", "generate", null],
      ["shot:sh030:last", "generate", null],
    ]);
    // a ref that can't be generated and has no continuity source is left out
    expect(keyframePlan([kf("sh030", "first", { need: "required", can_generate: false })], st)).toEqual([]);
  });
});

describe("image targets for refs", () => {
  const list: TargetList = {
    targets: [
      { id: "krea2", kind: "image", label: "Krea 2", capabilities: { mode: "t2i" } },
      { id: "flux2_klein_edit", kind: "image", label: "Flux 2 Klein edit", capabilities: { mode: "edit", max_refs: 2 }, readiness: { status: "ready", missing: [], resolved: {}, features_off: [], nodes_missing: [] } },
    ],
    default: { image: "krea2" },
  };

  it("reads the episode's defaults and their sources from /refs", () => {
    const served = { target: "krea2", target_source: "default" as const, keyframe_target: "flux2_klein_edit", keyframe_target_source: "default" as const };
    expect(refDefaults(list, served)).toEqual({ refs: "krea2", keyframes: "flux2_klein_edit", refsSource: "default", keyframesSource: "default" });
    expect(refDefaults(list, { target: "z_image_turbo", target_source: "editor", keyframe_target: "flux_kontext", keyframe_target_source: "series" }))
      .toEqual({ refs: "z_image_turbo", keyframes: "flux_kontext", refsSource: "editor", keyframesSource: "series" });
    // a series config whose refs block the server can't read: `defaults: null`
    expect(refDefaults(list, null)).toEqual({ refs: "krea2", keyframes: "krea2", refsSource: "default", keyframesSource: "default" });
    expect(imageModeText(list.targets[1])).toBe("edit, up to 2 references");
    expect(imageModeText(list.targets[0])).toBe("text to image");
  });

  it("a ref's image target: its own override, else what the server says it uses, else the default", () => {
    const d = refDefaults(list, { target: "krea2", keyframe_target: "flux2_klein_edit" });
    const own = ref("subject:bo", "character", { override_values: { target: "flux2_klein" }, effective: { target: "flux2_klein" } });
    expect(refTargetOf(own, d)).toEqual({ target: "flux2_klein", source: "override" });
    expect(refTargetOf(ref("subject:ada", "character", { effective: { target: "z_image_turbo" } }), d)).toEqual({ target: "z_image_turbo", source: "effective" });
    expect(refTargetOf(kf("sh020", "first"), d)).toEqual({ target: "flux2_klein_edit", source: "default" });
    expect(refsSnippet("keyframes", "flux_kontext")).toBe('"refs": {"keyframe_target": "flux_kontext"}');
    expect(refsSnippet("refs", "krea2")).toBe('"refs": {"target": "krea2"}');
  });

  it("says which references an edit target gets: exactly the server's edit_refs", () => {
    const refs = [ref("subject:ada", "character", { name: "Ada" }), ref("location:kitchen", "location", { name: "kitchen" })];
    const edit = list.targets[1];
    const plan = editRefsFor(kf("sh020", "first", {
      edit_refs: [
        { id: "subject:ada", role: "subject", view: "04_face", path: "refs/_takes/subject__ada/subject__ada_04_face_t01.png" },
        { id: "location:kitchen", role: "plate", path: "refs/_bg/kitchen.png" },
      ],
    }), edit, refs);
    expect(plan.refs.map((x) => x.label)).toEqual(["Ada (face)", "kitchen (plate)"]);
    expect(editRefsText(list, "flux2_klein_edit", plan)).toBe("Flux 2 Klein edit with Ada (face), kitchen (plate)");
    expect(editRefsFor(kf("sh020", "first"), edit, refs).refs).toEqual([]); // nothing picked yet
    expect(editRefsText(list, "flux2_klein_edit", editRefsFor(kf("sh020", "first"), edit, refs))).toMatch(/no reference images yet/);
    expect(editRefsFor(kf("sh020", "first", { edit_refs: [{ id: "location:kitchen" }] }), list.targets[0], refs).refs).toEqual([]); // t2i
  });

});

describe("refs this shot uses", () => {
  it("orders tiles by role and gives each a status", () => {
    const tiles = refTiles([
      { id: "shot:sh020:first", kind: "keyframe", role: "first", path: "refs/shots/sh020/first.png", exists: false, need: "required" },
      { id: "location:kitchen", kind: "location", role: "plate", path: "refs/_bg/kitchen.png", exists: true },
      { id: "sheet:sh020", kind: "reference_sheet", role: "reference_sheet", path: "refs/_sheets/sh020.png", exists: true, thumb: "refs/_sheets/sh020_small.png" },
      { id: "subject:ada", kind: "character", role: "subject", path: "refs/ada.png", exists: false },
      { id: "shot:sh020:last", kind: "keyframe", role: "last", path: "refs/shots/sh020/last.png", exists: false, need: "optional" },
    ], [ref("subject:ada", "character", { name: "Ada", exists: false })]);
    expect(tiles.map((t) => [t.role, t.label, t.status, t.statusText])).toEqual([
      ["subject", "Ada", "missing", "missing"],
      ["plate", "kitchen", "live", "live"],
      ["first", "first frame", "required", "required: missing"],
      ["last", "last frame", "missing", "none (optional)"],
      ["reference_sheet", "reference sheet", "live", "live"],
    ]);
    expect(tiles[4].image).toBe("refs/_sheets/sh020_small.png"); // thumb over path
    expect(tiles[0].image).toBeNull();
    expect(tiles.filter((t) => t.keyframe).map((t) => t.role)).toEqual(["first", "last"]);
    expect(refUsedStatus({ exists: true, need: "required" })).toBe("live");
  });

  it("takes the newer file state from the refs list (a pick or a clear since the shot loaded)", () => {
    const [t] = refTiles([{ id: "shot:sh020:first", kind: "keyframe", role: "first", path: "p.png", exists: true, need: "required" }],
      [kf("sh020", "first", { need: "required", exists: false })]);
    expect(t.status).toBe("required");
  });

  it("falls back to used_by and the shot's keyframes on an older server", () => {
    const refs = [
      ref("subject:ada", "character", { used_by: { proxy: ["sh020"] } }),
      ref("location:kitchen", "location", { used_by: { proxy: ["sh020"] } }),
      ref("voice:ada", "voice", { used_by: { proxy: ["sh020"] } }),
      ref("subject:bo", "character", { used_by: { proxy: ["sh030"] } }),
      kf("sh020", "first", { exists: true }),
    ];
    expect(refsUsedFallback(refs, "sh020", "proxy").map((u) => [u.id, u.role])).toEqual([
      ["subject:ada", "subject"], ["location:kitchen", "plate"], ["shot:sh020:first", "first"],
    ]);
    expect(shotRefTiles(undefined, refs, "sh020", "proxy").exact).toBe(false);
    expect(shotRefTiles([], refs, "sh020", "proxy")).toEqual({ tiles: [], exact: true });
  });
});

describe("negatives", () => {
  it("labels where the negative comes from", () => {
    expect(negativeSourceLabel("negative.txt")).toBe("from negative.txt");
    expect(negativeSourceLabel("none")).toBe(""); // the target takes no negative
    expect(negativeSourceLabel("series")).toBe("from series.json");
    expect(negativeSourceLabel("preset")).toBe("target default");
    expect(negativeSourceLabel("override")).toBe("shot override");
    expect(negativeSourceLabel("request")).toBe("this run");
    expect(negativeSourceLabel(null)).toBe("");
    expect(negativeSourceLabel("something_new")).toBe("something_new");
  });
  it("shows the field for targets that take one; notes cfg ≤ 1", () => {
    expect(takesNegative({ capabilities: { negative_prompt: true } })).toBe(true);
    expect(takesNegative({ capabilities: { negative_prompt: false } }, { negative: "x" })).toBe(false);
    expect(takesNegative(undefined, { negative_source: "series" })).toBe(true);
    expect(takesNegative(undefined, {})).toBe(false);
    expect([negativeNoEffect(1), negativeNoEffect(3.5), negativeNoEffect(undefined)]).toEqual([true, false, false]);
  });
  it("sends negative and model_low only when changed; empty clears", () => {
    const d = { override: { negative: "old", model_low: "a.safetensors" }, effective: { prompt: "p" }, built_prompt: "p" };
    const init = formFromDetail(d);
    expect(init).toMatchObject({ negative: "old", modelLow: "a.safetensors" });
    expect(overrideFields({ ...init }, init, d)).toEqual({});
    expect(overrideFields({ ...init, negative: " ", modelLow: "" }, init, d)).toEqual({ negative: null, model_low: null });
    expect(overrideFields({ ...init, negative: "blurry" }, init, d)).toEqual({ negative: "blurry" });
  });
});

describe("the ≈ rule", () => {
  it("marks a dur: model shot until a real take's length is known", () => {
    const s = shot("sh030", { length_estimated: true, seconds: 3.04 });
    expect(lengthEstimated(s)).toBe(true);
    expect(fmtShotSeconds(s, 3.04)).toBe("≈3.0s");
    expect(lengthEstimated({ ...s, cut: { ...s.cut, frames: 88 } })).toBe(false); // the take's frames win
    expect(lengthEstimated({ ...s, length_estimated: false })).toBe(false);
    expect(lengthEstimated(shot("sh010"))).toBe(false); // an older server
    expect(fmtShotSeconds(shot("sh010"), 3.04)).toBe("3.0s");
    expect(ESTIMATE_TITLE).toBe("length decided by the model at render time (dur: model); showing the estimate");
  });
});

describe("Phase 8.5 routes", () => {
  it("DELETE /h3pipe/refs/pick, and generate's target only when set", async () => {
    const calls: { method: string; path: string; body: string }[] = [];
    const api = createHttpApi({
      fetch: async (path, init) => {
        calls.push({ method: String(init?.method), path, body: String(init?.body) });
        return new Response(path.startsWith("/h3pipe/refs/pick") ? `{"id":"shot:sh020:first","exists":false,"takes":[],"picked":null}` : `{"queued":[],"errors":[]}`, { status: 200 });
      },
      url: (p) => p,
    });
    const r = await api.refsUnpick("C:\\ep05", "shot:sh020:first");
    expect(r?.id).toBe("shot:sh020:first");
    expect(calls[0]).toMatchObject({ method: "DELETE", path: "/h3pipe/refs/pick?ep=C%3A%5Cep05&ref=shot%3Ash020%3Afirst" });
    await api.refsUnpick("ep", "subject:ada", "04_face");
    expect(calls[1].path).toBe("/h3pipe/refs/pick?ep=ep&ref=subject%3Aada&view=04_face");
    const base = { ep: "ep", ref: "subject:ada", view: null, count: 1, seed_mode: "auto" as const, seed: null, prompt: null, model: null, loras: null, steps: null, note: "" };
    await api.refsGenerate(base);
    expect(JSON.parse(calls[2].body)).not.toHaveProperty("target");
    await api.refsGenerate({ ...base, target: "z_image_turbo" });
    expect(JSON.parse(calls[3].body).target).toBe("z_image_turbo");
  });
});

describe("mock: Phase 8.5", () => {
  it("lists keyframes each shot's target needs, with need, method and target", async () => {
    const api = createMockApi(() => {}, { latency: 0 });
    const ep = (await api.episodes())[0].ep;
    const { refs, defaults } = await api.refs(ep);
    expect(defaults).toEqual({ target: "krea2", target_source: "default", keyframe_target: "flux2_klein_edit", keyframe_target_source: "default" });
    const k = (id: string) => refs.find((r) => r.id === id)!;
    expect(k("shot:sh060:first")).toMatchObject({ need: "required", method: "continuity", target: "wan22_i2v", exists: false, can_generate: true });
    expect(k("shot:sh070:first")).toMatchObject({ need: "required", exists: true, picked: 1 });
    expect(k("shot:sh070:first").takes[0]).toMatchObject({ source: "generated", target: "flux2_klein_edit" });
    expect(k("shot:sh030:first")).toMatchObject({ need: "optional", method: "continuity", target: "ltx2", requested: true });
    expect(k("shot:sh080:first")).toMatchObject({ need: "optional", method: "generate", target: "minimax_h3_fl2va", requested: true });
    expect(k("shot:sh090:first").method).toBe("none");
    expect(refs.some((r) => r.id === "shot:sh010:first")).toBe(false); // H3 reads none

    const st = await api.episode(ep, "proxy");
    const sh060 = st.shots.find((s) => s.shot === "sh060")!;
    expect(sh060.missing_refs!.find((m) => m.slot === "First frame")).toMatchObject({ anyway: false });
    expect(st.shots.find((s) => s.shot === "sh030")!.length_estimated).toBe(false); // sh030 has takes
    expect(st.shots.find((s) => s.shot === "sh080")!.length_estimated).toBe(true);
    // Generate missing: sh030 first from sh020's take, sh060 first as a still (sh050 has none), sh080 first as a still
    expect(keyframePlan(refs, st).map((x) => [x.ref, x.mode])).toEqual([
      ["shot:sh030:first", "continuity"], ["shot:sh060:first", "generate"], ["shot:sh080:first", "generate"],
    ]);
  });

  it("won't render a shot missing a required keyframe, even anyway", async () => {
    const api = createMockApi(() => {}, { latency: 0 });
    const ep = (await api.episodes())[0].ep;
    const r = await api.render({
      ep, pass: "proxy", shots: ["sh060"], redo: true, seed_mode: "auto", seed: null, model: null, loras: null, steps: null,
      prompt: null, parent_take: null, note: "", allow_missing_refs: true,
    });
    expect(r.queued).toEqual([]);
    expect(r.skipped[0].missing_refs!.some((m) => m.anyway === false)).toBe(true);
  });

  it("clears a keyframe (h3pipe.ref cleared), generates one with a target, serves refs_used", async () => {
    const events: [string, unknown][] = [];
    const api = createMockApi((e, d) => events.push([e, d]), { latency: 0 });
    const ep = (await api.episodes())[0].ep;
    const cleared = await api.refsUnpick(ep, "shot:sh070:first");
    expect(cleared).toMatchObject({ exists: false, picked: null });
    expect(events.map(([e, d]) => (e === "h3pipe.ref" ? (d as { status: string }).status : e))).toEqual(["cleared", "h3pipe.episode"]);
    const d = await api.shot(ep, "proxy", "sh070");
    expect(d.refs_used!.find((u) => u.role === "first")).toMatchObject({ exists: false, need: "required" });
    const g = await api.refsGenerate({
      ep, ref: "shot:sh080:first", view: null, count: 1, seed_mode: "auto", seed: null, prompt: null, model: null, loras: null, steps: null, note: "", target: "z_image_turbo",
    });
    expect(g.queued).toHaveLength(1);
    const listed = (await api.refs(ep)).refs.find((r) => r.id === "shot:sh080:first")!;
    expect(listed.takes[0]).toMatchObject({ status: "queued", target: "z_image_turbo" });
    // H3 reads subjects and a plate; sh010's t01 kept its reference sheet
    const d10 = await api.shot(ep, "proxy", "sh010");
    expect(d10.refs_used!.map((u) => u.role)).toContain("plate");
    expect(d10.refs_used!.some((u) => u.role === "first")).toBe(false);
    expect(d10.takes.find((t) => t.take === 1)!.reference_image).toBe("refs/_sheets/sh010_refsheet.png");
  });

  it("serves image targets with readiness, negatives and the low-noise model", async () => {
    const api = createMockApi(() => {}, { latency: 0 });
    const ep = (await api.episodes())[0].ep;
    const t = await api.targets({ ready: true });
    const img = Object.fromEntries(t.targets.filter((x) => x.kind === "image").map((x) => [x.id, [x.capabilities?.mode, x.readiness?.status]]));
    expect(img).toEqual({
      krea2: ["t2i", "ready"], z_image_turbo: ["t2i", "ready"], flux2_klein: ["t2i", "ready"],
      flux2_klein_edit: ["edit", "ready"], flux_kontext: ["edit", "not_ready"], illustrious_sdxl: ["t2i", "degraded"],
    });
    const d = await api.shot(ep, "proxy", "sh070");
    expect(d.effective).toMatchObject({ negative_source: "negative.txt", model_low: "wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors" });
    await api.putOverride({ ep, pass: "proxy", shot: "sh070", both: false, fields: { negative: "no text", model_low: "my_wan_low_merge.safetensors" } });
    const d2 = await api.shot(ep, "proxy", "sh070");
    expect(d2.effective).toMatchObject({ negative: "no text", negative_source: "override", model_low: "my_wan_low_merge.safetensors" });
    const low = await api.modelFiles("wan22_i2v", "model_low");
    expect(low.files.map((f) => [f.name, f.match, f.mismatch])).toEqual([
      ["wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors", "name", false],
      ["wan2.2_i2v_low_noise_14B_fp16.safetensors", "name", false],
      ["my_wan_low_merge.safetensors", "fingerprint", false],
      ["wan2.2_fun_vace_low_noise_14B_fp8_scaled.safetensors", "other", true],
      ["wan2.2_t2v_low_noise_14B_fp8_scaled.safetensors", "other", true],
    ]);
    // the fixtures' H3 target has no negative
    expect(MOCK_TARGETS.targets.find((x) => x.id === "minimax_h3_ref2va")!.capabilities?.negative_prompt).toBeUndefined();
  });
});
