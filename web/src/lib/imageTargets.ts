// The targets refs generate with (docs/API.md "Phase 8.5 as built": image
// defaults; "Phase 9c-B as built": `voice_target`): which model series refs,
// shot keyframes and voices generate with (the episode's choice, the series
// config's `refs` block, the built-in default, or a per-ref override), and
// which reference images an edit target gets with a keyframe. Pure functions.

import type { EditRef, Ref, RefDefaultSource, RefDefaults, Target, TargetList } from "../types";
import { isKeyframeRef } from "./keyframes";
import { targetLabel } from "./targets";
import { VOICE_DEFAULT } from "./voice";

/** The built-in refs target (Phase 7). */
export const REFS_DEFAULT = "krea2";

/** The three kinds of ref target: series refs and keyframes take an image
 * target, a voice takes an audio one (Phase 9c-B). */
export type RefTargetKind = "refs" | "keyframes" | "voices";

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
  /** Phase 9c-B: the audio target voices generate with */
  voices: string;
  /** where each comes from: set in the editor for this episode, the series config, built in */
  refsSource: RefDefaultSource;
  keyframesSource: RefDefaultSource;
  voicesSource: RefDefaultSource;
}

/** The field of ResolvedRefDefaults a kind reads. */
export function defaultOf(d: ResolvedRefDefaults, kind: RefTargetKind): { target: string; source: RefDefaultSource } {
  if (kind === "keyframes") return { target: d.keyframes, source: d.keyframesSource };
  if (kind === "voices") return { target: d.voices, source: d.voicesSource };
  return { target: d.refs, source: d.refsSource };
}

function source(s: string | null | undefined): RefDefaultSource {
  return s === "editor" || s === "series" ? s : "default";
}

/**
 * The episode's image targets, as `GET /h3pipe/refs` `defaults` gives them.
 * Without them (a series config whose `refs` block the server can't read): the
 * list's image default for both.
 */
export function refDefaults(list: TargetList | null | undefined, served?: RefDefaults | null): ResolvedRefDefaults {
  const fallback = list?.default?.image || imageTargets(list).find((t) => t.default)?.id || REFS_DEFAULT;
  const voiceFallback = list?.default?.audio
    || (list?.targets ?? []).find((t) => t.kind === "audio" && t.default)?.id
    || (list?.targets ?? []).find((t) => t.kind === "audio")?.id
    || VOICE_DEFAULT;
  const refs = served?.target || fallback;
  return {
    refs,
    keyframes: served?.keyframe_target || refs,
    voices: served?.voice_target || voiceFallback,
    refsSource: source(served?.target_source),
    keyframesSource: source(served?.keyframe_target_source),
    voicesSource: source(served?.voice_target_source),
  };
}

/** "set for this episode" / "series.json" / "built-in default" */
export function refDefaultSourceLabel(s: RefDefaultSource): string {
  return s === "editor" ? "set for this episode" : s === "series" ? "series.json refs block" : "built-in default";
}

export function refTargetKind(r: Pick<Ref, "id" | "kind" | "scope">): RefTargetKind {
  if (r.kind === "voice") return "voices";
  return isKeyframeRef(r) ? "keyframes" : "refs";
}

/**
 * The image target a generate of `r` uses: the ref's own override, else what
 * the server says it uses now (`effective.target`), else the episode default.
 */
export function refTargetOf(
  r: Pick<Ref, "id" | "kind" | "scope"> & Partial<Pick<Ref, "override_values" | "effective">>,
  defaults: ResolvedRefDefaults,
): { target: string; source: "override" | "effective" | "default" } {
  const own = r.override_values?.target;
  if (own) return { target: own, source: "override" };
  if (r.effective?.target) return { target: r.effective.target, source: "effective" };
  return { target: defaultOf(defaults, refTargetKind(r)).target, source: "default" };
}

/** The series.json snippet that makes a choice permanent. */
export function refsSnippet(kind: RefTargetKind, id: string): string {
  const key = kind === "keyframes" ? "keyframe_target" : kind === "voices" ? "voice_target" : "target";
  return `"refs": {${JSON.stringify(key)}: ${JSON.stringify(id)}}`;
}

// ---------------------------------------------------------------------------
// edit targets: the references a keyframe generate sends
// ---------------------------------------------------------------------------

export interface EditRefsPlan {
  refs: { id: string; label: string; view?: string | null }[];
  max: number | null;
}

const VIEW_WORD: Record<string, string> = { "04_face": "face", "01_threequarter": "body" };

/**
 * What an edit target receives with `r`'s keyframe generate: the server's
 * `edit_refs` (exactly what a generate feeds, after `max_refs`). Empty for a
 * text-to-image target.
 */
export function editRefsFor(r: Pick<Ref, "edit_refs">, target: Target | undefined, refs: Ref[]): EditRefsPlan {
  const max = target?.capabilities?.max_refs ?? null;
  if (!isEditTarget(target)) return { refs: [], max };
  const name = (id: string) => refs.find((x) => x.id === id)?.name ?? id.replace(/^[a-z]+:/, "").replace(/_/g, " ");
  return {
    refs: (r.edit_refs ?? []).map((e: EditRef) => {
      const view = e.view ?? null;
      // a single-reference target gets one collage of the parts (no file until a generate)
      if (e.role === "composite" || !e.id) {
        const parts = (e.parts ?? []).map((p) => p.name ?? p.subject ?? p.location ?? "").filter(Boolean);
        return { id: e.id ?? "composite", view: null, label: e.name ? e.name : parts.length ? `one collage of ${parts.join(", ")}` : "one collage" };
      }
      const what = e.role === "plate" ? "plate" : view ? VIEW_WORD[view] ?? view.replace(/^\d+_/, "") : "";
      return { id: e.id, view, label: what ? `${name(e.id)} (${what})` : name(e.id) };
    }),
    max,
  };
}

/** "Flux 2 Klein edit with Ada (face), kitchen (plate)" */
export function editRefsText(list: TargetList | null | undefined, targetId: string, plan: EditRefsPlan): string {
  const label = targetLabel(list, targetId);
  if (!plan.refs.length) return `${label} (no reference images yet: pick the characters' views and the plate)`;
  return `${label} with ${plan.refs.map((x) => x.label).join(", ")}`;
}
