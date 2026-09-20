// Phase 9b: timeline editing. The cut's pure maths (reorder, trims, undo, the
// recording under the cut, peaks), then the edits end to end on the mock API.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, createHttpApi, type Transport } from "../src/api";
import { currentPlaylist, refreshEpisode } from "../src/actions";
import {
  copyCut, cutKey, cutSettled, isTyping, lockedPickRefusal, moveClip, nudgeClip, redoCut, resetCut, resetCutHistory,
  seekCutAt, setTrims, shuttle, toggleLock, trimAtPlayhead, undoCut, undoStack,
} from "../src/cutActions";
import { setApi, setHost, type Host } from "../src/host";
import {
  anyOutOfOrder, applyEntries, applyOrder, clampTrim, clearTrims, dropIndex, entriesOf, entryOf, framesLabel, moveTo, nudge,
  outOfOrder, pxToFrames, sameEntries, scriptOrder, trimLimits, withFields,
} from "../src/lib/cutEdit";
import {
  MAX_BINS_PER_SECOND, PeaksCache, binsFor, binsPerSecond, drawPeaks, peakBars, peaksKey, resamplePeaks,
} from "../src/lib/peaks";
import { baseIn, buildPlaylist, spanOf, windowOf } from "../src/lib/playlist";
import { expectedAt, masterStart, masterSync, needsResync, recordingAt, trackSlice, trackState } from "../src/lib/recording";
import { UndoStack, applyEdit, diffEdit } from "../src/lib/undo";
import { createMockApi } from "../src/mock/mockApi";
import { checkEntries, copyEntries, materialize, resetEntries, resolveCut } from "../src/mock/mockCut";
import { initialState, statusKey, store } from "../src/store";
import { parseFrames } from "../src/components/CutSection";
import { waveSource } from "../src/components/Waveform";
import type { CutEntry, CutInfo, EpisodeStatus, ShotStatus, TakeSummary } from "../src/types";

function take(n: number, over: Partial<TakeSummary> = {}): TakeSummary {
  return {
    take: n, status: "ok", has_video: true, seed: "1", seed_source: "stable", note: "", overrides: [], stale: [],
    thumb: null, strip: null, mp4: `r/sh/t${n}.mp4`, queued: null, finished: null, save_notes: "", ...over,
  };
}

const CUT: CutInfo = { take: 1, picked: false, pass: "proxy", placeholder: false, usable: true, trim_in: 0, trim_out: 0, locked: false, note: "", in_cut_file: false };

function shot(id: string, length = 48, over: Partial<ShotStatus> = {}, cut: Partial<CutInfo> = {}): ShotStatus {
  return {
    shot: id, orphan: false, sequence: "sq01", length, seconds: length / 24, size: null, subjects: [], audio_policy: null,
    cut: { ...CUT, ...cut }, override: { fields: [], stale: false }, takes: [take(1, { mp4: `r/${id}/t1.mp4`, frames: length, audio: `r/${id}/t1.mp4` })], ...over,
  };
}

function ep(shots: ShotStatus[], over: Partial<EpisodeStatus> = {}): EpisodeStatus {
  return { episode: "ep", title: "", pass: "proxy", fps: 24, width: 448, height: 256, folder: "renders_proxy", shots, ...over };
}

const ids = (xs: { shot: string }[]) => xs.map((x) => x.shot);
const L = ["a", "b", "c", "d"].map((shot) => ({ shot }));

// ---------------------------------------------------------------------------

