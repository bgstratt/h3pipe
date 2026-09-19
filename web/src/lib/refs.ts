// The Refs tab's grouping, filters and "blocks N shots".

import type { EpisodeStatus, Pass, Ref, RefTake, RefView } from "../types";
import { missingOf, normPath } from "./missingRefs";

export type RefGroupId = "characters" | "props" | "locations" | "voices" | "keyframes";
export type RefFilter = "episode" | "all" | "missing";

export const REF_GROUPS: { id: RefGroupId; label: string }[] = [
  { id: "characters", label: "Characters" },
  { id: "props", label: "Props & vehicles" },
  { id: "locations", label: "Locations" },
  { id: "voices", label: "Voices" },
  { id: "keyframes", label: "Shot keyframes" },
];

/** kreagen.VIEWS, in order. */
export const VIEWS: { view: string; label: string }[] = [
  { view: "01_threequarter", label: "three-quarter" },
  { view: "02_side", label: "side" },
  { view: "03_back", label: "back" },
  { view: "04_face", label: "face" },
];

export function viewLabel(view: string | null | undefined): string {
  if (!view) return "";
  return VIEWS.find((v) => v.view === view)?.label ?? view.replace(/^\d+_/, "");
}

export function groupOf(r: Pick<Ref, "kind" | "scope">): RefGroupId {
  if (r.scope === "shot" || r.kind === "keyframe") return "keyframes";
  switch (r.kind) {
    case "character": return "characters";
    case "location": return "locations";
    case "voice": return "voices";
    default: return "props"; // prop, vehicle, and anything new
  }
}

export function usedBy(r: Pick<Ref, "used_by">, pass: Pass): string[] {
  return r.used_by?.[pass] ?? [];
}

/**
 * Shots of this episode the ref blocks: those whose `missing_refs` name its file.
 * Without an episode status, a missing ref blocks every shot that uses it.
 */
export function blockedShots(r: Ref, st: EpisodeStatus | undefined, pass: Pass): string[] {
  if (!st) return r.exists ? [] : usedBy(r, pass);
  const p = normPath(r.path);
  if (!p) return [];                       // the series config names no file: it blocks nothing
  return st.shots.filter((s) => missingOf(s).some((m) => normPath(m.path) === p)).map((s) => s.shot);
}

export interface RefGroup {
  id: RefGroupId;
  label: string;
  refs: Ref[];
  /** refs of this group in the list before the filter */
  total: number;
}

export function passesFilter(r: Ref, filter: RefFilter, pass: Pass): boolean {
  if (filter === "all") return true;
  if (filter === "missing") return !r.exists;
  return usedBy(r, pass).length > 0;
}

/** Every group in a fixed order (empty ones included), refs sorted by name. */
export function groupRefs(refs: Ref[], filter: RefFilter, pass: Pass): RefGroup[] {
  return REF_GROUPS.map(({ id, label }) => {
    const all = refs.filter((r) => groupOf(r) === id);
    const shown = all
      .filter((r) => passesFilter(r, filter, pass))
      .sort((a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: "base" }) || a.id.localeCompare(b.id));
    return { id, label, refs: shown, total: all.length };
  });
}

/** The candidate that's live (picked) in a view or a ref. */
export function pickedTake(x: Pick<RefView, "picked" | "takes">): RefTake | undefined {
  return x.picked == null ? undefined : x.takes.find((t) => t.take === x.picked);
}

export function viewOf(r: Ref, view: string | null | undefined): RefView | undefined {
  return view ? r.views?.find((v) => v.view === view) : undefined;
}

/** The candidates of a ref, or of one of a character's views. */
export function takesOf(r: Ref, view: string | null | undefined): RefTake[] {
  return view ? viewOf(r, view)?.takes ?? [] : r.takes;
}

/** Views of a character with no pick yet (the sheet is stitched when none are left). */
export function unpickedViews(r: Ref): string[] {
  if (!hasViews(r)) return [];
  const have = new Map(r.views!.map((v) => [v.view, v.picked]));
  return VIEWS.map((v) => v.view).filter((v) => have.get(v) == null);
}

export function isAudioRef(r: Pick<Ref, "kind">): boolean {
  return r.kind === "voice";
}

/** Generation isn't offered for voices (nothing generates them yet) or keyframes (FL2V, later). */
export function canGenerate(r: Pick<Ref, "kind" | "scope"> & { can_generate?: boolean }): boolean {
  // the server knows best (a character with no sheet or no design can't be
  // generated); the kind rule is the fallback
  if (r.can_generate === false) return false;
  return r.kind !== "voice" && groupOf(r) !== "keyframes";
}

/** The Refs tab's summary line. */
export function refCounts(refs: Ref[], st: EpisodeStatus | undefined, pass: Pass): { missing: number; blocking: number; shots: number } {
  const blocked = new Set<string>();
  let missing = 0;
  let blocking = 0;
  for (const r of refs) {
    if (r.exists) continue;
    missing++;
    const b = blockedShots(r, st, pass);
    if (b.length) blocking++;
    b.forEach((s) => blocked.add(s));
  }
  return { missing, blocking, shots: blocked.size };
}

/** A character with per-view candidates. The server sends `views: []` for every
 * other ref, so test the length, never just the field. */
export function hasViews(r: Pick<Ref, "views">): boolean {
  return !!r.views && r.views.length > 0;
}
