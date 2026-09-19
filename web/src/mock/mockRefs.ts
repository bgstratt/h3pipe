// The dev page's stateful refs, built from the kitchen_sink series config
// (tests/fixtures/kitchen_sink/series.json). The mock episode (DeanStories ep05)
// uses other names, so its shots are mapped onto kitchen_sink refs by index:
// synthetic, but enough to see missing refs block shots, and picks unblock them.
// Candidate images are SVGs made on the fly (data: URLs).
//
// The shapes follow what h3refs.ref_json really sends (checked against a real
// episode): `views: []` on every ref that isn't a character, a character's
// top-level `effective` is just `{target}` and each view has its own prompt,
// override and effective, a voice's candidates carry `audio` (with `image`
// null), and the narrator is voice-only here, as in real series configs:
// `path: null`, `can_generate: false`.

import seriesCfgRaw from "../../../tests/fixtures/kitchen_sink/series.json?raw";
import { VIEWS } from "../lib/refs";
import type {
  EditRef, Lora, MissingRef, Override, OverrideFields, Pass, Ref, RefDefaultSource, RefDefaults, RefEffective, RefGenerateRequest,
  RefGenerateResult, RefFrameSource, RefImportRequest, RefPickRequest, RefTake, RefView, SeedMode,
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
  /** the ref's own override */
  ov: Override;
  /** a character's per-view overrides */
  vov: Record<string, Override>;
  subject?: string;
  /** views cleared by Clear (a character) */
  vcleared: Record<string, boolean>;
  why?: string;
}

const MODEL = "flux2_krea_dev_fp8.safetensors";
const STEPS = 28;
/** the mock's voice-only character (its series config entry has no picture here) */
const VOICE_ONLY = new Set(["narrator"]);

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

/** What the build says a shot's keyframe is (Phase 8.5), from its target and script. */
export interface KeyframeNeedSpec {
  shot: string;
  which: "first" | "last";
  need: "required" | "optional";
  method: string;
  target: string;
  requested: boolean;
  /** the script's raw line, when it has one */
  script?: string | null;
}

/** The image targets' presets the mock generates with, and how many references an edit one takes. */
const IMAGE_MODELS: Record<string, { model: string; steps: number; edit?: number }> = {
  krea2: { model: MODEL, steps: STEPS },
  z_image_turbo: { model: "z_image_turbo_bf16.safetensors", steps: 8 },
  flux2_klein: { model: "flux2_klein_9b_fp8.safetensors", steps: 4 },
  flux2_klein_edit: { model: "flux2_klein_9b_fp8.safetensors", steps: 4, edit: 4 },
  flux_kontext: { model: "flux1-kontext-dev-fp8.safetensors", steps: 20, edit: 1 },
  illustrious_sdxl: { model: "illustriousXL_v01.safetensors", steps: 8 },
};
/** the built-in defaults (series.json has no `refs` block in the mock) */
export const MOCK_REF_DEFAULTS = { target: "krea2", keyframe_target: "flux2_klein_edit" };

