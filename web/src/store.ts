// One module-level store shared by every surface. Each surface is its own React
// root, but they all live in one bundle, so they all see this module.

import { useSyncExternalStore } from "react";
import type {
  BuildResult, Config, EpisodeStatus, EpisodeSummary, Pass, ShotDetail, TakeRef,
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

export interface ViewerState {
  shot: string;
  pass: Pass;
  a: number | null;
  b: number | null;
  mode: CompareMode;
  /** which slot a plain click in the strip loads */
  target: "a" | "b";
}

export interface MenuState {
  x: number;
  y: number;
  shot: string;
  pass: Pass;
  take: number | null;
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
  menu: MenuState | null;
  redo: RedoState | null;
  sidecar: { shot: string; pass: Pass; take: number } | null;
  toasts: Toast[];
  models: string[] | null;
  loras: string[] | null;
  modelsError: string | null;
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
    menu: null,
    redo: null,
    sidecar: null,
    toasts: [],
    models: null,
    loras: null,
    modelsError: null,
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
