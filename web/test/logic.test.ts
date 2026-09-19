// Badges, grouping, paths, the override form and redo planning.
import { describe, expect, it } from "vitest";
import { planRedo } from "../src/actions";
import { absPath, groupBySequence, sameEp, shotBadges, shotSeconds } from "../src/lib/format";
import { formFromDetail, overrideFields } from "../src/lib/overrideForm";
import type { ShotDetail, ShotStatus, TakeSummary } from "../src/types";

const BIG = "6430499148929255544";

function take(n: number, over: Partial<TakeSummary> = {}): TakeSummary {
  return {
    take: n, status: "ok", has_video: true, seed: BIG, seed_source: "stable", note: "", overrides: [], stale: [],
    thumb: null, strip: null, mp4: `r/sh/t${n}.mp4`, queued: null, finished: null, save_notes: "", ...over,
  };
}

function shot(over: Partial<ShotStatus> = {}): ShotStatus {
  return {
    shot: "sh020", orphan: false, sequence: "sq01", length: 107, seconds: 4.458, size: "medium", subjects: [],
    audio_policy: "generate",
    cut: { take: 1, picked: false, pass: "proxy", placeholder: false, usable: true, trim_in: 0, trim_out: 0, locked: false, note: "", in_cut_file: true },
    override: { fields: [], stale: false }, takes: [take(1)], ...over,
  };
}

const kinds = (s: ShotStatus, r?: Set<number>) => shotBadges(s, r).map((b) => b.kind);

describe("shotBadges", () => {
  it("no badges for a clean shot", () => {
    expect(kinds(shot())).toEqual([]);
  });
  it("stale comes from the cut's take, ignoring 'unknown'", () => {
    expect(shotBadges(shot({ takes: [take(1, { stale: ["script", "ref"] })] }))[0]).toMatchObject({ kind: "stale", label: "stale: script,ref" });
    expect(kinds(shot({ takes: [take(1, { stale: ["unknown"] })] }))).toEqual([]);
    // a stale take the cut doesn't use isn't a shot badge
    expect(kinds(shot({ takes: [take(1), take(2, { stale: ["script"] })] }))).toEqual([]);
  });
  it("override and override stale", () => {
    expect(kinds(shot({ override: { fields: ["prompt"], stale: true } }))).toEqual(["override", "override-stale"]);
  });
  it("queued vs rendering", () => {
    const s = shot({ takes: [take(1), take(2, { status: "queued" }), take(3, { status: "queued" })] });
    expect(kinds(s)).toEqual(["queued"]);
    expect(shotBadges(s)[0].label).toBe("queued ×2");
    expect(kinds(s, new Set([2]))).toEqual(["rendering", "queued"]);
  });
  it("placeholder, no takes, orphan, failed, unusable pick", () => {
    expect(kinds(shot({ takes: [], cut: { ...shot().cut, take: 2, placeholder: true, pass: "final" } }))).toEqual(["placeholder"]);
    expect(kinds(shot({ takes: [], cut: { ...shot().cut, take: null, usable: false } }))).toEqual(["none"]);
    expect(kinds(shot({ orphan: true, sequence: null, takes: [] }))).toContain("orphan");
    expect(kinds(shot({ takes: [take(1, { status: "failed", has_video: false })], cut: { ...shot().cut, take: null, usable: false } }))).toEqual(["failed"]);
    expect(kinds(shot({ cut: { ...shot().cut, picked: true, usable: false } }))).toContain("unusable");
  });
});

describe("groupBySequence", () => {
  it("groups consecutive runs in cut order, a recurring sequence twice", () => {
    const g = groupBySequence([
      shot({ shot: "a", sequence: "sq01", seconds: 1 }),
      shot({ shot: "b", sequence: "sq01", seconds: 2 }),
      shot({ shot: "c", sequence: "sq02", seconds: 3 }),
      shot({ shot: "d", sequence: "sq01", seconds: 4 }),
    ]);
    expect(g.map((x) => [x.sequence, x.shots.map((s) => s.shot).join(""), x.seconds, x.start])).toEqual([
      ["sq01", "ab", 3, 0], ["sq02", "c", 3, 2], ["sq01", "d", 4, 3],
    ]);
  });
  it("with the fps, a shot counts at its cut take's real frames (the timeline)", () => {
    const cut = { ...shot().cut, frames: 199 };
    const shots = [shot({ shot: "a", seconds: 5.042, cut }), shot({ shot: "b", seconds: 2 })];
    expect(groupBySequence(shots, 24)[0].seconds).toBeCloseTo(199 / 24 + 2);
    expect(groupBySequence(shots)[0].seconds).toBeCloseTo(7.042);        // no fps: the build's
    expect(shotSeconds(shots[0], 24)).toBeCloseTo(8.2917, 4);           // a predicted 8.3 s take
    expect(shotSeconds(shots[1], 24)).toBe(2);
    expect(shotSeconds(shot({ cut: { ...cut, frames: null } }), 24)).toBe(4.458);
  });
  it("a cut take at another frame rate counts at its own (a 16 fps Wan take)", () => {
    const cut = { ...shot().cut, frames: 81, fps: 16 };
    expect(shotSeconds(shot({ cut }), 24)).toBe(81 / 16);
    expect(groupBySequence([shot({ cut }), shot({ shot: "b", seconds: 2 })], 24)[0].seconds).toBeCloseTo(81 / 16 + 2);
  });
});

