// Play all: the cut as a list of clips on one clock, played from the takes
// themselves (no assemble). Pure; the player component (CutPlayer) drives two
// <video> elements from it.

import type { EpisodeStatus, Pass, ShotStatus, TakeSummary } from "../types";
import { cutTake, tn } from "./format";

export interface PlayItem {
  shot: string;
  /** position in the playlist (orphans are left out, as assemble skips them) */
  index: number;
  /** where the take comes from (the other pass for a placeholder) */
  pass: Pass;
  take: number | null;
  /** the mp4, relative to the episode; null = a black "missing" card */
  mp4: string | null;
  /** why there's no video, for the card */
  why: string;
  /** start on the cut's clock, seconds */
  start: number;
  /** seconds this clip lasts on the cut's clock */
  dur: number;
  /** seconds into the file where it starts and ends (trims applied) */
  inT: number;
  outT: number;
  /** Phase 9b: the entry's trims (the cut's frames) */
  trimIn: number;
  trimOut: number;
  /** Phase 9b: the clip's untrimmed length in the cut's frames (its dialogue
   * window when it has one; fractional for a clip at another rate); null when unknown */
  total: number | null;
  /** Phase 9b: the shot's dialogue window on the episode's track (null: none) */
  audioIn: number | null;
  audioOut: number | null;
}

/** The take a timeline clip shows: the cut's take, from the other pass for a placeholder. */
export function clipTake(s: ShotStatus, other: EpisodeStatus | undefined): TakeSummary | undefined {
  if (!s.cut.placeholder) return cutTake(s);
  if (s.cut.take == null) return undefined;
  return other?.shots.find((x) => x.shot === s.shot)?.takes.find((t) => t.take === s.cut.take);
}

/**
 * cut.json trims are frames dropped from the head (`trim_in`) and tail
 * (`trim_out`) of a clip `frames` long, as h3assemble applies them. Returns the
 * window in seconds, or null when the trims leave nothing (assemble skips it).
 * `clipFps` is the clip's own rate when it isn't the cut's (`fps`): its
 * `frames` are at that rate, the trims at the cut's (assemble converts first).
 */
export function trimWindow(frames: number, trimIn: number, trimOut: number, fps: number, clipFps?: number | null): { inT: number; outT: number; dur: number } | null {
  const f = fps > 0 ? fps : 24;
  const c = clipFps && clipFps > 0 ? clipFps : f;
  const a = Math.max(0, Math.floor(trimIn || 0));
  const b = Math.max(0, Math.floor(trimOut || 0));
  if (c === f) {
    const used = frames - a - b;
    if (!(frames > 0) || used < 1) return null;
    return { inT: a / f, outT: (frames - b) / f, dur: used / f };
  }
  const len = frames / c;
  if (!(frames > 0) || len * f - a - b < 1) return null;
  return { inT: a / f, outT: len - b / f, dur: len - (a + b) / f };
}

/** The frame rate of a shot's frames in the cut: the take's, else the cut's
 * (the take's or the shot's target's), else the episode's. */
export function fpsOf(s: ShotStatus, fps: number, take?: TakeSummary): number {
  return take?.fps || s.cut.fps || fps || 24;
}

/** The shot's length in frames: the cut take's real count when the server
 * sends it (its sidecar's; a `dur: model` take's is the model's choice), else
 * the build's `length`, else from `seconds`. */
export function framesOf(s: ShotStatus, fps: number, take?: TakeSummary): number {
  const real = take?.frames ?? s.cut.frames;
  if (real != null && real > 0) return real;
  if (s.length != null && s.length > 0) return s.length;
  return Math.max(1, Math.round((s.seconds ?? 1) * (fps || 24)));
}

/** A shot's dialogue window on the track, when it has a valid one. */
export function windowOf(s: ShotStatus): { audioIn: number; audioOut: number } | null {
  const a = s.audio_in;
  const b = s.audio_out;
  return typeof a === "number" && typeof b === "number" && Number.isFinite(a) && Number.isFinite(b) && b > a ? { audioIn: a, audioOut: b } : null;
}

/** The earliest dialogue window start (h3assemble's `base_in`), else 0. */
export function baseIn(st: EpisodeStatus | undefined): number {
  let m = Infinity;
  for (const s of st?.shots ?? []) {
    const w = windowOf(s);
    if (w && w.audioIn < m) m = w.audioIn;
  }
  return Number.isFinite(m) ? m : 0;
}

/**
 * The frames of a clip the cut can use, before its cut.json trims, as
 * h3assemble counts them: a shot timed against recorded dialogue is its
 * window (H3 renders it rounded up to its frame grid), at most what's on
 * disk; else the whole take. `frames` at `rate`, the cut's clock at `fps`.
 */
