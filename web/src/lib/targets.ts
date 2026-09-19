// Targets (docs/API.md "Targets (Phase 7)" and "Phase 8 additions: retargeting a
// shot"): the picker's list, what a target's binding lets the model and LoRA
// pickers offer, the "retargeted" label, the bin/timeline/take badges, and the
// warnings a render response may carry. Pure functions; the components call them.

import type {
  EpisodeStatus, ModelFile, ModelList, RenderResult, ShotDetail, ShotStatus, Target, TargetList, TargetWidget, TakeSummary,
} from "../types";
import { tn, type Badge } from "./format";

/** The H3 target's id: what a server from before Phase 7 renders everything on. */
export const LEGACY_TARGET = "minimax_h3_ref2va";

export function videoTargets(list: TargetList | null | undefined): Target[] {
  return (list?.targets ?? []).filter((t) => (t.kind ?? "video") === "video");
}

export function findTarget(list: TargetList | null | undefined, id: string | null | undefined): Target | undefined {
  if (!id) return undefined;
  return list?.targets.find((t) => t.id === id);
}

/**
 * The series default video target: the episode's own `target` (the series
 * config's `series.target`), else the list's default, else the target marked
 * `default`, else H3.
 */
export function seriesDefaultTarget(list: TargetList | null | undefined, st?: Pick<EpisodeStatus, "target"> | null): string {
  return st?.target
    || list?.default?.video
    || videoTargets(list).find((t) => t.default)?.id
    || LEGACY_TARGET;
}

/** The list's default video target (the one marked "default" in the picker). */
export function listDefaultTarget(list: TargetList | null | undefined): string | undefined {
  return list?.default?.video || videoTargets(list).find((t) => t.default)?.id;
}

export function targetLabel(list: TargetList | null | undefined, id: string | null | undefined): string {
  if (!id) return "";
  return findTarget(list, id)?.label || id;
}

/**
 * A short badge label: the first word of the target's label ("LTX-2" -> "LTX",
 * "MiniMax H3 Ref2VA" -> "MiniMax"), else the letters its id starts with.
 */
export function targetShort(list: TargetList | null | undefined, id: string | null | undefined): string {
  if (!id) return "";
  const label = findTarget(list, id)?.label;
  const word = label?.trim().split(/[\s\-_/]+/)[0];
  if (word) return word.slice(0, 10);
  const m = /^[A-Za-z]+/.exec(id);
  return (m ? m[0] : id).slice(0, 8).toUpperCase();
}

/** The target a shot's next render uses: the served `target`, else its built one, else the default. */
export function shotTarget(
  s: Pick<ShotStatus, "target" | "built_target"> | Pick<ShotDetail, "target" | "built_target"> | null | undefined,
  fallback: string,
): string {
  return s?.target || s?.built_target || fallback;
}

/**
 * The `target` to send in a shot override when the shot is set to `chosen`:
 * null when it's what the shot would render on anyway (its script's target,
 * else the episode's), so the retarget is cleared. A shot built for the series
 * target has no script target of its own, so it falls back to the episode
 * target; picking its built target then has to be sent explicitly when the
 * episode target differs (see TODO(contract) in api.ts).
 */
export function overrideTargetValue(
  chosen: string | null | undefined,
  built: string | null | undefined,
  episode?: Pick<EpisodeStatus, "target" | "series_target" | "target_source"> | null,
): string | null {
  if (!chosen) return null;
  const scriptOwn = !!built && !!episode?.series_target && built !== episode.series_target;
  const fallback = !episode?.target || !episode.target_source || scriptOwn ? built : episode.target;
  return chosen !== fallback ? chosen : null;
}

/** True when the shot now renders on a target other than the one it was built for. */
export function isRetargeted(s: { target?: string | null; built_target?: string | null } | null | undefined): boolean {
  return !!s?.target && !!s.built_target && s.target !== s.built_target;
}

/** "(retargeted from MiniMax H3 Ref2VA)", or null when the shot is on its built target. */
export function retargetNote(list: TargetList | null | undefined, s: { target?: string | null; built_target?: string | null } | null | undefined): string | null {
  if (!isRetargeted(s)) return null;
  return `(retargeted from ${targetLabel(list, s!.built_target)})`;
}

// ---------------------------------------------------------------------------
// the binding: what the model and LoRA pickers may offer
// ---------------------------------------------------------------------------

/**
 * How a picker for one render parameter behaves under a target:
 *  - `node`: a graph widget; its choices are ComfyUI's for `class_type`/`field`;
 *  - `loader`: the target's loader reads it from the shotlist (free choice from
 *    ComfyUI's generic list);
 *  - `none`: the target has no such widget: hide the picker;
 *  - `unknown`: no target info (older server, or targets not loaded): the old,
 *    unfiltered behaviour.
 */
export type WidgetSpec =
  | { kind: "node"; class_type: string; field: string }
  | { kind: "loader" }
  | { kind: "none" }
  | { kind: "unknown" };

