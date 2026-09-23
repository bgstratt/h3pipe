import { useEffect, useMemo, useState, type ReactNode } from "react";
import { closeRedo, closeSidecar, copyText, loadDetail, runRedo, takePrompt, type RedoSeed } from "../actions";
import { errText, parseSeed } from "../api";
import { absPath, shortName, tn } from "../lib/format";
import { missingOf } from "../lib/missingRefs";
import { loraRow, parseLoras, parseSteps, type LoraRow } from "../lib/overrideForm";
import { findTarget, isModelMismatch, isRetargeted, MODEL_MISMATCH_LABEL, modelWarning, runSize, shotTarget, targetLabel } from "../lib/targets";
import { useApp } from "../store";
import type { Pass, ShotDetail, TakeDetail, TargetList } from "../types";
import { DiffView, LoraEditor, ModelSelect } from "./Fields";
import { useDetail, useDetailError, useShotStatus } from "./hooks";
import { MissingRefsNote } from "./MissingRefs";
import { ResolvedNotes, TargetReadinessNote } from "./Readiness";
import { TargetSelect, useTargetPickers, useTargets } from "./Targets";

export function Dialog({ title, onClose, children, footer, wide }: { title: ReactNode; onClose: () => void; children: ReactNode; footer?: ReactNode; wide?: boolean }) {
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
  /** Phase 8: the target for this run; "" = the shot's own */
  target: string;
  /** keep this take's frames as a PNG sequence, for retouching a frame or two */
  frames: boolean;
}

/** The settings a run on another target starts from: that target's preset for the pass. */
function presetForm(list: TargetList | null, target: string, pass: Pass): Pick<RedoForm, "model" | "loras" | "lorasOn" | "steps"> {
  const p = findTarget(list, target)?.presets?.[pass] ?? findTarget(list, target)?.presets?.final;
  const loras = p?.loras ?? (p?.lora ? [{ name: p.lora, strength: 1 }] : null);
  return {
    model: p?.model ?? "",
    loras: (loras ?? []).map(loraRow),
    lorasOn: loras != null,
    steps: p?.steps != null ? String(p.steps) : "",
  };
}

/** `current`: the shot's own target. A parent take rendered on another target
 * lends its seed, not its model/LoRAs/steps (they belong to that model). */
function initForm(d: ShotDetail, parent: TakeDetail | undefined, pass: Pass, current: string | null = null): RedoForm {
  const sc = parent?.sidecar ?? null;
  const scTarget = typeof sc?.target === "string" ? sc.target : null;
  const settings = sc && (!scTarget || !current || scTarget === current) ? sc : null;
  const loras = settings?.loras !== undefined ? settings.loras : d.effective.loras;
  return {
    target: "",
    parent: parent?.take ?? null,
    seedChoice: "new",
    typedSeed: sc?.seed ?? d.effective.seed ?? "",
    model: settings?.model ?? d.effective.model ?? "",
    loras: (loras ?? []).map(loraRow),
    lorasOn: loras != null,
    steps: String(settings?.steps ?? d.effective.steps ?? ""),
    pass,
    note: "",
    source: "current",
    prompt: d.effective.prompt,
    save: true,
    frames: false,
  };
}

export function RedoDialog() {
  const r = useApp((s) => s.redo);
  const ep = useApp((s) => s.ep);
  const d = useDetail(r?.shot, r?.pass);
  const derr = useDetailError(r?.shot, r?.pass);
  if (!r || !ep) return null;
  return (
    <Dialog title={<>New take of {r.shot}{r.parent != null ? ` (like ${tn(r.parent)})` : ""}</>} onClose={closeRedo} wide>
      {!d && !derr && <div className="h3-muted">Loading {r.shot}…</div>}
      {derr && !d && <div className="h3-note h3-note-err">{derr}</div>}
      {d && <RedoBody key={`${r.shot}|${r.pass}|${r.parent}`} d={d} shot={r.shot} openPass={r.pass} parent={r.parent} />}
    </Dialog>
  );
}

