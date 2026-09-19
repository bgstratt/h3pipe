// The Refs sidebar tab (Phase 5): every ref in the bible, grouped, with its live
// file, its candidates (takes), pick / generate / import / compare, and the
// ref's prompt and settings override.

import { memo, useEffect, useMemo, useRef, useState, type MouseEvent } from "react";
import {
  copyText, generateRef, loadRefs, openBrowse, openImageCompare, pickRef, revertRefOverride, saveRefOverride,
  selectRefTake, setRefsFilter, toggleRefOpen,
} from "../actions";
import { errText } from "../api";
import { api } from "../host";
import { shortName, tn } from "../lib/format";
import { formFromDetail, isDirty, overrideFields, type OverrideForm, type OverrideSource } from "../lib/overrideForm";
import {
  VIEWS, blockedShots, canGenerate, groupRefs, isAudioRef, pickedTake, refCounts, takesOf, unpickedViews, usedBy,
  viewLabel, viewOf, type RefFilter,
} from "../lib/refs";
import { store, useApp } from "../store";
import type { Ref, RefTake, SeedMode } from "../types";
import { useStatus } from "./hooks";
import { OverrideFields } from "./OverrideFields";
import { PassToggle } from "./ShotsTab";
import { Progress, statusClass } from "./Thumb";

const FILTERS: { id: RefFilter; label: string; title: string }[] = [
  { id: "episode", label: "this episode", title: "Refs used by this episode's shots (this pass)" },
  { id: "all", label: "all", title: "Everything in the bible, used or not" },
  { id: "missing", label: "missing", title: "Refs whose file isn't on disk" },
];

export const KEYFRAMES_EMPTY = "FL2V keyframes: generated when an FL2V model is set up";
const NO_FILE = "series.json names no file for this (e.g. a voice-only character has no sheet)";

/** A ref's live file (the one renders read), or a "missing" placeholder. */
function LiveThumb({ ep, r, size }: { ep: string; r: Ref; size: number }) {
  if (!r.path) {
    // the bible names no file for it (a voice-only character has no sheet)
    return (
      <div className="h3-refthumb h3-thumb h3-empty" style={{ width: size, height: size }} title={NO_FILE}>
        <span className="h3-thumb-label">no file</span>
      </div>
    );
  }
  if (isAudioRef(r)) {
    return (
      <div className={`h3-refthumb h3-thumb${r.exists ? "" : " h3-empty h3-missing"}`} style={{ width: size, height: size }} title={r.path}>
        <i className="pi pi-volume-up" style={{ fontSize: size / 2.6 }} />
        {!r.exists && <span className="h3-thumb-label">missing</span>}
      </div>
    );
  }
  if (!r.exists) {
    return (
      <div className="h3-refthumb h3-thumb h3-empty h3-missing" style={{ width: size, height: size }} title={`${r.path} isn't on disk`}>
        <span>missing</span>
      </div>
    );
  }
  return (
    <div
      className="h3-refthumb h3-thumb"
      style={{ width: size, height: size, backgroundImage: `url("${api().refFileUrl(ep, r.path, r.sha1)}")`, backgroundSize: "contain" }}
      title={r.path}
    />
  );
}

/** Progress of a queued candidate, if ComfyUI is running it now. */
function useCandidateProgress(ref: string, view: string | null, take: number): { value: number; max: number } | undefined {
  return useApp((s) => {
    const pid = s.running;
    if (!pid) return undefined;
    const r = s.refPrompts[pid];
    if (!r || r.ref !== ref || (r.view ?? null) !== view || r.take !== take) return undefined;
    return s.progress[pid];
  });
}

