// Play all: trim maths, the playlist, and stepping through it on one clock.
import { describe, expect, it } from "vitest";
import {
  atOutPoint, buildPlaylist, clipOffset, cutTime, fileTime, framesOf, locate, nextVideo, startOf, totalDuration, trimWindow,
  type PlayItem,
} from "../src/lib/playlist";
import type { CutInfo, EpisodeStatus, ShotStatus, TakeSummary } from "../src/types";

function take(n: number, over: Partial<TakeSummary> = {}): TakeSummary {
  return {
    take: n, status: "ok", has_video: true, seed: "1", seed_source: "stable", note: "", overrides: [], stale: [],
    thumb: null, strip: null, mp4: `r/sh/t${n}.mp4`, queued: null, finished: null, save_notes: "", ...over,
  };
}

const CUT: CutInfo = { take: 1, picked: false, pass: "proxy", placeholder: false, usable: true, trim_in: 0, trim_out: 0, locked: false, note: "", in_cut_file: false };

function shot(id: string, length: number, over: Partial<ShotStatus> = {}, cut: Partial<CutInfo> = {}): ShotStatus {
  return {
    shot: id, orphan: false, sequence: "sq01", length, seconds: length / 24, size: null, subjects: [], audio_policy: null,
    cut: { ...CUT, ...cut }, override: { fields: [], stale: false }, takes: [take(1, { mp4: `r/${id}/t1.mp4` })], ...over,
  };
}

function ep(shots: ShotStatus[], pass: "proxy" | "final" = "proxy"): EpisodeStatus {
  return { episode: "ep", title: "", pass, fps: 24, width: 448, height: 256, folder: "renders_proxy", shots };
}

describe("trimWindow", () => {
  it("drops trim_in frames from the head and trim_out from the tail", () => {
    expect(trimWindow(96, 0, 0, 24)).toEqual({ inT: 0, outT: 4, dur: 4 });
    expect(trimWindow(96, 12, 24, 24)).toEqual({ inT: 0.5, outT: 3, dur: 2.5 });
  });
  it("ignores negative trims and fractional frames", () => {
    expect(trimWindow(48, -5, 2.9, 24)).toEqual({ inT: 0, outT: 46 / 24, dur: 46 / 24 });
  });
  it("is null when the trims leave nothing (as assemble skips it)", () => {
    expect(trimWindow(48, 24, 24, 24)).toBeNull();
    expect(trimWindow(48, 40, 10, 24)).toBeNull();
    expect(trimWindow(0, 0, 0, 24)).toBeNull();
  });
  it("falls back to 24 fps", () => {
    expect(trimWindow(48, 0, 0, 0)?.dur).toBe(2);
  });
});

describe("buildPlaylist", () => {
  const st = ep([
    shot("a", 48),
    shot("b", 72, { takes: [] }, { take: null, usable: false }),
    shot("c", 96, {}, { trim_in: 24, trim_out: 12 }),
    shot("x", 0, { orphan: true, sequence: null, length: null, seconds: null, takes: [] }),
    shot("d", 24, { takes: [take(1, { status: "failed", has_video: false, mp4: null })] }),
    shot("e", 48, { takes: [] }, { placeholder: true, pass: "final", take: 3 }),
  ]);
  const other = ep([shot("e", 48, { takes: [take(3, { mp4: "renders/e/t3.mp4" })] })], "final");

  it("is the cut in order, orphans left out, starts accumulating", () => {
    const items = buildPlaylist(st, other);
    expect(items.map((i) => i.shot)).toEqual(["a", "b", "c", "d", "e"]);
    expect(items.map((i) => i.index)).toEqual([0, 1, 2, 3, 4]);
    expect(items.map((i) => i.start)).toEqual([0, 2, 5, 7.5, 8.5]);
    expect(totalDuration(items)).toBe(10.5);
  });
  it("a shot with no usable take is a missing card for its duration", () => {
    const [, b, , d] = buildPlaylist(st, other);
    expect(b).toMatchObject({ mp4: null, dur: 3, why: "no take" });
    expect(d).toMatchObject({ mp4: null, dur: 1, why: "t01 failed" });
  });
  it("applies trims to the file window and the clip's length", () => {
    const c = buildPlaylist(st, other)[2];
    expect(c).toMatchObject({ mp4: "r/c/t1.mp4", inT: 1, outT: 3.5, dur: 2.5 });
  });
  it("a placeholder plays the other pass's take", () => {
    const e = buildPlaylist(st, other)[4];
    expect(e).toMatchObject({ mp4: "renders/e/t3.mp4", pass: "final", take: 3 });
    // without the other pass loaded it's a missing card, not a crash
    expect(buildPlaylist(st)[4]).toMatchObject({ mp4: null, why: "final t03 not found" });
  });
  it("trims that leave nothing make a missing card of the untrimmed length", () => {
    const items = buildPlaylist(ep([shot("a", 24, {}, { trim_in: 20, trim_out: 10 })]));
    expect(items[0]).toMatchObject({ mp4: null, dur: 1, why: "trimmed to nothing" });
  });
  it("uses seconds when the length is unknown", () => {
    expect(buildPlaylist(ep([shot("a", 0, { length: null, seconds: 2.5 })]))[0].dur).toBe(2.5);
  });
  it("a take's real frames beat the build's length (dur: model)", () => {
    // built at an estimate of 121 frames; the model chose 199 (8.29 s)
    const items = buildPlaylist(ep([
      shot("a", 121, { takes: [take(1, { mp4: "r/a/t1.mp4", frames: 199 })] }, { frames: 199 }),
      shot("b", 48),
    ]));
    expect(items[0]).toMatchObject({ mp4: "r/a/t1.mp4", inT: 0, outT: 199 / 24, dur: 199 / 24 });
    expect(items[1].start).toBe(199 / 24);
    // trims come off the real length
    const [c] = buildPlaylist(ep([shot("c", 121, { takes: [take(1, { frames: 199 })] }, { trim_out: 7 })]));
    expect(c.dur).toBe(192 / 24);
    // the cut's frames count when the take summary has none (an older take list)
    expect(buildPlaylist(ep([shot("d", 121, {}, { frames: 73 })]))[0].dur).toBe(73 / 24);
    expect(framesOf(shot("e", 121), 24)).toBe(121);
  });
});

