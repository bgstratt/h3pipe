import { useEffect, useMemo } from "react";
import { loadModels } from "../actions";
import { diffStats, diffWords } from "../lib/diff";
import type { LoraRow } from "../lib/overrideForm";
import { shortName } from "../lib/format";
import { isModelMismatch, modelGroups, modelWarning } from "../lib/targets";
import { useApp } from "../store";
import type { ModelList } from "../types";

/** Model picker from ComfyUI's diffusion_models (or unet) list. `""` = the placeholder. */
/** `choices` (a target's own list) replaces ComfyUI's generic one. `grouped`
 * (GET /h3pipe/models) puts the files of the target's family first, the rest
 * under "Other files (unverified)", and warns when one of those is picked. */
export function ModelSelect({ value, onChange, placeholder, choices, grouped }: { value: string; onChange: (v: string) => void; placeholder?: string; choices?: string[]; grouped?: ModelList }) {
  const models = useApp((s) => s.models);
  const err = useApp((s) => s.modelsError);
  useEffect(() => {
    void loadModels();
  }, []);
  const groups = modelGroups(grouped);
  if (groups && grouped) {
    const all = grouped.files.map((f) => f.name);
    const warn = modelWarning(grouped, value);
    const mismatch = isModelMismatch(grouped, value);
    return (
      <div className="h3-col" style={{ gap: 3 }}>
        <select className="h3-in" value={value} onChange={(e) => onChange(e.target.value)} title={value || placeholder || ""}>
          {placeholder != null && <option value="">{placeholder}</option>}
          {value && !all.includes(value) && <option value={value}>{shortName(value, 48)} (not installed)</option>}
          {groups.matching.map((f) => (
            <option key={f.name} value={f.name} title={f.match === "fingerprint" ? `${f.name}\n${f.detail}` : f.name}>
              {shortName(f.name, 48)}{f.match === "fingerprint" ? ` (${grouped.label} by its header)` : ""}
            </option>
          ))}
          {groups.other.length > 0 && (
            <optgroup label="Other files (unverified)">
              {groups.other.map((f) => (
                <option key={f.name} value={f.name} title={f.detail ? `${f.name}\n${f.detail}` : f.name}>
                  {shortName(f.name, 48)}{f.mismatch ? ` (${f.label || "another family"})` : ""}
                </option>
              ))}
            </optgroup>
          )}
        </select>
        {warn && <div className={`h3-note ${mismatch ? "h3-note-err" : "h3-note-info"} h3-small`}>{warn}</div>}
      </div>
    );
  }
  const list = choices ?? models ?? [];
  const missing = value && !list.includes(value);
  return (
    <select className="h3-in" value={value} onChange={(e) => onChange(e.target.value)} title={value || placeholder || ""}>
      {placeholder != null && <option value="">{placeholder}</option>}
      {missing && <option value={value}>{shortName(value, 48)} ({choices ? "not offered by this target" : "not installed"})</option>}
      {list.map((m) => (
        <option key={m} value={m} title={m}>
          {shortName(m, 48)}
        </option>
      ))}
      {!choices && !models && !err && <option disabled>Loading models…</option>}
      {!choices && err && <option disabled>Couldn't list models: {err}</option>}
    </select>
  );
}

/** A stack of LoRA rows with strength. */
export function LoraEditor({ rows, onChange, choices }: { rows: LoraRow[]; onChange: (rows: LoraRow[]) => void; choices?: string[] }) {
  const loras = useApp((s) => s.loras);
  useEffect(() => {
    void loadModels();
  }, []);
  const list = choices ?? loras ?? [];
  const set = (i: number, patch: Partial<LoraRow>) => onChange(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  return (
    <div className="h3-col" style={{ gap: 3 }}>
      {rows.map((r, i) => (
        <div key={i} className="h3-lora">
          <select className="h3-in" value={r.name} onChange={(e) => set(i, { name: e.target.value })} title={r.name}>
            <option value="">(choose a LoRA)</option>
            {r.name && !list.includes(r.name) && <option value={r.name}>{shortName(r.name, 44)} ({choices ? "not offered by this target" : "not installed"})</option>}
            {list.map((l) => (
              <option key={l} value={l} title={l}>{shortName(l, 44)}</option>
            ))}
          </select>
          <input
            className="h3-in"
            type="number"
            step={0.05}
            value={r.strength}
            title="Strength"
            onChange={(e) => set(i, { strength: e.target.value })}
          />
          <button className="h3-btn h3-icon" title="Remove" onClick={() => onChange(rows.filter((_, j) => j !== i))}>✕</button>
        </div>
      ))}
      <div className="h3-row">
        <button className="h3-btn" onClick={() => onChange([...rows, { name: "", strength: "1" }])}>+ LoRA</button>
        {!rows.length && <span className="h3-muted h3-small">No LoRAs (the workflow's LoRA is bypassed)</span>}
      </div>
    </div>
  );
}

/** The built prompt (old) against the edited one (new), word by word. */
export function DiffView({ oldText, newText }: { oldText: string; newText: string }) {
  const parts = useMemo(() => diffWords(oldText, newText), [oldText, newText]);
  const stats = useMemo(() => diffStats(parts), [parts]);
  if (oldText === newText) return <div className="h3-muted h3-small">Same as the built prompt.</div>;
  return (
    <div className="h3-col" style={{ gap: 2 }}>
      <div className="h3-small h3-muted">
        vs the built prompt: <span style={{ color: "var(--h3-ok)" }}>+{stats.added}</span> / <span style={{ color: "var(--h3-failed)" }}>−{stats.removed}</span> words
      </div>
      <pre className="h3-pre h3-diff">
        {parts.map((p, i) =>
          p.op === "eq" ? <span key={i}>{p.text}</span> : p.op === "add" ? <ins key={i}>{p.text}</ins> : <del key={i}>{p.text}</del>,
        )}
      </pre>
    </div>
  );
}
