import { useEffect, useMemo, useState, type ReactNode } from "react";
import { closeRedo, closeSidecar, copyText, loadDetail, runRedo, takePrompt, type RedoSeed } from "../actions";
import { errText, parseSeed } from "../api";
import { absPath, shortName, tn } from "../lib/format";
import { loraRow, parseLoras, parseSteps, type LoraRow } from "../lib/overrideForm";
import { useApp } from "../store";
import type { Pass, ShotDetail, TakeDetail } from "../types";
import { DiffView, LoraEditor, ModelSelect } from "./Fields";
import { useDetail, useDetailError } from "./hooks";

function Dialog({ title, onClose, children, footer, wide }: { title: ReactNode; onClose: () => void; children: ReactNode; footer?: ReactNode; wide?: boolean }) {
  useEffect(() => {
    const key = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [onClose]);
  return (
    <div className="h3-backdrop" onPointerDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="h3-dialog" style={wide ? { width: "min(760px, 100%)" } : undefined} role="dialog">
        <div className="h3-dialog-head">
          <span className="h3-title h3-grow">{title}</span>
          <button className="h3-btn h3-icon" title="Close" onClick={onClose}><i className="pi pi-times" /></button>
        </div>
        <div className="h3-dialog-body">{children}</div>
        {footer && <div className="h3-dialog-foot">{footer}</div>}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// redo
// ---------------------------------------------------------------------------

type SeedChoice = "new" | "same" | "typed";
type PromptSource = "current" | "parent";

interface RedoForm {
  parent: number | null;
  seedChoice: SeedChoice;
  typedSeed: string;
  model: string;
  loras: LoraRow[];
  lorasOn: boolean;
  steps: string;
  pass: Pass;
  note: string;
  source: PromptSource;
  prompt: string;
  save: boolean;
}

function initForm(d: ShotDetail, parent: TakeDetail | undefined, pass: Pass): RedoForm {
  const sc = parent?.sidecar ?? null;
  const loras = sc?.loras !== undefined ? sc.loras : d.effective.loras;
  return {
    parent: parent?.take ?? null,
    seedChoice: "new",
    typedSeed: sc?.seed ?? d.effective.seed ?? "",
    model: sc?.model ?? d.effective.model ?? "",
    loras: (loras ?? []).map(loraRow),
    lorasOn: loras != null,
    steps: String(sc?.steps ?? d.effective.steps ?? ""),
    pass,
    note: "",
    source: "current",
    prompt: d.effective.prompt,
    save: true,
  };
}

export function RedoDialog() {
  const r = useApp((s) => s.redo);
  const ep = useApp((s) => s.ep);
  const d = useDetail(r?.shot, r?.pass);
  const derr = useDetailError(r?.shot, r?.pass);
  if (!r || !ep) return null;
  return (
    <Dialog title={<>Redo {r.shot}{r.parent != null ? ` from ${tn(r.parent)}` : ""}</>} onClose={closeRedo} wide>
      {!d && !derr && <div className="h3-muted">Loading {r.shot}…</div>}
      {derr && !d && <div className="h3-note h3-note-err">{derr}</div>}
      {d && <RedoBody key={`${r.shot}|${r.pass}|${r.parent}`} d={d} shot={r.shot} openPass={r.pass} parent={r.parent} />}
    </Dialog>
  );
}

function RedoBody({ d, shot, openPass, parent }: { d: ShotDetail; shot: string; openPass: Pass; parent: number | null }) {
  const parentTake = d.takes.find((t) => t.take === parent);
  const [f, setF] = useState<RedoForm>(() => initForm(d, parentTake, openPass));
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [parentPrompt, setParentPrompt] = useState<{ take: number; text: string | null; error?: string } | null>(null);
  const [showDiff, setShowDiff] = useState(false);
  const set = (p: Partial<RedoForm>) => {
    setErr(null);
    setF((x) => ({ ...x, ...p }));
  };
  const pt = d.takes.find((t) => t.take === f.parent);
  const parentSeed = pt?.sidecar?.seed ?? null;
  // the target pass's detail (for its built prompt and effective values)
  const targetD = useDetail(shot, f.pass) ?? (f.pass === d.pass ? d : undefined);
  useEffect(() => {
    if (f.pass !== d.pass) void loadDetail(shot, f.pass);
  }, [f.pass, d.pass, shot]);

  // parent's prompt from its frozen shotlist
  useEffect(() => {
    if (f.source !== "parent" || f.parent == null) return;
    if (parentPrompt?.take === f.parent) return;
    let live = true;
    const take = f.parent;
    takePrompt(shot, take, d.pass)
      .then((text) => {
        if (!live) return;
        setParentPrompt({ take, text });
        if (text != null) setF((x) => ({ ...x, prompt: text }));
      })
      .catch((e) => live && setParentPrompt({ take, text: null, error: errText(e) }));
    return () => {
      live = false;
    };
  }, [f.source, f.parent, shot, d.pass, parentPrompt?.take]);

  const chooseParent = (n: number | null) => {
    const t = d.takes.find((x) => x.take === n);
    const init = initForm(d, t, f.pass);
    set({
      parent: n, model: init.model, loras: init.loras, lorasOn: init.lorasOn, steps: init.steps, typedSeed: init.typedSeed,
      ...(f.source === "parent" ? { prompt: "" } : {}),
    });
    setParentPrompt(null);
  };

  const submit = async () => {
    let seed: RedoSeed;
    let steps: number;
    let loras;
    try {
      if (f.seedChoice === "new") seed = { mode: "new" };
      else if (f.seedChoice === "same") {
        if (!parentSeed) throw new Error("The parent take has no recorded seed; type one instead.");
        seed = { mode: "same", seed: parentSeed };
      } else seed = { mode: "typed", seed: parseSeed(f.typedSeed) };
      steps = parseSteps(f.steps);
      loras = f.lorasOn ? parseLoras(f.loras) : null;
      if (!f.prompt.trim()) throw new Error("The prompt is empty.");
    } catch (e) {
      setErr(errText(e));
      return;
    }
    setBusy(true);
    try {
      const ok = await runRedo({
        shot, pass: f.pass, parent: f.parent, seed, model: f.model, loras, steps, prompt: f.prompt, note: f.note.trim(),
        saveAsOverride: f.save,
      });
      if (ok) closeRedo();
    } finally {
      setBusy(false);
    }
  };

  const builtPrompt = targetD?.built_prompt ?? d.built_prompt;
  return (
    <>
      <div className="h3-field" style={{ gridTemplateColumns: "76px minmax(0,1fr)" }}>
        <label>From take</label>
        <select className="h3-in" value={f.parent ?? ""} onChange={(e) => chooseParent(e.target.value === "" ? null : Number(e.target.value))}>
          <option value="">(none: the shot's current settings)</option>
          {d.takes.map((t) => (
            <option key={t.take} value={t.take}>
              {tn(t.take)} · {t.status}{t.sidecar?.seed ? ` · seed ${t.sidecar.seed}` : " · no sidecar"}
            </option>
          ))}
        </select>
        <label>Seed</label>
        <div className="h3-col" style={{ gap: 3 }}>
          <div className="h3-row h3-wrap">
            <label className="h3-check"><input type="radio" checked={f.seedChoice === "new"} onChange={() => set({ seedChoice: "new" })} /> new</label>
            <label className="h3-check" title={parentSeed ? `seed ${parentSeed}` : "The parent take has no recorded seed"}>
              <input type="radio" disabled={!parentSeed} checked={f.seedChoice === "same"} onChange={() => set({ seedChoice: "same" })} />
              same as {pt ? tn(pt.take) : "parent"}
            </label>
            <label className="h3-check"><input type="radio" checked={f.seedChoice === "typed"} onChange={() => set({ seedChoice: "typed" })} /> typed</label>
          </div>
          {f.seedChoice === "same" && parentSeed && <span className="h3-mono h3-muted">{parentSeed}</span>}
          {f.seedChoice === "typed" && (
            <input className="h3-in h3-mono" inputMode="numeric" value={f.typedSeed} onChange={(e) => set({ typedSeed: e.target.value.replace(/[^\d]/g, "") })} />
          )}
        </div>
        <label>Model</label>
        <ModelSelect value={f.model} onChange={(model) => set({ model })} placeholder="(the workflow's model)" />
        <label>LoRAs</label>
        <div className="h3-col" style={{ gap: 3 }}>
          <label className="h3-check h3-small">
            <input type="checkbox" checked={f.lorasOn} onChange={(e) => set({ lorasOn: e.target.checked })} />
            set LoRAs {f.lorasOn ? "" : "(off: the workflow's own LoRA)"}
          </label>
          {f.lorasOn && <LoraEditor rows={f.loras} onChange={(loras) => set({ loras })} />}
        </div>
        <label>Steps</label>
        <input className="h3-in" style={{ width: 80 }} inputMode="numeric" value={f.steps} onChange={(e) => set({ steps: e.target.value.replace(/[^\d]/g, "") })} />
        <label>Pass</label>
        <span className="h3-seg">
          {(["proxy", "final"] as Pass[]).map((p) => (
            <button key={p} className={f.pass === p ? "h3-on" : ""} onClick={() => set({ pass: p })}>{p}</button>
          ))}
        </span>
        <label>Note</label>
        <input className="h3-in" placeholder="what this take tries (kept in its sidecar)" value={f.note} onChange={(e) => set({ note: e.target.value })} />
        <label>Prompt</label>
        <div className="h3-col" style={{ gap: 3 }}>
          <div className="h3-row h3-wrap">
            <label className="h3-check">
              <input type="radio" checked={f.source === "current"} onChange={() => set({ source: "current", prompt: (targetD ?? d).effective.prompt })} />
              current {(targetD ?? d).override.prompt != null ? "override" : "build"}
            </label>
            <label className="h3-check" title={pt?.files.shotlist ? "From the take's frozen shotlist" : "That take has no frozen shotlist"}>
              <input type="radio" disabled={!pt?.files.shotlist} checked={f.source === "parent"} onChange={() => set({ source: "parent" })} />
              {pt ? tn(pt.take) : "parent take"}'s prompt
            </label>
            <span className="h3-grow" />
            <label className="h3-check h3-small"><input type="checkbox" checked={showDiff} onChange={(e) => setShowDiff(e.target.checked)} /> diff vs built</label>
          </div>
          {f.source === "parent" && parentPrompt?.error && <div className="h3-note h3-note-err">{parentPrompt.error}</div>}
          {f.source === "parent" && f.parent != null && parentPrompt?.take !== f.parent && <div className="h3-muted">Loading {tn(f.parent)}'s prompt…</div>}
          {showDiff ? (
            <DiffView oldText={builtPrompt} newText={f.prompt} />
          ) : (
            <textarea className="h3-in" rows={10} value={f.prompt} spellCheck={false} onChange={(e) => set({ prompt: e.target.value })} />
          )}
        </div>
      </div>
      <label className="h3-check" title="Writes the prompt, model, LoRAs, steps (and a typed/same seed) to overrides.json first, so later renders keep them">
        <input type="checkbox" checked={f.save} onChange={(e) => set({ save: e.target.checked })} />
        Save these as the shot's {f.pass} override
      </label>
      {err && <div className="h3-note h3-note-err">{err}</div>}
      <div className="h3-row" style={{ justifyContent: "flex-end" }}>
        <span className="h3-muted h3-small h3-grow">
          {f.model ? shortName(f.model, 40) : ""} · {f.steps} steps · seed {f.seedChoice === "new" ? "new" : f.seedChoice === "same" ? parentSeed : f.typedSeed || "?"}
        </span>
        <button className="h3-btn" onClick={closeRedo}>Cancel</button>
        <button className="h3-btn h3-primary" disabled={busy} onClick={() => void submit()}>
          <i className={busy ? "pi pi-spin pi-spinner" : "pi pi-refresh"} /> Queue {f.pass} redo
        </button>
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------
// sidecar details
// ---------------------------------------------------------------------------

export function SidecarDialog() {
  const sc = useApp((s) => s.sidecar);
  const ep = useApp((s) => s.ep);
  const d = useDetail(sc?.shot, sc?.pass);
  const derr = useDetailError(sc?.shot, sc?.pass);
  const t = useMemo(() => d?.takes.find((x) => x.take === sc?.take), [d, sc?.take]);
  if (!sc || !ep) return null;
  const json = t ? JSON.stringify(t.sidecar, null, 2) : "";
  return (
    <Dialog
      title={<>{sc.shot} {tn(sc.take)} <span className="h3-muted h3-small">{sc.pass}</span></>}
      onClose={closeSidecar}
      wide
      footer={
        <>
          {t && <button className="h3-btn" onClick={() => void copyText(json, "Sidecar")}>Copy JSON</button>}
          <button className="h3-btn" onClick={closeSidecar}>Close</button>
        </>
      }
    >
      {!d && !derr && <div className="h3-muted">Loading…</div>}
      {derr && !d && <div className="h3-note h3-note-err">{derr}</div>}
      {d && !t && <div className="h3-note h3-note-err">{sc.shot} has no take {sc.take} in {sc.pass}.</div>}
      {t && (
        <>
          <div className="h3-kv">
            <span>status</span><span>{t.status}{t.has_video ? "" : " (no video)"}</span>
            <span>stale</span><span>{t.stale.length ? t.stale.join(", ") : "no"}</span>
            {Object.entries(t.files).map(([k, v]) => [
              <span key={k + "k"}>{k}</span>,
              <span key={k + "v"} className="h3-mono">
                <button className="h3-link" title="Copy the absolute path" onClick={() => void copyText(absPath(ep, v!), "Path")}>{v}</button>
              </span>,
            ])}
          </div>
          {t.sidecar ? <pre className="h3-pre">{json}</pre> : <div className="h3-muted">No sidecar: this take is from before sidecars.</div>}
        </>
      )}
    </Dialog>
  );
}
