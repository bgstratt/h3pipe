// Phase 9b: the episode's recorded dialogue (`track`) under Play all, as
// `h3assemble --audio master` lays it: the recording runs continuously from the
// first clip's dialogue window (its `audio_in`, else the earliest window) plus
// that clip's trim-in, one second of recording per second of cut. Each clip
// with a window *should* hear its own slice (`audio_in` + its trim-in); when
// trims, reordering or a changed length move a clip off its slice, the
// recording drifts from there on, and the player says so.

import type { PlayItem } from "./playlist";

/** Where the recording starts under cut time 0. */
export function masterStart(items: PlayItem[], fps: number, base = 0): number {
  const first = items[0];
  if (!first) return base;
  const f = fps > 0 ? fps : 24;
  return (first.audioIn ?? base) + first.trimIn / f;
}

/** The recording's time under cut time `t`. */
export function recordingAt(start: number, t: number): number {
  return start + Math.max(0, t);
}

/** Where a clip's own dialogue starts on the track (its window plus its trim-in), or null. */
export function expectedAt(it: PlayItem, fps: number): number | null {
  if (it.audioIn == null) return null;
  return it.audioIn + it.trimIn / (fps > 0 ? fps : 24);
}

/** The slice of the track a clip's picture lines up with (for its waveform), or null. */
export function trackSlice(it: PlayItem, fps: number): { start: number; end: number } | null {
  const s = expectedAt(it, fps);
  if (s == null || !(it.dur > 0)) return null;
  return { start: s, end: s + it.dur };
}

export interface Drift {
  shot: string;
  /** seconds the recording is late (+) or early (-) under this clip */
  drift: number;
}

export interface MasterSync {
  start: number;
  /** clips with a dialogue window that hear someone else's slice */
  drifts: Drift[];
  /** h3assemble's warnings, in its words; empty when the recording lines up */
  warnings: string[];
}

/**
 * How the recording lines up under the cut. `outOfOrder`: the cut isn't in
 * script order (the caller knows it from `out_of_order` / `script_index`).
 * A clip counts as drifting when it's more than a frame off its slice.
 */
export function masterSync(items: PlayItem[], fps: number, base = 0, outOfOrder = false): MasterSync {
  const f = fps > 0 ? fps : 24;
  const start = masterStart(items, f, base);
  const tol = 1 / f + 1e-3;
  const drifts: Drift[] = [];
  for (const it of items) {
    const want = expectedAt(it, f);
    if (want == null) continue;
    const d = recordingAt(start, it.start) - want;
    if (Math.abs(d) > tol) drifts.push({ shot: it.shot, drift: Math.round(d * 1000) / 1000 });
  }
  const warnings: string[] = [];
  const trimmed = items.filter((it) => it.audioIn != null && (it.trimIn > 0 || it.trimOut > 0)).map((it) => it.shot);
  if (trimmed.length) {
    warnings.push(`${trimmed.join(", ")} ${trimmed.length === 1 ? "has" : "have"} a dialogue window and a cut.json trim; the recording drifts from there on`);
  }
  if (outOfOrder) warnings.push("the cut is not in script order; the recording only lines up with script order");
  if (drifts.length && !warnings.length) {
    const d = drifts[0];
    warnings.push(`the recording is ${Math.abs(d.drift).toFixed(2)} s ${d.drift > 0 ? "late" : "early"} under ${d.shot}${drifts.length > 1 ? ` (and ${drifts.length - 1} more)` : ""}`);
  }
  return { start, drifts, warnings };
}

/** Whether the <audio> element should be re-seeked: it's further than `slack` s off. */
export function needsResync(current: number, want: number, slack = 0.12): boolean {
  return !Number.isFinite(current) || Math.abs(current - want) > slack;
}
