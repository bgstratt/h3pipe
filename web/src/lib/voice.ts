// Phase 9c-B: generated voice refs. Which audio target a voice generates
// with, how long a sample may be, and the span maths behind "use a line from
// a take" (drag a span on a take's waveform, preview it, save it as a
// candidate). Pure functions.

import type { Ref, RefTake, Target, TargetList, VoiceLineSource } from "../types";

/** The built-in audio target (targets.DEFAULT_AUDIO_TARGET). */
export const VOICE_DEFAULT = "ltx2_voice";

/**
 * How long a generated sample may be when nothing says. The server clamps a
 * request into the target's own range and refuses one outside it, but
 * `GET /h3pipe/targets` only publishes `capabilities.max_seconds` — the
 * minimum isn't served (see the report's contract gaps), so this is the
 * floor the field offers.
 */
export const MIN_VOICE_SECONDS = 2;
export const MAX_VOICE_SECONDS = 20;

export function audioTargets(list: TargetList | null | undefined): Target[] {
  return (list?.targets ?? []).filter((t) => t.kind === "audio");
}

/** "text to speech, up to 20 s" / "voice cloning, up to 20 s". */
export function voiceModeText(t: Pick<Target, "capabilities"> | undefined): string {
  const c = t?.capabilities;
  if (!c) return "";
  const mode = c.mode === "voice_clone" ? "voice cloning" : c.mode === "t2a" ? "text to speech" : String(c.mode ?? "");
  const secs = c.max_seconds ? `up to ${Math.round(c.max_seconds)} s` : "";
  return [mode, secs].filter(Boolean).join(", ");
}

/**
 * What the target can and can't do, for the picker's note. `ltx2_voice`
 * reports `reference_audio: false` — the node is there but the installed
 * LTX-2.5 transformers carry no audio ID-LoRA weights, so it can't copy an
 * existing voice; the wording alone makes it (API.md "Phase 9c-B as built").
 */
export function voiceCapNotes(t: Pick<Target, "capabilities"> | undefined): string[] {
  const c = t?.capabilities;
  if (!c) return [];
  const out: string[] = [];
  out.push(c.reference_audio
    ? "It can copy a voice from the sample the series config names."
    : "It can't copy an existing voice: the sample is made from the wording only (the character's `voice` line and design), so it won't sound like a recording you already have. Use “a line from a take…” for that.");
  if (c.max_seconds) out.push(`Samples are at most ${Math.round(c.max_seconds)} seconds.`);
  if (c.negative_prompt === false) out.push("It takes no negative prompt.");
  return out;
}

/** The longest sample this ref's target makes: its `effective`, else the target's. */
export function maxVoiceSeconds(r: Pick<Ref, "effective"> | null | undefined, t?: Pick<Target, "capabilities">): number {
  const eff = r?.effective?.max_seconds;
  if (typeof eff === "number" && eff > 0) return eff;
  const cap = t?.capabilities?.max_seconds;
  return typeof cap === "number" && cap > 0 ? cap : MAX_VOICE_SECONDS;
}

/** What the seconds field starts at: what a generate would use now. */
export function defaultVoiceSeconds(r: Pick<Ref, "effective"> | null | undefined, t?: Pick<Target, "capabilities">): number {
  const s = r?.effective?.seconds;
  if (typeof s === "number" && s > 0) return Math.round(s);
  return Math.min(8, maxVoiceSeconds(r, t));
}

/** "the longest line Dean says in this episode" / "a neutral sentence". */
export function lineSourceText(src: VoiceLineSource | null | undefined): string {
  switch (src) {
    case "script": return "the character's longest line in this episode's script";
    case "neutral": return "a fixed neutral sentence (this character says nothing in this episode)";
    case "request": return "the prompt typed for this generate";
    case "override": return "the ref's prompt override";
    default: return "";
  }
}

/** A voice candidate's one-line description: its length and what it says. */
export function candidateText(t: Pick<RefTake, "seconds" | "line" | "source">): string {
  const bits: string[] = [];
  if (t.seconds != null) bits.push(`${Number(t.seconds).toFixed(t.seconds >= 10 ? 0 : 1)} s`);
  if (t.source === "from_take") bits.push("from a take");
  if (t.line) bits.push(`“${t.line.length > 60 ? `${t.line.slice(0, 59)}…` : t.line}”`);
  return bits.join(" · ");
}

