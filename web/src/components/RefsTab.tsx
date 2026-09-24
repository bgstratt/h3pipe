// The Refs sidebar tab (Phase 5): every ref in the series config, grouped, with its live
// file, its candidates (takes), pick / generate / import / compare, and the
// ref's prompt and settings override.

import { memo, useEffect, useMemo, useRef, useState, type MouseEvent } from "react";
import {
  clearRef, copyText, discardRefTake, dismissMissingResult, dropFilesFromDrag, generateMissing, generateRef, keyframeFromTake, loadRefs, openBrowse,
  openImageCompare, openRefFile, pickRef, revertRefOverride, saveRefOverride, selectRefTake, setRefDefault, setRefsFilter, toggleRefOpen,
} from "../actions";
import {
  cutNeighbour, isKeyframeRef, keyframeBlocks, keyframeGroups, keyframeOf, keyframePlan, keyframeSource, methodLabel, methodTitle,
  needLabel,
} from "../lib/keyframes";
import { errText } from "../api";
import { api } from "../host";
import { shortName, tn } from "../lib/format";
import {
  defaultOf, editRefsFor, editRefsText, imageModeText, imageTargets, isEditTarget, refDefaultSourceLabel, refDefaults,
  refTargetKind, refTargetOf, refsSnippet, type RefTargetKind, type ResolvedRefDefaults,
} from "../lib/imageTargets";
import { audioTargets, voiceCapNotes, voiceModeText } from "../lib/voice";
import { VoiceCandidate, VoiceGenerateBar, VoiceLive } from "./VoiceRef";
import { dragHasFiles, missingResultText } from "../lib/lookback";
import { formFromDetail, isDirty, overrideFields, type OverrideForm, type OverrideSource } from "../lib/overrideForm";
import { pickWarning, readinessBadge, targetOptionText } from "../lib/readiness";
import { sharedNote } from "../lib/shared";
import {
  SHEET_VIEW, VIEWS, blockedShots, canGenerate, groupRefs, hasViews, missingPlan, isAudioRef, pickedOf, pickedTake, refCounts, takeFile, takeUsable, takesOf,
  unpickedViews, usedBy, viewLabel, viewOf, type RefFilter,
} from "../lib/refs";
import { findTarget, targetLabel, targetShort } from "../lib/targets";
import { store, useApp } from "../store";
import type { Ref, RefTake, SeedMode } from "../types";
import { useStatus } from "./hooks";
import { useTargets } from "./Targets";
import { OverrideFields } from "./OverrideFields";
import { PassToggle } from "./ShotsTab";
import { Progress } from "./Thumb";
import { DropSlot, SupplyPicker, UploadButton } from "./Upload";

const FILTERS: { id: RefFilter; label: string; title: string }[] = [
  { id: "episode", label: "this episode", title: "Refs used by this episode's shots (this pass)" },
  { id: "all", label: "all", title: "Everything in the series config, used or not" },
  { id: "missing", label: "missing", title: "Refs whose file isn't on disk" },
];

export const KEYFRAMES_EMPTY = "No shot's target reads keyframes. Use a previous shot's frame (right-click a shot) or import one";
const NO_FILE = "series.json names no file for this (e.g. a voice-only character has no sheet)";

/** The episode's image targets for refs and keyframes (GET /h3pipe/refs `defaults`). */
function useRefDefaults(): ResolvedRefDefaults {
  const list = useApp((s) => s.targets);
  const served = useApp((s) => (s.ep ? s.refDefaults[s.ep] : null));
  return useMemo(() => refDefaults(list, served), [list, served]);
}

/**
 * "Refs render with: Krea 2 ✓ · Keyframes with: Flux 2 Klein edit ✓". Each is
 * a picker over the image targets. A choice is the episode's (PUT
 * /h3pipe/refs/defaults, kept in overrides.json); the first option goes back to
 * the series config's (or the built-in) default. An episode choice offers the
 * series.json snippet that makes it permanent.
 */
