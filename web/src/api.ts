// The typed client for docs/API.md: one function per route. The same `Api`
// interface is implemented by the mock (src/mock/) for the dev page.
//
// What the contract still leaves open (the UI works around each one):
//  - TODO(contract): shot_detail has no pre-override ("built") model/LoRAs/steps
//    once an override sets them; `built` is the raw shotlist entry, which omits
//    the series config's defaults. The inspector shows the effective values.
//  - TODO(contract): a placeholder cut entry's take lives in the other pass;
//    episode_status gives its number but not its thumb/strip/mp4, so the UI
//    loads the other pass's status to draw it.
//  - TODO(contract): `GET /h3pipe/shot` takes no `target`, so the redo/render
//    dialogs can't show the size and length a one-off run on *another* target
//    would use (they show the target's preset size instead).
//  - TODO(contract): there is no prompt override per target: a retargeted
//    shot's per-pass prompt override is ignored, so the inspector shows its
//    `effective.prompt` read-only.
//  - TODO(contract): a character view's `override.values` in `GET /h3pipe/refs`
//    are the character's own fields merged with the view's, and a view has no
//    `built_prompt`. The per-view editor can't tell a view's own field from an
//    inherited one, nor diff an overridden view prompt against the series
//    config's text.

import type {
  AssembleResult, BrowseFiles, BrowseResult, BuildResult, CancelResult, ComfyQueue, Config, CutEntry,
  CutFile, CutWhat, DiscardResult, EpisodeStatus, EpisodeSummary, EpisodeTargetResult, ModelList, OverrideRequest, OverrideResult, Pass,
  PeaksResult, PickRequest, Ref, RefDefaults, RefDiscardRequest, RefGenerateMissingRequest, RefGenerateMissingResult, RefGenerateRequest,
  RefGenerateResult, RefImportRequest, RefKeyframeRequest, RefList, RefOverrideInfo, RefOverrideRequest, RefPickRequest,
  RefTake, RefUploadRequest, RenderRequest, RenderResult, Seed, ShotDetail, TakeRef, TargetKind, TargetList,
  PromoteHashes, PromotePlan, PromoteResult, SourceCheck, SourceDoc, SourceFile, SourceHash, SourceSaveRequest, SourceSaveResult,
} from "./types";
import { comboChoices } from "./lib/targets";

/** A ref take as the import routes return it. */
export type ImportedTake = RefTake & { view?: string | null };

/** Upload progress: bytes sent so far, and the total (0 when unknown). */
export type UploadProgress = (sent: number, total: number) => void;

/** POST /h3pipe/refs/import (multipart) refuses a file over this (413). */
export const UPLOAD_LIMIT = 64 * 1024 * 1024;

