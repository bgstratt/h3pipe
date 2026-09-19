// The inspector's override form: text fields in, the minimal PUT /h3pipe/override
// `fields` out (only what changed; null clears a field, per docs/API.md).

import { parseSeed } from "../api";
import type { Lora, Override, OverrideFields } from "../types";

/** What the form needs from a shot's detail (or a ref, adapted): the override
 * as stored, the prompt in effect, and the prompt without an override. */
export interface OverrideSource {
  override: Override;
  effective: { prompt: string };
  built_prompt: string;
}
import { promptText } from "./format";

export interface LoraRow {
  name: string;
  strength: string;
}

export interface OverrideForm {
  prompt: string;
  /** "" = no seed override */
  seed: string;
  /** "" = the built model */
  model: string;
  /** built = no LoRA override; custom = exactly `loras` (possibly none) */
  lorasMode: "built" | "custom";
  loras: LoraRow[];
  /** "" = the built steps */
  steps: string;
  note: string;
}

export function formFromDetail(d: Pick<OverrideSource, "override" | "effective">): OverrideForm {
  const o = d.override;
  return {
    prompt: o.prompt != null ? promptText(o.prompt) : d.effective.prompt,
    seed: o.seed ?? "",
    model: o.model ?? "",
    lorasMode: o.loras != null ? "custom" : "built",
    loras: (o.loras ?? []).map(loraRow),
    steps: o.steps != null ? String(o.steps) : "",
    note: o.note ?? "",
  };
}

export function loraRow(l: Lora): LoraRow {
  return { name: l.name, strength: String(l.strength ?? 1) };
}

export function parseLoras(rows: LoraRow[]): Lora[] {
  return rows
    .filter((r) => r.name.trim())
    .map((r) => {
      const s = r.strength.trim() === "" ? 1 : Number(r.strength);
      if (!Number.isFinite(s)) throw new Error(`LoRA strength for ${r.name} isn't a number: "${r.strength}"`);
      return { name: r.name.trim(), strength: s };
    });
}

export function parseSteps(text: string): number {
  const n = Number(text.trim());
  if (!Number.isInteger(n) || n < 1 || n > 1000) throw new Error(`Steps must be a whole number from 1 to 1000, not "${text}".`);
  return n;
}

function sameForm(a: OverrideForm, b: OverrideForm, k: keyof OverrideForm): boolean {
  return JSON.stringify(a[k]) === JSON.stringify(b[k]);
}

export function isDirty(form: OverrideForm, initial: OverrideForm): boolean {
  return (Object.keys(form) as (keyof OverrideForm)[]).some((k) => !sameForm(form, initial, k));
}

/**
 * The fields to send: only those that changed since `initial`. Throws a readable
 * Error for bad input (seed, steps, LoRA strength).
 */
export function overrideFields(form: OverrideForm, initial: OverrideForm, d: Pick<OverrideSource, "built_prompt">): OverrideFields {
  const f: OverrideFields = {};
  if (form.prompt !== initial.prompt) {
    // back to exactly the built text = no prompt override
    f.prompt = form.prompt.trim() === "" || form.prompt === d.built_prompt ? null : form.prompt;
  }
  if (form.seed !== initial.seed) f.seed = form.seed.trim() === "" ? null : parseSeed(form.seed);
  if (form.model !== initial.model) f.model = form.model === "" ? null : form.model;
  if (form.lorasMode !== initial.lorasMode || (form.lorasMode === "custom" && !sameForm(form, initial, "loras"))) {
    f.loras = form.lorasMode === "built" ? null : parseLoras(form.loras);
  }
  if (form.steps !== initial.steps) f.steps = form.steps.trim() === "" ? null : parseSteps(form.steps);
  if (form.note !== initial.note) f.note = form.note.trim() === "" ? null : form.note;
  return f;
}
