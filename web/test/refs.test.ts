// Refs grouping and filters, missing-refs summaries, render reports, browse helpers.
import { describe, expect, it } from "vitest";
import { renderReport } from "../src/actions";
import { crumbs, depthBelow, reachable } from "../src/lib/browse";
import { shotBadges } from "../src/lib/format";
import { missingRefsSummary, missingRefsTitle, splitByMissingRefs } from "../src/lib/missingRefs";
import { blockedShots, canGenerate, groupOf, groupRefs, hasViews, refCounts, unpickedViews, usedBy } from "../src/lib/refs";
import type { EpisodeStatus, MissingRef, Ref, ShotStatus } from "../src/types";

function ref(id: string, kind: Ref["kind"], over: Partial<Ref> = {}): Ref {
  return {
    id, scope: id.startsWith("shot:") ? "shot" : "series", kind, name: id.split(":")[1], path: `refs/${id.split(":")[1]}.png`,
    exists: true, sha1: "x", used_by: { proxy: [], final: [] }, prompt: "", override: { fields: [], stale: false },
    takes: [], picked: null, ...over,
  };
}

function shot(id: string, missing: MissingRef[] = [], over: Partial<ShotStatus> = {}): ShotStatus {
  return {
    shot: id, orphan: false, sequence: "sq01", length: 24, seconds: 1, size: null, subjects: [], audio_policy: null,
    cut: { take: null, picked: false, pass: "proxy", placeholder: false, usable: false, trim_in: 0, trim_out: 0, locked: false, note: "", in_cut_file: false },
    override: { fields: [], stale: false }, takes: [], missing_refs: missing, ...over,
  };
}

const M = (slot: string, path: string, kind: MissingRef["kind"] = "image", subject?: string): MissingRef => ({ slot, path, kind, ...(subject ? { subject } : {}) });

describe("refs grouping", () => {
  const refs = [
    ref("subject:bo", "character", { exists: false, used_by: { proxy: ["sh010"] } }),
    ref("subject:ada", "character", { used_by: { proxy: ["sh010", "sh020"] } }),
    ref("subject:van", "vehicle"),
    ref("subject:kettle", "prop", { exists: false }),
    ref("location:street", "location", { exists: false, used_by: { proxy: ["sh030"], final: [] } }),
    ref("voice:ada", "voice", { used_by: { proxy: ["sh010"] } }),
    ref("shot:sh010:first", "keyframe"),
  ];
  it("puts each kind in its group, in a fixed order, sorted by name", () => {
    const g = groupRefs(refs, "all", "proxy");
    expect(g.map((x) => x.label)).toEqual(["Characters", "Props & vehicles", "Locations", "Voices", "Shot keyframes"]);
    expect(g.map((x) => x.refs.map((r) => r.id))).toEqual([
      ["subject:ada", "subject:bo"], ["subject:kettle", "subject:van"], ["location:street"], ["voice:ada"], ["shot:sh010:first"],
    ]);
    expect(groupOf({ kind: "character", scope: "shot" })).toBe("keyframes");
  });
  it("filters: used by this episode (per pass), all, missing only; totals kept", () => {
    const used = groupRefs(refs, "episode", "proxy");
    expect(used.flatMap((x) => x.refs.map((r) => r.id))).toEqual(["subject:ada", "subject:bo", "location:street", "voice:ada"]);
    expect(used[0].total).toBe(2);
    expect(groupRefs(refs, "episode", "final").flatMap((x) => x.refs)).toEqual([]);
    expect(groupRefs(refs, "missing", "proxy").flatMap((x) => x.refs.map((r) => r.id))).toEqual(["subject:bo", "subject:kettle", "location:street"]);
    expect(groupRefs([], "all", "proxy").every((x) => x.refs.length === 0)).toBe(true);
    expect(usedBy(ref("a:b", "prop", { used_by: {} }), "proxy")).toEqual([]);
  });
  it("blocks: shots whose missing_refs name the ref's file (slashes and case aside)", () => {
    const st = { shots: [shot("sh010", [M("Picture 2", "refs\\BO.png")]), shot("sh020"), shot("sh030", [M("Picture 1", "refs/street.png")])] } as EpisodeStatus;
    expect(blockedShots(refs[0], st, "proxy")).toEqual(["sh010"]);
    expect(blockedShots(refs[4], st, "proxy")).toEqual(["sh030"]);
    expect(blockedShots(refs[3], st, "proxy")).toEqual([]); // missing but unused
    // no status loaded: a missing ref blocks what uses it
    expect(blockedShots(refs[4], undefined, "proxy")).toEqual(["sh030"]);
    expect(blockedShots(refs[1], undefined, "proxy")).toEqual([]);
    expect(refCounts(refs, st, "proxy")).toEqual({ missing: 3, blocking: 2, shots: 2 });
  });
  it("views still to pick; what can be generated", () => {
    const r = ref("subject:bo", "character", {
      views: [{ view: "01_threequarter", picked: 2, takes: [] }, { view: "03_back", picked: null, takes: [] }],
    });
    expect(unpickedViews(r)).toEqual(["02_side", "03_back", "04_face"]);
    expect(unpickedViews(ref("subject:van", "vehicle"))).toEqual([]);
    expect(canGenerate(refs[0])).toBe(true);
    expect(canGenerate(refs[5])).toBe(false);
    expect(canGenerate(refs[6])).toBe(false);
  });
});

