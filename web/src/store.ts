// One module-level store shared by every surface. Each surface is its own React
// root, but they all live in one bundle, so they all see this module.

import { useSyncExternalStore } from "react";
import type { ToastAction } from "./host";
import type { RefFilter } from "./lib/refs";
import type {
  AlignReady, AlignResult, BuildResult, Config, CutAudioSource, EpisodeStatus, EpisodeSummary, ModelList, Pass, Ref,
  RefDefaults, RefGenerateMissingResult, ShotDetail, SourceFile, TakeRef, TargetList,
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
  /** Phase 9b: J / L shuttle speed: 1, 2, 4 forwards, -1, -2, -4 backwards (absent: 1) */
  rate?: number;
}

/** Phase 9b: what Play all sounds like: each clip's own audio, or the recorded dialogue. */
export type CutAudio = "clips" | "recording";

/** Phase 9c-A: "track" picks the episode's dialogue recording.
 * Phase 9d: "clip-audio" picks a media file for one clip's sound. */
export type BrowsePurpose = "roots" | "import" | "track" | "clip-audio";

export interface BrowseState {
  purpose: BrowsePurpose;
  /** import: which ref (and view) the file becomes a candidate of */
  ref?: string;
  view?: string | null;
  /** import: images, or audio for a voice (and for a recording) */
  files?: "image" | "audio";
  /** clip-audio: the clip whose audio the chosen file becomes */
  shot?: string;
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
  action?: ToastAction;
}

/** The What's missing window: one target's files, or (target null) every target. */
export interface MissingPanelState {
  target: string | null;
}

/** A file being uploaded into a ref slot (drag and drop, or the file picker). */
export interface UploadState {
  /** the file's name */
  name: string;
  /** bytes sent / total (total 0: unknown) */
  sent: number;
  total: number;
  /** set when it failed (the slot shows it until dismissed or retried) */
  error?: string;
  /** finished: the new candidate's take */
  take?: number;
}

/** The key of a ref slot's upload: the ref, and a character's view. */
export function uploadKey(ref: string, view?: string | null): string {
  return `${ref}|${view ?? ""}`;
}

/** Phase 9c-A: the Recording window (attach a recording, align the script). */
export interface TrackPanelState {
  /** the episode it was opened for */
  ep: string;
}

/** Phase 9c-A: an h3align run, from the `h3pipe.align` events. */
export interface AlignRun {
  ep: string;
  busy: boolean;
  dryRun: boolean;
  stage: string;
  pct: number;
  text: string;
  error: string | null;
}

/** Phase 9c-B: the "use a line from a take" window, for one voice ref. */
export interface VoiceClipState {
  ref: string;
  shot: string | null;
  take: number | null;
  pass: Pass;
}

/**
 * Phase 9d: the "Audio from…" window. `draft` is the source being edited,
 * null meaning the clip's own sound; nothing is saved until Use this audio.
 */
