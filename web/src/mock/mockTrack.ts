// Phase 9c-A in the mock: what h3align needs (this machine has ffmpeg and
// numpy but no Whisper, as the real one does), and a simulated run that sends
// the `h3pipe.align` stages and writes dialogue windows. Deterministic.

import type { AlignReady, AlignRequest, AlignResult, Pass } from "../types";

/** A shot the run times, in script order. */
export interface AlignShot {
  shot: string;
  /** how long it is now, seconds */
  seconds: number;
  /** it has dialogue (a shot without any keeps its `dur:`) */
  dialogue: boolean;
}

export interface MockAlignDeps {
  ep: string;
  emit: (event: "h3pipe.align", detail: unknown) => void;
  wait: (ms: number) => Promise<void>;
  shots: () => AlignShot[];
  /** the recording now, or null when the series config names none */
  track: () => { path: string; duration: number; words: boolean } | null;
  /** write the windows a real run found (a dry run never calls it) */
  write: (windows: Record<string, { audioIn: number; audioOut: number }>) => void;
  /** a run's transcript is cached beside the recording afterwards */
  cached: (words: boolean) => void;
}

/** What the mock's Python has installed. As on the real machine: ffmpeg and
 * numpy, no Whisper — so a run needs a cached transcript. */
const INSTALLED: Record<string, string | null> = {
  numpy: "2.2.1",
  "faster-whisper": null,
  "openai-whisper": null,
};
const PYTHON = "C:\\ComfyUI\\python_embeded\\python.exe";
let ffmpeg: string | null = "C:\\ffmpeg\\bin\\ffmpeg.exe";

/** The dev page's `h3mockPip("faster-whisper")`: pretend it was installed. */
export function mockPipInstall(name: string, version = "1.2.0") {
  if (name === "ffmpeg") ffmpeg = "C:\\ffmpeg\\bin\\ffmpeg.exe";
  else INSTALLED[name] = version;
}

/** The dev page's `h3mockPipRemove("ffmpeg")`, to see the missing-ffmpeg note. */
export function mockPipRemove(name: string) {
  if (name === "ffmpeg") ffmpeg = null;
  else INSTALLED[name] = null;
}

export function mockAlignReady(): AlignReady {
  const missing: string[] = [];
  if (!ffmpeg) missing.push("ffmpeg");
  if (INSTALLED.numpy == null) missing.push("numpy");
  if (INSTALLED["faster-whisper"] == null && INSTALLED["openai-whisper"] == null) missing.push("faster-whisper");
  const pip = missing.filter((m) => m !== "ffmpeg");
  return {
    ready: !missing.length,
    ffmpeg,
    missing,
    python: PYTHON,
    install: pip.length ? `"${PYTHON}" -m pip install ${pip.join(" ")}` : "",
    ffmpeg_hint: ffmpeg ? "" : "install ffmpeg and put it on PATH (winget install Gyan.FFmpeg)",
    packages: { ...INSTALLED },
    models: { default: "medium.en", choices: ["tiny.en", "base.en", "small.en", "medium.en", "large-v3"] },
  };
}

export class AlignError extends Error {
  constructor(message: string, public status: number, public data?: unknown) {
    super(message);
  }
}

function r3(n: number): number {
  return Math.round(n * 1000) / 1000;
}

export function createMockAlign(deps: MockAlignDeps) {
  return {
    ready: mockAlignReady,
    /** POST /h3pipe/align: the stages, then the report. */
    async run(req: AlignRequest & { pass?: Pass }): Promise<AlignResult> {
      const track = deps.track();
      if (!track) throw new AlignError("the series config names no recording (audio.track)", 400);
      const ready = mockAlignReady();
      // a cached transcript means no Whisper is needed: h3align reads it
      const need = ready.missing.filter((m) => !(track.words && (m === "faster-whisper" || m === "openai-whisper")));
      if (need.length) {
        throw new AlignError(
          `h3align needs ${need.join(", ")} (install them for the Python that runs the pipeline)`,
          409,
          {
            missing: need,
            install: need.filter((m) => m !== "ffmpeg").length ? `"${PYTHON}" -m pip install ${need.filter((m) => m !== "ffmpeg").join(" ")}` : "",
            python: PYTHON,
            ffmpeg,
            words: track.words,
          },
        );
      }
      const dry = !!req.dry_run;
      const shots = deps.shots();
      const spoken = shots.filter((s) => s.dialogue);
      deps.emit("h3pipe.align", {
        ep: deps.ep, stage: "transcribe", pct: 10,
        text: track.words ? `reading ${track.path}.words.json` : `transcribing ${track.path} with ${req.model || "medium.en"}`,
      });
      await deps.wait(500);
      deps.emit("h3pipe.align", {
        ep: deps.ep, stage: "match", pct: 65,
        text: `${spoken.length * 13} words, ${spoken.length} lines`,
      });
      await deps.wait(400);
      // windows laid end to end from 1 s, as h3align would time them
      const windows: Record<string, { audioIn: number; audioOut: number }> = {};
      const changes: AlignResult["changes"] = [];
      let at = 1;
      for (const s of shots) {
        if (!s.dialogue) {
          changes.push({ shot: s.shot, audio_in: null, audio_out: null, note: "keeps its dur:" });
          continue;
        }
        const audio_in = r3(at);
        const audio_out = r3(at + s.seconds);
        windows[s.shot] = { audioIn: audio_in, audioOut: audio_out };
        changes.push({ shot: s.shot, audio_in, audio_out, note: `${(audio_out - audio_in).toFixed(2)}s of dialogue` });
        at = audio_out + 0.25;
      }
      deps.emit("h3pipe.align", { ep: deps.ep, stage: "write", pct: 90, text: dry ? "dry run" : `writing ${spoken.length} windows` });
      await deps.wait(250);
      if (!dry) {
        deps.write(windows);
        deps.cached(true);
      }
      deps.emit("h3pipe.align", { ep: deps.ep, stage: "write", pct: 100, text: dry ? "dry run — nothing written" : "done" });
      const report = [
        `# align report — ${deps.ep}`,
        "",
        `recording: ${track.path} (${track.duration.toFixed(2)}s)`,
        `transcript: ${track.words ? "cached (.words.json)" : `${req.model || "medium.en"}`}`,
        "",
        ...changes.map((c) => `- ${c.shot}: ${c.audio_in == null ? "keeps its dur:" : `${c.audio_in.toFixed(2)}–${c.audio_out!.toFixed(2)}s`}`),
      ].join("\n");
      return {
        ok: true,
        dry_run: dry,
        report,
        report_path: dry ? null : "align_report.md",
        changes,
        notes: changes.map((c) => `${c.shot}: ${c.note ?? ""}`),
        recording: track.path,
        duration: track.duration,
        words: track.words,
        script_hash: dry ? null : `mock${Date.now().toString(16)}`,
        series_hash: dry ? null : `mock${(Date.now() + 1).toString(16)}`,
        track: null,
        build: null,
        log: `h3align ${deps.ep}${dry ? " --dry-run" : ""}\n  ${spoken.length} lines matched\n  ${dry ? "nothing written" : "wrote align_report.md"}\n`,
      };
    },
  };
}