const Candidate = memo(function Candidate({ ep, r, view, t, live, selected }: {
  ep: string; r: Ref; view: string | null; t: RefTake; live: boolean; selected: boolean;
}) {
  const prog = useCandidateProgress(r.id, view, t.take);
  const url = t.image ? api().refFileUrl(ep, t.image) : null;
  const click = (e: MouseEvent) => {
    // read at click time, so candidates don't all re-render on every selection
    const sel = store.get().refSel;
    if ((e.ctrlKey || e.metaKey) && sel && sel.ref === r.id && sel.view === view && sel.take !== t.take) {
      openImageCompare(r.id, view, sel.take, t.take);
      return;
    }
    selectRefTake(r.id, view, selected ? null : t.take);
  };
  const title = [
    `${tn(t.take)} · ${t.status}${live ? " · live" : ""} · ${t.source}`,
    t.seed ? `seed ${t.seed}` : "",
    t.note ? `“${t.note}”` : "",
    t.save_notes && t.status === "failed" ? t.save_notes : "",
    "click: select · ctrl/⌘-click: compare with the selected · double-click: open in the viewer",
  ].filter(Boolean).join("\n");
  if (isAudioRef(r)) {
    return (
      <div className={`h3-audio-take${selected ? " h3-sel" : ""}${live ? " h3-live" : ""}`} onClick={click} title={title}>
        <span className="h3-row">
          <span className={`h3-dot ${statusClass(t.status)}`} />
          <b>{tn(t.take)}</b>
          {live && <span className="h3-badge h3-b-cut">live</span>}
          <span className="h3-muted h3-small">{t.source}</span>
        </span>
        {url && t.status === "ok" && <audio controls preload="none" src={url} onClick={(e) => e.stopPropagation()} />}
      </div>
    );
  }
  return (
    <div
      className={`h3-cand${selected ? " h3-sel" : ""}${live ? " h3-live" : ""}`}
      onClick={click}
      onDoubleClick={() => openImageCompare(r.id, view, t.take, live ? null : pickedOf(r, view))}
      title={title}
    >
      <div
        className={`h3-cand-img h3-thumb${url && t.status === "ok" ? "" : " h3-empty"}`}
        style={url && t.status === "ok" ? { backgroundImage: `url("${url}")` } : undefined}
      >
        {!(url && t.status === "ok") && <span className={t.status === "failed" ? "h3-err" : ""}>{t.status}</span>}
        <span className="h3-thumb-label">{tn(t.take)}{t.source === "imported" ? " ⤓" : ""}</span>
        {live && <span className="h3-cand-live">live</span>}
      </div>
      {prog && <Progress value={prog.value} max={prog.max} />}
    </div>
  );
});

function pickedOf(r: Ref, view: string | null): number | null {
  return (view ? viewOf(r, view)?.picked : r.picked) ?? null;
}


function CandidateGrid({ ep, r, view, cols }: { ep: string; r: Ref; view: string | null; cols?: boolean }) {
  const sel = useApp((s) => s.refSel);
  const takes = takesOf(r, view);
  const picked = pickedOf(r, view);
  return (
    <div className={cols ? "h3-cand-col" : isAudioRef(r) ? "h3-col" : "h3-cand-grid"}>
      {[...takes].reverse().map((t) => (
        <Candidate
          key={t.take}
          ep={ep}
          r={r}
          view={view}
          t={t}
          live={picked === t.take}
          selected={!!sel && sel.ref === r.id && sel.view === view && sel.take === t.take}
        />
      ))}
      {!takes.length && <div className="h3-muted h3-small h3-cand-none">no candidates</div>}
    </div>
  );
}