export interface Api {
  getConfig(): Promise<Config>;
  putConfig(roots: string[]): Promise<Config>;
  episodes(): Promise<EpisodeSummary[]>;
  episode(ep: string, pass: Pass): Promise<EpisodeStatus>;
  shot(ep: string, pass: Pass, shot: string): Promise<ShotDetail>;
  build(ep: string): Promise<BuildResult>;
  /** URL of a file inside the episode (for <img>/<video>); supports Range. */
  fileUrl(ep: string, path: string): string;
  /** A JSON file inside the episode (e.g. a frozen shotlist), seeds as strings. */
  readJson<T = unknown>(ep: string, path: string): Promise<T>;
  render(req: RenderRequest): Promise<RenderResult>;
  cancel(take: TakeRef): Promise<CancelResult>;
  /** POST /h3pipe/discard: move a take's files to `_trash/` (409 while queued). */
  discard(take: TakeRef): Promise<DiscardResult>;
  pick(req: PickRequest): Promise<{ cut: CutFile }>;
  putCut(ep: string, pass: Pass, entries: CutEntry[]): Promise<{ cut: CutFile }>;
  /** Phase 9b: POST /h3pipe/cut/reset: script order and/or zero trims (picks, locks, notes kept). */
  cutReset(ep: string, pass: Pass, what: CutWhat): Promise<{ cut: CutFile }>;
  /** Phase 9b: POST /h3pipe/cut/copy: one pass's order and/or trims onto the other (never picks). */
  cutCopy(ep: string, from: Pass, to: Pass, what: CutWhat): Promise<{ cut: CutFile }>;
  /** Phase 9b: GET /h3pipe/peaks: `bins` peaks (0..255) of a media file over start..end seconds. */
  peaks(ep: string, path: string, bins: number, start?: number | null, end?: number | null): Promise<PeaksResult>;
  putOverride(req: OverrideRequest): Promise<OverrideResult>;
  deleteOverride(ep: string, shot: string, pass?: Pass): Promise<OverrideResult>;
  assemble(ep: string, pass: Pass, partial: boolean): Promise<AssembleResult>;
  /** Folders on the ComfyUI machine; no path = the starting points. `files`
   * lists image or audio files too (for importing a ref). */
  browse(path?: string | null, files?: BrowseFiles | null): Promise<BrowseResult>;
  refs(ep: string): Promise<RefList>;
  /** URL of a ref file (live file or a candidate, `../` beside a parent-folder
   * series config); `version` busts the image cache after a re-pick. */
  refFileUrl(ep: string, path: string, version?: string | null): string;
  refsGenerate(req: RefGenerateRequest): Promise<RefGenerateResult>;
  /** POST /h3pipe/refs/generate-missing: one candidate for every missing series
   * ref, and every needed keyframe filled (continuity, a still, or the script's file). */
  refsGenerateMissing(req: RefGenerateMissingRequest): Promise<RefGenerateMissingResult>;
  refsPick(req: RefPickRequest): Promise<Ref>;
  refsImport(req: RefImportRequest): Promise<ImportedTake>;
  /** POST /h3pipe/refs/import as multipart (drag and drop, a file picker), with
   * upload progress where the transport can report it. */
  refsUpload(req: RefUploadRequest, onProgress?: UploadProgress): Promise<ImportedTake>;
  /** POST /h3pipe/refs/discard: move a candidate to `_trash/`; its pick is cleared.
   * Returns the ref as `refs` lists it. */
  refsDiscard(req: RefDiscardRequest): Promise<Ref>;
  /** A shot's first / last keyframe from a frame of a video take (continuity);
   * returns the keyframe ref as `refs` lists it. */
  refsKeyframe(req: RefKeyframeRequest): Promise<Ref>;
  /** DELETE /h3pipe/refs/pick: unpick a ref (its live file is removed; its takes
   * stay). For a keyframe this is Clear: the shot renders without one. */
  refsUnpick(ep: string, ref: string, view?: string | null): Promise<Ref>;
  /** PUT /h3pipe/refs/defaults: the episode's image targets (null clears one; a key left out is kept). */
  putRefDefaults(ep: string, fields: { target?: string | null; keyframe_target?: string | null }): Promise<{ defaults: RefDefaults }>;
  putRefOverride(req: RefOverrideRequest): Promise<{ override: RefOverrideResult }>;
  deleteRefOverride(ep: string, ref: string, view?: string | null): Promise<{ override: RefOverrideResult }>;
  /** ComfyUI's own lists (not h3pipe routes). */
  models(): Promise<string[]>;
  loras(): Promise<string[]>;
  comfyQueue(): Promise<ComfyQueue>;
  /** Phase 7: every target (video and image), for the target picker. A kind
   * filters; `ready: true` asks for each target's `readiness` (`?ready=1`). */
  targets(opts?: TargetKind | TargetsQuery): Promise<TargetList>;
  /** PUT /h3pipe/episode-target: the episode's default target; null clears it
   * (back to series.json's). */
  putEpisodeTarget(ep: string, target: string | null): Promise<EpisodeTargetResult>;
  /** ComfyUI's choices for one combo widget (`/object_info/<class_type>`), or
   * null when the node or widget isn't there. */
  widgetChoices(classType: string, field: string): Promise<string[] | null>;
  /** GET /h3pipe/models: one model param's files, matching the target's family first. */
  modelFiles(target: string, param: string, ep?: string | null): Promise<ModelList>;
  /** Phase 9a: GET /h3pipe/source: the script or the series config, with its hash. */
  source(ep: string, file: SourceFile): Promise<SourceDoc>;
  /** GET /h3pipe/source?…&hash_only=1: has the file changed on disk? */
  sourceHash(ep: string, file: SourceFile): Promise<SourceHash>;
  /** POST /h3pipe/source/check: parse and check `text` without writing it. */
  checkSource(ep: string, file: SourceFile, text: string): Promise<SourceCheck>;
  /** PUT /h3pipe/source: 409 (ApiError.data = {error, hash, text}) when the file
   * changed since `base_hash`; 400 for a series config that isn't JSON. */
  putSource(req: SourceSaveRequest): Promise<SourceSaveResult>;
  /** GET /h3pipe/promote: what the authored files can take from the overrides. */
  promotePlan(ep: string, shot?: string | null): Promise<PromotePlan>;
  /** POST /h3pipe/promote: 409 when either file changed since the plan; 400 for
   * an id the plan (for the same `shot`) doesn't offer. */
  promote(ep: string, items: string[] | "all", hashes: PromoteHashes, shot?: string | null): Promise<PromoteResult>;
}

