import { useEffect, useMemo, useRef, useState } from "react";
import {
  loadDetail, openRedo, openViewer, queueRender, baseRender, renderShots, revertOverride, saveOverride,
} from "../actions";
import { errText } from "../api";
import { host } from "../host";
import { cutTake, fmtSeconds, shortName, shotBadges, tn } from "../lib/format";
import { formFromDetail, isDirty, overrideFields, type OverrideForm } from "../lib/overrideForm";
import { useApp } from "../store";
import type { ShotDetail } from "../types";
import { DiffView, LoraEditor, ModelSelect } from "./Fields";
import { useDetail, useDetailError, useShotStatus } from "./hooks";
import { PassToggle } from "./ShotsTab";
import { Badges } from "./Thumb";

function Built({ d }: { d: ShotDetail }) {
  const b = d.built as Record<string, unknown>;
  const rows: [string, unknown][] = [
    ["seed", b.seed],
    ["steps", b.steps],
    ["model", b.model],
    ["lora", b.lora],
    ["length", b.length != null ? `${b.length} frames` : undefined],
    ["size", b.size],
    ["subjects", Array.isArray(b.subjects) ? (b.subjects as string[]).join(", ") : b.subjects],
    ["background", b.background],
    ["audio", b.audio_policy],
    ["panels", b.panels],
  ];
  return (
    <details>
      <summary>Built values (read-only)</summary>
      <div className="h3-col" style={{ marginTop: 4 }}>
        <div className="h3-kv">
          {rows.filter(([, v]) => v !== undefined && v !== "" && v !== null).map(([k, v]) => [
            <span key={k + "k"}>{k}</span>,
            <span key={k + "v"} className={k === "seed" ? "h3-mono" : ""} title={String(v)}>{k === "model" || k === "lora" ? shortName(String(v), 60) : String(v)}</span>,
          ])}
        </div>
        <div className="h3-h">Effective now</div>
        <div className="h3-kv">
          <span>seed</span><span className="h3-mono">{d.effective.seed} <span className="h3-muted">({d.effective.seed_source})</span></span>
          <span>model</span><span title={d.effective.model}>{shortName(d.effective.model, 60) || "(workflow's)"}</span>
          <span>LoRAs</span><span>{d.effective.loras == null ? "(workflow's)" : d.effective.loras.length ? d.effective.loras.map((l) => `${shortName(l.name, 40)} @${l.strength}`).join(", ") : "none"}</span>
          <span>steps</span><span>{d.effective.steps}</span>
        </div>
        <details>
          <summary>Built prompt</summary>
          <pre className="h3-pre">{d.built_prompt}</pre>
        </details>
        <details>
          <summary>Shotlist entry (JSON)</summary>
          <pre className="h3-pre">{JSON.stringify(d.built, null, 1)}</pre>
        </details>
      </div>
    </details>
  );
}

