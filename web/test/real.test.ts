// The UI against REAL route JSON, not only the mock. Earlier mocks hid shapes a
// real episode has (a voice-only character with `path: null`, `views: []`,
// `target_source` "default" / "series", refs_used entries with `id: null`).
//
// Two sources:
//  - REAL_SHAPES below: small pieces copied from real answers (committed);
//  - test/local/<name>/*.json (gitignored, never committed): whole answers dumped
//    from a scratch copy of a real episode through the functions the routes call
//    (h3edit.episode_status / shot_detail, h3refs.refs_listing, the Phase 8.6
//    handlers). Those tests are skipped when the folder isn't there.
//
// Components are rendered to HTML with react-dom/server (no effects run), with
// the store filled from the JSON, so a shape a component can't take throws here.
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { createElement } from "react";
import { renderToString } from "react-dom/server";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { parseJsonSeedSafe, type Api } from "../src/api";
import { Inspector } from "../src/components/Inspector";
import { RefsTab } from "../src/components/RefsTab";
import { setApi } from "../src/host";
import { editRefsFor, refDefaults, refTargetOf } from "../src/lib/imageTargets";
import { keyframePlan } from "../src/lib/keyframes";
import { missingResultText } from "../src/lib/lookback";
import { negativeSourceLabel, takesNegative } from "../src/lib/negative";
import { blockedShots, groupRefs, missingPlan, refCounts, takeFile, takeUsable } from "../src/lib/refs";
import { shotRefTiles } from "../src/lib/refsUsed";
import { detailKey, initialState, statusKey, store } from "../src/store";
import type { EpisodeStatus, Pass, Ref, RefList, ShotDetail, TargetList } from "../src/types";

const HERE = dirname(fileURLToPath(import.meta.url));
const LOCAL = join(HERE, "local");

/** Pieces of real answers (h3refs.ref_json, h3edit.refs_used), trimmed. */
const REAL_SHAPES = {
  narrator: {
    id: "subject:narrator", key: "subject__narrator", scope: "series", kind: "character", name: "the Narrator", subject: "narrator",
    path: null, exists: false, sha1: null, used_by: { final: [], proxy: [] }, prompt: "", can_generate: false,
    why_not: "subject:narrator has no sheet in the series config (a voice-only character)",
    override: { fields: [], stale: false, values: {} }, views: ["01_threequarter", "02_side", "03_back", "04_face"].map((view) => ({
      view, picked: null, cleared: false, prompt: "", override: { fields: [], stale: false, values: {} }, effective: null, takes: [],
    })), takes: [], picked: null, effective: null, cleared: false, override_values: {}, built_prompt: "",
  },
  voice: {
    id: "voice:dean", key: "voice__dean", scope: "series", kind: "voice", name: "Dean's voice", subject: "dean",
    path: "audio/voices/dean_sample.wav", exists: true, sha1: "ab", used_by: { final: ["sh020"], proxy: ["sh020"] }, prompt: "",
    can_generate: false, why_not: "voice:dean is a voice: nothing generates voices yet (import a recording)",
    override: { fields: [], stale: false, values: {} }, views: [], picked: 1, effective: null, cleared: false, override_values: {}, built_prompt: "",
    takes: [{
      take: 1, view: null, status: "ok", usable: true, seed: null, seed_source: null, image: null,
      audio: "refs/_takes/voice__dean/voice__dean_t01.wav", source: "imported", note: "", prompt: null, model: null, loras: null,
      steps: null, width: null, height: null, overrides: [], queued: "2026-09-18T22:00:00-05:00", finished: "2026-09-18T22:00:00-05:00",
      comfy_prompt_id: null, save_notes: "", original_name: "dean.wav",
    }],
  },
  refsUsed: [
    { id: "subject:dean", kind: "image", role: "subject", path: "refs/dean/dean_sheet_4panel.png", exists: true, need: "required", thumb: "refs/dean/dean_sheet_4panel.png", slot: "Picture 1" },
    { id: "voice:dean", kind: "audio", role: "voice", path: "audio/voices/dean_sample.wav", exists: true, need: "required", thumb: "audio/voices/dean_sample.wav", slot: "Audio 1" },
    { id: null, kind: "audio", role: "recording", path: "audio/ep38_dialogue.wav", exists: false, need: "required", thumb: null, slot: "dialogue recording" },
    { id: null, kind: "image", role: "reference_sheet", path: null, exists: false, need: "required", thumb: null, slot: "reference sheet" },
  ],
} as const;

const urlApi = {
  fileUrl: (_ep: string, p: string) => `/file/${p}`,
  refFileUrl: (_ep: string, p: string, v?: string | null) => `/ref/${p}${v ? `?v=${v}` : ""}`,
} as unknown as Api;

function renderWith(patch: Parameters<typeof store.set>[0], el: Parameters<typeof renderToString>[0]): string {
  store.set(initialState());
  store.set(patch);
  return renderToString(el);
}

