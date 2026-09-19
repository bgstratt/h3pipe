// Continuity keyframes (docs/API.md "Continuity keyframes"): a shot's first
// frame from the previous shot's last frame. The shot's neighbours in cut
// order, its keyframe refs, which targets read keyframes, and the viewer's
// playhead as a frame number. Pure functions; the components call them.

import type { EpisodeStatus, Ref, RefTake, Target, TargetList } from "../types";
import { findTarget, targetLabel, targetShort, videoTargets } from "./targets";
import { tn } from "./format";

export type KeyframeEnd = "first" | "last";
export const KEYFRAME_ENDS: KeyframeEnd[] = ["first", "last"];

export function keyframeRefId(shot: string, end: KeyframeEnd): string {
  return `shot:${shot}:${end}`;
}

/** A shot's keyframe refs, as `GET /h3pipe/refs` lists them (only those that exist). */
export function shotKeyframes(refs: Ref[] | undefined, shot: string): Partial<Record<KeyframeEnd, Ref>> {
  const out: Partial<Record<KeyframeEnd, Ref>> = {};
  for (const end of KEYFRAME_ENDS) {
    const r = refs?.find((x) => x.id === keyframeRefId(shot, end));
    if (r) out[end] = r;
  }
  return out;
}

/**
 * The shot `step` places from `shot` in the cut (-1: the previous shot, 1: the
 * next), skipping orphans as assemble does (h3edit.cut_neighbour). The status
 * lists shots in cut order. null at either end, or if the shot isn't there.
 */
export function cutNeighbour(st: Pick<EpisodeStatus, "shots"> | undefined, shot: string, step: -1 | 1): string | null {
  const shots = (st?.shots ?? []).filter((s) => !s.orphan);
  const i = shots.findIndex((s) => s.shot === shot);
  if (i < 0) return null;
  return shots[i + step]?.shot ?? null;
}

function keyframeCaps(t: Target | undefined): string[] | undefined {
  const k = t?.capabilities?.keyframes;
  return Array.isArray(k) ? k : undefined;
}

/** Whether a target reads keyframes: true / false from `capabilities.keyframes`,
 * null when the server doesn't say. */
export function usesKeyframes(list: TargetList | null | undefined, target: string | null | undefined): boolean | null {
  const caps = keyframeCaps(findTarget(list, target));
  return caps ? caps.length > 0 : null;
}

/**
 * The note shown when the shot's target doesn't read keyframes, e.g. "used by
 * LTX; not by MiniMax H3 Ref2VA". null when it does, or when the server doesn't
 * say (no capabilities).
 */
export function keyframeNote(list: TargetList | null | undefined, target: string | null | undefined): string | null {
  if (usesKeyframes(list, target) !== false) return null;
  const users = videoTargets(list).filter((t) => (keyframeCaps(t) ?? []).length > 0).map((t) => targetShort(list, t.id));
  const by = users.length ? `used by ${users.join(", ")}; ` : "";
  return `${by}not by ${targetLabel(list, target)}`;
}

/**
 * The frame the viewer shows, for "use this frame": the playhead time as a
 * frame number, or "last" at the very end (an ended video's time is its
 * duration, one frame past the last).
 */
export function frameAt(time: number, fps: number, duration?: number | null, ended = false): number | "last" {
  const f = fps > 0 ? fps : 24;
  if (ended || (duration != null && Number.isFinite(duration) && duration > 0 && time >= duration - 0.99 / f)) return "last";
  // frame n is on screen from n/fps to (n+1)/fps; the epsilon absorbs a
  // frame-stepped time that lands a hair under n/fps
  return Math.max(0, Math.floor(time * f + 0.01));
}

/** Where a keyframe take came from, for a caption: "sh010 t02 · frame 72 of 73". */
export function keyframeSource(t: Pick<RefTake, "source" | "from"> | undefined): string | null {
  if (!t || t.source !== "frame" || !t.from) return null;
  const f = t.from;
  const frame = f.frame != null ? ` · frame ${f.frame}${f.frames ? ` of ${f.frames}` : ""}` : "";
  return `${f.shot} ${f.pass && f.pass !== "proxy" ? `${f.pass} ` : ""}${f.take != null ? tn(f.take) : ""}${frame}`.replace(/\s+/g, " ").trim();
}

/** The live (picked) take of a keyframe ref. */
export function liveTake(r: Ref | undefined): RefTake | undefined {
  return r && r.picked != null ? r.takes.find((t) => t.take === r.picked) : undefined;
}
