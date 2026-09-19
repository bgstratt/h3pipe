// The dev page's stateful refs, built from the kitchen_sink series config
// (tests/fixtures/kitchen_sink/series.json). The mock episode (DeanStories ep05)
// uses other names, so its shots are mapped onto kitchen_sink refs by index:
// synthetic, but enough to see missing refs block shots, and picks unblock them.
// Candidate images are SVGs made on the fly (data: URLs).

import seriesCfgRaw from "../../../tests/fixtures/kitchen_sink/series.json?raw";
import { VIEWS } from "../lib/refs";
import type {
  Lora, MissingRef, Override, OverrideFields, Pass, Ref, RefEffective, RefGenerateRequest, RefGenerateResult,
  RefFrameSource, RefImportRequest, RefPickRequest, RefTake, RefView, SeedMode,
} from "../types";
import { fsFileExists } from "./mockFs";

interface SeriesConfig {
  style?: { look?: string };
  subjects: Record<string, { kind?: string; name?: string; design?: string; sheet?: string; voice_sample?: string } | string>;
  locations: Record<string, { description?: string; plate?: string } | string>;
}

export class RefError extends Error {
  constructor(message: string, public status: number) {
    super(message);
  }
}

type Emit = (event: "progress" | "execution_start" | "execution_success" | "executing" | "h3pipe.ref" | "h3pipe.episode", detail: unknown) => void;

/** a ref as the mock keeps it: the contract's shape plus its override values */
interface MRef extends Ref {
  base_prompt: string;
  ov: Override;
  subject?: string;
}

const MODEL = "flux2_krea_dev_fp8.safetensors";
const STEPS = 28;

function hash(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619);
  return h >>> 0;
}

function stableSeed(id: string): string {
  // a stand-in for stable_seed: big, deterministic, as a string
  return String(hash(id) * 1_000_003 + hash(id + "#"));
}

function randomSeed(): string {
  return String(Math.floor(Math.random() * 2 ** 52));
}

function esc(s: string): string {
  return s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]!);
}

/** A placeholder picture: a figure / object / backdrop in colours from the seed. */
export function svgImage(title: string, sub: string, seed: string, kind: string, w = 512, h = 512): string {
  const n = hash(seed + title);
  const hue = n % 360;
  const hue2 = (hue + 40 + ((n >> 9) % 80)) % 360;
  const bg = `hsl(${hue} 35% 22%)`;
  const fg = `hsl(${hue2} 70% 62%)`;
  let shape: string;
  if (kind === "location") {
    shape = `<rect x="0" y="${h * 0.62}" width="${w}" height="${h * 0.38}" fill="hsl(${hue} 30% 14%)"/>` +
      `<rect x="${w * 0.12}" y="${h * 0.2}" width="${w * 0.3}" height="${h * 0.42}" fill="${fg}" opacity=".5"/>` +
      `<rect x="${w * 0.55}" y="${h * 0.32}" width="${w * 0.32}" height="${h * 0.3}" fill="${fg}" opacity=".3"/>`;
  } else if (kind === "prop" || kind === "vehicle") {
    shape = `<rect x="${w * 0.25}" y="${h * 0.35}" width="${w * 0.5}" height="${h * 0.3}" rx="${w * 0.06}" fill="${fg}"/>` +
      `<circle cx="${w * 0.35}" cy="${h * 0.68}" r="${w * 0.06}" fill="#111"/><circle cx="${w * 0.65}" cy="${h * 0.68}" r="${w * 0.06}" fill="#111"/>`;
  } else {
    const lean = ((n >> 3) % 30) - 15;
    shape = `<g transform="rotate(${lean} ${w / 2} ${h / 2})"><circle cx="${w / 2}" cy="${h * 0.28}" r="${w * 0.1}" fill="${fg}"/>` +
      `<rect x="${w * 0.4}" y="${h * 0.4}" width="${w * 0.2}" height="${h * 0.34}" rx="${w * 0.05}" fill="${fg}"/>` +
      `<rect x="${w * 0.41}" y="${h * 0.74}" width="${w * 0.07}" height="${h * 0.16}" fill="${fg}"/><rect x="${w * 0.52}" y="${h * 0.74}" width="${w * 0.07}" height="${h * 0.16}" fill="${fg}"/></g>`;
  }
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">` +
    `<rect width="100%" height="100%" fill="${bg}"/>${shape}` +
    `<text x="12" y="30" font-family="sans-serif" font-size="22" fill="#fff">${esc(title)}</text>` +
    `<text x="12" y="${h - 14}" font-family="monospace" font-size="16" fill="#ddd">${esc(sub)}</text></svg>`;
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}