export function spanOf(s: ShotStatus, fps: number, take: TakeSummary | undefined, base = 0): { frames: number; rate: number; total: number } {
  const f = fps > 0 ? fps : 24;
  const frames = framesOf(s, f, take);
  const rate = fpsOf(s, f, take);
  const onDisk = rate === f ? frames : (frames / rate) * f;
  const w = windowOf(s);
  if (w) {
    const keep = Math.round((w.audioOut - base) * f) - Math.round((w.audioIn - base) * f);
    if (keep > 0) {
      const span = Math.min(keep, rate === f ? frames : Math.round(onDisk));
      return { frames: span, rate: f, total: span };
    }
  }
  return { frames, rate, total: onDisk };
}

/**
 * The cut in order. A shot without a usable take (or whose trims leave nothing)
 * becomes a missing card lasting the shot's duration. `other` is the other
 * pass's status, for placeholders.
 */
export function buildPlaylist(st: EpisodeStatus | undefined, other?: EpisodeStatus): PlayItem[] {
  if (!st) return [];
  const fps = st.fps || 24;
  const base = baseIn(st);
  const out: PlayItem[] = [];
  let t = 0;
  for (const s of st.shots) {
    if (s.orphan) continue;
    const take = clipTake(s, s.cut.placeholder ? other : undefined);
    const { frames, rate, total } = spanOf(s, fps, take, base);
    const usable = !!take && take.status === "ok" && take.has_video && !!take.mp4;
    const trimIn = Math.max(0, Math.floor(s.cut.trim_in || 0));
    const trimOut = Math.max(0, Math.floor(s.cut.trim_out || 0));
    const win = trimWindow(frames, trimIn, trimOut, fps, rate);
    const w = windowOf(s);
    const extra = { trimIn, trimOut, total, audioIn: w?.audioIn ?? null, audioOut: w?.audioOut ?? null };
    let item: Omit<PlayItem, "start" | "index">;
    if (usable && win) {
      item = { shot: s.shot, pass: s.cut.placeholder ? s.cut.pass : st.pass, take: take!.take, mp4: take!.mp4, why: "", ...win, ...extra };
    } else {
      const dur = win?.dur ?? frames / rate;
      const why = !take
        ? s.cut.take == null ? "no take" : `${s.cut.placeholder ? `${s.cut.pass} ` : ""}${tn(s.cut.take)} not found`
        : !usable ? `${tn(take.take)} ${take.status === "ok" ? "has no video" : take.status}` : "trimmed to nothing";
      item = { shot: s.shot, pass: st.pass, take: take?.take ?? null, mp4: null, why, inT: 0, outT: dur, dur, ...extra };
    }
    out.push({ ...item, index: out.length, start: t });
    t += item.dur;
  }
  return out;
}

export function totalDuration(items: PlayItem[]): number {
  const last = items[items.length - 1];
  return last ? last.start + last.dur : 0;
}

/** Which clip plays at cut time `t`, and how far into it. Clamped to the cut. */
export function locate(items: PlayItem[], t: number): { index: number; offset: number } {
  if (!items.length) return { index: -1, offset: 0 };
  if (!(t > 0)) return { index: 0, offset: 0 };
  let lo = 0;
  let hi = items.length - 1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (items[mid].start <= t) lo = mid;
    else hi = mid - 1;
  }
  const it = items[lo];
  return { index: lo, offset: Math.min(Math.max(0, t - it.start), it.dur) };
}

/** Cut time of a position inside clip `index` (seconds into the clip, not the file). */
export function cutTime(items: PlayItem[], index: number, offset: number): number {
  const it = items[index];
  if (!it) return totalDuration(items);
  return it.start + Math.min(Math.max(0, offset), it.dur);
}

/** File time for an offset into a clip, and back. */
export function fileTime(it: PlayItem, offset: number): number {
  return it.inT + Math.min(Math.max(0, offset), it.dur);
}

export function clipOffset(it: PlayItem, fileT: number): number {
  return Math.min(Math.max(0, fileT - it.inT), it.dur);
}

/** Half a frame: a clip counts as finished this close to its out point. */
export function atOutPoint(it: PlayItem, fileT: number, fps: number): boolean {
  return fileT >= it.outT - 0.5 / (fps || 24);
}

/** The next clip with video after `index` (what the spare <video> should preload). */
export function nextVideo(items: PlayItem[], index: number): number {
  for (let i = index + 1; i < items.length; i++) if (items[i].mp4) return i;
  return -1;
}

/** Start of `shot` on the cut's clock, or null if it isn't in the playlist. */
export function startOf(items: PlayItem[], shot: string): number | null {
  const it = items.find((x) => x.shot === shot);
  return it ? it.start : null;
}