describe("missing refs", () => {
  const shots = [
    shot("sh010", [M("Picture 1", "refs/_bg/street.png"), M("Audio 1", "audio/voices/bo.wav", "audio", "bo")]),
    shot("sh020"),
    shot("sh030", [M("Picture 1", "refs/_bg/street.png")]),
    shot("sh040", [M("Picture 2", "refs/cy/cy_sheet.png", "image", "cy")], { orphan: true }),
    shot("sh050", undefined, { missing_refs: undefined }),
  ];
  it("summarises the episode: blocked shots and the files blocking most first", () => {
    const s = missingRefsSummary(shots);
    expect(s.shots).toEqual(["sh010", "sh030"]); // orphans don't count
    expect(s.files.map((f) => [f.path, f.shots])).toEqual([
      ["refs/_bg/street.png", ["sh010", "sh030"]],
      ["audio/voices/bo.wav", ["sh010"]],
    ]);
    expect(s.text).toBe("2 shots are missing refs (2 files)");
    expect(missingRefsSummary([shots[2]]).text).toBe("1 shot is missing refs (1 file)");
    expect(missingRefsSummary([shots[1]]).text).toBe("");
  });
  it("splits a render request into ready and skipped", () => {
    expect(splitByMissingRefs(shots, ["sh010", "sh020", "sh050", "nope"])).toEqual({
      ready: ["sh020", "sh050", "nope"],
      blocked: [{ shot: "sh010", refs: shots[0].missing_refs }],
    });
  });
  it("a missing refs badge lists slots and paths", () => {
    const b = shotBadges(shots[0]).find((x) => x.kind === "missing-refs")!;
    expect(b.label).toBe("missing refs");
    expect(b.title).toContain("Picture 1: refs/_bg/street.png");
    expect(b.title).toContain("Audio 1: audio/voices/bo.wav (bo)");
    expect(shotBadges(shots[1]).some((x) => x.kind === "missing-refs")).toBe(false);
    expect(missingRefsTitle([])).toBe("");
  });
  it("reports skipped shots from the render response, missing refs apart", () => {
    const toasts = renderReport({
      queued: [{ shot: "sh020", take: 2, prompt_id: "p", seed: "1", seed_source: "new" }],
      skipped: [
        { shot: "sh010", reason: "missing refs", missing_refs: [M("Picture 1", "refs/_bg/street.png")] },
        { shot: "sh030", reason: "has a usable take (pass redo: true)" },
      ],
      errors: [],
    });
    expect(toasts.map((t) => [t.severity, t.summary])).toEqual([
      ["success", "Queued 1 take"],
      ["warn", "Skipped 1 shot: missing refs"],
      ["info", "Skipped 1 shot"],
    ]);
    expect(toasts[1].detail).toContain("sh010: Picture 1 refs/_bg/street.png");
    expect(toasts[2].detail).toBe("sh030: has a usable take (pass redo: true)");
    expect(renderReport({ queued: [], skipped: [{ shot: "a", reason: "why" }], errors: [] })[0]).toMatchObject({ severity: "warn", summary: "Nothing queued" });
  });
});

