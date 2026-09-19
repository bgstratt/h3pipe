// Negatives (docs/API.md "Negatives"): where a pass's negative prompt comes
// from, in words, and whether a target takes one. Pure functions.

import type { Target } from "../types";

/**
 * "from negative.txt", "from series.json", "target default", "shot override",
 * "this run"; "" when unknown. The contract doesn't pin the value strings
 * (TODO(contract) in api.ts), so the likely spellings are all read.
 */
export function negativeSourceLabel(src: string | null | undefined): string {
  switch ((src ?? "").trim().toLowerCase()) {
    case "negative.txt":
    case "negative_txt":
    case "episode":
    case "file":
      return "from negative.txt";
    case "series":
    case "series.json":
    case "series_config":
      return "from series.json";
    case "preset":
    case "target":
    case "default":
    case "target_default":
      return "target default";
    case "override":
    case "shot":
      return "shot override";
    case "request":
      return "this run";
    case "":
      return "";
    default:
      return String(src);
  }
}

/**
 * Whether the negative field is shown for a target: it says it takes a
 * negative (`capabilities.negative_prompt`), or the shot's effective values
 * carry one. H3 has no negative param.
 */
export function takesNegative(t: Pick<Target, "capabilities"> | undefined, eff?: { negative?: string | null; negative_source?: string | null } | null): boolean {
  if (t?.capabilities?.negative_prompt === true) return true;
  if (t?.capabilities?.negative_prompt === false) return false;
  return eff?.negative != null || !!eff?.negative_source;
}

/** The negative has no effect at cfg ≤ 1 (turbo presets). */
export function negativeNoEffect(cfg: unknown): boolean {
  return typeof cfg === "number" && cfg <= 1;
}
