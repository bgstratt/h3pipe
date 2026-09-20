// Phase 9c-A: the Recording window. Attach the episode's dialogue recording
// (browse the ComfyUI machine, type a path, or drop a file in), see what is
// attached, and run h3align against it — with what to install when h3align
// can't run, live progress, and the report it wrote.

import { useRef, useState, type DragEvent } from "react";
import {
  attachTrack, clearTrack, closeTrackPanel, copyText, dismissAlignResult, dismissUpload, loadAlignReady, openBrowse,
  openTrackPanel, runAlign, uploadTrack,
} from "../actions";
import { fmtSeconds } from "../lib/format";
import { dragHasFiles } from "../lib/lookback";
import {
  ALIGN_STAGES, TRACK_ACCEPT, alignBlockers, alignedText, buildError, changedShots, missingText, needsAlignBuild,
  packagesText, progressText, resultSummary, scriptClash, trackSummary, windowText,
} from "../lib/track";
import { statusKey, uploadKey, useApp } from "../store";
import { Dialog } from "./Dialogs";
import { useStatus } from "./hooks";
import { Progress } from "./Thumb";
import { UploadStatus } from "./Upload";

/** The h3 Shots header button; it badges the recording's state. */
export function TrackButton() {
  const ep = useApp((s) => s.ep);
  const st = useStatus();
  const t = trackSummary(st?.track);
  if (!ep) return null;
  const title = t
    ? `Recording: ${t.text}${t.problem ? ` — ${t.problem}` : ""}\n${alignedText(t.aligned, st?.pass ?? "proxy")}\nClick to attach another, align the script, or clear it.`
    : "No dialogue recording. Click to attach one and time the script against it (h3align).";
  return (
    <button className={`h3-btn h3-icon${t ? " h3-on" : ""}`} title={title} onClick={() => openTrackPanel()}>
      <i className="pi pi-volume-up" />
    </button>
  );
}

/** One line in the h3 Shots tab: what is attached, and whether it is aligned. */
export function TrackLine() {
  const st = useStatus();
  const pass = useApp((s) => s.pass);
  const ep = useApp((s) => s.ep);
  const build = useApp((s) => (s.ep ? s.trackBuild[s.ep] : undefined));
  const t = trackSummary(st?.track);
  if (!ep || !t) return null;
  const bad = !!build && !build.ok;
  return (
    <div className={`h3-row h3-small h3-wrap${t.problem ? " h3-err" : " h3-muted"}`} style={{ gap: 6 }}>
      <i className="pi pi-volume-up" />
      <span className="h3-ell" title={st?.track?.path ?? ""}>{t.text}</span>
      {t.problem && <span className="h3-err">{t.problem}</span>}
      <span>· {alignedText(t.aligned, pass)}</span>
      {t.words && <span className="h3-badge h3-b-ok" title="A transcript sits beside the recording: re-aligning needs only ffmpeg.">transcript cached</span>}
      {(t.needsAlign || bad) && (
        <button className="h3-link" onClick={() => openTrackPanel()}>align to finish</button>
      )}
      <button className="h3-link" onClick={() => openTrackPanel()}>recording…</button>
    </div>
  );
}

/** Drop a recording here, or pick one with the file picker. */
function DropRecording() {
  const [over, setOver] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const up = useApp((s) => s.uploads[uploadKey("track", null)]);
  const busy = !!up && !up.error;
  const take = (f: File | undefined) => {
    if (f) void uploadTrack(f);
  };
  const drop = (e: DragEvent) => {
    if (!dragHasFiles(e.dataTransfer)) return;
    e.preventDefault();
    e.stopPropagation();
    setOver(false);
    take(e.dataTransfer.files?.[0]);
  };
  return (
    <div
      className={`h3-drop h3-track-drop${over ? " h3-drop-over" : ""}`}
      onDragEnter={(e) => dragHasFiles(e.dataTransfer) && (e.preventDefault(), setOver(true))}
      onDragOver={(e) => {
        if (!dragHasFiles(e.dataTransfer)) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = "copy";
      }}
      onDragLeave={() => setOver(false)}
      onDrop={drop}
    >
      <i className="pi pi-upload h3-muted" />{" "}
      <span className="h3-muted h3-small">Drop a recording here, or </span>
      <button className="h3-link" disabled={busy} onClick={() => input.current?.click()}>choose a file…</button>
      <input
        ref={input}
        type="file"
        accept={TRACK_ACCEPT}
        style={{ display: "none" }}
        onChange={(e) => {
          const f = e.target.files?.[0];
          e.target.value = "";
          take(f);
        }}
      />
      <UploadStatus refId="track" view={null} />
      {!!up?.error && <button className="h3-link" onClick={() => dismissUpload("track", null)}>dismiss</button>}
    </div>
  );
}

