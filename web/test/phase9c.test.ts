// Phase 9c: the dialogue recording (attach, align) and generated voice refs.
// The pure maths first (what h3align needs, the span a voice is cut from,
// the waveform reuse), then both halves end to end on the mock API, then the
// components rendered to HTML so a shape they can't take throws here.
import { createElement } from "react";
import { renderToString } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { attachTrack, clearTrack, loadAlignReady, loadRefs, pickRef, refreshEpisode, runAlign, voiceFromTake } from "../src/actions";
import { RefsTab } from "../src/components/RefsTab";
import { TrackLine, TrackWindow } from "../src/components/Track";
import { VoiceClipWindow } from "../src/components/VoiceRef";
import { peaksCache } from "../src/cutActions";
import { setApi, setHost, type Host } from "../src/host";
import { defaultOf, refDefaults, refTargetKind, refTargetOf, refsSnippet } from "../src/lib/imageTargets";
import { drawPeaks, peakBars, peaksKey } from "../src/lib/peaks";
import { canGenerate } from "../src/lib/refs";
import {
  alignBlockers, alignedText, buildError, changedShots, isWhisper, missingText, needsAlignBuild, packagesText,
  progressText, resultSummary, scriptClash, trackFileProblem, trackName, trackSummary, windowText,
} from "../src/lib/track";
import {
  SPAN_MAX, SPAN_MIN, audioTargets, candidateText, clampSpan, defaultVoiceSeconds, fromTakeText, lineSourceText,
  maxVoiceSeconds, moveEdge, slideSpan, spanBox, spanProblem, spanText, takesWithAudio, timeToX, voiceCapNotes,
  voiceModeText, xToTime,
} from "../src/lib/voice";
import { createMockApi } from "../src/mock/mockApi";
import { mockAlignReady, mockPipInstall, mockPipRemove } from "../src/mock/mockTrack";
import { MOCK_TARGETS } from "../src/mock/mockTargets";
import { initialState, statusKey, store } from "../src/store";
import type { AlignReady, AlignResult, BuildResult, Ref, Track } from "../src/types";

// ---------------------------------------------------------------------------
// A: what h3align needs, and what the recording is
// ---------------------------------------------------------------------------

const READY: AlignReady = {
  ready: false, ffmpeg: "C:\\ffmpeg\\bin\\ffmpeg.exe", missing: ["faster-whisper"],
  python: "C:\\ComfyUI\\python_embeded\\python.exe",
  install: '"C:\\ComfyUI\\python_embeded\\python.exe" -m pip install faster-whisper',
  ffmpeg_hint: "", packages: { numpy: "2.2.1", "faster-whisper": null, "openai-whisper": null },
  models: { default: "medium.en", choices: ["tiny.en", "medium.en"] },
};

describe("what h3align needs", () => {
  it("a missing Whisper stops a first run, but not one with a cached transcript", () => {
    const first = alignBlockers(READY, false);
    expect(first).toMatchObject({ ok: false, missing: ["faster-whisper"], whisperSkipped: false });
    expect(first.install).toBe(READY.install);
    expect(missingText(first)).toContain("h3align needs faster-whisper");
    expect(missingText(first)).toContain(READY.python);
    // the same answer, with a transcript beside the recording: it can run
    const again = alignBlockers(READY, true);
    expect(again).toMatchObject({ ok: true, missing: [], whisperSkipped: true, install: "" });
    expect(missingText(again)).toBe("");
  });

  it("ffmpeg and numpy are needed either way, and the install line drops the whisper", () => {
    const r: AlignReady = { ...READY, ffmpeg: null, missing: ["ffmpeg", "numpy", "faster-whisper"], ffmpeg_hint: "winget install Gyan.FFmpeg" };
    const b = alignBlockers(r, true);
    expect(b.missing).toEqual(["ffmpeg", "numpy"]);
    expect(b.ok).toBe(false);
    expect(b.install).toBe(`"${r.python}" -m pip install numpy`);
    expect(b.ffmpegHint).toBe("winget install Gyan.FFmpeg");
    expect(missingText(b)).toContain("ffmpeg and numpy");
    // with nothing cached, every package is in the server's own line
    expect(alignBlockers(r, false).install).toBe(r.install);
  });

  it("either Whisper counts, and the versions read as a line", () => {
    expect(isWhisper("faster-whisper")).toBe(true);
    expect(isWhisper("openai-whisper")).toBe(true);
    expect(isWhisper("numpy")).toBe(false);
    expect(packagesText(READY)).toBe("numpy: 2.2.1 · faster-whisper: not installed · openai-whisper: not installed");
    expect(packagesText({ ...READY, packages: { numpy: "" } })).toBe("numpy: installed");
    expect(packagesText(null)).toBe("");
  });

  it("nothing missing is ready, and no answer at all blocks nothing", () => {
    expect(alignBlockers({ ...READY, ready: true, missing: [] }, false).ok).toBe(true);
    expect(alignBlockers(null, false)).toMatchObject({ ok: true, missing: [] });
  });
});