/** Where a candidate cut out of a take came from: "sh020 t02 (proxy) 1.20–3.40 s". */
export function fromTakeText(t: Pick<RefTake, "from" | "source">): string {
  const f = t.from;
  if (!f || t.source !== "from_take") return "";
  const span = f.start != null && f.end != null ? ` ${f.start.toFixed(2)}–${f.end.toFixed(2)} s` : "";
  const take = f.take != null ? ` t${String(f.take).padStart(2, "0")}` : "";
  return `${f.shot}${take}${f.pass ? ` (${f.pass})` : ""}${span}`;
}

// ---------------------------------------------------------------------------
// the span (POST /h3pipe/refs/voice-from-take)
// ---------------------------------------------------------------------------

/** h3refs.MIN_CLIP / MAX_CLIP: a shorter span is a click, not a voice. */
export const SPAN_MIN = 0.2;
export const SPAN_MAX = 30;

export interface Span {
  start: number;
  end: number;
}

function r3(n: number): number {
  return Math.round(n * 1000) / 1000;
}

/** Why the server would refuse this span (null: it takes it). */
export function spanProblem(s: Span, duration: number | null | undefined): string | null {
  if (!(s.end > s.start)) return "Drag from left to right to choose a span.";
  const len = s.end - s.start;
  if (s.start < 0) return "The span starts before the take.";
  if (len < SPAN_MIN) return `The span must be at least ${SPAN_MIN}s long (it is ${len.toFixed(2)}s).`;
  if (len > SPAN_MAX) return `The span must be at most ${SPAN_MAX}s long (it is ${len.toFixed(2)}s).`;
  if (duration != null && duration > 0 && s.start >= duration) {
    return `The take is ${duration.toFixed(2)}s long: the span starts after it ends.`;
  }
  return null;
}

/**
 * A dragged span, made legal: inside 0..duration, at least SPAN_MIN and at
 * most SPAN_MAX long. A drag in either direction gives the same span.
 */
export function clampSpan(a: number, b: number, duration: number | null | undefined): Span {
  const total = duration != null && duration > 0 ? duration : Math.max(a, b, SPAN_MIN);
  let start = Math.min(Math.max(0, Math.min(a, b)), total);
  let end = Math.min(Math.max(0, Math.max(a, b)), total);
  if (end - start > SPAN_MAX) end = start + SPAN_MAX;
  if (end - start < SPAN_MIN) {
    // grow to the right if there is room, else to the left
    end = start + SPAN_MIN;
    if (end > total) {
      end = total;
      start = Math.max(0, end - SPAN_MIN);
    }
  }
  return { start: r3(start), end: r3(end) };
}

/** Seconds at `x` pixels across a `width`-wide waveform of `duration` seconds. */
export function xToTime(x: number, width: number, duration: number): number {
  if (!(width > 0) || !(duration > 0)) return 0;
  return Math.min(duration, Math.max(0, (x / width) * duration));
}

/** The pixel a time sits at, clamped into the box. */
export function timeToX(t: number, width: number, duration: number): number {
  if (!(duration > 0)) return 0;
  return Math.min(width, Math.max(0, (t / duration) * width));
}

/** A span's left edge and width in pixels. */
export function spanBox(s: Span, width: number, duration: number): { left: number; width: number } {
  const a = timeToX(s.start, width, duration);
  const b = timeToX(s.end, width, duration);
  return { left: a, width: Math.max(1, b - a) };
}

/** Move one edge of a span (a handle drag), keeping it legal. */
export function moveEdge(s: Span, edge: "start" | "end", t: number, duration: number | null | undefined): Span {
  return edge === "start" ? clampSpan(t, s.end, duration) : clampSpan(s.start, t, duration);
}

/** Slide the whole span by `d` seconds without changing its length. */
export function slideSpan(s: Span, d: number, duration: number | null | undefined): Span {
  const total = duration != null && duration > 0 ? duration : s.end;
  const len = s.end - s.start;
  const start = Math.min(Math.max(0, s.start + d), Math.max(0, total - len));
  return { start: r3(start), end: r3(start + len) };
}

/** "1.20 – 3.40 s · 2.20 s". */
export function spanText(s: Span): string {
  return `${s.start.toFixed(2)} – ${s.end.toFixed(2)} s · ${(s.end - s.start).toFixed(2)} s`;
}

/** The takes of a shot whose sound a span can be cut from (`take.audio`). */
export function takesWithAudio<T extends { audio?: string | null; status: string }>(takes: T[]): T[] {
  return takes.filter((t) => t.status === "ok" && !!t.audio);
}