function RedoBody({ d, shot, openPass, parent }: { d: ShotDetail; shot: string; openPass: Pass; parent: number | null }) {
  const parentTake = d.takes.find((t) => t.take === parent);
  const { list, video, seriesDefault } = useTargets();
  // the shot's own target, and the one this run uses ("" in the form = the shot's own)
  const current = list || d.target || d.built_target ? shotTarget(d, seriesDefault) : null;
  const [f, setF] = useState<RedoForm>(() => initForm(d, parentTake, openPass, current));
  const runTarget = f.target || current || "";
  const oneOff = !!f.target && f.target !== current;
  const pickers = useTargetPickers(runTarget || null);
  // the prompt is the target's when the shot is retargeted or this run is on another target
  const lockPrompt = oneOff || isRetargeted(d);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [parentPrompt, setParentPrompt] = useState<{ take: number; text: string | null; error?: string } | null>(null);
  const [showDiff, setShowDiff] = useState(false);
  const [allowMissing, setAllowMissing] = useState(false);
  const [allowMismatch, setAllowMismatch] = useState(false);
  const targetStatus = useShotStatus(shot, f.pass);
  const missing = targetStatus ? missingOf(targetStatus) : [];
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
    const init = initForm(d, t, f.pass, current);
    set({
      parent: n, typedSeed: init.typedSeed,
      // on another target, keep that target's settings
      ...(oneOff ? {} : { model: init.model, loras: init.loras, lorasOn: init.lorasOn, steps: init.steps }),
      ...(f.source === "parent" ? { prompt: "" } : {}),
    });
    setParentPrompt(null);
  };

  const chooseTarget = (id: string) => {
    if (!id || id === current) {
      // back to the shot's own target: its current settings
      const init = initForm(d, pt, f.pass, current);
      set({ target: "", model: init.model, loras: init.loras, lorasOn: init.lorasOn, steps: init.steps, prompt: (targetD ?? d).effective.prompt, source: "current" });
    } else {
      set({ target: id, ...presetForm(list, id, f.pass), source: "current", save: false });
    }
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
      loras = f.lorasOn && pickers.loras !== null ? parseLoras(f.loras) : null;
      if (!lockPrompt && !f.prompt.trim()) throw new Error("The prompt is empty.");
    } catch (e) {
      setErr(errText(e));
      return;
    }
    setBusy(true);
    try {
      const ok = await runRedo({
        shot, pass: f.pass, parent: f.parent, seed, model: pickers.models === null ? "" : f.model, loras, steps, prompt: f.prompt, note: f.note.trim(),
        saveAsOverride: f.save && !oneOff, allowMissingRefs: allowMissing,
        allowModelMismatch: mismatch && allowMismatch,
        target: oneOff ? f.target : null, lockPrompt, keepFrames: f.frames,
      });
      if (ok) closeRedo();
    } finally {
      setBusy(false);
    }
  };

  const builtPrompt = targetD?.built_prompt ?? d.built_prompt;
  // the model this run loads is another family than the target needs: the server skips it unless allowed
  const runModel = f.model || (oneOff ? "" : (targetD ?? d).effective.model);
  const mismatch = isModelMismatch(pickers.modelFiles, runModel);
  const size = runSize(targetD, runTarget, seriesDefault, list);
  const runLabel = targetLabel(list, runTarget);
  return (
    <>
      <div className="h3-field" style={{ gridTemplateColumns: "76px minmax(0,1fr)" }}>
        {list && current && (
          <>
            <label title="Target for this run">Target</label>
            <div className="h3-col" style={{ gap: 2 }}>
              <TargetSelect
                value={runTarget}
                list={list}
                video={video}
                title="Target for this run only (sent with the render). The shot's own target is changed in the inspector."
                onChange={chooseTarget}
              />
              <span className="h3-small h3-muted">
                for this run{oneOff ? ` (the shot stays on ${targetLabel(list, current)})` : ""} · {size.text}
              </span>
              <TargetReadinessNote id={runTarget} list={list} />
            </div>
          </>
        )}
        <label>From take</label>
        <select className="h3-in" value={f.parent ?? ""} onChange={(e) => chooseParent(e.target.value === "" ? null : Number(e.target.value))}>
          <option value="">(none: the shot's current settings)</option>
          {d.takes.map((t) => (
            <option key={t.take} value={t.take}>
              {tn(t.take)} · {t.status}{t.sidecar?.seed ? ` · seed ${t.sidecar.seed}` : " · no sidecar"}{typeof t.sidecar?.target === "string" && current && t.sidecar.target !== current ? ` · on ${targetLabel(list, t.sidecar.target)}` : ""}
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
        {pickers.models === null ? (
          <span className="h3-muted h3-small">set by the target (it has no model widget)</span>
        ) : (
          <ModelSelect value={f.model} onChange={(model) => set({ model })} placeholder="(the workflow's model)" choices={pickers.models} grouped={pickers.modelFiles} />
        )}
        {pickers.loras !== null && (
          <>
            <label>LoRAs</label>
            <div className="h3-col" style={{ gap: 3 }}>
              <label className="h3-check h3-small">
                <input type="checkbox" checked={f.lorasOn} onChange={(e) => set({ lorasOn: e.target.checked })} />
                set LoRAs {f.lorasOn ? "" : "(off: the workflow's own LoRA)"}
              </label>
              {f.lorasOn && <LoraEditor rows={f.loras} onChange={(loras) => set({ loras })} choices={pickers.loras} />}
            </div>
          </>
        )}
        <label>Steps</label>
        <input className="h3-in" style={{ width: 80 }} inputMode="numeric" value={f.steps} onChange={(e) => set({ steps: e.target.value.replace(/[^\d]/g, "") })} />
        <label>Pass</label>
        <span className="h3-seg">
          {(["proxy", "final"] as Pass[]).map((p) => (
            <button key={p} className={f.pass === p ? "h3-on" : ""} onClick={() => set({ pass: p, ...(oneOff ? presetForm(list, f.target, p) : {}) })}>{p}</button>
          ))}
        </span>
        <label>Note</label>
        <input className="h3-in" placeholder="what this take tries (kept in its sidecar)" value={f.note} onChange={(e) => set({ note: e.target.value })} />
        <label>Prompt</label>
        {lockPrompt ? (
          <div className="h3-col" style={{ gap: 3 }}>
            <div className="h3-note h3-note-info h3-small">
              {oneOff
                ? <>{runLabel} writes its own prompt for this shot when the run is queued; a prompt written for {targetLabel(list, current)} doesn't apply.</>
                : <>This shot is retargeted to {runLabel}: per-pass prompt overrides are ignored, and this is the prompt {runLabel} writes for it (read-only).</>}
            </div>
            {!oneOff && <pre className="h3-pre" style={{ maxHeight: 220 }}>{(targetD ?? d).effective.prompt}</pre>}
          </div>
        ) : (
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
        )}
      </div>
      <label
        className="h3-check"
        title={oneOff
          ? `A run on another target isn't saved: the override belongs to ${targetLabel(list, current)}. To keep ${runLabel}, change the shot's target in the inspector.`
          : "Writes the prompt, model, LoRAs, steps (and a typed/same seed) to overrides.json first, so later renders keep them"}
      >
        <input type="checkbox" disabled={oneOff} checked={f.save && !oneOff} onChange={(e) => set({ save: e.target.checked })} />
        Save these as the shot's {f.pass} override{oneOff ? " (not for a one-off target)" : ""}
      </label>
      <label
        className="h3-check"
        title={"Also writes this take's frames as PNGs in its frames/ folder, so a shot that is "
          + "right but for a frame or two can be retouched and re-encoded. Costs a couple of "
          + "seconds and a few hundred MB a take."}
      >
        <input type="checkbox" checked={f.frames} onChange={(e) => set({ frames: e.target.checked })} />
        Keep the frames (PNG sequence)
      </label>
      <MissingRefsNote blocked={missing.length ? [{ shot, refs: missing }] : []} allow={allowMissing} setAllow={setAllowMissing} />
      {mismatch && (
        <label className="h3-check" title={modelWarning(pickers.modelFiles, runModel) ?? ""}>
          <input type="checkbox" checked={allowMismatch} onChange={(e) => setAllowMismatch(e.target.checked)} />
          {MODEL_MISMATCH_LABEL}
        </label>
      )}
      {err && <div className="h3-note h3-note-err">{err}</div>}
      <div className="h3-row" style={{ justifyContent: "flex-end" }}>
        <span className="h3-muted h3-small h3-grow">
          {runLabel ? `${runLabel} · ` : ""}{f.model ? shortName(f.model, 40) : ""} · {f.steps} steps · seed {f.seedChoice === "new" ? "new" : f.seedChoice === "same" ? parentSeed : f.typedSeed || "?"}
        </span>
        <button className="h3-btn" onClick={closeRedo}>Cancel</button>
        <button className="h3-btn h3-primary" disabled={busy} onClick={() => void submit()}>
          <i className={busy ? "pi pi-spin pi-spinner" : "pi pi-refresh"} /> Queue new {f.pass} take
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
  const targets = useApp((s) => s.targets);
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
          {t.sidecar?.resolved && (
            <ResolvedNotes resolved={t.sidecar.resolved} target={typeof t.sidecar.target === "string" ? t.sidecar.target : d?.target} list={targets} />
          )}
          {t.sidecar ? <pre className="h3-pre">{json}</pre> : <div className="h3-muted">No sidecar: this take is from before sidecars.</div>}
        </>
      )}
    </Dialog>
  );
}
