// Phase 9d: a shot's audio from elsewhere. A cut entry's `audio` says where a
// clip's sound comes from — another take, a media file in the episode, or
// silence — with `start` (seconds into the source), `offset` (seconds against
// the picture) and `gain`. Pure: the maths behind the window's handles, the
// badge's words, and what the server would refuse.
//
// The clock. A clip is `clipSeconds` long on the cut's clock, 0..clipSeconds.
// At clip time t the source is heard at `start + (t - offset)`: before
// `offset` there is silence, and the source running out leaves silence too.
// The audio is never allowed to change the clip's length.

import type { CutAudioKind, CutAudioSource, CutEntry, CutInfo, Pass, ShotStatus, TakeSummary } from "../types";
import { tn } from "./format";

/** PUT /h3pipe/cut refuses a gain outside this. */
export const GAIN_MIN = 0;
export const GAIN_MAX = 4;
export const GAIN_DEFAULT = 1;

/** Seconds are stored to the millisecond (as the span maths does). */
export function r3(n: number): number {
  return Number.isFinite(n) ? Math.round(n * 1000) / 1000 : 0;
}

function num(v: unknown, fallback = 0): number {
  return typeof v === "number" && Number.isFinite(v) ? v : fallback;
}

export const startOf = (a: CutAudioSource | null | undefined): number => Math.max(0, num(a?.start));
export const offsetOf = (a: CutAudioSource | null | undefined): number => num(a?.offset);
export const gainOf = (a: CutAudioSource | null | undefined): number => num(a?.gain, GAIN_DEFAULT);

/** The clip's own audio (no `audio` in the entry) reads as null everywhere. */
export function audioOf(e: { audio?: CutAudioSource | null } | null | undefined): CutAudioSource | null {
  const a = e?.audio;
  return a && typeof a === "object" && typeof a.source === "string" ? a : null;
}

/** A take source's shot: its own when the source doesn't name one. */
export function audioShot(a: CutAudioSource | null | undefined, own: string): string {
  return a?.shot || own;
}

// ---------------------------------------------------------------------------
// clamping
// ---------------------------------------------------------------------------

/** `start` seconds into the source: 0 or more, and inside it when its length is known. */
export function clampStart(want: number, sourceDuration: number | null | undefined): number {
  const v = Math.max(0, num(want));
  if (sourceDuration != null && sourceDuration > 0) return r3(Math.min(v, sourceDuration));
  return r3(v);
}

/**
 * `offset` seconds against the picture. Shifting by more than the clip's own
 * length would leave nothing but silence, so that's as far as it goes.
 */
export function clampOffset(want: number, clipSeconds: number | null | undefined): number {
  const v = num(want);
  const lim = clipSeconds != null && clipSeconds > 0 ? clipSeconds : 0;
  if (!lim) return r3(v);
  return r3(Math.min(lim, Math.max(-lim, v)));
}

/** A linear gain, inside the range the server takes. */
export function clampGain(want: number): number {
  const v = num(want, GAIN_DEFAULT);
  return Math.round(Math.min(GAIN_MAX, Math.max(GAIN_MIN, v)) * 100) / 100;
}

/** Seconds at `x` pixels across a `width`-wide box of `duration` seconds. */
export function xToTime(x: number, width: number, duration: number): number {
  if (!(width > 0) || !(duration > 0)) return 0;
  return Math.min(duration, Math.max(0, (x / width) * duration));
}

/** The pixel a time sits at (not clamped: a marker may run past either edge). */
export function timeToX(t: number, width: number, duration: number): number {
  if (!(duration > 0) || !(width > 0)) return 0;
  return (t / duration) * width;
}

// ---------------------------------------------------------------------------
// the layout: where the source is heard under the picture
// ---------------------------------------------------------------------------