export interface MockRefs {
  list(): Ref[];
  /** GET /h3pipe/refs `defaults` */
  defaults(): RefDefaults;
  /** PUT /h3pipe/refs/defaults: null clears; a key left out is kept */
  setDefaults(fields: { target?: string | null; keyframe_target?: string | null }): RefDefaults;
  /** a ref's file state, for refs_used */
  info(id: string): { path: string | null; exists: boolean; kind: string; sha1: string | null } | undefined;
  /** the keyframes a shot needs now */
  keyframeNeeds(shot: string): KeyframeNeedSpec[];
  /** DELETE /h3pipe/refs/pick */
  unpick(ref: string, view: string | null): Ref;
  /** register a picture served by path (a take's reference image) */
  addImage(path: string, url: string): void;
  missingFor(shot: string, index: number): MissingRef[];
  usedByShot(index: number): string[];
  image(path: string): string | undefined;
  generate(req: RefGenerateRequest): RefGenerateResult;
  pick(req: RefPickRequest): Ref;
  import(req: RefImportRequest): RefTake & { view: string | null };
  /** the multipart import (Phase 8.6): a file by name, picked with `pick` */
  upload(req: { ref: string; view: string | null; name: string; pick: boolean }): RefTake & { view: string | null; original_name: string };
  /** POST /h3pipe/refs/discard */
  discard(req: { ref: string; view: string | null; take: number }): Ref;
  /** a keyframe take cut out of a video take (the api resolves the source) */
  keyframe(req: { shot: string; which: "first" | "last"; from: RefFrameSource; pick: boolean | null }): Ref;
  putOverride(ref: string, fields: OverrideFields, view?: string | null): Override & { stale: boolean };
  deleteOverride(ref: string, view?: string | null): Override & { stale: boolean };
  /** the dependents to mark ref-stale after a pick (shot indexes) */
  dependents(ref: string): number[];
  /** Phase 9a: every ref override (and a character view's), for promote */
  overrideList(): { ref: string; view: string | null; subject: string | null; kind: string; values: Override }[];
  /** Phase 9a: drop some fields of a ref's (or a view's) override (promoted) */
  dropOverrideFields(ref: string, view: string | null, fields: string[]): void;
}

