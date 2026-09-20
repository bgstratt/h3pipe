// Phase 9c-A: the episode's dialogue recording, in plain words. What the
// attached track is, what h3align still needs (and in which Python), how far
// a run has got, and what it changed. Pure functions; Track.tsx draws them.

import type { AlignChange, AlignEvent, AlignMissing, AlignReady, AlignResult, BuildResult, Pass, Track } from "../types";
import { fmtSeconds } from "./format";

/** What /h3pipe/track takes (h3track.AUDIO_EXT). The browser's file picker and
 * drag and drop check against it before uploading. */
export const TRACK_EXT = /\.(wav|mp3|flac|ogg|m4a|aac|opus)$/i;
/** `accept` for the recording's file input. */
export const TRACK_ACCEPT = ".wav,.mp3,.flac,.ogg,.m4a,.aac,.opus,audio/*";

/** "that isn't a recording" for a file the track route would refuse (null: fine). */
export function trackFileProblem(f: { name: string }): string | null {
  return TRACK_EXT.test(f.name || "")
    ? null
    : `${f.name} isn't a recording: the track must be a .wav, .mp3, .flac, .ogg, .m4a, .aac or .opus file.`;
}

/** The recording's name, without its folders. */
export function trackName(track: Pick<Track, "path"> | null | undefined): string {
  return track?.path ? track.path.split(/[\\/]/).pop() || track.path : "";
}

export interface TrackSummary {
  /** "ep38_dialogue.wav · 3:24 · 48000 Hz" */
  text: string;
  /** what is wrong with it, or "" */
  problem: string;
  /** a transcript sits beside it: re-aligning needs no Whisper */
  words: boolean;
  /** how many of the pass's shots carry a dialogue window */
  aligned: number;
  /** nothing is aligned yet: the build fails until it is */
  needsAlign: boolean;
}

/** One line about the attached recording (null: the series config names none). */
export function trackSummary(track: Track | null | undefined): TrackSummary | null {
  if (!track || !track.path) return null;
  const bits = [trackName(track)];
  if (track.duration != null) bits.push(fmtSeconds(track.duration));
  if (track.rate) bits.push(`${track.rate} Hz`);
  const aligned = typeof track.aligned === "number" ? track.aligned : 0;
  return {
    text: bits.join(" · "),
    problem: track.exists === false ? `${track.path} isn't on disk` : "",
    words: !!track.words,
    aligned,
    needsAlign: track.exists !== false && aligned === 0,
  };
}

/** "3 shots have a dialogue window" / "no shot is timed against it yet". */
export function alignedText(aligned: number, pass: Pass): string {
  if (!aligned) return `no ${pass} shot is timed against it yet`;
  return `${aligned} ${pass} shot${aligned === 1 ? "" : "s"} ${aligned === 1 ? "has" : "have"} a dialogue window`;
}

// ---------------------------------------------------------------------------
// readiness: what to install, and where
// ---------------------------------------------------------------------------

/** The whisper packages: either one is enough, and neither is needed when the
 * recording already has a transcript (API.md "Phase 9c-A as built"). */
export const WHISPERS = ["faster-whisper", "openai-whisper"];

export function isWhisper(name: string): boolean {
  return WHISPERS.includes(name);
}

export interface AlignBlockers {
  /** true: a run can start now */
  ok: boolean;
  /** what is still missing for THIS run */
  missing: string[];
  /** the pip line for the missing packages ("" when only ffmpeg is) */
  install: string;
  /** the Python to install into */
  python: string;
  /** what to do about ffmpeg (only when it is missing) */
  ffmpegHint: string;
  /** a Whisper is missing, but a cached transcript makes it unnecessary */
  whisperSkipped: boolean;
}

/**
 * What still stands in the way of a run, given whether the recording already
 * has a transcript beside it (`track.words`): with one, h3align never
 * transcribes, so a missing Whisper doesn't stop it — only ffmpeg and numpy do.
 */
export function alignBlockers(ready: AlignReady | AlignMissing | null | undefined, words = false): AlignBlockers {
  const all = ready?.missing ?? [];
  const missing = words ? all.filter((m) => !isWhisper(m)) : [...all];
  const whisperSkipped = words && all.some(isWhisper);
  const pip = missing.filter((m) => m !== "ffmpeg");
  const python = ready?.python ?? "";
  // the server's `install` line covers every missing package; with the whisper
  // dropped, write the line for what is left
  const install = !pip.length ? "" : whisperSkipped ? `"${python}" -m pip install ${pip.join(" ")}` : ready?.install ?? "";
  return {
    ok: !missing.length,
    missing,
    install,
    python,
    ffmpegHint: missing.includes("ffmpeg") ? (ready as AlignReady | null)?.ffmpeg_hint ?? "install ffmpeg and put it on PATH" : "",
    whisperSkipped,
  };
}

