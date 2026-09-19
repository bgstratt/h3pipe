// Readiness (docs/API.md "Readiness, requirement tiers, and the episode
// target"): the badges, the plain-words tier explanations, the What's missing
// panel's grouping, the episode target's source labels and counts, the
// series.json snippet, and how a render skip for a missing file is spotted.
// Pure functions; the components call them.

import type {
  EpisodeStatus, EpisodeTargetSource, MissingFile, Readiness, RenderSkip, RequirementTier, Resolution, ShotStatus, Target,
  TargetList,
} from "../types";
import { shortName } from "./format";

// ---------------------------------------------------------------------------
// badges
// ---------------------------------------------------------------------------

export interface ReadinessBadge {
  status: Readiness["status"];
  /** ✓ ◐ ✗ ? */
  icon: string;
  /** "ready", "degraded", "not ready", "unknown" */
  label: string;
  /** a CSS modifier: h3-rd-<cls> */
  cls: "ready" | "degraded" | "not-ready" | "unknown";
  /** the tooltip: what it means, and what's missing */
  title: string;
}

/** The readiness of a target in the list (undefined: the server didn't say, e.g. no `?ready=1`). */
export function readinessOf(list: TargetList | null | undefined, id: string | null | undefined): Readiness | undefined {
  if (!id) return undefined;
  const r = list?.targets.find((t) => t.id === id)?.readiness;
  return r ? normReadiness(r) : undefined;
}

/** Fill in what an older or partial answer leaves out, so the UI never trips on it. */
export function normReadiness(r: Partial<Readiness>): Readiness {
  const status = r.status === "ready" || r.status === "degraded" || r.status === "not_ready" ? r.status : "unknown";
  return {
    status,
    missing: Array.isArray(r.missing) ? r.missing.map((m) => ({ ...m, tier: normTier(m.tier) })) : [],
    resolved: r.resolved && typeof r.resolved === "object" ? r.resolved : {},
    features_off: Array.isArray(r.features_off) ? r.features_off : [],
    nodes_missing: Array.isArray(r.nodes_missing) ? r.nodes_missing : [],
  };
}

/** An unmarked param is required (API.md). */
export function normTier(t: unknown): RequirementTier {
  return t === "accelerator" || t === "optional" ? t : "required";
}

function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`;
}

/** "2 files missing", "1 custom node missing", "1 file and 1 custom node missing" */
export function missingCountText(r: Readiness, tiers: RequirementTier[] = ["required", "accelerator", "optional"]): string {
  const files = r.missing.filter((m) => tiers.includes(m.tier)).length;
  const nodes = r.nodes_missing.length;
  const parts = [files ? plural(files, "file") : "", nodes ? plural(nodes, "custom node") : ""].filter(Boolean);
  return parts.length ? `${parts.join(" and ")} missing` : "nothing missing";
}

/** The badge for a target's readiness, or null when the server didn't send any. */
export function readinessBadge(r: Readiness | null | undefined): ReadinessBadge | null {
  if (!r) return null;
  const n = normReadiness(r);
  switch (n.status) {
    case "ready":
      return {
        status: "ready", icon: "✓", label: "ready", cls: "ready",
        title: Object.values(n.resolved).some((x) => x.how === "family")
          ? "Ready: every file it needs is installed (some as a same-family substitute)"
          : "Ready: every file it needs is installed",
      };
    case "degraded":
      return {
        status: "degraded", icon: "◐", label: "degraded", cls: "degraded",
        title: `Renders, but degraded: ${missingCountText(n, ["accelerator", "optional"])}${n.features_off.length ? `; off: ${n.features_off.join(", ")}` : ""}`,
      };
    case "not_ready":
      return {
        status: "not_ready", icon: "✗", label: "not ready", cls: "not-ready",
        title: `Can't render yet: ${missingCountText(n, ["required"])}`,
      };
    default:
      return { status: "unknown", icon: "?", label: "unknown", cls: "unknown", title: "Readiness unknown: ComfyUI didn't answer" };
  }
}

