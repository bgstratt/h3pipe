// Phase 9c against REAL route JSON: test/local/p9c_* (gitignored, never
// committed) are dumps taken from a SCRATCH COPY of a real episode through
// the functions the routes call — h3track.align_ready / attach_track / align,
// h3edit.track_info / episode_status / build_episode, h3refs.refs_listing /
// voice_from_take, targets.list_targets("audio"), h3peaks.peaks. No render was
// queued and nothing outside the copy was written. Skipped without the dumps.
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { createElement } from "react";
import { renderToString } from "react-dom/server";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { parseJsonSeedSafe, type Api } from "../src/api";
import { RefsTab } from "../src/components/RefsTab";
import { TrackLine, TrackWindow } from "../src/components/Track";
import { waveSource } from "../src/components/Waveform";
import { setApi } from "../src/host";
import { defaultOf, refDefaults, refTargetOf } from "../src/lib/imageTargets";
import { buildPlaylist, clipTake } from "../src/lib/playlist";
import { trackState } from "../src/lib/recording";
import { canGenerate, takeFile, takeUsable } from "../src/lib/refs";
import {
  alignBlockers, alignedText, buildError, changedShots, missingText, needsAlignBuild, packagesText, resultSummary,
  scriptClash, trackSummary, windowText,
} from "../src/lib/track";
import { candidateText, defaultVoiceSeconds, fromTakeText, maxVoiceSeconds, voiceCapNotes, voiceModeText } from "../src/lib/voice";
import { initialState, statusKey, store } from "../src/store";
import type {
  AlignReady, AlignResult, BuildResult, EpisodeStatus, PeaksResult, RefList, TargetList, Track, VoiceFromTakeResult,
} from "../src/types";

const HERE = dirname(fileURLToPath(import.meta.url));
const LOCAL = join(HERE, "local");
const dumps = existsSync(LOCAL)
  ? readdirSync(LOCAL).filter((d) => d.startsWith("p9c_") && existsSync(join(LOCAL, d, "align_ready.json")))
  : [];

function load<T>(dir: string, name: string): T | undefined {
  const p = join(LOCAL, dir, name);
  return existsSync(p) ? parseJsonSeedSafe<T>(readFileSync(p, "utf-8")) : undefined;
}

const urlApi = {
  fileUrl: (_ep: string, p: string) => `/file/${p}`,
  refFileUrl: (_ep: string, p: string, v?: string | null) => `/ref/${p}${v ? `?v=${v}` : ""}`,
  peaks: () => Promise.resolve({ duration: 0, bins: 0, peaks: [] }),
} as unknown as Api;

function renderWith(patch: Parameters<typeof store.set>[0], el: Parameters<typeof renderToString>[0]): string {
  store.set(initialState());
  store.set(patch);
  return renderToString(el);
}

