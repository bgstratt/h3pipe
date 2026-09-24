// The Refs tab's grouping, filters and "blocks N shots".

import type { EpisodeStatus, Pass, Ref, RefTake, RefView } from "../types";
import { isKeyframeRef, keyframeBlocks, keyframeOf, keyframeWanted } from "./keyframes";
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

/**
 * P8: the reserved pseudo-view for a character's whole supplied sheet
 * (h3refs.SHEET_VIEW). Not one of VIEWS: nothing generates a sheet, it is the
 * four views stitched -- or one you supply ready-made.
 */
export const SHEET_VIEW = "sheet";

export function viewLabel(view: string | null | undefined): string {
  if (view === SHEET_VIEW) return "sheet";
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
  // Phase 8.5: a required keyframe with no file blocks its shot, whatever missing_refs says
  const kf = keyframeBlocks(r) ? keyframeOf(r)?.shot : undefined;
  if (!st) return r.exists ? [] : kf ? [kf] : usedBy(r, pass);
  const p = normPath(r.path);
  // no path: the series config names no file, so it blocks nothing
  const out = p ? st.shots.filter((s) => missingOf(s).some((m) => normPath(m.path) === p)).map((s) => s.shot) : [];
  if (kf && !out.includes(kf) && st.shots.some((s) => s.shot === kf)) out.push(kf);
  return out;
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
  // a keyframe counts as missing only when it's required or the script asks for it
  if (filter === "missing") return !r.exists && (!isKeyframeRef(r) || keyframeWanted(r));
  // a shot's keyframe belongs to this episode, whether or not its target reads it
  if (groupOf(r) === "keyframes") return true;
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

/**
 * The candidates of a ref, or of one of a character's views. P8: a character's
 * supplied sheets are the ref's OWN takes (`takes` / `picked` in the listing),
 * so SHEET_VIEW reads them from there rather than from a `views` entry.
 */
export function takesOf(r: Ref, view: string | null | undefined): RefTake[] {
  if (!view || view === SHEET_VIEW) return r.takes;
  return viewOf(r, view)?.takes ?? [];
}

/** Which take is live for a ref, one of its views, or its supplied sheet. */
export function pickedOf(r: Ref, view: string | null | undefined): number | null {
  if (!view || view === SHEET_VIEW) return r.picked ?? null;
  return viewOf(r, view)?.picked ?? null;
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

/** A candidate's file: its image, or a voice's `audio` (whose `image` is null). */
export function takeFile(t: Pick<RefTake, "image" | "audio">): string | null {
  return t.image ?? t.audio ?? null;
}

/** A candidate that can be picked: the server's `usable`, else finished with a file. */
export function takeUsable(t: Pick<RefTake, "status" | "image" | "audio" | "usable">): boolean {
  return t.usable ?? (t.status === "ok" && !!takeFile(t));
}

/**
 * Keyframes are generated from Phase 8.5 on (a still by the keyframe image
 * model), and voices from Phase 9c-B (a sample by the episode's audio
 * target), on a server that says so. A server from before either answers
 * nothing for them, and the kind rule keeps the old behaviour.
 */
export function canGenerate(r: Pick<Ref, "kind" | "scope"> & { can_generate?: boolean; need?: Ref["need"] }): boolean {
  // the server knows best (a character with no sheet or no design can't be
  // generated; a voice needs an audio target); the kind rule is the fallback
  if (r.can_generate === false) return false;
  if (groupOf(r) === "keyframes") return r.can_generate === true || r.need !== undefined;
  if (r.kind === "voice") return r.can_generate === true;
  return true;
}

/** The Refs tab's summary line. */
export function refCounts(refs: Ref[], st: EpisodeStatus | undefined, pass: Pass): { missing: number; blocking: number; shots: number } {
  const blocked = new Set<string>();
  let missing = 0;
  let blocking = 0;
  for (const r of refs) {
    if (r.exists) continue;
    if (isKeyframeRef(r) && !keyframeWanted(r)) continue; // an optional keyframe nobody asked for
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

/** One Generate call "Generate missing" will make: a ref, and the view (null
 * for a non-character, or for a character that needs all four views). */
export interface MissingGen { ref: string; view: string | null; label: string }

const inFlight = (t: { status: string }) => t.status === "queued" || t.status === "ok";

/**
 * What "Generate missing" queues for this episode and pass: every ref with no
 * live file that this pass uses and that can be generated, skipping anything
 * already queued or finished and waiting to be auto-picked. A character gets
 * only the views that still need a candidate (all four together as one call,
 * sharing a seed, when none has one).
 */
export function missingPlan(refs: Ref[], pass: Pass): MissingGen[] {
  const out: MissingGen[] = [];
  for (const r of refs) {
    // keyframes have their own plan (lib/keyframes keyframePlan: continuity or a still)
    if (isKeyframeRef(r) || r.exists || !r.path || !canGenerate(r) || !usedBy(r, pass).length) continue;
    if (hasViews(r)) {
      const need = r.views!.filter((v) => v.picked == null && !v.takes.some(inFlight)).map((v) => v.view);
      if (need.length === r.views!.length) out.push({ ref: r.id, view: null, label: r.name });
      else for (const v of need) out.push({ ref: r.id, view: v, label: `${r.name} ${v.replace(/^\d+_/, "")}` });
    } else if (!r.takes.some(inFlight)) {
      out.push({ ref: r.id, view: null, label: r.name });
    }
  }
  return out;
}
