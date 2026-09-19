// A stateful in-memory implementation of `Api`, built from a real rendered episode
// (fixtures.json, made by web/scripts/make_fixtures.py). Used only by the dev page.
// Renders are simulated: queued -> progress events -> ok, reusing real media.
// Round 2 / Phase 5: a fake folder tree for /browse (mockFs.ts) and stateful refs
// from the kitchen_sink series config (mockRefs.ts), which also decide each shot's
// `missing_refs`.

import type { Api } from "../api";
import { ApiError, UPLOAD_LIMIT, parseJsonSeedSafe, targetsQuery } from "../api";
import type { HostEvent } from "../host";
import { reachable } from "../lib/browse";
import { promptText } from "../lib/format";
import type {
  CutEntry, EpisodeStatus, EpisodeSummary, Lora, MissingRef, Override, OverrideResult, Pass, RefGenerateMissingResult, RefUsed,
  RenderResult, ShotDetail, ShotStatus, ShotTargetSource, TakeDetail, TakeSummary,
} from "../types";
import { keyframeWanted } from "../lib/keyframes";
import { hasViews } from "../lib/refs";
import fixturesRaw from "./fixtures.json?raw";
import { FsError, browse as fsBrowse, fsExists } from "./mockFs";
import { RefError, createMockRefs } from "./mockRefs";
import { svgImage, type KeyframeNeedSpec } from "./mockRefs";
import {
  H3, H3_FL2V, LTX, LTX_REFS, MOCK_TARGETS, MOCK_WIDGET_CHOICES, WAN_I2V, ltxPrompt, mockModelFiles, mockReadiness, mockTakeResolved,
  preset, targetLength,
} from "./mockTargets";

/** series.json's `series.target` in the mock */
const SERIES_TARGET = H3;
/** shots whose script names a target (`target:` on the shot line). Phase 8.5:
 * two Wan 2.2 I2V shots (a required first frame: sh060's missing, sh070's a
 * generated still) and two H3 FL2V shots. */
const SCRIPT_TARGETS: Record<string, string> = { sh040: H3, sh060: WAN_I2V, sh070: WAN_I2V, sh080: H3_FL2V, sh090: H3_FL2V };
/** the script's `first:` / `last:` lines (Phase 8.5) */
const SCRIPT_KEYFRAMES: Record<string, Partial<Record<"first" | "last", string>>> = {
  sh030: { first: "continuity" },
  sh080: { first: "generate" },
  sh090: { first: "none", last: "import" },
};
/** shots built with `dur: model` (their length is an estimate until a take exists) */
const DUR_MODEL = new Set(["sh030", "sh080"]);
/** the episode's negative.txt */
const NEGATIVE_TXT = "blurry, extra fingers, text, watermark, photorealistic";

interface Fixtures {
  ep: string;
  summary: EpisodeSummary;
  status: Partial<Record<Pass, EpisodeStatus>>;
  detail: Partial<Record<Pass, Record<string, ShotDetail>>>;
  shotlist: Record<string, { shots: { prompt?: string | string[] }[] }>;
}

export type Emit = (event: HostEvent, detail: unknown) => void;

export interface MockOptions {
  /** start with no project roots, to see the first-run prompt */
  firstRun?: boolean;
  /** ms of simulated latency per call */
  latency?: number;
  /** base URL of the media folder */
  mediaBase?: string;
}

class MockError extends Error {
  constructor(message: string, public status: number) {
    super(message);
  }
}

const clone = <T>(x: T): T => JSON.parse(JSON.stringify(x));

