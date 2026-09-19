// One module-level store shared by every surface. Each surface is its own React
// root, but they all live in one bundle, so they all see this module.

import { useSyncExternalStore } from "react";
import type { RefFilter } from "./lib/refs";
import type {
  BuildResult, Config, EpisodeStatus, EpisodeSummary, Pass, Ref, ShotDetail, TakeRef, TargetList,
} from "./types";

// ---------------------------------------------------------------------------
// a tiny store
// ---------------------------------------------------------------------------

export interface Store<S> {
  get(): S;
  set(patch: Partial<S> | ((s: S) => Partial<S>)): void;
  subscribe(fn: () => void): () => void;
}

export function createStore<S extends object>(initial: S): Store<S> {
  let state = initial;
  const subs = new Set<() => void>();
  return {
    get: () => state,
    set(patch) {
      const p = typeof patch === "function" ? patch(state) : patch;
      let changed = false;
      for (const k in p) {
        if (!Object.is((p as S)[k as keyof S], state[k as keyof S])) {
          changed = true;
          break;
        }
      }
      if (!changed) return;
      state = { ...state, ...p };
      subs.forEach((f) => f());
    },
    subscribe(fn) {
      subs.add(fn);
      return () => {
        subs.delete(fn);
      };
    },
  };
}

/** Subscribe a component to a slice. The selector must return a stable value
 * (a piece of state, or a primitive), not a fresh object. */
export function useSelector<S, T>(store: Store<S>, sel: (s: S) => T): T {
  return useSyncExternalStore(store.subscribe, () => sel(store.get()), () => sel(store.get()));
}

// ---------------------------------------------------------------------------
// the editor's state
// ---------------------------------------------------------------------------

export type CompareMode = "single" | "side" | "wipe";

/**
 * The floating viewer shows one of:
 *  - `takes`: one shot's takes, A/B compare (the take viewer);
 *  - `cut`: Play all, the cut in order from its takes;
 *  - `image`: a ref's candidates (stills), A/B side by side or wipe.
 */
export type ViewerKind = "takes" | "cut" | "image";

export interface ViewerState {
  kind: ViewerKind;
  /** takes: the shot; cut: the shot playing now; image: unused ("") */
  shot: string;
  pass: Pass;
  a: number | null;
  b: number | null;
  mode: CompareMode;
  /** which slot a plain click in the strip loads */
  target: "a" | "b";
  /** image mode: the ref and view whose candidates are compared (a/b are take numbers) */
  ref?: string;
  view?: string | null;
}

export interface CutPlayState {
  /** wanted: the player follows it */
  playing: boolean;
  /** the playhead on the cut's clock, seconds (written by the player) */
  pos: number;
  /** a seek request; `n` changes on every request so the same time can be sought twice */
  seek: { t: number; n: number } | null;
}

export type BrowsePurpose = "roots" | "import";

export interface BrowseState {
  purpose: BrowsePurpose;
  /** import: which ref (and view) the file becomes a candidate of */
  ref?: string;
  view?: string | null;
  /** import: images, or audio for a voice */
  files?: "image" | "audio";
}

export interface RenderAsk {
  shots: string[];
  pass: Pass;
  redo: boolean;
  title: string;
}

export interface RefTakeRef {
  ep: string;
  ref: string;
  view: string | null;
  take: number;
}

export interface MenuState {
  x: number;
  y: number;
  shot: string;
  pass: Pass;
  take: number | null;
  /** the frame the viewer showed when it opened the menu (continuity keyframes) */
  frame?: number | "last" | null;
}

export interface RedoState {
  shot: string;
  pass: Pass;
  parent: number | null;
}

export interface Toast {
  id: number;
  severity: "success" | "info" | "warn" | "error";
  summary: string;
  detail?: string;
}

export interface AppState {
  config: Config | null;
  configError: string | null;
  episodes: EpisodeSummary[] | null;
  episodesError: string | null;
  ep: string | null;
  pass: Pass;
  /** keyed by statusKey(ep, pass) */
  status: Record<string, EpisodeStatus>;
  statusError: Record<string, string>;
  statusLoading: Record<string, boolean>;
  /** keyed by detailKey(ep, pass, shot) */
  details: Record<string, ShotDetail>;
  detailError: Record<string, string>;
  shot: string | null;
  take: number | null;
  expanded: Record<string, boolean>;
  collapsedSeq: Record<string, boolean>;
  /** ComfyUI prompt id -> the take it renders */
  prompts: Record<string, TakeRef>;
  /** the prompt ComfyUI is executing now */
  running: string | null;
  pending: string[];
  progress: Record<string, { value: number; max: number }>;
  viewer: ViewerState | null;
  /** the floating inspector is open (it follows `shot`) */
  inspector: boolean;
  cutPlay: CutPlayState;
  browse: BrowseState | null;
  /** the render confirmation (shots that will be skipped for missing refs) */
  renderAsk: RenderAsk | null;
  /** refs by episode (GET /h3pipe/refs) */
  refs: Record<string, Ref[]>;
  refsError: Record<string, string>;
  refsLoading: Record<string, boolean>;
  refsFilter: RefFilter;
  /** expanded rows in the Refs tab, by ref id */
  refOpen: Record<string, boolean>;
  /** the selected candidate in the Refs tab */
  refSel: { ref: string; view: string | null; take: number } | null;
  /** ComfyUI prompt id -> the ref take it generates */
  refPrompts: Record<string, RefTakeRef>;
  menu: MenuState | null;
  redo: RedoState | null;
  sidecar: { shot: string; pass: Pass; take: number } | null;
  toasts: Toast[];
  models: string[] | null;
  loras: string[] | null;
  modelsError: string | null;
  /** Phase 7: GET /h3pipe/targets (null until loaded, or on a server without it) */
  targets: TargetList | null;
  targetsError: string | null;
  /** ComfyUI choices of the combo widgets targets bind, by "class_type|field" */
  widgetChoices: Record<string, string[]>;
  zoom: number;
  build: { busy: boolean; result: BuildResult | null; error: string | null };
  assemble: { busy: boolean; output: string | null; report: string | null; error: string | null };
  /** in-flight actions, by a caller-chosen key, to disable buttons */
  busy: Record<string, boolean>;
}