export interface AudioLayout {
  /** seconds of silence before the source is heard */
  head: number;
  /** seconds of the source heard under the clip */
  used: number;
  /** seconds of silence after it (the source ran out, or `offset` is negative) */
  tail: number;
  /** the first and last source time heard */
  srcIn: number;
  srcOut: number;
  /** the whole picture window in source time (it can fall outside the file) */
  pictureIn: number;
  pictureOut: number;
  /** nothing of the source is heard at all */
  silent: boolean;
  /** the source doesn't cover the whole clip (head or tail silence) */
  short: boolean;
}

/**
 * Where the chosen source falls under a clip `clipSeconds` long.
 * `sourceDuration` null: the file's length isn't known yet, so the source is
 * assumed long enough (the window redraws when the peaks arrive).
 */
export function audioLayout(
  a: CutAudioSource | null | undefined, clipSeconds: number, sourceDuration: number | null | undefined,
): AudioLayout {
  const clip = Math.max(0, num(clipSeconds));
  const start = startOf(a);
  const offset = offsetOf(a);
  const head = Math.min(clip, Math.max(0, offset));
  const srcIn = start + Math.max(0, -offset);
  const room = clip - head;
  const avail = sourceDuration != null && sourceDuration > 0 ? Math.max(0, sourceDuration - srcIn) : room;
  const used = Math.max(0, Math.min(room, avail));
  return {
    head: r3(head),
    used: r3(used),
    tail: r3(Math.max(0, clip - head - used)),
    srcIn: r3(srcIn),
    srcOut: r3(srcIn + used),
    pictureIn: r3(start - offset),
    pictureOut: r3(start - offset + clip),
    silent: !(used > 0),
    short: r3(used) < r3(clip),
  };
}

/**
 * The source time heard at clip time `t`, or null where the clip is silent
 * (before `offset`, or past the end of the source). The preview player reads it.
 */
export function previewAt(
  a: CutAudioSource | null | undefined, t: number, clipSeconds: number, sourceDuration: number | null | undefined,
): number | null {
  // the clip's own sound, and silence, have no source to seek
  if (!a || a.source === "none") return null;
  const L = audioLayout(a, clipSeconds, sourceDuration);
  if (L.silent) return null;
  const at = num(t);
  if (at < L.head || at > L.head + L.used) return null;
  return r3(L.srcIn + (at - L.head));
}

/** The picture window drawn over the source waveform, in pixels. */
export function pictureBox(
  a: CutAudioSource | null | undefined, clipSeconds: number, sourceDuration: number | null | undefined, width: number,
): { left: number; width: number } {
  const L = audioLayout(a, clipSeconds, sourceDuration);
  const dur = sourceDuration != null && sourceDuration > 0 ? sourceDuration : Math.max(clipSeconds, L.pictureOut, 1);
  const a0 = timeToX(L.pictureIn, width, dur);
  const b0 = timeToX(L.pictureOut, width, dur);
  return { left: a0, width: Math.max(1, b0 - a0) };
}

/** The heard part of the source, drawn over the clip's own length, in pixels. */
export function clipBox(
  a: CutAudioSource | null | undefined, clipSeconds: number, sourceDuration: number | null | undefined, width: number,
): { left: number; width: number } {
  const L = audioLayout(a, clipSeconds, sourceDuration);
  const dur = clipSeconds > 0 ? clipSeconds : 1;
  const left = timeToX(L.head, width, dur);
  return { left, width: Math.max(1, timeToX(L.head + L.used, width, dur) - left) };
}

// ---------------------------------------------------------------------------
// writing an entry
// ---------------------------------------------------------------------------

/**
 * `a` as cut.json would hold it: the fields the source uses, defaults left
 * out, seconds rounded. Null for "the clip's own sound", so an entry that
 * says nothing and one that says "own" write the same file.
 */