describe("real shapes (committed pieces)", () => {
  beforeAll(() => setApi(urlApi));
  afterAll(() => store.set(initialState()));

  it("a voice candidate plays its `audio` (its `image` is null)", () => {
    const t = REAL_SHAPES.voice.takes[0];
    expect(takeFile(t)).toBe("refs/_takes/voice__dean/voice__dean_t01.wav");
    expect(takeUsable(t)).toBe(true);
    const ep = "C:\\eps\\ep38";
    const html = renderWith({
      ep, refs: { [ep]: [REAL_SHAPES.voice as unknown as Ref, REAL_SHAPES.narrator as unknown as Ref] }, refsFilter: "all",
      refOpen: { "voice:dean": true, "subject:narrator": true },
    }, createElement(RefsTab));
    expect(html).toContain("<audio");
    expect(html).toContain("voice__dean_t01.wav");
    // the voice-only narrator: no file, no generate, nothing to drop onto
    expect(html).toContain("no file");
  });

  it("refs_used entries with a null id, audio, and no file make tiles", () => {
    const { tiles } = shotRefTiles(REAL_SHAPES.refsUsed as never, [], "sh020", "proxy");
    expect(tiles.map((t) => [t.role, t.label, t.audio, t.image, t.refId])).toEqual([
      ["subject", "dean", false, "refs/dean/dean_sheet_4panel.png", "subject:dean"],
      ["reference_sheet", "reference sheet", false, null, null],
      ["voice", "dean", true, null, "voice:dean"],
      ["recording", "ep38_dialogue.wav", true, null, null],
    ]);
    expect(new Set(tiles.map((t) => t.id)).size).toBe(tiles.length); // unique React keys
  });

  it("reads the settled negative_source values", () => {
    expect(negativeSourceLabel("none")).toBe("");
    expect(takesNegative(undefined, { negative: null, negative_source: "none" })).toBe(false);
    expect(takesNegative(undefined, { negative: "blurry", negative_source: "negative.txt" })).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// whole answers from a real episode (test/local, gitignored)
// ---------------------------------------------------------------------------

const dumps = existsSync(LOCAL) ? readdirSync(LOCAL).filter((d) => existsSync(join(LOCAL, d, "refs.json"))) : [];

function load<T>(dir: string, name: string): T | undefined {
  const p = join(LOCAL, dir, name);
  return existsSync(p) ? parseJsonSeedSafe<T>(readFileSync(p, "utf-8")) : undefined;
}

describe.skipIf(!dumps.length)("real episode dumps (test/local)", () => {
  beforeAll(() => setApi(urlApi));
  afterAll(() => store.set(initialState()));

  for (const dir of dumps) {
    const meta = load<{ ep: string }>(dir, "meta.json")!;
    const list = load<RefList>(dir, "refs.json")!;
    const targets = load<TargetList>(dir, "targets.json")!;
    const ep = meta.ep;

    for (const pass of ["proxy", "final"] as Pass[]) {
      const st = load<EpisodeStatus>(dir, `episode_${pass}.json`);
      const shots = load<Record<string, ShotDetail | { error: string }>>(dir, `shots_${pass}.json`) ?? {};
      if (!st) continue;

      it(`${dir} ${pass}: the Refs tab's logic takes the real listing`, () => {
        const refs = list.refs;
        const groups = groupRefs(refs, "all", pass);
        expect(groups.reduce((n, g) => n + g.refs.length, 0)).toBe(refs.length);
        expect(() => refCounts(refs, st, pass)).not.toThrow();
        for (const r of refs) expect(() => blockedShots(r, st, pass)).not.toThrow();
        expect(() => missingPlan(refs, pass)).not.toThrow();
        expect(() => keyframePlan(refs, st)).not.toThrow();
        const d = refDefaults(targets, list.defaults);
        for (const r of refs) {
          const tg = refTargetOf(r, d);
          expect(typeof tg.target).toBe("string");
          const plan = editRefsFor(r, targets.targets.find((t) => t.id === tg.target), refs);
          for (const x of plan.refs) expect(typeof x.label).toBe("string");
        }
        // every candidate's file is where the listing says
        for (const r of refs) for (const t of [...r.takes, ...(r.views ?? []).flatMap((v) => v.takes)]) {
          if (t.status === "ok") expect(takeFile(t)).toBeTruthy();
        }
      });

      it(`${dir} ${pass}: the Refs tab renders every ref open`, () => {
        const html = renderWith({
          ep, pass, targets, status: { [statusKey(ep, pass)]: st }, refs: { [ep]: list.refs },
          refDefaults: { [ep]: list.defaults ?? null }, refsFilter: "all",
          refOpen: Object.fromEntries(list.refs.map((r) => [r.id, true])),
        }, createElement(RefsTab));
        for (const r of list.refs) expect(html).toContain(r.id);
        // characters get a drop target per view
        const chars = list.refs.filter((r) => (r.views ?? []).length);
        expect((html.match(/data-drop="image"/g) ?? []).length).toBeGreaterThanOrEqual(chars.length * 4);
      });

      for (const [shot, d] of Object.entries(shots)) {
        if ("error" in d) continue;
        it(`${dir} ${pass}: the inspector renders ${shot}`, () => {
          const { tiles } = shotRefTiles(d.refs_used, list.refs, shot, pass);
          expect(new Set(tiles.map((t) => t.id)).size).toBe(tiles.length);
          const html = renderWith({
            ep, pass, targets, shot, status: { [statusKey(ep, pass)]: st }, refs: { [ep]: list.refs },
            details: { [detailKey(ep, pass, shot)]: d },
          }, createElement(Inspector));
          expect(html).toContain("Refs this shot uses");
          expect(html).toContain("Revert");
        });
      }
    }

    const p86 = load<Record<string, [number, unknown]>>(dir, "phase86.json");
    it.skipIf(!p86)(`${dir}: the Phase 8.6 answers fit the types`, () => {
      const [, gm] = p86!.generate_missing_dry as [number, Parameters<typeof missingResultText>[0]];
      expect(missingResultText(gm).summary).toMatch(/^Generate missing/);
      const [code, dis] = p86!.discard as [number, { moved: string[]; cut_changed: boolean; pass: string }];
      expect(code).toBe(200);
      for (const m of dis.moved) expect(m).toContain("_trash/");
      const [, ref] = p86!.refs_discard as [number, Ref];
      expect(ref).toMatchObject({ exists: false, picked: null, cleared: true });
    });
  }
});
