// The typed client for docs/API.md: one function per route. The same `Api`
// interface is implemented by the mock (src/mock/) for the dev page.
//
// CONTRACT TODOs (gaps found while building the UI; see the report):
//  - TODO(contract): `POST /h3pipe/cancel` says it "returns the take's new status"
//    without a shape. Typed here as `{status?: ...}`; the UI only needs 2xx.
//  - TODO(contract): `PUT /h3pipe/override` returns each pass's override "plus
//    stale per pass"; whether `stale` sits inside each pass object or beside
//    it is not pinned down. The UI refetches `/h3pipe/shot` instead of reading it.
//  - TODO(contract): shot_detail has no pre-override ("built") model/LoRAs/steps
//    once an override sets them; the inspector can only show the effective
//    values plus the raw built shotlist entry (which omits series defaults).
//  - TODO(contract): a placeholder cut entry's take lives in the other pass;
//    episode_status gives its number but not its thumb/strip/mp4, so the UI
//    loads the other pass's status to draw it.
//
// Round 2 / Phase 5 (refs) gaps:
//  - TODO(contract): `GET /h3pipe/browse` lists folders only. Importing a ref
//    needs a file picker, so the client sends `files=image|audio` and reads an
//    optional `files: [{name, path, size?}]`. A server without it gets the
//    typed-path fallback in the dialog.
//  - TODO(contract): ref files live beside the series config, which (Phase 5 layout) can be
//    the episode's parent folder, but `GET /h3pipe/file` only serves paths inside
//    `ep`. `refFileUrl` uses `/h3pipe/file` with the ref's path as given, plus
//    `v=<sha1>` to beat the browser's image cache after a re-pick. A
//    `/h3pipe/refs/file?ep&path` (paths relative to the series config) would settle it.
//  - TODO(contract): `GET /h3pipe/refs` gives the effective `prompt` and the
//    override's field *names*, not the override's values or the effective
//    seed/model/LoRAs/steps, so the ref override editor can't show them. Read
//    here as optional `override_values`, `effective` and `built_prompt`.
//  - TODO(contract): per-view prompts/overrides for a character: `/refs` has one
//    `prompt` per ref; the override routes take `view`, but nothing lists a view's
//    own prompt or override. The UI edits the ref-level override only.
//  - TODO(contract): the ref override routes are "the same shape as the shot
//    override routes"; the response of PUT/DELETE isn't pinned down. The UI
//    refetches `/h3pipe/refs`.
//  - TODO(contract): `PUT /h3pipe/refs/pick` doesn't say whether it sends
//    `h3pipe.episode` (a pick changes `missing_refs` and makes takes ref-stale).
//    The UI refetches the episode itself after a pick or import.
//  - TODO(contract): `/refs/generate` progress: the UI matches ComfyUI `progress`
//    events by the `prompt_id`s it gets back; after a reload a queued ref take
//    carries no prompt id (read as optional `comfy_prompt_id`).
//  - TODO(contract): `POST /h3pipe/refs/import` "returns the new take": typed as
//    a RefTake (plus `view`).
//  - TODO(contract): a render's `skipped[].reason` for missing refs is free text;
//    the UI groups by the presence of `missing_refs` instead.
//
// Phase 8 (retargeting) gaps, found while building the target picker:
//  - TODO(contract): `GET /h3pipe/shot` takes no `target`, so the redo/render
//    dialogs can't show the size and length a one-off run on *another* target
//    would use. They show `effective.width/height/length` for the shot's own
//    target and "size set by the target" (plus the target's preset size) for
//    any other. A `target=` parameter on /h3pipe/shot would settle it.
//  - TODO(contract): render warnings (e.g. audio downgraded to generate for a
//    target with no voice reference) have no documented place or shape. The UI
//    reads a top-level `warnings`, and `warning`/`warnings` on queued, skipped
//    and errored entries, each a string or `{shot, warning|message|text}`.
//  - TODO(contract): the per-pass prompt override is ignored for a retargeted
//    shot, and there's no prompt override per target yet. The inspector shows
//    `effective.prompt` read-only for a retargeted shot until there is.
//  - TODO(contract): does `DELETE /h3pipe/override?pass=` keep `target` (shared,
//    like `seed`)? The UI assumes so: "Revert <pass>" leaves the target and
//    "Revert all" clears it; the picker has its own revert (`target: null`).
//  - TODO(contract): the episode's top-level `target` is read as the series
//    default for the "not the default" badge; the list's `default.video` is the
//    fallback. Say which one is the series default once episodes mix targets.
//  - TODO(contract): whether `GET /h3pipe/targets?kind=video` or the unfiltered
//    list is canonical for the picker; the client asks for all and filters.
//  - TODO(contract): the model/LoRA pickers read choices from ComfyUI's own
//    `/object_info/<class_type>` for a `{class_type, field}` widget (as API.md
//    suggests); a LoRA widget names its file field `name`, not `field`.

