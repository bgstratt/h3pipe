// Image targets for refs (docs/API.md "Phase 8.5: Image targets"): which image
// model series refs and keyframes generate with (the series config's `refs`
// block, a per-ref override, the Refs tab's session choice), and which
// reference images an edit target gets with a keyframe. Pure functions.

import type { EditRef, Pass, Ref, RefDefaults, Target, TargetList } from "../types";
import { isKeyframeRef, keyframeOf } from "./keyframes";
import { readinessOf } from "./readiness";
import { findTarget, targetLabel } from "./targets";

/** The contract's default keyframe target when it is ready. */
export const KEYFRAME_EDIT_DEFAULT = "flux2_klein_edit";
/** The built-in refs target (Phase 7). */
export const REFS_DEFAULT = "krea2";

export type RefTargetKind = "refs" | "keyframes";

export function imageTargets(list: TargetList | null | undefined): Target[] {
  return (list?.targets ?? []).filter((t) => t.kind === "image");
}

export function isEditTarget(t: Pick<Target, "capabilities"> | undefined): boolean {
  return t?.capabilities?.mode === "edit";
}

/** "edit, up to 4 references" / "text to image" / "" */
export function imageModeText(t: Pick<Target, "capabilities"> | undefined): string {
  const c = t?.capabilities;
  if (!c?.mode) return "";
  if (c.mode === "edit") return c.max_refs ? `edit, up to ${c.max_refs} reference${c.max_refs > 1 ? "s" : ""}` : "edit with references";
  if (c.mode === "t2i") return "text to image";
  return String(c.mode);
}

export interface ResolvedRefDefaults {
  refs: string;
  keyframes: string;
  /** "series": the server sent the series config's block; "default": the fallback rule */
  source: "series" | "default";
}

/**
 * The series defaults: the server's (TODO(contract): `defaults` on /refs),
 * else the list's image default (else krea2) for refs, and flux2_klein_edit
 * for keyframes when it is ready (else the refs target), as the contract says.
 */
export function refDefaults(list: TargetList | null | undefined, served?: RefDefaults | null): ResolvedRefDefaults {
  const refs = served?.target || list?.default?.image || imageTargets(list).find((t) => t.default)?.id || REFS_DEFAULT;
  let keyframes = served?.keyframe_target || "";
  if (!keyframes) {
    const edit = findTarget(list, KEYFRAME_EDIT_DEFAULT);
    const ready = readinessOf(list, KEYFRAME_EDIT_DEFAULT)?.status;
    keyframes = edit && (ready === "ready" || ready === "degraded") ? KEYFRAME_EDIT_DEFAULT : refs;
  }
  return { refs, keyframes, source: served?.target || served?.keyframe_target ? "series" : "default" };
}

export function refTargetKind(r: Pick<Ref, "id" | "kind" | "scope">): RefTargetKind {
  return isKeyframeRef(r) ? "keyframes" : "refs";
}

/** The session choice in the Refs tab, per kind (null: the series default). */
export type RefTargetChoice = Partial<Record<RefTargetKind, string | null>>;

/**
 * The image target a generate of `r` uses: the ref's own override, else the
 * tab's choice, else the series default. `source` says which.
 */
export function refTargetOf(
  r: Pick<Ref, "id" | "kind" | "scope"> & Partial<Pick<Ref, "override_values" | "effective">>,
  defaults: ResolvedRefDefaults,
  choice: RefTargetChoice = {},
): { target: string; source: "override" | "choice" | "series" | "effective" } {
  const own = r.override_values?.target;
  if (own) return { target: own, source: "override" };
  const kind = refTargetKind(r);
  const c = choice[kind];
  if (c) return { target: c, source: "choice" };
  if (r.effective?.target) return { target: r.effective.target, source: "effective" };
  return { target: kind === "keyframes" ? defaults.keyframes : defaults.refs, source: "series" };
}

/**
 * The `target` to send with a generate: the tab's choice when it differs from
 * the series default and the ref has no override of its own (a request's
 * target beats the override, so it mustn't be sent then). null otherwise.
 */
export function generateTarget(
  r: Pick<Ref, "id" | "kind" | "scope"> & Partial<Pick<Ref, "override_values">>,
  defaults: ResolvedRefDefaults,
  choice: RefTargetChoice = {},
): string | null {
  if (r.override_values?.target) return null;
  const kind = refTargetKind(r);
  const c = choice[kind];
  const def = kind === "keyframes" ? defaults.keyframes : defaults.refs;
  return c && c !== def ? c : null;
}

/** The series.json snippet that makes a choice permanent. */
export function refsSnippet(kind: RefTargetKind, id: string): string {
  return `"refs": {${JSON.stringify(kind === "keyframes" ? "keyframe_target" : "target")}: ${JSON.stringify(id)}}`;
}

// ---------------------------------------------------------------------------
// edit targets: the references a keyframe generate sends
// ---------------------------------------------------------------------------

export interface EditRefsPlan {
  refs: { id: string; label: string; view?: string | null }[];
  /** true when the server listed them (`edit_refs`); false: the UI's guess */
  exact: boolean;
  max: number | null;
}

/** Face or body by shot size, as the contract says (a guess at the size words). */
export function viewForSize(size: string | null | undefined): string {
  return /close|cu\b|ecu|face/i.test(size ?? "") ? "04_face" : "01_threequarter";
}

/**
 * What an edit target receives with `r`'s keyframe generate: the server's
 * `edit_refs` when it sends them, else a guess: the picked characters (then
 * props) the shot uses, then its plate, up to `max_refs`. Empty for a t2i target.
 */
export function editRefsFor(
  r: Ref,
  target: Target | undefined,
  refs: Ref[],
  pass: Pass,
  size?: string | null,
): EditRefsPlan {
  const max = target?.capabilities?.max_refs ?? null;
  if (!isEditTarget(target)) return { refs: [], exact: true, max };
  const name = (id: string) => refs.find((x) => x.id === id)?.name ?? id.replace(/^[a-z]+:/, "");
  if (Array.isArray(r.edit_refs)) {
    return { refs: r.edit_refs.map((e: EditRef) => ({ id: e.id, view: e.view ?? null, label: name(e.id) })), exact: true, max };
  }
  const shot = keyframeOf(r)?.shot;
  if (!shot) return { refs: [], exact: false, max };
  const uses = refs.filter((x) => !isKeyframeRef(x) && x.kind !== "voice" && x.exists && (x.used_by?.[pass] ?? []).includes(shot));
  const order = (x: Ref) => (x.kind === "character" ? 0 : x.kind === "location" ? 2 : 1);
  const sorted = [...uses].sort((a, b) => order(a) - order(b) || a.name.localeCompare(b.name));
  const view = viewForSize(size);
  const out = sorted.map((x) => ({
    id: x.id,
    view: x.kind === "character" ? view : null,
    label: x.kind === "location" ? `${x.name} (plate)` : x.kind === "character" ? `${x.name} (${view === "04_face" ? "face" : "body"})` : x.name,
  }));
  return { refs: max != null ? out.slice(0, max) : out, exact: false, max };
}

/** "Flux 2 Klein edit with Ada (face), kitchen (plate)" */
export function editRefsText(list: TargetList | null | undefined, targetId: string, plan: EditRefsPlan): string {
  const label = targetLabel(list, targetId);
  if (!plan.refs.length) return plan.exact ? `${label} (no reference images)` : `${label}: no picked references for this shot yet`;
  return `${label} with ${plan.refs.map((x) => x.label).join(", ")}${plan.exact ? "" : " (guessed)"}`;
}