describe("browse helpers", () => {
  it("breadcrumbs for Windows, POSIX and UNC paths", () => {
    expect(crumbs("C:\\Users\\me")).toEqual([
      { label: "C:\\", path: "C:\\" }, { label: "Users", path: "C:\\Users" }, { label: "me", path: "C:\\Users\\me" },
    ]);
    expect(crumbs("D:\\")).toEqual([{ label: "D:\\", path: "D:\\" }]);
    expect(crumbs("/home/me")).toEqual([{ label: "/", path: "/" }, { label: "home", path: "/home" }, { label: "me", path: "/home/me" }]);
    expect(crumbs("\\\\nas\\share\\shows").map((c) => c.path)).toEqual(["\\\\nas\\share\\", "\\\\nas\\share\\shows"]);
    expect(crumbs("")).toEqual([]);
  });
  it("an episode is reachable up to two levels below a root", () => {
    expect(depthBelow("C:\\Shows\\Dean\\ep05", "c:/shows/")).toBe(2);
    expect(depthBelow("C:\\Showsx\\ep05", "C:\\Shows")).toBe(-1);
    expect(reachable("C:\\Shows\\Dean\\ep05", ["C:\\Shows"])).toBe(true);
    expect(reachable("C:\\Shows\\Dean\\ep05", ["C:\\"])).toBe(false);
    expect(reachable("C:\\Shows\\Dean\\ep05", [])).toBe(false);
  });
});

// Regression: the real server sends `path: null` (and `prompt: null`) for a
// voice-only character with no sheet in the series config. The Refs tab crashed on it.
describe("a ref whose series config entry names no file", () => {
  const narrator = ref("subject:narrator", "character", {
    path: null, exists: false, sha1: null, prompt: null, can_generate: false,
    why_not: "no `sheet` path in series.json",
  });
  const st = { shots: [shot("sh010", [M("Picture 4", "refs/_bg/x.png")])] } as unknown as EpisodeStatus;
  it("groups, counts and blocks nothing without throwing", () => {
    expect(() => groupRefs([narrator], "all", "proxy")).not.toThrow();
    expect(() => groupRefs([narrator], "missing", "proxy")).not.toThrow();
    expect(blockedShots(narrator, st, "proxy")).toEqual([]);
    expect(() => refCounts([narrator], st, "proxy")).not.toThrow();
  });
  it("can't be generated when the server says so", () => {
    expect(canGenerate(narrator)).toBe(false);
    expect(canGenerate(ref("subject:ada", "character"))).toBe(true);
  });
});

// Regression: the server sends `views: []` for every ref that isn't a character.
// Testing `!!r.views` treated props and locations as four-view characters (a
// view dropdown and no candidate grid).
describe("single-image refs as the server sends them", () => {
  const plate = ref("location:castle_garden", "location", { views: [] });
  const bumble = ref("subject:bumble", "character", {
    views: ["01_threequarter", "02_side", "03_back", "04_face"].map((view) => ({ view, picked: null, takes: [] })) as any,
  });
  it("has no views", () => {
    expect(hasViews(plate)).toBe(false);
    expect(hasViews(ref("subject:van", "vehicle"))).toBe(false);   // field absent
    expect(hasViews(bumble)).toBe(true);
  });
  it("never reports views to pick for a single-image ref", () => {
    expect(unpickedViews(plate)).toEqual([]);
    expect(unpickedViews(bumble)).toHaveLength(4);
  });
});