function specOf(w: TargetWidget | null | undefined, fieldKeys: string[]): WidgetSpec {
  if (!w) return { kind: "none" };
  if ("via" in w && w.via) return { kind: "loader" };
  const ct = (w as { class_type?: unknown }).class_type;
  if (typeof ct !== "string" || !ct) return { kind: "unknown" };
  for (const k of fieldKeys) {
    const f = (w as Record<string, unknown>)[k];
    if (typeof f === "string" && f) return { kind: "node", class_type: ct, field: f };
  }
  return { kind: "unknown" };
}

export interface PickerSpec {
  model: WidgetSpec;
  loras: WidgetSpec;
}

/** The model and LoRA pickers' spec for a target (undefined target = unknown). */
export function pickerSpec(t: Target | undefined): PickerSpec {
  if (!t || !t.widgets) return { model: { kind: "unknown" }, loras: { kind: "unknown" } };
  return {
    model: specOf(t.widgets.model, ["field", "name"]),
    // a LoRA widget names its file field `name` (API.md); accept `field` too
    loras: specOf(t.widgets.loras ?? t.widgets.lora, ["name", "field"]),
  };
}

export function widgetKey(spec: { class_type: string; field: string }): string {
  return `${spec.class_type}|${spec.field}`;
}

/**
 * The list a picker offers: the widget's own choices when they're loaded;
 * `undefined` = ComfyUI's generic list (a loader-read parameter, no target info,
 * or choices not loaded / not available); `null` = hide the picker (the target
 * has no such widget).
 */
export function pickerChoices(
  spec: WidgetSpec,
  widgetChoices: Record<string, string[]> | undefined,
): string[] | null | undefined {
  if (spec.kind === "none") return null;
  if (spec.kind === "node") return widgetChoices?.[widgetKey(spec)];
  return undefined;
}

// ---------------------------------------------------------------------------
// model families (GET /h3pipe/models)
// ---------------------------------------------------------------------------

/** The render dialog's checkbox that sends allow_model_mismatch. */
export const MODEL_MISMATCH_LABEL = "Render anyway (model mismatch)";

export interface ModelGroups {
  /** named like the target's family, or its header says so */
  matching: ModelFile[];
  /** "Other files (unverified)": unknown, unreadable, or another family */
  other: ModelFile[];
}

/** The model picker's two groups, in the server's order; null without a list. */
export function modelGroups(list: ModelList | null | undefined): ModelGroups | null {
  if (!list) return null;
  return {
    matching: list.files.filter((f) => f.match === "name" || f.match === "fingerprint"),
    other: list.files.filter((f) => f.match !== "name" && f.match !== "fingerprint"),
  };
}

/** The file `value` names in the list, when it is one of the "other" files. */
export function otherModel(list: ModelList | null | undefined, value: string | null | undefined): ModelFile | null {
  if (!list || !value) return null;
  const f = list.files.find((x) => x.name === value);
  return f && f.match === "other" ? f : null;
}

/** True when the server would skip a render with this model (another family). */
export function isModelMismatch(list: ModelList | null | undefined, value: string | null | undefined): boolean {
  return !!otherModel(list, value)?.mismatch;
}

/** The warning under the model picker for an "other" file, or null. */
export function modelWarning(list: ModelList | null | undefined, value: string | null | undefined): string | null {
  const f = otherModel(list, value);
  if (!f || !list) return null;
  if (f.mismatch) return `${f.detail || `${f.name} is ${f.label || "another model family"}, not ${list.label}`}. A render skips the shot unless you tick “${MODEL_MISMATCH_LABEL}”.`;
  return `Unverified: ${f.detail || `${f.name} isn't named like ${list.label}`}.`;
}

/**
 * Parse ComfyUI's `/object_info/<class_type>` for one combo widget's choices.
 * Handles both the old form (`[["a", "b"], {...}]`) and the newer COMBO form
 * (`["COMBO", {"options": [...]}]`). Returns null when the widget isn't a combo.
 */
export function comboChoices(info: unknown, classType: string, field: string): string[] | null {
  const node = (info as Record<string, unknown> | null)?.[classType] as { input?: Record<string, Record<string, unknown>> } | undefined;
  const inputs = node?.input;
  if (!inputs) return null;
  const spec = (inputs.required?.[field] ?? inputs.optional?.[field]) as unknown[] | undefined;
  if (!Array.isArray(spec) || !spec.length) return null;
  if (Array.isArray(spec[0])) return (spec[0] as unknown[]).map(String);
  const opts = (spec[1] as { options?: unknown } | undefined)?.options;
  if (spec[0] === "COMBO" && Array.isArray(opts)) return opts.map(String);
  return null;
}

// ---------------------------------------------------------------------------
// badges
// ---------------------------------------------------------------------------

/** A badge on a shot whose target isn't the series default (e.g. "LTX"). */
export function shotTargetBadge(
  s: Pick<ShotStatus, "target" | "built_target">,
  list: TargetList | null | undefined,
  seriesDefault: string,
): Badge | null {
  const t = shotTarget(s, seriesDefault);
  if (t === seriesDefault) return null;
  const re = isRetargeted(s) ? ` ${retargetNote(list, s)}` : "";
  return { kind: "target", label: targetShort(list, t), title: `Renders on ${targetLabel(list, t)}${re}; the series default is ${targetLabel(list, seriesDefault)}` };
}

