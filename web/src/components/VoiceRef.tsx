// Phase 9c-B: voice refs. A voice row generates samples with the episode's
// audio target (count, prompt, seconds), lists its candidates with a player
// and the waveform (GET /h3pipe/peaks, the same drawing as the timeline's
// lane), and can take a line straight out of a shot take's sound.

import { useEffect, useMemo, useRef, useState, type PointerEvent as RPointerEvent } from "react";
import { closeVoiceClip, generateRef, openVoiceClip, updateVoiceClip, voiceFromTake } from "../actions";
import { api } from "../host";
import { MAX_BINS } from "../lib/peaks";
import { fmtSeconds, tn } from "../lib/format";
import { takesOf } from "../lib/refs";
import { findTarget, targetLabel } from "../lib/targets";
import {
  MIN_VOICE_SECONDS, SPAN_MAX, SPAN_MIN, candidateText, clampSpan, defaultVoiceSeconds, fromTakeText, lineSourceText,
  maxVoiceSeconds, moveEdge, spanBox, spanProblem, spanText, takesWithAudio, voiceCapNotes, voiceModeText, xToTime,
  type Span,
} from "../lib/voice";
import { statusKey, useApp } from "../store";
import type { Ref, RefTake } from "../types";
import { Dialog } from "./Dialogs";
import { useTargets } from "./Targets";
import { PeaksCanvas, usePeaks, useSeen } from "./Waveform";

const WAVE_H = 34;
const CLIP_WAVE_H = 72;

/** Peaks for a whole audio file in a fixed-width box (one bin a pixel). */
function useFileWave(ep: string | null, path: string | null | undefined, width: number, enabled: boolean, version?: string | null) {
  const q = useMemo(
    () => (ep && path ? { ep, path, bins: Math.min(MAX_BINS, Math.max(16, Math.round(width))), version: version ?? null } : null),
    [ep, path, width, version],
  );
  return usePeaks(q, enabled);
}

/** A waveform of one audio file, drawn as the timeline's lane draws its clips. */
export function VoiceWave({ ep, path, width, height = WAVE_H, version, className, children }: {
  ep: string; path: string; width: number; height?: number; version?: string | null; className?: string;
  children?: React.ReactNode;
}) {
  const box = useRef<HTMLDivElement>(null);
  const seen = useSeen(box);
  const entry = useFileWave(ep, path, width, seen, version);
  const peaks = entry?.ok ? entry.data.peaks : EMPTY;
  const title = entry && !entry.ok ? entry.error : entry?.ok && entry.data.silent ? `${path}: no audio stream` : path;
  return (
    <div ref={box} className={`h3-wave h3-voice-wave${className ? ` ${className}` : ""}`} style={{ width, height }} title={title}>
      <PeaksCanvas peaks={peaks} width={width} height={height} />
      {!entry && seen && <span className="h3-wave-note">…</span>}
      {entry && !entry.ok && <span className="h3-wave-note h3-err">!</span>}
      {children}
    </div>
  );
}

const EMPTY: number[] = [];

/** How long a file is, from its peaks answer (null until they arrive). */
function useFileSeconds(ep: string | null, path: string | null | undefined): number | null {
  const entry = useFileWave(ep, path, 64, true);
  return entry?.ok && entry.data.duration > 0 ? entry.data.duration : null;
}

/** One voice candidate: play it, see how long it is and what it says. */
export function VoiceCandidate({ ep, t, live, selected, onClick }: {
  ep: string; r: Ref; t: RefTake; live: boolean; selected: boolean; onClick: (e: React.MouseEvent) => void;
}) {
  const file = t.audio ?? null;
  const url = file ? api().refFileUrl(ep, file, t.finished ?? null) : null;
  const ok = t.status === "ok" && !!url;
  const title = [
    `${tn(t.take)} · ${t.status}${live ? " · live" : ""} · ${t.source}${t.original_name ? ` (${t.original_name})` : ""}`,
    t.line ? `says: “${t.line}”${t.line_source ? ` (${lineSourceText(t.line_source)})` : ""}` : "",
    fromTakeText(t) ? `cut out of ${fromTakeText(t)}` : "",
    t.seed ? `seed ${t.seed}` : "",
    t.note ? `“${t.note}”` : "",
    t.save_notes && t.status === "failed" ? t.save_notes : "",
  ].filter(Boolean).join("\n");
  return (
    <div className={`h3-audio-take${selected ? " h3-sel" : ""}${live ? " h3-live" : ""}`} onClick={onClick} title={title}>
      <span className="h3-row">
        <b>{tn(t.take)}</b>
        {live && <span className="h3-badge h3-b-cut">live</span>}
        <span className="h3-muted h3-small">{t.status === "ok" ? candidateText(t) || t.source : t.status}</span>
        <span className="h3-grow" />
        {t.source === "from_take" && <span className="h3-badge" title={`cut out of ${fromTakeText(t)}`}>from {fromTakeText(t).split(" ")[0]}</span>}
        {t.target && <span className="h3-muted h3-small">{t.target}</span>}
      </span>
      {ok && file && (
        <div className="h3-row" style={{ gap: 6 }}>
          <VoiceWave ep={ep} path={file} width={180} version={t.finished ?? null} />
          <audio controls preload="none" src={url!} onClick={(e) => e.stopPropagation()} />
        </div>
      )}
      {t.status === "failed" && t.save_notes && <span className="h3-small h3-err">{t.save_notes}</span>}
    </div>
  );
}