export const ZOOM_MIN = 8;
export const ZOOM_MAX = 200;
export const ZOOM_DEFAULT = 40;

export function initialState(prefs: Prefs = {}): AppState {
  return {
    config: null,
    configError: null,
    episodes: null,
    episodesError: null,
    ep: prefs.ep ?? null,
    pass: prefs.pass ?? "proxy",
    status: {},
    statusError: {},
    statusLoading: {},
    details: {},
    detailError: {},
    shot: null,
    take: null,
    expanded: {},
    collapsedSeq: {},
    prompts: {},
    running: null,
    pending: [],
    progress: {},
    viewer: null,
    inspector: false,
    cutPlay: { playing: false, pos: 0, seek: null },
    browse: null,
    renderAsk: null,
    refs: {},
    refsError: {},
    refsLoading: {},
    refsFilter: "episode",
    refOpen: {},
    refSel: null,
    refPrompts: {},
    menu: null,
    redo: null,
    sidecar: null,
    toasts: [],
    models: null,
    loras: null,
    modelsError: null,
    targets: null,
    targetsError: null,
    widgetChoices: {},
    zoom: clampZoom(prefs.zoom ?? ZOOM_DEFAULT),
    build: { busy: false, result: null, error: null },
    assemble: { busy: false, output: null, report: null, error: null },
    busy: {},
  };
}

export function statusKey(ep: string, pass: Pass): string {
  return `${ep}|${pass}`;
}

export function detailKey(ep: string, pass: Pass, shot: string): string {
  return `${ep}|${pass}|${shot}`;
}

export function clampZoom(z: number): number {
  return Number.isFinite(z) ? Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, z)) : ZOOM_DEFAULT;
}

// ---------------------------------------------------------------------------
// remembered between sessions: last episode, pass and zoom (localStorage)
// ---------------------------------------------------------------------------

export const PREFS_KEY = "h3pipe.editor.v1";

export interface Prefs {
  ep?: string | null;
  pass?: Pass;
  zoom?: number;
}

type KV = Pick<Storage, "getItem" | "setItem">;

function defaultStorage(): KV | null {
  try {
    return typeof localStorage !== "undefined" ? localStorage : null;
  } catch {
    return null;
  }
}

export function loadPrefs(storage: KV | null = defaultStorage()): Prefs {
  try {
    const raw = storage?.getItem(PREFS_KEY);
    if (!raw) return {};
    const p = JSON.parse(raw) as Prefs;
    return {
      ep: typeof p.ep === "string" ? p.ep : null,
      pass: p.pass === "final" || p.pass === "proxy" ? p.pass : undefined,
      zoom: typeof p.zoom === "number" ? p.zoom : undefined,
    };
  } catch {
    return {};
  }
}

export function savePrefs(s: Pick<AppState, "ep" | "pass" | "zoom">, storage: KV | null = defaultStorage()) {
  try {
    storage?.setItem(PREFS_KEY, JSON.stringify({ ep: s.ep, pass: s.pass, zoom: s.zoom }));
  } catch {
    /* private window or blocked storage: not remembering is fine */
  }
}

/** Save prefs whenever they change. Returns the unsubscribe. */
export function persistPrefs(st: Store<AppState>, storage: KV | null = defaultStorage()): () => void {
  let last = { ep: st.get().ep, pass: st.get().pass, zoom: st.get().zoom };
  return st.subscribe(() => {
    const s = st.get();
    if (s.ep !== last.ep || s.pass !== last.pass || s.zoom !== last.zoom) {
      last = { ep: s.ep, pass: s.pass, zoom: s.zoom };
      savePrefs(last, storage);
    }
  });
}

// ---------------------------------------------------------------------------
// derived
// ---------------------------------------------------------------------------

/** Take numbers of `shot` whose ComfyUI job is executing now. */
export function renderingTakes(
  s: Pick<AppState, "running" | "prompts">, ep: string, pass: Pass, shot: string,
): Set<number> {
  const out = new Set<number>();
  if (s.running) {
    const r = s.prompts[s.running];
    if (r && r.ep === ep && r.pass === pass && r.shot === shot) out.add(r.take);
  }
  return out;
}

/** The prompt id rendering a take, if known. */
export function promptOf(s: Pick<AppState, "prompts">, ref: TakeRef): string | undefined {
  for (const [pid, r] of Object.entries(s.prompts)) {
    if (r.ep === ref.ep && r.pass === ref.pass && r.shot === ref.shot && r.take === ref.take) return pid;
  }
  return undefined;
}

// ---------------------------------------------------------------------------
// the singleton
// ---------------------------------------------------------------------------

export const store: Store<AppState> = createStore(initialState(loadPrefs()));

export function useApp<T>(sel: (s: AppState) => T): T {
  return useSelector(store, sel);
}

export function currentStatus(s: AppState = store.get()): EpisodeStatus | undefined {
  return s.ep ? s.status[statusKey(s.ep, s.pass)] : undefined;
}

export function currentRefs(s: AppState = store.get()): Ref[] | undefined {
  return s.ep ? s.refs[s.ep] : undefined;
}
