// Shapes from docs/API.md. Seeds are ALWAYS strings: built seeds are 63-bit and a
// JS number loses precision above 2^53. Never pass a Seed through Number().

export type Pass = "final" | "proxy";
export const PASSES: Pass[] = ["final", "proxy"];

/** A seed as decimal digits. */
export type Seed = string;

export interface Config {
  roots: string[];
  comfy: string;
  version: number;
}

export interface EpisodeSummary {
  ep: string;
  name: string;
  series: string;
  title: string;
  built: Record<Pass, boolean>;
  shots: number;
  script: string;
}

export type TakeStatus = "queued" | "ok" | "failed";
/** script | ref | preset, or unknown for a take from before sidecars */
export type StaleReason = string;

export interface TakeSummary {
  take: number;
  status: TakeStatus;
  has_video: boolean;
  seed: Seed | null;
  seed_source: string | null;
  note: string;
  overrides: string[];
  stale: StaleReason[];
  thumb: string | null;
  strip: string | null;
  mp4: string | null;
  queued: string | null;
  finished: string | null;
  save_notes: string;
  /** Not in the contract yet (see TODO in api.ts); read if a server sends it. */
  comfy_prompt_id?: string;
}

export interface CutInfo {
  take: number | null;
  picked: boolean;
  pass: Pass;
  placeholder: boolean;
  usable: boolean;
  trim_in: number;
  trim_out: number;
  locked: boolean;
  note: string;
  in_cut_file: boolean;
}

export interface ShotStatus {
  shot: string;
  orphan: boolean;
  sequence: string | null;
  length: number | null;
  seconds: number | null;
  size: string | null;
  subjects: string[];
  audio_policy: string | null;
  cut: CutInfo;
  override: { fields: string[]; stale: boolean };
  takes: TakeSummary[];
  /** References its render needs that aren't on disk (API.md "Missing references").
   * Optional: a server from before round 2 doesn't send it. Empty = ready. */
  missing_refs?: MissingRef[];
}

export interface MissingRef {
  /** the loader slot, e.g. "Picture 4" or "Audio 1" */
  slot: string;
  kind: "image" | "audio";
  /** relative to the episode (or its bible) */
  path: string;
  subject?: string;
}

export interface EpisodeStatus {
  episode: string;
  title: string;
  pass: Pass;
  fps: number;
  width: number | null;
  height: number | null;
  folder: string;
  shots: ShotStatus[];
}

export interface Lora {
  name: string;
  strength: number;
}

export interface Sidecar {
  shot?: string;
  take?: number;
  pass?: Pass;
  status?: TakeStatus;
  seed?: Seed | null;
  seed_source?: string;
  model?: string;
  loras?: Lora[] | null;
  steps?: number;
  parent_take?: number | null;
  comfy_prompt_id?: string;
  note?: string;
  [key: string]: unknown;
}

export type TakeFileKind = "mp4" | "thumb" | "strip" | "shotlist" | "h3_wav";

export interface TakeDetail {
  take: number;
  status: TakeStatus;
  has_video: boolean;
  stale: StaleReason[];
  sidecar: Sidecar | null;
  files: Partial<Record<TakeFileKind, string>>;
}

/** A pass's effective override, as shot_detail returns it (no base_hash). */
export interface Override {
  prompt?: string | string[] | null;
  seed?: Seed | null;
  model?: string | null;
  loras?: Lora[] | null;
  steps?: number | null;
  note?: string | null;
}

export interface Effective {
  prompt: string;
  seed: Seed;
  seed_source: string;
  model: string;
  loras: Lora[] | null;
  steps: number;
}

export interface ShotDetail {
  shot: string;
  pass: Pass;
  index: number;
  built: Record<string, unknown>;
  built_prompt: string;
  override: Override;
  override_stale: boolean;
  effective: Effective;
  takes: TakeDetail[];
}

export interface BuildPassResult {
  ok: boolean;
  report: string;
  error: string;
}

export interface BuildResult {
  ok: boolean;
  passes: Partial<Record<Pass, BuildPassResult>>;
}

export type SeedMode = "auto" | "new" | "same";

export interface RenderRequest {
  ep: string;
  pass: Pass;
  shots: string[];
  redo: boolean;
  seed_mode: SeedMode;
  seed: Seed | null;
  model: string | null;
  loras: Lora[] | null;
  steps: number | null;
  prompt: string | null;
  parent_take: number | null;
  note: string;
  /** Queue shots with missing refs anyway (flat grey pictures, no audio ref). */
  allow_missing_refs?: boolean;
}

export interface RenderSkip {
  shot: string;
  reason: string;
  take?: number;
  missing_refs?: MissingRef[];
}

export interface RenderResult {
  queued: { shot: string; take: number; prompt_id: string; seed: Seed; seed_source: string }[];
  skipped: RenderSkip[];
  errors: { shot: string; error: string; take?: number }[];
}

export interface TakeRef {
  ep: string;
  pass: Pass;
  shot: string;
  take: number;
}

/** API.md says cancel "returns the take's new status" without a shape. */
export type CancelResult = { status?: TakeStatus } & Record<string, unknown>;

export interface PickRequest {
  ep: string;
  pass: Pass;
  shot: string;
  take: number | null;
  from_pass: Pass | null;
  force?: boolean;
}