/** "faster-whisper and numpy aren't installed for …\python.exe". */
export function missingText(b: AlignBlockers): string {
  if (b.ok) return "";
  const list = b.missing.length === 1 ? b.missing[0] : `${b.missing.slice(0, -1).join(", ")} and ${b.missing[b.missing.length - 1]}`;
  const where = b.python ? ` for ${b.python}` : "";
  return `h3align needs ${list}${b.missing.some((m) => m !== "ffmpeg") ? where : ""}.`;
}

/** The installed version of each package the check probed, as a line. */
export function packagesText(ready: AlignReady | null | undefined): string {
  const p = ready?.packages;
  if (!p) return "";
  return Object.entries(p)
    .map(([name, v]) => `${name}: ${v == null ? "not installed" : v || "installed"}`)
    .join(" · ");
}

// ---------------------------------------------------------------------------
// a run
// ---------------------------------------------------------------------------

export const ALIGN_STAGES: Record<string, string> = {
  transcribe: "Transcribing the recording",
  match: "Matching the script to the words",
  write: "Writing the windows",
};

/** "Matching the script to the words — 1204 words, 38 lines". */
export function progressText(ev: Pick<AlignEvent, "stage" | "text"> | null | undefined): string {
  if (!ev) return "";
  const head = ALIGN_STAGES[ev.stage] ?? ev.stage;
  return ev.text ? `${head} — ${ev.text}` : head;
}

/** The shots an align run gave (or changed) a window. */
export function changedShots(r: Pick<AlignResult, "changes"> | null | undefined): AlignChange[] {
  return (r?.changes ?? []).filter((c) => c.audio_in != null || c.audio_out != null);
}

/** "12 of 38 shots timed" (a dry run says "would be"). */
export function resultSummary(r: AlignResult | null | undefined): string {
  if (!r) return "";
  const n = changedShots(r).length;
  const all = r.changes?.length ?? 0;
  const word = r.dry_run ? "would be timed" : "timed";
  return `${n} of ${all} shot${all === 1 ? "" : "s"} ${word}${r.words ? " (from the cached transcript)" : ""}`;
}

/** A window as the report shows it: "1.20 – 3.90 s (2.70 s)". */
export function windowText(c: Pick<AlignChange, "audio_in" | "audio_out">): string {
  if (c.audio_in == null || c.audio_out == null) return "keeps its dur:";
  return `${c.audio_in.toFixed(2)} – ${c.audio_out.toFixed(2)} s (${(c.audio_out - c.audio_in).toFixed(2)} s)`;
}

// ---------------------------------------------------------------------------
// the build after attaching
// ---------------------------------------------------------------------------

/** The first error of a failed build, or "". */
export function buildError(b: BuildResult | null | undefined): string {
  if (!b || b.ok) return "";
  for (const p of ["final", "proxy"] as Pass[]) {
    const x = b.passes?.[p];
    if (x && !x.ok && x.error) return x.error;
  }
  // a build that couldn't start at all has no passes, only `error`
  return b.error ?? "";
}

/**
 * A build that couldn't start because the episode folder has more than one
 * candidate script. h3align leaves `align_report.md` beside the script, and
 * an episode whose script isn't `<folder>.md` then has two .md files — so
 * every build after the first align fails until the report is moved or
 * renamed (reported as a backend gap). The editor says what to do.
 */
export function scriptClash(b: BuildResult | null | undefined): string {
  const e = buildError(b);
  return /which \.md/i.test(e)
    ? "The episode folder now has more than one .md, so the build can't tell which one is the script — h3align's `align_report.md` is beside it. "
      + "Rename the script to match the folder (or move align_report.md) and build again."
    : "";
}

/**
 * The build that follows attaching a recording FAILS while the script has no
 * `audio:` windows ("policy dub_keep_foley needs an `audio: in-out` window"):
 * `source_track` turns every dialogue shot into a dub. The route answers 200
 * with that build on purpose — it means "align to finish", not "something
 * broke" (API.md "Phase 9c-A as built").
 */
export function needsAlignBuild(b: BuildResult | null | undefined): boolean {
  const e = buildError(b);
  return /audio:\s*in-out|dub_keep_foley|needs an `?audio/i.test(e);
}