/** The selected candidate's actions: pick, compare, details. */
function Selection({ r }: { r: Ref }) {
  const sel = useApp((s) => (s.refSel && s.refSel.ref === r.id ? s.refSel : null));
  const busy = useApp((s) => !!s.busy[`refpick|${r.id}`]);
  if (!sel) return null;
  const t = takesOf(r, sel.view).find((x) => x.take === sel.take);
  if (!t) return null;
  const picked = pickedOf(r, sel.view);
  const live = picked === t.take;
  const usable = t.status === "ok" && !!t.image;
  return (
    <div className="h3-ref-sel">
      <div className="h3-row h3-wrap">
        <b>{tn(t.take)}</b>
        {sel.view && <span className="h3-muted">{viewLabel(sel.view)}</span>}
        <span className="h3-muted h3-small">{t.status} · {t.source}{t.seed ? ` · seed ${t.seed}` : ""}</span>
        <span className="h3-grow" />
        <button className="h3-btn h3-primary" disabled={!usable || live || busy} title={live ? "Already the live file" : `Copy ${tn(t.take)} to ${r.path}${sel.view ? " (and stitch the sheet once all four views are picked)" : ""}`} onClick={() => void pickRef(r.id, sel.view, t.take)}>
          <i className="pi pi-check" /> {live ? "Live" : "Pick"}
        </button>
        {!isAudioRef(r) && (
          <button
            className="h3-btn"
            disabled={!usable}
            title={picked != null && !live ? `Open ${tn(t.take)} (A) against the live ${tn(picked)} (B)` : "Open in the viewer"}
            onClick={() => openImageCompare(r.id, sel.view, t.take, picked != null && !live ? picked : null)}
          >
            <i className="pi pi-clone" /> {picked != null && !live ? "Compare with live" : "View"}
          </button>
        )}
      </div>
      {(t.prompt || t.model || t.note || (t.status === "failed" && t.save_notes)) && (
        <div className="h3-kv">
          {t.note && <><span>note</span><span>{t.note}</span></>}
          {t.model && <><span>model</span><span title={t.model}>{shortName(t.model, 40)}</span></>}
          {t.steps != null && <><span>steps</span><span>{t.steps}</span></>}
          {t.status === "failed" && t.save_notes && <><span>error</span><span className="h3-err">{t.save_notes}</span></>}
          {t.prompt && <><span>prompt</span><span className="h3-small" title={t.prompt}>{t.prompt.length > 180 ? t.prompt.slice(0, 179) + "…" : t.prompt}</span></>}
        </div>
      )}
      <div className="h3-muted h3-small">Ctrl/⌘-click another candidate to compare the two.</div>
    </div>
  );
}

function GenerateBar({ r }: { r: Ref }) {
  const busy = useApp((s) => !!s.busy[`refgen|${r.id}`] || !!s.busy[`refimport|${r.id}`]);
  const isChar = !!r.views;
  const [view, setView] = useState<string>(isChar ? "" : "");
  const [count, setCount] = useState(1);
  const [seedMode, setSeedMode] = useState<SeedMode>("auto");
  const gen = canGenerate(r);
  const importView = isChar ? view || null : null;
  return (
    <div className="h3-row h3-wrap h3-genbar">
      {isChar && (
        <select className="h3-in" value={view} onChange={(e) => setView(e.target.value)} title="Which view to generate or import into">
          <option value="">all four views</option>
          {VIEWS.map((v) => <option key={v.view} value={v.view}>{v.label}</option>)}
        </select>
      )}
      {gen && (
        <>
          <select className="h3-in" value={count} onChange={(e) => setCount(Number(e.target.value))} title="How many candidates (each with its own seed)">
            {[1, 2, 3, 4].map((n) => <option key={n} value={n}>{n}×</option>)}
          </select>
          <span className="h3-seg" title="Seed: auto (the ref's pinned or stable seed for a first candidate, new after), new, or same as the last">
            {(["auto", "new", "same"] as SeedMode[]).map((m) => (
              <button key={m} className={seedMode === m ? "h3-on" : ""} onClick={() => setSeedMode(m)}>{m}</button>
            ))}
          </span>
          <button
            className="h3-btn h3-primary"
            disabled={busy}
            title={isChar && !view ? "Queue all four views, sharing one seed per candidate" : "Queue new candidates"}
            onClick={() => void generateRef({
              ref: r.id, view: isChar ? view || null : null, count, seed_mode: seedMode, seed: null,
              prompt: null, model: null, loras: null, steps: null, note: "",
            })}
          >
            <i className={busy ? "pi pi-spin pi-spinner" : "pi pi-sparkles"} /> Generate {count > 1 ? `${count} more` : "1 more"}
          </button>
        </>
      )}
      <button
        className="h3-btn"
        disabled={busy || (isChar && !view)}
        title={isChar && !view ? "Choose a view to import into" : `Import ${isAudioRef(r) ? "an audio file" : "an image"} from the ComfyUI machine as a new candidate`}
        onClick={() => openBrowse({ purpose: "import", ref: r.id, view: importView, files: isAudioRef(r) ? "audio" : "image" })}
      >
        <i className="pi pi-download" /> Import…
      </button>
    </div>
  );
}