/** What h3align needs and where, or "ready". */
function Readiness({ words }: { words: boolean }) {
  const ready = useApp((s) => s.alignReady);
  const err = useApp((s) => s.alignReadyError);
  const b = alignBlockers(ready, words);
  if (err && !ready) {
    return (
      <div className="h3-note h3-note-err h3-small">
        Couldn't check what h3align needs: {err}{" "}
        <button className="h3-link" onClick={() => void loadAlignReady(true)}>retry</button>
      </div>
    );
  }
  if (!ready) return <div className="h3-muted h3-small">Checking what h3align needs…</div>;
  if (b.ok) {
    return (
      <div className="h3-small h3-muted">
        <span className="h3-ok">Ready.</span> h3align runs in {ready.python || "the pipeline's Python"}.
        {b.whisperSkipped && " No Whisper is needed: the recording already has a transcript beside it."}
        {packagesText(ready) ? ` (${packagesText(ready)})` : ""}
      </div>
    );
  }
  return (
    <div className="h3-note h3-note-err h3-small h3-col" style={{ gap: 4 }}>
      <b>{missingText(b)}</b>
      {b.install && (
        <div className="h3-row h3-wrap" style={{ gap: 4 }}>
          <span>Install them into that Python:</span>
          <code className="h3-mono h3-ell" title={b.install}>{b.install}</code>
          <button className="h3-link" onClick={() => void copyText(b.install, "Command")}>copy</button>
        </div>
      )}
      {b.ffmpegHint && <div>ffmpeg: {b.ffmpegHint}</div>}
      <div className="h3-muted">
        Re-aligning a recording that already has a transcript (<span className="h3-mono">&lt;recording&gt;.words.json</span>) needs
        only ffmpeg — no Whisper — so an episode aligned once can be re-timed after a script edit without any of this.
      </div>
      <div className="h3-row">
        <button className="h3-link" onClick={() => void loadAlignReady(true)}>check again</button>
      </div>
    </div>
  );
}

/** The live stages of a run (`h3pipe.align`). */
function AlignProgress() {
  const run = useApp((s) => s.alignRun);
  if (!run?.busy) return null;
  return (
    <div className="h3-col" style={{ gap: 2 }}>
      <span className="h3-small">
        {progressText(run)}{run.dryRun ? " (dry run)" : ""}
      </span>
      <Progress value={run.pct} max={100} />
    </div>
  );
}

/** The report a run wrote, and the shots it changed. */
function AlignReport() {
  const ep = useApp((s) => s.ep);
  const r = useApp((s) => (s.ep ? s.alignResult[s.ep] : undefined));
  const [showLog, setShowLog] = useState(false);
  if (!ep || !r) return null;
  const changed = changedShots(r);
  return (
    <div className="h3-col" style={{ gap: 4 }}>
      <div className="h3-row h3-wrap">
        <b>{r.dry_run ? "Dry run" : "Aligned"}</b>
        <span className="h3-muted h3-small">{resultSummary(r)}</span>
        {r.report_path && <span className="h3-muted h3-small h3-mono">{r.report_path}</span>}
        <span className="h3-grow" />
        <button className="h3-link" onClick={() => setShowLog(!showLog)}>{showLog ? "hide the log" : "show the log"}</button>
        <button className="h3-link" onClick={dismissAlignResult}>dismiss</button>
      </div>
      {r.dry_run && <div className="h3-note h3-note-info h3-small">Nothing was written. Run it for real to write the windows into the script.</div>}
      {changed.length > 0 && (
        <div className="h3-kv h3-small h3-align-changes">
          {changed.slice(0, 200).map((c) => [
            <span key={`${c.shot}k`} className="h3-mono">{c.shot}</span>,
            <span key={`${c.shot}v`} title={c.note ?? ""}>{windowText(c)}</span>,
          ])}
        </div>
      )}
      {!changed.length && <div className="h3-muted h3-small">No shot was timed: every one keeps its <span className="h3-mono">dur:</span>.</div>}
      {r.report && <pre className="h3-pre" style={{ maxHeight: 200 }}>{r.report}</pre>}
      {showLog && r.log && <pre className="h3-pre h3-muted" style={{ maxHeight: 200 }}>{r.log}</pre>}
    </div>
  );
}