describe("reorder", () => {
  it("moveTo takes an insertion point in the list as it is", () => {
    expect(ids(moveTo(L, "a", 2)!)).toEqual(["b", "a", "c", "d"]);
    expect(ids(moveTo(L, "a", 4)!)).toEqual(["b", "c", "d", "a"]);
    expect(ids(moveTo(L, "d", 0)!)).toEqual(["d", "a", "b", "c"]);
    expect(ids(moveTo(L, "c", 1)!)).toEqual(["a", "c", "b", "d"]);
  });
  it("moveTo: the gaps either side of the clip, or an unknown shot, change nothing", () => {
    expect(moveTo(L, "b", 1)).toBeNull();
    expect(moveTo(L, "b", 2)).toBeNull();
    expect(moveTo(L, "zz", 0)).toBeNull();
    // clamped to the ends
    expect(ids(moveTo(L, "b", 99)!)).toEqual(["a", "c", "d", "b"]);
    expect(ids(moveTo(L, "b", -5)!)).toEqual(["b", "a", "c", "d"]);
  });
  it("nudge moves one place and stops at the ends (Alt+arrows)", () => {
    expect(ids(nudge(L, "b", -1)!)).toEqual(["b", "a", "c", "d"]);
    expect(ids(nudge(L, "b", 1)!)).toEqual(["a", "c", "b", "d"]);
    expect(nudge(L, "a", -1)).toBeNull();
    expect(nudge(L, "d", 1)).toBeNull();
  });
  it("applyOrder keeps shots the order doesn't name after their neighbour, ignores unknown ones", () => {
    const list = ["a", "b", "new", "c"].map((shot) => ({ shot }));
    expect(ids(applyOrder(list, ["c", "b", "a", "gone"]))).toEqual(["c", "b", "new", "a"]);
    expect(ids(applyOrder([{ shot: "x" }, ...L], ["b", "a"]))).toEqual(["x", "b", "c", "d", "a"]);
  });
  it("dropIndex picks the nearest gap from the clips' edges", () => {
    const edges = [{ left: 0, right: 100 }, { left: 102, right: 150 }, { left: 152, right: 300 }];
    expect(dropIndex(edges, -40)).toBe(0);
    expect(dropIndex(edges, 30)).toBe(0);
    expect(dropIndex(edges, 80)).toBe(1);
    expect(dropIndex(edges, 140)).toBe(2);
    expect(dropIndex(edges, 290)).toBe(3);
    expect(dropIndex([], 5)).toBe(0);
  });
  it("outOfOrder flags the fewest moved shots, never orphans", () => {
    expect(outOfOrder([0, 1, 2, 3])).toEqual([false, false, false, false]);
    // one clip dragged to the end
    expect(outOfOrder([1, 2, 3, 0])).toEqual([false, false, false, true]);
    expect(outOfOrder([0, 3, 1, 2])).toEqual([false, true, false, false]);
    expect(outOfOrder([0, null, 2, 1])).toEqual([false, false, true, false]);
    expect(outOfOrder([])).toEqual([]);
  });
  it("entriesOf writes back exactly what the status came from (h3takes.cut_entry_to_json)", () => {
    const st = ep([
      shot("a"),
      shot("b", 48, {}, { picked: true, take: 2, trim_in: 3, locked: true, note: "keep" }),
      shot("c", 48, {}, { placeholder: true, pass: "final", picked: true, take: 4 }),
      shot("d", 48, {}, { placeholder: true, pass: "final", picked: false, take: 9 }),
      shot("e", 48, {}, { trim_in: -2, trim_out: 2.7 }),
    ]);
    expect(entriesOf(st)).toEqual([
      { shot: "a" },
      { shot: "b", take: 2, trim_in: 3, locked: true, note: "keep" },
      { shot: "c", pass: "final", take: 4 },
      { shot: "d", pass: "final" },
      { shot: "e", trim_out: 2 },
    ]);
    // real data: a cut without the optional fields
    expect(entryOf({ ...shot("z"), cut: { take: null, picked: false, pass: "proxy" } as unknown as CutInfo }, "proxy")).toEqual({ shot: "z" });
  });
  it("withFields / clearTrims set trims and locks, leaving zero and false out", () => {
    const e: CutEntry[] = [{ shot: "a", take: 2, trim_in: 4, note: "n" }, { shot: "b" }];
    expect(withFields(e, "a", { trim_in: 0, locked: true })).toEqual([{ shot: "a", take: 2, locked: true, note: "n" }, { shot: "b" }]);
    expect(withFields(e, "b", { trim_out: 7.9 })[1]).toEqual({ shot: "b", trim_out: 7 });
    expect(clearTrims(e)).toEqual([{ shot: "a", take: 2, note: "n" }, { shot: "b" }]);
    expect(sameEntries(e, clearTrims(clearTrims(e)))).toBe(false);
    expect(sameEntries(e, e.map((x) => ({ ...x })))).toBe(true);
  });
  it("applyEntries is the status after the save: order, trims, locks, out_of_order", () => {
    const st = ep(["a", "b", "c"].map((id, i) => shot(id, 48, {}, { script_index: i, order: i, out_of_order: false })));
    const next = applyEntries(st, [{ shot: "c" }, { shot: "a", trim_in: 5 }, { shot: "b", locked: true }]);
    expect(ids(next.shots)).toEqual(["c", "a", "b"]);
    expect(next.shots.map((s) => s.cut.order)).toEqual([0, 1, 2]);
    expect(next.shots.map((s) => s.cut.out_of_order)).toEqual([true, false, false]);
    expect(next.shots[1].cut.trim_in).toBe(5);
    expect(next.shots[2].cut.locked).toBe(true);
    expect(st.shots[0].shot).toBe("a"); // not mutated
    expect(anyOutOfOrder(next)).toBe(true);
    expect(anyOutOfOrder(st)).toBe(false);
    expect(scriptOrder(next)).toEqual(["a", "b", "c"]);
    // an older server: no script_index, no order
    expect(scriptOrder(ep([shot("a")]))).toBeNull();
    expect(applyEntries(ep([shot("a"), shot("b")]), [{ shot: "b" }, { shot: "a" }]).shots[0].cut.order).toBeUndefined();
  });
});

