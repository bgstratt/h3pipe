// The inspector's "Refs this shot uses" strip (docs/API.md "Inspector: the refs
// a shot uses"): one tile per ref the shot's current target reads, with a
// status. Pure functions; the Inspector draws them.

import type { Pass, Ref, RefUsed } from "../types";
import { isKeyframeRef, keyframeOf } from "./keyframes";

export type RefUsedStatus = "live" | "missing" | "required";

export interface RefTile {
  id: string;
  role: string;
  /** "Ada", "kitchen", "first frame", "reference sheet" */
  label: string;
  /** what the tile's corner says: "subject", "plate", "first", … */
  roleLabel: string;
  status: RefUsedStatus;
  /** "live", "missing", "required: missing", "none (optional)" */
  statusText: string;
  /** the picture, relative like a ref's path (null: nothing to show) */
  image: string | null;
  /** cache buster for the picture (the ref's sha1) */
  version: string | null;
  /** a keyframe tile (gets From previous / Clear) */
  keyframe: boolean;
  title: string;
}

export const ROLE_ORDER = ["subject", "plate", "first", "last", "reference_sheet"];

export const ROLE_LABEL: Record<string, string> = {
  subject: "subject",
  plate: "plate",
  first: "first frame",
  last: "last frame",
  reference_sheet: "reference sheet",
};

/** live when the file is there; required when a required one isn't; else missing. */
export function refUsedStatus(u: Pick<RefUsed, "exists" | "need">): RefUsedStatus {
  if (u.exists) return "live";
  return u.need === "required" ? "required" : "missing";
}

function statusText(u: Pick<RefUsed, "exists" | "need" | "role">): string {
  const s = refUsedStatus(u);
  if (s === "live") return "live";
  if (s === "required") return "required: missing";
  if ((u.role === "first" || u.role === "last") && u.need === "optional") return "none (optional)";
  return "missing";
}

function nameOf(u: Pick<RefUsed, "id" | "role">, refs: Ref[] | undefined): string {
  if (u.role === "first" || u.role === "last") return ROLE_LABEL[u.role];
  if (u.role === "reference_sheet") return "reference sheet";
  return refs?.find((r) => r.id === u.id)?.name ?? u.id.replace(/^[a-z_]+:/, "").replace(/_/g, " ");
}

/** The tiles, from the server's `refs_used`, in role order (subjects, plate, first, last, sheet). */
export function refTiles(used: RefUsed[], refs?: Ref[]): RefTile[] {
  const rank = (role: string) => {
    const i = ROLE_ORDER.indexOf(role);
    return i < 0 ? ROLE_ORDER.length : i;
  };
  return [...used]
    .map((u, i) => ({ u, i }))
    .sort((a, b) => rank(a.u.role) - rank(b.u.role) || a.i - b.i)
    .map(({ u: raw }) => {
      const r = refs?.find((x) => x.id === raw.id);
      // the Refs list is refreshed on every ref event: its file state is the newer one
      const u: RefUsed = r ? { ...raw, exists: r.exists, path: r.path ?? raw.path, need: raw.need ?? r.need ?? null } : raw;
      const label = nameOf(u, refs);
      const status = refUsedStatus(u);
      const st = statusText(u);
      const image = u.exists ? u.thumb || u.path : null;
      return {
        id: u.id,
        role: u.role,
        label,
        roleLabel: ROLE_LABEL[u.role] ?? u.role,
        status,
        statusText: st,
        image,
        version: r?.sha1 ?? null,
        keyframe: u.role === "first" || u.role === "last",
        title: `${label} · ${ROLE_LABEL[u.role] ?? u.role} · ${st}${u.path ? `\n${u.path}` : ""}\nClick: open it in the Refs tab`,
      };
    });
}

/**
 * Without `refs_used` (a server from before Phase 8.5): the refs whose
 * `used_by` names the shot (subjects, then locations as the plate), then its
 * keyframes. Voices are left out (the strip is pictures).
 */
export function refsUsedFallback(refs: Ref[] | undefined, shot: string, pass: Pass): RefUsed[] {
  const out: RefUsed[] = [];
  for (const r of refs ?? []) {
    if (isKeyframeRef(r)) {
      const k = keyframeOf(r);
      if (k?.shot === shot) out.push({ id: r.id, kind: r.kind, role: k.which, path: r.path, exists: r.exists, need: r.need ?? null });
      continue;
    }
    if (r.kind === "voice" || !(r.used_by?.[pass] ?? []).includes(shot)) continue;
    out.push({ id: r.id, kind: r.kind, role: r.kind === "location" ? "plate" : "subject", path: r.path, exists: r.exists, need: null });
  }
  return out;
}

/** The tiles for a shot: the server's list when it sends one, else the fallback. */
export function shotRefTiles(used: RefUsed[] | undefined, refs: Ref[] | undefined, shot: string, pass: Pass): { tiles: RefTile[]; exact: boolean } {
  if (Array.isArray(used)) return { tiles: refTiles(used, refs), exact: true };
  return { tiles: refTiles(refsUsedFallback(refs, shot, pass), refs), exact: false };
}