describe("paths", () => {
  it("absPath follows the episode's separator", () => {
    expect(absPath("C:\\Shows\\ep05", "renders_proxy/sh020/sh020_t01.mp4")).toBe("C:\\Shows\\ep05\\renders_proxy\\sh020\\sh020_t01.mp4");
    expect(absPath("/home/me/ep05/", "a/b.mp4")).toBe("/home/me/ep05/a/b.mp4");
  });
  it("sameEp ignores case, slash style and a trailing slash", () => {
    expect(sameEp("C:\\Shows\\EP05", "c:/shows/ep05/")).toBe(true);
    expect(sameEp("C:\\Shows\\ep05", "C:\\Shows\\ep06")).toBe(false);
    expect(sameEp(null, "x")).toBe(false);
  });
});

function detail(over: Partial<ShotDetail> = {}): ShotDetail {
  return {
    shot: "sh020", pass: "proxy", index: 1, built: { seed: BIG, steps: 4 }, built_prompt: "built text",
    override: {}, override_stale: false,
    effective: { prompt: "built text", seed: BIG, seed_source: "stable", model: "m.safetensors", loras: [{ name: "turbo", strength: 1 }], steps: 4 },
    takes: [], ...over,
  };
}

describe("override form", () => {
  it("sends only what changed, seeds as strings", () => {
    const d = detail();
    const init = formFromDetail(d);
    expect(overrideFields(init, init, d)).toEqual({});
    const f = { ...init, prompt: "new text", seed: BIG, steps: "10" };
    const out = overrideFields(f, init, d);
    expect(out).toEqual({ prompt: "new text", seed: BIG, steps: 10 });
    expect(typeof out.seed).toBe("string");
  });
  it("clears with null: empty seed/steps, built LoRAs, prompt back to built", () => {
    const d = detail({ override: { prompt: "mine", seed: "5", steps: 8, loras: [], model: "x" }, effective: { ...detail().effective, prompt: "mine" } });
    const init = formFromDetail(d);
    expect(init).toMatchObject({ prompt: "mine", seed: "5", steps: "8", lorasMode: "custom", loras: [], model: "x" });
    const f = { ...init, prompt: "built text", seed: "", steps: "", lorasMode: "built" as const, model: "" };
    expect(overrideFields(f, init, d)).toEqual({ prompt: null, seed: null, steps: null, loras: null, model: null });
  });
  it("custom LoRAs parse with strength, and bad input throws readably", () => {
    const d = detail();
    const init = formFromDetail(d);
    const f = { ...init, lorasMode: "custom" as const, loras: [{ name: "a", strength: "0.6" }, { name: "", strength: "1" }] };
    expect(overrideFields(f, init, d).loras).toEqual([{ name: "a", strength: 0.6 }]);
    expect(() => overrideFields({ ...init, steps: "0" }, init, d)).toThrow(/Steps/);
    expect(() => overrideFields({ ...f, loras: [{ name: "a", strength: "lots" }] }, init, d)).toThrow(/strength/);
  });
});

describe("planRedo", () => {
  const base = {
    shot: "sh020", pass: "proxy" as const, parent: 2, model: "m.safetensors", loras: [{ name: "turbo", strength: 1 }],
    steps: 4, prompt: "built text", note: "calmer",
  };
  it("new seed, no override: everything in the render request", () => {
    const p = planRedo({ ...base, seed: { mode: "new" }, saveAsOverride: false, prompt: "tweaked" }, "E", detail());
    expect(p.override).toBeNull();
    expect(p.render).toMatchObject({ redo: true, parent_take: 2, seed_mode: "new", seed: null, prompt: "tweaked", steps: 4, note: "calmer", shots: ["sh020"] });
  });
  it("'same as parent' sends the parent's seed as a typed string", () => {
    const p = planRedo({ ...base, seed: { mode: "same", seed: BIG }, saveAsOverride: false }, "E", detail());
    expect(p.render.seed).toBe(BIG);
    expect(p.render.seed_mode).toBe("auto");
  });
  it("save as override: writes only the changes, render carries seed choice only", () => {
    const p = planRedo({ ...base, seed: { mode: "typed", seed: "77" }, steps: 10, prompt: "tweaked", saveAsOverride: true }, "E", detail());
    expect(p.override).toEqual({ prompt: "tweaked", steps: 10, seed: "77" });
    expect(p.render).toMatchObject({ prompt: null, model: null, loras: null, steps: null, seed: "77", parent_take: 2, redo: true });
  });
  it("save as override with nothing changed and a new seed writes no override", () => {
    const p = planRedo({ ...base, seed: { mode: "new" }, saveAsOverride: true }, "E", detail());
    expect(p.override).toBeNull();
    expect(p.render.seed_mode).toBe("new");
  });
});
