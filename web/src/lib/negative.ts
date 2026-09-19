// Negatives (docs/API.md "Negatives"): where a pass's negative prompt comes
// from, in words, and whether a target takes one. Pure functions.

import type { Target } from "../types";

/**
 * `negative_source` in words (API.md "Phase 8.5 as built": request | override |
 * negative.txt | series | preset | none): "this run", "shot override", "from
 * negative.txt", "from series.json", "target default"; "" for none (the target
 * takes no negative) or unknown.
 */
export function negativeSourceLabel(src: string | null | undefined): string {
  switch (src ?? "") {
    case "negative.txt":
      return "from negative.txt";
    case "series":
      return "from series.json";
    case "preset":
      return "target default";
    case "override":
      return "shot override";
    case "request":
      return "this run";
    case "none":
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
  if (eff?.negative_source) return eff.negative_source !== "none";
  return eff?.negative != null;
}

/** The negative has no effect at cfg ≤ 1 (turbo presets). */
export function negativeNoEffect(cfg: unknown): boolean {
  return typeof cfg === "number" && cfg <= 1;
}
