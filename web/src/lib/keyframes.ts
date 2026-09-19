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
  const users = [...new Set(videoTargets(list).filter((t) => (keyframeCaps(t) ?? []).length > 0).map((t) => targetShort(list, t.id)))];
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

// ---------------------------------------------------------------------------
// Phase 8.5: keyframes as needed refs (API.md "Keyframes as needed refs")
// ---------------------------------------------------------------------------

/** The shot and end a keyframe ref is for: its own fields, else its id. */
export function keyframeOf(r: Pick<Ref, "id"> & Partial<Pick<Ref, "shot" | "which">>): { shot: string; which: KeyframeEnd } | null {
  const m = /^shot:(.+):(first|last)$/.exec(r.id);
  const shot = r.shot || m?.[1];
  const which = (r.which === "first" || r.which === "last" ? r.which : m?.[2]) as KeyframeEnd | undefined;
  return shot && which ? { shot, which } : null;
}

export function isKeyframeRef(r: Pick<Ref, "id" | "kind" | "scope">): boolean {
  return r.kind === "keyframe" || r.scope === "shot" || /^shot:/.test(r.id);
}

/** "required" / "optional" / "" (an older server, or a keyframe no target reads). */
export function needLabel(need: Ref["need"]): string {
  return need === "required" ? "required" : need === "optional" ? "optional" : "";
}

/** A keyframe's method in words: how it will be filled. */
export function methodLabel(method: Ref["method"], which: KeyframeEnd = "first"): string {
  switch (method) {
    case null:
    case undefined:
    case "":
      return "";
    case "continuity":
      return which === "first" ? "from the previous shot" : "from the next shot";
    case "generate":
      return "generate a still";
    case "import":
      return "import a file";
    case "none":
      return "none (don't use one)";
    default:
      // the script names a file
      return `file: ${method}`;
  }
}

/** A tooltip for the method: what it means. */
export function methodTitle(method: Ref["method"], which: KeyframeEnd = "first"): string {
  switch (method) {
    case "continuity":
      return `Continuity: ${which === "first" ? "the previous shot's last frame" : "the next shot's first frame"}, from the take the cut uses. Generate missing makes a still instead when that shot has no usable take.`;
    case "generate":
      return "A still made by the keyframe image model from the shot's description";
    case "import":
      return "The script asks for an imported image: use Import…";
    case "none":
      return "The script says not to use one, even though the target could";
    case null:
    case undefined:
    case "":
      return "";
    default:
      return `The script names this file: ${method}`;
  }
}

/** A required keyframe with no live file: the shot can't render (not even anyway). */
export function keyframeBlocks(r: Pick<Ref, "need" | "exists">): boolean {
  return r.need === "required" && !r.exists;
}

/**
 * Whether a keyframe without a file counts as missing: required ones, and
 * optional ones the script asks for. A server from before Phase 8.5 sends no
 * `need`: every keyframe without a file counts, as before.
 */
export function keyframeWanted(r: Pick<Ref, "need" | "method" | "requested">): boolean {
  if (r.method === "none") return false;
  if (r.need === undefined) return true;
  if (r.need === "required") return true;
  return !!r.requested;
}

export interface KeyframeShotGroup {
  shot: string;
  /** first, then last (only those listed) */
  refs: Ref[];
  /** the video target that reads them */
  target: string | null;
}

/** Keyframe refs by shot, in cut order (shots not in the status last, by id). */
export function keyframeGroups(refs: Ref[], st?: Pick<EpisodeStatus, "shots"> | null): KeyframeShotGroup[] {
  const by = new Map<string, Ref[]>();
  for (const r of refs) {
    if (!isKeyframeRef(r)) continue;
    const k = keyframeOf(r);
    if (!k) continue;
    const list = by.get(k.shot) ?? [];
    list.push(r);
    by.set(k.shot, list);
  }
  const order = new Map((st?.shots ?? []).map((s, i) => [s.shot, i]));
  const pos = (s: string) => order.get(s) ?? Number.MAX_SAFE_INTEGER;
  const shots = [...by.keys()].sort((a, b) => pos(a) - pos(b) || a.localeCompare(b));
  const end = (r: Ref) => (keyframeOf(r)!.which === "first" ? 0 : 1);
  return shots.map((shot) => {
    const list = by.get(shot)!.sort((a, b) => end(a) - end(b));
    return { shot, refs: list, target: list.find((r) => r.target)?.target ?? null };
  });
}

/** One keyframe Generate missing fills: from the neighbour's take, or a still. */
export interface KeyframeGen {
  ref: string;
  shot: string;
  which: KeyframeEnd;
  mode: "continuity" | "generate";
  /** the neighbour the continuity frame comes from */
  source?: string;
  label: string;
}

const busyTake = (t: { status: string }) => t.status === "queued" || t.status === "ok";

/**
 * What Generate missing does for one keyframe. Nothing for one with a file,
 * one not wanted (optional and not asked for, or `none`), one to import or a
 * file the script names, or one with a candidate already queued or waiting.
 * Continuity when that's the method and the neighbour in the cut has a usable
 * take; else a generated still (when the ref can be generated).
 */
export function keyframePlanItem(r: Ref, st?: Pick<EpisodeStatus, "shots"> | null): KeyframeGen | null {
  const k = keyframeOf(r);
  if (!k || r.exists || r.need == null || !keyframeWanted(r)) return null;
  if (r.takes.some(busyTake)) return null;
  const method = r.method ?? "generate";
  if (method !== "continuity" && method !== "generate") return null;
  const label = `${k.shot} ${k.which} frame`;
  if (method === "continuity") {
    const src = cutNeighbour(st ?? undefined, k.shot, k.which === "first" ? -1 : 1);
    const s = src ? st?.shots.find((x) => x.shot === src) : undefined;
    if (src && s && s.cut.take != null && s.cut.usable) {
      return { ref: r.id, shot: k.shot, which: k.which, mode: "continuity", source: src, label: `${label} (from ${src})` };
    }
  }
  if (r.can_generate === false) return null;
  return { ref: r.id, shot: k.shot, which: k.which, mode: "generate", label: `${label} (still)` };
}

/** Every keyframe Generate missing fills, in cut order. */
export function keyframePlan(refs: Ref[], st?: Pick<EpisodeStatus, "shots"> | null): KeyframeGen[] {
  return keyframeGroups(refs, st).flatMap((g) => g.refs.map((r) => keyframePlanItem(r, st)).filter((x): x is KeyframeGen => !!x));
}

/** The Clear confirmation: what happens to the shot. */
export function clearKeyframeText(r: Pick<Ref, "id" | "need"> & Partial<Pick<Ref, "shot" | "which">>): string {
  const k = keyframeOf(r);
  const shot = k?.shot ?? "The shot";
  const end = k?.which ?? "";
  const what = k ? `${k.shot}'s ${k.which} keyframe` : r.id;
  const then = r.need === "required"
    ? `${shot} needs one: it can't render until it has a ${end} keyframe again.`
    : `${shot} then renders without a ${end} keyframe.`;
  return `Clear ${what}?\n\nIts live file is removed; the candidates stay, so you can pick one again. ${then}`;
}

/** The warning before unpicking a series ref (allowed, but the shots using it go missing). */
export function clearSeriesRefText(name: string, used: number): string {
  const who = used
    ? `${used} shot${used > 1 ? "s" : ""} of this episode use${used > 1 ? "" : "s"} it and will be`
    : "every shot that uses it will be";
  return `Clear ${name}'s live file?\n\nThe candidates stay, but ${who} blocked (missing ref) until you pick one again.`;
}
