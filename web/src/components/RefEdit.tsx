// Edit a ref's picture (POST /h3pipe/refs/generate with `edit`): a take, or
// the live picture, changed as an instruction says on an edit target, as a new
// candidate of the same ref and view. Other refs' pictures can come along as
// image 2 onward ("put her in the coat from image 2").

import { useMemo, useState } from "react";
import { generateRef } from "../actions";
import { tn } from "../lib/format";
import { imageTargets, isEditTarget } from "../lib/imageTargets";
import { parseLoras, type LoraRow } from "../lib/overrideForm";
import { hasViews, isAudioRef, viewLabel } from "../lib/refs";
import { targetOptionText } from "../lib/readiness";
import { useApp } from "../store";
import type { Ref, RefEditRecord, RefEditWith } from "../types";
import { LoraEditor } from "./Fields";
import { useTargets } from "./Targets";

const EXAMPLES = [
  "replace the coat with a grey wool hoodie",
  "remove the people; keep the empty room",
  "same place at dawn, low warm sunlight from the left",
];

/** "t03 of Ada (side)" / "the live picture" */
export function editSourceText(e: RefEditRecord): string {
  const what = e.take != null ? tn(e.take) : "the live picture";
  return `${what}${e.view ? ` (${viewLabel(e.view)})` : ""}`;
}

export function EditForm({ r, view, take, onDone }: { r: Ref; view: string | null; take: number | null; onDone: () => void }) {
  const { list } = useTargets();
  const refs = useApp((s) => (s.ep ? s.refs[s.ep] : null));
  const busy = useApp((s) => !!s.busy[`refgen|${r.id}`]);
  const edits = useMemo(() => imageTargets(list).filter(isEditTarget), [list]);
  const [text, setText] = useState("");
  const [target, setTarget] = useState("");
  const [count, setCount] = useState(1);
  const [withs, setWiths] = useState<RefEditWith[]>([]);
  const [loras, setLoras] = useState<LoraRow[]>([]);
  const [wrap, setWrap] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const t = edits.find((x) => x.id === target);
  const max = t?.capabilities?.max_refs ?? null;
  const pictures = (refs ?? []).filter((x) => !isAudioRef(x) && x.exists && x.id !== r.id);
  const nameOf = (id: string) => refs?.find((x) => x.id === id)?.name ?? id;
  const tooMany = max != null && 1 + withs.length > max;

  const submit = async () => {
    setErr(null);
    let ls;
    try {
      ls = parseLoras(loras);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      return;
    }
    const ok = await generateRef({
      ref: r.id, view, count, seed_mode: "new", seed: null, prompt: text.trim(), model: null,
      loras: ls.length ? ls : null, steps: null, note: "", target: target || null,
      edit: { take, with: withs, wrap },
    });
    if (ok) onDone();
  };

  return (
    <div className="h3-col h3-ref-edit" style={{ gap: 6 }}>
      <div className="h3-small">
        Edit <b>{editSourceText({ take, view, instruction: "" })}</b> of {r.name}: the result is a new candidate, and the original stays.
      </div>
      <textarea
        className="h3-in"
        rows={3}
        value={text}
        placeholder={`What to change, e.g. “${EXAMPLES[r.kind === "location" ? 2 : 0]}”`}
        onChange={(e) => setText(e.target.value)}
      />
      <div className="h3-row h3-wrap" style={{ gap: 6 }}>
        <select className="h3-in" value={target} onChange={(e) => setTarget(e.target.value)} title="The edit model. The default is this ref's own model if it can edit, else the episode's keyframe model.">
          <option value="">(the episode's edit model)</option>
          {edits.map((x) => {
            const n = x.capabilities?.max_refs;
            return <option key={x.id} value={x.id}>{`${targetOptionText(x)}${n ? ` · up to ${n} picture${n === 1 ? "" : "s"}` : ""}`}</option>;
          })}
        </select>
        <select className="h3-in" value={count} onChange={(e) => setCount(Number(e.target.value))} title="How many candidates (each with a new seed)">
          {[1, 2, 3, 4].map((n) => <option key={n} value={n}>{n}×</option>)}
        </select>
        <label className="h3-small" title="Send the instruction exactly as typed, with nothing added around it: for a LoRA that wants its own trigger words (e.g. “<mva> front-left quarter view, eye-level shot”)">
          <input type="checkbox" checked={!wrap} onChange={(e) => setWrap(!e.target.checked)} /> send as typed
        </label>
      </div>
      <div className="h3-col" style={{ gap: 3 }}>
        <div className="h3-row h3-wrap" style={{ gap: 4 }}>
          <span className="h3-small h3-muted" title="Other refs' live pictures, sent after this one: the instruction can call them image 2, image 3…">Bring in:</span>
          {withs.map((w, i) => (
            <span key={i} className="h3-chip">
              image {i + 2}: {nameOf(w.ref)}{w.view ? ` (${viewLabel(w.view)})` : ""}
              <button className="h3-link" title="Leave it out" onClick={() => setWiths(withs.filter((_, j) => j !== i))}>✕</button>
            </span>
          ))}
          <select
            className="h3-in"
            value=""
            onChange={(e) => {
              const id = e.target.value;
              if (!id) return;
              const other = refs?.find((x) => x.id === id);
              setWiths([...withs, { ref: id, view: other && hasViews(other) ? "01_threequarter" : null }]);
            }}
          >
            <option value="">+ a picture…</option>
            {pictures.map((x) => <option key={x.id} value={x.id}>{`${x.name} (${x.kind})`}</option>)}
          </select>
        </div>
        {tooMany && <div className="h3-warn h3-small">{t ? targetOptionText(t) : "This model"} reads at most {max} picture{max === 1 ? "" : "s"}, and this edit has {1 + withs.length}.</div>}
      </div>
      <details>
        <summary className="h3-small h3-muted">LoRAs {loras.length ? `(${loras.length})` : ""}</summary>
        <LoraEditor rows={loras} onChange={setLoras} />
      </details>
      {err && <div className="h3-err h3-small">{err}</div>}
      <div className="h3-row" style={{ gap: 6 }}>
        <button className="h3-btn h3-primary" disabled={busy || !text.trim() || tooMany} onClick={() => void submit()}>
          <i className={busy ? "pi pi-spin pi-spinner" : "pi pi-pencil"} /> Edit {count > 1 ? `into ${count} takes` : ""}
        </button>
        <button className="h3-btn" onClick={onDone}>Cancel</button>
      </div>
    </div>
  );
}