describe("locate and clocks", () => {
  const items = buildPlaylist(ep([shot("a", 48), shot("b", 72, { takes: [] }, { take: null }), shot("c", 96, {}, { trim_in: 24 })]));
  it("finds the clip and offset for a cut time, clamped", () => {
    expect(locate(items, 0)).toEqual({ index: 0, offset: 0 });
    expect(locate(items, 1.5)).toEqual({ index: 0, offset: 1.5 });
    expect(locate(items, 2)).toEqual({ index: 1, offset: 0 });
    expect(locate(items, 5.25)).toEqual({ index: 2, offset: 0.25 });
    expect(locate(items, 99)).toEqual({ index: 2, offset: 3 });
    expect(locate(items, -1)).toEqual({ index: 0, offset: 0 });
    expect(locate([], 3)).toEqual({ index: -1, offset: 0 });
  });
  it("converts between cut, clip and file time with the trim", () => {
    const c = items[2];
    expect(fileTime(c, 0.5)).toBe(1.5);
    expect(clipOffset(c, 1.5)).toBe(0.5);
    expect(clipOffset(c, 0.2)).toBe(0); // before the in point
    expect(cutTime(items, 2, 0.5)).toBe(5.5);
    expect(cutTime(items, 9, 0)).toBe(totalDuration(items));
    expect(startOf(items, "c")).toBe(5);
    expect(startOf(items, "zz")).toBeNull();
  });
  it("a clip is done half a frame before its out point", () => {
    const a = items[0];
    expect(atOutPoint(a, 2 - 1 / 24, 24)).toBe(false);
    expect(atOutPoint(a, 2 - 0.4 / 24, 24)).toBe(true);
  });
  it("the spare <video> preloads the next clip with video, skipping cards", () => {
    expect(nextVideo(items, 0)).toBe(2);
    expect(nextVideo(items, 1)).toBe(2);
    expect(nextVideo(items, 2)).toBe(-1);
  });
});

/**
 * Play the list the way CutPlayer does: the clip on screen advances to the next
 * at its out point, a card runs on the clock, the spare holds the next video.
 */
function simulate(items: PlayItem[], fps: number, dt: number) {
  const log: string[] = [];
  let idx = 0;
  let fileT = items[0].inT;
  let card = 0;
  let spare = nextVideo(items, 0);
  let t = 0;
  log.push(`show ${items[0].shot}`);
  for (let guard = 0; guard < 10000 && idx < items.length; guard++) {
    const it = items[idx];
    const done = it.mp4 ? atOutPoint(it, fileT, fps) : card >= it.dur;
    if (done) {
      idx++;
      if (idx >= items.length) break;
      const next = items[idx];
      // the next video must be the one already in the spare slot
      if (next.mp4) log.push(`${spare === idx ? "swap" : "LOAD"} ${next.shot}`);
      else log.push(`card ${next.shot}`);
      spare = nextVideo(items, idx);
      fileT = next.inT;
      card = 0;
      continue;
    }
    t += dt;
    if (it.mp4) fileT += dt;
    else card += dt;
  }
  return { log, t };
}

describe("play-all sequencing", () => {
  it("plays every clip in order, cards included, each from the preloaded spare", () => {
    const items = buildPlaylist(ep([
      shot("a", 48), shot("b", 24, { takes: [] }, { take: null }), shot("c", 48, {}, { trim_in: 12, trim_out: 12 }), shot("d", 24),
    ]));
    const { log, t } = simulate(items, 24, 1 / 60);
    expect(log).toEqual(["show a", "card b", "swap c", "swap d"]);
    // the whole cut plays in about its duration (each clip ends within a frame of its out point)
    expect(Math.abs(t - totalDuration(items))).toBeLessThan(4 / 24);
  });
});