export function normalizeAudio(a: CutAudioSource | null | undefined, ownShot?: string): CutAudioSource | null {
  if (!a || (a.source !== "take" && a.source !== "file" && a.source !== "none")) return null;
  if (a.source === "none") return { source: "none" };
  const out: CutAudioSource = { source: a.source };
  if (a.source === "take") {
    const shot = a.shot || ownShot || "";
    if (shot) out.shot = shot;
    if (a.take != null && Number.isFinite(a.take)) out.take = Math.round(a.take);
    if (a.pass === "proxy" || a.pass === "final") out.pass = a.pass;
  } else {
    if (!a.path) return null;
    out.path = a.path.replace(/\\/g, "/");
  }
  const start = r3(Math.max(0, num(a.start)));
  const offset = r3(num(a.offset));
  const gain = clampGain(num(a.gain, GAIN_DEFAULT));
  if (start) out.start = start;
  if (offset) out.offset = offset;
  if (gain !== GAIN_DEFAULT) out.gain = gain;
  return out;
}

/** Two audio sources write the same entry. */
export function sameAudio(a: CutAudioSource | null | undefined, b: CutAudioSource | null | undefined): boolean {
  const x = normalizeAudio(a);
  const y = normalizeAudio(b);
  if (!x || !y) return !x && !y;
  return x.source === y.source && (x.shot ?? "") === (y.shot ?? "") && (x.take ?? null) === (y.take ?? null)
    && (x.pass ?? null) === (y.pass ?? null) && (x.path ?? "") === (y.path ?? "")
    && startOf(x) === startOf(y) && offsetOf(x) === offsetOf(y) && gainOf(x) === gainOf(y);
}

// ---------------------------------------------------------------------------
// what the server would refuse, and what's merely worth saying
// ---------------------------------------------------------------------------

/** Why PUT /h3pipe/cut would answer 400 (null: it takes it). */
export function audioProblem(a: CutAudioSource | null | undefined, sourceHasSound = true): string | null {
  if (!a) return null;
  if (a.source !== "take" && a.source !== "file" && a.source !== "none") return `Unknown audio source "${String(a.source)}".`;
  if (a.source === "none") return null;
  if (a.source === "take") {
    if (!a.shot) return "Pick the shot the sound comes from.";
    if (a.take == null) return `Pick a take of ${a.shot}.`;
    if (!sourceHasSound) return `${a.shot} ${tn(a.take)} has no sound.`;
  } else {
    if (!a.path) return "Pick a file inside the episode.";
    if (/^([a-zA-Z]:|[\\/])/.test(a.path)) return `${a.path} is outside the episode (give a path inside it).`;
    if (!sourceHasSound) return `${a.path.split("/").pop()} has no audio stream.`;
  }
  if (num(a.start) < 0) return "The start must be 0 s or more.";
  const g = num(a.gain, GAIN_DEFAULT);
  if (g < GAIN_MIN || g > GAIN_MAX) return `The gain must be between ${GAIN_MIN} and ${GAIN_MAX} (it is ${g}).`;
  return null;
}

/** Advice the save doesn't block on: the source doesn't cover the clip. */
export function audioNote(L: AudioLayout, clipSeconds: number): string {
  if (L.silent) return `Nothing is heard: the whole ${fmt(clipSeconds)} clip is silent.`;
  if (!L.short) return "";
  const bits: string[] = [];
  if (L.head > 0) bits.push(`${fmt(L.head)} of silence first`);
  if (L.tail > 0) bits.push(`${fmt(L.tail)} of silence at the end (the source runs out)`);
  return bits.length ? `${bits.join(", ")} — the clip keeps its ${fmt(clipSeconds)}.` : "";
}

function fmt(s: number): string {
  return `${s.toFixed(2)} s`;
}

// ---------------------------------------------------------------------------
// the badge
// ---------------------------------------------------------------------------

/** The file name at the end of an episode-relative path. */
export function baseName(path: string | null | undefined): string {
  return (path ?? "").split(/[\\/]/).filter(Boolean).pop() ?? "";
}

/**
 * The speaker badge's words. The server sends `audio_why`; this is what it
 * would say, and what the badge falls back to (an older server, and the
 * optimistic update before the server answers).
 */
export function audioWhy(a: CutAudioSource | null | undefined, ownShot = ""): string {
  if (!a) return "";
  if (a.source === "none") return "silent";
  if (a.source === "file") return baseName(a.path) || "a file";
  const shot = audioShot(a, ownShot);
  const take = a.take != null ? ` ${tn(a.take)}` : "";
  return `${shot}${take}`.trim();
}