/** A ref override route's answer: the (effective) override values, plus `stale`. */
export type RefOverrideResult = NonNullable<RefOverrideInfo["values"]> & { stale?: boolean };

export interface TargetsQuery {
  kind?: TargetKind;
  /** ask for readiness (`?ready=1`) */
  ready?: boolean;
}

/** `targets("video")` and `targets({kind, ready})` both work. */
export function targetsQuery(opts?: TargetKind | TargetsQuery): TargetsQuery {
  if (!opts) return {};
  return typeof opts === "string" ? { kind: opts } : opts;
}

export class ApiError extends Error {
  /** `data`: the error response's JSON body, when it had one (a 409's `{hash, text}`). */
  constructor(message: string, public status: number, public route: string, public data?: unknown) {
    super(message);
    this.name = "ApiError";
  }
}

// ---------------------------------------------------------------------------
// seeds
// ---------------------------------------------------------------------------

const SEED_RE = /^\d{1,20}$/;

export function isSeed(s: unknown): s is Seed {
  return typeof s === "string" && SEED_RE.test(s);
}

/** A typed seed from a text field: digits only, trimmed. Throws a readable error. */
export function parseSeed(text: string): Seed {
  const s = text.trim();
  if (!SEED_RE.test(s)) throw new Error(`A seed is a whole number (digits only), not "${text}".`);
  // no leading zeros, but keep "0"
  const t = s.replace(/^0+(?=\d)/, "");
  if (BigInt(t) >= 2n ** 64n) throw new Error("That seed is larger than 64 bits.");
  return t;
}

/**
 * JSON.parse that keeps every `"seed": <integer>` as a string. The routes already
 * send strings, but files read through /h3pipe/file (frozen shotlists, sidecars)
 * are raw JSON, and JSON.parse would round a 63-bit seed to the nearest double.
 * Only matches a real key (a `"seed"` inside a JSON string has escaped quotes).
 */
export function parseJsonSeedSafe<T = unknown>(text: string): T {
  const quoted = text.replace(/("seed"\s*:\s*)(-?\d+)(?=\s*[,}\]\r\n])/g, '$1"$2"');
  return JSON.parse(quoted) as T;
}

// ---------------------------------------------------------------------------
// the HTTP client
// ---------------------------------------------------------------------------

export interface Transport {
  /** fetch a server path such as "/h3pipe/episodes" */
  fetch(path: string, init?: RequestInit): Promise<Response>;
  /** the absolute URL of a server path, for <img src> / <video src> */
  url(path: string): string;
  /** POST a multipart form with upload progress (XMLHttpRequest); without it,
   * uploads go through `fetch` and report no progress. */
  upload?(path: string, form: FormData, onProgress?: UploadProgress): Promise<{ status: number; statusText: string; text: string }>;
}

