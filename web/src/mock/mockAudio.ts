// Phase 9b in the mock: a recorded dialogue track and the peaks route. The
// track is synthetic (a "voice" per dialogue window: a buzz at the shot's own
// pitch with a syllable envelope, low room noise between), so the dev page
// can play it under Play all and draw it. Takes' peaks are made up from their
// path. Everything is deterministic.

import { resamplePeaks } from "../lib/peaks";
import type { PeaksResult } from "../types";

export const PEAK_RATE = 200; // bins per second, as the server keeps them
export const TRACK_RATE = 8000;

export interface MockWindow {
  shot: string;
  audioIn: number;
  audioOut: number;
}

function hash(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

/** A tiny deterministic PRNG (mulberry32). */
function rng(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** max |amplitude| per 1/PEAK_RATE s, 0..255, of a sample function. */
function peaksOf(sample: (t: number) => number, duration: number): number[] {
  const n = Math.ceil(duration * PEAK_RATE);
  const per = TRACK_RATE / PEAK_RATE;
  const out = new Array<number>(n);
  for (let i = 0; i < n; i++) {
    let m = 0;
    for (let j = 0; j < per; j++) m = Math.max(m, Math.abs(sample((i * per + j) / TRACK_RATE)));
    out[i] = Math.min(255, Math.round(m * 255));
  }
  return out;
}

/** A playable 16-bit mono WAV of a sample function, as an object URL (null
 * outside a browser: the tests run in node). */
function wavUrl(sample: (t: number) => number, duration: number): string | null {
  if (typeof Blob === "undefined" || typeof URL === "undefined" || typeof URL.createObjectURL !== "function") return null;
  const n = Math.ceil(duration * TRACK_RATE);
  const buf = new ArrayBuffer(44 + n * 2);
  const v = new DataView(buf);
  const str = (o: number, s: string) => [...s].forEach((c, i) => v.setUint8(o + i, c.charCodeAt(0)));
  str(0, "RIFF");
  v.setUint32(4, 36 + n * 2, true);
  str(8, "WAVE");
  str(12, "fmt ");
  v.setUint32(16, 16, true);
  v.setUint16(20, 1, true);
  v.setUint16(22, 1, true);
  v.setUint32(24, TRACK_RATE, true);
  v.setUint32(28, TRACK_RATE * 2, true);
  v.setUint16(32, 2, true);
  v.setUint16(34, 16, true);
  str(36, "data");
  v.setUint32(40, n * 2, true);
  for (let i = 0; i < n; i++) v.setInt16(44 + i * 2, Math.max(-1, Math.min(1, sample(i / TRACK_RATE))) * 32767, true);
  return URL.createObjectURL(new Blob([buf], { type: "audio/wav" }));
}

/**
 * Phase 9c: one generated voice sample — a buzz at the speaker's own pitch
 * with a syllable envelope, so a candidate can be played and drawn. `seed`
 * (the ref and take) decides the pitch, so two candidates differ.
 */
export function makeVoice(seed: string, duration: number) {
  const h = hash(seed);
  const pitch = 95 + (h % 160);
  const phase = (h % 977) / 977;
  const noise = rng(h);
  const sample = (t: number): number => {
    if (t < 0.08 || t > duration - 0.08) return (noise() - 0.5) * 0.02;
    const syll = Math.max(0, Math.sin(Math.PI * 2 * (3.5 * t + phase)));
    const env = 0.2 + 0.8 * syll;
    const saw = ((t * pitch) % 1) * 2 - 1;
    return (noise() - 0.5) * 0.02 + 0.7 * env * (0.7 * saw + 0.3 * Math.sin(2 * Math.PI * 2 * pitch * t));
  };
  let peaks: number[] | null = null;
  let url: string | null = null;
  return {
    duration,
    peaks: () => (peaks ??= peaksOf(sample, duration)),
    url: () => (url ??= wavUrl(sample, duration)),
  };
}

/** The synthetic track: one sample function over `duration` seconds. */
export function makeTrack(windows: MockWindow[], duration: number) {
  const ws = windows.map((w) => ({ ...w, pitch: 110 + (hash(w.shot) % 140), phase: (hash(w.shot) % 1000) / 1000 }));
  const noise = rng(7);
  const sample = (t: number): number => {
    const w = ws.find((x) => t >= x.audioIn && t < x.audioOut);
    const room = (noise() - 0.5) * 0.02;
    if (!w) return room;
    const local = t - w.audioIn;
    const len = w.audioOut - w.audioIn;
    // speech-ish: ~4 syllables a second, a short pause at each end
    const edge = Math.min(1, local / 0.15, (len - local) / 0.15);
    const syll = Math.max(0, Math.sin(Math.PI * 2 * (4 * local + w.phase)));
    const env = edge * (0.25 + 0.75 * syll);
    const saw = ((t * w.pitch) % 1) * 2 - 1;
    return room + 0.6 * env * (0.7 * saw + 0.3 * Math.sin(2 * Math.PI * 2 * w.pitch * t));
  };
  let peaks: number[] | null = null;
  let url: string | null = null;
  return {
    duration,
    /** max |amplitude| per 1/200 s, 0..255 */
    peaks: () => (peaks ??= peaksOf(sample, duration)),
    /** A playable WAV (object URL), made on first use; null outside a browser. */
    url: () => (url ??= wavUrl(sample, duration)),
  };
}

/** Made-up peaks for a take's audio `seconds` long, from its path. */
export function takePeaks(path: string, seconds: number): number[] {
  const r = rng(hash(path));
  const n = Math.max(1, Math.ceil(seconds * PEAK_RATE));
  const out = new Array<number>(n);
  let level = 0.3;
  for (let i = 0; i < n; i++) {
    if (i % 40 === 0) level = 0.15 + r() * 0.75;
    const wob = 0.6 + 0.4 * Math.sin(i / 7);
    out[i] = Math.min(255, Math.round(255 * level * wob * (0.7 + 0.3 * r())));
  }
  return out;
}

/** GET /h3pipe/peaks over full-resolution peaks: the start..end slice, resampled to `bins`. */
export function slicePeaks(full: number[], duration: number, bins: number, start?: number | null, end?: number | null): PeaksResult {
  const s = Math.max(0, start ?? 0);
  const e = Math.min(duration, end ?? duration);
  const a = Math.floor(s * PEAK_RATE);
  const b = Math.max(a + 1, Math.ceil(e * PEAK_RATE));
  const part = e > s ? full.slice(a, Math.min(full.length, b)) : [];
  const n = Math.max(1, Math.round(bins));
  return { duration, bins: n, peaks: part.length ? resamplePeaks(part, n) : new Array(n).fill(0) };
}