/** The live voice sample the renders read: its waveform and a player. */
export function VoiceLive({ ep, r }: { ep: string; r: Ref }) {
  if (!r.path || !r.exists) return null;
  return (
    <div className="h3-row" style={{ gap: 6 }}>
      <VoiceWave ep={ep} path={r.path} width={200} version={r.sha1 ?? null} />
      <audio controls preload="none" src={api().refFileUrl(ep, r.path, r.sha1)} />
      <span className="h3-muted h3-small h3-ell">{r.path}</span>
    </div>
  );
}

/**
 * Generate voice samples: how many, how long, and what the speaker says. The
 * prompt starts as the brief the target would write (the character's `voice`
 * line, their design, and a line to say).
 */
export function VoiceGenerateBar({ r }: { r: Ref }) {
  const busy = useApp((s) => !!s.busy[`refgen|${r.id}`] || !!s.busy[`refimport|${r.id}`]);
  const { list } = useTargets();
  const target = findTarget(list, r.effective?.target ?? undefined);
  const max = Math.round(maxVoiceSeconds(r, target));
  const [count, setCount] = useState(1);
  const [seconds, setSeconds] = useState(() => defaultVoiceSeconds(r, target));
  const [prompt, setPrompt] = useState("");
  const [open, setOpen] = useState(false);
  const eff = r.effective;
  const line = eff?.line ?? "";
  return (
    <div className="h3-col" style={{ gap: 4 }}>
      <div className="h3-row h3-wrap h3-genbar">
        <select className="h3-in" value={count} onChange={(e) => setCount(Number(e.target.value))} title="How many samples (each with its own seed)">
          {[1, 2, 3, 4].map((n) => <option key={n} value={n}>{n}×</option>)}
        </select>
        <label className="h3-row h3-small" title={`How long the sample is, ${MIN_VOICE_SECONDS}–${max} s on ${targetLabel(list, eff?.target ?? "")}. The target snaps it onto its frame grid.`}>
          <input
            className="h3-in"
            style={{ width: 58 }}
            type="number"
            min={MIN_VOICE_SECONDS}
            max={max}
            step={1}
            value={seconds}
            onChange={(e) => setSeconds(Number(e.target.value))}
          />
          <span className="h3-muted">s</span>
        </label>
        <button
          className="h3-btn h3-primary"
          disabled={busy || !(seconds > 0)}
          title={`Queue ${count > 1 ? `${count} samples` : "a sample"} of about ${seconds} s with ${targetLabel(list, eff?.target ?? "")}`}
          onClick={() => void generateRef({
            ref: r.id, view: null, count, seed_mode: "auto", seed: null,
            prompt: prompt.trim() || null, model: null, loras: null, steps: null, note: "", seconds,
          })}
        >
          <i className={busy ? "pi pi-spin pi-spinner" : "pi pi-sparkles"} /> Generate {count > 1 ? `${count} samples` : "a sample"}
        </button>
        <button
          className="h3-btn"
          title="Cut a line this character already speaks in a take out of its sound, and use that as the sample (no model runs)"
          onClick={() => openVoiceClip(r.id)}
        >
          <i className="pi pi-scissors" /> Use a line from a take…
        </button>
        <button className="h3-link" onClick={() => setOpen(!open)}>{open ? "hide the line" : "the line it says…"}</button>
      </div>
      {open && (
        <div className="h3-col" style={{ gap: 2 }}>
          <span className="h3-small h3-muted">
            {line ? <>It says <b>“{line}”</b>{eff?.line_source ? ` — ${lineSourceText(eff.line_source)}` : ""}.</> : "The target writes the line."}
            {" "}Type your own below to say something else (it is recorded with the candidate).
          </span>
          <textarea
            className="h3-in"
            rows={3}
            placeholder={line || "the line to say"}
            value={prompt}
            spellCheck={false}
            onChange={(e) => setPrompt(e.target.value)}
          />
          {eff?.prompt && (
            <details>
              <summary className="h3-small h3-muted">the whole brief the target gets</summary>
              <pre className="h3-pre h3-small" style={{ maxHeight: 160 }}>{eff.prompt}</pre>
            </details>
          )}
          {target && (
            <span className="h3-small h3-muted">
              {targetLabel(list, target.id)}: {voiceModeText(target)}. {voiceCapNotes(target).join(" ")}
            </span>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// "Use a line from a take…"
// ---------------------------------------------------------------------------

const CLIP_W = 620;

/** The waveform of one take, with a draggable span over it. */
function SpanWave({ ep, path, duration, span, setSpan, playAt }: {
  ep: string; path: string; duration: number | null; span: Span; setSpan: (s: Span) => void; playAt: number | null;
}) {
  const box = useRef<HTMLDivElement>(null);
  const entry = useFileWave(ep, path, CLIP_W, true);
  const dur = duration ?? (entry?.ok ? entry.data.duration : 0) ?? 0;
  const peaks = entry?.ok ? entry.data.peaks : EMPTY;
  const at = (clientX: number): number => {
    const r = box.current?.getBoundingClientRect();
    if (!r) return 0;
    return xToTime(clientX - r.left, r.width, dur);
  };
  const drag = (e: RPointerEvent<HTMLDivElement>, edge: "start" | "end" | null) => {
    if (e.button !== 0 || !(dur > 0)) return;
    e.preventDefault();
    e.stopPropagation();
    let cur = edge ? span : clampSpan(at(e.clientX), at(e.clientX) + SPAN_MIN, dur);
    if (!edge) setSpan(cur);
    const move = (ev: PointerEvent) => {
      cur = edge ? moveEdge(cur, edge, at(ev.clientX), dur) : clampSpan(cur.start, at(ev.clientX), dur);
      setSpan(cur);
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };
  const b = spanBox(span, CLIP_W, dur || 1);
  return (
    <div
      ref={box}
      className="h3-wave h3-span-wave"
      style={{ width: CLIP_W, height: CLIP_WAVE_H }}
      title="Drag to choose the span; drag its edges to fine-tune"
      onPointerDown={(e) => drag(e, null)}
    >
      <PeaksCanvas peaks={peaks} width={CLIP_W} height={CLIP_WAVE_H} />
      {!entry && <span className="h3-wave-note">…</span>}
      {entry && !entry.ok && <span className="h3-wave-note h3-err">{entry.error}</span>}
      {dur > 0 && (
        <div className="h3-span" style={{ left: b.left, width: b.width }}>
          <div className="h3-span-h h3-l" onPointerDown={(e) => drag(e, "start")} />
          <div className="h3-span-h h3-r" onPointerDown={(e) => drag(e, "end")} />
        </div>
      )}
      {playAt != null && dur > 0 && <div className="h3-playhead" style={{ left: (playAt / dur) * CLIP_W }} />}
    </div>
  );
}

export function VoiceClipWindow() {
  const v = useApp((s) => s.voiceClip);
  const ep = useApp((s) => s.ep);
  if (!v || !ep) return null;
  return <VoiceClipBody key={v.ref} ep={ep} />;
}

function VoiceClipBody({ ep }: { ep: string }) {
  const v = useApp((s) => s.voiceClip)!;
  const refs = useApp((s) => s.refs[ep]);
  const st = useApp((s) => s.status[statusKey(ep, v.pass)]);
  const busy = useApp((s) => !!s.busy[`voiceclip|${v.ref}`]);
  const r = refs?.find((x) => x.id === v.ref);
  const audio = useRef<HTMLAudioElement>(null);
  const [span, setSpan] = useState<Span>({ start: 0, end: 3 });
  const [playAt, setPlayAt] = useState<number | null>(null);

  // the shots whose take in this pass has sound; the character's own first
  const shots = useMemo(() => (st?.shots ?? []).filter((s) => takesWithAudio(s.takes).length), [st]);
  const mine = useMemo(
    () => (r?.subject ? shots.filter((s) => s.subjects.includes(r.subject!)) : []),
    [shots, r?.subject],
  );
  const shot = v.shot && shots.some((s) => s.shot === v.shot) ? v.shot : (mine[0]?.shot ?? shots[0]?.shot ?? null);
  const shotSt = shots.find((s) => s.shot === shot);
  const takes = shotSt ? takesWithAudio(shotSt.takes) : [];
  const take = takes.find((t) => t.take === v.take) ?? takes.find((t) => t.take === shotSt?.cut.take) ?? takes[takes.length - 1];
  const path = take?.audio ?? null;
  const duration = useFileSeconds(ep, path);

  // a new file: start with a span at the top of it
  useEffect(() => {
    setSpan(clampSpan(0, Math.min(3, duration ?? 3), duration));
    setPlayAt(null);
  }, [path, duration]);

  const problem = spanProblem(span, duration);
  const preview = () => {
    const a = audio.current;
    if (!a) return;
    a.currentTime = span.start;
    void a.play();
  };
  const onTime = () => {
    const a = audio.current;
    if (!a) return;
    setPlayAt(a.currentTime);
    if (a.currentTime >= span.end) {
      a.pause();
      setPlayAt(null);
    }
  };

  return (
    <Dialog
      title={<>A line from a take as {r?.name ?? v.ref} <span className="h3-muted h3-small">{v.pass}</span></>}
      onClose={closeVoiceClip}
      wide
      footer={
        <>
          <span className="h3-muted h3-small h3-grow">{problem ?? `${spanText(span)} of ${shot ?? ""} ${take ? tn(take.take) : ""}`}</span>
          <button className="h3-btn" onClick={closeVoiceClip}>Cancel</button>
          <button
            className="h3-btn h3-primary"
            disabled={!!problem || !shot || !take || busy}
            title={r?.exists
              ? "Add it as a candidate (the live sample stays until you pick it)"
              : "Add it as a candidate; with no sample yet it goes live at once — and series.json gains the voice_sample line"}
            onClick={() => {
              if (!shot || !take) return;
              void voiceFromTake({ ref: v.ref, shot, take: take.take, pass: v.pass, start: span.start, end: span.end })
                .then((ok) => ok && closeVoiceClip());
            }}
          >
            <i className={busy ? "pi pi-spin pi-spinner" : "pi pi-check"} /> Use this line
          </button>
        </>
      }
    >
      {!shots.length ? (
        <div className="h3-note">No {v.pass} take has any sound yet. Render one (or switch pass) and try again.</div>
      ) : (
        <>
          <div className="h3-row h3-wrap">
            <label className="h3-row h3-small">
              <span className="h3-muted">Shot</span>
              <select className="h3-in" value={shot ?? ""} onChange={(e) => updateVoiceClip({ shot: e.target.value, take: null })}>
                {mine.length > 0 && (
                  <optgroup label={`${r?.name ?? "this character"} speaks`}>
                    {mine.map((s) => <option key={s.shot} value={s.shot}>{s.shot}{s.subjects.length ? ` · ${s.subjects.join(", ")}` : ""}</option>)}
                  </optgroup>
                )}
                <optgroup label="every shot with sound">
                  {shots.map((s) => <option key={s.shot} value={s.shot}>{s.shot}{s.subjects.length ? ` · ${s.subjects.join(", ")}` : ""}</option>)}
                </optgroup>
              </select>
            </label>
            <label className="h3-row h3-small">
              <span className="h3-muted">Take</span>
              <select className="h3-in" value={take?.take ?? ""} onChange={(e) => updateVoiceClip({ take: Number(e.target.value) })}>
                {takes.map((t) => <option key={t.take} value={t.take}>{tn(t.take)}{shotSt?.cut.take === t.take ? " (the cut's)" : ""}</option>)}
              </select>
            </label>
            <span className="h3-muted h3-small">
              {duration != null ? `${fmtSeconds(duration)} of sound` : "reading the sound…"}
            </span>
            <span className="h3-grow" />
            <button className="h3-btn" disabled={!path || !!problem} title="Play just the span" onClick={preview}>
              <i className="pi pi-play" /> Preview the span
            </button>
          </div>
          {path && (
            <>
              <SpanWave ep={ep} path={path} duration={duration} span={span} setSpan={setSpan} playAt={playAt} />
              <audio ref={audio} src={api().fileUrl(ep, path)} preload="metadata" onTimeUpdate={onTime} onPause={() => setPlayAt(null)} style={{ display: "none" }} />
            </>
          )}
          <div className="h3-row h3-wrap h3-small">
            <label className="h3-row">
              <span className="h3-muted">from</span>
              <input
                className="h3-in" style={{ width: 80 }} type="number" step={0.05} min={0}
                value={span.start}
                onChange={(e) => setSpan(clampSpan(Number(e.target.value), span.end, duration))}
              />
            </label>
            <label className="h3-row">
              <span className="h3-muted">to</span>
              <input
                className="h3-in" style={{ width: 80 }} type="number" step={0.05} min={0}
                value={span.end}
                onChange={(e) => setSpan(clampSpan(span.start, Number(e.target.value), duration))}
              />
            </label>
            <span className="h3-muted">seconds · {SPAN_MIN}s to {SPAN_MAX}s</span>
          </div>
          {problem && <div className="h3-note h3-note-err h3-small">{problem}</div>}
          <div className="h3-muted h3-small">
            ffmpeg cuts that span out of the take's sound into a 16-bit wav; no model runs. The candidate records the shot,
            take and span, so you can find it again.
          </div>
        </>
      )}
    </Dialog>
  );
}

/** The candidates of a voice ref, newest first (used by the Refs tab). */
export function voiceTakes(r: Ref): RefTake[] {
  return [...takesOf(r, null)].reverse();
}