function OverrideEditor({ d, shot }: { d: ShotDetail; shot: string }) {
  const pass = useApp((s) => s.pass);
  const busy = useApp((s) => !!s.busy[`override|${shot}`]);
  const initial = useMemo(() => formFromDetail(d), [d]);
  const [form, setForm] = useState<OverrideForm>(initial);
  const [base, setBase] = useState<OverrideForm>(initial);
  const [showDiff, setShowDiff] = useState(false);
  const [both, setBoth] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const dirty = isDirty(form, base);
  const dirtyRef = useRef(dirty);
  dirtyRef.current = dirty;

  // a refetch (after save, or a live event) resets the form unless you're mid-edit
  useEffect(() => {
    if (!dirtyRef.current) {
      setForm(initial);
      setBase(initial);
    } else {
      setBase(initial);
    }
  }, [initial]);

  const set = (patch: Partial<OverrideForm>) => {
    setErr(null);
    setForm((f) => ({ ...f, ...patch }));
  };

  const save = async () => {
    let fields;
    try {
      fields = overrideFields(form, base, d);
    } catch (e) {
      setErr(errText(e));
      return;
    }
    if (!Object.keys(fields).length) return;
    // on success the detail is refetched and the effect above rebases the form on it
    if (await saveOverride(shot, pass, both, fields)) {
      host().toast("success", `${shot}: override saved`, Object.keys(fields).join(", ") + (both ? " (both passes)" : ` (${pass})`));
    }
  };

  const ov = d.override;
  const hasOverride = Object.keys(ov).length > 0;
  const eff = d.effective;
  const promptChanged = form.prompt !== d.built_prompt;

  return (
    <div className="h3-col">
      {d.override_stale && (
        <div className="h3-note">
          <b>Override stale.</b> It was written against an older build of this shot; the build now
          produces different text. It still applies. Check the diff, then keep it (Save re-bases it),
          or revert the prompt.
          <div className="h3-row" style={{ marginTop: 4 }}>
            <button className="h3-btn" onClick={() => setShowDiff(true)}>Show diff</button>
            <button className="h3-btn" disabled={busy} onClick={() => void saveOverride(shot, pass, false, { prompt: form.prompt })}>Keep (re-base)</button>
            <button className="h3-btn" disabled={busy} onClick={() => void saveOverride(shot, pass, false, { prompt: null })}>Use built prompt</button>
          </div>
        </div>
      )}
      <div className="h3-row">
        <span className="h3-h h3-grow">Prompt {ov.prompt != null ? <span className="h3-badge h3-b-override">override</span> : <span className="h3-muted">(built)</span>}</span>
        <label className="h3-check h3-small"><input type="checkbox" checked={showDiff} onChange={(e) => setShowDiff(e.target.checked)} /> diff</label>
        <button className="h3-btn" disabled={!promptChanged} title="Put the built prompt back in the box" onClick={() => set({ prompt: d.built_prompt })}>Built</button>
      </div>
      {showDiff ? (
        <DiffView oldText={d.built_prompt} newText={form.prompt} />
      ) : (
        <textarea className="h3-in" rows={12} value={form.prompt} onChange={(e) => set({ prompt: e.target.value })} spellCheck={false} />
      )}
      <div className="h3-field">
        <label>Seed</label>
        <div className="h3-row">
          <input
            className="h3-in h3-grow h3-mono"
            inputMode="numeric"
            placeholder={`${String(d.built.seed ?? eff.seed)} (built)`}
            value={form.seed}
            onChange={(e) => set({ seed: e.target.value.replace(/[^\d]/g, "") })}
            title="Pinned seed for this shot (both passes). Empty = the built seed; a redo still picks a new one unless you choose same/typed."
          />
          {form.seed && <button className="h3-btn h3-icon" title="Clear" onClick={() => set({ seed: "" })}>✕</button>}
        </div>
        <label>Model</label>
        <ModelSelect value={form.model} onChange={(model) => set({ model })} placeholder={`(built) ${ov.model == null ? shortName(eff.model, 40) : ""}`} />
        <label>LoRAs</label>
        <div className="h3-col" style={{ gap: 3 }}>
          <span className="h3-seg">
            <button className={form.lorasMode === "built" ? "h3-on" : ""} onClick={() => set({ lorasMode: "built" })}>built</button>
            <button
              className={form.lorasMode === "custom" ? "h3-on" : ""}
              onClick={() => set({
                lorasMode: "custom",
                loras: form.loras.length ? form.loras : (eff.loras ?? []).map((l) => ({ name: l.name, strength: String(l.strength) })),
              })}
            >
              custom
            </button>
          </span>
          {form.lorasMode === "custom" ? (
            <LoraEditor rows={form.loras} onChange={(loras) => set({ loras })} />
          ) : (
            <span className="h3-muted h3-small">{eff.loras?.length && ov.loras == null ? eff.loras.map((l) => `${shortName(l.name, 32)} @${l.strength}`).join(", ") : ov.loras == null ? "the workflow's LoRA" : ""}</span>
          )}
        </div>
        <label>Steps</label>
        <input className="h3-in" inputMode="numeric" placeholder={`${ov.steps == null ? eff.steps : ""} (built)`} value={form.steps} onChange={(e) => set({ steps: e.target.value.replace(/[^\d]/g, "") })} />
        <label>Note</label>
        <input className="h3-in" value={form.note} placeholder="why this override" onChange={(e) => set({ note: e.target.value })} />
      </div>
      <label className="h3-check" title="Prompt, model, LoRAs and steps are per pass; this writes them to final and proxy. Seed and note are always shared.">
        <input type="checkbox" checked={both} onChange={(e) => setBoth(e.target.checked)} /> Apply to both passes
      </label>
      {err && <div className="h3-note h3-note-err">{err}</div>}
      <div className="h3-row h3-wrap">
        <button className="h3-btn h3-primary" disabled={!dirty || busy} onClick={() => void save()}>
          {busy ? "Saving…" : "Save override"}
        </button>
        <button className="h3-btn" disabled={!dirty || busy} onClick={() => { setForm(base); setErr(null); }}>Discard edits</button>
        <span className="h3-grow" />
        <button
          className="h3-btn h3-danger"
          disabled={!hasOverride || busy}
          title={`Remove this shot's ${pass} override (prompt, model, LoRAs, steps). Seed and note stay.`}
          onClick={() => confirm(`Remove ${shot}'s ${pass} override?`) && void revertOverride(shot, pass)}
        >
          Revert {pass}
        </button>
        <button
          className="h3-btn h3-danger"
          disabled={!hasOverride || busy}
          title="Remove every override of this shot, both passes, seed and note included"
          onClick={() => confirm(`Remove all of ${shot}'s overrides (both passes, seed and note)?`) && void revertOverride(shot, null)}
        >
          Revert all
        </button>
      </div>
      {dirty && <div className="h3-small h3-muted">Unsaved edits. Render and Redo use the saved override; the Redo dialog can save these for you.</div>}
    </div>
  );
}

