// Phase 9d against REAL route JSON: test/local/p9d_* (gitignored, never
// committed) are dumps taken from a SCRATCH COPY of a real episode through the
// functions the routes call — h3edit.episode_status for both passes, and
// h3peaks.peaks / media_info for the takes' own sound. No render was queued and
// nothing outside the copy was written. Skipped without the dumps.
//
// The backend's 9d routes weren't merged when these were taken, so the dumps
// carry no `audio` / `audio_file` / `audio_why`: the tests read the real shapes
// the window actually depends on (each take's `audio`, the cut, the fps and the
// lengths), then lay a contract-shaped source over them and check the maths,
// the badge and the surfaces against the real ids and paths. Re-dump once the
// routes are in, and the first test below will see the server's own fields.
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { createElement } from "react";
import { renderToString } from "react-dom/server";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { parseJsonSeedSafe, type Api } from "../src/api";
import { AudioBadge, ClipAudioWindow, draftFile } from "../src/components/ClipAudio";
import { CutSection } from "../src/components/CutSection";
import { Timeline } from "../src/components/Timeline";
import { setApi } from "../src/host";
import {
  audioBadgeOf, audioLayout, audioProblem, audioWhy, clipsWithAudio, normalizeAudio, previewAt, resolveAudioFile,
  soundShots, takeAudioFile, takesWithSound,
} from "../src/lib/audioSource";
import { applyEntries, entriesOf, entryOf } from "../src/lib/cutEdit";
import { buildPlaylist, clipTake } from "../src/lib/playlist";
import { initialState, statusKey, store } from "../src/store";
import type { CutAudioSource, EpisodeStatus, PeaksResult } from "../src/types";

const HERE = dirname(fileURLToPath(import.meta.url));
const LOCAL = join(HERE, "local");
const dumps = existsSync(LOCAL)
  ? readdirSync(LOCAL).filter((d) => d.startsWith("p9d_") && existsSync(join(LOCAL, d, "episode_proxy.json")))
  : [];

function load<T>(dir: string, name: string): T | undefined {
  const p = join(LOCAL, dir, name);
  return existsSync(p) ? parseJsonSeedSafe<T>(readFileSync(p, "utf-8")) : undefined;
}

const urlApi = {
  fileUrl: (_ep: string, p: string) => `/file/${p}`,
  refFileUrl: (_ep: string, p: string) => `/ref/${p}`,
  peaks: () => Promise.resolve({ duration: 0, bins: 0, peaks: [] }),
} as unknown as Api;

function renderWith(patch: Parameters<typeof store.set>[0], el: Parameters<typeof renderToString>[0]): string {
  store.set(initialState());
  store.set(patch);
  return renderToString(el);
}

