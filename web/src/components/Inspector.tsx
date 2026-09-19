// The inspector: a floating window (round 2; it was a sidebar tab) showing the
// selected shot. It follows the selection while open.

import { useEffect, useMemo, useRef, useState } from "react";
import {
  baseRender, clearRef, closeInspector, focusRef, keyframeFromTake, loadDetail, loadRefs, openPromote, openRedo, openViewer, queueRender,
  renderShots, revertOverride, saveOverride, setShotTarget, showInScript,
} from "../actions";
import { errText } from "../api";
import { api, host } from "../host";
import { ESTIMATE_TITLE, cutTake, fmtShotSeconds, lengthEstimated, shortName, shotBadges, tn } from "../lib/format";
import { KEYFRAME_ENDS, cutNeighbour, keyframeNote, keyframeRefId, shotKeyframes, usesKeyframes } from "../lib/keyframes";
import { missingOf } from "../lib/missingRefs";
import { negativeNoEffect, negativeSourceLabel, takesNegative } from "../lib/negative";
import { formFromDetail, isDirty, overrideFields, type OverrideForm } from "../lib/overrideForm";
import { shotRefTiles } from "../lib/refsUsed";
import { findTarget, isRetargeted, retargetNote, shotTarget, targetBadges, targetLabel } from "../lib/targets";
import { useApp } from "../store";
import type { ShotDetail } from "../types";
import { FloatingWindow, defaultInspectorRect } from "./FloatingWindow";
import { useDetail, useDetailError, useShotStatus, useStatus } from "./hooks";
import { MissingRefsNote } from "./MissingRefs";
import { OverrideFields } from "./OverrideFields";
import { ResolvedNotes, TargetReadinessNote } from "./Readiness";
import { PassToggle } from "./ShotsTab";
import { TargetSelect, useTargetPickers, useTargets } from "./Targets";
import { Badges } from "./Thumb";
import { CutSection } from "./CutSection";
import { DropSlot, UploadButton } from "./Upload";

/** The shot's video target: a picker over the video targets (the default
 * marked). A change saves the override's `target` for both passes. */
function TargetPicker({ d, shot }: { d: ShotDetail | undefined; shot: string }) {
  const s = useShotStatus(shot);
  const { list, video, seriesDefault, error } = useTargets();
  const busy = useApp((st) => !!st.busy[`override|${shot}`]);
  const src = d ?? s;
  const current = shotTarget(src, seriesDefault);
  const built = src?.built_target ?? null;
  const note = retargetNote(list, src);
  const tsrc = d?.target_source ?? s?.target_source;
  const srcNote = targetSourceNote(tsrc, targetLabel(list, seriesDefault));
  // with target_source known, only a shot override can be reverted; else the old rule
  const canRevert = tsrc ? tsrc === "override" : isRetargeted(src);
  if (!list) {
    // a server without /h3pipe/targets: say what the shot renders on, if it says
    return src?.target ? <div className="h3-small h3-muted" title={error ?? ""}>target: {src.target}</div> : null;
  }
  return (
    <div className="h3-col" style={{ gap: 2 }}>
      <div className="h3-target-row">
        <span className="h3-h">Target</span>
        <TargetSelect
          value={current}
          list={list}
          video={video}
          disabled={busy || !src}
          title="The video model this shot renders on (both passes). Its prompt, model, LoRA and steps defaults come with it."
          onChange={(id) => id && id !== current && void setShotTarget(shot, id, built)}
        />
        {canRevert && (
          <button className="h3-btn" disabled={busy} title="Remove this shot's own target: it goes back to its script's target, else the episode's" onClick={() => void setShotTarget(shot, null, built)}>
            Revert target
          </button>
        )}
      </div>
      {srcNote && <span className="h3-small h3-muted">{srcNote}</span>}
      {note && !srcNote && <span className="h3-small h3-muted">{note}</span>}
      <TargetReadinessNote id={current} list={list} />
    </div>
  );
}

/** Where the shot's target comes from, in words (null for an older server). */
function targetSourceNote(src: string | null | undefined, episodeLabel: string): string | null {
  switch (src) {
    case "episode": return `follows the episode's model (${episodeLabel})`;
    case "override": return "set for this shot in the editor";
    case "script": return "set by the script (a target: line or a profile)";
    case "request": return "set for this run";
    case "series": return `the series config's target (${episodeLabel})`;
    case "default": return `the built-in default (${episodeLabel})`;
    default: return null;
  }
}