export interface ClipAudioState {
  shot: string;
  pass: Pass;
  draft: CutAudioSource | null;
  /** a file being uploaded into the episode for this clip (null: none) */
  upload?: { name: string; sent: number; total: number; error?: string } | null;
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
  /** the episode's image targets for refs and keyframes, as /refs sends them, by episode */
  refDefaults: Record<string, RefDefaults | null>;
  /** Phase 8.6: uploads into ref slots, by uploadKey(ref, view) */
  uploads: Record<string, UploadState>;
  /** Phase 8.6: the last Generate missing, by episode (what it queued, picked and skipped) */
  missingResult: Record<string, RefGenerateMissingResult & { pass: Pass; at: number }>;
  /** Phase 8.5: the Refs tab scrolls to this ref (n changes on every request) */
  refFocus: { id: string; n: number } | null;
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
  /** readiness (`?ready=1`) is being fetched */
  readinessLoading: boolean;
  /** when the target list (with readiness) was last loaded, ms since epoch */
  readinessAt: number | null;
  /** the What's missing window (null: closed) */
  missingPanel: MissingPanelState | null;
  /** ComfyUI choices of the combo widgets targets bind, by "class_type|field" */
  widgetChoices: Record<string, string[]>;
  /** GET /h3pipe/models, by "target|param" */
  modelFiles: Record<string, ModelList>;
  zoom: number;
  build: { busy: boolean; result: BuildResult | null; error: string | null };
  assemble: { busy: boolean; output: string | null; report: string | null; error: string | null };
  /** in-flight actions, by a caller-chosen key, to disable buttons */
  busy: Record<string, boolean>;
  /** Phase 9a: the Script / Series config windows are open */
  sourceOpen: Record<SourceFile, boolean>;
  /** Phase 9a: a window's buffer has unsaved edits (set by the window) */
  sourceDirty: Record<SourceFile, boolean>;
  /** Phase 9a: scroll the Script window to this shot (n changes on every request) */
  scriptFocus: { shot: string; n: number } | null;
  /** Phase 9a: bumped when the authored files changed through the editor (promote,
   * a save in the other window): open windows check the disk again */
  sourceN: number;
  /** Phase 9a: the Promote dialog (shot null: the whole episode) */
  promote: { shot: string | null } | null;
  /** Phase 9b: Play all's sound (the recording needs the episode's `track`) */
  cutAudio: CutAudio;
  /** Phase 9b: the waveform lane under the timeline's clips is shown */
  waves: boolean;
  /** Phase 9b: what Ctrl+Z / Ctrl+Shift+Z would undo / redo, by statusKey */
  cutUndo: Record<string, { undo: string | null; redo: string | null }>;
  /** Phase 9b: the timeline's Cut menu, open at (x, y) */
  cutMenu: { x: number; y: number } | null;
  /** Phase 9c-A: the Recording window (null: closed) */
  trackPanel: TrackPanelState | null;
  /** Phase 9c-A: GET /h3pipe/align/ready, once loaded (null: not asked yet) */
  alignReady: AlignReady | null;
  alignReadyError: string | null;
  /** Phase 9c-A: the run going now, with its live progress (null: none) */
  alignRun: AlignRun | null;
  /** Phase 9c-A: the last run's answer, by episode */
  alignResult: Record<string, AlignResult>;
  /** Phase 9c-A: the build the last attach/clear answered with, by episode
   * (it fails until the script has `audio:` windows: "align to finish") */
  trackBuild: Record<string, BuildResult | null>;
  /** Phase 9c-B: the "use a line from a take" window (null: closed) */
  voiceClip: VoiceClipState | null;
  /** Phase 9d: the "Audio from…" window for one clip (null: closed) */
  clipAudio: ClipAudioState | null;
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
    refDefaults: {},
    uploads: {},
    missingResult: {},

    refFocus: null,
    menu: null,
    redo: null,
    sidecar: null,
    toasts: [],
    models: null,
    loras: null,
    modelsError: null,
    targets: null,
    targetsError: null,
    readinessLoading: false,
    readinessAt: null,
    missingPanel: null,
    widgetChoices: {},
    modelFiles: {},
    zoom: clampZoom(prefs.zoom ?? ZOOM_DEFAULT),
    build: { busy: false, result: null, error: null },
    assemble: { busy: false, output: null, report: null, error: null },
    busy: {},
    sourceOpen: { script: false, series: false },
    sourceDirty: { script: false, series: false },
    scriptFocus: null,
    sourceN: 0,
    promote: null,
    cutAudio: "clips",
    waves: prefs.waves ?? false,
    cutUndo: {},
    cutMenu: null,
    trackPanel: null,
    alignReady: null,
    alignReadyError: null,
    alignRun: null,
    alignResult: {},
    trackBuild: {},
    voiceClip: null,
    clipAudio: null,
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
  /** Phase 9b: the waveform lane */
  waves?: boolean;
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
      waves: typeof p.waves === "boolean" ? p.waves : undefined,
    };
  } catch {
    return {};
  }
}

export function savePrefs(s: Pick<AppState, "ep" | "pass" | "zoom"> & { waves?: boolean }, storage: KV | null = defaultStorage()) {
  try {
    storage?.setItem(PREFS_KEY, JSON.stringify({ ep: s.ep, pass: s.pass, zoom: s.zoom, ...(s.waves != null ? { waves: s.waves } : {}) }));
  } catch {
    /* private window or blocked storage: not remembering is fine */
  }
}

/** Save prefs whenever they change. Returns the unsubscribe. */
export function persistPrefs(st: Store<AppState>, storage: KV | null = defaultStorage()): () => void {
  let last = { ep: st.get().ep, pass: st.get().pass, zoom: st.get().zoom, waves: st.get().waves };
  return st.subscribe(() => {
    const s = st.get();
    if (s.ep !== last.ep || s.pass !== last.pass || s.zoom !== last.zoom || s.waves !== last.waves) {
      last = { ep: s.ep, pass: s.pass, zoom: s.zoom, waves: s.waves };
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