describe("trims", () => {
  it("pxToFrames snaps pointer pixels to whole frames at the zoom", () => {
    expect(pxToFrames(40, 40, 24)).toBe(24);
    expect(pxToFrames(1, 40, 24)).toBe(1); // 0.6 frame rounds to 1
    expect(pxToFrames(0.8, 40, 24)).toBe(0);
    expect(Object.is(pxToFrames(-0.2, 40, 24), 0)).toBe(true);
    expect(pxToFrames(-20, 40, 24)).toBe(-12);
    expect(pxToFrames(10, 0, 24)).toBe(0);
  });
  it("trimLimits leave at least one frame", () => {
    expect(trimLimits(48, 0, 0)).toEqual({ maxIn: 47, maxOut: 47 });
    expect(trimLimits(48, 10, 20)).toEqual({ maxIn: 27, maxOut: 37 });
    expect(trimLimits(null, 0, 0).maxIn).toBe(Infinity);
    // a 16 fps take in a 24 fps cut: 32 frames are 48 of the cut's
    expect(trimLimits((32 / 16) * 24, 0, 0)).toEqual({ maxIn: 47, maxOut: 47 });
    expect(trimLimits(10.5, 0, 0)).toEqual({ maxIn: 9, maxOut: 9 });
  });
  it("clampTrim keeps a side in 0..its limit, whole frames", () => {
    expect(clampTrim("in", 60, 48, 0, 10)).toBe(37);
    expect(clampTrim("out", -3, 48, 0, 0)).toBe(0);
    expect(clampTrim("out", 5.6, 48, 40, 0)).toBe(6);
    expect(clampTrim("out", 99, 48, 40, 0)).toBe(7);
    expect(clampTrim("in", Number.NaN, 48, 0, 0)).toBe(0);
  });
  it("framesLabel and parseFrames", () => {
    expect(framesLabel(12, 24)).toBe("12 f · 0.50 s");
    expect(parseFrames(" 12 ")).toBe(12);
    expect(parseFrames("1.5")).toBeNull();
    expect(parseFrames("-1")).toBeNull();
    expect(parseFrames("")).toBeNull();
  });
});

describe("undo", () => {
  const before: CutEntry[] = [{ shot: "a" }, { shot: "b", take: 2 }, { shot: "c" }];
  it("diffEdit records the order and the fields it changed, nothing else", () => {
    expect(diffEdit("x", before, before.map((e) => ({ ...e })))).toBeNull();
    const move = diffEdit("Move c", before, [before[2], before[0], before[1]])!;
    expect(move.order).toEqual({ before: ["a", "b", "c"], after: ["c", "a", "b"] });
    expect(move.fields).toEqual({});
    const trim = diffEdit("Trim b", before, withFields(before, "b", { trim_in: 4 }))!;
    expect(trim.order).toBeNull();
    // Phase 9d added `audio` to the fields an edit carries
    expect(trim.fields).toEqual({
      b: {
        before: { trim_in: 0, trim_out: 0, locked: false, audio: null },
        after: { trim_in: 4, trim_out: 0, locked: false, audio: null },
      },
    });
  });
  it("undoing doesn't undo a pick made since", () => {
    const trim = diffEdit("Trim b", before, withFields(before, "b", { trim_in: 4 }))!;
    const since: CutEntry[] = [{ shot: "a", take: 7 }, { shot: "b", take: 3, trim_in: 4 }, { shot: "c" }];
    expect(applyEdit(since, trim, "undo")).toEqual([{ shot: "a", take: 7 }, { shot: "b", take: 3 }, { shot: "c" }]);
    expect(applyEdit(applyEdit(since, trim, "undo"), trim, "redo")).toEqual(since);
    const move = diffEdit("Move c", before, [before[2], before[0], before[1]])!;
    // a shot new to the script since: it stays after its neighbour
    const now = [{ shot: "c" }, { shot: "a", take: 7 }, { shot: "new" }, { shot: "b" }];
    expect(ids(applyEdit(now, move, "undo"))).toEqual(["a", "new", "b", "c"]);
  });
  it("the stack: push clears redo; a failed undo / redo goes back", () => {
    const s = new UndoStack(2);
    const e = (label: string) => ({ label, order: null, fields: {} });
    s.push(e("1"));
    s.push(e("2"));
    s.push(e("3"));
    expect(s.undoLabel).toBe("3");
    const u = s.takeUndo()!;
    expect(u.label).toBe("3");
    expect(s.redoLabel).toBe("3");
    s.restoreUndo(u);
    expect(s.undoLabel).toBe("3");
    expect(s.redoLabel).toBeNull();
    s.takeUndo();
    const r = s.takeRedo()!;
    s.restoreRedo(r);
    expect(s.redoLabel).toBe("3");
    s.push(e("4"));
    expect(s.redoLabel).toBeNull();
    expect(s.takeUndo()!.label).toBe("4");
    expect(s.takeUndo()!.label).toBe("2");
    expect(s.takeUndo()).toBeUndefined(); // "1" fell off (limit 2)
  });
});

