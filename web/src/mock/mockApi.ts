// A stateful in-memory implementation of `Api`, built from a real rendered episode
// (fixtures.json, made by web/scripts/make_fixtures.py). Used only by the dev page.
// Renders are simulated: queued -> progress events -> ok, reusing real media.
// Round 2 / Phase 5: a fake folder tree for /browse (mockFs.ts) and stateful refs
// from the kitchen_sink bible (mockRefs.ts), which also decide each shot's
// `missing_refs`.

import type { Api } from "../api";
import { ApiError, parseJsonSeedSafe } from "../api";
import type { HostEvent } from "../host";
import { reachable } from "../lib/browse";
import { promptText } from "../lib/format";
import type {
  CutEntry, EpisodeStatus, EpisodeSummary, Lora, Override, Pass, RenderResult, ShotDetail,
  ShotStatus, TakeDetail, TakeSummary,
} from "../types";
import fixturesRaw from "./fixtures.json?raw";
import { FsError, browse as fsBrowse, fsExists } from "./mockFs";
import { RefError, createMockRefs } from "./mockRefs";

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
  });

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
    for (const s of e.shots) s.missing_refs = s.orphan ? [] : refs.missingFor(s.shot, shotIds.indexOf(s.shot));
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
    return {
      prompt: ov.prompt != null ? promptText(ov.prompt) : d.built_prompt,
      seed: ov.seed ?? (b.seed as string) ?? "0",
      seed_source: ov.seed != null ? "override" : "stable",
      model: ov.model ?? base?.model ?? "",
      loras: ov.loras !== undefined ? ov.loras : base?.loras ?? null,
      steps: ov.steps ?? b.steps ?? 4,
    };
  }

  function syncOverrideSummary(shot: string) {
    for (const pass of ["final", "proxy"] as Pass[]) {
      const s = status[pass]?.shots.find((x) => x.shot === shot);
      const d = detail[pass]?.[shot];
      if (!s || !d) continue;
      s.override = { fields: Object.keys(d.override).sort(), stale: d.override_stale && !!d.override.prompt };
      d.effective = effectiveOf(pass, shot);
    }
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
      return clone(det(pass, shot));
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
        const missing = refs.missingFor(shot, shotIds.indexOf(shot));
        if (missing.length && !req.allow_missing_refs) {
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
        const eff = effectiveOf(req.pass, shot);
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
          overrides, stale: [], thumb: null, strip: null, mp4: null, queued: now, finished: null, save_notes: "",
        };
        s.takes.push(sum);
        const loras: Lora[] | null = req.loras ?? eff.loras;
        const td: TakeDetail = {
          take, status: "queued", has_video: false, stale: [],
          sidecar: {
            target: "minimax_h3_ref2va", status: "queued", queued: now, comfy_prompt_id: pid, seed, seed_source: src,
            model: req.model ?? eff.model, loras, steps: req.steps ?? eff.steps, overrides,
            parent_take: req.parent_take, note: req.note ?? "", shot, take, pass: req.pass,
            ...(missing.length ? { missing_refs: missing.map((m) => m.slot) } : {}),
          },
          files: {},
        };
        d.takes.push(td);
        // the frozen shotlist, so "redo from this take" can read its prompt
        const stem = `${st(req.pass).folder}/${shot}/${shot}_t${String(take).padStart(2, "0")}`;
        fx.shotlist[`${stem}.shotlist.json`] = { shots: [{ prompt: req.prompt ?? eff.prompt }] };
        td.files.shotlist = `${stem}.shotlist.json`;
        out.queued.push({ shot, take, prompt_id: pid, seed, seed_source: src });
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
      return { status: "failed" };
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
          const shared = k === "seed" || k === "note";
          if (!shared && !passes.includes(pass)) continue;
          if (v === null) delete ov[k];
          else (ov as Record<string, unknown>)[k] = v;
          if (k === "prompt" && passes.includes(pass)) d.override_stale = false;
        }
      }
      syncOverrideSummary(req.shot);
      emit("h3pipe.episode", { ep: EP });
      const res: Record<string, Override & { stale: boolean }> = {};
      for (const p of ["final", "proxy"] as Pass[]) {
        const d = detail[p]?.[req.shot];
        if (d) res[p] = { ...clone(d.override), stale: d.override_stale };
      }
      return { override: res };
    },
    async deleteOverride(ep, shot, pass) {
      await wait();
      need(ep);
      for (const p of ["final", "proxy"] as Pass[]) {
        const d = detail[p]?.[shot];
        if (!d) continue;
        if (!pass) d.override = {};
        else if (p === pass) d.override = Object.fromEntries(Object.entries(d.override).filter(([k]) => k === "seed" || k === "note"));
        d.override_stale = false;
      }
      syncOverrideSummary(shot);
      emit("h3pipe.episode", { ep: EP });
      return {};
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
      return { refs: refs.list() };
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
    async putRefOverride(req) {
      await wait();
      need(req.ep);
      refs.putOverride(req.ref, req.fields);
      return {};
    },
    async deleteRefOverride(ep, ref) {
      await wait();
      need(ep);
      refs.deleteOverride(ref);
      return {};
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
