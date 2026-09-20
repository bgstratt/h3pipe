// Phase 9d: "Audio from…" — a clip's picture with someone else's sound.
// The window offers the clip's own audio, any shot's take with sound, a file
// inside the episode (browse or upload), or silence; draws both waveforms with
// the clip's picture length marked against the source; and moves `start` and
// `offset` by dragging or by typing. Saving goes through the ordinary cut-edit
// path (PUT /h3pipe/cut), so it is optimistic, undoable and in the history.

import {
  useCallback, useEffect, useMemo, useRef, useState,
  type KeyboardEvent as RKeyboardEvent, type PointerEvent as RPointerEvent,
} from "react";
import {
  browseClipAudio, closeClipAudio, currentPlaylist, openClipAudio, setClipAudioDraft, uploadClipAudio,
} from "../actions";
import { setClipAudio } from "../cutActions";
import { api } from "../host";
import {
  GAIN_DEFAULT, GAIN_MAX, GAIN_MIN, audioBadgeOf, audioLayout, audioNote, audioOf, audioProblem, audioShot, audioWhy,
  baseName, clampGain, clampOffset, clampStart, clipBox, gainOf, offsetOf, pictureBox, previewAt, r3, resolveAudioFile,
  sameAudio, soundShots, startOf, takeAudioFile, takesWithSound, timeToX, xToTime, RECORDING_IGNORES_AUDIO,
} from "../lib/audioSource";
import { fmtSeconds, tn } from "../lib/format";
import { MAX_BINS } from "../lib/peaks";
import { clipTake } from "../lib/playlist";
import { needsResync } from "../lib/recording";
import { useApp } from "../store";
import type { CutAudioKind, CutAudioSource, EpisodeStatus, Pass, ShotStatus } from "../types";
import { Dialog } from "./Dialogs";
import { useStatus } from "./hooks";
import { PeaksCanvas, usePeaks } from "./Waveform";

const WAVE_W = 640;
const WAVE_H = 56;
const EMPTY: number[] = [];

// ---------------------------------------------------------------------------
// the badge (the timeline's clips, and the inspector)
// ---------------------------------------------------------------------------

/**
 * The speaker badge a clip whose audio isn't its own shows. In recording mode
 * it is dimmed: Play all (and `h3assemble --audio master`) ignore per-clip
 * audio then.
 */
export function AudioBadge({ shot, cut, dim }: { shot: string; cut: ShotStatus["cut"]; dim?: boolean }) {
  const b = audioBadgeOf(cut, shot);
  if (!b) return null;
  return (
    <span
      className={`h3-badge h3-b-audio h3-b-audio-${b.kind}${dim ? " h3-dim" : ""}`}
      title={dim ? `${b.title}\n\n${RECORDING_IGNORES_AUDIO}` : b.title}
    >
      <i className={b.kind === "none" ? "pi pi-volume-off" : "pi pi-volume-up"} /> {b.label}
    </span>
  );
}

// ---------------------------------------------------------------------------
// peaks helpers
// ---------------------------------------------------------------------------

function useWave(ep: string | null, path: string | null | undefined, width: number, version?: string | null) {
  const q = useMemo(
    () => (ep && path ? { ep, path, bins: Math.min(MAX_BINS, Math.max(16, Math.round(width))), version: version ?? null } : null),
    [ep, path, width, version],
  );
  return usePeaks(q, true);
}

/** A file's length in seconds, from its peaks answer (null until they arrive). */
function useSeconds(ep: string | null, path: string | null | undefined): number | null {
  const e = useWave(ep, path, 64);
  return e?.ok && e.data.duration > 0 ? e.data.duration : null;
}

// ---------------------------------------------------------------------------
// the two lanes
// ---------------------------------------------------------------------------

/**
 * The clip's own length, with the chosen audio's placement over it: the bar
 * is where the source is heard, and dragging it is `offset`.
 */
