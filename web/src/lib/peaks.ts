// Phase 9b: waveforms under the timeline's clips, from GET /h3pipe/peaks.
// Peaks are fetched per clip at the zoom's resolution (rounded up to a power of
// two bins per second, so zooming doesn't refetch on every step), cached, and
// resampled to the canvas's pixels when drawn.

import { errText } from "../api";
import type { PeaksResult } from "../types";

/** The server computes peaks at 200 bins per second: asking for more is no finer. */
export const MAX_BINS_PER_SECOND = 200;
export const MIN_BINS_PER_SECOND = 8;
/** One request never asks for more bins than this. */
export const MAX_BINS = 8192;

/** Bins per second for a zoom (px per second): the next power of two, clamped. */
export function binsPerSecond(zoom: number): number {
  const z = Number.isFinite(zoom) && zoom > 0 ? zoom : MIN_BINS_PER_SECOND;
  const p = 2 ** Math.ceil(Math.log2(z));
  return Math.min(MAX_BINS_PER_SECOND, Math.max(MIN_BINS_PER_SECOND, p));
}

/** Bins to ask for a slice `seconds` long at `zoom`. */
export function binsFor(seconds: number, zoom: number): number {
  if (!(seconds > 0)) return 1;
  return Math.min(MAX_BINS, Math.max(1, Math.round(seconds * binsPerSecond(zoom))));
}

/**
 * `peaks` resampled to `n` values: the max of each output cell's span when
 * shrinking (a peak is never lost), the covering value when stretching.
 */
export function resamplePeaks(peaks: ArrayLike<number>, n: number): number[] {
  const m = peaks.length;
  const count = Math.max(0, Math.floor(n));
  if (!count) return [];
  if (!m) return new Array(count).fill(0);
  const out = new Array<number>(count);
  for (let i = 0; i < count; i++) {
    const a = (i * m) / count;
    const b = ((i + 1) * m) / count;
    let lo = Math.floor(a);
    let hi = Math.max(lo + 1, Math.ceil(b));
    if (hi > m) hi = m;
    if (lo >= hi) lo = hi - 1;
    let v = 0;
    for (let j = lo; j < hi; j++) {
      const x = Number(peaks[j]) || 0;
      if (x > v) v = x;
    }
    out[i] = v;
  }
  return out;
}

export interface Bar {
  x: number;
  y: number;
  w: number;
  h: number;
}

/**
 * The bars that draw `peaks` (0..255) in a `width` x `height` box, mirrored
 * about the middle: one bar per `step` px, at least 1 px tall so silence
 * still shows a line.
 */
export function peakBars(peaks: ArrayLike<number>, width: number, height: number, step = 1): Bar[] {
  const w = Math.max(0, Math.floor(width));
  const h = Math.max(0, height);
  const s = Math.max(1, Math.floor(step));
  if (!w || !h) return [];
  const n = Math.ceil(w / s);
  const vals = resamplePeaks(peaks, n);
  const mid = h / 2;
  return vals.map((v, i) => {
    const amp = Math.min(1, Math.max(0, v / 255));
    const bh = Math.max(1, Math.round(amp * h));
    return { x: i * s, y: Math.round(mid - bh / 2), w: Math.min(s, w - i * s), h: bh };
  });
}

/** Draw bars into a 2D context (a canvas, or a stub in tests). */
export function drawPeaks(
  ctx: Pick<CanvasRenderingContext2D, "clearRect" | "fillRect"> & { fillStyle: CanvasRenderingContext2D["fillStyle"] },
  peaks: ArrayLike<number>, width: number, height: number, color: string, step = 1,
) {
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = color;
  for (const b of peakBars(peaks, width, height, step)) ctx.fillRect(b.x, b.y, b.w, b.h);
}

// ---------------------------------------------------------------------------
// the cache
// ---------------------------------------------------------------------------

export interface PeaksQuery {
  ep: string;
  path: string;
  bins: number;
  start?: number | null;
  end?: number | null;
  /** part of the cache key only (not sent): a take re-rendered into the same
   * number keeps its path, so its `finished` time tells the files apart */
  version?: string | null;
}

export type PeaksEntry = { ok: true; data: PeaksResult } | { ok: false; error: string };

type Fetcher = (q: PeaksQuery) => Promise<PeaksResult>;

/** Key of a peaks request: the file, the slice (to the millisecond) and the bins. */
export function peaksKey(q: PeaksQuery): string {
  const r = (x: number | null | undefined) => (x == null || !Number.isFinite(x) ? "" : (Math.round(x * 1000) / 1000).toString());
  return `${q.ep}|${q.path}|${r(q.start)}|${r(q.end)}|${Math.round(q.bins)}${q.version ? `|${q.version}` : ""}`;
}

/**
 * Fetched peaks, by request, with in-flight requests shared and the oldest
 * dropped past `limit`. A failure is cached too (a missing file shouldn't be
 * asked for on every redraw); `forget(ep)` drops an episode's entries (after
 * a render replaces a take's file).
 */
export class PeaksCache {
  private done = new Map<string, PeaksEntry>();
  private flying = new Map<string, Promise<PeaksEntry>>();
  constructor(private fetcher: Fetcher, private limit = 400) {}

  peek(q: PeaksQuery): PeaksEntry | undefined {
    return this.done.get(peaksKey(q));
  }

  get(q: PeaksQuery): Promise<PeaksEntry> {
    const k = peaksKey(q);
    const hit = this.done.get(k);
    if (hit) {
      // most recently used goes last
      this.done.delete(k);
      this.done.set(k, hit);
      return Promise.resolve(hit);
    }
    const f = this.flying.get(k);
    if (f) return f;
    const p = this.fetcher(q)
      .then((data): PeaksEntry => ({ ok: true, data }))
      .catch((e): PeaksEntry => ({ ok: false, error: errText(e) }))
      .then((entry) => {
        this.flying.delete(k);
        this.done.set(k, entry);
        while (this.done.size > this.limit) {
          const oldest = this.done.keys().next().value;
          if (oldest === undefined) break;
          this.done.delete(oldest);
        }
        return entry;
      });
    this.flying.set(k, p);
    return p;
  }

  forget(ep?: string) {
    if (ep == null) {
      this.done.clear();
      return;
    }
    for (const k of [...this.done.keys()]) if (k.startsWith(ep + "|")) this.done.delete(k);
  }

  get size(): number {
    return this.done.size;
  }
}