export function TrackWindow() {
  const panel = useApp((s) => s.trackPanel);
  const ep = useApp((s) => s.ep);
  const pass = useApp((s) => s.pass);
  const st = useApp((s) => (s.ep ? s.status[statusKey(s.ep, s.pass)] : undefined));
  const busy = useApp((s) => !!s.busy.track);
  const run = useApp((s) => s.alignRun);
  const ready = useApp((s) => s.alignReady);
  const build = useApp((s) => (s.ep ? s.trackBuild[s.ep] : undefined));
  const [typed, setTyped] = useState("");
  const [model, setModel] = useState("");
  const [snap, setSnap] = useState(true);
  if (!panel || !ep) return null;
  const t = trackSummary(st?.track);
  const words = !!t?.words;
  const blockers = alignBlockers(ready, words);
  const running = !!run?.busy;
  const buildBad = !!build && !build.ok;
  const clash = scriptClash(build);

  return (
    <Dialog title={<>Recording <span className="h3-muted h3-small">{st?.episode ?? ep}</span></>} onClose={closeTrackPanel} wide>
      <div className="h3-h">The episode's dialogue recording</div>
      {t ? (
        <div className="h3-col" style={{ gap: 3 }}>
          <div className="h3-row h3-wrap">
            <i className="pi pi-volume-up" />
            <b className="h3-ell" title={st?.track?.path ?? ""}>{t.text}</b>
            {t.problem && <span className="h3-badge h3-b-failed" title={t.problem}>missing</span>}
            <span className="h3-badge" title={t.words
              ? "A transcript sits beside the recording (<recording>.words.json): re-aligning reads it, so no Whisper is needed."
              : "No transcript beside the recording yet: the first align has to transcribe it (that is what needs a Whisper)."}>
              {t.words ? "transcript cached" : "no transcript yet"}
            </span>
            <span className="h3-grow" />
            <button className="h3-btn h3-danger" disabled={busy || running} title="series.json stops naming it; the file stays on disk" onClick={() => void clearTrack()}>
              <i className="pi pi-times" /> Clear
            </button>
          </div>
          <div className="h3-small h3-muted">
            {alignedText(t.aligned, pass)}
            {st?.track?.duration != null ? ` · the recording is ${fmtSeconds(st.track.duration)} long` : ""}
          </div>
          {t.problem && <div className="h3-note h3-note-err h3-small">{t.problem}. Attach it again, or put the file back.</div>}
        </div>
      ) : (
        <div className="h3-muted h3-small">
          None. Attach one and the series config gets <span className="h3-mono">audio.track</span> and{" "}
          <span className="h3-mono">audio.mode: source_track</span> (written like a promote, with a copy in <span className="h3-mono">_history/</span>).
        </div>
      )}

      {buildBad && clash && (
        <div className="h3-note h3-note-err h3-small">
          <b>The build can't start.</b> {clash}
          <pre className="h3-pre h3-small" style={{ maxHeight: 70 }}>{buildError(build)}</pre>
        </div>
      )}
      {buildBad && !clash && (
        <div className={`h3-note h3-small ${needsAlignBuild(build) ? "h3-note-info" : "h3-note-err"}`}>
          {needsAlignBuild(build) ? (
            <>
              <b>Align to finish.</b> With a recording attached every dialogue shot is a dub, and the build needs an{" "}
              <span className="h3-mono">audio: in-out</span> window on each — so the build fails until the script has them.
              That is expected: aligning writes them.
            </>
          ) : <b>The rebuild failed.</b>}
          <pre className="h3-pre h3-small" style={{ maxHeight: 90 }}>{buildError(build)}</pre>
        </div>
      )}

      <div className="h3-h">{t ? "Attach another" : "Attach a recording"}</div>
      <div className="h3-row h3-wrap">
        <button className="h3-btn" disabled={busy || running} onClick={() => openBrowse({ purpose: "track", files: "audio" })}>
          <i className="pi pi-folder-open" /> Browse the ComfyUI machine…
        </button>
        <input
          className="h3-in h3-grow h3-mono"
          placeholder="or a path on the ComfyUI machine (wav, mp3, flac, ogg, m4a, aac, opus)"
          value={typed}
          onChange={(e) => setTyped(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && typed.trim()) void attachTrack(typed.trim());
          }}
        />
        <button className="h3-btn" disabled={!typed.trim() || busy || running} onClick={() => void attachTrack(typed.trim())}>Use this path</button>
      </div>
      <DropRecording />
      <div className="h3-muted h3-small">
        A file already inside the episode is used where it is; anything else is copied into{" "}
        <span className="h3-mono">&lt;episode&gt;/audio/</span>. Attaching the same file twice changes nothing.
      </div>

      <div className="h3-h">Align the script to the recording</div>
      <Readiness words={words} />
      <div className="h3-row h3-wrap">
        {!!ready?.models?.choices?.length && (
          <label className="h3-row h3-small" title={words
            ? "Only used if the recording has to be transcribed again — it already has a transcript, which is read instead."
            : "The Whisper model that transcribes the recording."}>
            <span className="h3-muted">Whisper model</span>
            <select className="h3-in" value={model} onChange={(e) => setModel(e.target.value)} disabled={running}>
              <option value="">{`default (${ready.models.default ?? "medium.en"})`}</option>
              {ready.models.choices.map((m) => <option key={m} value={m}>{m}</option>)}
            </select>
          </label>
        )}
        <label className="h3-check h3-small" title="Snap each window to the frame grid (h3align's --no-snap turns it off)">
          <input type="checkbox" checked={snap} disabled={running} onChange={(e) => setSnap(e.target.checked)} /> snap to frames
        </label>
        <span className="h3-grow" />
        <button
          className="h3-btn"
          disabled={!t || running || !blockers.ok}
          title={!t ? "Attach a recording first" : !blockers.ok ? missingText(blockers) : "Run h3align and show what it would write, without writing anything"}
          onClick={() => void runAlign({ dryRun: true, model: model || null, snap })}
        >
          <i className={running && run?.dryRun ? "pi pi-spin pi-spinner" : "pi pi-eye"} /> Dry run
        </button>
        <button
          className="h3-btn h3-primary"
          disabled={!t || running || !blockers.ok}
          title={!t ? "Attach a recording first" : !blockers.ok ? missingText(blockers) : "Write the `audio:` windows into the script and rebuild (copies of both files go to _history/)"}
          onClick={() => void runAlign({ dryRun: false, model: model || null, snap })}
        >
          <i className={running && !run?.dryRun ? "pi pi-spin pi-spinner" : "pi pi-clock"} /> Align
        </button>
      </div>
      <AlignProgress />
      {run?.error && !run.busy && <div className="h3-note h3-note-err h3-small">{run.error}</div>}
      <AlignReport />
      <div className="h3-muted h3-small">
        Aligning writes the script's <span className="h3-mono">audio: in-out</span> lines (and the series config), rebuilds the
        episode, and leaves <span className="h3-mono">align_report.md</span> beside it. The old files go to{" "}
        <span className="h3-mono">_history/</span>. The stages are {Object.values(ALIGN_STAGES).join(", ").toLowerCase()}.
      </div>
    </Dialog>
  );
}