describe("the attached recording", () => {
  const track: Track = { path: "audio/ep38_dialogue.wav", duration: 45, rate: 16000, exists: true, words: true, aligned: 13 };

  it("reads as one line, with the transcript and how much is timed", () => {
    const t = trackSummary(track)!;
    expect(t.text).toBe("ep38_dialogue.wav · 45s · 16000 Hz");
    expect(t).toMatchObject({ problem: "", words: true, aligned: 13, needsAlign: false });
    expect(trackName(track)).toBe("ep38_dialogue.wav");
    expect(alignedText(13, "proxy")).toBe("13 proxy shots have a dialogue window");
    expect(alignedText(1, "final")).toBe("1 final shot has a dialogue window");
    expect(alignedText(0, "proxy")).toBe("no proxy shot is timed against it yet");
  });

  it("a recording that isn't there, and one nothing is timed against", () => {
    expect(trackSummary({ ...track, exists: false })!.problem).toContain("isn't on disk");
    expect(trackSummary({ ...track, aligned: 0 })!.needsAlign).toBe(true);
    // a missing file isn't "align to finish": put the file back first
    expect(trackSummary({ ...track, exists: false, aligned: 0 })!.needsAlign).toBe(false);
    expect(trackSummary(null)).toBeNull();
    expect(trackSummary({ path: "" } as Track)).toBeNull();
  });

  it("refuses a file that isn't a recording, and takes every extension the route does", () => {
    for (const n of ["a.wav", "b.MP3", "c.flac", "d.ogg", "e.m4a", "f.aac", "g.opus"]) {
      expect(trackFileProblem({ name: n })).toBeNull();
    }
    expect(trackFileProblem({ name: "take.mp4" })).toContain("isn't a recording");
    expect(trackFileProblem({ name: "notes.txt" })).toContain(".opus");
  });
});

describe("a run and its report", () => {
  const result: AlignResult = {
    ok: true, dry_run: false, report: "# Alignment", report_path: "align_report.md",
    changes: [
      { shot: "sh010", audio_in: 0.75, audio_out: 4.7, note: "" },
      { shot: "sh050", audio_in: null, audio_out: null, note: "keeps its dur:" },
    ],
    notes: [], recording: "audio/x.wav", duration: 45, words: true,
    script_hash: "a", series_hash: "b", track: null, build: null, log: "",
  };

  it("says how far it has got, in words", () => {
    expect(progressText({ stage: "transcribe", text: "reading x.words.json" })).toBe("Transcribing the recording — reading x.words.json");
    expect(progressText({ stage: "match", text: "" })).toBe("Matching the script to the words");
    expect(progressText({ stage: "odd", text: "" })).toBe("odd");
    expect(progressText(null)).toBe("");
  });

  it("counts only the shots that were given a window", () => {
    expect(changedShots(result).map((c) => c.shot)).toEqual(["sh010"]);
    expect(resultSummary(result)).toBe("1 of 2 shots timed (from the cached transcript)");
    expect(resultSummary({ ...result, dry_run: true, words: false })).toBe("1 of 2 shots would be timed");
    expect(windowText(result.changes[0])).toBe("0.75 – 4.70 s (3.95 s)");
    expect(windowText(result.changes[1])).toBe("keeps its dur:");
    expect(resultSummary(null)).toBe("");
  });
});