function ImageModelBar() {
  const { list } = useTargets();
  const defaults = useRefDefaults();
  const busy = useApp((s) => s.busy);
  const images = imageTargets(list);
  const voices = audioTargets(list);
  if (!list || (!images.length && !voices.length)) return null;
  const row = (kind: RefTargetKind, label: string) => {
    const choices = kind === "voices" ? voices : images;
    if (!choices.length) return null;
    const { target: cur, source: src } = defaultOf(defaults, kind);
    const t = findTarget(list, cur);
    const warn = pickWarning(targetLabel(list, cur), t?.readiness);
    const badge = readinessBadge(t?.readiness);
    const editor = src === "editor";
    const what = kind === "voices" ? voiceModeText(t) || "audio model" : imageModeText(t) || "image model";
    return (
      <span className="h3-row" style={{ gap: 4 }}>
        <span className="h3-muted h3-small">{label}</span>
        <select
          className="h3-in"
          value={editor ? cur : ""}
          disabled={!!busy[`refdefaults|${kind}`]}
          title={`${what}${badge ? ` · ${badge.title}` : ""}\nNow: ${targetLabel(list, cur)} (${refDefaultSourceLabel(src)}). A choice here is this episode's; a ref with its own model keeps it.`}
          onChange={(e) => void setRefDefault(kind, e.target.value || null)}
        >
          <option value="">{editor ? "(back to the series config's default)" : `${targetLabel(list, cur)} (${refDefaultSourceLabel(src)})`}</option>
          {choices.map((x) => (
            <option key={x.id} value={x.id} title={kind === "voices" ? voiceModeText(x) : imageModeText(x)}>
              {targetOptionText(x)}{kind === "voices" ? "" : isEditTarget(x) ? " · edit" : ""}
            </option>
          ))}
        </select>
        {warn && <span className={warn.severity === "err" ? "h3-err h3-small" : "h3-warn h3-small"} title={warn.text}>{warn.severity === "err" ? "not ready" : "degraded"}</span>}
      </span>
    );
  };
  const voiceTarget = findTarget(list, defaults.voices);
  const custom = (["refs", "keyframes", "voices"] as RefTargetKind[]).filter((k) => defaultOf(defaults, k).source === "editor");
  const WHAT: Record<RefTargetKind, string> = { refs: "refs", keyframes: "keyframes", voices: "voice samples" };
  return (
    <div className="h3-col" style={{ gap: 3 }}>
      <div className="h3-row h3-wrap" style={{ gap: 8 }}>
        {row("refs", "Refs render with:")}
        {row("keyframes", "Keyframes with:")}
        {row("voices", "Voices with:")}
      </div>
      {voiceTarget && (
        <span className="h3-small h3-muted" title={voiceCapNotes(voiceTarget).join(" ")}>
          {targetLabel(list, voiceTarget.id)}: {voiceModeText(voiceTarget)}. {voiceCapNotes(voiceTarget)[0]}
        </span>
      )}
      {custom.map((k) => {
        const id = defaultOf(defaults, k).target;
        return (
          <div key={k} className="h3-note h3-note-info h3-small">
            This episode generates {WHAT[k]} with {targetLabel(list, id)} (kept in overrides.json; series.json isn't changed).
            To make it the series default, add to series.json:{" "}
            <code className="h3-mono">{refsSnippet(k, id)}</code>{" "}
            <button className="h3-link" onClick={() => void copyText(refsSnippet(k, id), "Snippet")}>copy</button>{" "}
            <button className="h3-link" onClick={() => void setRefDefault(k, null)}>back to the series default</button>
          </div>
        );
      })}
    </div>
  );
}

/**
 * P8: where a character's live sheet came from -- one supplied as a take, the
 * four views stitched, or (no take at all) a file put in the folder by hand,
 * which is a perfectly good way to supply one and is left alone.
 */
function SheetLine({ r }: { r: Ref }) {
  if (!r.exists) return null;
  if (r.live_from === SHEET_VIEW) {
    const t = r.picked;
    return (
      <div className="h3-small h3-muted">
        The live sheet is a supplied one{t ? ` (${tn(t)})` : ""}. Picking all four views stitches over it.
      </div>
    );
  }
  if (r.live_from === "views") {
    return <div className="h3-small h3-muted">The live sheet is stitched from the four picked views.</div>;
  }
  return (
    <div className="h3-small h3-muted">
      The live sheet was put there outside the editor — it is used as it is. Drop one here to
      keep it as a candidate instead.
    </div>
  );
}

/** A keyframe's actions: continuity, a generated still, import, clear. */
function KeyframeActions({ r }: { r: Ref }) {
  const k = keyframeOf(r);
  const pass = useApp((s) => s.pass);
  const st = useStatus();
  const refs = useApp((s) => (s.ep ? s.refs[s.ep] : undefined));
  const defaults = useRefDefaults();
  const { list } = useTargets();
  const busyFrame = useApp((s) => !!k && !!s.busy[`keyframe|${k.shot}|${k.which}`]);
  const busyGen = useApp((s) => !!s.busy[`refgen|${r.id}`] || !!s.busy[`refimport|${r.id}`]);
  const busyClear = useApp((s) => !!s.busy[`refclear|${r.id}`]);
  if (!k) return null;
  const src = cutNeighbour(st, k.shot, k.which === "first" ? -1 : 1);
  const srcSt = src ? st?.shots.find((x) => x.shot === src) : undefined;
  const srcOk = !!srcSt && srcSt.cut.take != null && srcSt.cut.usable;
  const tg = refTargetOf(r, defaults);
  const target = findTarget(list, tg.target);
  const plan = editRefsFor(r, target, refs ?? []);
  const gen = canGenerate(r);
  const cont = k.which === "first" ? "From previous shot" : "From next shot";
  return (
    <div className="h3-col" style={{ gap: 2 }} onClick={(e) => e.stopPropagation()}>
      <div className="h3-row h3-wrap" style={{ gap: 4 }}>
        <button
          className="h3-btn"
          disabled={busyFrame || !src}
          title={src
            ? `A new candidate: ${src}'s ${k.which === "first" ? "last" : "first"} frame, from the take the ${pass} cut uses${srcOk ? "" : ` (${src} has no usable take yet)`}. It goes live if ${k.shot} has no ${k.which} keyframe yet.`
            : `${k.shot} has no ${k.which === "first" ? "previous" : "next"} shot in the ${pass} cut`}
          onClick={() => void keyframeFromTake({ shot: k.shot, which: k.which })}
        >
          <i className={busyFrame ? "pi pi-spin pi-spinner" : "pi pi-link"} /> {cont}{src ? ` (${src})` : ""}
        </button>
        <button
          className="h3-btn"
          disabled={busyGen || !gen}
          title={gen
            ? `A still of ${k.shot}'s ${k.which === "first" ? "opening" : "closing"} moment, made with ${editRefsText(list, tg.target, plan)}`
            : r.why_not || "This server doesn't generate keyframes"}
          onClick={() => void generateRef({ ref: r.id, view: null, count: 1, seed_mode: "auto", seed: null, prompt: null, model: null, loras: null, steps: null, note: "" })}
        >
          <i className={busyGen ? "pi pi-spin pi-spinner" : "pi pi-sparkles"} /> Generate
        </button>
        <UploadButton refId={r.id} view={null} kind="image" title={`Upload an image from this computer as ${k.shot}'s ${k.which} keyframe, live at once (or drop one on this row)`} />
        <button
          className="h3-btn"
          disabled={busyGen}
          title="Import an image from the ComfyUI machine as a new candidate"
          onClick={() => openBrowse({ purpose: "import", ref: r.id, view: null, files: "image" })}
        >
          <i className="pi pi-download" /> Import…
        </button>
        <button
          className="h3-btn h3-danger"
          disabled={busyClear || !r.exists}
          title={r.exists
            ? `Remove the live ${k.which} keyframe (the candidates stay). ${r.need === "required" ? `${k.shot} can't render without one.` : `${k.shot} then renders without it.`}`
            : "No live keyframe to clear"}
          onClick={() => void clearRef(r.id)}
        >
          <i className="pi pi-times" /> Clear
        </button>
      </div>
      {gen && isEditTarget(target) && (
        <span className="h3-small h3-muted" title="An edit model keeps the characters' look: it gets their picked views and the plate as reference images">
          Generate uses {editRefsText(list, tg.target, plan)}
        </span>
      )}
    </div>
  );
}

/** A ref's live file (the one renders read), or a "missing" placeholder. */
function LiveThumb({ ep, r, size }: { ep: string; r: Ref; size: number }) {
  if (!r.path && isAudioRef(r)) {
    // Phase 9c-B: a character the series config gives no `voice_sample`; a
    // pick writes refs/voices/<id>.wav and that line
    return (
      <div className="h3-refthumb h3-thumb h3-empty" style={{ width: size, height: size }} title="No voice sample yet: generate one (or take a line from a take) and pick it, and series.json gains the voice_sample line.">
        <i className="pi pi-volume-off" style={{ fontSize: size / 2.6 }} />
        <span className="h3-thumb-label">no sample</span>
      </div>
    );
  }
  if (!r.path) {
    // the series config names no file for it (a voice-only character has no sheet)
    return (
      <div className="h3-refthumb h3-thumb h3-empty" style={{ width: size, height: size }} title={NO_FILE}>
        <span className="h3-thumb-label">no file</span>
      </div>
    );
  }
  if (isAudioRef(r)) {
    return (
      <div className={`h3-refthumb h3-thumb${r.exists ? "" : " h3-empty h3-missing"}`} style={{ width: size, height: size }} title={r.exists ? r.path : `${r.path} isn't on disk: drop an audio file here, or Upload…`}>
        <i className="pi pi-volume-up" style={{ fontSize: size / 2.6 }} />
        {!r.exists && <span className="h3-thumb-label">missing</span>}
      </div>
    );
  }
  if (!r.exists) {
    return (
      <div className="h3-refthumb h3-thumb h3-empty h3-missing" style={{ width: size, height: size }} title={`${r.path} isn't on disk${hasViews(r) ? "" : ": drop an image here, or Upload…"}`}>
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
  // a voice's candidate is `audio` (its `image` is null)
  const file = takeFile(t);
  const url = file ? api().refFileUrl(ep, file) : null;
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
    `${tn(t.take)} · ${t.status}${live ? " · live" : ""} · ${t.source}${t.original_name ? ` (${t.original_name})` : ""}`,
    keyframeSource(t) ? `from ${keyframeSource(t)}` : "",
    t.seed ? `seed ${t.seed}` : "",
    t.note ? `“${t.note}”` : "",
    t.save_notes && t.status === "failed" ? t.save_notes : "",
    "click: select · ctrl/⌘-click: compare with the selected · double-click: open in the viewer",
  ].filter(Boolean).join("\n");
  // Phase 9c-B: a voice candidate plays, and draws its waveform
  if (isAudioRef(r)) return <VoiceCandidate ep={ep} r={r} t={t} live={live} selected={selected} onClick={click} />;
  return (
    <div
      className={`h3-cand${selected ? " h3-sel" : ""}${live ? " h3-live" : ""}`}
      onClick={click}
      onDoubleClick={() => openImageCompare(r.id, view, t.take, live ? null : pickedOf(r, view))}
      title={title}
    >
      <div
        className={`h3-cand-img h3-thumb${url && t.status === "ok" ? "" : " h3-empty"}`}
        style={{
          ...(url && t.status === "ok" ? { backgroundImage: `url("${url}")` } : {}),
          // a single-image ref shows each candidate whole, at its own shape
          // (a 16:9 plate isn't cropped to a square); the view columns stay square
          ...(!view ? { aspectRatio: t.width && t.height ? `${t.width} / ${t.height}` : r.kind === "location" ? "16 / 9" : "1", backgroundSize: "contain" } : {}),
        }}
      >
        {!(url && t.status === "ok") && <span className={t.status === "failed" ? "h3-err" : ""}>{t.status}</span>}
        <span className="h3-thumb-label">{tn(t.take)}{t.source === "imported" ? " ⤓" : t.source === "frame" && t.from ? ` ← ${t.from.shot}` : ""}</span>
        {live && <span className="h3-cand-live">live</span>}
      </div>
      {prog && <Progress value={prog.value} max={prog.max} />}
    </div>
  );
});

function CandidateGrid({ ep, r, view, cols }: { ep: string; r: Ref; view: string | null; cols?: boolean }) {
  const sel = useApp((s) => s.refSel);
  const takes = takesOf(r, view);
  const picked = pickedOf(r, view);
  return (
    <div className={cols ? "h3-cand-col" : isAudioRef(r) ? "h3-col" : `h3-cand-grid h3-cand-single${r.kind === "location" ? " h3-cand-wide" : ""}`}>
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

/** The selected candidate's actions: pick, compare, discard, details. */
function Selection({ r }: { r: Ref }) {
  const sel = useApp((s) => (s.refSel && s.refSel.ref === r.id ? s.refSel : null));
  const busy = useApp((s) => !!s.busy[`refpick|${r.id}`]);
  const busyDiscard = useApp((s) => !!sel && !!s.busy[`refdiscard|${r.id}|${sel.view ?? ""}|${sel.take}`]);
  if (!sel) return null;
  const t = takesOf(r, sel.view).find((x) => x.take === sel.take);
  if (!t) return null;
  const picked = pickedOf(r, sel.view);
  const live = picked === t.take;
  const usable = takeUsable(t);
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
        {live && (
          <button
            className="h3-btn h3-danger"
            title={isKeyframeRef(r)
              ? "Clear: remove the live keyframe (the candidates stay); the shot renders without one"
              : "Remove the live file (the candidates stay). The shots that use it are blocked until you pick one again."}
            onClick={() => void clearRef(r.id, sel.view)}
          >
            <i className="pi pi-times" /> {isKeyframeRef(r) ? "Clear" : "Unpick"}
          </button>
        )}
        <button
          className="h3-btn h3-danger"
          disabled={t.status === "queued" || busyDiscard}
          title={t.status === "queued"
            ? "Still queued: it can be discarded once it has finished or failed"
            : `Move ${tn(t.take)} to refs/_takes/_trash (nothing is deleted)${live ? "; it is live, so the ref is cleared too" : ""}`}
          onClick={() => void discardRefTake(r.id, sel.view, t.take)}
        >
          <i className={busyDiscard ? "pi pi-spin pi-spinner" : "pi pi-trash"} /> Discard
        </button>
      </div>
      {(t.prompt || t.model || t.note || t.original_name || keyframeSource(t) || (t.source === "generated") || (t.status === "failed" && t.save_notes)) && (
        <div className="h3-kv">
          {keyframeSource(t) && <><span>from</span><span>{keyframeSource(t)}</span></>}
          {t.source === "generated" && (
            <>
              <span>drawn</span>
              <span title={(t.references || []).length
                ? "An edit: these pictures were fed to the model as references"
                : "Text to image: the model was given the prompt and nothing else"}>
                {(t.references || []).length
                  ? `edited from ${(t.references || []).map((x) =>
                      `${x.name || x.id || "a reference"}${x.view ? ` (${viewLabel(x.view)})` : ""}`).join(", ")}`
                  : "from the prompt alone"}
              </span>
            </>
          )}
          {t.original_name && <><span>file</span><span>{t.original_name}</span></>}
          {t.target && <><span>model</span><span>{t.target}</span></>}
          {t.note && <><span>note</span><span>{t.note}</span></>}
          {t.model && <><span>file</span><span title={t.model}>{shortName(t.model, 40)}</span></>}
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
  const isChar = hasViews(r);
  const [view, setView] = useState<string>(isChar ? "" : "");
  const [count, setCount] = useState(1);
  const [seedMode, setSeedMode] = useState<SeedMode>("auto");
  // a voice generates through its own bar (count, seconds and the line)
  const gen = canGenerate(r) && !isAudioRef(r);
  const importView = isChar ? view || null : null;
  const kind = isAudioRef(r) ? "audio" : "image";
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
            <i className={busy ? "pi pi-spin pi-spinner" : "pi pi-sparkles"} /> Generate {count > 1 ? `${count} takes` : "a take"}
          </button>
        </>
      )}
      <button
        className="h3-btn"
        disabled={busy || (isChar && !view)}
        title={isChar && !view ? "Choose a view to import into" : `Import ${isAudioRef(r) ? "an audio file" : "an image"} from the ComfyUI machine as a new candidate`}
        onClick={() => openBrowse({ purpose: "import", ref: r.id, view: importView, files: kind })}
      >
        <i className="pi pi-download" /> Import…
      </button>
      {!isChar ? (
        <UploadButton refId={r.id} view={null} kind={kind} title={`Upload ${kind === "audio" ? "an audio file" : "an image"} from this computer as a new candidate, live at once (or drop one on this row)`} />
      ) : (
        // P8: a finished 4-panel sheet, instead of generating four views
        <UploadButton
          refId={r.id}
          view={SHEET_VIEW}
          kind="image"
          label="Upload sheet…"
          title="Upload a finished 4-panel sheet from this computer: it becomes a candidate and goes live, without stitching"
        />
      )}
    </div>
  );
}

/** The ref's own image model (`target` in refs/_overrides.json), saved at once. */
function RefTargetOverride({ r }: { r: Ref }) {
  const { list } = useTargets();
  const defaults = useRefDefaults();
  const busy = useApp((s) => !!s.busy[`refoverride|${r.id}`]);
  const kind: RefTargetKind = refTargetKind(r);
  // a voice's own target must be an audio one, an image ref's an image one
  const choices = kind === "voices" ? audioTargets(list) : imageTargets(list);
  if (!choices.length) return null;
  const own = r.override_values?.target ?? "";
  const { target: fallback, source: src } = defaultOf(defaults, kind);
  const cur = refTargetOf(r, defaults);
  const t = findTarget(list, cur.target);
  const what = kind === "voices" ? "Voice model" : "Image model";
  const mode = (x: typeof t) => (kind === "voices" ? voiceModeText(x) : imageModeText(x));
  return (
    <div className="h3-col" style={{ gap: 2 }}>
      <div className="h3-row">
        <span className="h3-h">{what}</span>
        <select
          className="h3-in h3-grow"
          value={own}
          disabled={busy}
          title={`This ref's own model (kept in refs/_overrides.json). Empty: the ${kind === "keyframes" ? "keyframes'" : kind === "voices" ? "voices'" : "refs'"} model above.`}
          onChange={(e) => void saveRefOverride(r.id, { target: e.target.value || null })}
        >
          <option value="">{`(${refDefaultSourceLabel(src)}: ${targetLabel(list, fallback)})`}</option>
          {choices.map((x) => <option key={x.id} value={x.id}>{targetOptionText(x)}{kind !== "voices" && isEditTarget(x) ? " · edit" : ""}</option>)}
        </select>
      </div>
      {t && <span className="h3-small h3-muted">{targetLabel(list, cur.target)}{mode(t) ? `: ${mode(t)}` : ""}{cur.source === "override" ? " (this ref's own)" : ""}</span>}
    </div>
  );
}

/**
 * The ref's prompt/seed/model/LoRA/steps override, with the inspector's form.
 * A character's settings are per view (each view generates with its own
 * prompt); "all views" holds what they share (seed, model, LoRAs, steps), and
 * its prompt is per view only.
 */
function RefOverrideEditor({ r }: { r: Ref }) {
  const chars = hasViews(r);
  const [view, setView] = useState<string | null>(chars ? r.views![0].view : null);
  return (
    <div className="h3-col">
      <RefTargetOverride r={r} />
      {chars && (
        <div className="h3-row h3-wrap">
          <span className="h3-seg" title="Each view generates with its own prompt and settings; all views share the character's">
            {r.views!.map((v) => (
              <button key={v.view} className={view === v.view ? "h3-on" : ""} onClick={() => setView(v.view)}>
                {viewLabel(v.view)}{v.override?.fields.some((f) => f !== "target") ? " •" : ""}
              </button>
            ))}
            <button className={view == null ? "h3-on" : ""} title="Settings every view shares (no prompt: that is per view)" onClick={() => setView(null)}>
              all views{r.override.fields.some((f) => f !== "target") ? " •" : ""}
            </button>
          </span>
        </div>
      )}
      <RefSettingsForm key={`${r.id}|${view ?? ""}`} r={r} view={chars ? view : null} />
    </div>
  );
}

/** The override form for a ref, or for one of a character's views. */
function RefSettingsForm({ r, view }: { r: Ref; view: string | null }) {
  const busy = useApp((s) => !!s.busy[`refoverride|${r.id}`]);
  const chars = hasViews(r);
  // a character's own level has no prompt (a prompt override is per view)
  const noPrompt = chars && !view;
  // everything from `r`, so a refetch (a new `r`) is the only thing that rebases the form
  const { ovInfo, eff, ovFields, src, inherited } = useMemo(() => {
    const v = view ? viewOf(r, view) : undefined;
    const ovInfo = v ? v.override : r.override;
    const values = (v ? v.override?.values : r.override_values ?? r.override.values) ?? {};
    const eff = v ? v.effective ?? null : hasViews(r) ? null : r.effective ?? null;
    const ovFields = ovInfo?.fields ?? [];
    const prompt = v ? v.prompt ?? eff?.prompt ?? "" : eff?.prompt ?? r.prompt ?? "";
    const overridden = ovFields.includes("prompt");
    const src: OverrideSource = {
      override: values,
      effective: { prompt },
      // P9: a view now carries the series config's wording for itself, so an
      // overridden view prompt can be diffed and put back. (It used to send ""
      // here, which left Diff and Series config with nothing to work from.)
      built_prompt: v
        ? v.built_prompt ?? (overridden ? "" : prompt)
        : r.built_prompt ?? (overridden ? "" : prompt),
    };
    // P9: which of the fields on show are this view's own; the rest come from
    // the character and are changed on its row
    const inherited = v ? ovFields.filter((f) => !(ovInfo?.own ?? ovFields).includes(f)) : [];
    return { ovInfo, eff, ovFields, src, inherited };
  }, [r, view]);
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
    await saveRefOverride(r.id, fields, view);
  };
  const ov = src.override;
  // the image target is its own picker (and per ref, never per view)
  const has = ovFields.some((f) => f !== "target");
  const who = view ? `${r.name} (${viewLabel(view)})` : r.name;
  const promptOverridden = ovFields.includes("prompt");
  return (
    <div className="h3-col">
      {ovInfo?.stale && <div className="h3-note"><b>Override stale.</b> The series config's text for {view ? "this view" : "this ref"} changed since the override was written.</div>}
      {view && (
        <div className="h3-small h3-muted">
          What {viewLabel(view)} generates with.{" "}
          {inherited.length
            ? <>Its {inherited.join(", ")} {inherited.length > 1 ? "come" : "comes"} from {r.name} and {inherited.length > 1 ? "are" : "is"} shared by all four views — changing {inherited.length > 1 ? "them" : "it"} here makes {inherited.length > 1 ? "them" : "it"} this view's own.</>
            : <>A change here is this view's own; {r.name}'s row sets what all four share.</>}
          {promptOverridden && " The series config's text is under Diff, and Series config puts it back."}
        </div>
      )}
      <OverrideFields
        form={form}
        set={set}
        builtPrompt={src.built_prompt}
        builtLabel="series config"
        promptOverridden={promptOverridden}
        promptLocked={noPrompt ? { label: "the sheet's description, read-only", note: "A character's prompt is per view: pick a view above to change what it generates.", text: r.built_prompt ?? r.prompt ?? "" } : null}
        showDiff={showDiff}
        setShowDiff={setShowDiff}
        rows={7}
        seedPlaceholder={`${eff?.seed ?? (eff?.seed_source === "new" ? "new each time" : "")} (series config)`}
        modelPlaceholder={`(series config) ${ov.model == null && eff?.model ? shortName(eff.model, 40) : ""}`}
        stepsPlaceholder={`${ov.steps == null && eff?.steps != null ? eff.steps : ""} (series config)`}
        effLoras={eff?.loras ?? null}
        lorasOverridden={ov.loras != null || ovFields.includes("loras")}
      />
      {err && <div className="h3-note h3-note-err">{err}</div>}
      <div className="h3-row h3-wrap">
        <button className="h3-btn h3-primary" disabled={!dirty || busy} onClick={() => void save()}>{busy ? "Saving…" : "Save"}</button>
        <button className="h3-btn" disabled={!dirty || busy} onClick={() => setForm(base)}>Discard edits</button>
        <span className="h3-grow" />
        <button
          className="h3-btn h3-danger"
          disabled={!has || busy}
          title={view ? `Remove ${viewLabel(view)}'s own settings (the ones for all views stay)` : "Remove this ref's settings (its image model stays)"}
          onClick={() => confirm(`Put ${who} back to the series config's settings?`) && void revertRefOverride(r.id, view)}
        >
          Revert
        </button>
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
      {isAudioRef(r) && <VoiceLive ep={ep} r={r} />}
      {hasViews(r) ? (
        <>
          <SheetLine r={r} />
          {r.takes.length > 0 && (
            <div className="h3-col" style={{ gap: 2 }}>
              <div className="h3-h h3-small">Supplied sheets</div>
              <CandidateGrid ep={ep} r={r} view={SHEET_VIEW} />
            </div>
          )}
          {missingViews.length > 0 && missingViews.length < 4 && (
            <div className="h3-small h3-muted">The sheet is stitched when every view has a pick: {missingViews.map(viewLabel).join(", ")} still to pick.</div>
          )}
          <div className="h3-views">
            {VIEWS.map((v) => {
              const rv = viewOf(r, v.view);
              const live = rv ? pickedTake(rv) : undefined;
              return (
                <DropSlot key={v.view} refId={r.id} view={v.view} kind="image" className="h3-view-col" title={`Drop an image on ${v.label}: it becomes a new candidate and goes live`}>
                  <div className="h3-view-head" title={v.view}>
                    {v.label}
                    <span className={live ? "h3-muted" : "h3-err"}>{live ? ` ${tn(live.take)}` : rv?.cleared ? " cleared" : " —"}</span>
                    <span className="h3-grow" />
                    <UploadButton refId={r.id} view={v.view} kind="image" label="" title={`Upload an image from this computer as ${r.name}'s ${v.label} view, live at once (or drop one on this column)`} />
                  </div>
                  <CandidateGrid ep={ep} r={r} view={v.view} cols />
                </DropSlot>
              );
            })}
          </div>
        </>
      ) : (
        <CandidateGrid ep={ep} r={r} view={null} />
      )}
      <Selection r={r} />
      {/* a keyframe's continuity / generate / import / clear sit on its row (KeyframeActions) */}
      {r.can_generate === false && r.why_not && (
        <div className="h3-small h3-muted">Can't generate: {r.why_not}. {isKeyframeRef(r) ? "Use a neighbouring shot's frame or import one." : "Import a file instead."}</div>
      )}
      {isAudioRef(r) && canGenerate(r) && <VoiceGenerateBar r={r} />}
      {isAudioRef(r) && !canGenerate(r) && (
        <div className="h3-small h3-muted">
          This server doesn't generate voices{r.why_not ? `: ${r.why_not}` : ""}. Import a recording, or take a line from a take.
        </div>
      )}
      {!isKeyframeRef(r) && <GenerateBar r={r} />}
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
  const all = hasViews(r) ? r.views!.flatMap((v) => v.takes) : r.takes;
  const queued = all.filter((t) => t.status === "queued").length;
  const live = hasViews(r) ? null : r.picked;
  const picks = hasViews(r) ? r.views!.filter((v) => v.picked != null).length : null;
  const kf = isKeyframeRef(r) ? keyframeOf(r) : null;
  const { list } = useTargets();
  const need = needLabel(r.need);
  const method = kf ? methodLabel(r.method, kf.which) : "";
  // an optional keyframe with no file isn't "missing": the shot renders without it
  const showMissing = !r.exists && !!r.path && (!kf || r.need !== "optional" || !!r.requested) && r.method !== "none";
  const chars = hasViews(r);
  const note = sharedNote(r, ep);
  return (
    <DropSlot
      refId={r.id}
      // P8: a character's row takes a ready-made 4-panel sheet, on the reserved
      // `sheet` view -- the file it names IS a sheet, so dropping one there is
      // the obvious thing to try. Its four views are still below.
      view={chars ? SHEET_VIEW : null}
      kind={isAudioRef(r) ? "audio" : "image"}
      refuse={!r.path && !isAudioRef(r)
        // a voice with no sample yet still takes one: picking it writes the series config
        ? "The series config names no file for this ref"
        : null}
      title={chars
        ? "Drop a finished 4-panel sheet here, or open the row and drop one picture per view"
        : undefined}
      className={`h3-ref${open ? " h3-open" : ""}`}
      dataRef={r.id}
    >

      <div
        className="h3-ref-row"
        onClick={() => toggleRefOpen(r.id)}
        onDoubleClick={() => {
          // the two clicks of a double-click have already toggled it twice, so
          // the row is where it started; open the live file on top of that
          if (!isAudioRef(r) && r.exists && r.path) openRefFile(r.id, r.path);
        }}
        title={r.exists && !isAudioRef(r)
          ? `Click to expand, double-click to open ${r.path}`
          : undefined}
      >
        <span className="h3-chev">{open ? "▼" : "▶"}</span>
        <LiveThumb ep={ep} r={r} size={40} />
        <div className="h3-col h3-grow" style={{ gap: 1 }}>
          <div className="h3-row">
            <b className="h3-ell">{kf ? `${kf.which} frame` : r.name}</b>
            {r.of && (
              <span
                className="h3-muted h3-small h3-ell"
                title={`A wardrobe variant of ${r.of}: same character, own sheet. Its views are generated from ${r.of}'s, and it shares ${r.of}'s voice.`}
              >
                variant of {r.of}
              </span>
            )}
            <span className="h3-muted h3-small h3-ell">{r.id}</span>
          </div>
          {kf ? (
            <div className="h3-row h3-small h3-muted h3-wrap">
              {method && <span title={methodTitle(r.method, kf.which)}>{method}</span>}
              {r.target && <span title={`${targetLabel(list, r.target)} reads this keyframe`}>· read by {targetShort(list, r.target)}</span>}
              <span>· {all.length} cand.</span>
              {live != null && <span>· live {tn(live)}</span>}
            </div>
          ) : (
            <div className="h3-row h3-small h3-muted">
              <span title={used.join(", ")}>used by {used.length} shot{used.length === 1 ? "" : "s"}</span>
              <span>· {all.length} cand.</span>
              {picks != null && <span>· {picks}/4 views</span>}
              {live != null && <span>· live {tn(live)}</span>}
            </div>
          )}
          <span className="h3-badges">
            {need && (
              <span
                className={`h3-badge ${need === "required" ? "h3-b-kf-required" : "h3-b-kf-optional"}`}
                title={need === "required" ? `${targetLabel(list, r.target)} can't render ${kf?.shot ?? "the shot"} without it` : "The target can use it; the shot renders without one"}
              >
                {need}
              </span>
            )}
            {showMissing && <span className="h3-badge h3-b-failed" title={`${r.path} isn't on disk`}>missing</span>}
            {/* P9: the live file isn't this episode's own pick, and others read it */}
            {note && <span className={`h3-badge ${note.label === "not from here" ? "h3-b-override-stale" : "h3-b-override"}`} title={note.title}>{note.label}</span>}
            {blocked.length > 0 && <span className="h3-badge h3-b-missing-refs" title={`Renders of these shots are skipped until this ref exists:\n${blocked.join(", ")}`}>blocks {blocked.length}</span>}
            {queued > 0 && <span className="h3-badge h3-b-queued">queued ×{queued}</span>}
            {r.override.fields.length > 0 && <span className="h3-badge h3-b-override" title={`Override: ${r.override.fields.join(", ")}`}>override</span>}
            {r.override.stale && <span className="h3-badge h3-b-override-stale">override stale</span>}
          </span>
          {kf && <KeyframeActions r={r} />}
          {kf && keyframeBlocks(r) && (
            <div className="h3-small h3-err">Missing: {targetLabel(list, r.target)} can't render {kf.shot} without a {kf.which} frame (not even with Render anyway).</div>
          )}
        </div>
      </div>
      {open && <RefDetail ep={ep} r={r} />}
    </DropSlot>
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
  const { list: targetsList } = useTargets();

  useEffect(() => {
    if (ep) void loadRefs(ep);
  }, [ep]);

  const groups = useMemo(() => groupRefs(refs ?? [], filter, pass), [refs, filter, pass]);
  const counts = useMemo(() => refCounts(refs ?? [], st, pass), [refs, st, pass]);
  const plan = useMemo(() => missingPlan(refs ?? [], pass), [refs, pass]);
  const kplan = useMemo(() => keyframePlan(refs ?? [], st), [refs, st]);
  const planLabels = [...plan.map((p) => p.label), ...kplan.map((k) => k.label)];
  const genBusy = useApp((s) => !!s.busy["refgen|missing"]);
  const lastMissing = useApp((s) => (s.ep ? s.missingResult[s.ep] : undefined));
  const focus = useApp((s) => s.refFocus);
  const scrollRef = useRef<HTMLDivElement>(null);
  // P8: the tab itself takes a bulk drop (a folder, or a pile of files)
  const [bulkOver, setBulkOver] = useState(false);
  const bulkDepth = useRef(0);

  // the inspector's "Refs this shot uses": scroll to the ref (its group opened)
  useEffect(() => {
    if (!focus || !refs) return;
    const r = refs.find((x) => x.id === focus.id);
    if (r) {
      const gid = isKeyframeRef(r) ? "keyframes" : groupRefs([r], "all", pass).find((g) => g.refs.length)?.id;
      if (gid) setCollapsed((c) => (c[gid] ? { ...c, [gid]: false } : c));
    }
    const t = setTimeout(() => {
      const box = scrollRef.current;
      const el = box?.querySelector<HTMLElement>(`[data-ref="${CSS.escape(focus.id)}"]`);
      if (box && el) {
        // below the sticky group header
        const head = el.closest(".h3-seq")?.querySelector<HTMLElement>(".h3-seq-head")?.offsetHeight ?? 0;
        box.scrollTop += el.getBoundingClientRect().top - box.getBoundingClientRect().top - head - 4;
      }
      el?.classList.add("h3-flash");
      setTimeout(() => el?.classList.remove("h3-flash"), 1200);
    }, 50);
    return () => clearTimeout(t);
  }, [focus, refs, pass]);

  return (
    <div className="h3-surface">
      <div className="h3-bar">
        <span className="h3-title">Refs</span>
        {refs && <span className="h3-muted h3-small">{refs.length} in the series config</span>}
        <span className="h3-grow" />
        {loading && <i className="pi pi-spin pi-spinner h3-muted" />}
        <PassToggle />
        <button className="h3-btn h3-icon" title="Refresh" disabled={!ep} onClick={() => void loadRefs()}><i className="pi pi-refresh" /></button>
      </div>
      {!ep && <div className="h3-empty-state">Pick an episode in the h3 Shots tab.</div>}
      {ep && (
        <>
          <div className="h3-pad h3-col" style={{ gap: 4 }}>
            <ImageModelBar />
            <div className="h3-row h3-wrap">
              <span className="h3-seg" title="Which refs to list">
                {FILTERS.map((f) => (
                  <button key={f.id} className={filter === f.id ? "h3-on" : ""} title={f.title} onClick={() => setRefsFilter(f.id)}>{f.label}</button>
                ))}
              </span>
              <span className="h3-grow" />
              <SupplyPicker />
              {(planLabels.length > 0 || counts.missing > 0) && (
                <button
                  className="h3-btn h3-primary"
                  disabled={genBusy}
                  title={`Queue one candidate for each ref this episode is missing (${pass}); each goes live when it finishes.
Keyframes: required ones, and optional ones the script asks for (continuity from the neighbouring take when it has one, else a still; a file the script names is imported).
It lists what it will do first.${planLabels.length ? `\n${planLabels.join(", ")}` : ""}`}
                  onClick={() => void generateMissing()}
                >
                  <i className={genBusy ? "pi pi-spin pi-spinner" : "pi pi-sparkles"} /> Generate missing{planLabels.length ? ` (${planLabels.length})` : ""}
                </button>
              )}
            </div>
            {lastMissing && lastMissing.pass === pass && (
              <div className="h3-note h3-note-info h3-small">
                {missingResultText(lastMissing).summary}.
                {lastMissing.skipped.length > 0 && (
                  <span title={lastMissing.skipped.map((x) => `${x.ref}${x.view ? ` (${x.view})` : ""}: ${x.reason}`).join("\n")}> (hover for the skipped)</span>
                )}{" "}
                <button className="h3-link" onClick={dismissMissingResult}>dismiss</button>
              </div>
            )}

            {counts.missing > 0 && (
              <div className="h3-note">
                <b>{counts.missing} ref{counts.missing > 1 ? "s" : ""} missing</b>
                {counts.shots > 0 ? `, blocking ${counts.shots} shot${counts.shots > 1 ? "s" : ""} of this episode (${pass})` : ", none used by this episode"}.
              </div>
            )}
            {err && <div className="h3-note h3-note-err">{err} <button className="h3-link" onClick={() => void loadRefs()}>Retry</button></div>}
          </div>
          <div
            className={`h3-scroll h3-sep${bulkOver ? " h3-drop-over" : ""}`}
            ref={scrollRef}
            onDragEnter={(e) => { if (dragHasFiles(e.dataTransfer)) { e.preventDefault(); bulkDepth.current++; setBulkOver(true); } }}
            onDragLeave={(e) => { if (dragHasFiles(e.dataTransfer) && --bulkDepth.current <= 0) { bulkDepth.current = 0; setBulkOver(false); } }}
            onDragOver={(e) => { if (dragHasFiles(e.dataTransfer)) { e.preventDefault(); e.dataTransfer.dropEffect = "copy"; } }}
            onDrop={(e) => {
              // a drop that a ref row handled has already stopped propagating;
              // this is the bulk path -- files matched to slots by name (P8)
              if (!dragHasFiles(e.dataTransfer)) return;
              e.preventDefault();
              bulkDepth.current = 0;
              setBulkOver(false);
              void dropFilesFromDrag(e.dataTransfer);
            }}
          >
            {bulkOver && (
              <div className="h3-note h3-note-info h3-small">
                Drop pictures and voice samples here: each is matched to a ref by its file name,
                and you see the table before anything is sent.
              </div>
            )}
            {!refs && !err && <div className="h3-empty-state">{loading ? "Loading…" : ""}</div>}
            {refs && groups.map((g) => {
              const isCollapsed = !!collapsed[g.id];
              return (
                <div key={g.id} className="h3-seq">
                  <div className="h3-seq-head" onClick={() => setCollapsed((c) => ({ ...c, [g.id]: !c[g.id] }))}>
                    <span className="h3-chev">{isCollapsed ? "▶" : "▼"}</span>
                    <b>{g.label}</b>
                    <span className="h3-muted h3-small">{g.refs.length}{g.refs.length !== g.total ? ` of ${g.total}` : ""}</span>
                  </div>
                  {!isCollapsed && g.id !== "keyframes" && g.refs.map((r) => <RefRow key={r.id} ep={ep} r={r} />)}
                  {!isCollapsed && g.id === "keyframes" && keyframeGroups(g.refs, st).map((kg) => (
                    <div key={kg.shot} className="h3-kf-shot">
                      <div className="h3-kf-shot-head h3-row h3-small">
                        <b>{kg.shot}</b>
                        {kg.target && <span className="h3-muted">on {targetLabel(targetsList, kg.target)}</span>}
                      </div>
                      {kg.refs.map((r) => <RefRow key={r.id} ep={ep} r={r} />)}
                    </div>
                  ))}
                  {!isCollapsed && !g.refs.length && (
                    <div className="h3-muted h3-small h3-pad">
                      {g.id === "keyframes" && !g.total ? KEYFRAMES_EMPTY : g.total ? `None match “${FILTERS.find((f) => f.id === filter)?.label}”.` : "None in the series config."}
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
