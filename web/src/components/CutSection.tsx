// Phase 9b: the inspector's Cut section: where the shot sits in the pass's
// cut, its lock, and its trims as numbers, over the take's strip with the
// trimmed parts dimmed.

import { useEffect, useState } from "react";
import { currentPlaylist, openClipAudio } from "../actions";
import { clearClipAudio, setTrims, toggleLock } from "../cutActions";
import { api } from "../host";
import { GAIN_DEFAULT, RECORDING_IGNORES_AUDIO, audioOf, gainOf, offsetOf, startOf } from "../lib/audioSource";
import { framesLabel, isOutOfOrder, trimLimits } from "../lib/cutEdit";
import { fmtSeconds, tn } from "../lib/format";
import { clipTake } from "../lib/playlist";
import { useApp } from "../store";
import type { ShotStatus } from "../types";
import { AudioBadge } from "./ClipAudio";
import { useStatus } from "./hooks";

/** A whole number of frames typed in a field: the number, or null when it isn't one. */
export function parseFrames(text: string): number | null {
  const t = text.trim();
  if (!/^\d+$/.test(t)) return null;
  const n = Number(t);
  return Number.isSafeInteger(n) ? n : null;
}

function FramesField({ label, value, max, disabled, fps, onCommit }: {
  label: string; value: number; max: number; disabled: boolean; fps: number; onCommit: (n: number) => void;
}) {
  const [text, setText] = useState(String(value));
  useEffect(() => setText(String(value)), [value]);
  const n = parseFrames(text);
  const bad = n == null || n > max;
  const commit = () => {
    if (n == null) return setText(String(value));
    if (n !== value) onCommit(Math.min(n, max));
  };
  return (
    <label className="h3-row h3-cut-field" title={`Frames dropped from the ${label === "in" ? "head" : "tail"} (at most ${Number.isFinite(max) ? max : "—"}: at least one frame stays)`}>
      <span className="h3-muted">trim {label}</span>
      <input
        type="text"
        inputMode="numeric"
        className={`h3-in h3-mono${bad ? " h3-bad" : ""}`}
        style={{ width: 56 }}
        value={text}
        disabled={disabled}
        onChange={(e) => setText(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") commit();
          if (e.key === "Escape") setText(String(value));
          e.stopPropagation();
        }}
      />
      <span className="h3-muted h3-small">f · {(Math.min(n ?? value, Number.isFinite(max) ? max : n ?? value) / fps).toFixed(2)} s</span>
    </label>
  );
}