describe("the build after attaching", () => {
  const dub = (error: string): BuildResult => ({ ok: false, passes: { final: { ok: false, report: "", error }, proxy: { ok: true, report: "", error: "" } } });

  it("“align to finish” is the expected failure, not an error", () => {
    const b = dub("error in ep37.md: shot sh010: policy dub_keep_foley needs an `audio: in-out` window on the recorded track (h3align.py writes these).");
    expect(needsAlignBuild(b)).toBe(true);
    expect(buildError(b)).toContain("dub_keep_foley");
    expect(needsAlignBuild(dub("shot sh010: the plate refs/_bg/x.png is missing"))).toBe(false);
    expect(needsAlignBuild({ ok: true, passes: {} })).toBe(false);
    expect(needsAlignBuild(null)).toBe(false);
  });

  it("a build that couldn't start at all carries `error` and no passes", () => {
    // as built: h3align leaves align_report.md beside the script, so an
    // episode whose script isn't <folder>.md stops building
    const b: BuildResult = { ok: false, error: "can't tell which .md in the folder is the script", passes: {} };
    expect(buildError(b)).toBe(b.error);
    expect(scriptClash(b)).toContain("align_report.md");
    expect(scriptClash(dub("something else"))).toBe("");
  });
});

// ---------------------------------------------------------------------------
// B: the span a voice is cut from, and the voice targets
// ---------------------------------------------------------------------------

describe("the span of a line from a take", () => {
  it("a drag in either direction gives the same span, inside the take", () => {
    expect(clampSpan(1.2, 3.4, 10)).toEqual({ start: 1.2, end: 3.4 });
    expect(clampSpan(3.4, 1.2, 10)).toEqual({ start: 1.2, end: 3.4 });
    expect(clampSpan(-5, 3, 10)).toEqual({ start: 0, end: 3 });
    expect(clampSpan(8, 99, 10)).toEqual({ start: 8, end: 10 });
  });

  it("it is never shorter than 0.2s or longer than 30s", () => {
    expect(clampSpan(1, 1, 10)).toEqual({ start: 1, end: 1 + SPAN_MIN });
    // no room to the right: it grows to the left instead
    expect(clampSpan(10, 10, 10)).toEqual({ start: 10 - SPAN_MIN, end: 10 });
    expect(clampSpan(0, 60, 120)).toEqual({ start: 0, end: SPAN_MAX });
    // a take shorter than the minimum still gives something legal
    expect(clampSpan(0, 0.1, 0.1)).toEqual({ start: 0, end: 0.1 });
  });

  it("says what the server would refuse", () => {
    expect(spanProblem({ start: 1, end: 3 }, 10)).toBeNull();
    expect(spanProblem({ start: 3, end: 3 }, 10)).toContain("left to right");
    expect(spanProblem({ start: 1, end: 1.1 }, 10)).toContain("at least 0.2s");
    expect(spanProblem({ start: 0, end: 31 }, 60)).toContain("at most 30s");
    expect(spanProblem({ start: 12, end: 13 }, 10)).toContain("starts after it ends");
    expect(spanProblem({ start: -1, end: 2 }, 10)).toContain("before the take");
  });

  it("edges move one end, and a slide keeps the length", () => {
    const s = { start: 2, end: 5 };
    expect(moveEdge(s, "start", 3, 10)).toEqual({ start: 3, end: 5 });
    expect(moveEdge(s, "end", 9, 10)).toEqual({ start: 2, end: 9 });
    // dragging an edge past the other one flips it, still legal
    expect(moveEdge(s, "start", 9, 10)).toEqual({ start: 5, end: 9 });
    expect(slideSpan(s, 2, 10)).toEqual({ start: 4, end: 7 });
    expect(slideSpan(s, 99, 10)).toEqual({ start: 7, end: 10 });
    expect(slideSpan(s, -99, 10)).toEqual({ start: 0, end: 3 });
  });

  it("maps pixels to seconds and back, and boxes the span", () => {
    expect(xToTime(300, 600, 10)).toBe(5);
    expect(xToTime(-20, 600, 10)).toBe(0);
    expect(xToTime(900, 600, 10)).toBe(10);
    expect(xToTime(300, 0, 10)).toBe(0);
    expect(timeToX(5, 600, 10)).toBe(300);
    expect(timeToX(50, 600, 10)).toBe(600);
    expect(spanBox({ start: 2, end: 4 }, 600, 10)).toEqual({ left: 120, width: 120 });
    // a span too small to see is still a visible sliver
    expect(spanBox({ start: 2, end: 2.0001 }, 600, 10).width).toBe(1);
    expect(spanText({ start: 1.2, end: 3.456 })).toBe("1.20 – 3.46 s · 2.26 s");
  });

  it("only takes with sound can lend a line", () => {
    const takes = [
      { take: 1, status: "ok", audio: "r/sh010/t1.mp4" },
      { take: 2, status: "ok", audio: null },
      { take: 3, status: "failed", audio: "r/sh010/t3.mp4" },
      { take: 4, status: "queued", audio: "r/sh010/t4.mp4" },
    ];
    expect(takesWithAudio(takes).map((t) => t.take)).toEqual([1]);
  });
});