import type {
  AssembleResult, BrowseFiles, BrowseResult, BuildResult, CancelResult, ComfyQueue, Config, CutEntry,
  CutFile, EpisodeStatus, EpisodeSummary, OverrideRequest, OverrideResult, Pass, PickRequest, Ref,
  RefGenerateRequest, RefGenerateResult, RefImportRequest, RefList, RefOverrideRequest, RefPickRequest,
  RefTake, RenderRequest, RenderResult, Seed, ShotDetail, TakeRef, TargetKind, TargetList,
} from "./types";
import { comboChoices } from "./lib/targets";

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
  pick(req: PickRequest): Promise<{ cut: CutFile }>;
  putCut(ep: string, pass: Pass, entries: CutEntry[]): Promise<{ cut: CutFile }>;
  putOverride(req: OverrideRequest): Promise<OverrideResult>;
  deleteOverride(ep: string, shot: string, pass?: Pass): Promise<unknown>;
  assemble(ep: string, pass: Pass, partial: boolean): Promise<AssembleResult>;
  /** Folders on the ComfyUI machine; no path = the starting points. `files` asks
   * for files too (not in the contract yet, see the TODO above). */
  browse(path?: string | null, files?: BrowseFiles | null): Promise<BrowseResult>;
  refs(ep: string): Promise<RefList>;
  /** URL of a ref file (live file or a candidate); `version` busts the image cache. */
  refFileUrl(ep: string, path: string, version?: string | null): string;
  refsGenerate(req: RefGenerateRequest): Promise<RefGenerateResult>;
  refsPick(req: RefPickRequest): Promise<Ref>;
  refsImport(req: RefImportRequest): Promise<RefTake & { view?: string | null }>;
  putRefOverride(req: RefOverrideRequest): Promise<unknown>;
  deleteRefOverride(ep: string, ref: string, view?: string | null): Promise<unknown>;
  /** ComfyUI's own lists (not h3pipe routes). */
  models(): Promise<string[]>;
  loras(): Promise<string[]>;
  comfyQueue(): Promise<ComfyQueue>;
  /** Phase 7: every target (video and image), for the target picker. */
  targets(kind?: TargetKind): Promise<TargetList>;
  /** ComfyUI's choices for one combo widget (`/object_info/<class_type>`), or
   * null when the node or widget isn't there. */
  widgetChoices(classType: string, field: string): Promise<string[] | null>;
}

export class ApiError extends Error {
  constructor(message: string, public status: number, public route: string) {
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
    const text = await res.text();
    let data: unknown = undefined;
    if (text) {
      try {
        data = parseJsonSeedSafe(text);
      } catch {
        data = undefined;
      }
    }
    if (!res.ok) {
      const msg = (data && typeof data === "object" && typeof (data as { error?: unknown }).error === "string")
        ? (data as { error: string }).error
        : res.status === 404 && route.startsWith("/h3pipe/") && data === undefined
          ? `${route} isn't there (HTTP 404). Is the h3pipe node pack loaded and up to date?`
          : `${method} ${route} failed: HTTP ${res.status} ${res.statusText}`.trim();
      throw new ApiError(msg, res.status, route);
    }
    if (data === undefined && text) {
      throw new ApiError(`${method} ${route} answered with something that isn't JSON.`, res.status, route);
    }
    return data as T;
  }

  const get = <T>(path: string) => call<T>("GET", path);

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
    pick: (req) => call("PUT", "/h3pipe/pick", req),
    putCut: (ep, pass, entries) => call("PUT", "/h3pipe/cut", { ep, pass, entries }),
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
      return call("POST", "/h3pipe/refs/generate", req);
    },
    refsPick: (req) => call("PUT", "/h3pipe/refs/pick", req),
    refsImport: (req) => call("POST", "/h3pipe/refs/import", req),
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
    targets: async (kind) => {
      const r = await get<Partial<TargetList>>(`/h3pipe/targets?${qs({ kind })}`);
      return { targets: Array.isArray(r?.targets) ? r.targets : [], default: r?.default ?? {} };
    },
    widgetChoices: async (classType, field) => {
      const info = await get<unknown>(`/object_info/${encodeURIComponent(classType)}`);
      return comboChoices(info, classType, field);
    },
    comfyQueue: async () => {
      const q = await get<{ queue_running?: unknown[][]; queue_pending?: unknown[][] }>("/queue");
      const ids = (xs?: unknown[][]) => (xs ?? []).map((x) => String(x[1]));
      return { running: ids(q.queue_running), pending: ids(q.queue_pending) };
    },
  };
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