function ClipLane({ ep, path, clipSeconds, draft, sourceDuration, playAt, onOffset, version }: {
  ep: string; path: string | null; clipSeconds: number; draft: CutAudioSource | null; sourceDuration: number | null;
  playAt: number | null; onOffset: (v: number) => void; version?: string | null;
}) {
  const box = useRef<HTMLDivElement>(null);
  const entry = useWave(ep, path, WAVE_W, version);
  const L = audioLayout(draft, clipSeconds, sourceDuration);
  const b = clipBox(draft, clipSeconds, sourceDuration, WAVE_W);
  const drag = (e: RPointerEvent<HTMLDivElement>) => {
    if (e.button !== 0 || !(clipSeconds > 0)) return;
    e.preventDefault();
    e.stopPropagation();
    const x0 = e.clientX;
    const base = offsetOf(draft);
    const move = (ev: PointerEvent) => {
      const d = ((ev.clientX - x0) / WAVE_W) * clipSeconds;
      onOffset(clampOffset(base + d, clipSeconds));
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };
  return (
    <div
      ref={box}
      className="h3-wave h3-clipaudio-lane"
      style={{ width: WAVE_W, height: WAVE_H }}
      title={path ? `${baseName(path)} — the clip's own sound, for comparison` : "This clip's take has no sound of its own"}
    >
      <PeaksCanvas peaks={entry?.ok ? entry.data.peaks : EMPTY} width={WAVE_W} height={WAVE_H} />
      {!path && <span className="h3-wave-note">no sound of its own</span>}
      {draft && !L.silent && (
        <div
          className="h3-span h3-audio-place"
          style={{ left: b.left, width: b.width }}
          title={`Drag to shift the audio against the picture (offset ${offsetOf(draft).toFixed(2)} s)`}
          onPointerDown={drag}
        />
      )}
      {draft && L.head > 0 && <div className="h3-trim-dim" style={{ left: 0, width: timeToX(L.head, WAVE_W, clipSeconds || 1) }} />}
      {draft && L.tail > 0 && <div className="h3-trim-dim" style={{ right: 0, width: timeToX(L.tail, WAVE_W, clipSeconds || 1) }} />}
      {playAt != null && clipSeconds > 0 && <div className="h3-playhead" style={{ left: timeToX(playAt, WAVE_W, clipSeconds) }} />}
    </div>
  );
}

/**
 * The source file, whole, with the clip's picture length marked over it and a
 * draggable `start`. Dragging the marker's body is `start` too (the audio
 * slides under a fixed picture); the left handle is exact.
 */
function SourceLane({ ep, path, clipSeconds, draft, duration, playAt, onStart, version }: {
  ep: string; path: string | null; clipSeconds: number; draft: CutAudioSource | null; duration: number | null;
  playAt: number | null; onStart: (v: number) => void; version?: string | null;
}) {
  const box = useRef<HTMLDivElement>(null);
  const entry = useWave(ep, path, WAVE_W, version);
  const dur = duration ?? (entry?.ok ? entry.data.duration : 0) ?? 0;
  const L = audioLayout(draft, clipSeconds, duration);
  const p = pictureBox(draft, clipSeconds, duration, WAVE_W);
  /** `handle`: the start goes where the pointer is; else it slides with the drag. */
  const drag = (e: RPointerEvent<HTMLDivElement>, handle: boolean) => {
    if (e.button !== 0 || !(dur > 0)) return;
    e.preventDefault();
    e.stopPropagation();
    const left = box.current?.getBoundingClientRect().left ?? 0;
    const x0 = e.clientX;
    const base = startOf(draft);
    const move = (ev: PointerEvent) => {
      const want = handle ? xToTime(ev.clientX - left, WAVE_W, dur) : base + ((ev.clientX - x0) / WAVE_W) * dur;
      onStart(clampStart(want, dur));
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };
  const title = entry && !entry.ok
    ? entry.error
    : entry?.ok && entry.data.silent
      ? `${path}: no audio stream`
      : `${path ?? "no source"}${dur ? ` · ${fmtSeconds(dur)}` : ""}`;
  return (
    <div ref={box} className="h3-wave h3-clipaudio-src" style={{ width: WAVE_W, height: WAVE_H }} title={title}>
      <PeaksCanvas peaks={entry?.ok ? entry.data.peaks : EMPTY} width={WAVE_W} height={WAVE_H} />
      {path && !entry && <span className="h3-wave-note">…</span>}
      {entry && !entry.ok && <span className="h3-wave-note h3-err">{entry.error}</span>}
      {path && dur > 0 && (
        <div
          className="h3-audio-picture"
          style={{ left: Math.max(-2, p.left), width: Math.max(2, Math.min(WAVE_W - Math.max(0, p.left), p.width)) }}
          title={`The clip's ${fmtSeconds(clipSeconds)} of picture over the source; drag to move the start`}
          onPointerDown={(e) => drag(e, false)}
        >
          <div className="h3-span-h h3-l" title="Drag: where in the source the audio begins (start)" onPointerDown={(e) => drag(e, true)} />
        </div>
      )}
      {path && dur > 0 && !L.silent && (
        <div
          className="h3-audio-used"
          style={{ left: timeToX(L.srcIn, WAVE_W, dur), width: Math.max(1, timeToX(L.srcOut, WAVE_W, dur) - timeToX(L.srcIn, WAVE_W, dur)) }}
        />
      )}
      {playAt != null && dur > 0 && <div className="h3-playhead" style={{ left: timeToX(playAt, WAVE_W, dur) }} />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// the window
// ---------------------------------------------------------------------------

export function ClipAudioWindow() {
  const c = useApp((s) => s.clipAudio);
  const ep = useApp((s) => s.ep);
  if (!c || !ep) return null;
  return <ClipAudioBody key={`${c.shot}|${c.pass}`} ep={ep} />;
}

/** The source the draft names, resolved to a file inside the episode. */
export function draftFile(
  draft: CutAudioSource | null, shot: string, pass: Pass, st: EpisodeStatus | undefined, other: EpisodeStatus | undefined,
): string | null {
  if (!draft || draft.source === "none") return null;
  if (draft.source === "file") return draft.path ?? null;
  const from = draft.pass && draft.pass !== pass ? other : st;
  return takeAudioFile(from?.shots, audioShot(draft, shot), draft.take ?? null);
}

function ClipAudioBody({ ep }: { ep: string }) {
  const c = useApp((s) => s.clipAudio)!;
  const st = useStatus(c.pass);
  const other = useStatus(c.pass === "proxy" ? "final" : "proxy");
  const items = useApp((s) => currentPlaylist(s));
  const recording = useApp((s) => s.cutAudio === "recording");
  const video = useRef<HTMLVideoElement>(null);
  const audio = useRef<HTMLAudioElement>(null);
  const [playing, setPlaying] = useState(false);
  const [at, setAt] = useState<number | null>(null);

  const draft = c.draft;
  const shot = st?.shots.find((s) => s.shot === c.shot);
  const item = items.find((i) => i.shot === c.shot);
  const clipSeconds = item?.dur ?? shot?.seconds ?? 0;
  const own = shot ? clipTake(shot, shot.cut.placeholder ? other : undefined) : undefined;
  const ownAudio = own?.audio ?? null;
  const live = audioOf(shot?.cut);
  const locked = !!shot?.cut.locked;

  // the shots (this one first) whose takes have sound, in the draft's pass
  const takePass: Pass = draft?.source === "take" && draft.pass ? draft.pass : c.pass;
  const passSt = takePass === c.pass ? st : other;
  const shots = useMemo(() => soundShots(passSt?.shots ?? [], c.shot), [passSt, c.shot]);
  const pickShot = draft?.source === "take" ? audioShot(draft, c.shot) : c.shot;
  const takes = takesWithSound(shots.find((s) => s.shot === pickShot)?.takes);

  const path = draftFile(draft, c.shot, c.pass, st, other);
  const duration = useSeconds(ep, path);
  const L = audioLayout(draft, clipSeconds, duration);
  const gain = gainOf(draft);
  const problem = audioProblem(draft, !!path || draft?.source === "none");
  const note = draft && !problem ? audioNote(L, clipSeconds) : "";
  const unchanged = sameAudio(live, draft);

  const patch = useCallback((p: Partial<CutAudioSource>) => {
    setClipAudioDraft({ ...(draft ?? { source: "take" }), ...p } as CutAudioSource);
  }, [draft]);

  /** Switch which kind of source this is, keeping start / offset / gain. */
  const chooseKind = (kind: CutAudioKind | "own") => {
    stop();
    if (kind === "own") return setClipAudioDraft(null);
    const keep = { start: draft?.start, offset: draft?.offset, gain: draft?.gain };
    if (kind === "none") return setClipAudioDraft({ source: "none" });
    if (kind === "file") return setClipAudioDraft({ source: "file", path: draft?.path ?? "", ...keep });
    const first = shots[0];
    const t = takesWithSound(first?.takes);
    setClipAudioDraft({
      source: "take", shot: draft?.shot ?? first?.shot ?? c.shot,
      take: draft?.take ?? t[t.length - 1]?.take, pass: c.pass, ...keep,
    });
  };

  // ---- the preview: the clip's picture with the chosen audio under it ----
  const stop = useCallback(() => {
    setPlaying(false);
    setAt(null);
    video.current?.pause();
    audio.current?.pause();
  }, []);

  useEffect(() => stop, [stop]);
  // a new source: the playhead means nothing any more
  useEffect(() => setAt(null), [path]);

  const play = () => {
    const v = video.current;
    if (!v || !item) return;
    v.currentTime = item.inT;
    setPlaying(true);
    void v.play().catch(() => setPlaying(false));
  };

  const onTime = () => {
    const v = video.current;
    const a = audio.current;
    if (!v || !item) return;
    const t = v.currentTime - item.inT;
    setAt(Math.max(0, t));
    if (t >= clipSeconds - 0.01) {
      stop();
      return;
    }
    if (!a) return;
    const want = previewAt(draft, t, clipSeconds, duration);
    if (want == null) {
      if (!a.paused) a.pause();
      return;
    }
    a.volume = Math.min(1, Math.max(0, gain));
    if (a.paused) {
      a.currentTime = want;
      void a.play().catch(() => {});
    } else if (needsResync(a.currentTime, want)) {
      a.currentTime = want;
    }
  };

  // Phase 9b keyboard scoping: the window's keys are its own (a space here
  // must not reach the timeline's or Play all's shortcuts).
  const onKeyDown = (e: RKeyboardEvent<HTMLDivElement>) => {
    if (e.key === "Escape") return;
    e.stopPropagation();
  };

  const srcLabel = !draft
    ? `${c.shot}'s own take`
    : draft.source === "none"
      ? "silence"
      : draft.source === "file"
        ? draft.path || "no file yet"
        : `${audioShot(draft, c.shot)} ${draft.take != null ? tn(draft.take) : "?"}${draft.pass && draft.pass !== c.pass ? ` (${draft.pass})` : ""}`;

  return (
    <Dialog
      title={<>Audio for {c.shot} <span className="h3-muted h3-small">{c.pass} · {fmtSeconds(clipSeconds)}</span></>}
      onClose={closeClipAudio}
      wide
      footer={
        <>
          <span className="h3-muted h3-small h3-grow h3-ell">
            {problem ?? (unchanged ? `${c.shot} already plays ${srcLabel}` : `${c.shot} will play ${srcLabel}`)}
          </span>
          <button className="h3-btn" onClick={closeClipAudio}>Cancel</button>
          <button
            className="h3-btn h3-primary"
            data-clipaudio-save=""
            disabled={!!problem || unchanged || locked}
            title={locked ? `${c.shot} is locked in the ${c.pass} cut: unlock it from its menu first` : "Written to cut.json; Ctrl+Z undoes it"}
            onClick={() => {
              stop();
              void setClipAudio(c.shot, draft).then((ok) => ok && closeClipAudio());
            }}
          >
            <i className="pi pi-check" /> Use this audio
          </button>
        </>
      }
    >
      <div className="h3-col" style={{ gap: 6 }} onKeyDown={onKeyDown}>
        {locked && (
          <div className="h3-note h3-note-err h3-small">
            {c.shot} is locked in the {c.pass} cut: unlock it (its menu) before changing what it plays.
          </div>
        )}
        {recording && <div className="h3-note h3-small">{RECORDING_IGNORES_AUDIO}</div>}

        <div className="h3-row h3-wrap h3-seg-row">
          <span className="h3-seg">
            <button className={!draft ? "h3-on" : ""} title={ownAudio ? `${c.shot}'s own take (${baseName(ownAudio)})` : `${c.shot}'s own take has no sound`} onClick={() => chooseKind("own")}>
              its own
            </button>
            <button className={draft?.source === "take" ? "h3-on" : ""} title="Another take's sound — the same shot's, or any other's" onClick={() => chooseKind("take")}>
              another take
            </button>
            <button className={draft?.source === "file" ? "h3-on" : ""} title="A media file inside the episode" onClick={() => chooseKind("file")}>
              a file
            </button>
            <button className={draft?.source === "none" ? "h3-on" : ""} title="No sound at all under this clip" onClick={() => chooseKind("none")}>
              silent
            </button>
          </span>
          <span className="h3-grow" />
          <button
            className="h3-btn"
            disabled={!item?.mp4 || (!!draft && !path && draft.source !== "none")}
            title={item?.mp4 ? "Play this clip's picture with the chosen audio under it" : `${c.shot} has no video to play`}
            onClick={() => (playing ? stop() : play())}
          >
            <i className={playing ? "pi pi-pause" : "pi pi-play"} /> {playing ? "Stop" : "Preview"}
          </button>
        </div>

        {draft?.source === "take" && (
          <div className="h3-row h3-wrap h3-small">
            <label className="h3-row">
              <span className="h3-muted">Shot</span>
              <select
                className="h3-in"
                value={pickShot}
                onChange={(e) => {
                  const s = shots.find((x) => x.shot === e.target.value);
                  const t = takesWithSound(s?.takes);
                  patch({ shot: e.target.value, take: t[t.length - 1]?.take });
                }}
              >
                {!shots.some((s) => s.shot === pickShot) && <option value={pickShot}>{pickShot} (no take with sound)</option>}
                {shots.map((s) => (
                  <option key={s.shot} value={s.shot}>
                    {s.shot}{s.shot === c.shot ? " (this clip)" : ""}{s.subjects.length ? ` · ${s.subjects.join(", ")}` : ""}
                  </option>
                ))}
              </select>
            </label>
            <label className="h3-row">
              <span className="h3-muted">Take</span>
              <select className="h3-in" value={draft.take ?? ""} onChange={(e) => patch({ take: Number(e.target.value) })}>
                {!takes.length && <option value="">no take with sound</option>}
                {takes.map((t) => (
                  <option key={t.take} value={t.take}>{tn(t.take)}{t.take === shots.find((s) => s.shot === pickShot)?.cut.take ? " (the cut's)" : ""}</option>
                ))}
              </select>
            </label>
            <label className="h3-row">
              <span className="h3-muted">Pass</span>
              <span className="h3-seg">
                {(["proxy", "final"] as Pass[]).map((p) => (
                  <button key={p} className={takePass === p ? "h3-on" : ""} onClick={() => patch({ pass: p })}>{p}</button>
                ))}
              </span>
            </label>
            <span className="h3-muted">{path ? baseName(path) : "that take has no sound"}</span>
          </div>
        )}

        {draft?.source === "file" && (
          <FilePicker shot={c.shot} path={draft.path ?? ""} onPath={(p) => patch({ path: p })} />
        )}

        {draft?.source !== "none" && (
          <>
            <div className="h3-small h3-muted">
              <b>{c.shot}</b>, {fmtSeconds(clipSeconds)} — its own sound{draft ? ", with the chosen audio laid over it (drag the bar: offset)" : ""}
            </div>
            <ClipLane
              ep={ep}
              path={ownAudio}
              clipSeconds={clipSeconds}
              draft={draft}
              sourceDuration={duration}
              playAt={at}
              version={own?.finished ?? null}
              onOffset={(v) => patch({ offset: v })}
            />
          </>
        )}
        {draft && draft.source !== "none" && (
          <>
            <div className="h3-small h3-muted">
              The source{path ? <> — <span className="h3-mono">{path}</span>{duration != null ? ` · ${fmtSeconds(duration)}` : ""}</> : ""}
              {" "}with the clip's picture marked over it (drag the left handle: start)
            </div>
            <SourceLane
              ep={ep}
              path={path}
              clipSeconds={clipSeconds}
              draft={draft}
              duration={duration}
              playAt={at != null ? previewAt(draft, at, clipSeconds, duration) : null}
              onStart={(v) => patch({ start: v })}
            />
            <NumberRow draft={draft} clipSeconds={clipSeconds} duration={duration} patch={patch} />
          </>
        )}
        {draft?.source === "none" && (
          <div className="h3-note h3-small">
            {c.shot} plays silent: assemble lays {fmtSeconds(clipSeconds)} of silence under it, and Play all mutes it. The clip keeps its length.
          </div>
        )}
        {!draft && (
          <div className="h3-note h3-small">
            {c.shot} plays its own take's sound{ownAudio ? <> (<span className="h3-mono">{baseName(ownAudio)}</span>)</> : ", and that take has no sound"}.
            {live ? " Save to put it back." : ""}
          </div>
        )}
        {note && <div className="h3-note h3-small">{note}</div>}
        {problem && <div className="h3-note h3-note-err h3-small">{problem}</div>}
        {gain > 1 && <div className="h3-muted h3-small">A gain above 1 can't be previewed louder here; the export applies it.</div>}
        {item?.mp4 && (
          <video
            ref={video}
            src={api().fileUrl(ep, item.mp4)}
            // its own sound while nothing else is chosen; muted once something is
            muted={!!draft}
            preload="metadata"
            className="h3-clipaudio-video"
            onTimeUpdate={onTime}
            onPause={() => setPlaying(false)}
          />
        )}
        {path && (
          <audio ref={audio} src={api().fileUrl(ep, path)} preload="metadata" style={{ display: "none" }} />
        )}
      </div>
    </Dialog>
  );
}

/** `start`, `offset` and `gain` as numbers, beside the handles. */
function NumberRow({ draft, clipSeconds, duration, patch }: {
  draft: CutAudioSource | null; clipSeconds: number; duration: number | null; patch: (p: Partial<CutAudioSource>) => void;
}) {
  const L = audioLayout(draft, clipSeconds, duration);
  return (
    <div className="h3-row h3-wrap h3-small">
      <label className="h3-row" title="Seconds into the source where the audio begins (0 or more)">
        <span className="h3-muted">start</span>
        <input
          className="h3-in h3-mono" style={{ width: 82 }} type="number" step={0.05} min={0}
          value={startOf(draft)}
          onChange={(e) => patch({ start: clampStart(Number(e.target.value), duration) })}
        />
        <span className="h3-muted">s</span>
      </label>
      <label className="h3-row" title="Seconds the audio is shifted against the picture (positive: later, the gap is silence)">
        <span className="h3-muted">offset</span>
        <input
          className="h3-in h3-mono" style={{ width: 82 }} type="number" step={0.05}
          value={offsetOf(draft)}
          onChange={(e) => patch({ offset: clampOffset(Number(e.target.value), clipSeconds) })}
        />
        <span className="h3-muted">s</span>
      </label>
      <label className="h3-row" title={`A linear multiplier, ${GAIN_MIN}–${GAIN_MAX}; ${GAIN_DEFAULT} leaves it as it is`}>
        <span className="h3-muted">gain</span>
        <input
          type="range" min={GAIN_MIN} max={GAIN_MAX} step={0.05} style={{ width: 96 }}
          value={gainOf(draft)}
          onChange={(e) => patch({ gain: clampGain(Number(e.target.value)) })}
        />
        <input
          className="h3-in h3-mono" style={{ width: 66 }} type="number" step={0.05} min={GAIN_MIN} max={GAIN_MAX}
          value={gainOf(draft)}
          onChange={(e) => patch({ gain: clampGain(Number(e.target.value)) })}
        />
      </label>
      <span className="h3-grow" />
      <span className="h3-muted h3-mono" data-clipaudio-layout="">
        {L.head > 0 ? `${L.head.toFixed(2)}s silence · ` : ""}
        {L.used.toFixed(2)}s of {r3(L.srcIn).toFixed(2)}–{r3(L.srcOut).toFixed(2)}s
        {L.tail > 0 ? ` · ${L.tail.toFixed(2)}s silence` : ""}
      </span>
    </div>
  );
}

/** The file source: browse the ComfyUI machine, upload from here, or type a path. */
function FilePicker({ shot, path, onPath }: { shot: string; path: string; onPath: (p: string) => void }) {
  const up = useApp((s) => s.clipAudio?.upload ?? null);
  const input = useRef<HTMLInputElement>(null);
  return (
    <div className="h3-col" style={{ gap: 3 }}>
      <div className="h3-row h3-wrap h3-small">
        <input
          className="h3-in h3-grow h3-mono"
          placeholder="a path inside the episode, e.g. audio/line_sh030.wav"
          value={path}
          spellCheck={false}
          onChange={(e) => onPath(e.target.value)}
        />
        <button className="h3-btn" title="Browse the ComfyUI machine for a media file inside this episode" onClick={() => browseClipAudio(shot)}>
          <i className="pi pi-folder-open" /> Browse…
        </button>
        <button className="h3-btn" title="Upload a file from this computer into the episode" onClick={() => input.current?.click()}>
          <i className="pi pi-upload" /> Upload…
        </button>
        <input
          ref={input}
          type="file"
          accept="audio/*,video/mp4"
          style={{ display: "none" }}
          onChange={(e) => {
            const f = e.target.files?.[0];
            e.target.value = "";
            if (f) void uploadClipAudio(f);
          }}
        />
      </div>
      {up && !up.error && (
        <span className="h3-muted h3-small">Uploading {up.name}… {up.total ? `${Math.round((up.sent / up.total) * 100)}%` : ""}</span>
      )}
      {up?.error && <div className="h3-note h3-note-err h3-small">{up.error}</div>}
    </div>
  );
}

/** The menu item, so the context menu can open the window. */
export const openAudioFrom = openClipAudio;

/** Re-exported for the Inspector's Cut section and the tests. */
export { audioWhy, resolveAudioFile };