describe("voice targets and candidates", () => {
  const voice = MOCK_TARGETS.targets.find((t) => t.id === "ltx2_voice")!;

  it("says what the target does — and that it can't copy a voice", () => {
    expect(audioTargets(MOCK_TARGETS).map((t) => t.id)).toEqual(["ltx2_voice"]);
    expect(voiceModeText(voice)).toBe("text to speech, up to 20 s");
    expect(voiceModeText({ capabilities: { mode: "voice_clone", max_seconds: 10 } })).toBe("voice cloning, up to 10 s");
    const notes = voiceCapNotes(voice);
    expect(notes[0]).toContain("can't copy an existing voice");
    expect(notes.join(" ")).toContain("20 seconds");
    expect(voiceCapNotes({ capabilities: { mode: "voice_clone", reference_audio: true } })[0]).toContain("can copy a voice");
    expect(voiceCapNotes(undefined)).toEqual([]);
  });

  it("how long a sample is: the ref's own, else the target's", () => {
    const r = { effective: { seconds: 8.0416666, max_seconds: 20 } } as Ref;
    expect(defaultVoiceSeconds(r)).toBe(8);
    expect(maxVoiceSeconds(r)).toBe(20);
    expect(maxVoiceSeconds({} as Ref, voice)).toBe(20);
    expect(defaultVoiceSeconds({} as Ref, { capabilities: { max_seconds: 5 } })).toBe(5);
  });

  it("a candidate reads as its length and the line it says", () => {
    expect(candidateText({ seconds: 8.04, line: "Hello there", source: "generated" })).toBe("8.0 s · “Hello there”");
    expect(candidateText({ seconds: 12, line: null, source: "from_take" })).toBe("12 s · from a take");
    expect(lineSourceText("script")).toContain("longest line");
    expect(lineSourceText("neutral")).toContain("neutral");
    expect(lineSourceText(null)).toBe("");
    expect(fromTakeText({ source: "from_take", from: { shot: "sh020", take: 2, pass: "proxy", start: 0.5, end: 2.5 } }))
      .toBe("sh020 t02 (proxy) 0.50–2.50 s");
    expect(fromTakeText({ source: "generated" })).toBe("");
  });

  it("a voice generates only when the server says so", () => {
    expect(canGenerate({ kind: "voice", scope: "series", can_generate: true })).toBe(true);
    expect(canGenerate({ kind: "voice", scope: "series", can_generate: false })).toBe(false);
    // a server from before 9c says nothing about voices: the old rule holds
    expect(canGenerate({ kind: "voice", scope: "series" })).toBe(false);
    expect(canGenerate({ kind: "character", scope: "series" })).toBe(true);
  });

  it("the voice target joins the ref defaults, with its own series.json key", () => {
    const d = refDefaults(MOCK_TARGETS, {
      target: "krea2", target_source: "default", keyframe_target: "flux2_klein_edit", keyframe_target_source: "series",
      voice_target: "ltx2_voice", voice_target_source: "editor",
    });
    expect(d.voices).toBe("ltx2_voice");
    expect(defaultOf(d, "voices")).toEqual({ target: "ltx2_voice", source: "editor" });
    expect(defaultOf(d, "keyframes")).toEqual({ target: "flux2_klein_edit", source: "series" });
    expect(refsSnippet("voices", "ltx2_voice")).toBe('"refs": {"voice_target": "ltx2_voice"}');
    expect(refTargetKind({ id: "voice:dean", kind: "voice", scope: "series" })).toBe("voices");
    // with nothing served, a voice falls back to the list's audio default
    const none = refDefaults(MOCK_TARGETS, null);
    expect(none.voices).toBe("ltx2_voice");
    expect(refTargetOf({ id: "voice:dean", kind: "voice", scope: "series" }, none)).toEqual({ target: "ltx2_voice", source: "default" });
  });
});