describe("the recording under the cut", () => {
  // windows laid end to end from 1 s, two frames of silence between b and c
  const windows = { a: [1, 3], b: [3, 5], c: [5 + 2 / 24, 7 + 2 / 24] } as Record<string, [number, number]>;
  const mk = (order: string[], trims: Record<string, [number, number]> = {}) =>
    ep(order.map((id) => shot(id, 60, { audio_in: windows[id][0], audio_out: windows[id][1] }, { trim_in: trims[id]?.[0] ?? 0, trim_out: trims[id]?.[1] ?? 0 })));

  it("a dialogue window is the clip's span (h3assemble trims to it)", () => {
    const st = mk(["a"]);
    expect(windowOf(st.shots[0])).toEqual({ audioIn: 1, audioOut: 3 });
    expect(windowOf(shot("x", 48, { audio_in: 3, audio_out: 1 }))).toBeNull();
    expect(baseIn(st)).toBe(1);
    // 60 frames on disk, a 48-frame window
    expect(spanOf(st.shots[0], 24, st.shots[0].takes[0], 1)).toEqual({ frames: 48, rate: 24, total: 48 });
    const items = buildPlaylist(st);
    expect(items[0]).toMatchObject({ dur: 2, inT: 0, outT: 2, total: 48, audioIn: 1, audioOut: 3 });
    // no window: the whole take
    expect(buildPlaylist(ep([shot("n", 60)]))[0]).toMatchObject({ dur: 2.5, total: 60, audioIn: null });
  });
  it("in script order it lines up, except where the windows leave a gap", () => {
    const items = buildPlaylist(mk(["a", "b", "c"]));
    const sync = masterSync(items, 24, 1);
    expect(sync.start).toBe(1);
    expect(recordingAt(sync.start, 2.5)).toBe(3.5);
    expect(sync.warnings).toEqual(["the recording is 0.08 s early under c"]);
    // the mapping: each clip's window plus its trim-in
    expect(expectedAt(items[2], 24)).toBeCloseTo(5 + 2 / 24);
    expect(trackSlice(items[1], 24)).toEqual({ start: 3, end: 5 });
  });
  it("trims shift the start and make it drift, as --audio master warns", () => {
    const items = buildPlaylist(mk(["a", "b"], { a: [12, 0], b: [0, 6] }));
    expect(masterStart(items, 24, 1)).toBe(1.5);
    const sync = masterSync(items, 24, 1);
    expect(sync.warnings[0]).toMatch(/^a, b have a dialogue window and a cut\.json trim/);
    // a trim-out on a: b starts earlier than its window
    const s2 = masterSync(buildPlaylist(mk(["a", "b"], { a: [0, 12] })), 24, 1);
    expect(s2.drifts).toEqual([{ shot: "b", drift: -0.5 }]);
    expect(trackSlice(items[0], 24)).toEqual({ start: 1.5, end: 3 });
  });
  it("reordering drifts, and says the cut isn't in script order", () => {
    const items = buildPlaylist(mk(["b", "a"]));
    const sync = masterSync(items, 24, 1, true);
    expect(sync.start).toBe(3);
    expect(sync.drifts.map((d) => d.shot)).toEqual(["a"]);
    expect(sync.warnings).toContain("the cut is not in script order; the recording only lines up with script order");
  });
  it("a clip without a window has nothing to line up; an empty cut starts at the base", () => {
    expect(masterSync(buildPlaylist(ep([shot("n")])), 24).drifts).toEqual([]);
    expect(masterStart([], 24, 4)).toBe(4);
    expect(needsResync(10, 10.1)).toBe(false);
    expect(needsResync(10, 10.3)).toBe(true);
    expect(needsResync(Number.NaN, 1)).toBe(true);
  });
});