/** A picker option's text: "✓ MiniMax H3 Ref2VA", "✗ LTX+refs (not ready)". */
export function targetOptionText(t: Pick<Target, "id" | "label" | "readiness">, isDefault = false): string {
  const b = readinessBadge(t.readiness);
  const name = `${t.label || t.id}${isDefault ? " (default)" : ""}`;
  if (!b) return name;
  return `${b.icon} ${name}${b.status === "ready" ? "" : ` (${b.label})`}`;
}

// ---------------------------------------------------------------------------
// the What's missing panel
// ---------------------------------------------------------------------------

/** "enables duration head": the feature an optional file turns on. */
export function featureOf(m: MissingFile, target?: Pick<Target, "models"> | null): string {
  if (m.feature) return m.feature;
  const label = target?.models?.[m.param]?.label;
  return label || m.param.replace(/_/g, " ");
}

/** A tier in plain words, for one missing file. */
export function tierText(m: MissingFile, target?: Pick<Target, "models"> | null): string {
  switch (normTier(m.tier)) {
    case "accelerator":
      return "speeds it up; without it, renders use slower base settings";
    case "optional":
      return `enables ${featureOf(m, target)}`;
    default:
      return "needed";
  }
}

export const TIER_LABEL: Record<RequirementTier, string> = {
  required: "Required",
  accelerator: "Accelerator",
  optional: "Optional",
};

export const TIER_ORDER: RequirementTier[] = ["required", "accelerator", "optional"];

export interface MissingGroup {
  tier: RequirementTier;
  label: string;
  /** what the whole group means */
  blurb: string;
  files: MissingFile[];
}

/** A target's missing files by tier (required first), empty groups left out. */
export function missingGroups(r: Readiness | null | undefined): MissingGroup[] {
  if (!r) return [];
  const n = normReadiness(r);
  const blurb: Record<RequirementTier, string> = {
    required: "Needed: this target can't render until these are installed.",
    accelerator: "Speed-ups: without them, renders use the target's slower base settings.",
    optional: "Features: without them, only that feature is off.",
  };
  return TIER_ORDER.map((tier) => ({ tier, label: TIER_LABEL[tier], blurb: blurb[tier], files: n.missing.filter((m) => m.tier === tier) }))
    .filter((g) => g.files.length > 0);
}

/** Where a file goes, as a path under ComfyUI: "models/loras". */
export function folderPath(folder: string | null | undefined): string {
  if (!folder) return "";
  return /^models[\\/]/.test(folder) ? folder : `models/${folder}`;
}

