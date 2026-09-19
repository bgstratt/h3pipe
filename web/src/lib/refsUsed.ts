// The inspector's "Refs this shot uses" strip (docs/API.md "Inspector: the refs
// a shot uses"): one tile per ref the shot's current target reads, with a
// status. Pure functions; the Inspector draws them.

import type { Pass, Ref, RefUsed } from "../types";
import { isKeyframeRef, keyframeOf } from "./keyframes";

export type RefUsedStatus = "live" | "missing" | "required";

export interface RefTile {
  /** the ref id, else a key made from the role and slot (a recording, a composed sheet) */
  id: string;
  /** the ref this tile opens in the Refs tab (null: none, e.g. a reference sheet) */
  refId: string | null;
  /** a voice sample or a dialogue recording: an icon, not a picture */
  audio: boolean;
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

export const ROLE_ORDER = ["subject", "plate", "first", "last", "reference_sheet", "voice", "recording"];

export const ROLE_LABEL: Record<string, string> = {
  subject: "subject",
  plate: "plate",
  first: "first frame",
  last: "last frame",
  reference_sheet: "reference sheet",
  voice: "voice sample",
  recording: "dialogue recording",
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

function nameOf(u: Pick<RefUsed, "id" | "role" | "path" | "slot">, refs: Ref[] | undefined): string {
  if (u.role === "first" || u.role === "last") return ROLE_LABEL[u.role];
  if (u.role === "reference_sheet") return u.slot || "reference sheet";
  const r = u.id ? refs?.find((x) => x.id === u.id) : undefined;
  if (r) return r.name;
  if (u.id) return u.id.replace(/^[a-z_]+:/, "").replace(/_/g, " ");
  // a recording, or a plate the series config doesn't name: the file's name
  return u.path?.split(/[\\/]/).pop() || ROLE_LABEL[u.role] || u.role;
}

const isAudio = (u: Pick<RefUsed, "kind" | "role">) => u.kind === "audio" || u.role === "voice" || u.role === "recording";

/** The tiles, from the server's `refs_used`, in role order (subjects, plate, first, last, sheet). */
export function refTiles(used: RefUsed[], refs?: Ref[]): RefTile[] {
  const rank = (role: string) => {
    const i = ROLE_ORDER.indexOf(role);
    return i < 0 ? ROLE_ORDER.length : i;
  };
  return [...used]
    .map((u, i) => ({ u, i }))
    .sort((a, b) => rank(a.u.role) - rank(b.u.role) || a.i - b.i)
    .map(({ u: raw, i }) => {
      const r = raw.id ? refs?.find((x) => x.id === raw.id) : undefined;
      // the Refs list is refreshed on every ref event: its file state is the newer one
      const u: RefUsed = r ? { ...raw, exists: r.exists, path: r.path ?? raw.path, need: raw.need ?? r.need ?? null } : raw;
      const label = nameOf(u, refs);
      const status = refUsedStatus(u);
      const st = statusText(u);
      const audio = isAudio(u);
      // `thumb` is null when the file isn't on disk; `path` is the ref's live file
      const image = audio || !u.exists ? null : (r ? r.path : null) ?? u.thumb ?? u.path;
      return {
        id: u.id ?? `${u.role}:${u.slot ?? i}`,
        refId: u.id,
        audio,
        role: u.role,
        label,
        roleLabel: ROLE_LABEL[u.role] ?? u.role,
        status,
        statusText: st,
        image,
        version: r?.sha1 ?? null,
        keyframe: u.role === "first" || u.role === "last",
        title: `${label} · ${ROLE_LABEL[u.role] ?? u.role}${u.slot && u.slot !== label ? ` (${u.slot})` : ""} · ${st}${u.path ? `\n${u.path}` : ""}${u.id ? "\nClick: open it in the Refs tab" : ""}`,
      };
    });
}

/**
 * Without `refs_used` (a server from before Phase 8.5): the refs whose
 * `used_by` names the shot (subjects, then locations as the plate), then its
 * keyframes. Voices are left out.
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
