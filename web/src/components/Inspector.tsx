// The inspector: a floating window (round 2; it was a sidebar tab) showing the
// selected shot. It follows the selection while open.

import { useEffect, useMemo, useRef, useState } from "react";
import {
  baseRender, closeInspector, keyframeFromTake, loadDetail, loadRefs, openKeyframesInRefs, openRedo, openViewer, queueRender,
  renderShots, revertOverride, saveOverride, setShotTarget,
} from "../actions";
import { errText } from "../api";
import { api, host } from "../host";
import { cutTake, fmtSeconds, shortName, shotBadges, tn } from "../lib/format";
import { KEYFRAME_ENDS, cutNeighbour, keyframeNote, keyframeSource, liveTake, shotKeyframes } from "../lib/keyframes";
import { missingOf } from "../lib/missingRefs";
import { formFromDetail, isDirty, overrideFields, type OverrideForm } from "../lib/overrideForm";
import { isRetargeted, retargetNote, shotTarget, targetBadges, targetLabel } from "../lib/targets";
import { useApp } from "../store";
import type { ShotDetail } from "../types";
import { FloatingWindow, defaultInspectorRect } from "./FloatingWindow";
import { aspectOf, useDetail, useDetailError, useShotStatus, useStatus } from "./hooks";
import { MissingRefsNote } from "./MissingRefs";
import { OverrideFields } from "./OverrideFields";
import { PassToggle } from "./ShotsTab";
import { TargetSelect, useTargetPickers, useTargets } from "./Targets";
import { Badges } from "./Thumb";

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
        {isRetargeted(src) && (
          <button className="h3-btn" disabled={busy} title={`Back to ${targetLabel(list, built)}, the target the build compiled this shot for`} onClick={() => void setShotTarget(shot, null, built)}>
            Revert target
          </button>
        )}
      </div>
      {note && <span className="h3-small h3-muted">{note}</span>}
    </div>
  );
}

/**
 * The shot's first / last keyframes (refs `shot:<id>:first|last`), with the
 * continuity action. There is no route to unpick or delete a ref take yet, so
 * no "Clear" (see api.ts).
 */
function Keyframes({ shot }: { shot: string }) {
  const ep = useApp((s) => s.ep);
  const pass = useApp((s) => s.pass);
  const refs = useApp((s) => (s.ep ? s.refs[s.ep] : undefined));
  const busy = useApp((s) => !!s.busy[`keyframe|${shot}|first`]);
  const st = useStatus();
  const s = useShotStatus(shot);
  const { list, seriesDefault } = useTargets();
  useEffect(() => {
    if (ep && !refs) void loadRefs(ep);
  }, [ep, refs]);
  if (!ep) return null;
  const kf = shotKeyframes(refs, shot);
  const prev = cutNeighbour(st, shot, -1);
  const note = keyframeNote(list, shotTarget(s, seriesDefault));
  return (
    <div className="h3-col" style={{ gap: 4 }}>
      <div className="h3-row">
        <span className="h3-h">Keyframes</span>
        {note && <span className="h3-small h3-muted" title="Whether this shot's target reads keyframes (GET /h3pipe/targets capabilities)">{note}</span>}
      </div>
      <div className="h3-row h3-wrap" style={{ alignItems: "flex-start" }}>
        {KEYFRAME_ENDS.map((end) => {
          const r = kf[end];
          const t = liveTake(r);
          const from = keyframeSource(t);
          const url = r?.exists && r.path ? api().refFileUrl(ep, r.path, r.sha1) : null;
          return (
            <div key={end} className="h3-col" style={{ gap: 2, width: 128 }}>
              <div
                className={`h3-thumb${url ? "" : " h3-empty"}`}
                style={{ width: 128, aspectRatio: aspectOf(st), ...(url ? { backgroundImage: `url("${url}")`, backgroundSize: "contain" } : {}) }}
                title={r?.path ?? `refs/shots/${shot}/${end}.png`}
              >
                {!url && <span className="h3-muted h3-small">{r ? "not picked" : "none"}</span>}
                <span className="h3-thumb-label">{end}{t ? ` ${tn(t.take)}` : ""}</span>
              </div>
              <span className="h3-small h3-muted h3-ell" title={from ?? ""}>{from ? `← ${from}` : r && t ? t.source : ""}</span>
            </div>
          );
        })}
      </div>
      <div className="h3-row h3-wrap">
        <button
          className="h3-btn"
          disabled={busy || !prev}
          title={prev
            ? `${shot}'s first frame = ${prev}'s last frame, from the take the ${pass} cut uses. A new candidate; it goes live if ${shot} has no first keyframe yet.`
            : `${shot} is the first shot of the ${pass} cut`}
          onClick={() => void keyframeFromTake({ shot, which: "first" })}
        >
          <i className={busy ? "pi pi-spin pi-spinner" : "pi pi-link"} /> From previous shot{prev ? ` (${prev})` : ""}
        </button>
        <button className="h3-btn" title="The keyframes' candidates, pick and import" onClick={() => openKeyframesInRefs(shot)}>
          <i className="pi pi-palette" /> Open in Refs
        </button>
      </div>
    </div>
  );
}

const RECT_KEY = "h3pipe.inspector.rect";

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
        loraChoices={pickers.loras}
        promptLocked={promptLocked}
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
          <MissingRefsNote blocked={missing.length ? [{ shot, refs: missing }] : []} allow={allowMissing} setAllow={setAllowMissing} />
          <Keyframes shot={shot} />
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
      {s && <span className="h3-muted h3-small">{s.sequence} · {fmtSeconds(s.seconds)}{s.size ? ` · ${s.size}` : ""}</span>}
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
