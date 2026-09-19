// The override form's fields (prompt with a diff against the built text, seed,
// model, LoRAs, steps, note). Shared by the shot inspector and the Refs tab.

import type { ReactNode } from "react";
import type { OverrideForm } from "../lib/overrideForm";
import { shortName } from "../lib/format";
import type { Lora, ModelList } from "../types";
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
  /** Phase 8: the model picker's list under the shot's target. undefined =
   * ComfyUI's generic list; null = the target has no model widget. */
  modelChoices?: string[] | null;
  /** GET /h3pipe/models: group the model picker by the target's family */
  modelFiles?: ModelList;
  /** the same for LoRAs; null hides the LoRA rows */
  loraChoices?: string[] | null;
  /** Phase 8: the prompt can't be overridden (a retargeted shot): show this
   * note and the effective prompt read-only instead of the editor. */
  promptLocked?: { note: ReactNode; text: string; label?: string } | null;
  /** Phase 8.5: the negative field (targets with a `negative` param); absent hides it.
   * `effective` is the negative a render uses without an override, `source` where it comes from. */
  negative?: { effective: string; source: string; note?: string | null } | null;
  /** Phase 8.5: a two-stage target's low-noise model picker; absent hides it */
  modelLow?: { placeholder: string; files?: ModelList; choices?: string[] } | null;
}

export function OverrideFields(p: OverrideFieldsProps) {
  const { form, set } = p;
  const built = p.builtLabel ?? "built";
  const promptChanged = form.prompt !== p.builtPrompt;
  const locked = p.promptLocked;
  return (
    <>
      {locked ? (
        <>
          <div className="h3-row">
            <span className="h3-h h3-grow">Prompt <span className="h3-muted">({locked.label ?? "written by the target, read-only"})</span></span>
          </div>
          <div className="h3-note h3-note-info h3-small">{locked.note}</div>
          <pre className="h3-pre" style={{ maxHeight: 260 }}>{locked.text}</pre>
        </>
      ) : (
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
        </>
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
        {p.modelChoices === null ? (
          <span className="h3-muted h3-small">set by the target (it has no model widget)</span>
        ) : (
          <ModelSelect value={form.model} onChange={(model) => set({ model })} placeholder={p.modelPlaceholder} choices={p.modelChoices} grouped={p.modelFiles} />
        )}
        {p.modelLow && (
          <>
            <label title="The second (low-noise) stage of a two-stage target (Wan 2.2 14B)">Low-noise model</label>
            <ModelSelect
              value={form.modelLow}
              onChange={(modelLow) => set({ modelLow })}
              placeholder={p.modelLow.placeholder}
              choices={p.modelLow.choices ?? p.modelLow.files?.files.map((f) => f.name)}
              grouped={p.modelLow.files}
            />
          </>
        )}
        {p.loraChoices !== null && (
          <>
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
                <LoraEditor rows={form.loras} onChange={(loras) => set({ loras })} choices={p.loraChoices} />
              ) : (
                <span className="h3-muted h3-small">
                  {p.effLoras?.length && !p.lorasOverridden ? p.effLoras.map((l) => `${shortName(l.name, 32)} @${l.strength}`).join(", ") : !p.lorasOverridden ? "the workflow's LoRA" : ""}
                </span>
              )}
            </div>
          </>
        )}
        <label>Steps</label>
        <input className="h3-in" inputMode="numeric" placeholder={p.stepsPlaceholder} value={form.steps} onChange={(e) => set({ steps: e.target.value.replace(/[^\d]/g, "") })} />
        {p.negative && (
          <>
            <label title="What the model should avoid. Empty = the episode's negative.txt, else series.json's, else the target's">Negative</label>
            <div className="h3-col" style={{ gap: 2 }}>
              <textarea
                className="h3-in"
                rows={2}
                value={form.negative}
                placeholder={p.negative.effective ? `${p.negative.effective}` : "(none)"}
                onChange={(e) => set({ negative: e.target.value })}
                spellCheck={false}
              />
              <span className="h3-small h3-muted">
                {form.negative.trim()
                  ? "shot override (this pass)"
                  : p.negative.source ? `${p.negative.source}${p.negative.effective ? "" : " (empty)"}` : "no override"}
                {p.negative.note ? ` · ${p.negative.note}` : ""}
              </span>
            </div>
          </>
        )}
        <label>Note</label>
        <input className="h3-in" value={form.note} placeholder="why this override" onChange={(e) => set({ note: e.target.value })} />
      </div>
    </>
  );
}