/** The selected (else the cut) take's model substitutions, from its sidecar. */
function TakeResolved({ d, take }: { d: ShotDetail; take: number | null }) {
  const { list } = useTargets();
  const t = d.takes.find((x) => x.take === take);
  const sc = t?.sidecar;
  if (!t || !sc?.resolved) return null;
  return (
    <div className="h3-col" style={{ gap: 2 }}>
      <span className="h3-small h3-muted">{tn(t.take)} rendered with:</span>
      <ResolvedNotes resolved={sc.resolved} target={typeof sc.target === "string" ? sc.target : d.target} list={list} />
    </div>
  );
}

/**
 * "Refs this shot uses" (read-only): a tile per ref the shot's current target
 * reads (subjects, plate, first / last keyframes, reference sheet) with its
 * status. A click opens the Refs tab on it; generating and picking stay there.
 * Keyframes keep two quick actions: From previous, and Clear.
 */
function RefsUsed({ shot, d }: { shot: string; d: ShotDetail | undefined }) {
  const ep = useApp((s) => s.ep);
  const pass = useApp((s) => s.pass);
  const refs = useApp((s) => (s.ep ? s.refs[s.ep] : undefined));
  const busyPrev = useApp((s) => !!s.busy[`keyframe|${shot}|first`]);
  const busyClear = useApp((s) => !!s.busy[`refclear|${keyframeRefId(shot, "first")}`] || !!s.busy[`refclear|${keyframeRefId(shot, "last")}`]);
  const st = useStatus();
  const s = useShotStatus(shot);
  const { list, seriesDefault } = useTargets();
  useEffect(() => {
    if (ep && !refs) void loadRefs(ep);
  }, [ep, refs]);
  const { tiles, exact } = useMemo(() => shotRefTiles(d?.refs_used, refs, shot, pass), [d?.refs_used, refs, shot, pass]);
  if (!ep) return null;
  const prev = cutNeighbour(st, shot, -1);
  const kf = shotKeyframes(refs, shot);
  const reads = usesKeyframes(list, shotTarget(s, seriesDefault));
  const note = keyframeNote(list, shotTarget(s, seriesDefault));
  const hasKfTile = tiles.some((t) => t.keyframe);
  const known = new Set((refs ?? []).map((r) => r.id));
  return (
    <div className="h3-col" style={{ gap: 4 }}>
      <div className="h3-row">
        <span className="h3-h">Refs this shot uses</span>
        {!exact && <span className="h3-small h3-muted" title="This server doesn't send refs_used: read from each ref's used_by">(from the refs list)</span>}
      </div>
      {!tiles.length && <span className="h3-small h3-muted">None: its target reads no reference images.</span>}
      <div className="h3-refs-used">
        {tiles.map((t) => {
          const url = t.image ? api().refFileUrl(ep, t.image, t.version) : null;
          const canOpen = !!t.refId && known.has(t.refId);
          const tile = (
            <div
              className={`h3-ru h3-ru-${t.status}${canOpen ? " h3-ru-link" : ""}${t.audio ? " h3-ru-audio" : ""}`}
              title={(canOpen ? t.title : t.title.replace(/\nClick:.*$/, "")) + (t.keyframe ? "\nDrop an image here: it becomes the live keyframe" : "")}
              onClick={() => canOpen && focusRef(t.refId!)}
            >
              <div
                className={`h3-thumb${url ? "" : " h3-empty"}`}
                style={{ width: 64, height: 44, ...(url ? { backgroundImage: `url("${url}")`, backgroundSize: "contain" } : {}) }}
              >
                {t.audio ? <i className="pi pi-volume-up" /> : !url && <span className="h3-small">{t.status === "live" ? "" : "—"}</span>}
              </div>
              <span className="h3-small h3-ell" style={{ maxWidth: 64 }}>{t.label}</span>
              <span className={`h3-small h3-ru-status`}>{t.statusText}</span>
            </div>
          );
          // a keyframe slot takes a dropped image (uploaded and picked)
          return t.keyframe && t.refId ? (
            <DropSlot key={t.id} refId={t.refId} view={null} kind="image">{tile}</DropSlot>
          ) : (
            <div key={t.id}>{tile}</div>
          );
        })}
      </div>
      {(hasKfTile || reads || kf.first || kf.last) && (
        <div className="h3-row h3-wrap">
          <button
            className="h3-btn"
            disabled={busyPrev || !prev}
            title={(prev
              ? `${shot}'s first frame = ${prev}'s last frame, from the take the ${pass} cut uses. A new candidate; it goes live if ${shot} has no first keyframe yet.`
              : `${shot} is the first shot of the ${pass} cut`) + (note ? `\nKeyframes are ${note}.` : "")}
            onClick={() => void keyframeFromTake({ shot, which: "first" })}
          >
            <i className={busyPrev ? "pi pi-spin pi-spinner" : "pi pi-link"} /> From previous{prev ? ` (${prev})` : ""}
          </button>
          {tiles.filter((t) => t.keyframe && t.refId).map((t) => (
            <UploadButton
              key={`up-${t.id}`}
              refId={t.refId!}
              view={null}
              kind="image"
              label={`Upload ${t.role === "last" ? "last" : "first"}…`}
              title={`Upload an image from this computer as ${shot}'s ${t.role} keyframe, live at once (or drop one on its tile)`}
            />
          ))}
          {KEYFRAME_ENDS.filter((end) => end === "first" || kf.last?.exists).map((end) => (
            <button
              key={end}
              className="h3-btn h3-danger"
              disabled={busyClear || !kf[end]?.exists}
              title={kf[end]?.exists ? `Remove ${shot}'s live ${end} keyframe (its candidates stay)` : `${shot} has no live ${end} keyframe`}
              onClick={() => void clearRef(keyframeRefId(shot, end))}
            >
              <i className="pi pi-times" /> Clear {end === "first" ? "keyframe" : "last keyframe"}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/** The reference sheet (or VACE reference) the selected take rendered with. */
function TakeReference({ d, take }: { d: ShotDetail; take: number | null }) {
  const ep = useApp((s) => s.ep);
  const t = d.takes.find((x) => x.take === take);
  const path = t?.reference_image;
  if (!ep || !t || !path) return null;
  const url = api().fileUrl(ep, path);
  return (
    <div className="h3-col" style={{ gap: 2 }}>
      <span className="h3-small h3-muted">{tn(t.take)} rendered with this reference image:</span>
      <a href={url} target="_blank" rel="noreferrer" title={`${path}\nOpen full size`}>
        <div className="h3-thumb" style={{ width: 192, aspectRatio: "16 / 9", backgroundImage: `url("${url}")`, backgroundSize: "contain" }} />
      </a>
    </div>
  );
}

const RECT_KEY = "h3pipe.inspector.rect";

/** What the server says about the shot's next render (an ignored prompt
 * override, an audio fallback), and why it can't render on its target. */
function RenderNotes({ d }: { d: ShotDetail }) {
  const notes = d.effective.notes ?? [];
  const err = d.effective.error;
  if (!notes.length && !err) return null;
  return (
    <div className="h3-col" style={{ gap: 2 }}>
      {err && <div className="h3-note h3-note-err h3-small">{err}</div>}
      {notes.length > 0 && (
        <div className="h3-note h3-note-info h3-small">
          <b>Next render:</b>
          <ul className="h3-missing-list">{notes.map((n) => <li key={n}>{n}</li>)}</ul>
        </div>
      )}
    </div>
  );
}

function Built({ d }: { d: ShotDetail }) {
  const { list } = useTargets();
  const b = d.built as Record<string, unknown>;
  const e = d.effective;
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
          {(e.target || d.target) && [
            <span key="tk">target</span>,
            <span key="tv" title={e.target || d.target || ""}>{targetLabel(list, e.target || d.target)}{d.built_target && (e.target || d.target) !== d.built_target ? ` (built for ${targetLabel(list, d.built_target)})` : ""}</span>,
          ]}
          {e.width && e.height ? [<span key="sk">size</span>, <span key="sv">{e.width}×{e.height}{e.length ? ` · ${e.length} frames` : ""}</span>] : null}
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
  const { list, seriesDefault } = useTargets();
  const target = shotTarget(d, seriesDefault);
  const pickers = useTargetPickers(target);
  const tgt = findTarget(list, target);
  const retargeted = isRetargeted(d);
  const promptLocked = retargeted
    ? {
        note: (
          <>
            This shot is retargeted from {targetLabel(list, d.built_target)} to <b>{targetLabel(list, target)}</b>. Per-pass prompt
            overrides are ignored for a retargeted shot (a prompt written for one model isn't valid for another), so
            this is the prompt {targetLabel(list, target)} writes for it, read-only.
            {ov.prompt != null && <> The saved {pass} prompt override is kept, unused, for when the shot goes back.</>}
          </>
        ),
        text: eff.prompt,
      }
    : null;
  const whose = retargeted ? "target's" : "built";

  return (
    <div className="h3-col">
      {d.override_stale && !retargeted && (
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
      <OverrideFields
        form={form}
        set={set}
        builtPrompt={d.built_prompt}
        promptOverridden={ov.prompt != null}
        showDiff={showDiff}
        setShowDiff={setShowDiff}
        seedPlaceholder={`${String(d.built.seed ?? eff.seed)} (built)`}
        seedTitle="Pinned seed for this shot (both passes). Empty = the built seed; a redo still picks a new one unless you choose same/typed."
        modelPlaceholder={`(${whose}) ${ov.model == null ? shortName(eff.model, 40) : ""}`}
        stepsPlaceholder={`${ov.steps == null ? eff.steps : ""} (${whose})`}
        effLoras={eff.loras}
        lorasOverridden={ov.loras != null}
        modelChoices={pickers.models}
        modelFiles={pickers.modelFiles}
        loraChoices={pickers.loras}
        promptLocked={promptLocked}
        negative={takesNegative(tgt, eff)
          ? {
              // what applies without a shot override (unknown while one is saved)
              effective: eff.negative_source !== "override" ? eff.negative ?? "" : "",
              source: eff.negative_source === "override" ? "" : negativeSourceLabel(eff.negative_source),
              note: negativeNoEffect(tgt?.presets?.[pass]?.cfg) ? "no effect at cfg ≤ 1 (turbo)" : null,
            }
          : null}
        modelLow={pickers.twoStage
          ? {
              placeholder: `(${whose}) ${ov.model_low == null ? shortName(eff.model_low ?? String(tgt?.presets?.[pass]?.model_low ?? ""), 40) : ""}`,
              files: pickers.modelLowFiles,
            }
          : null}
      />
      <label className="h3-check" title="Prompt, model, LoRAs and steps are per pass; this writes them to final and proxy. Seed, note and target are always shared.">
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
          title={`Remove this shot's ${pass} override (prompt, model, LoRAs, steps). Seed, note and target stay.`}
          onClick={() => confirm(`Remove ${shot}'s ${pass} override?`) && void revertOverride(shot, pass)}
        >
          Revert {pass}
        </button>
        <button
          className="h3-btn h3-danger"
          disabled={!hasOverride || busy}
          title="Remove every override of this shot, both passes, seed, note and target included"
          onClick={() => confirm(`Remove all of ${shot}'s overrides (both passes, seed, note and target)?`) && void revertOverride(shot, null)}
        >
          Revert all
        </button>
      </div>
      <div className="h3-row h3-wrap">
        <button
          className="h3-btn"
          disabled={!hasOverride || busy}
          title={hasOverride
            ? `Move what the script or series config can express (e.g. ${shot}'s target, model, LoRAs, steps) into them; the rest stays an override`
            : `${shot} has no override`}
          onClick={() => openPromote(shot)}
        >
          <i className="pi pi-upload" /> Promote…
        </button>
        <button className="h3-btn" title={`Open the script at ${shot}'s lines`} onClick={() => showInScript(shot)}>
          <i className="pi pi-file-edit" /> Show in script
        </button>
      </div>
      {dirty && <div className="h3-small h3-muted">Unsaved edits. Render and Redo use the saved override; the Redo dialog can save these for you.</div>}
    </div>
  );
}

/** The inspector's contents for the selected shot. */
export function Inspector() {
  const ep = useApp((s) => s.ep);
  const shot = useApp((s) => s.shot);
  const pass = useApp((s) => s.pass);
  const d = useDetail(shot);
  const derr = useDetailError(shot);
  const s = useShotStatus(shot);
  const selTake = useApp((st) => st.take);
  const [allowMissing, setAllowMissing] = useState(false);
  const renderBusy = useApp((st) => !!shot && Object.keys(st.busy).some((k) => k.startsWith(`render|${pass}|`) && k.split("|")[2].split(",").includes(shot)));

  useEffect(() => {
    if (shot && ep) void loadDetail(shot);
  }, [shot, ep, pass]);
  // "render anyway" is a per-shot decision
  useEffect(() => setAllowMissing(false), [shot]);

  const { list: targets, seriesDefault } = useTargets();
  const badges = useMemo(() => {
    if (!s) return [];
    return [...targetBadges(s, targets, seriesDefault, cutTake(s)), ...shotBadges(s)];
  }, [s, targets, seriesDefault]);

  if (!ep) return <div className="h3-empty-state">Pick an episode in the h3 Shots tab.</div>;
  if (!shot) return <div className="h3-empty-state">Select a shot in the Shots tab or the timeline.</div>;
  const ct = s ? cutTake(s) : undefined;
  const hasUsable = !!s?.takes.some((t) => t.status === "ok" && t.has_video);
  const missing = s ? missingOf(s) : [];
  const blocked = missing.length && !allowMissing;
  return (
    <div className="h3-scroll h3-pad h3-col">
      {s && (
        <div className="h3-col" style={{ gap: 4 }}>
          {s.subjects.length > 0 && <div className="h3-small h3-muted">subjects: {s.subjects.join(", ")}</div>}
          <Badges badges={badges} />
          <TargetPicker d={d} shot={shot} />
          {d && <TakeResolved d={d} take={selTake ?? ct?.take ?? null} />}
          {d && <TakeReference d={d} take={selTake ?? ct?.take ?? null} />}
          {d && <RenderNotes d={d} />}

          <MissingRefsNote blocked={missing.length ? [{ shot, refs: missing }] : []} allow={allowMissing} setAllow={setAllowMissing} />
          <RefsUsed shot={shot} d={d} />
          <div className="h3-row h3-wrap">
            <button
              className="h3-btn h3-primary"
              disabled={renderBusy || !d}
              title={blocked
                ? "Missing refs: the server will skip this shot (tick Render anyway to render with stand-ins)"
                : hasUsable ? "Queue a redo with the saved override and a new seed" : "Queue the first take"}
              onClick={() => {
                if (!hasUsable) void renderShots([shot], false, allowMissing);
                else void queueRender({ ...baseRender(ep, pass, [shot], allowMissing), redo: true, seed_mode: "new", parent_take: ct?.take ?? null });
              }}
            >
              <i className="pi pi-play" /> {hasUsable ? "Redo (new seed)" : "Render"}{blocked ? " (will skip)" : ""}
            </button>
            <button className="h3-btn" disabled={!d} onClick={() => openRedo(shot, ct?.take ?? s.takes[s.takes.length - 1]?.take ?? null)}>
              <i className="pi pi-refresh" /> Redo…
            </button>
            <button className="h3-btn" disabled={!ct?.mp4} onClick={() => ct && openViewer(shot, ct.take, null, "single")}>
              <i className="pi pi-eye" /> View {ct ? tn(ct.take) : ""}
            </button>
          </div>
          <CutSection shot={shot} />
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
  );
}

/** The floating inspector window (drag by the head, resize from the corner). */
export function InspectorWindow() {
  const open = useApp((s) => s.inspector);
  const ep = useApp((s) => s.ep);
  const shot = useApp((s) => s.shot);
  const s = useShotStatus(shot);
  if (!open) return null;
  const head = (
    <>
      <i className="pi pi-sliders-h h3-muted" />
      <b>{shot ?? "Inspector"}</b>
      {s && (
        <span className="h3-muted h3-small" title={lengthEstimated(s) ? ESTIMATE_TITLE : undefined}>
          {s.sequence} · {fmtShotSeconds(s, s.seconds)}{s.size ? ` · ${s.size}` : ""}
        </span>
      )}
      <span className="h3-grow" />
      {ep && <PassToggle />}
      <button className="h3-btn h3-icon" title="Close" onClick={closeInspector}><i className="pi pi-times" /></button>
    </>
  );
  return (
    <FloatingWindow storageKey={RECT_KEY} defaultRect={defaultInspectorRect} head={head} className="h3-inspector" minW={300}>
      <Inspector />
    </FloatingWindow>
  );
}