describe("peaks", () => {
  it("bins per second follow the zoom in powers of two, capped at the server's 200", () => {
    expect(binsPerSecond(40)).toBe(64);
    expect(binsPerSecond(64)).toBe(64);
    expect(binsPerSecond(3)).toBe(8);
    expect(binsPerSecond(1000)).toBe(MAX_BINS_PER_SECOND);
    expect(binsPerSecond(Number.NaN)).toBe(8);
    expect(binsFor(2.5, 40)).toBe(160);
    expect(binsFor(0, 40)).toBe(1);
    expect(binsFor(1e6, 200)).toBe(8192);
  });
  it("resamplePeaks keeps every peak when shrinking and covers when stretching", () => {
    expect(resamplePeaks([1, 9, 2, 3, 8, 4], 3)).toEqual([9, 3, 8]);
    expect(resamplePeaks([1, 9, 2, 3, 8, 4], 2)).toEqual([9, 8]);
    expect(resamplePeaks([5, 7], 4)).toEqual([5, 5, 7, 7]);
    expect(resamplePeaks([], 3)).toEqual([0, 0, 0]);
    expect(resamplePeaks([1, 2], 0)).toEqual([]);
    expect(resamplePeaks([3, 1, 4, 1, 5], 5)).toEqual([3, 1, 4, 1, 5]);
  });
  it("peakBars mirror about the middle, at least a pixel tall", () => {
    const bars = peakBars([255, 0, 128], 3, 20);
    expect(bars).toEqual([
      { x: 0, y: 0, w: 1, h: 20 },
      { x: 1, y: 10, w: 1, h: 1 },
      { x: 2, y: 5, w: 1, h: 10 },
    ]);
    expect(peakBars([255], 5, 10, 2).map((b) => [b.x, b.w])).toEqual([[0, 2], [2, 2], [4, 1]]);
    expect(peakBars([1], 0, 10)).toEqual([]);
  });
  it("drawPeaks clears and fills with the theme colour", () => {
    const calls: string[] = [];
    const ctx = {
      fillStyle: "" as string | CanvasGradient | CanvasPattern,
      clearRect: (...a: number[]) => calls.push(`clear ${a.join(",")}`),
      fillRect: (...a: number[]) => calls.push(`fill ${a.join(",")} ${String(ctx.fillStyle)}`),
    };
    drawPeaks(ctx, [255, 0], 2, 10, "#abc");
    expect(calls).toEqual(["clear 0,0,2,10", "fill 0,0,1,10 #abc", "fill 1,5,1,1 #abc"]);
  });
  it("the cache shares a request, keeps failures, drops the oldest and forgets an episode", async () => {
    let n = 0;
    const fetcher = vi.fn(async (q: { path: string }) => {
      n++;
      if (q.path === "bad") throw new Error("404 nope");
      return { duration: 1, bins: 2, peaks: [1, 2] };
    });
    const c = new PeaksCache(fetcher, 2);
    const q = { ep: "E", path: "a.mp4", bins: 2, start: 0, end: 1 };
    const [x, y] = await Promise.all([c.get(q), c.get({ ...q })]);
    expect(x).toBe(y);
    expect(n).toBe(1);
    expect(c.peek(q)).toEqual({ ok: true, data: { duration: 1, bins: 2, peaks: [1, 2] } });
    expect(await c.get({ ...q, path: "bad" })).toEqual({ ok: false, error: "404 nope" });
    await c.get({ ...q, path: "bad" });
    expect(n).toBe(2);
    await c.get({ ...q, path: "c.mp4" });
    expect(c.size).toBe(2);
    expect(c.peek(q)).toBeUndefined(); // the oldest went
    c.forget("E");
    expect(c.size).toBe(0);
    expect(peaksKey({ ep: "E", path: "p", bins: 10.4, start: 1.23456 })).toBe("E|p|1.235||10");
  });
  it("waveSource: the take's audio over the part the cut plays, or the track slice on 'recording'", () => {
    const st = ep([shot("a", 60, { audio_in: 2, audio_out: 4 }, { trim_in: 12 })], { track: { path: "audio/rec.wav", duration: 10, rate: 48000 } });
    const [it] = buildPlaylist(st);
    const t = st.shots[0].takes[0];
    expect(waveSource("E", it, t, st, false, 40)).toMatchObject({ kind: "take", q: { path: "r/a/t1.mp4", start: 0.5, end: 2, bins: 96 } });
    expect(waveSource("E", it, t, st, true, 40)).toMatchObject({ kind: "recording", q: { path: "audio/rec.wav", start: 2.5, end: 4 } });
    // no audio file: nothing; no track: the take's own
    expect(waveSource("E", it, { ...t, audio: null }, st, false, 40)).toBeNull();
    expect(waveSource("E", it, t, { ...st, track: null }, true, 40)?.kind).toBe("take");
    expect(waveSource("E", undefined, t, st, true, 40)).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// the mock's cut routes (h3takes semantics)
// ---------------------------------------------------------------------------

describe("mock cut.json", () => {
  const script = ["a", "b", "c", "d"];
  it("resolveCut puts unlisted script shots after their predecessor and keeps orphans", () => {
    const r = resolveCut([{ shot: "c" }, { shot: "gone" }, { shot: "a", pass: "final", take: 2 }], "proxy", script);
    expect(ids(r)).toEqual(["c", "d", "gone", "a", "b"]);
    expect(r.find((e) => e.shot === "gone")!.orphan).toBe(true);
    expect(r.find((e) => e.shot === "a")!.placeholder).toBe(true);
    expect(materialize([{ shot: "c" }], "proxy", script)).toEqual([{ shot: "a" }, { shot: "b" }, { shot: "c" }, { shot: "d" }]);
    expect(ids(materialize([{ shot: "c" }, { shot: "a" }], "proxy", script))).toEqual(["c", "d", "a", "b"]);
  });
  it("checkEntries: 400 for duplicates, unknown fields, bad trims, trims leaving no frame", () => {
    const frames = (e: CutEntry): [number, number] | null => (e.shot === "a" ? [48, 24] : null);
    expect(() => checkEntries([{ shot: "a" }, { shot: "a" }], 24, frames)).toThrow(/twice/);
    expect(() => checkEntries([{ shot: "a", foo: 1 } as unknown as CutEntry], 24, frames)).toThrow(/unknown field foo/);
    expect(() => checkEntries([{ shot: "a", trim_in: -1 }], 24, frames)).toThrow(/whole number/);
    expect(() => checkEntries([{ shot: "a", trim_in: 1.5 }], 24, frames)).toThrow(/whole number/);
    expect(() => checkEntries([{ shot: "a", trim_in: 40, trim_out: 8 }], 24, frames)).toThrow(/less than one frame/);
    expect(checkEntries([{ shot: "a", trim_in: 40, trim_out: 7 }, { shot: "b", trim_in: 999 }], 24, frames)).toHaveLength(2);
  });
  it("reset and copy", () => {
    const list: CutEntry[] = [{ shot: "c", trim_in: 4, locked: true }, { shot: "a", take: 3 }, { shot: "b", note: "n" }];
    expect(resetEntries(list, "proxy", script, "order")).toEqual([{ shot: "a", take: 3 }, { shot: "b", note: "n" }, { shot: "c", trim_in: 4, locked: true }, { shot: "d" }]);
    // as built: a locked entry keeps its trims
    expect(resetEntries(list, "proxy", script, "trims")[0]).toEqual({ shot: "c", trim_in: 4, locked: true });
    expect(resetEntries([{ shot: "a", trim_out: 3 }], "proxy", script, "all")[0]).toEqual({ shot: "a" });
    const to = copyEntries(list, "final", 24, [{ shot: "a", take: 1 }], "proxy", 12, script, "all");
    expect(to).toEqual([{ shot: "c", trim_in: 2 }, { shot: "d" }, { shot: "a", take: 1 }, { shot: "b" }]);
    // copied trims are cut down to leave a frame (trim_out first); a locked target keeps its own
    const clamp = copyEntries([{ shot: "a", trim_in: 30, trim_out: 30 }], "final", 24, [{ shot: "a" }, { shot: "b", locked: true, trim_in: 1 }], "proxy", 24, script, "trims", () => [48, 24]);
    expect(clamp.slice(0, 2)).toEqual([{ shot: "a", trim_in: 30, trim_out: 17 }, { shot: "b", locked: true, trim_in: 1 }]);
    // the local fallback of Clear trims skips locked entries too
    expect(clearTrims([{ shot: "a", trim_in: 2, locked: true }, { shot: "b", trim_out: 1 }])).toEqual([{ shot: "a", trim_in: 2, locked: true }, { shot: "b" }]);
  });
});

// ---------------------------------------------------------------------------
// the edits, end to end on the mock API
// ---------------------------------------------------------------------------

describe("cut edits on the mock", () => {
  let api: ReturnType<typeof createMockApi>;
  let EP: string;
  const toasts: { sev: string; summary: string }[] = [];
  const cur = () => store.get().status[statusKey(EP, "proxy")];
  const order = () => ids(cur().shots).slice(0, 5);

  beforeEach(async () => {
    toasts.length = 0;
    setHost({ on: () => () => {}, toast: (sev, summary) => void toasts.push({ sev, summary }), show: () => {} } as Host);
    api = createMockApi(() => {}, { latency: 0 });
    setApi(api);
    EP = (await api.episodes())[0].ep;
    store.set(initialState());
    resetCutHistory();
    store.set({ ep: EP, pass: "proxy" });
    await refreshEpisode(EP, "proxy");
  });
  afterEach(() => store.set(initialState()));

  it("the mock serves 9b's fields", async () => {
    const st = cur();
    expect(st.track?.path).toBe("audio/ep05_dialogue.wav");
    expect(st.shots[0].cut).toMatchObject({ order: 0, script_index: 0, out_of_order: false });
    expect(st.shots.find((s) => s.shot === "sh010")!.takes[0].audio).toMatch(/sh010_t01\.mp4$/);
    expect(typeof st.shots.find((s) => s.shot === "sh020")!.audio_in).toBe("number");
    expect(st.shots.find((s) => s.shot === "sh040")!.audio_in).toBeUndefined();
    const p = await api.peaks(EP, st.track!.path, 50, 1, 3);
    expect(p.peaks).toHaveLength(50);
    expect(Math.max(...p.peaks)).toBeGreaterThan(50);
    await expect(api.peaks(EP, "../x.wav", 5)).rejects.toMatchObject({ status: 400 });
    await expect(api.peaks(EP, "nope.wav", 5)).rejects.toMatchObject({ status: 404 });
  });

  it("move, nudge, undo, redo: one PUT each, the badge follows", async () => {
    const put = vi.spyOn(api, "putCut");
    expect(order()).toEqual(["sh010", "sh020", "sh030", "sh040", "sh050"]);
    await moveClip("sh010", 3);
    expect(order()).toEqual(["sh020", "sh030", "sh010", "sh040", "sh050"]);
    expect(put).toHaveBeenCalledTimes(1);
    expect(put.mock.calls[0][2].map((e) => e.shot).slice(0, 3)).toEqual(["sh020", "sh030", "sh010"]);
    // the server agrees
    const fresh = await refreshEpisode(EP, "proxy");
    expect(ids(fresh!.shots).slice(0, 3)).toEqual(["sh020", "sh030", "sh010"]);
    expect(fresh!.shots.find((s) => s.shot === "sh010")!.cut.out_of_order).toBe(true);
    expect(anyOutOfOrder(fresh)).toBe(true);
    await nudgeClip("sh050", -1);
    expect(order()).toEqual(["sh020", "sh030", "sh010", "sh050", "sh040"]);
    expect(store.get().cutUndo[statusKey(EP, "proxy")]).toEqual({ undo: "Move sh050 left", redo: null });
    await undoCut();
    expect(order()).toEqual(["sh020", "sh030", "sh010", "sh040", "sh050"]);
    await undoCut();
    expect(order()).toEqual(["sh010", "sh020", "sh030", "sh040", "sh050"]);
    await redoCut();
    expect(order()).toEqual(["sh020", "sh030", "sh010", "sh040", "sh050"]);
    expect(put).toHaveBeenCalledTimes(5);
    await undoCut();
    await undoCut();
    expect(toasts.at(-1)?.summary).toBe("Nothing to undo");
  });

  it("trims clamp to one frame, and I / O trim at the playhead", async () => {
    await setTrims("sh010", 500, 0);
    const s = cur().shots.find((x) => x.shot === "sh010")!;
    expect(s.cut.trim_in).toBe((s.length ?? 0) - 1);
    expect((await api.episode(EP, "proxy")).shots.find((x) => x.shot === "sh010")!.cut.trim_in).toBe((s.length ?? 0) - 1);
    await setTrims("sh010", 0, 0);
    // I / O need Play all open
    expect(await trimAtPlayhead("in")).toBe(false);
    seekCutAt(1);
    expect(store.get().viewer?.kind).toBe("cut");
    expect(store.get().cutPlay.playing).toBe(false);
    await trimAtPlayhead("in");
    expect(cur().shots.find((x) => x.shot === "sh010")!.cut.trim_in).toBe(24);
    expect(store.get().cutPlay.seek?.t).toBe(0);
    seekCutAt(0.5);
    await trimAtPlayhead("out");
    const sh = cur().shots.find((x) => x.shot === "sh010")!;
    expect(sh.cut.trim_out).toBe((sh.length ?? 0) - 24 - 12);
    await undoCut();
    await undoCut();
    expect(cur().shots.find((x) => x.shot === "sh010")!.cut).toMatchObject({ trim_in: 0, trim_out: 0 });
  });

  it("a locked clip refuses moves, trims and re-picks; the server's 409 too", async () => {
    await toggleLock("sh020");
    expect(cur().shots.find((x) => x.shot === "sh020")!.cut.locked).toBe(true);
    const put = vi.spyOn(api, "putCut");
    expect(await moveClip("sh020", 0)).toBe(false);
    expect(await nudgeClip("sh020", 1)).toBe(false);
    expect(await setTrims("sh020", 4, 0)).toBe(false);
    expect(put).not.toHaveBeenCalled();
    expect(lockedPickRefusal("sh020")).toMatch(/locked/);
    await expect(api.pick({ ep: EP, pass: "proxy", shot: "sh020", take: 2, from_pass: null })).rejects.toMatchObject({ status: 409 });
    await toggleLock("sh020");
    expect(lockedPickRefusal("sh020")).toBeNull();
    // a pick keeps the cut's order (the mock materialises like h3takes.pick)
    await moveClip("sh030", 0);
    await api.pick({ ep: EP, pass: "proxy", shot: "sh020", take: 2, from_pass: null });
    expect(ids((await api.episode(EP, "proxy")).shots).slice(0, 3)).toEqual(["sh030", "sh010", "sh020"]);
  });

  it("a status fetched while a save is on its way keeps the edit (no lost update)", async () => {
    const real = api.putCut.bind(api);
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    vi.spyOn(api, "putCut").mockImplementationOnce(async (...a) => {
      await gate;
      return real(...a);
    });
    const saving = moveClip("sh010", 3);
    expect(order().slice(0, 3)).toEqual(["sh020", "sh030", "sh010"]);
    // an h3pipe.episode refetch lands before the PUT: the server doesn't have it yet
    await refreshEpisode(EP, "proxy");
    expect(order().slice(0, 3)).toEqual(["sh020", "sh030", "sh010"]);
    // a second edit made now is written on top of the first
    const second = nudgeClip("sh050", -1);
    release();
    await saving;
    await second;
    const server = await api.episode(EP, "proxy");
    expect(ids(server.shots).slice(0, 5)).toEqual(["sh020", "sh030", "sh010", "sh050", "sh040"]);
  });

  it("a refused save rolls back and keeps nothing to undo", async () => {
    const before = cur();
    vi.spyOn(api, "putCut").mockRejectedValueOnce(Object.assign(new Error("nope"), { status: 400 }));
    expect(await moveClip("sh010", 3)).toBe(false);
    await cutSettled();
    expect(ids(cur().shots)).toEqual(ids(before.shots));
    expect(undoStack(EP, "proxy").undoLabel).toBeNull();
    expect(toasts.some((t) => t.sev === "error")).toBe(true);
  });

  it("reset and copy go through the routes and can be undone", async () => {
    await moveClip("sh010", 3);
    await setTrims("sh020", 6, 0);
    expect(await resetCut("order")).toBe(true);
    expect(order()).toEqual(["sh010", "sh020", "sh030", "sh040", "sh050"]);
    expect(cur().shots.find((x) => x.shot === "sh020")!.cut.trim_in).toBe(6);
    await resetCut("trims");
    expect(cur().shots.find((x) => x.shot === "sh020")!.cut.trim_in).toBe(0);
    await undoCut();
    expect(cur().shots.find((x) => x.shot === "sh020")!.cut.trim_in).toBe(6);
    // copy the proxy order onto final (from final's view)
    await moveClip("sh030", 0);
    store.set({ pass: "final" });
    await refreshEpisode(EP, "final");
    await copyCut("order");
    const fin = store.get().status[statusKey(EP, "final")];
    expect(ids(fin.shots)[0]).toBe("sh030");
  });

  it("an older server without the reset route: done here with a PUT", async () => {
    await moveClip("sh010", 3);
    vi.spyOn(api, "cutReset").mockRejectedValueOnce(new ApiError("gone", 404, "/h3pipe/cut/reset"));
    expect(await resetCut("order")).toBe(true);
    expect(order()).toEqual(["sh010", "sh020", "sh030", "sh040", "sh050"]);
  });
});

describe("keys", () => {
  const k = (key: string, over: Partial<Parameters<typeof cutKey>[0]> = {}) => ({
    key, ctrlKey: false, metaKey: false, shiftKey: false, altKey: false, target: null, ...over,
  });
  beforeEach(() => {
    store.set(initialState());
    setHost({ on: () => () => {}, toast: () => {}, show: () => {} } as Host);
  });
  it("text fields and CodeMirror keep their keys", () => {
    expect(isTyping({ tagName: "INPUT" } as unknown as EventTarget)).toBe(true);
    expect(isTyping({ tagName: "DIV", isContentEditable: true } as unknown as EventTarget)).toBe(true);
    expect(isTyping({ tagName: "SPAN", closest: (s: string) => (s.includes(".cm-editor") ? {} : null) } as unknown as EventTarget)).toBe(true);
    expect(isTyping({ tagName: "DIV", closest: () => null } as unknown as EventTarget)).toBe(false);
    expect(cutKey(k(" ", { target: { tagName: "TEXTAREA" } as unknown as EventTarget }))).toBe(false);
  });
  it("the cut's shortcuts are claimed; others aren't", () => {
    expect(cutKey(k("z", { ctrlKey: true }))).toBe(true);
    expect(cutKey(k("Z", { ctrlKey: true, shiftKey: true }))).toBe(true);
    expect(cutKey(k("ArrowLeft", { altKey: true }))).toBe(true);
    expect(cutKey(k("i"))).toBe(true);
    expect(cutKey(k("x"))).toBe(false);
    expect(cutKey(k("c", { ctrlKey: true }))).toBe(false);
    expect(cutKey(k("ArrowLeft"))).toBe(false);
  });
  it("J / K / L shuttle: faster on a repeat, K stops", () => {
    const st = ep([shot("a", 240)]);
    store.set({ ep: "E", pass: "proxy", status: { [statusKey("E", "proxy")]: st } });
    seekCutAt(5);
    shuttle("l");
    expect(store.get().cutPlay).toMatchObject({ playing: true, rate: 1 });
    shuttle("l");
    shuttle("l");
    shuttle("l");
    expect(store.get().cutPlay.rate).toBe(4);
    shuttle("j");
    expect(store.get().cutPlay.rate).toBe(-1);
    shuttle("j");
    expect(store.get().cutPlay.rate).toBe(-2);
    shuttle("k");
    expect(store.get().cutPlay).toMatchObject({ playing: false, rate: 1 });
    // space plays at 1x again
    cutKey(k(" "));
    expect(store.get().cutPlay).toMatchObject({ playing: true, rate: 1 });
  });
});

describe("the client and the as-built shapes", () => {
  function http(answer: unknown) {
    const calls: string[] = [];
    const t: Transport = {
      async fetch(path, init) {
        calls.push(`${init?.method ?? "GET"} ${path}`);
        return new Response(JSON.stringify(answer), { status: 200, headers: { "Content-Type": "application/json" } });
      },
      url: (x) => x,
    };
    return { api: createHttpApi(t), calls };
  }
  it("peaks: the query, a silent file (bins 0, peaks []), start / end", async () => {
    const { api, calls } = http({ duration: 3.2, bins: 0, peaks: [], silent: true, start: 0.5, end: 2 });
    const r = await api.peaks("E:\\ep", "renders_proxy/sh010/sh010_t01.mp4", 12.4, 0.5, 2.0000004);
    expect(calls[0]).toBe("GET /h3pipe/peaks?ep=E%3A%5Cep&path=renders_proxy%2Fsh010%2Fsh010_t01.mp4&bins=12&start=0.5&end=2");
    expect(r).toEqual({ duration: 3.2, bins: 0, peaks: [], silent: true, start: 0.5, end: 2 });
    // junk from an older server is survivable
    const junk = await http({ peaks: [1, "x", null, 300] }).api.peaks("E", "a.wav", 4);
    expect(junk).toMatchObject({ duration: 0, bins: 4, peaks: [1, 0, 0, 300], silent: false });
  });
  it("reset and copy post what the contract says", async () => {
    const { api, calls } = http({ cut: {} });
    await api.cutReset("E", "proxy", "trims");
    await api.cutCopy("E", "final", "proxy", "order");
    expect(calls).toEqual(["POST /h3pipe/cut/reset", "POST /h3pipe/cut/copy"]);
  });
  it("trackState: missing, another drive, or playable", () => {
    expect(trackState(null)).toBeNull();
    expect(trackState({ path: "", duration: null })).toBeNull();
    expect(trackState({ path: "audio/a.wav", duration: null, rate: null, exists: false })?.why).toMatch(/isn't there/);
    expect(trackState({ path: "D:/rec/a.wav", duration: 3, exists: true })?.why).toMatch(/another drive/);
    expect(trackState({ path: "../audio/a.wav", duration: 3, rate: 48000, exists: true })).toEqual({ path: "../audio/a.wav", why: null });
    // a server from before `exists`
    expect(trackState({ path: "audio/a.wav" })?.why).toBeNull();
  });
  it("the playlist of no episode is one stable empty list (components select it)", () => {
    const s = initialState();
    expect(currentPlaylist(s)).toBe(currentPlaylist({ ...s }));
    expect(currentPlaylist(s)).toEqual([]);
  });
  it("a take re-rendered into the same number isn't served stale peaks", () => {
    const a = peaksKey({ ep: "E", path: "p.mp4", bins: 10, version: "2026-09-19T10:00" });
    const b = peaksKey({ ep: "E", path: "p.mp4", bins: 10, version: "2026-09-19T11:00" });
    expect(a).not.toBe(b);
  });
});