describe("the waveform drawing the voice refs reuse", () => {
  it("draws the same bars the timeline's lane does", () => {
    const calls: number[][] = [];
    const ctx = { clearRect: () => {}, fillRect: (x: number, y: number, w: number, h: number) => void calls.push([x, y, w, h]), fillStyle: "" };
    drawPeaks(ctx, [0, 128, 255, 64], 4, 10, "#fff");
    expect(calls).toHaveLength(4);
    expect(peakBars([0, 128, 255, 64], 4, 10)).toHaveLength(4);
    // silence still shows a line
    expect(peakBars([0, 0], 2, 10).every((b) => b.h >= 1)).toBe(true);
  });

  it("a candidate's peaks are keyed by its file and its finish time", () => {
    const a = peaksKey({ ep: "e", path: "refs/_takes/voice__dean/voice__dean_t01.wav", bins: 180 });
    const b = peaksKey({ ep: "e", path: "refs/_takes/voice__dean/voice__dean_t01.wav", bins: 180, version: "2026-09-19T10:00:00" });
    expect(a).not.toBe(b);
  });
});

// ---------------------------------------------------------------------------
// end to end on the mock API
// ---------------------------------------------------------------------------

describe("the recording, end to end on the mock", () => {
  let api: ReturnType<typeof createMockApi>;
  let EP: string;
  const toasts: { sev: string; summary: string; detail?: string }[] = [];
  const events: [string, unknown][] = [];
  const listeners = new Map<string, ((d: unknown) => void)[]>();
  const st = () => store.get().status[statusKey(EP, "proxy")];

  beforeEach(async () => {
    toasts.length = 0;
    events.length = 0;
    listeners.clear();
    peaksCache.forget();
    setHost({
      on: (e, cb) => {
        listeners.set(e, [...(listeners.get(e) ?? []), cb]);
        return () => {};
      },
      toast: (sev, summary, detail) => void toasts.push({ sev, summary, detail }),
      show: () => {},
    } as Host);
    api = createMockApi((e, d) => {
      events.push([e, d]);
      for (const cb of listeners.get(e) ?? []) cb(d);
    }, { latency: 0 });
    setApi(api);
    EP = (await api.episodes())[0].ep;
    store.set(initialState());
    store.set({ ep: EP, pass: "proxy" });
    await refreshEpisode(EP, "proxy");
  });
  afterEach(() => store.set(initialState()));

  it("serves the track with `words` and `aligned`, as the route does", () => {
    expect(st().track).toMatchObject({ path: "audio/ep05_dialogue.wav", exists: true, words: true });
    expect(st().track!.aligned).toBeGreaterThan(0);
  });

  it("clears the recording and attaches another; the episode follows", async () => {
    expect(await clearTrack(false)).toBe(true);
    await refreshEpisode(EP, "proxy");
    expect(st().track).toBeNull();
    expect(await attachTrack("C:\\Shows\\DeanStories\\audio\\ep05 take 2.wav")).toBe(true);
    await refreshEpisode(EP, "proxy");
    // it lands in <episode>/audio/, its name made safe (h3track keeps spaces)
    expect(st().track!.path).toBe("audio/ep05 take 2.wav");
    // a recording just attached has no transcript beside it
    expect(st().track!.words).toBe(false);
    expect(toasts.some((t) => /Attached/.test(t.summary))).toBe(true);
  });

  it("refuses a file that isn't a recording, and a path that isn't there", async () => {
    await expect(api.attachTrack(EP, "C:\\Shows\\notes.txt")).rejects.toMatchObject({ status: 400 });
    await expect(api.attachTrack(EP, "C:\\Shows\\nope.wav")).rejects.toMatchObject({ status: 404 });
  });

  it("attaching with no `audio:` windows answers 200 with the “align to finish” build", async () => {
    api.unalign();                                   // the script loses its windows
    await clearTrack(false);
    const r = await api.attachTrack(EP, "C:\\Shows\\DeanStories\\audio\\ep05_dialogue.wav");
    expect(r.track).toMatchObject({ aligned: 0 });
    expect(r.build!.ok).toBe(false);
    expect(needsAlignBuild(r.build)).toBe(true);
  });

  it("a run without a Whisper is 409 — unless the recording has a transcript", async () => {
    await loadAlignReady(true);
    expect(store.get().alignReady).toMatchObject({ ready: false, missing: ["faster-whisper"] });
    // the default recording has a cached transcript, so it runs
    expect(await runAlign({ dryRun: true })).toBe(true);
    // a freshly attached one has none: 409, and the panel learns what's missing
    await attachTrack("C:\\Shows\\DeanStories\\audio\\ep05 take 2.wav");
    expect(await runAlign({})).toBe(false);
    expect(store.get().alignRun!.error).toContain("faster-whisper");
    expect(store.get().alignReady!.missing).toContain("faster-whisper");
    expect(toasts.some((t) => t.summary === "h3align can't run yet")).toBe(true);
    // install one and it goes through
    mockPipInstall("faster-whisper");
    await loadAlignReady(true);
    expect(alignBlockers(store.get().alignReady, false).ok).toBe(true);
    expect(await runAlign({})).toBe(true);
    mockPipRemove("faster-whisper");
  });

  it("a dry run writes nothing; a real run reports its stages and the changed shots", async () => {
    const stages: string[] = [];
    listeners.set("h3pipe.align", [(d) => stages.push((d as { stage: string }).stage)]);
    const dry = await runAlign({ dryRun: true });
    expect(dry).toBe(true);
    expect(stages).toEqual(["transcribe", "match", "write", "write"]);
    const r1 = store.get().alignResult[EP];
    expect(r1).toMatchObject({ dry_run: true, report_path: null, script_hash: null, build: null });
    expect(changedShots(r1).length).toBeGreaterThan(5);
    expect(store.get().alignRun).toMatchObject({ busy: false, pct: 100 });

    await runAlign({});
    const r2 = store.get().alignResult[EP];
    expect(r2).toMatchObject({ dry_run: false, report_path: "align_report.md" });
    expect(r2.script_hash).toBeTruthy();
    expect(r2.build).toBeTruthy();
    // every shot with dialogue got a window, the silent ones keep their dur:
    expect(r2.changes.some((c) => c.audio_in == null)).toBe(true);
    await refreshEpisode(EP, "proxy");
    expect(st().track!.aligned).toBe(changedShots(r2).length);
  });

  it("the timeline's recording toggle has what it needs once a track is attached", async () => {
    const track = st().track!;
    const p = await api.peaks(EP, track.path, 64, 1, 4);
    expect(p.peaks).toHaveLength(64);
    expect(Math.max(...p.peaks)).toBeGreaterThan(0);
    // and nothing to play once it is cleared
    await clearTrack(false);
    await refreshEpisode(EP, "proxy");
    expect(st().track).toBeNull();
  });

  it("the mock's readiness answer is the real machine's shape", () => {
    const r = mockAlignReady();
    expect(r.missing).toEqual(["faster-whisper"]);
    expect(r.install).toContain("pip install faster-whisper");
    expect(r.packages!.numpy).toBeTruthy();
    expect(r.models!.choices).toContain("medium.en");
  });
});

