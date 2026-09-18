import { useEffect, useMemo } from "react";
import { loadModels } from "../actions";
import { diffStats, diffWords } from "../lib/diff";
import type { LoraRow } from "../lib/overrideForm";
import { shortName } from "../lib/format";
import { useApp } from "../store";

/** Model picker from ComfyUI's diffusion_models (or unet) list. `""` = the placeholder. */
export function ModelSelect({ value, onChange, placeholder }: { value: string; onChange: (v: string) => void; placeholder?: string }) {
  const models = useApp((s) => s.models);
  const err = useApp((s) => s.modelsError);
  useEffect(() => {
    void loadModels();
  }, []);
  const list = models ?? [];
  const missing = value && !list.includes(value);
  return (
    <select className="h3-in" value={value} onChange={(e) => onChange(e.target.value)} title={value || placeholder || ""}>
      {placeholder != null && <option value="">{placeholder}</option>}
      {missing && <option value={value}>{shortName(value, 48)} (not installed)</option>}
      {list.map((m) => (
        <option key={m} value={m} title={m}>
          {shortName(m, 48)}
        </option>
      ))}
      {!models && !err && <option disabled>Loading models…</option>}
      {err && <option disabled>Couldn't list models: {err}</option>}
    </select>
  );
}

/** A stack of LoRA rows with strength. */
export function LoraEditor({ rows, onChange }: { rows: LoraRow[]; onChange: (rows: LoraRow[]) => void }) {
  const loras = useApp((s) => s.loras);
  useEffect(() => {
    void loadModels();
  }, []);
  const list = loras ?? [];
  const set = (i: number, patch: Partial<LoraRow>) => onChange(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  return (
    <div className="h3-col" style={{ gap: 3 }}>
      {rows.map((r, i) => (
        <div key={i} className="h3-lora">
          <select className="h3-in" value={r.name} onChange={(e) => set(i, { name: e.target.value })} title={r.name}>
            <option value="">(choose a LoRA)</option>
            {r.name && !list.includes(r.name) && <option value={r.name}>{shortName(r.name, 44)} (not installed)</option>}
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