describe.skipIf(!dumps.length)("Phase 9d on real episode dumps (test/local/p9d_*)", () => {
  beforeAll(() => setApi(urlApi));
  afterAll(() => store.set(initialState()));

  for (const dir of dumps) {
    const meta = load<{ ep: string }>(dir, "meta.json")!;
    const ep = meta.ep;
    const proxy = load<EpisodeStatus>(dir, "episode_proxy.json")!;
    const final = load<EpisodeStatus>(dir, "episode_final.json");
    // the first three shots with a take that has sound: the source, and a borrower
    const withSound = proxy.shots.filter((s) => takesWithSound(s.takes).length);

    it(`${dir}: the real status carries what the window needs (take.audio, cut, fps)`, () => {
      expect(proxy.shots.length).toBeGreaterThan(0);
      expect(proxy.fps).toBeGreaterThan(0);
      expect(withSound.length).toBeGreaterThan(1);
      for (const s of withSound) {
        for (const t of takesWithSound(s.takes)) {
          expect(t.audio).toMatch(/\.(mp4|wav)$/);
          // h3peaks.clip_audio: the mp4 when it carries sound, else the take's _h3.wav
          expect(t.audio === t.mp4 || t.audio!.endsWith("_h3.wav")).toBe(true);
        }
        expect(takeAudioFile(proxy.shots, s.shot, takesWithSound(s.takes)[0].take)).toBeTruthy();
      }
      // this server is from before 9d, so nothing carries a source yet
      const served = proxy.shots.filter((s) => s.cut.audio != null);
      if (served.length) {
        // a 9d server: its own fields are what the badge reads
        for (const s of served) {
          expect(audioBadgeOf(s.cut, s.shot)!.label).toBe(s.cut.audio_why || audioWhy(s.cut.audio, s.shot));
        }
      } else {
        expect(clipsWithAudio(proxy.shots)).toEqual([]);
        expect(proxy.shots.every((s) => audioBadgeOf(s.cut, s.shot) === null)).toBe(true);
      }
    });

    it(`${dir}: a take source over a real clip — the layout fits the real lengths`, () => {
      const items = buildPlaylist(proxy, final);
      const me = withSound[0];
      const lender = withSound[1];
      const a: CutAudioSource = {
        source: "take", shot: lender.shot, take: takesWithSound(lender.takes)[0].take, pass: "proxy",
      };
      expect(audioProblem(a)).toBeNull();
      const clip = items.find((i) => i.shot === me.shot)!;
      expect(clip.dur).toBeGreaterThan(0);
      // the source's real length, from the peaks dump when it is there
      const peaks = load<Record<string, { path: string; peaks: PeaksResult }>>(dir, "peaks.json") ?? {};
      const entry = Object.values(peaks).find((p) => p.path === takeAudioFile(proxy.shots, a.shot!, a.take!));
      const dur = entry?.peaks.duration ?? null;
      const L = audioLayout(a, clip.dur, dur);
      expect(Math.round((L.head + L.used + L.tail) * 1000) / 1000).toBe(Math.round(clip.dur * 1000) / 1000);
      if (dur != null) {
        expect(L.srcOut).toBeLessThanOrEqual(Math.round(dur * 1000) / 1000 + 1e-6);
        // shifting it half a second late still keeps the clip's length
        const late = audioLayout({ ...a, offset: 0.5 }, clip.dur, dur);
        expect(late.head).toBe(0.5);
        expect(Math.round((late.head + late.used + late.tail) * 1000) / 1000).toBe(Math.round(clip.dur * 1000) / 1000);
        expect(previewAt({ ...a, offset: 0.5 }, 0.25, clip.dur, dur)).toBeNull();
        expect(previewAt({ ...a, offset: 0.5 }, 0.5, clip.dur, dur)).toBe(0);
      }
      expect(resolveAudioFile(a, me.shot, "proxy", proxy.shots)).toBe(takeAudioFile(proxy.shots, a.shot!, a.take!));
    });

    it(`${dir}: the real peaks answers are drawable on both lanes`, () => {
      const peaks = load<Record<string, { path: string; peaks: PeaksResult; info?: { audio: boolean } }>>(dir, "peaks.json");
      if (!peaks) return;
      for (const [key, v] of Object.entries(peaks)) {
        expect(v.peaks.peaks).toHaveLength(v.peaks.bins);
        expect(v.peaks.duration).toBeGreaterThan(0);
        expect(Math.max(...v.peaks.peaks), key).toBeGreaterThan(0);
        for (const n of v.peaks.peaks) expect(n >= 0 && n <= 255).toBe(true);
        if (v.info) expect(v.info.audio).toBe(true);
      }
    });

    it(`${dir}: setting a source on a real cut writes one entry and nothing else moves`, () => {
      const me = withSound[0];
      const lender = withSound[1];
      const a = normalizeAudio({ source: "take", shot: lender.shot, take: takesWithSound(lender.takes)[0].take, pass: "proxy" })!;
      const before = entriesOf(proxy);
      expect(before).toHaveLength(proxy.shots.length);
      const after = before.map((e) => (e.shot === me.shot ? { ...e, audio: a } : e));
      const next = applyEntries(proxy, after);
      expect(next.shots.map((s) => s.shot)).toEqual(proxy.shots.map((s) => s.shot));
      const cut = next.shots.find((s) => s.shot === me.shot)!.cut;
      expect(cut.audio).toEqual(a);
      expect(cut.audio_file).toBe(takeAudioFile(proxy.shots, a.shot!, a.take!));
      expect(cut.audio_why).toBe(`${lender.shot} t${String(a.take).padStart(2, "0")}`);
      // and it round-trips back out as the file would hold it
      expect(entryOf(next.shots.find((s) => s.shot === me.shot)!, "proxy").audio).toEqual(a);
      // every other shot's entry is untouched
      for (const s of next.shots) {
        if (s.shot === me.shot) continue;
        expect(entryOf(s, "proxy")).toEqual(before.find((e) => e.shot === s.shot));
      }
    });

    it(`${dir}: the shot picker offers the real shots with sound, this clip first`, () => {
      const me = withSound[0];
      const list = soundShots(proxy.shots, me.shot);
      expect(list[0].shot).toBe(me.shot);
      expect(list).toHaveLength(withSound.length);
      // every listed shot really has a take with sound
      for (const s of list) expect(takesWithSound(s.takes).length).toBeGreaterThan(0);
      if (final) {
        const t = takesWithSound(final.shots.find((s) => s.shot === me.shot)?.takes ?? [])[0];
        if (t) expect(draftFile({ source: "take", shot: me.shot, take: t.take, pass: "final" }, me.shot, "proxy", proxy, final)).toBe(t.audio);
      }
    });

    it(`${dir}: the window renders over the real episode`, () => {
      const me = withSound[0];
      const lender = withSound[1];
      const draft: CutAudioSource = {
        source: "take", shot: lender.shot, take: takesWithSound(lender.takes)[0].take, pass: "proxy", start: 0.4, gain: 1.2,
      };
      const base = { ep, pass: "proxy" as const, status: { [statusKey(ep, "proxy")]: proxy } };
      const html = renderWith({ ...base, clipAudio: { shot: me.shot, pass: "proxy", draft, upload: null } }, createElement(ClipAudioWindow));
      expect(html).toContain(me.shot);
      expect(html).toContain(lender.shot);
      expect(html).toContain("Use this audio");
      // the source's real file is what the window draws and plays
      expect(html).toContain(takeAudioFile(proxy.shots, draft.shot!, draft.take!)!);
      // a file source with a real episode path
      const file = renderWith(
        { ...base, clipAudio: { shot: me.shot, pass: "proxy", draft: { source: "file", path: "audio/line.wav" }, upload: null } },
        createElement(ClipAudioWindow),
      );
      expect(file).toContain("audio/line.wav");
      expect(file).toContain("Upload…");
    });

    it(`${dir}: the timeline and the inspector show the real badge`, () => {
      const me = withSound[0];
      const lender = withSound[1];
      const a = normalizeAudio({ source: "take", shot: lender.shot, take: takesWithSound(lender.takes)[0].take, pass: "proxy" })!;
      const st = applyEntries(proxy, entriesOf(proxy).map((e) => (e.shot === me.shot ? { ...e, audio: a } : e)));
      const why = st.shots.find((s) => s.shot === me.shot)!.cut.audio_why!;
      const base = { ep, pass: "proxy" as const, status: { [statusKey(ep, "proxy")]: st }, zoom: 60 };
      const tl = renderWith(base, createElement(Timeline));
      expect(tl).toContain("h3-b-audio");
      expect(tl).toContain(why);
      const insp = renderWith({ ...base, shot: me.shot }, createElement(CutSection, { shot: me.shot }));
      expect(insp).toContain(why);
      expect(insp).toContain("Audio from…");
      // dimmed while Play all is on the recording
      const dim = renderWith({ ...base, cutAudio: "recording" as const }, createElement(AudioBadge, {
        shot: me.shot, cut: st.shots.find((s) => s.shot === me.shot)!.cut, dim: true,
      }));
      expect(dim).toContain("h3-dim");
      expect(dim).toContain("Play all is on the recording");
    });

    it(`${dir}: silence on a real clip keeps its length`, () => {
      const items = buildPlaylist(proxy, final);
      const s = proxy.shots.find((x) => clipTake(x, final) != null)!;
      const clip = items.find((i) => i.shot === s.shot)!;
      const st = applyEntries(proxy, entriesOf(proxy).map((e) => (e.shot === s.shot ? { ...e, audio: { source: "none" as const } } : e)));
      expect(st.shots.find((x) => x.shot === s.shot)!.cut.audio_why).toBe("silent");
      expect(st.shots.find((x) => x.shot === s.shot)!.cut.audio_file).toBeNull();
      // the picture is untouched: the playlist is the same clip by clip
      expect(buildPlaylist(st, final).map((i) => [i.shot, i.dur])).toEqual(items.map((i) => [i.shot, i.dur]));
      expect(clip.dur).toBeGreaterThan(0);
    });
  }
});
