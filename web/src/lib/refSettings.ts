// P4: a ref's sampler settings in the Refs tab -- cfg and the image target's
// knobs (h3refs.TUNABLE_PARAMS) as form text, turned into override fields, and
// a take's settings made the ref's ("Use these settings"). Pure functions.

import { TUNABLE_PARAMS, type OverrideFields, type RefParams, type RefTake } from "../types";

export type Knobs = Record<"cfg" | (typeof TUNABLE_PARAMS)[number], string>;

/** An override's (or effective's) cfg and knobs as form text ("" = not set). */
export function knobsOf(o: { cfg?: number | null; params?: RefParams | null }): Knobs {
  return {
    cfg: o.cfg != null ? String(o.cfg) : "",
    ...(Object.fromEntries(TUNABLE_PARAMS.map((k) => [k, o.params?.[k] != null ? String(o.params[k]) : ""])) as Omit<Knobs, "cfg">),
  };
}

/** The changed knobs as override fields: `cfg`, and `params` whole when any knob changed. */
export function knobFields(k: Knobs, base: Knobs): OverrideFields {
  const out: OverrideFields = {};
  const num = (name: string, v: string) => {
    const n = Number(v);
    if (!Number.isFinite(n)) throw new Error(`${name} must be a number, not "${v}"`);
    return n;
  };
  if (k.cfg !== base.cfg) out.cfg = k.cfg.trim() === "" ? null : num("CFG", k.cfg);
  if (TUNABLE_PARAMS.some((p) => k[p] !== base[p])) {
    const params: RefParams = {};
    for (const p of TUNABLE_PARAMS) {
      const v = k[p].trim();
      if (v) params[p] = p === "sampler" || p === "scheduler" ? v : num(p, v);
    }
    out.params = Object.keys(params).length ? params : null;
  }
  return out;
}

/** "Use these settings": a take's seed, model, LoRAs, steps, cfg, knobs and size as the override. */
export function takeSettings(t: RefTake, keyframe: boolean): OverrideFields {
  const f: OverrideFields = {};
  if (t.seed != null) f.seed = t.seed;
  f.model = t.model || null;
  f.loras = t.loras ?? null;
  if (t.steps != null) f.steps = t.steps;
  if (t.cfg != null) f.cfg = t.cfg;
  f.params = t.params && Object.keys(t.params).length ? t.params : null;
  if (!keyframe && t.width && t.height) f.size = `${t.width}x${t.height}`;
  return f;
}