export interface CutEntry {
  shot: string;
  take?: number;
  pass?: Pass;
  trim_in?: number;
  trim_out?: number;
  locked?: boolean;
  note?: string;
}

export interface CutFile {
  episode?: string;
  final?: CutEntry[];
  proxy?: CutEntry[];
}

export interface OverrideFields {
  prompt?: string | null;
  seed?: Seed | null;
  model?: string | null;
  loras?: Lora[] | null;
  steps?: number | null;
  note?: string | null;
}

export interface OverrideRequest {
  ep: string;
  pass: Pass;
  shot: string;
  both: boolean;
  fields: OverrideFields;
}

export interface OverrideResult {
  override: Partial<Record<Pass, Override & { stale?: boolean }>>;
}

export interface AssembleResult {
  ok: boolean;
  output: string;
  report: string;
}

// websocket events (docs/API.md "Live updates")
export interface TakeEvent {
  ep: string;
  pass: Pass;
  shot: string;
  take: number;
  status: TakeStatus;
  thumb?: string | null;
}

export interface EpisodeEvent {
  ep: string;
}

export interface ProgressEvent {
  value: number;
  max: number;
  prompt_id?: string;
  node?: string | null;
}

export interface PromptEvent {
  prompt_id?: string;
  exception_message?: string;
  node_type?: string;
  [key: string]: unknown;
}

export interface ComfyQueue {
  running: string[];
  pending: string[];
}

// ---------------------------------------------------------------------------
// folder browser (API.md "Round 2 additions")
// ---------------------------------------------------------------------------

export interface BrowseDir {
  name: string;
  path: string;
  /** has a series.json and a script */
  episode: boolean;
  /** has a series.json */
  bible: boolean;
}

export interface BrowseFile {
  name: string;
  path: string;
  size?: number;
}

export interface BrowseResult {
  /** "" (or absent) at the starting points */
  path: string;
  /** null at the starting points; "" at a drive root (up = the starting points) */
  parent: string | null;
  episode: boolean;
  truncated: boolean;
  dirs: BrowseDir[];
  /** Not in the contract: only when the request asks for files (see api.ts). */
  files?: BrowseFile[];
}

export type BrowseFiles = "image" | "audio";

// ---------------------------------------------------------------------------
// references (API.md "References (Phase 5)")
// ---------------------------------------------------------------------------

export type RefScope = "series" | "shot";
export type RefKind = "character" | "prop" | "vehicle" | "location" | "voice" | "keyframe";

export interface RefTake {
  take: number;
  status: TakeStatus;
  seed: Seed | null;
  /** the candidate file (image, or audio for a voice), relative like `path` */
  image: string | null;
  source: "generated" | "imported";
  note: string;
  /** Not in the contract's list example; read if a server sends them. */
  prompt?: string;
  model?: string;
  steps?: number;
  loras?: Lora[] | null;
  queued?: string | null;
  finished?: string | null;
  save_notes?: string;
  comfy_prompt_id?: string;
  /** image size, once the saver or the import has written it */
  width?: number | null;
  height?: number | null;
}

export interface RefView {
  /** 01_threequarter | 02_side | 03_back | 04_face */
  view: string;
  picked: number | null;
  takes: RefTake[];
}

/** What a generate would use now (not in the contract yet: see api.ts). */
export interface RefEffective {
  prompt: string;
  seed: Seed;
  model: string;
  loras: Lora[] | null;
  steps: number;
}

export interface Ref {
  /** subject:<id> | location:<id> | voice:<id> | shot:<shot>:first|last */
  id: string;
  scope: RefScope;
  kind: RefKind;
  name: string;
  /** the file renders read; null when the bible names none (a voice-only character) */
  path: string | null;
  exists: boolean;
  sha1: string | null;
  used_by: Partial<Record<Pass, string[]>>;
  /** the prompt a generate would use now; null when the bible lacks what it needs */
  prompt: string | null;
  /** false when this ref can't be generated; `why_not` says why */
  can_generate?: boolean;
  why_not?: string | null;
  override: { fields: string[]; stale: boolean };
  /** characters only */
  views?: RefView[];
  takes: RefTake[];
  picked: number | null;
  /** TODO(contract): see api.ts. The override's values and the effective settings. */
  override_values?: Override;
  effective?: RefEffective;
  built_prompt?: string;
}

export interface RefList {
  refs: Ref[];
}

export interface RefGenerateRequest {
  ep: string;
  ref: string;
  view: string | null;
  count: number;
  seed_mode: SeedMode;
  seed: Seed | null;
  prompt: string | null;
  model: string | null;
  loras: Lora[] | null;
  steps: number | null;
  note: string;
}

export interface RefGenerateResult {
  queued: { ref: string; view: string | null; take: number; prompt_id: string; seed: Seed }[];
  errors: { ref?: string; view?: string | null; error: string }[];
}

export interface RefPickRequest {
  ep: string;
  ref: string;
  view?: string | null;
  take: number;
}

export interface RefImportRequest {
  ep: string;
  ref: string;
  view?: string | null;
  source_path: string;
}

export interface RefOverrideRequest {
  ep: string;
  ref: string;
  view?: string | null;
  fields: OverrideFields;
}

export interface RefEvent {
  ep: string;
  ref: string;
  view: string | null;
  take: number;
  status: TakeStatus;
}