/** An XMLHttpRequest upload to `url`, for a Transport's `upload`. */
export function xhrUpload(url: string, form: FormData, onProgress?: UploadProgress): Promise<{ status: number; statusText: string; text: string }> {
  return new Promise((resolve, reject) => {
    const x = new XMLHttpRequest();
    x.open("POST", url);
    if (onProgress) x.upload.onprogress = (e) => onProgress(e.loaded, e.lengthComputable ? e.total : 0);
    x.onload = () => resolve({ status: x.status, statusText: x.statusText, text: x.responseText });
    x.onerror = () => reject(new Error("the upload didn't reach ComfyUI"));
    x.onabort = () => reject(new Error("the upload was cancelled"));
    x.send(form);
  });
}

/** The server's JSON (seeds kept as strings), or an ApiError for a failed status. */
function answer<T>(method: string, route: string, status: number, statusText: string, text: string): T {
  let data: unknown = undefined;
  if (text) {
    try {
      data = parseJsonSeedSafe(text);
    } catch {
      data = undefined;
    }
  }
  if (status < 200 || status >= 300) {
    const msg = (data && typeof data === "object" && typeof (data as { error?: unknown }).error === "string")
      ? (data as { error: string }).error
      : status === 413
        ? `The file is too big to upload (the limit is ${UPLOAD_LIMIT / 1024 / 1024} MB).`
        : status === 404 && route.startsWith("/h3pipe/") && data === undefined
          ? `${route} isn't there (HTTP 404). Is the h3pipe node pack loaded and up to date?`
          : `${method} ${route} failed: HTTP ${status} ${statusText}`.trim();
    throw new ApiError(msg, status, route, data);
  }
  if (data === undefined && text) {
    throw new ApiError(`${method} ${route} answered with something that isn't JSON.`, status, route);
  }
  return data as T;
}

function qs(params: Record<string, string | undefined | null>): string {
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v != null) u.set(k, v);
  return u.toString();
}