describe("voice refs, end to end on the mock", () => {
  let api: ReturnType<typeof createMockApi>;
  let EP: string;
  const toasts: { sev: string; summary: string; detail?: string }[] = [];
  const refs = () => store.get().refs[EP] ?? [];
  const voice = (id = "voice:ada") => refs().find((r) => r.id === id)!;

  beforeEach(async () => {
    toasts.length = 0;
    setHost({ on: () => () => {}, toast: (sev, summary, detail) => void toasts.push({ sev, summary, detail }), show: () => {} } as Host);
    api = createMockApi(() => {}, { latency: 0 });
    setApi(api);
    EP = (await api.episodes())[0].ep;
    store.set(initialState());
    store.set({ ep: EP, pass: "proxy" });
    await refreshEpisode(EP, "proxy");
    await loadRefs(EP);
  });
  afterEach(() => store.set(initialState()));

  it("lists a voice for every character, with or without a sample", async () => {
    const voices = refs().filter((r) => r.kind === "voice");
    expect(voices.map((r) => r.id).sort()).toEqual(["voice:ada", "voice:bo", "voice:cy", "voice:narrator", "voice:rex"]);
    // rex's series config names no sample: no path, nothing on disk, still generable
    expect(voice("voice:rex")).toMatchObject({ path: null, exists: false, can_generate: true });
    expect(voice("voice:ada")).toMatchObject({ exists: true, can_generate: true });
    expect(voice("voice:rex").effective).toMatchObject({ target: "ltx2_voice", line_source: expect.any(String) });
  });

  it("generates a sample of a chosen length, and refuses one outside the range", async () => {
    const before = voice("voice:bo").takes.length;
    const r = await api.refsGenerate({
      ep: EP, ref: "voice:bo", view: null, count: 2, seed_mode: "auto", seed: null, prompt: null,
      model: null, loras: null, steps: null, note: "", seconds: 6,
    });
    expect(r.queued).toHaveLength(2);
    expect(r.queued.every((q) => q.target === "ltx2_voice")).toBe(true);
    await vi.waitFor(async () => {
      await loadRefs(EP);
      expect(voice("voice:bo").takes.filter((x) => x.status === "ok")).toHaveLength(2);
    }, { timeout: 8000, interval: 100 });
    const t = voice("voice:bo").takes[before];
    expect(t).toMatchObject({ source: "generated", image: null });
    expect(t.seconds).toBeGreaterThan(5);
    expect(t.audio).toMatch(/\.wav$/);
    expect(t.line).toBeTruthy();
    // the candidate's own sound is served, with peaks to draw
    const p = await api.peaks(EP, t.audio!, 100);
    expect(p.peaks).toHaveLength(100);
    expect(Math.max(...p.peaks)).toBeGreaterThan(0);
    await expect(api.refsGenerate({
      ep: EP, ref: "voice:bo", view: null, count: 1, seed_mode: "auto", seed: null, prompt: null,
      model: null, loras: null, steps: null, note: "", seconds: 99,
    })).rejects.toMatchObject({ status: 400 });
  });

  it("picking a voice with no sample writes the series config (series_changed)", async () => {
    // give rex a candidate from a take, then pick it
    const ok = await voiceFromTake({ ref: "voice:rex", shot: "sh010", take: 1, pass: "proxy", start: 0.4, end: 2.4 });
    expect(ok).toBe(true);
    const rex = voice("voice:rex");
    // it had no live file, so the new candidate went live at once
    expect(rex).toMatchObject({ exists: true, path: "refs/voices/rex.wav" });
    expect(rex.takes[0]).toMatchObject({ source: "from_take", seconds: 2 });
    expect(rex.takes[0].from).toMatchObject({ shot: "sh010", take: 1, pass: "proxy", start: 0.4, end: 2.4 });
    expect(toasts.some((t) => (t.detail ?? "").includes("series.json"))).toBe(true);
  });

  it("a second candidate is not picked, and picking it says the series config changed", async () => {
    await voiceFromTake({ ref: "voice:rex", shot: "sh010", take: 1, pass: "proxy", start: 0.4, end: 2.4 });
    const r = await api.refsVoiceFromTake({ ep: EP, ref: "voice:rex", shot: "sh010", take: 1, pass: "proxy", start: 3, end: 5 });
    expect(r.picked).toBe(false);
    expect(r.source).toMatchObject({ shot: "sh010", start: 3, end: 5 });
    const pick = await api.refsPick({ ep: EP, ref: "voice:rex", take: r.take });
    // the path was written the first time, so this pick changes nothing there
    expect(pick.series_changed).toBe(false);
  });

  it("refuses a span the server would refuse, and a ref that isn't a voice", async () => {
    const bad = (start: number, end: number) => api.refsVoiceFromTake({ ep: EP, ref: "voice:ada", shot: "sh010", take: 1, pass: "proxy", start, end });
    await expect(bad(1, 1.1)).rejects.toMatchObject({ status: 400 });
    await expect(bad(0, 31)).rejects.toMatchObject({ status: 400 });
    await expect(api.refsVoiceFromTake({ ep: EP, ref: "subject:ada", shot: "sh010", take: 1, pass: "proxy", start: 0, end: 2 }))
      .rejects.toMatchObject({ status: 400 });
    await expect(api.refsVoiceFromTake({ ep: EP, ref: "voice:ada", shot: "nope", take: 1, pass: "proxy", start: 0, end: 2 }))
      .rejects.toMatchObject({ status: 404 });
  });

  it("clears a voice that has a sample, and refuses one that has none", async () => {
    const r = await api.refsUnpick(EP, "voice:ada");
    expect(r).toMatchObject({ exists: false });
    await expect(api.refsUnpick(EP, "voice:rex")).rejects.toMatchObject({ status: 400 });
  });

  it("the episode's voice target is its own default, and a wrong kind is refused", async () => {
    const { defaults } = await api.refs(EP);
    expect(defaults).toMatchObject({ voice_target: "ltx2_voice", voice_target_source: "default" });
    await expect(api.putRefDefaults(EP, { voice_target: "krea2" })).rejects.toMatchObject({ status: 400 });
    const r = await api.putRefDefaults(EP, { voice_target: "ltx2_voice" });
    expect(r.defaults).toMatchObject({ voice_target_source: "editor", target_source: "default" });
  });

  it("picking a generated candidate makes it the live sample", async () => {
    await api.refsGenerate({
      ep: EP, ref: "voice:bo", view: null, count: 1, seed_mode: "auto", seed: null, prompt: null,
      model: null, loras: null, steps: null, note: "", seconds: 4,
    });
    await vi.waitFor(async () => {
      await loadRefs(EP);
      expect(voice("voice:bo").takes.some((t) => t.status === "ok")).toBe(true);
    }, { timeout: 8000, interval: 100 });
    const t = voice("voice:bo").takes.find((x) => x.status === "ok")!;
    expect(await pickRef("voice:bo", null, t.take)).toBe(true);
    expect(voice("voice:bo")).toMatchObject({ picked: t.take, exists: true });
  });
});