export interface AudioBadgeInfo {
  label: string;
  title: string;
  kind: CutAudioKind;
}

/** The badge a clip with an audio source shows, or null when it plays its own. */
export function audioBadgeOf(cut: CutInfo | null | undefined, shot: string): AudioBadgeInfo | null {
  const a = audioOf(cut);
  if (!a) return null;
  const label = cut?.audio_why || audioWhy(a, shot);
  const bits: string[] = [];
  if (a.source === "none") bits.push(`${shot} plays silent: this clip's own sound is muted in the cut.`);
  else if (a.source === "file") bits.push(`${shot} plays ${a.path ?? "a file"} instead of its own take's sound.`);
  else bits.push(`${shot} plays ${label}'s sound${a.pass ? ` (${a.pass})` : ""} instead of its own.`);
  if (a.source !== "none") {
    const extra: string[] = [];
    if (startOf(a)) extra.push(`from ${startOf(a).toFixed(2)} s in`);
    if (offsetOf(a)) extra.push(`${offsetOf(a) > 0 ? "+" : ""}${offsetOf(a).toFixed(2)} s against the picture`);
    if (gainOf(a) !== GAIN_DEFAULT) extra.push(`gain ${gainOf(a)}`);
    if (extra.length) bits.push(extra.join(" · "));
    if (cut?.audio_file) bits.push(cut.audio_file);
  }
  bits.push("Its menu's “Audio from…” changes it; the inspector's Cut section clears it.");
  return { label, title: bits.join("\n"), kind: a.source };
}

/** What the Play all toggle says when the recording is on: per-clip audio is ignored. */
export const RECORDING_IGNORES_AUDIO =
  "Play all is on the recording, so the clips' own audio sources are ignored (h3assemble --audio master wins too, and warns which clips it overrides).";

/** How many clips of the cut have an audio source of their own. */
export function clipsWithAudio(shots: Pick<ShotStatus, "shot" | "cut">[]): string[] {
  return shots.filter((s) => audioOf(s.cut)).map((s) => s.shot);
}

// ---------------------------------------------------------------------------
// the takes a source can come from
// ---------------------------------------------------------------------------

/** The takes of a shot whose sound can be borrowed (`take.audio`, as /episode sends it). */
export function takesWithSound(takes: TakeSummary[] | undefined): TakeSummary[] {
  return (takes ?? []).filter((t) => t.status === "ok" && !!t.audio);
}

/** Every shot with at least one take with sound, the clip's own shot first. */
export function soundShots<T extends { shot: string; takes: TakeSummary[] }>(shots: T[], own: string): T[] {
  const list = shots.filter((s) => takesWithSound(s.takes).length);
  const mine = list.filter((s) => s.shot === own);
  return [...mine, ...list.filter((s) => s.shot !== own)];
}

/** The file a take source would play, from a pass's status (null: not found). */
export function takeAudioFile(shots: ShotStatus[] | undefined, shot: string, take: number | null | undefined): string | null {
  if (take == null) return null;
  return shots?.find((s) => s.shot === shot)?.takes.find((t) => t.take === take)?.audio ?? null;
}

/**
 * What a clip will actually play, worked out here for the optimistic update
 * (the server sends `audio_file`). Null for silence, and for a take in the
 * other pass, whose status this surface may not hold.
 */
export function resolveAudioFile(
  a: CutAudioSource | null | undefined, ownShot: string, pass: Pass, shots: ShotStatus[] | undefined,
): string | null {
  if (!a || a.source === "none") return null;
  if (a.source === "file") return a.path ?? null;
  if (a.pass && a.pass !== pass) return null;
  return takeAudioFile(shots, audioShot(a, ownShot), a.take ?? null);
}

/** A cut entry's audio, for `entriesOf` (`CutEntry` and `CutInfo` both carry it). */
export function entryAudio(e: CutEntry | undefined): CutAudioSource | null {
  return normalizeAudio(audioOf(e));
}