export function createHttpApi(t: Transport): Api {
  async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
    const route = path.split("?")[0];
    let res: Response;
    try {
      res = await t.fetch(path, {
        method,
        headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
        body: body !== undefined ? JSON.stringify(body) : undefined,
      });
    } catch (e) {
      throw new ApiError(`Can't reach ComfyUI (${method} ${route}): ${errText(e)}`, 0, route);
    }
    return answer<T>(method, route, res.status, res.statusText, await res.text());
  }

  const get = <T>(path: string) => call<T>("GET", path);

  /** A multipart POST (the browser sets the boundary): with progress through
   * the transport's `upload` when it has one, else plain `fetch`. */
  async function postForm<T>(path: string, form: FormData, onProgress?: UploadProgress): Promise<T> {
    const route = path.split("?")[0];
    try {
      if (t.upload) {
        const r = await t.upload(path, form, onProgress);
        return answer<T>("POST", route, r.status, r.statusText, r.text);
      }
      const res = await t.fetch(path, { method: "POST", body: form });
      return answer<T>("POST", route, res.status, res.statusText, await res.text());
    } catch (e) {
      if (e instanceof ApiError) throw e;
      throw new ApiError(`Can't reach ComfyUI (POST ${route}): ${errText(e)}`, 0, route);
    }
  }

  return {
    getConfig: () => get("/h3pipe/config"),
    putConfig: (roots) => call("PUT", "/h3pipe/config", { roots }),
    episodes: () => get("/h3pipe/episodes"),
    episode: (ep, pass) => get(`/h3pipe/episode?${qs({ ep, pass })}`),
    shot: (ep, pass, shot) => get(`/h3pipe/shot?${qs({ ep, pass, shot })}`),
    build: (ep) => call("POST", "/h3pipe/build", { ep }),
    fileUrl: (ep, path) => t.url(`/h3pipe/file?${qs({ ep, path })}`),
    readJson: (ep, path) => get(`/h3pipe/file?${qs({ ep, path })}`),
    render: (req) => {
      if (req.seed !== null && !isSeed(req.seed)) {
        return Promise.reject(new Error(`Seed must be a string of digits, got ${String(req.seed)}`));
      }
      return call("POST", "/h3pipe/render", req);
    },
    cancel: (take) => call("POST", "/h3pipe/cancel", take),
    discard: ({ ep, pass, shot, take }) => call("POST", "/h3pipe/discard", { ep, shot, take, pass }),
    pick: (req) => call("PUT", "/h3pipe/pick", req),
    putCut: (ep, pass, entries) => call("PUT", "/h3pipe/cut", { ep, pass, entries }),
    cutReset: (ep, pass, what) => call("POST", "/h3pipe/cut/reset", { ep, pass, what }),
    cutCopy: (ep, from, to, what) => call("POST", "/h3pipe/cut/copy", { ep, from, to, what }),
    peaks: async (ep, path, bins, start, end) => {
      const n = Math.max(1, Math.round(bins));
      const r = await get<Partial<PeaksResult>>(`/h3pipe/peaks?${qs({
        ep, path, bins: String(n),
        start: start != null && Number.isFinite(start) ? String(round6(start)) : undefined,
        end: end != null && Number.isFinite(end) ? String(round6(end)) : undefined,
      })}`);
      const peaks = Array.isArray(r?.peaks) ? r.peaks.map((x) => (typeof x === "number" && Number.isFinite(x) ? x : 0)) : [];
      return { duration: typeof r?.duration === "number" ? r.duration : 0, bins: peaks.length, peaks, silent: !!r?.silent };
    },
    putOverride: (req) => {
      const s = req.fields.seed;
      if (s !== undefined && s !== null && !isSeed(s)) {
        return Promise.reject(new Error(`Seed must be a string of digits, got ${String(s)}`));
      }
      return call("PUT", "/h3pipe/override", req);
    },
    deleteOverride: (ep, shot, pass) => call("DELETE", `/h3pipe/override?${qs({ ep, shot, pass })}`),
    assemble: (ep, pass, partial) => call("POST", "/h3pipe/assemble", { ep, pass, partial }),
    browse: (path, files) => get(`/h3pipe/browse?${qs({ path: path || undefined, files: files || undefined })}`),
    refs: (ep) => get(`/h3pipe/refs?${qs({ ep })}`),
    refFileUrl: (ep, path, version) => t.url(`/h3pipe/file?${qs({ ep, path, v: version || undefined })}`),
    refsGenerate: (req) => {
      if (req.seed !== null && !isSeed(req.seed)) {
        return Promise.reject(new Error(`Seed must be a string of digits, got ${String(req.seed)}`));
      }
      // `target` only when set (an older server may reject an unknown field's null)
      const { target, ...rest } = req;
      return call("POST", "/h3pipe/refs/generate", target ? { ...rest, target } : rest);
    },
    refsGenerateMissing: (req) => {
      // only what's set: the server's defaults apply to the rest
      const body: Record<string, unknown> = { ep: req.ep };
      for (const k of ["pass", "kinds", "target", "keyframe_target"] as const) if (req[k] != null) body[k] = req[k];
      if (req.dry_run) body.dry_run = true;
      return call("POST", "/h3pipe/refs/generate-missing", body);
    },
    refsPick: (req) => call("PUT", "/h3pipe/refs/pick", req),
    refsImport: ({ pick, ...req }) => call("POST", "/h3pipe/refs/import", pick ? { ...req, pick: true } : req),
    refsUpload: (req, onProgress) => {
      const form = new FormData();
      form.append("ep", req.ep);
      form.append("ref", req.ref);
      if (req.view) form.append("view", req.view);
      if (req.pick) form.append("pick", "1");
      const name = req.name ?? (typeof File !== "undefined" && req.file instanceof File ? req.file.name : "upload");
      form.append("file", req.file, name);
      return postForm("/h3pipe/refs/import", form, onProgress);
    },
    refsDiscard: ({ ep, ref, view, take }) => call("POST", "/h3pipe/refs/discard", view ? { ep, ref, view, take } : { ep, ref, take }),
    refsKeyframe: (req) => call("POST", "/h3pipe/refs/keyframe", req),
    refsUnpick: (ep, ref, view) => call("DELETE", `/h3pipe/refs/pick?${qs({ ep, ref, view: view || undefined })}`),
    putRefDefaults: (ep, fields) => call("PUT", "/h3pipe/refs/defaults", { ep, ...fields }),

    putRefOverride: (req) => {
      const s = req.fields.seed;
      if (s !== undefined && s !== null && !isSeed(s)) {
        return Promise.reject(new Error(`Seed must be a string of digits, got ${String(s)}`));
      }
      return call("PUT", "/h3pipe/refs/override", req);
    },
    deleteRefOverride: (ep, ref, view) => call("DELETE", `/h3pipe/refs/override?${qs({ ep, ref, view: view || undefined })}`),
    models: async () => {
      try {
        const m = await get<string[]>("/models/diffusion_models");
        if (Array.isArray(m) && m.length) return m;
      } catch {
        /* older installs: fall through to unet */
      }
      return get<string[]>("/models/unet");
    },
    loras: () => get("/models/loras"),
    targets: async (opts) => {
      const { kind, ready } = targetsQuery(opts);
      const r = await get<Partial<TargetList>>(`/h3pipe/targets?${qs({ kind, ready: ready ? "1" : undefined })}`);
      return { targets: Array.isArray(r?.targets) ? r.targets : [], default: r?.default ?? {} };
    },
    putEpisodeTarget: async (ep, target) => {
      if (target !== null && (typeof target !== "string" || !target)) {
        return Promise.reject(new Error(`An episode target is a target id or null, got ${String(target)}`));
      }
      const r = await call<EpisodeTargetResult | undefined>("PUT", "/h3pipe/episode-target", { ep, target });
      return r ?? {};
    },
    widgetChoices: async (classType, field) => {
      const info = await get<unknown>(`/object_info/${encodeURIComponent(classType)}`);
      return comboChoices(info, classType, field);
    },
    modelFiles: async (target, param, ep) => {
      const r = await get<Partial<ModelList>>(`/h3pipe/models?${qs({ target, param, ep: ep || undefined })}`);
      return {
        target: r?.target ?? target, param: r?.param ?? param, family: r?.family ?? "", label: r?.label ?? r?.family ?? "",
        patterns: Array.isArray(r?.patterns) ? r.patterns : [], fingerprint: !!r?.fingerprint,
        files: Array.isArray(r?.files) ? r.files : [],
      };
    },
    source: (ep, file) => get(`/h3pipe/source?${qs({ ep, file })}`),
    sourceHash: (ep, file) => get(`/h3pipe/source?${qs({ ep, file, hash_only: "1" })}`),
    checkSource: (ep, file, text) => call("POST", "/h3pipe/source/check", { ep, file, text }),
    putSource: (req) => call("PUT", "/h3pipe/source", req),
    promotePlan: (ep, shot) => get(`/h3pipe/promote?${qs({ ep, shot: shot || undefined })}`),
    promote: (ep, items, hashes, shot) => call("POST", "/h3pipe/promote", shot ? { ep, items, hashes, shot } : { ep, items, hashes }),
    comfyQueue: async () => {
      const q = await get<{ queue_running?: unknown[][]; queue_pending?: unknown[][] }>("/queue");
      const ids = (xs?: unknown[][]) => (xs ?? []).map((x) => String(x[1]));
      return { running: ids(q.queue_running), pending: ids(q.queue_pending) };
    },
  };
}

function round6(n: number): number {
  return Math.round(n * 1e6) / 1e6;
}

export function errText(e: unknown): string {
  if (e instanceof Error) return e.message;
  if (typeof e === "string") return e;
  try {
    return JSON.stringify(e);
  } catch {
    return String(e);
  }
}