export function createMockRefs(opts: {
  ep: string;
  emit: Emit;
  shots: string[];
  wait: (ms: number) => Promise<void>;
  enqueue: (job: () => Promise<void>) => void;
  nextPrompt: () => string;
  onLiveChange: (ref: string) => void;
  /** Phase 8.5: every shot's keyframe needs now (they follow its target) */
  needs?: () => KeyframeNeedSpec[];
}): MockRefs {
  const seriesCfg = JSON.parse(seriesCfgRaw) as SeriesConfig;
  const look = seriesCfg.style?.look ?? "";
  const refs: MRef[] = [];
  const images = new Map<string, string>();
  /** the episode's image targets (overrides.json episode.refs_target / keyframe_target) */
  const chosen: { target: string | null; keyframe_target: string | null } = { target: null, keyframe_target: null };

  const key = (id: string) => id.replace(/:/g, "__");
  const takePath = (r: MRef, view: string | null, take: number, ext = "png") =>
    `refs/_takes/${key(r.id)}/${key(r.id)}${view ? `_${view}` : ""}_t${String(take).padStart(2, "0")}.${ext}`;
  const base = (id: string, kind: Ref["kind"], extra: Partial<MRef>): MRef => ({
    id, scope: "series", kind, name: id, path: null, exists: false, sha1: null, used_by: {}, prompt: "", base_prompt: "",
    override: { fields: [], stale: false }, ov: {}, vov: {}, vcleared: {}, takes: [], picked: null, views: [], ...extra,
  });

  for (const [id, raw] of Object.entries(seriesCfg.subjects)) {
    if (id.startsWith("_") || typeof raw !== "object") continue;
    const kind = (raw.kind ?? "character") as Ref["kind"];
    const name = raw.name ?? id;
    const voiceOnly = VOICE_ONLY.has(id);
    const base_prompt = kind === "character"
      ? `A character reference sheet of ${name}: ${raw.design ?? ""}. ${look}. Plain light-grey background, even studio light.`
      : `A reference picture of ${name}: ${raw.design ?? ""}, alone on a plain light-grey background. ${look}.`;
    refs.push(base(`subject:${id}`, kind, {
      name, path: voiceOnly ? null : raw.sheet ?? `refs/${id}/${id}.png`, prompt: base_prompt, base_prompt, subject: id,
      ...(voiceOnly ? { why: `the series config names no sheet for ${name} (a voice-only character)` } : {}),
      ...(kind === "character" ? { views: VIEWS.map((v) => ({ view: v.view, picked: null, takes: [] })) } : {}),
    }));
    if (raw.voice_sample) {
      refs.push(base(`voice:${id}`, "voice", {
        name: `${name}'s voice`, path: raw.voice_sample, subject: id, why: "nothing generates voices yet: import a recording",
      }));
    }
  }
  for (const [id, raw] of Object.entries(seriesCfg.locations)) {
    if (id.startsWith("_") || typeof raw !== "object") continue;
    const base_prompt = `A background plate, no people: ${raw.description ?? id}. ${look}.`;
    refs.push(base(`location:${id}`, "location", { name: id.replace(/_/g, " "), path: raw.plate ?? `refs/_bg/${id}.png`, prompt: base_prompt, base_prompt }));
  }
  const byId = (id: string) => {
    const r = refs.find((x) => x.id === id);
    if (!r) throw new RefError(`No ref ${id} in series.json`, 404);
    return r;
  };

  function defaults(): RefDefaults {
    const src = (v: string | null): RefDefaultSource => (v ? "editor" : "default");
    return {
      target: chosen.target ?? MOCK_REF_DEFAULTS.target,
      target_source: src(chosen.target),
      keyframe_target: chosen.keyframe_target ?? MOCK_REF_DEFAULTS.keyframe_target,
      keyframe_target_source: src(chosen.keyframe_target),
    };
  }

  function imageTarget(r: MRef): string {
    const d = defaults();
    return r.ov.target ?? (r.kind === "keyframe" ? d.keyframe_target! : d.target!);
  }

  /** the override a generate of (r, view) uses: the ref's fields, then the view's */
  function merged(r: MRef, view: string | null): Override {
    return { ...r.ov, ...(view ? r.vov[view] ?? {} : {}) };
  }

  function effective(r: MRef, view: string | null = null, target = imageTarget(r)): RefEffective {
    const m = IMAGE_MODELS[target] ?? IMAGE_MODELS.krea2;
    const ov = merged(r, view);
    const prompt = view ? `${r.base_prompt} View: ${view.replace(/^\d+_/, "")}.` : r.base_prompt;
    const generated = (view ? r.views?.find((v) => v.view === view)?.takes : r.takes)?.some((t) => t.source === "generated");
    return {
      prompt: typeof ov.prompt === "string" ? ov.prompt : prompt,
      // `auto` keeps the stable seed until the ref has a generated take (then a new one each time)
      seed: ov.seed ?? (generated ? null : stableSeed(r.id + (view ?? ""))),
      seed_source: ov.seed != null ? "override" : generated ? "new" : "stable",
      model: ov.model ?? m.model,
      loras: ov.loras !== undefined ? ov.loras ?? null : null,
      steps: ov.steps ?? m.steps,
      width: 1024,
      height: r.kind === "location" ? 576 : 1024,
      target,
    };
  }

  /** A shot's keyframe ref, made on first use (no takes). */
  function ensureKeyframe(shot: string, which: "first" | "last"): MRef {
    const id = `shot:${shot}:${which}`;
    let r = refs.find((x) => x.id === id);
    if (!r) {
      const base_prompt = `The ${which === "first" ? "opening" : "closing"} frame of ${shot}: ${look}. How the shot ${which === "first" ? "opens" : "ends"}, the location and the characters as designed.`;
      r = base(id, "keyframe", {
        scope: "shot", name: `${shot} ${which} frame`, path: `refs/shots/${shot}/${which}.png`,
        used_by: { final: [], proxy: [] }, prompt: base_prompt, base_prompt, shot, which,
      });
      refs.push(r);
    }
    return r;
  }

  /** Put the build's keyframe needs on the keyframe refs (listing needed ones before any take exists). */
  function syncKeyframes() {
    const needs = opts.needs?.() ?? [];
    const byKf = new Map(needs.map((n) => [`shot:${n.shot}:${n.which}`, n]));
    for (const n of needs) ensureKeyframe(n.shot, n.which);
    for (const r of refs) {
      if (r.kind !== "keyframe") continue;
      const n = byKf.get(r.id);
      r.need = n?.need ?? null;
      r.method = n?.method ?? null;
      r.target = n?.target ?? null;
      r.requested = n?.requested ?? false;
      r.script = n?.script ?? (n?.requested ? n.method : null);
      r.reads = n ? true : null;
      // real keyframe refs list no shots in used_by
      r.used_by = { final: [], proxy: [] };
    }
  }

  function addTake(r: MRef, view: string | null, o: { status: RefTake["status"]; seed: string; source?: RefTake["source"]; note?: string; ext?: string; sourceName?: string; target?: string | null }): RefTake {
    const list = view ? r.views!.find((v) => v.view === view)!.takes : r.takes;
    const take = Math.max(0, ...list.map((t) => t.take), ...(trash.get(`${r.id}|${view ?? ""}`) ?? [])) + 1;
    const eff = effective(r, view, o.target ?? imageTarget(r));
    const gen = o.source !== "imported" && o.source !== "frame";
    const t: RefTake = {
      take, view, status: o.status, usable: false, seed: gen ? o.seed : null, seed_source: gen ? "stable" : null,
      image: null, audio: null, source: o.source ?? "generated", note: o.note ?? "", prompt: gen ? eff.prompt : null,
      model: gen ? eff.model : null, steps: gen ? eff.steps : null, loras: eff.loras ?? null, width: null, height: null,
      overrides: gen ? Object.keys(merged(r, view)).filter((k) => k !== "target") : [],
      queued: new Date().toISOString(), finished: null, comfy_prompt_id: null, save_notes: "",
      ...(gen ? { target: eff.target } : {}),
    };
    list.push(t);
    if (o.status === "ok") finishTake(r, view, t, o.ext, o.sourceName);
    return t;
  }

  function finishTake(r: MRef, view: string | null, t: RefTake, ext?: string, sourceName?: string) {
    t.status = "ok";
    t.usable = true;
    t.finished = new Date().toISOString();
    const file = takePath(r, view, t.take, ext ?? (r.kind === "voice" ? "wav" : "png"));
    // a voice's candidate is audio: `image` stays null
    if (r.kind === "voice") t.audio = file;
    else t.image = file;
    if (r.kind === "keyframe") {
      const sub = sourceName ? `imported: ${sourceName}` : `still · t${String(t.take).padStart(2, "0")} · ${t.target ?? ""}`;
      images.set(file, svgImage(r.name, sub, `${t.seed ?? sourceName}`, "location", 448, 256));
      t.width = 448;
      t.height = 256;
    } else if (r.kind !== "voice") {
      const sub = sourceName ? `imported: ${sourceName}` : `${view ? view.replace(/^\d+_/, "") + " · " : ""}t${String(t.take).padStart(2, "0")} · ${t.seed}`;
      images.set(file, svgImage(r.name, sub, `${t.seed ?? sourceName}${view ?? ""}`, r.kind));
      t.width = 1024;
      t.height = r.kind === "location" ? 576 : 1024;
    }
  }

  function setLive(r: MRef) {
    if (!r.path) return;
    const path = r.path;
    r.sha1 = hash(`${r.id}${Date.now()}${Math.random()}`).toString(16).padStart(8, "0");
    r.cleared = false;
    if (r.views?.length) {
      const urls = r.views.map((v) => images.get(v.takes.find((t) => t.take === v.picked)?.image ?? "") ?? "");
      images.set(path, svgSheet(r.name, urls));
    } else if (r.kind !== "voice") {
      const t = r.takes.find((x) => x.take === r.picked);
      const img = t?.image ? images.get(t.image) : undefined;
      if (img) images.set(path, img);
    }
    r.exists = true;
  }

  /** take numbers moved to _trash, per ref|view (a new take never reuses one) */
  const trash = new Map<string, number[]>();

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
  {
    // Ada's face view has a prompt of its own
    const ada = byId("subject:ada");
    ada.vov["04_face"] = { prompt: `${ada.base_prompt} View: face. Close on the face, glasses catching the light.` };
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
    // Phase 8.5: sh070 (Wan 2.2 I2V) has a generated first frame, live
    const kf = ensureKeyframe("sh070", "first");
    addTake(kf, null, { status: "ok", seed: seedOf(kf, 0), target: MOCK_REF_DEFAULTS.keyframe_target });
    kf.picked = 1;
    setLive(kf);
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

  /** What an edit keyframe target is fed: the shot's characters (three-quarter view), then its plate, up to max_refs. */
  function editRefs(r: MRef, target: string): EditRef[] | undefined {
    const max = IMAGE_MODELS[target]?.edit;
    if (!max || !r.shot) return undefined;
    const uses = usesOf(shots.indexOf(r.shot)).map((u) => byId(u.ref)).filter((x) => x.kind !== "voice" && x.exists);
    const order = (x: MRef) => (x.kind === "character" ? 0 : x.kind === "location" ? 2 : 1);
    return [...new Set(uses)].sort((a, b) => order(a) - order(b)).slice(0, max).map((x) => {
      if (x.kind === "location") return { id: x.id, role: "plate", path: x.path };
      const v = x.views?.find((y) => y.view === "01_threequarter");
      const t = v?.takes.find((y) => y.take === v.picked);
      return { id: x.id, role: "subject", view: "01_threequarter", path: t?.image ?? x.path };
    });
  }

  const fieldsOf = (o: Override) => Object.keys(o).filter((k) => (o as Record<string, unknown>)[k] != null).sort();

  function view(r: MRef): Ref {
    const { base_prompt, ov, vov, subject, vcleared, why, ...rest } = r;
    void base_prompt;
    const chars = !!r.views?.length;
    const tgt = imageTarget(r);
    const eff = r.why ? null : chars ? { target: tgt } : effective(r, null, tgt);
    const out: Ref = {
      ...rest,
      key: key(r.id),
      subject: subject ?? null,
      // a character's own prompt is its sheet text; each view has what it generates with
      prompt: chars || !eff ? r.base_prompt : eff.prompt ?? r.base_prompt,
      can_generate: !why,
      why_not: why ?? null,
      override: { fields: fieldsOf(ov), stale: false, values: ov },
      override_values: ov,
      effective: eff,
      built_prompt: r.base_prompt,
      cleared: !!r.cleared,
      views: chars
        ? r.views!.map((v): RefView => {
          const m = merged(r, v.view);
          const ve = why ? null : effective(r, v.view, tgt);
          return {
            view: v.view, picked: v.picked, cleared: !!vcleared[v.view],
            prompt: ve?.prompt ?? `${r.base_prompt} View: ${v.view.replace(/^\d+_/, "")}.`,
            override: { fields: fieldsOf(m), stale: false, values: m },
            effective: ve,
            takes: v.takes,
          };
        })
        : [],
    } as Ref & { key: string; subject: string | null };
    if (r.kind === "keyframe") {
      const er = editRefs(r, tgt);
      if (er) out.edit_refs = er;
    }
    return JSON.parse(JSON.stringify(out));
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
      if (t.status !== "queued") return; // discarded meanwhile
      finishTake(r, v, t);
      opts.emit("executing", null);
      opts.emit("execution_success", { prompt_id: pid });
      opts.emit("h3pipe.ref", { ep: opts.ep, ref: r.id, view: v, take: t.take, status: "ok" });
    });
  }

  function pickSeed(r: MRef, view: string | null, list: RefTake[], mode: SeedMode, typed: string | null, forceNew: boolean): string {
    if (typed) return typed;
    if (mode === "same") return list[list.length - 1]?.seed ?? stableSeed(r.id + (view ?? ""));
    if (mode === "new" || forceNew) return randomSeed();
    return effective(r, view).seed ?? randomSeed();
  }

  function listOf(r: MRef, view: string | null): { list: RefTake[]; rv?: RefView } {
    if (r.views?.length) {
      if (!view) throw new RefError(`${r.name} is a character: name a view`, 400);
      const rv = r.views.find((v) => v.view === view);
      if (!rv) throw new RefError(`No view ${view}`, 404);
      return { list: rv.takes, rv };
    }
    return { list: r.takes };
  }

  function doPick(r: MRef, view: string | null, take: number) {
    const { rv } = listOf(r, view);
    if (rv) {
      rv.picked = take;
      r.vcleared[rv.view] = false;
      if (r.views!.every((v) => v.picked != null)) setLive(r);
    } else {
      r.picked = take;
      if (r.kind === "voice") {
        r.exists = true;
        r.cleared = false;
        r.sha1 = hash(`${r.id}${Date.now()}`).toString(16);
      } else setLive(r);
    }
    opts.onLiveChange(r.id);
  }

  function doClear(r: MRef, view: string | null) {
    const { rv } = listOf(r, view);
    if (rv) {
      rv.picked = null;
      r.vcleared[rv.view] = true;
    } else {
      r.picked = null;
      r.cleared = true;
    }
    r.exists = false;
    r.sha1 = null;
    if (r.path) images.delete(r.path);
    opts.onLiveChange(r.id);
  }

  function importFile(r: MRef, view: string | null, name: string) {
    listOf(r, view);
    const audio = r.kind === "voice";
    if (!r.path) throw new RefError(`${r.id}: the series config names no file for it`, 400);
    if (audio ? !/\.(wav|mp3|flac|ogg|m4a)$/i.test(name) : !/\.(png|jpe?g|webp)$/i.test(name)) {
      throw new RefError(audio ? `${name}: a voice takes wav, mp3, flac, ogg or m4a` : `${name}: an image must be png, jpg, jpeg or webp`, 400);
    }
    return addTake(r, view, { status: "ok", seed: "", source: "imported", sourceName: name, ext: audio ? (name.split(".").pop() ?? "wav") : "png" });
  }

  function refFor(id: string): MRef {
    const m = /^shot:(.+):(first|last)$/.exec(id);
    return m ? ensureKeyframe(m[1], m[2] as "first" | "last") : byId(id);
  }

  return {
    list: () => {
      syncKeyframes();
      return refs.map(view);
    },
    defaults,
    setDefaults(fields) {
      for (const k of ["target", "keyframe_target"] as const) {
        if (!(k in fields)) continue;
        const v = fields[k] ?? null;
        if (v != null && !IMAGE_MODELS[v]) throw new RefError(`${v} isn't an image target`, 400);
        chosen[k] = v;
      }
      return defaults();
    },
    info(id) {
      const r = refs.find((x) => x.id === id);
      return r ? { path: r.path, exists: r.exists, kind: r.kind, sha1: r.sha1 } : undefined;
    },
    keyframeNeeds: (shot) => (opts.needs?.() ?? []).filter((n) => n.shot === shot),
    addImage: (path, url) => void images.set(path, url),
    unpick(ref, v) {
      const r = byId(ref);
      if (r.kind === "voice") throw new RefError("A voice can't be cleared", 400);
      const prev = v ? r.views?.find((x) => x.view === v)?.picked ?? null : r.picked;
      doClear(r, v);
      opts.emit("h3pipe.ref", { ep: opts.ep, ref: r.id, view: v, take: prev, status: "cleared" });
      syncKeyframes();
      return view(r);
    },
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
      const r = refFor(req.ref);
      if (r.kind === "voice") throw new RefError("Nothing generates voices yet: import a recording.", 400);
      if (r.why) throw new RefError(r.why, 400);
      if (req.count < 1 || req.count > 16) throw new RefError("count is 1 to 16", 400);
      if (req.target != null && !IMAGE_MODELS[req.target]) throw new RefError(`${req.target} isn't an image target`, 400);
      if (req.prompt != null && r.views?.length && !req.view) throw new RefError(`${r.id} is a character: a prompt is per view`, 400);
      const views: (string | null)[] = r.views?.length ? (req.view ? [req.view] : VIEWS.map((v) => v.view)) : [null];
      const out: RefGenerateResult = { queued: [], errors: [] };
      for (let c = 0; c < req.count; c++) {
        let shared: string | null = null; // all four views share one seed per candidate
        for (const v of views) {
          const list = v ? r.views!.find((x) => x.view === v)!.takes : r.takes;
          // count > 1: each candidate after the first gets a new seed
          const seed: string = shared ?? pickSeed(r, v, list, req.seed_mode, req.seed, c > 0);
          shared = seed;
          const t = addTake(r, v, { status: "queued", seed, note: req.note, target: req.target ?? null });
          // one-off settings for this call land in the candidate's record
          if (req.prompt != null) t.prompt = req.prompt;
          if (req.model != null) t.model = req.model;
          if (req.steps != null) t.steps = req.steps;
          if (req.loras != null) t.loras = req.loras;
          const pid = opts.nextPrompt();
          t.comfy_prompt_id = pid;
          out.queued.push({ ref: r.id, view: v, take: t.take, prompt_id: pid, seed, target: t.target ?? imageTarget(r) });
          opts.emit("h3pipe.ref", { ep: opts.ep, ref: r.id, view: v, take: t.take, status: "queued" });
          simulate(r, v, t, pid);
        }
      }
      return out;
    },
    pick(req) {
      const r = byId(req.ref);
      const { list } = listOf(r, req.view ?? null);
      const t = list.find((x) => x.take === req.take);
      if (!t) throw new RefError(`No take ${req.take}`, 404);
      if (t.status !== "ok") throw new RefError(`t${String(req.take).padStart(2, "0")} is ${t.status}; only a finished candidate can be picked`, 409);
      doPick(r, req.view ?? null, req.take);
      return view(r);
    },
    import(req) {
      const r = refFor(req.ref);
      if (!fsFileExists(req.source_path)) throw new RefError(`${req.source_path} doesn't exist on the ComfyUI machine`, 404);
      const name = req.source_path.split(/[\\/]/).pop() ?? "file";
      const t = importFile(r, req.view ?? null, name);
      opts.emit("h3pipe.ref", { ep: opts.ep, ref: r.id, view: req.view ?? null, take: t.take, status: "ok" });
      if (req.pick) {
        doPick(r, req.view ?? null, t.take);
        opts.emit("h3pipe.ref", { ep: opts.ep, ref: r.id, view: req.view ?? null, take: t.take, status: "picked" });
      }
      return { ...JSON.parse(JSON.stringify(t)), view: req.view ?? null };
    },
    upload(req) {
      const r = refFor(req.ref);
      const t = importFile(r, req.view, req.name);
      opts.emit("h3pipe.ref", { ep: opts.ep, ref: r.id, view: req.view, take: t.take, status: "ok" });
      if (req.pick) {
        doPick(r, req.view, t.take);
        opts.emit("h3pipe.ref", { ep: opts.ep, ref: r.id, view: req.view, take: t.take, status: "picked" });
      }
      return { ...JSON.parse(JSON.stringify(t)), view: req.view, original_name: req.name };
    },
    discard(req) {
      const r = refFor(req.ref);
      const { list, rv } = listOf(r, req.view);
      const i = list.findIndex((x) => x.take === req.take);
      if (i < 0) throw new RefError(`${r.id}${req.view ? ` ${req.view}` : ""} has no take ${req.take}`, 404);
      if (list[i].status === "queued") throw new RefError(`t${String(req.take).padStart(2, "0")} is still queued`, 409);
      const [gone] = list.splice(i, 1);
      const tk = `${r.id}|${req.view ?? ""}`;
      trash.set(tk, [...(trash.get(tk) ?? []), gone.take]);
      // a voice is never cleared: its picked take is only moved (the live file stays)
      const live = r.kind !== "voice" && (rv ? rv.picked : r.picked) === req.take;
      if (live) doClear(r, req.view);
      opts.emit("h3pipe.ref", { ep: opts.ep, ref: r.id, view: req.view, take: req.take, status: "discarded" });
      if (live) opts.emit("h3pipe.ref", { ep: opts.ep, ref: r.id, view: req.view, take: req.take, status: "cleared" });
      return view(r);
    },
    keyframe(req) {
      const id = `shot:${req.shot}:${req.which}`;
      const r = ensureKeyframe(req.shot, req.which);
      const f = req.from;
      const t = addTake(r, null, { status: "queued", seed: "", source: "frame" });
      t.from = { ...f };
      t.status = "ok";
      t.usable = true;
      t.finished = new Date().toISOString();
      t.image = takePath(r, null, t.take);
      t.width = 448;
      t.height = 256;
      t.save_notes = `frame ${f.frame} of ${f.frames} of ${f.shot} ${f.pass} t${String(f.take).padStart(2, "0")}`;
      images.set(t.image, svgImage(`${f.shot} t${String(f.take).padStart(2, "0")}`, `frame ${f.frame} → ${req.shot} ${req.which}`, `${f.shot}${f.take}${f.frame}`, "location", 448, 256));
      opts.emit("h3pipe.ref", { ep: opts.ep, ref: id, view: null, take: t.take, status: "ok" });
      if (req.pick || (req.pick == null && !r.exists && !r.cleared)) {
        r.picked = t.take;
        setLive(r);
        opts.onLiveChange(r.id);
        opts.emit("h3pipe.ref", { ep: opts.ep, ref: id, view: null, take: t.take, status: "picked" });
      }
      return view(r);
    },
    putOverride(ref, fields, v) {
      const r = refFor(ref);
      if (r.kind === "voice") throw new RefError("A voice has no generation settings", 400);
      if (r.views?.length && !v && fields.prompt != null) throw new RefError(`${r.id} is a character: a prompt override is per view`, 400);
      if (v && fields.target != null) throw new RefError("an image target override is per ref, not per view", 400);
      if (v) listOf(r, v);
      const dst: Override = v ? (r.vov[v] ??= {}) : r.ov;
      for (const [k, val] of Object.entries(fields) as [keyof Override, unknown][]) {
        if (val === null || val === "") delete dst[k];
        else (dst as Record<string, unknown>)[k] = k === "loras" ? (val as Lora[]) : val;
      }
      return { ...merged(r, v ?? null), stale: false };
    },
    overrideList() {
      const out: ReturnType<MockRefs["overrideList"]> = [];
      for (const r of refs) {
        if (Object.keys(r.ov).length) out.push({ ref: r.id, view: null, subject: r.subject ?? null, kind: r.kind, values: { ...r.ov } });
        for (const [v, o] of Object.entries(r.vov)) {
          if (Object.keys(o).length) out.push({ ref: r.id, view: v, subject: r.subject ?? null, kind: r.kind, values: { ...o } });
        }
      }
      return out;
    },
    dropOverrideFields(ref, v, fields) {
      const r = refFor(ref);
      const dst = v ? r.vov[v] : r.ov;
      if (!dst) return;
      for (const f of fields) delete (dst as Record<string, unknown>)[f];
    },
    deleteOverride(ref, v) {
      const r = refFor(ref);
      if (v) delete r.vov[v];
      else r.ov = {};
      return { ...merged(r, v ?? null), stale: false };
    },
  };
}
