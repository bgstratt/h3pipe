// The override form's fields (prompt with a diff against the built text, seed,
// model, LoRAs, steps, note). Shared by the shot inspector and the Refs tab.

import type { OverrideForm } from "../lib/overrideForm";
import { shortName } from "../lib/format";
import type { Lora } from "../types";
import { DiffView, LoraEditor, ModelSelect } from "./Fields";

export interface OverrideFieldsProps {
  form: OverrideForm;
  set: (patch: Partial<OverrideForm>) => void;
  /** what the prompt is without an override */
  builtPrompt: string;
  /** "built" for shots, "series config" for refs */
  builtLabel?: string;
  promptOverridden: boolean;
  showDiff: boolean;
  setShowDiff: (on: boolean) => void;
  seedPlaceholder: string;
  modelPlaceholder: string;
  stepsPlaceholder: string;
  /** the LoRAs in effect now (for "custom" to start from, and the hint) */
  effLoras: Lora[] | null;
  lorasOverridden: boolean;
  seedTitle?: string;
  rows?: number;
}

export function OverrideFields(p: OverrideFieldsProps) {
  const { form, set } = p;
  const built = p.builtLabel ?? "built";
  const promptChanged = form.prompt !== p.builtPrompt;
  return (
    <>
      <div className="h3-row">
        <span className="h3-h h3-grow">Prompt {p.promptOverridden ? <span className="h3-badge h3-b-override">override</span> : <span className="h3-muted">({built})</span>}</span>
        <label className="h3-check h3-small"><input type="checkbox" checked={p.showDiff} onChange={(e) => p.setShowDiff(e.target.checked)} /> diff</label>
        <button className="h3-btn" disabled={!promptChanged} title={`Put the ${built} prompt back in the box`} onClick={() => set({ prompt: p.builtPrompt })}>
          {built === "built" ? "Built" : "Series config"}
        </button>
      </div>
      {p.showDiff ? (
        <DiffView oldText={p.builtPrompt} newText={form.prompt} />
      ) : (
        <textarea className="h3-in" rows={p.rows ?? 12} value={form.prompt} onChange={(e) => set({ prompt: e.target.value })} spellCheck={false} />
      )}
      <div className="h3-field">
        <label>Seed</label>
        <div className="h3-row">
          <input
            className="h3-in h3-grow h3-mono"
            inputMode="numeric"
            placeholder={p.seedPlaceholder}
            value={form.seed}
            onChange={(e) => set({ seed: e.target.value.replace(/[^\d]/g, "") })}
            title={p.seedTitle}
          />
          {form.seed && <button className="h3-btn h3-icon" title="Clear" onClick={() => set({ seed: "" })}>✕</button>}
        </div>
        <label>Model</label>
        <ModelSelect value={form.model} onChange={(model) => set({ model })} placeholder={p.modelPlaceholder} />
        <label>LoRAs</label>
        <div className="h3-col" style={{ gap: 3 }}>
          <span className="h3-seg">
            <button className={form.lorasMode === "built" ? "h3-on" : ""} onClick={() => set({ lorasMode: "built" })}>{built}</button>
            <button
              className={form.lorasMode === "custom" ? "h3-on" : ""}
              onClick={() => set({
                lorasMode: "custom",
                loras: form.loras.length ? form.loras : (p.effLoras ?? []).map((l) => ({ name: l.name, strength: String(l.strength) })),
              })}
            >
              custom
            </button>
          </span>
          {form.lorasMode === "custom" ? (
            <LoraEditor rows={form.loras} onChange={(loras) => set({ loras })} />
          ) : (
            <span className="h3-muted h3-small">
              {p.effLoras?.length && !p.lorasOverridden ? p.effLoras.map((l) => `${shortName(l.name, 32)} @${l.strength}`).join(", ") : !p.lorasOverridden ? "the workflow's LoRA" : ""}
            </span>
          )}
        </div>
        <label>Steps</label>
        <input className="h3-in" inputMode="numeric" placeholder={p.stepsPlaceholder} value={form.steps} onChange={(e) => set({ steps: e.target.value.replace(/[^\d]/g, "") })} />
        <label>Note</label>
        <input className="h3-in" value={form.note} placeholder="why this override" onChange={(e) => set({ note: e.target.value })} />
      </div>
    </>
  );
}