/** The link for a missing file: a URL only when the server has a trustworthy one. */
export function downloadOf(m: MissingFile): { url: string } | { search: string } {
  if (m.url && /^https?:\/\//i.test(m.url)) return { url: m.url };
  return { search: m.source?.trim() || m.want };
}

/** "search for …" text for a file with no URL. */
export function searchText(m: MissingFile): string {
  const d = downloadOf(m);
  if ("url" in d) return "";
  return /^search\b/i.test(d.search) ? d.search : `search for ${d.search}`;
}

/** Plain words for a resolution that isn't exact, or null for an exact one. */
export function resolutionText(param: string, r: Resolution, target?: Pick<Target, "models"> | null): string | null {
  const what = target?.models?.[param]?.label || param.replace(/_/g, " ");
  const want = r.want ? shortName(r.want, 60) : what;
  switch (r.how) {
    case "exact":
      return null;
    case "family":
      return `using ${r.using ? shortName(r.using, 60) : "another file"} instead of ${want} (same family)`;
    case "base":
      return `rendered with base settings: ${want} missing`;
    case "off":
      return `${what} off: ${want} missing`;
    default:
      return `${what}: ${r.how}${r.using ? ` (${shortName(r.using, 60)})` : ""}`;
  }
}

/** Every non-exact resolution as a note, in param order. */
export function resolutionNotes(resolved: Record<string, Resolution> | null | undefined, target?: Pick<Target, "models"> | null): string[] {
  if (!resolved || typeof resolved !== "object") return [];
  const out: string[] = [];
  for (const [param, r] of Object.entries(resolved)) {
    if (!r || typeof r !== "object") continue;
    const t = resolutionText(param, r, target);
    if (t) out.push(t);
  }
  return out;
}

/** A target whose readiness needs a warning where it's picked: not ready or degraded. */
export function pickWarning(label: string, r: Readiness | null | undefined): { severity: "err" | "warn"; text: string } | null {
  if (!r) return null;
  const n = normReadiness(r);
  if (n.status === "not_ready") return { severity: "err", text: `${label} can't render yet: ${missingCountText(n, ["required"])}.` };
  if (n.status === "degraded") {
    const acc = n.missing.some((m) => m.tier === "accelerator");
    const off = n.features_off.length ? ` Off: ${n.features_off.join(", ")}.` : "";
    return { severity: "warn", text: `${label} renders degraded${acc ? " (slower base settings)" : ""}: ${missingCountText(n, ["accelerator", "optional"])}.${off}` };
  }
  return null;
}

/** The Shots tab banner for a not-ready episode target, or null. */
export function notReadyBanner(label: string, r: Readiness | null | undefined): string | null {
  if (!r) return null;
  const n = normReadiness(r);
  if (n.status !== "not_ready") return null;
  return `${label} can't render yet: ${missingCountText(n, ["required"])}`;
}

// ---------------------------------------------------------------------------
// the episode target
// ---------------------------------------------------------------------------

export const TARGET_SOURCE_LABEL: Record<EpisodeTargetSource, string> = {
  editor: "set in editor",
  series: "from series.json",
  default: "default",
};

/** "from series.json", "set in editor", "default"; "" for an older server. */
export function episodeTargetSourceLabel(src: EpisodeTargetSource | null | undefined): string {
  return src ? TARGET_SOURCE_LABEL[src] ?? "" : "";
}

/** series.json's target: `series_target`, else the list's default, else null. */
export function seriesTarget(st: Pick<EpisodeStatus, "series_target"> | null | undefined, list: TargetList | null | undefined): string | null {
  return st?.series_target || list?.default?.video || null;
}

export interface TargetCounts {
  /** shots that follow the episode target */
  episode: number;
  /** shots with a target of their own (override, script, profile, request) */
  own: number;
  /** shots the server didn't say about (an older server) */
  unknown: number;
}

/** How many shots follow the episode target and how many set their own. Orphans don't count. */
export function targetCounts(shots: Pick<ShotStatus, "orphan" | "target_source">[] | null | undefined): TargetCounts {
  const out = { episode: 0, own: 0, unknown: 0 };
  for (const s of shots ?? []) {
    if (s.orphan) continue;
    if (s.target_source === "episode") out.episode++;
    else if (s.target_source) out.own++;
    else out.unknown++;
  }
  return out;
}

/** "31 shots follow it · 2 set their own" */
export function targetCountsText(c: TargetCounts): string {
  const parts: string[] = [];
  if (c.episode || !c.own) parts.push(`${plural(c.episode, "shot")} follow${c.episode === 1 ? "s" : ""} it`);
  if (c.own) parts.push(`${c.own} set${c.own === 1 ? "s" : ""} ${c.own === 1 ? "its" : "their"} own`);
  return parts.join(" · ");
}

/** The line for series.json's `series` block: `"target": "ltx2"`. */
export function seriesSnippet(id: string): string {
  return `"target": ${JSON.stringify(id)}`;
}

// ---------------------------------------------------------------------------
// render skips
// ---------------------------------------------------------------------------

/** The files a skip names, wherever the server put them (see RenderSkip). */
export function skipMissingFiles(x: RenderSkip): MissingFile[] {
  const v = x.missing_files ?? x.missing;
  return Array.isArray(v) ? v.filter((m) => m && typeof m === "object" && typeof m.want === "string") : [];
}

/**
 * A skip because the shot's target isn't ready (a required file missing): it
 * names the files, or its reason says so. Missing refs and model mismatches
 * are their own kinds.
 */
export function isMissingFileSkip(x: RenderSkip): boolean {
  if (x.missing_refs?.length || x.model_mismatch?.length) return false;
  if (skipMissingFiles(x).length) return true;
  return /required (model )?file|not installed|isn't ready|is not ready|not ready|can't render yet|file missing|missing (model )?file/i.test(x.reason ?? "");
}