/**
 * A badge on a take rendered with a different target than the shot's current
 * one. A take with no recorded target (from before sidecars) gets none.
 */
export function takeTargetBadge(
  t: Pick<TakeSummary, "target">,
  shotCurrent: string,
  list: TargetList | null | undefined,
): Badge | null {
  if (!t.target || t.target === shotCurrent) return null;
  return { kind: "target", label: targetShort(list, t.target), title: `Rendered on ${targetLabel(list, t.target)}; the shot now renders on ${targetLabel(list, shotCurrent)}` };
}

/**
 * The target badges for a shot row or a timeline clip: the shot's (when it isn't
 * on the series default), then the shown take's (when it rendered on another
 * target than the shot's current one), labelled with its take number.
 */
export function targetBadges(
  s: Pick<ShotStatus, "target" | "built_target">,
  list: TargetList | null | undefined,
  seriesDefault: string,
  shown?: Pick<TakeSummary, "take" | "target"> | null,
): Badge[] {
  const out: Badge[] = [];
  const sb = shotTargetBadge(s, list, seriesDefault);
  if (sb) out.push(sb);
  const tb = shown ? takeTargetBadge(shown, shotTarget(s, seriesDefault), list) : null;
  if (tb && shown) out.push({ ...tb, label: `${tn(shown.take)} ${tb.label}`, title: `${tn(shown.take)}: ${tb.title}` });
  return out;
}

// ---------------------------------------------------------------------------
// the render dialogs: size and length for the chosen target
// ---------------------------------------------------------------------------

export interface RunSize {
  /** "1344×768 · 141 frames", or "size set by the target" */
  text: string;
  /** true when the numbers are what a render would actually use (shot detail) */
  exact: boolean;
}

/**
 * What a run on `chosen` renders at. Shot detail's `effective` is exact only for
 * the shot's own target (GET /h3pipe/shot takes no target; see api.ts); for
 * another target the preset's size is a hint and the length isn't known.
 */
export function runSize(d: Pick<ShotDetail, "effective" | "pass" | "target" | "built_target"> | undefined, chosen: string, fallback: string, list: TargetList | null | undefined): RunSize {
  const eff = d?.effective;
  const cur = d ? shotTarget(d, fallback) : fallback;
  const effTarget = eff?.target || cur;
  if (eff && chosen === effTarget && eff.width && eff.height) {
    return { text: `${eff.width}×${eff.height}${eff.length ? ` · ${eff.length} frames` : ""}`, exact: true };
  }
  const p = d ? findTarget(list, chosen)?.presets?.[d.pass] : undefined;
  if (p?.width && p?.height) return { text: `size set by the target (its ${d!.pass} preset is ${p.width}×${p.height})`, exact: false };
  return { text: "size set by the target", exact: false };
}

// ---------------------------------------------------------------------------
// render warnings
// ---------------------------------------------------------------------------

export interface RenderWarning {
  shot?: string;
  text: string;
}

function warningText(w: unknown): RenderWarning | null {
  if (typeof w === "string") return w.trim() ? { text: w } : null;
  if (w && typeof w === "object") {
    const o = w as Record<string, unknown>;
    const text = [o.warning, o.message, o.text, o.reason, o.detail].find((x) => typeof x === "string" && x.trim()) as string | undefined;
    if (!text) return null;
    return { shot: typeof o.shot === "string" ? o.shot : undefined, text };
  }
  return null;
}

function warningsIn(v: unknown, shot?: string): RenderWarning[] {
  if (v == null) return [];
  const arr = Array.isArray(v) ? v : [v];
  const out: RenderWarning[] = [];
  for (const w of arr) {
    const x = warningText(w);
    if (x) out.push({ ...x, shot: x.shot ?? shot });
  }
  return out;
}

/**
 * Every warning a render response carries, wherever it sits (the contract
 * doesn't pin it down): a top-level `warnings`, and `warning`/`warnings` on
 * queued, skipped and errored entries. Deduplicated; never throws.
 */
export function renderWarnings(r: RenderResult | null | undefined): RenderWarning[] {
  if (!r || typeof r !== "object") return [];
  const out: RenderWarning[] = [];
  const o = r as unknown as Record<string, unknown>;
  out.push(...warningsIn(o.warnings));
  out.push(...warningsIn(o.warning));
  for (const key of ["queued", "skipped", "errors"]) {
    const list = o[key];
    if (!Array.isArray(list)) continue;
    for (const e of list) {
      if (!e || typeof e !== "object") continue;
      const x = e as Record<string, unknown>;
      const shot = typeof x.shot === "string" ? x.shot : undefined;
      out.push(...warningsIn(x.warnings, shot), ...warningsIn(x.warning, shot));
    }
  }
  const seen = new Set<string>();
  return out.filter((w) => {
    const k = `${w.shot ?? ""}|${w.text}`;
    if (seen.has(k)) return false;
    seen.add(k);
    return true;
  });
}