// ---------------------------------------------------------------------------
// the components (rendered to HTML: a shape they can't take throws here)
// ---------------------------------------------------------------------------

describe("the Phase 9c surfaces render", () => {
  let api: ReturnType<typeof createMockApi>;
  let EP: string;

  beforeEach(async () => {
    setHost({ on: () => () => {}, toast: () => {}, show: () => {} } as Host);
    api = createMockApi(() => {}, { latency: 0 });
    setApi(api);
    EP = (await api.episodes())[0].ep;
    store.set(initialState());
    store.set({ ep: EP, pass: "proxy", targets: await api.targets({ ready: true }) });
    await refreshEpisode(EP, "proxy");
    await loadRefs(EP);
  });
  afterEach(() => store.set(initialState()));

  it("the Recording window says what is attached and what to install", () => {
    store.set({ trackPanel: { ep: EP }, alignReady: READY });
    const html = renderToString(createElement(TrackWindow));
    expect(html).toContain("ep05_dialogue.wav");
    expect(html).toContain("transcript cached");
    // with a cached transcript the missing Whisper doesn't block the run
    expect(html).toContain("Ready.");
    expect(html).toContain("Align");
    expect(html).toContain("Dry run");
  });

  it("with no transcript it names the packages and the Python", async () => {
    await clearTrack(false);
    await attachTrack("C:\\Shows\\DeanStories\\audio\\ep05 take 2.wav");
    await refreshEpisode(EP, "proxy");
    store.set({ trackPanel: { ep: EP }, alignReady: READY });
    const html = renderToString(createElement(TrackWindow));
    expect(html).toContain("faster-whisper");
    expect(html).toContain("python_embeded");
    expect(html).toContain("no transcript yet");
  });

  it("the “align to finish” build shows as a note, not an error", async () => {
    api.unalign();
    await clearTrack(false);
    await attachTrack("C:\\Shows\\DeanStories\\audio\\ep05_dialogue.wav");
    await refreshEpisode(EP, "proxy");
    store.set({ trackPanel: { ep: EP }, alignReady: READY });
    const html = renderToString(createElement(TrackWindow));
    expect(html).toContain("Align to finish");
    expect(html).toContain("h3-note-info");
    expect(renderToString(createElement(TrackLine))).toContain("align to finish");
  });

  it("the Refs tab draws a voice row with its candidates and the voice target", () => {
    store.set({ refsFilter: "all", refOpen: Object.fromEntries((store.get().refs[EP] ?? []).map((r) => [r.id, true])) });
    const html = renderToString(createElement(RefsTab));
    expect(html).toContain("Voices with:");
    expect(html).toContain("LTX-2.5 audio-only");
    expect(html).toContain("can&#x27;t copy an existing voice");
    expect(html).toContain("Use a line from a take");
    expect(html).toContain("<audio");
    // a character with no sample yet
    expect(html).toContain("no sample");
  });

  it("the voice-clip window lists the shots with sound and a legal span", () => {
    store.set({ voiceClip: { ref: "voice:ada", shot: null, take: null, pass: "proxy" } });
    const html = renderToString(createElement(VoiceClipWindow));
    expect(html).toContain("A line from a take");
    expect(html).toContain("Use this line");
    expect(html).toContain("Preview the span");
  });
});