/** The ref's prompt/seed/model/LoRA/steps override, with the inspector's form. */
function RefOverrideEditor({ r }: { r: Ref }) {
  const busy = useApp((s) => !!s.busy[`refoverride|${r.id}`]);
  const src: OverrideSource = useMemo(() => ({
    override: r.override_values ?? {},
    effective: { prompt: r.effective?.prompt ?? r.prompt ?? "" },
    built_prompt: r.built_prompt ?? (r.override.fields.includes("prompt") ? "" : r.prompt ?? ""),
  }), [r]);
  const initial = useMemo(() => formFromDetail(src), [src]);
  const [form, setForm] = useState<OverrideForm>(initial);
  const [base, setBase] = useState<OverrideForm>(initial);
  const [showDiff, setShowDiff] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const dirty = isDirty(form, base);
  const dirtyRef = useRef(dirty);
  dirtyRef.current = dirty;
  useEffect(() => {
    if (!dirtyRef.current) setForm(initial);
    setBase(initial);
  }, [initial]);
  const set = (p: Partial<OverrideForm>) => {
    setErr(null);
    setForm((f) => ({ ...f, ...p }));
  };
  const save = async () => {
    let fields;
    try {
      fields = overrideFields(form, base, src);
    } catch (e) {
      setErr(errText(e));
      return;
    }
    await saveRefOverride(r.id, fields);
  };
  const eff = r.effective;
  const ov = r.override_values ?? {};
  const has = r.override.fields.length > 0;
  return (
    <div className="h3-col">
      {r.override.stale && <div className="h3-note"><b>Override stale.</b> The bible's text for this ref changed since the override was written.</div>}
      {!r.override_values && has && (
        <div className="h3-note h3-note-info h3-small">This server doesn't send the override's values (only that {r.override.fields.join(", ")} are set); the form shows what a generate uses now.</div>
      )}
      <OverrideFields
        form={form}
        set={set}
        builtPrompt={src.built_prompt}
        builtLabel="bible"
        promptOverridden={r.override.fields.includes("prompt")}
        showDiff={showDiff}
        setShowDiff={setShowDiff}
        rows={7}
        seedPlaceholder={`${eff?.seed ?? ""} (bible)`}
        modelPlaceholder={`(bible) ${ov.model == null && eff ? shortName(eff.model, 40) : ""}`}
        stepsPlaceholder={`${ov.steps == null && eff ? eff.steps : ""} (bible)`}
        effLoras={eff?.loras ?? null}
        lorasOverridden={ov.loras != null || r.override.fields.includes("loras")}
      />
      {err && <div className="h3-note h3-note-err">{err}</div>}
      <div className="h3-row h3-wrap">
        <button className="h3-btn h3-primary" disabled={!dirty || busy} onClick={() => void save()}>{busy ? "Saving…" : "Save"}</button>
        <button className="h3-btn" disabled={!dirty || busy} onClick={() => setForm(base)}>Discard</button>
        <span className="h3-grow" />
        <button className="h3-btn h3-danger" disabled={!has || busy} onClick={() => confirm(`Put ${r.name} back to the bible's settings?`) && void revertRefOverride(r.id)}>Revert</button>
      </div>
      <div className="h3-muted h3-small">Used by the next Generate. Candidates already made keep their settings.</div>
    </div>
  );
}

function RefDetail({ ep, r }: { ep: string; r: Ref }) {
  const pass = useApp((s) => s.pass);
  const st = useStatus();
  const blocked = blockedShots(r, st, pass);
  const missingViews = unpickedViews(r);
  const used = usedBy(r, pass);
  return (
    <div className="h3-ref-detail">
      <div className="h3-row h3-small">
        <span className="h3-mono h3-ell h3-grow" title={r.path ?? NO_FILE}>{r.path ?? NO_FILE}</span>
        {r.path && <button className="h3-link" onClick={() => void copyText(r.path!, "Path")}>copy</button>}
      </div>
      {blocked.length > 0 && (
        <div className="h3-small h3-err">Blocks {blocked.length} shot{blocked.length > 1 ? "s" : ""}: {blocked.slice(0, 12).join(", ")}{blocked.length > 12 ? "…" : ""}</div>
      )}
      {used.length > 0 && !blocked.length && <div className="h3-small h3-muted">Used by {used.slice(0, 12).join(", ")}{used.length > 12 ? "…" : ""}</div>}
      {r.views ? (
        <>
          {missingViews.length > 0 && missingViews.length < 4 && (
            <div className="h3-small h3-muted">The sheet is stitched when every view has a pick: {missingViews.map(viewLabel).join(", ")} still to pick.</div>
          )}
          <div className="h3-views">
            {VIEWS.map((v) => {
              const rv = viewOf(r, v.view);
              const live = rv ? pickedTake(rv) : undefined;
              return (
                <div key={v.view} className="h3-view-col">
                  <div className="h3-view-head" title={v.view}>
                    {v.label}
                    <span className={live ? "h3-muted" : "h3-err"}>{live ? ` ${tn(live.take)}` : " —"}</span>
                  </div>
                  <CandidateGrid ep={ep} r={r} view={v.view} cols />
                </div>
              );
            })}
          </div>
        </>
      ) : (
        <CandidateGrid ep={ep} r={r} view={null} />
      )}
      <Selection r={r} />
      {r.can_generate === false && r.why_not && (
        <div className="h3-small h3-muted">Can't generate: {r.why_not}. Import a file instead.</div>
      )}
      <GenerateBar r={r} />
      {canGenerate(r) && (
        <details className="h3-ref-settings">
          <summary>Prompt and settings{r.override.fields.length ? ` (override: ${r.override.fields.join(", ")})` : ""}</summary>
          <RefOverrideEditor r={r} />
        </details>
      )}
    </div>
  );
}

function RefRow({ ep, r }: { ep: string; r: Ref }) {
  const open = useApp((s) => !!s.refOpen[r.id]);
  const pass = useApp((s) => s.pass);
  const st = useStatus();
  const blocked = blockedShots(r, st, pass);
  const used = usedBy(r, pass);
  const all = r.views ? r.views.flatMap((v) => v.takes) : r.takes;
  const queued = all.filter((t) => t.status === "queued").length;
  const live = r.views ? null : r.picked;
  const picks = r.views ? r.views.filter((v) => v.picked != null).length : null;
  return (
    <div className={`h3-ref${open ? " h3-open" : ""}`}>
      <div className="h3-ref-row" onClick={() => toggleRefOpen(r.id)}>
        <span className="h3-chev">{open ? "▼" : "▶"}</span>
        <LiveThumb ep={ep} r={r} size={40} />
        <div className="h3-col h3-grow" style={{ gap: 1 }}>
          <div className="h3-row">
            <b className="h3-ell">{r.name}</b>
            <span className="h3-muted h3-small h3-ell">{r.id}</span>
          </div>
          <div className="h3-row h3-small h3-muted">
            <span title={used.join(", ")}>used by {used.length} shot{used.length === 1 ? "" : "s"}</span>
            <span>· {all.length} cand.</span>
            {picks != null && <span>· {picks}/4 views</span>}
            {live != null && <span>· live {tn(live)}</span>}
          </div>
          <span className="h3-badges">
            {!r.exists && <span className="h3-badge h3-b-failed" title={`${r.path} isn't on disk`}>missing</span>}
            {blocked.length > 0 && <span className="h3-badge h3-b-missing-refs" title={`Renders of these shots are skipped until this ref exists:\n${blocked.join(", ")}`}>blocks {blocked.length}</span>}
            {queued > 0 && <span className="h3-badge h3-b-queued">queued ×{queued}</span>}
            {r.override.fields.length > 0 && <span className="h3-badge h3-b-override" title={`Override: ${r.override.fields.join(", ")}`}>override</span>}
            {r.override.stale && <span className="h3-badge h3-b-override-stale">override stale</span>}
          </span>
        </div>
      </div>
      {open && <RefDetail ep={ep} r={r} />}
    </div>
  );
}

export function RefsTab() {
  const ep = useApp((s) => s.ep);
  const pass = useApp((s) => s.pass);
  const refs = useApp((s) => (s.ep ? s.refs[s.ep] : undefined));
  const err = useApp((s) => (s.ep ? s.refsError[s.ep] : undefined));
  const loading = useApp((s) => (s.ep ? !!s.refsLoading[s.ep] : false));
  const filter = useApp((s) => s.refsFilter);
  const st = useStatus();
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});

  useEffect(() => {
    if (ep) void loadRefs(ep);
  }, [ep]);

  const groups = useMemo(() => groupRefs(refs ?? [], filter, pass), [refs, filter, pass]);
  const counts = useMemo(() => refCounts(refs ?? [], st, pass), [refs, st, pass]);

  return (
    <div className="h3-surface">
      <div className="h3-bar">
        <span className="h3-title">Refs</span>
        {refs && <span className="h3-muted h3-small">{refs.length} in the bible</span>}
        <span className="h3-grow" />
        {loading && <i className="pi pi-spin pi-spinner h3-muted" />}
        <PassToggle />
        <button className="h3-btn h3-icon" title="Refresh" disabled={!ep} onClick={() => void loadRefs()}><i className="pi pi-refresh" /></button>
      </div>
      {!ep && <div className="h3-empty-state">Pick an episode in the h3 Shots tab.</div>}
      {ep && (
        <>
          <div className="h3-pad h3-col" style={{ gap: 4 }}>
            <span className="h3-seg" title="Which refs to list">
              {FILTERS.map((f) => (
                <button key={f.id} className={filter === f.id ? "h3-on" : ""} title={f.title} onClick={() => setRefsFilter(f.id)}>{f.label}</button>
              ))}
            </span>
            {counts.missing > 0 && (
              <div className="h3-note">
                <b>{counts.missing} ref{counts.missing > 1 ? "s" : ""} missing</b>
                {counts.shots > 0 ? `, blocking ${counts.shots} shot${counts.shots > 1 ? "s" : ""} of this episode (${pass})` : ", none used by this episode"}.
              </div>
            )}
            {err && <div className="h3-note h3-note-err">{err} <button className="h3-link" onClick={() => void loadRefs()}>Retry</button></div>}
          </div>
          <div className="h3-scroll h3-sep">
            {!refs && !err && <div className="h3-empty-state">{loading ? "Loading…" : ""}</div>}
            {refs && groups.map((g) => {
              const isCollapsed = !!collapsed[g.id];
              return (
                <div key={g.id} className="h3-seq">
                  <div className="h3-seq-head" onClick={() => setCollapsed({ ...collapsed, [g.id]: !isCollapsed })}>
                    <span className="h3-chev">{isCollapsed ? "▶" : "▼"}</span>
                    <b>{g.label}</b>
                    <span className="h3-muted h3-small">{g.refs.length}{g.refs.length !== g.total ? ` of ${g.total}` : ""}</span>
                  </div>
                  {!isCollapsed && g.refs.map((r) => <RefRow key={r.id} ep={ep} r={r} />)}
                  {!isCollapsed && !g.refs.length && (
                    <div className="h3-muted h3-small h3-pad">
                      {g.id === "keyframes" && !g.total ? KEYFRAMES_EMPTY : g.total ? `None match “${FILTERS.find((f) => f.id === filter)?.label}”.` : "None in the bible."}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}