/** The 4-panel sheet stitched from the four picked views. */
function svgSheet(name: string, views: string[]): string {
  const cells = views.map((u, i) => `<image href="${u.replace(/"/g, "&quot;")}" x="${i * 256}" y="0" width="256" height="256"/>`).join("");
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="256" viewBox="0 0 1024 256">${cells}` +
    `<text x="8" y="248" font-family="sans-serif" font-size="14" fill="#fff">${esc(name)} sheet (stitched)</text></svg>`;
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}

export interface MockRefs {
  list(): Ref[];
  missingFor(shot: string, index: number): MissingRef[];
  usedByShot(index: number): string[];
  image(path: string): string | undefined;
  generate(req: RefGenerateRequest): RefGenerateResult;
  pick(req: RefPickRequest): Ref;
  import(req: RefImportRequest): RefTake & { view: string | null };
  /** a keyframe take cut out of a video take (the api resolves the source) */
  keyframe(req: { shot: string; which: "first" | "last"; from: RefFrameSource; pick: boolean | null }): Ref;
  putOverride(ref: string, fields: OverrideFields): void;
  deleteOverride(ref: string): void;
  /** the dependents to mark ref-stale after a pick (shot indexes) */
  dependents(ref: string): number[];
}

export function createMockRefs(opts: {
  ep: string;
  emit: Emit;
  shots: string[];
  wait: (ms: number) => Promise<void>;
  enqueue: (job: () => Promise<void>) => void;
  nextPrompt: () => string;
  onLiveChange: (ref: string) => void;
}): MockRefs {
  const seriesCfg = JSON.parse(seriesCfgRaw) as SeriesConfig;
  const look = seriesCfg.style?.look ?? "";
  const refs: MRef[] = [];
  const images = new Map<string, string>();

  const key = (id: string) => id.replace(/:/g, "__");
  const takePath = (r: MRef, view: string | null, take: number, ext = "png") =>
    `refs/_takes/${key(r.id)}/${key(r.id)}${view ? `_${view}` : ""}_t${String(take).padStart(2, "0")}.${ext}`;

  for (const [id, raw] of Object.entries(seriesCfg.subjects)) {
    if (id.startsWith("_") || typeof raw !== "object") continue;
    const kind = (raw.kind ?? "character") as Ref["kind"];
    const name = raw.name ?? id;
    const base_prompt = kind === "character"
      ? `A character reference sheet of ${name}: ${raw.design ?? ""}. ${look}. Plain light-grey background, even studio light.`
      : `A reference picture of ${name}: ${raw.design ?? ""}, alone on a plain light-grey background. ${look}.`;
    refs.push({
      id: `subject:${id}`, scope: "series", kind, name, path: raw.sheet ?? `refs/${id}/${id}.png`, exists: false, sha1: null,
      used_by: {}, prompt: base_prompt, base_prompt, override: { fields: [], stale: false }, ov: {}, subject: id,
      ...(kind === "character" ? { views: VIEWS.map((v) => ({ view: v.view, picked: null, takes: [] })) } : {}),
      takes: [], picked: null,
    });
    if (raw.voice_sample) {
      refs.push({
        id: `voice:${id}`, scope: "series", kind: "voice", name: `${name}'s voice`, path: raw.voice_sample, exists: false, sha1: null,
        used_by: {}, prompt: "", base_prompt: "", override: { fields: [], stale: false }, ov: {}, subject: id, takes: [], picked: null,
      });
    }
  }
  for (const [id, raw] of Object.entries(seriesCfg.locations)) {
    if (id.startsWith("_") || typeof raw !== "object") continue;
    const base_prompt = `A background plate, no people: ${raw.description ?? id}. ${look}.`;
    refs.push({
      id: `location:${id}`, scope: "series", kind: "location", name: id.replace(/_/g, " "), path: raw.plate ?? `refs/_bg/${id}.png`,
      exists: false, sha1: null, used_by: {}, prompt: base_prompt, base_prompt, override: { fields: [], stale: false }, ov: {},
      takes: [], picked: null,
    });
  }
  const byId = (id: string) => {
    const r = refs.find((x) => x.id === id);
    if (!r) throw new RefError(`No ref ${id} in series.json`, 404);
    return r;
  };

  function effective(r: MRef): RefEffective {
    return {
      prompt: typeof r.ov.prompt === "string" ? r.ov.prompt : r.base_prompt,
      seed: r.ov.seed ?? stableSeed(r.id),
      model: r.ov.model ?? MODEL,
      loras: r.ov.loras !== undefined ? r.ov.loras ?? null : null,
      steps: r.ov.steps ?? STEPS,
    };
  }

  function addTake(r: MRef, view: string | null, opts: { status: RefTake["status"]; seed: string; source?: RefTake["source"]; note?: string; ext?: string; sourceName?: string }): RefTake {
    const list = view ? r.views!.find((v) => v.view === view)!.takes : r.takes;
    const take = (list[list.length - 1]?.take ?? 0) + 1;
    const eff = effective(r);
    const t: RefTake = {
      take, status: opts.status, seed: opts.source === "imported" ? null : opts.seed, image: null,
      source: opts.source ?? "generated", note: opts.note ?? "", prompt: opts.source === "imported" ? undefined : eff.prompt,
      model: opts.source === "imported" ? undefined : eff.model, steps: opts.source === "imported" ? undefined : eff.steps,
      loras: eff.loras, queued: new Date().toISOString(), finished: null, save_notes: "",
    };
    list.push(t);
    if (opts.status === "ok") finishTake(r, view, t, opts.ext, opts.sourceName);
    return t;
  }

  function finishTake(r: MRef, view: string | null, t: RefTake, ext?: string, sourceName?: string) {
    t.status = "ok";
    t.finished = new Date().toISOString();
    t.image = takePath(r, view, t.take, ext ?? (r.kind === "voice" ? "wav" : "png"));
    if (r.kind !== "voice") {
      const sub = sourceName ? `imported: ${sourceName}` : `${view ? view.replace(/^\d+_/, "") + " · " : ""}t${String(t.take).padStart(2, "0")} · ${t.seed}`;
      images.set(t.image, svgImage(r.name, sub, `${t.seed ?? sourceName}${view ?? ""}`, r.kind));
    }
  }

  function setLive(r: MRef) {
    if (!r.path) return;
    const path = r.path;
    r.sha1 = hash(`${r.id}${Date.now()}${Math.random()}`).toString(16).padStart(8, "0");
    if (r.views) {
      const urls = r.views.map((v) => images.get(v.takes.find((t) => t.take === v.picked)?.image ?? "") ?? "");
      images.set(path, svgSheet(r.name, urls));
    } else {
      const t = r.takes.find((x) => x.take === r.picked);
      const img = t?.image ? images.get(t.image) : undefined;
      if (img) images.set(path, img);
    }
    r.exists = true;
  }

  // ---- the starting state -------------------------------------------------
  const seedOf = (r: MRef, n: number) => stableSeed(`${r.id}#${n}`);
  const charViews = (id: string, perView: number[], picks: (number | null)[]) => {
    const r = byId(id);
    r.views!.forEach((v, i) => {
      for (let k = 0; k < perView[i]; k++) addTake(r, v.view, { status: "ok", seed: seedOf(r, k) });
      v.picked = picks[i];
    });
    if (r.views!.every((v) => v.picked != null)) setLive(r);
  };
  charViews("subject:ada", [2, 2, 1, 2], [1, 2, 1, 1]);
  charViews("subject:bo", [2, 1, 1, 0], [2, 1, null, null]);
  charViews("subject:rex", [1, 0, 0, 0], [null, null, null, null]);
  charViews("subject:narrator", [1, 1, 1, 1], [1, 1, 1, 1]);
  {
    const k = byId("subject:kettle");
    for (let i = 0; i < 3; i++) addTake(k, null, { status: "ok", seed: seedOf(k, i) });
    k.picked = 2;
    setLive(k);
    const van = byId("subject:van");
    const t = addTake(van, null, { status: "failed", seed: seedOf(van, 0) });
    t.save_notes = "CUDA out of memory (mock)";
    const kit = byId("location:kitchen");
    addTake(kit, null, { status: "ok", seed: seedOf(kit, 0) });
    addTake(kit, null, { status: "ok", seed: "", source: "imported", sourceName: "kitchen_photo.jpg" });
    kit.picked = 1;
    setLive(kit);
    const kw = byId("location:kitchen_window");
    addTake(kw, null, { status: "ok", seed: seedOf(kw, 0) });
    addTake(kw, null, { status: "ok", seed: seedOf(kw, 1) });
    const va = byId("voice:ada");
    addTake(va, null, { status: "ok", seed: "", source: "imported", sourceName: "ada_sample.wav", ext: "wav" });
    va.picked = 1;
    va.exists = true;
    va.sha1 = "5eed0000";
  }

  // ---- which refs each mock shot uses -----------------------------------------
  const CHAR = ["subject:ada", "subject:narrator", "subject:ada", "subject:bo", "subject:narrator", "subject:cy", "subject:ada", "subject:rex"];
  const LOC = ["location:kitchen", "location:kitchen", "location:kitchen_window", "location:kitchen", "location:street"];
  function usesOf(i: number): { ref: string; slot: string }[] {
    const out: { ref: string; slot: string }[] = [{ ref: LOC[Math.floor(i / 5) % LOC.length], slot: "Picture 1" }];
    let pic = 2;
    const c = CHAR[i % CHAR.length];
    out.push({ ref: c, slot: `Picture ${pic++}` });
    if (i % 3 === 0 && CHAR[(i + 1) % CHAR.length] !== c) out.push({ ref: CHAR[(i + 1) % CHAR.length], slot: `Picture ${pic++}` });
    if (i % 7 === 0) out.push({ ref: "subject:kettle", slot: `Picture ${pic++}` });
    if (i % 11 === 3) out.push({ ref: "subject:van", slot: `Picture ${pic++}` });
    if (i % 4 === 0) {
      const v = `voice:${c.slice("subject:".length)}`;
      if (refs.some((r) => r.id === v)) out.push({ ref: v, slot: "Audio 1" });
    }
    return out;
  }
  const shots = opts.shots;
  for (const r of refs) {
    const used = shots.filter((_, i) => usesOf(i).some((u) => u.ref === r.id));
    r.used_by = { final: used, proxy: used } as Record<Pass, string[]>;
  }

  function view(r: MRef): Ref {
    const { base_prompt, ov, subject, ...rest } = r;
    void subject;
    const eff = effective(r);
    return JSON.parse(JSON.stringify({
      ...rest,
      prompt: eff.prompt,
      override: { fields: Object.keys(ov).sort(), stale: false },
      // not in the contract yet (see TODO(contract) in api.ts): the editor reads them if present
      override_values: ov,
      effective: eff,
      built_prompt: base_prompt,
    }));
  }

  function simulate(r: MRef, v: string | null, t: RefTake, pid: string) {
    opts.enqueue(async () => {
      if (t.status !== "queued") return;
      await opts.wait(400);
      opts.emit("execution_start", { prompt_id: pid });
      const max = 6;
      for (let k = 1; k <= max; k++) {
        opts.emit("progress", { value: k, max, prompt_id: pid, node: "9" });
        await opts.wait(250);
      }
      finishTake(r, v, t);
      opts.emit("executing", null);
      opts.emit("execution_success", { prompt_id: pid });
      opts.emit("h3pipe.ref", { ep: opts.ep, ref: r.id, view: v, take: t.take, status: "ok" });
    });
  }

  function pickSeed(r: MRef, list: RefTake[], mode: SeedMode, typed: string | null, forceNew: boolean): string {
    if (typed) return typed;
    if (mode === "same") return list[list.length - 1]?.seed ?? effective(r).seed;
    if (mode === "new" || forceNew) return randomSeed();
    return list.some((t) => t.source === "generated") ? randomSeed() : effective(r).seed;
  }

  return {
    list: () => refs.map(view),
    usedByShot: (i) => usesOf(i).map((u) => u.ref),
    missingFor(_shot, i) {
      const out: MissingRef[] = [];
      for (const u of usesOf(i)) {
        const r = byId(u.ref);
        if (r.exists || !r.path) continue;
        out.push({ slot: u.slot, kind: r.kind === "voice" ? "audio" : "image", path: r.path, ...(r.subject ? { subject: r.subject } : {}) });
      }
      return out;
    },
    dependents(ref) {
      return shots.map((_, i) => i).filter((i) => usesOf(i).some((u) => u.ref === ref));
    },
    image: (path) => images.get(path),
    generate(req) {
      const r = byId(req.ref);
      if (r.kind === "voice") throw new RefError("Nothing generates voices yet: import a recording.", 400);
      if (req.count < 1 || req.count > 4) throw new RefError("count is 1 to 4", 400);
      const views: (string | null)[] = r.views ? (req.view ? [req.view] : VIEWS.map((v) => v.view)) : [null];
      const out: RefGenerateResult = { queued: [], errors: [] };
      for (let c = 0; c < req.count; c++) {
        let shared: string | null = null; // all four views share one seed per candidate
        for (const v of views) {
          const list = v ? r.views!.find((x) => x.view === v)!.takes : r.takes;
          // count > 1: each candidate gets a new seed
          const seed: string = shared ?? pickSeed(r, list, req.seed_mode, req.seed, req.count > 1);
          shared = seed;
          const t = addTake(r, v, { status: "queued", seed, note: req.note });
          // one-off settings for this call land in the candidate's record
          if (req.prompt != null) t.prompt = req.prompt;
          if (req.model != null) t.model = req.model;
          if (req.steps != null) t.steps = req.steps;
          if (req.loras != null) t.loras = req.loras;
          const pid = opts.nextPrompt();
          t.comfy_prompt_id = pid;
          out.queued.push({ ref: r.id, view: v, take: t.take, prompt_id: pid, seed });
          opts.emit("h3pipe.ref", { ep: opts.ep, ref: r.id, view: v, take: t.take, status: "queued" });
          simulate(r, v, t, pid);
        }
      }
      return out;
    },
    pick(req) {
      const r = byId(req.ref);
      let rv: RefView | undefined;
      if (r.views) {
        if (!req.view) throw new RefError(`${r.name} is a character: pick a take per view`, 400);
        rv = r.views.find((v) => v.view === req.view);
        if (!rv) throw new RefError(`No view ${req.view}`, 404);
      }
      const list = rv ? rv.takes : r.takes;
      const t = list.find((x) => x.take === req.take);
      if (!t) throw new RefError(`No take ${req.take}`, 404);
      if (t.status !== "ok") throw new RefError(`t${String(req.take).padStart(2, "0")} is ${t.status}; only a finished candidate can be picked`, 409);
      if (rv) {
        rv.picked = req.take;
        if (r.views!.every((v) => v.picked != null)) setLive(r);
      } else {
        r.picked = req.take;
        if (r.kind === "voice") {
          r.exists = true;
          r.sha1 = hash(`${r.id}${Date.now()}`).toString(16);
        } else setLive(r);
      }
      opts.onLiveChange(r.id);
      return view(r);
    },
    import(req) {
      const r = byId(req.ref);
      if (r.views && !req.view) throw new RefError("Import into one view of a character", 400);
      if (!fsFileExists(req.source_path)) throw new RefError(`${req.source_path} doesn't exist on the ComfyUI machine`, 404);
      const name = req.source_path.split(/[\\/]/).pop() ?? "file";
      const audio = r.kind === "voice";
      if (audio !== /\.(wav|mp3|flac|ogg|m4a)$/i.test(name)) throw new RefError(audio ? "A voice takes an audio file" : "Import an image file", 400);
      const t = addTake(r, req.view ?? null, { status: "ok", seed: "", source: "imported", sourceName: name, ext: audio ? "wav" : "png" });
      opts.emit("h3pipe.ref", { ep: opts.ep, ref: r.id, view: req.view ?? null, take: t.take, status: "ok" });
      return { ...JSON.parse(JSON.stringify(t)), view: req.view ?? null };
    },
    keyframe(req) {
      const id = `shot:${req.shot}:${req.which}`;
      let r = refs.find((x) => x.id === id);
      if (!r) {
        r = {
          id, scope: "shot", kind: "keyframe", name: `${req.shot} ${req.which} frame`, path: `refs/shots/${req.shot}/${req.which}.png`,
          exists: false, sha1: null, used_by: { final: [], proxy: [] }, prompt: null, base_prompt: "",
          override: { fields: [], stale: false }, ov: {}, takes: [], picked: null, can_generate: false,
          why_not: "keyframes aren't generated: use another shot's frame or import an image",
        };
        refs.push(r);
      }
      const f = req.from;
      const t = addTake(r, null, { status: "queued", seed: "", source: "frame" });
      t.seed = null;
      t.prompt = undefined;
      t.model = undefined;
      t.steps = undefined;
      t.from = { ...f };
      t.status = "ok";
      t.finished = new Date().toISOString();
      t.image = takePath(r, null, t.take);
      t.width = 448;
      t.height = 256;
      t.save_notes = `frame ${f.frame} of ${f.frames} of ${f.shot} ${f.pass} t${String(f.take).padStart(2, "0")}`;
      images.set(t.image, svgImage(`${f.shot} t${String(f.take).padStart(2, "0")}`, `frame ${f.frame} → ${req.shot} ${req.which}`, `${f.shot}${f.take}${f.frame}`, "location", 448, 256));
      opts.emit("h3pipe.ref", { ep: opts.ep, ref: id, view: null, take: t.take, status: "ok" });
      if (req.pick || (req.pick == null && !r.exists)) {
        r.picked = t.take;
        setLive(r);
        opts.onLiveChange(r.id);
        opts.emit("h3pipe.ref", { ep: opts.ep, ref: id, view: null, take: t.take, status: "picked" });
      }
      return view(r);
    },
    putOverride(ref, fields) {
      const r = byId(ref);
      if (r.kind === "voice") throw new RefError("A voice has no generation settings", 400);
      for (const [k, v] of Object.entries(fields) as [keyof Override, unknown][]) {
        if (v === null || v === "") delete r.ov[k];
        else (r.ov as Record<string, unknown>)[k] = k === "loras" ? (v as Lora[]) : v;
      }
    },
    deleteOverride(ref) {
      byId(ref).ov = {};
    },
  };
}