export function CutSection({ shot }: { shot: string }) {
  const st = useStatus();
  const other = useStatus(st?.pass === "proxy" ? "final" : "proxy");
  const ep = useApp((s) => s.ep);
  const items = useApp((s) => currentPlaylist(s));
  if (!st || !ep) return null;
  const s = st.shots.find((x) => x.shot === shot);
  if (!s) return null;
  const fps = st.fps || 24;
  const it = items.find((x) => x.shot === shot);
  const idx = st.shots.indexOf(s);
  const locked = !!s.cut.locked;
  const a = s.cut.trim_in || 0;
  const b = s.cut.trim_out || 0;
  const total = it?.total ?? null;
  const { maxIn, maxOut } = trimLimits(total, a, b);
  const take = clipTake(s, s.cut.placeholder ? other : undefined);
  const strip = take?.strip ? api().fileUrl(ep, take.strip) : take?.thumb ? api().fileUrl(ep, take.thumb) : null;
  const moved = isOutOfOrder(st, shot);
  const pct = (n: number) => (total && total > 0 ? `${Math.min(100, (n / total) * 100)}%` : "0%");
  const canTrim = !locked && !s.orphan && total != null;
  return (
    <div className="h3-col h3-cut-sec" style={{ gap: 4 }}>
      <div className="h3-row h3-wrap">
        <span className="h3-h">Cut</span>
        <span className="h3-small h3-muted">
          {s.orphan ? "orphan (no longer in the script)" : `#${idx + 1} of ${st.shots.length}`}
          {moved ? " · moved (out of script order)" : ""}
          {take ? ` · ${s.cut.placeholder ? `${s.cut.pass} ` : ""}${tn(take.take)}${s.cut.picked ? " (picked)" : " (latest)"}` : " · no take"}
        </span>
        <span className="h3-grow" />
        <button
          className={`h3-btn${locked ? " h3-on" : ""}`}
          title={locked ? "Unlock: it can be moved, trimmed and re-picked again" : "Lock: no moves, trims or re-picks until unlocked"}
          onClick={() => void toggleLock(shot)}
        >
          <i className={locked ? "pi pi-lock" : "pi pi-lock-open"} /> {locked ? "Locked" : "Lock"}
        </button>
      </div>
      {total != null && (
        <div className="h3-cut-strip" style={{ backgroundImage: strip ? `url("${strip}")` : undefined }} title={`The whole take; the dimmed parts are trimmed off (${framesLabel(a, fps)} head, ${framesLabel(b, fps)} tail)`}>
          <div className="h3-trim-dim" style={{ left: 0, width: pct(a) }} />
          <div className="h3-trim-dim" style={{ right: 0, width: pct(b) }} />
        </div>
      )}
      <div className="h3-row h3-wrap">
        <FramesField label="in" value={a} max={maxIn} fps={fps} disabled={!canTrim} onCommit={(n) => void setTrims(shot, n, b, `Trim ${shot} in`)} />
        <FramesField label="out" value={b} max={maxOut} fps={fps} disabled={!canTrim} onCommit={(n) => void setTrims(shot, a, n, `Trim ${shot} out`)} />
        {(a > 0 || b > 0) && (
          <button className="h3-btn" disabled={!canTrim} onClick={() => void setTrims(shot, 0, 0, `Clear ${shot} trims`)}>Clear</button>
        )}
      </div>
      <ClipAudioLine shot={shot} cut={s.cut} locked={locked} />
      <span className="h3-small h3-muted">
        {it ? `plays ${fmtSeconds(it.dur)}${total != null ? ` of ${fmtSeconds(total / fps)}` : ""}${it.audioIn != null ? ` · dialogue window ${it.audioIn.toFixed(2)}–${(it.audioOut ?? 0).toFixed(2)} s` : ""}` : "not in Play all"}
        {locked ? " · locked: unlock to trim, move or re-pick" : ""}
      </span>
    </div>
  );
}

/**
 * Phase 9d: what this clip plays. Its own take's sound needs no line beyond
 * the button; an audio source shows where it comes from, how it sits, and
 * clears back to its own.
 */
function ClipAudioLine({ shot, cut, locked }: { shot: string; cut: ShotStatus["cut"]; locked: boolean }) {
  const recording = useApp((s) => s.cutAudio === "recording");
  const a = audioOf(cut);
  const bits = a && a.source !== "none"
    ? [
      startOf(a) ? `from ${startOf(a).toFixed(2)} s in` : "",
      offsetOf(a) ? `${offsetOf(a) > 0 ? "+" : ""}${offsetOf(a).toFixed(2)} s against the picture` : "",
      gainOf(a) !== GAIN_DEFAULT ? `gain ${gainOf(a)}` : "",
    ].filter(Boolean)
    : [];
  return (
    <div className="h3-row h3-wrap h3-small">
      <span className="h3-muted">audio</span>
      {a ? (
        <>
          <AudioBadge shot={shot} cut={cut} dim={recording} />
          {bits.length > 0 && <span className="h3-muted">{bits.join(" · ")}</span>}
          {cut.audio_file && <span className="h3-muted h3-mono h3-ell" title={cut.audio_file}>{cut.audio_file}</span>}
        </>
      ) : (
        <span className="h3-muted">its own take's sound</span>
      )}
      <span className="h3-grow" />
      <button className="h3-btn" title={`Pick where ${shot}'s sound comes from`} onClick={() => openClipAudio(shot)}>
        <i className="pi pi-volume-up" /> Audio from…
      </button>
      {a && (
        <button
          className="h3-btn"
          disabled={locked}
          title={locked ? `${shot} is locked in the cut` : `${shot} back to its own take's sound`}
          onClick={() => void clearClipAudio(shot)}
        >
          Clear
        </button>
      )}
      {a && recording && <span className="h3-muted" title={RECORDING_IGNORES_AUDIO}>(ignored while Play all is on the recording)</span>}
    </div>
  );
}