describe.skipIf(!dumps.length)("Phase 9c on real episode dumps (test/local/p9c_*)", () => {
  beforeAll(() => setApi(urlApi));
  afterAll(() => store.set(initialState()));

  for (const dir of dumps) {
    const meta = load<{ ep: string }>(dir, "meta.json")!;
    const ep = meta.ep;

    // ---- A: the recording ------------------------------------------------

    it(`${dir}: the real readiness answer, read the way the panel reads it`, () => {
      const ready = load<AlignReady>(dir, "align_ready.json")!;
      expect(typeof ready.ready).toBe("boolean");
      expect(Array.isArray(ready.missing)).toBe(true);
      expect(ready.python).toBeTruthy();
      const b = alignBlockers(ready, false);
      expect(b.ok).toBe(ready.ready);
      if (!b.ok) {
        expect(missingText(b)).toContain("h3align needs");
        if (b.missing.some((m) => m !== "ffmpeg")) expect(b.install).toContain("pip install");
      }
      // this machine has no Whisper, so a cached transcript is what makes a run possible
      const cached = alignBlockers(ready, true);
      expect(cached.missing.every((m) => m !== "faster-whisper" && m !== "openai-whisper")).toBe(true);
      if (ready.packages) expect(packagesText(ready)).toContain("numpy");
    });

    it(`${dir}: attaching gives a track with words / aligned, and the "align to finish" build`, () => {
      const before = load<Track | null>(dir, "track_before.json");
      const after = load<Track>(dir, "track_after.json")!;
      expect(before ?? null).toBeNull();                      // the episode had none
      expect(after).toMatchObject({ exists: true, path: expect.stringContaining("audio/") });
      // a recording just attached has no transcript beside it and nothing timed
      expect(after.words).toBe(false);
      expect(after.aligned).toBe(0);
      const t = trackSummary(after)!;
      expect(t.needsAlign).toBe(true);
      expect(t.text).toContain(".wav");
      expect(alignedText(after.aligned!, "proxy")).toContain("no proxy shot");

      const build = load<BuildResult>(dir, "build_after_attach.json")!;
      expect(build.ok).toBe(false);
      expect(needsAlignBuild(build)).toBe(true);
      expect(buildError(build)).toContain("audio: in-out");
      // only the final pass fails: proxy has no dub policy
      expect(build.passes.proxy?.ok).toBe(true);
    });

    it(`${dir}: a real h3align run reports its changes`, () => {
      const dry = load<AlignResult>(dir, "align_dry.json");
      const run = load<AlignResult>(dir, "align_run.json");
      if (!run) return;                                      // ffmpeg or numpy missing here
      expect(run.ok).toBe(true);
      expect(run.words).toBeTruthy();                        // read from the cache, no Whisper
      expect(run.report_path).toBe("align_report.md");
      expect(run.report).toContain("Alignment");
      const changed = changedShots(run);
      expect(changed.length).toBeGreaterThan(0);
      for (const c of changed) {
        expect(c.audio_out! > c.audio_in!).toBe(true);
        expect(windowText(c)).toMatch(/\d+\.\d\d – \d+\.\d\d s/);
      }
      expect(resultSummary(run)).toMatch(/\d+ of \d+ shots timed/);
      if (dry) {
        expect(dry.report_path).toBeNull();
        expect(dry.script_hash).toBeNull();
        expect(resultSummary(dry)).toContain("would be timed");
      }
    });

    it(`${dir}: the rebuild after a run, and the align_report.md clash`, () => {
      const after = load<BuildResult>(dir, "build_after_align.json");
      const workaround = load<BuildResult>(dir, "build_after_align_workaround.json");
      if (!after) return;
      if (!after.ok && /which \.md/.test(buildError(after))) {
        // a real backend gap: h3align leaves align_report.md beside the
        // script, so an episode whose script isn't <folder>.md stops building
        expect(scriptClash(after)).toContain("align_report.md");
        expect(workaround?.ok).toBe(true);
      } else {
        expect(scriptClash(after)).toBe("");
      }
    });

    it(`${dir}: once built, the windows are on the shots and the toggle can play`, () => {
      const st = load<EpisodeStatus>(dir, "episode_proxy_aligned.json");
      const track = load<Track>(dir, "track_after_align.json");
      if (!st || !track) return;
      expect(track.words).toBe(true);                        // the transcript is cached now
      const withWin = st.shots.filter((s) => s.audio_in != null);
      if (track.aligned) {
        expect(withWin).toHaveLength(track.aligned);
        expect(trackSummary(track)!.needsAlign).toBe(false);
        const ts = trackState(st.track);
        expect(ts).toMatchObject({ why: null });
        // each shot with a window draws its slice of the recording
        const items = buildPlaylist(st);
        for (const s of withWin) {
          const item = items.find((x) => x.shot === s.shot);
          if (!item) continue;
          const src = waveSource(ep, item, clipTake(s, undefined), st, true, 40);
          expect(src).toMatchObject({ kind: "recording", q: { path: st.track!.path, start: s.audio_in } });
        }
      }
    });

    it(`${dir}: a real peaks answer of the recording`, () => {
      const p = load<PeaksResult>(dir, "peaks_track.json");
      if (!p) return;
      expect(p.peaks).toHaveLength(p.bins);
      expect(Math.max(...p.peaks)).toBeGreaterThan(0);
      for (const v of p.peaks) expect(v >= 0 && v <= 255).toBe(true);
    });

    // ---- B: voice refs ---------------------------------------------------

    it(`${dir}: the audio targets, and what ltx2_voice says it can do`, () => {
      const list = load<TargetList>(dir, "targets_audio.json")!;
      expect(list.targets.length).toBeGreaterThan(0);
      expect(list.default.audio).toBeTruthy();
      for (const t of list.targets) {
        expect(t.kind).toBe("audio");
        expect(voiceModeText(t)).toBeTruthy();
        expect(voiceCapNotes(t).length).toBeGreaterThan(0);
      }
      const v = list.targets.find((t) => t.id === "ltx2_voice");
      if (v) {
        // as built: voice cloning is off (no audio ID-LoRA weights installed)
        expect(v.capabilities?.reference_audio).toBe(false);
        expect(voiceCapNotes(v)[0]).toContain("can't copy an existing voice");
        expect(v.capabilities?.max_seconds).toBeGreaterThan(0);
      }
    });

    it(`${dir}: a voice ref for every character, with what a generate would use`, () => {
      const list = load<RefList>(dir, "refs_final.json") ?? load<RefList>(dir, "refs_after.json")!;
      const targets = load<TargetList>(dir, "targets_all.json");
      const voices = list.refs.filter((r) => r.kind === "voice");
      expect(voices.length).toBeGreaterThan(0);
      const d = refDefaults(targets ?? null, list.defaults);
      expect(defaultOf(d, "voices").target).toBe(list.defaults?.voice_target);
      for (const r of voices) {
        expect(canGenerate(r)).toBe(r.can_generate !== false);
        expect(refTargetOf(r, d).target).toBeTruthy();
        if (r.effective) {
          expect(r.effective.target).toBe(list.defaults?.voice_target);
          expect(r.effective.line).toBeTruthy();
          expect(["script", "neutral", "request", "override"]).toContain(r.effective.line_source);
          // the server's seconds aren't whole: the field rounds them
          expect(Number.isInteger(defaultVoiceSeconds(r))).toBe(true);
          expect(maxVoiceSeconds(r)).toBeGreaterThanOrEqual(defaultVoiceSeconds(r));
        }
        for (const t of r.takes) {
          expect(t.image).toBeNull();                        // a voice candidate is audio
          if (t.status === "ok") {
            expect(takeFile(t)).toMatch(/\.(wav|flac|mp3|ogg|m4a)$/);
            expect(takeUsable(t)).toBe(true);
            expect(typeof candidateText(t)).toBe("string");
          }
        }
      }
    });

    it(`${dir}: a real voice-from-take answer`, () => {
      const r = load<VoiceFromTakeResult>(dir, "voice_from_take.json") as
        { ref: VoiceFromTakeResult; picked: boolean; take: number; source: unknown } | undefined;
      if (!r) return;
      expect(typeof r.picked).toBe("boolean");
      expect(r.take).toBeGreaterThan(0);
      // as built: `source` is an object, not the file path the contract implied
      expect(r.source).toMatchObject({ shot: expect.any(String), start: expect.any(Number), end: expect.any(Number) });
      const take = r.ref.takes.find((t) => t.take === r.take)!;
      expect(take).toMatchObject({ source: "from_take", image: null });
      expect(take.audio).toMatch(/\.wav$/);
      expect(take.from).toMatchObject({ shot: expect.any(String), start: expect.any(Number) });
      expect(fromTakeText(take)).toMatch(/^sh\d+ t\d\d \((proxy|final)\) \d+\.\d\d–\d+\.\d\d s$/);
      expect(candidateText(take)).toContain("from a take");
    });

    // ---- the surfaces ----------------------------------------------------

    it(`${dir}: the Recording window and the Shots line take the real answers`, () => {
      const st = load<EpisodeStatus>(dir, "episode_proxy_track.json") ?? load<EpisodeStatus>(dir, "episode_proxy_aligned.json")!;
      const ready = load<AlignReady>(dir, "align_ready.json")!;
      const build = load<BuildResult>(dir, "build_after_attach.json")!;
      const run = load<AlignResult>(dir, "align_run.json");
      const base = {
        ep, pass: "proxy" as const, status: { [statusKey(ep, "proxy")]: st }, alignReady: ready,
        trackBuild: { [ep]: build }, trackPanel: { ep },
        ...(run ? { alignResult: { [ep]: run } } : {}),
      };
      const html = renderWith(base, createElement(TrackWindow));
      expect(html).toContain("Align");
      if (st.track) expect(html).toContain(st.track.path.split("/").pop()!);
      if (!ready.ready) expect(html).toContain(ready.missing[0]);
      if (needsAlignBuild(build)) expect(html).toContain("Align to finish");
      if (run) expect(html).toContain("align_report.md");
      const line = renderWith(base, createElement(TrackLine));
      if (st.track) expect(line).toContain(st.track.path.split("/").pop()!);
    });

    it(`${dir}: the Refs tab draws every voice of the real listing`, () => {
      const list = load<RefList>(dir, "refs_final.json") ?? load<RefList>(dir, "refs_after.json")!;
      const targets = load<TargetList>(dir, "targets_all.json");
      const st = load<EpisodeStatus>(dir, "episode_proxy_aligned.json") ?? load<EpisodeStatus>(dir, "episode_proxy_before.json");
      const html = renderWith({
        ep, pass: "proxy", targets: targets ?? null, refs: { [ep]: list.refs },
        refDefaults: { [ep]: list.defaults ?? null }, refsFilter: "all",
        ...(st ? { status: { [statusKey(ep, "proxy")]: st } } : {}),
        refOpen: Object.fromEntries(list.refs.filter((r) => r.kind === "voice").map((r) => [r.id, true])),
      }, createElement(RefsTab));
      for (const r of list.refs.filter((x) => x.kind === "voice")) expect(html).toContain(r.id);
      if (targets?.targets.some((t) => t.kind === "audio")) {
        expect(html).toContain("Voices with:");
        expect(html).toContain("Use a line from a take");
      }
    });
  }
});
