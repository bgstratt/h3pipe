// Phase 9b against REAL route JSON: test/local/p9b_* (gitignored, never
// committed) are dumps of h3edit.episode_status from scratch copies of a real
// episode: as it is, after cut edits made through h3edit (a move, trims, a
// lock), and given a recorded dialogue track (series config audio.track and
// `audio:` windows, rebuilt). Skipped when the dumps aren't there.
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { createElement } from "react";
import { renderToString } from "react-dom/server";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { parseJsonSeedSafe, type Api } from "../src/api";
import { CutMenu } from "../src/components/CutMenu";
import { CutSection } from "../src/components/CutSection";
import { Timeline } from "../src/components/Timeline";
import { Viewer } from "../src/components/Viewer";
import { waveSource } from "../src/components/Waveform";
import { setApi } from "../src/host";
import { anyOutOfOrder, applyEntries, entriesOf, isOutOfOrder, outOfOrder, sameEntries } from "../src/lib/cutEdit";
import { baseIn, buildPlaylist, clipTake } from "../src/lib/playlist";
import { masterSync, trackState } from "../src/lib/recording";
import { initialState, statusKey, store } from "../src/store";
import type { EpisodeStatus, PeaksResult } from "../src/types";

const HERE = dirname(fileURLToPath(import.meta.url));
const LOCAL = join(HERE, "local");
const dumps = existsSync(LOCAL) ? readdirSync(LOCAL).filter((d) => d.startsWith("p9b_") && existsSync(join(LOCAL, d, "episode_proxy.json"))) : [];

function load<T>(dir: string, name: string): T | undefined {
  const p = join(LOCAL, dir, name);
  return existsSync(p) ? parseJsonSeedSafe<T>(readFileSync(p, "utf-8")) : undefined;
}

const urlApi = {
  fileUrl: (_ep: string, p: string) => `/file/${p}`,
  refFileUrl: (_ep: string, p: string) => `/ref/${p}`,
} as unknown as Api;

function renderWith(patch: Parameters<typeof store.set>[0], el: Parameters<typeof renderToString>[0]): string {
  store.set(initialState());
  store.set(patch);
  return renderToString(el);
}

describe.skipIf(!dumps.length)("Phase 9b on real episode dumps (test/local/p9b_*)", () => {
  beforeAll(() => setApi(urlApi));
  afterAll(() => store.set(initialState()));

  for (const dir of dumps) {
    const meta = load<{ ep: string; windows?: Record<string, [number, number]> }>(dir, "meta.json")!;
    const ep = meta.ep;
    for (const name of ["episode_proxy.json", "episode_proxy_edited.json", "episode_final.json"]) {
      const st = load<EpisodeStatus>(dir, name);
      if (!st) continue;
      const pass = st.pass;
      const key = statusKey(ep, pass);

      it(`${dir}/${name}: the cut round-trips through PUT entries`, () => {
        const entries = entriesOf(st);
        expect(entries.map((e) => e.shot)).toEqual(st.shots.map((s) => s.shot));
        const again = applyEntries(st, entries);
        expect(sameEntries(entriesOf(again), entries)).toBe(true);
        // the server's out_of_order is the same rule as ours
        const mine = outOfOrder(st.shots.map((s) => (s.orphan ? null : s.cut.script_index)));
        expect(st.shots.map((s) => !!s.cut.out_of_order)).toEqual(mine);
        for (const s of st.shots) expect(isOutOfOrder(st, s.shot)).toBe(!!s.cut.out_of_order);
        expect(st.shots.map((s) => s.cut.order)).toEqual(st.shots.map((_, i) => i));
      });

      it(`${dir}/${name}: Play all, the recording and the waveform sources`, () => {
        const items = buildPlaylist(st);
        for (const it of items) {
          expect(it.dur).toBeGreaterThan(0);
          expect(Number.isFinite(it.start)).toBe(true);
          if (it.total != null) expect(it.total).toBeGreaterThan(it.trimIn + it.trimOut);
        }
        const ts = trackState(st.track);
        if (meta.windows) {
          expect(ts).toEqual({ path: st.track!.path, why: null });
          const withWin = st.shots.filter((s) => s.audio_in != null);
          expect(withWin.map((s) => s.shot).sort()).toEqual(Object.keys(meta.windows).sort());
          const sync = masterSync(items, st.fps, baseIn(st), anyOutOfOrder(st));
          expect(sync.start).toBeCloseTo(withWin[0].audio_in!, 3);
          for (const s of st.shots) {
            const it = items.find((x) => x.shot === s.shot)!;
            const src = waveSource(ep, it, clipTake(s, undefined), st, true, 40);
            if (s.audio_in != null) expect(src).toMatchObject({ kind: "recording", q: { path: st.track!.path, start: s.audio_in } });
            else if (clipTake(s, undefined)?.audio) expect(src?.kind).toBe("take");
          }
        } else {
          expect(ts).toBeNull();
          expect(masterSync(items, st.fps).warnings).toEqual([]);
        }
      });

      it(`${dir}/${name}: the timeline, the cut section, the menu and Play all render`, () => {
        const shot = st.shots[0]?.shot ?? null;
        const base = { ep, pass, status: { [key]: st }, shot, waves: true };
        const tl = renderWith(base, createElement(Timeline));
        for (const s of st.shots) expect(tl).toContain(`data-shot="${s.shot}"`);
        if (name.includes("edited")) {
          expect(tl).toMatch(/>1(<!-- -->)?\s*moved</);
          expect(tl).toContain("pi-lock");
          expect(tl).toContain("h3-trimmed-mark");
        }
        if (shot) expect(renderWith(base, createElement(CutSection, { shot }))).toContain("Cut");
        expect(renderWith({ ...base, cutMenu: { x: 1, y: 1 } }, createElement(CutMenu))).toContain("Reset order");
        // the floating window reads the viewport size
        const g = globalThis as { window?: unknown };
        const had = "window" in g;
        if (!had) g.window = { innerWidth: 1280, innerHeight: 800, devicePixelRatio: 1 };
        const player = renderWith({
          ...base, cutAudio: "recording",
          viewer: { kind: "cut", shot: shot ?? "", pass, a: null, b: null, mode: "single", target: "a" },
        }, createElement(Viewer));
        if (!had) delete g.window;
        expect(player).toContain("Play all");
        expect(player.includes(">recording<")).toBe(!!meta.windows);
      });
    }

    const peaks = load<{ path: string; answer: PeaksResult }>(dir, "peaks.json");
    it.skipIf(!peaks?.answer)(`${dir}: a real peaks answer fits`, () => {
      const a = peaks!.answer;
      expect(a.peaks.length).toBe(a.bins);
      for (const v of a.peaks) expect(v >= 0 && v <= 255).toBe(true);
      expect(typeof a.start).toBe("number");
    });
  }
});