export function Inspector() {
  const ep = useApp((s) => s.ep);
  const shot = useApp((s) => s.shot);
  const pass = useApp((s) => s.pass);
  const d = useDetail(shot);
  const derr = useDetailError(shot);
  const s = useShotStatus(shot);
  const renderBusy = useApp((st) => !!shot && Object.keys(st.busy).some((k) => k.startsWith(`render|${pass}|`) && k.split("|")[2].split(",").includes(shot)));

  useEffect(() => {
    if (shot && ep) void loadDetail(shot);
  }, [shot, ep, pass]);

  const badges = useMemo(() => (s ? shotBadges(s) : []), [s]);

  if (!ep) return <div className="h3-surface"><div className="h3-empty-state">Pick an episode in the h3 Shots tab.</div></div>;
  if (!shot) {
    return (
      <div className="h3-surface">
        <div className="h3-bar"><span className="h3-title">Inspector</span><span className="h3-grow" /><PassToggle /></div>
        <div className="h3-empty-state">Select a shot in the Shots tab or the timeline.</div>
      </div>
    );
  }
  const ct = s ? cutTake(s) : undefined;
  const hasUsable = !!s?.takes.some((t) => t.status === "ok" && t.has_video);
  return (
    <div className="h3-surface">
      <div className="h3-bar">
        <span className="h3-title">{shot}</span>
        {s && <span className="h3-muted h3-small">{s.sequence} · {fmtSeconds(s.seconds)}{s.size ? ` · ${s.size}` : ""}</span>}
        <span className="h3-grow" />
        <PassToggle />
      </div>
      <div className="h3-scroll h3-pad h3-col">
        {s && (
          <div className="h3-col" style={{ gap: 4 }}>
            {s.subjects.length > 0 && <div className="h3-small h3-muted">subjects: {s.subjects.join(", ")}</div>}
            <Badges badges={badges} />
            <div className="h3-row h3-wrap">
              <button
                className="h3-btn h3-primary"
                disabled={renderBusy || !d}
                title={hasUsable ? "Queue a redo with the saved override and a new seed" : "Queue the first take"}
                onClick={() => {
                  if (!hasUsable) void renderShots([shot], false);
                  else if (ep) void queueRender({ ...baseRender(ep, pass, [shot]), redo: true, seed_mode: "new", parent_take: ct?.take ?? null });
                }}
              >
                <i className="pi pi-play" /> {hasUsable ? "Redo (new seed)" : "Render"}
              </button>
              <button className="h3-btn" disabled={!d} onClick={() => openRedo(shot, ct?.take ?? s.takes[s.takes.length - 1]?.take ?? null)}>
                <i className="pi pi-refresh" /> Redo…
              </button>
              <button className="h3-btn" disabled={!ct?.mp4} onClick={() => ct && openViewer(shot, ct.take, null, "single")}>
                <i className="pi pi-eye" /> View {ct ? tn(ct.take) : ""}
              </button>
            </div>
          </div>
        )}
        {derr && !d && (
          <div className="h3-note h3-note-err">
            {derr} <button className="h3-link" onClick={() => void loadDetail(shot, pass, true)}>Retry</button>
          </div>
        )}
        {!d && !derr && <div className="h3-muted">Loading…</div>}
        {d && (
          <>
            <Built d={d} />
            <div className="h3-sep" />
            <OverrideEditor key={`${ep}|${pass}|${shot}`} d={d} shot={shot} />
          </>
        )}
      </div>
    </div>
  );
}