export function createMockApi(emit: Emit, opts: MockOptions = {}): Api {
  // seeds must survive: parse with the seed-safe parser, like the real client
  const fx = parseJsonSeedSafe<Fixtures>(fixturesRaw);
  const EP = fx.ep;
  let roots: string[] = opts.firstRun ? [] : [EP.replace(/[\\/][^\\/]+[\\/][^\\/]+$/, "")];
  const status: Partial<Record<Pass, EpisodeStatus>> = clone(fx.status);
  const detail: Partial<Record<Pass, Record<string, ShotDetail>>> = clone(fx.detail);
  const cut: Record<Pass, CutEntry[]> = { final: [], proxy: [] };
  const media = opts.mediaBase ?? "/mock/media/";
  /** a simulated take's files -> the real file it borrows */
  const alias = new Map<string, string>();
  let nextPrompt = 1;
  let chain: Promise<void> = Promise.resolve();

  const wait = (ms = opts.latency ?? 120) => (ms > 0 ? new Promise<void>((r) => setTimeout(r, ms)) : Promise.resolve());

  const shotIds = (fx.status.proxy ?? fx.status.final)!.shots.map((s) => s.shot);
  const refs = createMockRefs({
    ep: EP,
    emit,
    shots: shotIds,
    wait: (ms) => wait(ms),
    enqueue: (job) => {
      chain = chain.then(job);
    },
    nextPrompt: () => `mock-${nextPrompt++}-ref`,
    onLiveChange: (ref) => markRefStale(ref),
    needs: () => keyframeNeeds(),
  });

  /**
   * The build's keyframe needs (Phase 8.5): every shot whose target reads
   * keyframes, or whose script asks for one. Wan I2V's first frame is required.
   * The method: the script's, else continuity with a previous shot, else generate.
   */
  function keyframeNeeds(): KeyframeNeedSpec[] {
    const s = status.proxy ?? status.final;
    if (!s) return [];
    const shots = s.shots.filter((x) => !x.orphan);
    const out: KeyframeNeedSpec[] = [];
    shots.forEach((sh, i) => {
      const d = detail.proxy?.[sh.shot] ?? detail.final?.[sh.shot];
      const target = shotTargetOf(sh.shot, d?.override ?? {}).target;
      const caps = MOCK_TARGETS.targets.find((t) => t.id === target)?.capabilities;
      const reads = (caps?.keyframes ?? []) as string[];
      const script = SCRIPT_KEYFRAMES[sh.shot] ?? {};
      for (const which of ["first", "last"] as const) {
        const asked = script[which];
        if (!reads.includes(which) && !asked) continue;
        const neighbour = which === "first" ? i > 0 : i < shots.length - 1;
        out.push({
          shot: sh.shot, which, target,
          need: which === "first" && caps?.requires_first ? "required" : "optional",
          method: asked ?? (neighbour ? "continuity" : "generate"),
          requested: !!asked && asked !== "none",
        });
      }
    });
    return out;
  }

  /** The refs a shot's current target reads (GET /h3pipe/shot `refs_used`), as
   * h3edit.refs_used sends them: `kind` image | audio, `need` on every entry,
   * `thumb` only when the file is there, `id` null for a composed sheet. */
  function refsUsedOf(shot: string, target: string): RefUsed[] {
    const caps = MOCK_TARGETS.targets.find((t) => t.id === target)?.capabilities;
    const out: RefUsed[] = [];
    let pic = 1;
    if (caps?.subject_refs !== false) {
      for (const id of refs.usedByShot(shotIds.indexOf(shot))) {
        const r = refs.info(id);
        if (!r) continue;
        const audio = r.kind === "voice";
        // a voice sample is read only by targets that take one
        if (audio && !caps?.voice_reference) continue;
        out.push({
          id, kind: audio ? "audio" : "image", role: audio ? "voice" : r.kind === "location" ? "plate" : "subject", path: r.path,
          exists: r.exists, need: "required", thumb: r.exists ? r.path : null, slot: audio ? "Audio 1" : `Picture ${pic++}`,
        });
      }
    }
    for (const n of refs.keyframeNeeds(shot)) {
      if (n.method === "none") continue;
      const id = `shot:${shot}:${n.which}`;
      const r = refs.info(id);
      const path = r?.path ?? `refs/shots/${shot}/${n.which}.png`;
      out.push({ id, kind: "image", role: n.which, path, exists: !!r?.exists, need: n.need, thumb: r?.exists ? path : null, slot: `${n.which} frame` });
    }
    if (target === LTX_REFS) {
      const path = `refs/_sheets/${shot}_refsheet.png`;
      out.push({ id: null, kind: "image", role: "reference_sheet", path, exists: true, need: "required", thumb: path, slot: "reference sheet" });
    }
    return out;
  }

  /** The series refs a shot is missing, when its target reads subject pictures (not Wan I2V, LTX-2 or FL2V). */
  function seriesMissing(shot: string): MissingRef[] {
    const d = detail.proxy?.[shot] ?? detail.final?.[shot];
    const target = shotTargetOf(shot, d?.override ?? {}).target;
    const caps = MOCK_TARGETS.targets.find((t) => t.id === target)?.capabilities;
    return caps?.subject_refs === false ? [] : refs.missingFor(shot, shotIds.indexOf(shot));
  }

  /** Missing refs a render can't do without (Wan I2V's first frame): anyway false. */
  function keyframeMissing(shot: string): MissingRef[] {
    return refs.keyframeNeeds(shot)
      .filter((n) => n.need === "required" && !refs.info(`shot:${shot}:${n.which}`)?.exists)
      .map((n) => ({
        slot: n.which === "first" ? "First frame" : "Last frame", kind: "image" as const, path: `refs/shots/${shot}/${n.which}.png`,
        anyway: false,
        why: `${MOCK_TARGETS.targets.find((t) => t.id === n.target)?.label ?? n.target} needs a ${n.which} frame: use the previous shot's frame, generate a still or import one (Refs tab)`,
      }));
  }

  // a reference sheet the ingredients target kept (reference_image on a take)
  refs.addImage("refs/_sheets/sh010_refsheet.png", svgImage("sh010 reference sheet", "Ada · Narrator · kitchen", "sheet", "location", 768, 448));
  for (const shot of shotIds) refs.addImage(`refs/_sheets/${shot}_refsheet.png`, svgImage(`${shot} reference sheet`, "subjects + plate", shot, "location", 768, 448));

  /** A re-picked ref makes the finished takes of the shots using it ref-stale. */
  function markRefStale(ref: string) {
    const idx = new Set(refs.dependents(ref));
    for (const pass of ["final", "proxy"] as Pass[]) {
      const s = status[pass];
      if (!s) continue;
      s.shots.forEach((sh) => {
        const i = shotIds.indexOf(sh.shot);
        if (!idx.has(i)) return;
        for (const t of sh.takes) if (t.status === "ok" && !t.stale.includes("ref")) t.stale = [...t.stale.filter((x) => x !== "unknown"), "ref"];
        for (const t of detail[pass]?.[sh.shot]?.takes ?? []) if (t.status === "ok" && !t.stale.includes("ref")) t.stale = [...t.stale.filter((x) => x !== "unknown"), "ref"];
      });
    }
  }

  function withMissing(e: EpisodeStatus): EpisodeStatus {
    for (const s of e.shots) {
      s.missing_refs = s.orphan ? [] : [...seriesMissing(s.shot), ...keyframeMissing(s.shot)];
      // Phase 8.5: `dur: model` before a take exists: the length is an estimate
      if (DUR_MODEL.has(s.shot)) s.length_estimated = !s.takes.some((t) => t.status === "ok" && t.has_video);
    }
    return e;
  }

  function need(ep: string) {
    if (!roots.length) throw new MockError("No project roots are configured.", 403);
    if (ep !== EP) throw new MockError(`Unknown episode ${ep}`, 404);
  }

  function st(pass: Pass): EpisodeStatus {
    const s = status[pass];
    if (!s) throw new MockError(`${pass} isn't built`, 404);
    return s;
  }

  function shotSt(pass: Pass, shot: string): ShotStatus {
    const s = st(pass).shots.find((x) => x.shot === shot);
    if (!s) throw new MockError(`${shot} is not in the ${pass} shotlist`, 404);
    return s;
  }

  function det(pass: Pass, shot: string): ShotDetail {
    const d = detail[pass]?.[shot];
    if (!d) throw new MockError(`${shot} is not in the ${pass} shotlist`, 404);
    return d;
  }

  /** recompute the cut fields the way h3edit does (latest usable, or the pick) */
  function recut(pass: Pass) {
    for (const s of st(pass).shots) {
      const e = cut[pass].find((c) => c.shot === s.shot);
      const usable = s.takes.filter((t) => t.status === "ok" && t.has_video);
      if (e?.take != null) {
        const t = s.takes.find((x) => x.take === e.take);
        s.cut = { ...s.cut, take: e.take, picked: true, usable: !!t && t.status === "ok" && t.has_video };
      } else {
        const last = usable[usable.length - 1];
        s.cut = { ...s.cut, take: last ? last.take : null, picked: false, usable: !!last };
      }
    }
  }
  // the smoke episode's cut.json picks sh020 t01 in proxy
  for (const pass of ["final", "proxy"] as Pass[]) {
    for (const s of status[pass]?.shots ?? []) if (s.cut.picked && s.cut.take != null) cut[pass].push({ shot: s.shot, take: s.cut.take });
  }

  function effectiveOf(pass: Pass, shot: string): ShotDetail["effective"] {
    const d = det(pass, shot);
    const ov = d.override;
    const b = d.built as { seed?: string; model?: string; steps?: number };
    const base = fx.detail[pass]?.[shot]?.effective;
    const target = shotTargetOf(shot, ov).target;
    const frames = Number((d.built as { length?: number }).length ?? 0);
    const seed = ov.seed ?? (b.seed as string) ?? "0";
    const seed_source = ov.seed != null ? "override" : "stable";
    if (target !== H3) {
      // retargeted: the target's own prompt and defaults; the prompt override is ignored
      const p = preset(target, pass);
      const caps = MOCK_TARGETS.targets.find((t) => t.id === target)?.capabilities;
      const built = d.built as { audio_policy?: string; voice_refs?: unknown[] };
      const notes = [
        ...(ov.prompt != null ? [`the prompt override was ignored: a prompt written for ${H3} isn't valid for ${target}`] : []),
        ...(!caps?.voice_reference && (built.audio_policy === "clone" || built.voice_refs?.length)
          ? [`audio clone renders as generate on ${target}: it takes no voice reference (voices come from each voice line)`] : []),
      ];
      // Phase 8.5: request → shot override → negative.txt (Wan) → series.json → preset
      const neg = caps?.negative_prompt
        ? ov.negative != null
          ? { negative: ov.negative, negative_source: "override" }
          : target === WAN_I2V || target === "wan22_vace"
            ? { negative: NEGATIVE_TXT, negative_source: "negative.txt" }
            : { negative: "worst quality, inconsistent motion, blurry, jittery, distorted", negative_source: "preset" }
        : {};
      const low = typeof p?.model_low === "string" ? { model_low: ov.model_low ?? p.model_low } : {};
      return {
        prompt: ltxPrompt(d.built_prompt), seed, seed_source,
        model: ov.model ?? p?.model ?? "", loras: ov.loras !== undefined ? ov.loras : null, steps: ov.steps ?? p?.steps ?? 8,
        target, width: p?.width ?? null, height: p?.height ?? null, length: targetLength(target, frames),
        ...neg, ...low, ...(notes.length ? { notes } : {}),
      };
    }
    return {
      prompt: ov.prompt != null ? promptText(ov.prompt) : d.built_prompt,
      seed, seed_source,
      model: ov.model ?? base?.model ?? "",
      loras: ov.loras !== undefined ? ov.loras : base?.loras ?? null,
      steps: ov.steps ?? b.steps ?? 4,
      target, width: st(pass).width, height: st(pass).height, length: frames || null,
      // H3 takes no negative
      negative: null, negative_source: "none",
    };
  }

  function syncOverrideSummary(shot: string) {
    for (const pass of ["final", "proxy"] as Pass[]) {
      const s = status[pass]?.shots.find((x) => x.shot === shot);
      const d = detail[pass]?.[shot];
      if (!s || !d) continue;
      s.override = { fields: Object.keys(d.override).sort(), stale: d.override_stale && !!d.override.prompt };
      d.effective = effectiveOf(pass, shot);
      // Phase 8: the target the next render uses, and the one the build compiled for
      const tg = shotTargetOf(shot, d.override);
      d.target = s.target = tg.target;
      d.target_source = s.target_source = tg.source;
      d.built_target = s.built_target = SCRIPT_TARGETS[shot] ?? SERIES_TARGET;
      s.profile = d.profile = null;
      for (const t of s.takes) {
        if (t.target === undefined) t.target = d.takes.find((x) => x.take === t.take)?.sidecar?.target as string | undefined ?? null;
      }
    }
  }

  /** The episode's default target: the editor's, else series.json's. */
  let episodeTarget: string | null = null;

  /** The target a shot's next render uses, and where it comes from (no request here). */
  function shotTargetOf(shot: string, ov: Override): { target: string; source: ShotTargetSource } {
    if (ov.target) return { target: ov.target, source: "override" };
    if (SCRIPT_TARGETS[shot]) return { target: SCRIPT_TARGETS[shot], source: "script" };
    // no target of its own: the editor's episode target, else series.json's
    return { target: episodeTarget ?? SERIES_TARGET, source: episodeTarget ? "episode" : "series" };
  }

  function syncEpisodeTarget() {
    for (const pass of ["final", "proxy"] as Pass[]) {
      const s = status[pass];
      if (!s) continue;
      s.target = episodeTarget ?? SERIES_TARGET;
      s.target_source = episodeTarget ? "editor" : "series";
      s.series_target = SERIES_TARGET;
    }
    for (const id of shotIds) syncOverrideSummary(id);
  }

  // Fixtures for Phase 8: sh030 is retargeted to LTX-2 (its H3 takes now render
  // on another target than the shot), and sh020's t02 was a one-off LTX-2 run,
  // so its takes mix targets. Readiness: sh020's t02 ran without LTX's duration
  // head, and its t01 before the H3 turbo LoRA was installed.
  for (const pass of ["final", "proxy"] as Pass[]) {
    const d = detail[pass]?.sh030;
    if (d) d.override.target = LTX;
    const t2 = detail[pass]?.sh020?.takes.find((t) => t.take === 2);
    if (t2?.sidecar) {
      t2.sidecar.target = LTX;
      t2.sidecar.resolved = { duration_head: { want: "ltx-2.5-duration-head-bf16.safetensors", using: null, how: "off" } };
    }
    // Phase 8.5: sh010's t01 was a one-off run on the ingredients target, which kept its reference sheet
    const s1 = detail[pass]?.sh010?.takes.find((t) => t.take === 1);
    if (s1) {
      if (s1.sidecar) s1.sidecar.target = LTX_REFS;
      s1.reference_image = "refs/_sheets/sh010_refsheet.png";
    }
    const t1 = detail[pass]?.sh020?.takes.find((t) => t.take === 1);
    if (t1?.sidecar) {
      t1.sidecar.resolved = {
        model: { want: "minimax_h3_ref2va_pruned_int8_convrot.safetensors", using: "minimax_h3_ref2va_pruned_int8_convrot.safetensors", how: "exact" },
        loras: { want: "minimax_h3_ref2v_lightx2v_turbo_4step_v0.1_resized_avg_rank_20_bf16.safetensors", using: null, how: "base" },
      };
    }
  }
  syncEpisodeTarget();

  /** PUT / DELETE /h3pipe/override's answer: both passes, and the targets. */
  function overrideResult(shot: string): OverrideResult {
    const res: OverrideResult = { override: {} };
    for (const p of ["final", "proxy"] as Pass[]) {
      const d = detail[p]?.[shot];
      if (!d) continue;
      res.override[p] = { ...clone(d.override), stale: d.override_stale };
      res.target = d.target ?? undefined;
      res.built_target = d.built_target ?? undefined;
    }
    return res;
  }

  function randomSeed(): string {

    // new seeds stay below 2^53 (PLAN.md)
    return String(Math.floor(Math.random() * 2 ** 52));
  }

  function donor(): { thumb: string; strip: string; mp4: string } {
    const all = Object.values(fx.status).flatMap((s) => s!.shots.flatMap((x) => x.takes));
    const pool = all.filter((t) => t.thumb && t.strip && t.mp4);
    const t = pool[Math.floor(Math.random() * pool.length)];
    return { thumb: t.thumb!, strip: t.strip!, mp4: t.mp4! };
  }

  function simulate(pass: Pass, shot: string, take: number, pid: string) {
    chain = chain.then(async () => {
      const s = shotSt(pass, shot);
      const t = s.takes.find((x) => x.take === take);
      if (!t || t.status !== "queued") return;
      await wait(500);
      emit("execution_start", { prompt_id: pid });
      const max = 8;
      for (let v = 1; v <= max; v++) {
        if (t.status !== "queued") return; // cancelled
        emit("progress", { value: v, max, prompt_id: pid, node: "3" });
        await wait(350);
      }
      const d = donor();
      const folder = st(pass).folder;
      const stem = `${folder}/${shot}/${shot}_t${String(take).padStart(2, "0")}`;
      const files = { thumb: `${stem}.jpg`, strip: `${stem}_strip.jpg`, mp4: `${stem}.mp4` };
      alias.set(files.thumb, d.thumb);
      alias.set(files.strip, d.strip);
      alias.set(files.mp4, d.mp4);
      Object.assign(t, { status: "ok", has_video: true, finished: new Date().toISOString(), save_notes: "mock render", ...files });
      const td = det(pass, shot).takes.find((x) => x.take === take);
      if (td) {
        td.status = "ok";
        td.has_video = true;
        td.files = { ...td.files, ...files };
        if (td.sidecar) Object.assign(td.sidecar, { status: "ok", finished: t.finished, frames: s.length });
      }
      recut(pass);
      emit("executing", null);
      emit("execution_success", { prompt_id: pid });
      emit("h3pipe.take", { ep: EP, pass, shot, take, status: "ok", thumb: files.thumb });
    });
  }

  const api: Api = {
    async getConfig() {
      await wait();
      return { roots: [...roots], comfy: "http://127.0.0.1:8188", version: 1 };
    },
    async putConfig(r) {
      await wait();
      if (r.some((x) => !x.trim())) throw new MockError("A root can't be empty.", 400);
      const bad = r.find((x) => !fsExists(x));
      if (bad) throw new MockError(`${bad} doesn't exist (the mock knows C:\\Shows, C:\\Users\\bgstr, D:\\Stock…).`, 400);
      roots = [...r];
      return { roots: [...roots], comfy: "http://127.0.0.1:8188", version: 1 };
    },
    async episodes() {
      await wait();
      return reachable(EP, roots) ? [clone(fx.summary)] : [];
    },
    async episode(ep, pass) {
      await wait();
      need(ep);
      return withMissing(clone(st(pass)));
    },
    async shot(ep, pass, shot) {
      await wait();
      need(ep);
      const d = clone(det(pass, shot));
      refs.list(); // the keyframe needs follow the shot's target
      d.refs_used = refsUsedOf(shot, shotTargetOf(shot, d.override).target);
      return d;
    },
    async build(ep) {
      await wait(900);
      need(ep);
      const report = `  ep05.md -> shotlist/shotlist.json\n  ${fx.summary.shots} shots, 0 warnings\n`;
      emit("h3pipe.episode", { ep: EP });
      return {
        ok: true,
        passes: {
          final: { ok: true, report, error: "" },
          proxy: { ok: true, report: report.replace("shotlist.json", "shotlist_proxy.json"), error: "" },
        },
      };
    },
    fileUrl(_ep, path) {
      const pic = refs.image(path); // a reference sheet a take kept
      if (pic) return pic;
      const real = alias.get(path) ?? path;
      return media + real.split("/").pop();
    },
    async readJson<T>(ep: string, path: string) {
      await wait();
      need(ep);
      const doc = fx.shotlist[path] ?? fx.shotlist[alias.get(path) ?? ""];
      if (!doc) throw new MockError(`No such file: ${path}`, 404);
      return clone(doc) as T;
    },
    async render(req) {
      await wait();
      need(req.ep);
      const out: RenderResult = { queued: [], skipped: [], errors: [] };
      for (const shot of req.shots) {
        let s: ShotStatus;
        try {
          s = shotSt(req.pass, shot);
        } catch (e) {
          out.errors.push({ shot, error: (e as Error).message });
          continue;
        }
        const d = det(req.pass, shot);
        // a target that isn't ready blocks the shot before a take is reserved
        const runOn = req.target || shotTargetOf(shot, d.override).target;
        const blockers = mockReadiness(runOn).missing.filter((m) => m.tier === "required");
        if (blockers.length) {
          out.skipped.push({
            shot,
            target: runOn,
            reason: `required file missing for ${runOn}: ${blockers.map((m) => `${m.want} (models/${m.folder})`).join(", ")}`,
            missing_files: blockers,
          });
          continue;
        }
        refs.list();
        const missing = [...seriesMissing(shot), ...keyframeMissing(shot)];
        // a required keyframe can't be rendered anyway (Wan I2V has nothing to start from)
        if (missing.length && (!req.allow_missing_refs || missing.some((m) => m.anyway === false))) {
          out.skipped.push({
            shot,
            reason: `missing refs: ${missing.map((m) => `${m.slot} (${m.path})`).join(", ")}; pass allow_missing_refs to render anyway`,
            missing_refs: missing,
          });
          continue;
        }
        if (!req.redo) {
          if (s.takes.some((t) => t.status === "queued")) { out.skipped.push({ shot, reason: "has a queued take" }); continue; }
          if (s.takes.some((t) => t.status === "ok" && t.has_video)) { out.skipped.push({ shot, reason: "has a usable take (pass redo: true)" }); continue; }
        }
        const take = (s.takes[s.takes.length - 1]?.take ?? 0) + 1;
        const cur = effectiveOf(req.pass, shot);
        // Phase 8: a one-off target beats the override; another target brings its
        // own prompt and defaults, and a prompt written for H3 doesn't apply
        const target = req.target || cur.target || H3;
        const p = preset(target, req.pass);
        const eff = target === cur.target ? cur : {
          ...cur, target, prompt: target === H3 ? d.built_prompt : ltxPrompt(d.built_prompt),
          model: p?.model ?? cur.model, loras: null, steps: p?.steps ?? cur.steps,
        };
        const prompt = target === H3 ? req.prompt ?? eff.prompt : eff.prompt;
        // a model file of another family is skipped unless allowed (h3jobs.check_models)
        const bad = mockModelFiles(target, "model")?.files.find((f) => f.name === (req.model ?? eff.model) && f.mismatch);
        if (bad && !req.allow_model_mismatch) {
          out.skipped.push({
            shot,
            reason: `model mismatch: ${bad.detail} (pass allow_model_mismatch: true to render anyway)`,
            model_mismatch: [{ param: "model", file: bad.name, family: bad.family ?? "", label: bad.label, message: bad.detail }],
          });
          continue;
        }
        let seed: string;
        let src: string;
        if (req.seed != null) { seed = req.seed; src = "typed"; }
        else if (d.override.seed != null && req.seed_mode !== "new") { seed = d.override.seed; src = "override"; }
        else if (req.seed_mode === "same") { seed = String(d.built.seed); src = "same"; }
        else if (req.seed_mode === "new" || (req.seed_mode === "auto" && s.takes.length)) { seed = randomSeed(); src = "new"; }
        else { seed = String(d.built.seed); src = "stable"; }
        const pid = `mock-${nextPrompt++}-${shot}`;
        const now = new Date().toISOString();
        const overrides = Object.keys(d.override).filter((k) => k !== "note" && (req as unknown as Record<string, unknown>)[k] == null);
        const sum: TakeSummary = {
          take, status: "queued", has_video: false, seed, seed_source: src, note: req.note ?? "",
          overrides, stale: [], thumb: null, strip: null, mp4: null, queued: now, finished: null, save_notes: "", target,
        };
        s.takes.push(sum);
        const loras: Lora[] | null = req.loras ?? eff.loras;
        const td: TakeDetail = {
          take, status: "queued", has_video: false, stale: [],
          sidecar: {
            target, status: "queued", queued: now, comfy_prompt_id: pid, seed, seed_source: src,
            model: req.model ?? eff.model, loras, steps: req.steps ?? eff.steps, overrides,
            parent_take: req.parent_take, note: req.note ?? "", shot, take, pass: req.pass,
            ...(missing.length ? { missing_refs: missing.map((m) => m.slot) } : {}),
            resolved: mockTakeResolved(target),
          },
          files: {},
        };
        d.takes.push(td);
        // the frozen shotlist, so "redo from this take" can read its prompt
        const stem = `${st(req.pass).folder}/${shot}/${shot}_t${String(take).padStart(2, "0")}`;
        fx.shotlist[`${stem}.shotlist.json`] = { shots: [{ prompt }] };
        td.files.shotlist = `${stem}.shotlist.json`;
        out.queued.push({ shot, take, prompt_id: pid, seed, seed_source: src, target });
        emit("h3pipe.take", { ep: EP, pass: req.pass, shot, take, status: "queued", thumb: null });
        simulate(req.pass, shot, take, pid);
      }
      return out;
    },
    async cancel(ref) {
      await wait();
      need(ref.ep);
      const s = shotSt(ref.pass, ref.shot);
      const t = s.takes.find((x) => x.take === ref.take);
      if (!t) throw new MockError(`${ref.shot} has no take ${ref.take}`, 404);
      if (t.status !== "queued") throw new MockError(`${ref.shot} t${ref.take} isn't queued (${t.status})`, 409);
      t.status = "failed";
      t.save_notes = "cancelled";
      const td = det(ref.pass, ref.shot).takes.find((x) => x.take === ref.take);
      if (td) td.status = "failed";
      emit("execution_interrupted", { prompt_id: String(td?.sidecar?.comfy_prompt_id ?? "") });
      emit("h3pipe.take", { ...ref, status: "failed", thumb: null });
      t.finished = new Date().toISOString();
      return { shot: ref.shot, pass: ref.pass, take: ref.take, status: "failed", save_notes: "cancelled", finished: t.finished };
    },
    async discard(ref) {
      await wait();
      need(ref.ep);
      const s = shotSt(ref.pass, ref.shot);
      const i = s.takes.findIndex((x) => x.take === ref.take);
      if (i < 0) throw new MockError(`${ref.shot} has no ${ref.pass} take ${ref.take}`, 404);
      const t = s.takes[i];
      if (t.status === "queued") throw new MockError(`${ref.shot} t${String(ref.take).padStart(2, "0")} is queued: cancel it first`, 409);
      // the files' new paths, under _trash/
      const stem = `${st(ref.pass).folder}/${ref.shot}/${ref.shot}_t${String(ref.take).padStart(2, "0")}`;
      const moved = [t.mp4, t.thumb, t.strip, `${stem}.json`].filter((x): x is string => !!x)
        .map((p) => p.replace(`${st(ref.pass).folder}/`, `${st(ref.pass).folder}/_trash/`));
      s.takes.splice(i, 1);
      const d = det(ref.pass, ref.shot);
      d.takes = d.takes.filter((x) => x.take !== ref.take);
      const before = cut[ref.pass].length;
      cut[ref.pass] = cut[ref.pass].filter((c) => !(c.shot === ref.shot && c.take === ref.take));
      recut(ref.pass);
      emit("h3pipe.episode", { ep: EP });
      return { shot: ref.shot, take: ref.take, pass: ref.pass, moved, cut_changed: cut[ref.pass].length !== before };
    },
    async pick(req) {
      await wait();
      need(req.ep);
      const s = shotSt(req.pass, req.shot);
      const list = cut[req.pass].filter((c) => c.shot !== req.shot);
      if (req.take != null) {
        const t = s.takes.find((x) => x.take === req.take);
        if (!t) throw new MockError(`${req.shot} has no take ${req.take}`, 404);
        if (!(t.status === "ok" && t.has_video) && !req.force) {
          throw new MockError(`${req.shot} t${String(req.take).padStart(2, "0")} is ${t.status}${t.has_video ? "" : " with no mp4"}`, 409);
        }
        list.push({ shot: req.shot, take: req.take });
      }
      cut[req.pass] = list;
      recut(req.pass);
      emit("h3pipe.episode", { ep: EP });
      return { cut: { episode: "ep05", ...cut } };
    },
    async putCut(ep, pass, entries) {
      await wait();
      need(ep);
      cut[pass] = entries;
      recut(pass);
      emit("h3pipe.episode", { ep: EP });
      return { cut: { episode: "ep05", ...cut } };
    },
    async putOverride(req) {
      await wait();
      need(req.ep);
      const passes: Pass[] = req.both ? ["final", "proxy"] : [req.pass];
      for (const pass of ["final", "proxy"] as Pass[]) {
        const d = detail[pass]?.[req.shot];
        if (!d) continue;
        const ov: Override = d.override;
        for (const [k, v] of Object.entries(req.fields) as [keyof Override, unknown][]) {
          const shared = k === "seed" || k === "note" || k === "target";
          if (!shared && !passes.includes(pass)) continue;
          // the target the shot would have anyway (script, else episode) clears the retarget
          if (v === null || (k === "target" && v === shotTargetOf(req.shot, {}).target)) delete ov[k];
          else (ov as Record<string, unknown>)[k] = v;
          if (k === "prompt" && passes.includes(pass)) d.override_stale = false;
        }
      }
      syncOverrideSummary(req.shot);
      emit("h3pipe.episode", { ep: EP });
      return overrideResult(req.shot);
    },
    async deleteOverride(ep, shot, pass) {
      await wait();
      need(ep);
      for (const p of ["final", "proxy"] as Pass[]) {
        const d = detail[p]?.[shot];
        if (!d) continue;
        if (!pass) d.override = {};
        else if (p === pass) d.override = Object.fromEntries(Object.entries(d.override).filter(([k]) => k === "seed" || k === "note" || k === "target"));
        d.override_stale = false;
      }
      syncOverrideSummary(shot);
      emit("h3pipe.episode", { ep: EP });
      return overrideResult(shot);
    },
    async assemble(ep, pass) {
      await wait(1500);
      need(ep);
      const n = st(pass).shots.filter((s) => s.cut.usable).length;
      return { ok: true, output: `${st(pass).folder}/ep05_${pass}.mp4`, report: `  ${n} shots, partial cut\n  wrote ${st(pass).folder}/ep05_${pass}.mp4\n` };
    },
    async browse(path, files) {
      await wait();
      return fsBrowse(path, !!files);
    },
    async refs(ep) {
      await wait();
      need(ep);
      return { refs: refs.list(), defaults: refs.defaults() };
    },
    async putRefDefaults(ep, fields) {
      await wait();
      need(ep);
      if (!("target" in fields) && !("keyframe_target" in fields)) throw new MockError("give target and/or keyframe_target", 400);
      const defaults = refs.setDefaults(fields);
      emit("h3pipe.episode", { ep: EP });
      return { defaults };
    },
    async refsGenerateMissing(req) {
      await wait();
      need(req.ep);
      const pass = req.pass ?? "proxy";
      const kinds = req.kinds ?? ["series", "keyframe"];
      const out: RefGenerateMissingResult = { queued: [], picked: [], skipped: [], errors: [] };
      const list = refs.list();
      const inFlight = (ts: { status: string }[]) => ts.some((t) => t.status === "queued" || t.status === "ok");
      if (kinds.includes("series")) {
        for (const r of list) {
          if (r.kind === "keyframe" || r.exists || !r.path || r.cleared || !(r.used_by[pass] ?? []).length) continue;
          if (r.can_generate === false) {
            out.skipped.push({ ref: r.id, reason: r.why_not ?? "can't be generated" });
            continue;
          }
          // skipped if any candidate is queued or waits to be picked; a character
          // missing all four views gets one generate (view null, a shared seed)
          const all = hasViews(r) ? r.views!.flatMap((v) => v.takes) : r.takes;
          if (inFlight(all)) {
            out.skipped.push({ ref: r.id, reason: "a candidate is queued or waiting to be picked" });
            continue;
          }
          const missingViews = hasViews(r) ? r.views!.filter((v) => v.picked == null && !v.cleared).map((v) => v.view) : [];
          const views: (string | null)[] = !hasViews(r) || missingViews.length === r.views!.length ? [null] : missingViews;
          for (const view of views) {
            if (req.dry_run) {
              out.queued.push({ ref: r.id, view, take: null, prompt_id: null, seed: null, target: req.target ?? r.effective?.target ?? null, method: "generate" });
              continue;
            }
            const g = refs.generate({
              ep: req.ep, ref: r.id, view, count: 1, seed_mode: "auto", seed: null, prompt: null, model: null, loras: null, steps: null,
              note: "generate missing", ...(req.target ? { target: req.target } : {}),
            });
            out.queued.push(...g.queued.map((q) => ({ ...q, seed_source: "stable", method: "generate" })));
          }
        }
      }
      if (kinds.includes("keyframe")) {
        const s = st(pass);
        const order = s.shots.filter((x) => !x.orphan);
        for (const r of list) {
          if (r.kind !== "keyframe" || r.exists || r.cleared || r.need == null || !keyframeWanted(r)) continue;
          if (inFlight(r.takes)) {
            out.skipped.push({ ref: r.id, reason: "a candidate is already queued or waiting to go live" });
            continue;
          }
          const i = order.findIndex((x) => x.shot === r.shot);
          const src = order[i + (r.which === "first" ? -1 : 1)];
          if (r.method === "continuity" && src && src.cut.take != null && src.cut.usable) {
            if (req.dry_run) {
              out.picked.push({ ref: r.id, take: null, method: "continuity" });
              continue;
            }
            const k = await api.refsKeyframe({ ep: req.ep, pass, shot: r.shot!, which: r.which!, pick: true });
            out.picked.push({ ref: r.id, take: k.picked, method: "continuity" });
            continue;
          }
          if (r.method === "import") {
            out.skipped.push({ ref: r.id, reason: "the script asks for an imported file: drop one on it, or Import…" });
            continue;
          }
          if (req.dry_run) {
            out.queued.push({ ref: r.id, view: null, take: null, prompt_id: null, seed: null, target: req.keyframe_target ?? r.effective?.target ?? null, method: "generate" });
            continue;
          }
          const g = refs.generate({
            ep: req.ep, ref: r.id, view: null, count: 1, seed_mode: "auto", seed: null, prompt: null, model: null, loras: null, steps: null,
            note: "generate missing", ...(req.keyframe_target ? { target: req.keyframe_target } : {}),
          });
          out.queued.push(...g.queued.map((q) => ({ ...q, seed_source: "stable", method: "generate" })));
        }
      }

      if (!req.dry_run && (out.queued.length || out.picked.length)) emit("h3pipe.episode", { ep: EP });
      return out;
    },
    async refsUpload(req, onProgress) {
      need(req.ep);
      const name = req.name ?? "upload";
      const total = req.file.size;
      if (total > UPLOAD_LIMIT) throw new MockError(`${name} is over 64 MB`, 413);
      // simulated progress, in quarters
      for (let q = 1; q <= 4; q++) {
        await wait(80);
        onProgress?.(Math.round((total * q) / 4), total);
      }
      const t = refs.upload({ ref: req.ref, view: req.view ?? null, name, pick: !!req.pick });
      emit("h3pipe.episode", { ep: EP });
      return t;
    },
    async refsDiscard(req) {
      await wait();
      need(req.ep);
      const r = refs.discard({ ref: req.ref, view: req.view ?? null, take: req.take });
      emit("h3pipe.episode", { ep: EP });
      return r;
    },
    async refsUnpick(ep, ref, view) {
      await wait();
      need(ep);
      const r = refs.unpick(ref, view ?? null);
      emit("h3pipe.episode", { ep: EP });
      return r;
    },
    refFileUrl(_ep, path) {
      return refs.image(path) ?? media + path.split("/").pop();
    },
    async refsGenerate(req) {
      await wait();
      need(req.ep);
      return refs.generate(req);
    },
    async refsPick(req) {
      await wait();
      need(req.ep);
      const r = refs.pick(req);
      emit("h3pipe.episode", { ep: EP });
      return r;
    },
    async refsImport(req) {
      await wait(300);
      need(req.ep);
      return refs.import(req);
    },
    async refsKeyframe(req) {
      await wait(250);
      need(req.ep);
      const which = req.which ?? "first";
      if (which !== "first" && which !== "last") throw new MockError(`which must be "first" or "last", not ${String(which)}`, 400);
      const shots = st(req.pass).shots.filter((s) => !s.orphan);
      const i = shots.findIndex((s) => s.shot === req.shot);
      if (i < 0) throw new MockError(`${req.shot} is not in the ${req.pass} cut`, 404);
      let src: ShotStatus | undefined;
      if (req.source_shot) {
        src = st(req.pass).shots.find((s) => s.shot === req.source_shot);
        if (!src) throw new MockError(`${req.source_shot} is not in the ${req.pass} cut and has no takes`, 404);
      } else {
        src = shots[i + (which === "first" ? -1 : 1)];
        if (!src) {
          throw new MockError(`${req.shot} is the ${which} shot of the ${req.pass} cut: there is no ${which === "first" ? "previous" : "next"} shot to take a frame from`, 400);
        }
      }
      const takeNo = req.source_take ?? src.cut.take;
      if (takeNo == null) throw new MockError(`${src.shot} has no usable ${req.pass} take to take a frame from (render it first)`, 409);
      const t = src.takes.find((x) => x.take === takeNo);
      if (!t) throw new MockError(`${src.shot} has no ${req.pass} take ${takeNo}`, 404);
      if (t.status !== "ok" || !t.has_video) throw new MockError(`${src.shot} ${req.pass} t${String(takeNo).padStart(2, "0")} is ${t.status}`, 409);
      const frames = src.length ?? 73;
      const spec = req.frame ?? (which === "first" ? "last" : "first");
      const frame = spec === "first" ? 0 : spec === "last" ? frames - 1 : spec < 0 ? spec + frames : spec;
      if (!Number.isInteger(frame) || frame < 0 || frame >= frames) throw new MockError(`frame ${String(spec)} is outside the take (${frames} frames: 0 to ${frames - 1})`, 400);
      const r = refs.keyframe({ shot: req.shot, which, from: { shot: src.shot, take: takeNo, pass: req.pass, frame, frames }, pick: req.pick ?? null });
      if (r.picked === r.takes[r.takes.length - 1]?.take) {
        // the shot's takes that used the old keyframe are ref-stale now
        for (const pass of ["final", "proxy"] as Pass[]) {
          for (const x of status[pass]?.shots.find((s) => s.shot === req.shot)?.takes ?? []) {
            if (x.status === "ok" && !x.stale.includes("ref")) x.stale = [...x.stale.filter((y) => y !== "unknown"), "ref"];
          }
        }
      }
      emit("h3pipe.episode", { ep: EP });
      return r;
    },
    async putRefOverride(req) {
      await wait();
      need(req.ep);
      const override = refs.putOverride(req.ref, req.fields, req.view ?? null);
      emit("h3pipe.episode", { ep: EP });
      return { override };
    },
    async deleteRefOverride(ep, ref, view) {
      await wait();
      need(ep);
      const override = refs.deleteOverride(ref, view ?? null);
      emit("h3pipe.episode", { ep: EP });
      return { override };
    },
    async models() {
      await wait();
      return ["minimax_h3_ref2va_pruned_int8_convrot.safetensors", "minimax_h3_ref2va_bf16.safetensors", "wan2.2_t2v_14B_fp8.safetensors"];
    },
    async loras() {
      await wait();
      return [
        "minimax_h3_ref2v_lightx2v_turbo_4step_v0.1_resized_avg_rank_20_bf16.safetensors",
        "storybook_style_v2.safetensors",
        "film_grain_subtle.safetensors",
      ];
    },
    async targets(o) {
      const { kind, ready } = targetsQuery(o);
      await wait(ready ? 300 : undefined);
      const all = clone(MOCK_TARGETS);
      if (ready) for (const t of all.targets) t.readiness = mockReadiness(t.id);
      return kind ? { ...all, targets: all.targets.filter((t) => t.kind === kind) } : all;
    },
    async putEpisodeTarget(ep, target) {
      await wait();
      need(ep);
      if (target !== null && !MOCK_TARGETS.targets.some((t) => t.id === target && t.kind === "video")) {
        throw new MockError(`${String(target)} isn't a video target (GET /h3pipe/targets?kind=video)`, 400);
      }
      episodeTarget = target;
      syncEpisodeTarget();
      emit("h3pipe.episode", { ep: EP });
      const s = st("proxy");
      return { target: s.target, target_source: s.target_source, series_target: s.series_target };
    },
    async widgetChoices(classType, field) {
      await wait();
      return MOCK_WIDGET_CHOICES[`${classType}|${field}`] ?? null;
    },
    async modelFiles(target, param) {
      await wait();
      const r = mockModelFiles(target, param);
      if (!r) throw new MockError(`${target} declares no model family for ${param}`, 400);
      return r;
    },
    async comfyQueue() {
      await wait();
      return { running: [], pending: [] };
    },
  };

  // Errors from the mock look like the real client's ApiError (status + message)
  const wrapped = {} as Api;
  for (const [k, fn] of Object.entries(api) as [keyof Api, (...a: unknown[]) => unknown][]) {
    (wrapped as unknown as Record<string, unknown>)[k] = k === "fileUrl" || k === "refFileUrl" ? fn : async (...a: unknown[]) => {
      try {
        return await fn(...a);
      } catch (e) {
        if (e instanceof MockError || e instanceof FsError || e instanceof RefError) throw new ApiError(e.message, e.status, `/mock/${k}`);
        throw e;
      }
    };
  }
  return wrapped;
}
