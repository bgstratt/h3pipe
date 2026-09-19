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
  /** Phase 7: the video target the take rendered with, from its sidecar
   * (null for a take from before sidecars; absent from older servers). */
  target?: string | null;
  /** The take's real frame count, as the saver wrote it (null: not rendered;
   * absent from older servers). A `dur: model` take's is the model's choice. */
  frames?: number | null;
  /** The frame rate the take was rendered at, from its sidecar (a Wan 14B
   * take is 16 fps in a 24 fps episode); null/absent: the episode's. */
  fps?: number | null;
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
  /** The cut take's real frame count (its sidecar's), or null. The shot's
   * `seconds` is the build's (for `dur: model`, an estimate); the timeline and
   * Play all use this when present. Absent from older servers. */
  frames?: number | null;
  /** The frame rate of `frames` (and of the build's `length` when there is no
   * take): the cut take's, else the shot's target's. Absent: the episode's. */
  fps?: number | null;
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
  /** Phase 7/8: the video target the shot's next render uses (request, then
   * override, then script, then series config). Absent from older servers. */
  target?: string | null;
  /** Phase 8: the target the build compiled the shot for. */
  built_target?: string | null;
  /** Phase 7: the render profile the shot was built with, or null. */
  profile?: string | null;
  /** Where `target` comes from: the render request, a shot override, the script
   * (a shot/sequence line or a profile), or the episode default. Absent from
   * older servers. */
  target_source?: ShotTargetSource | null;
}

/** "series" / "default": the shot has no target of its own and follows the episode's
 * default (series.json's, or MiniMax H3), as "episode" does for an editor-set one. */
export type ShotTargetSource = "request" | "override" | "script" | "episode" | "series" | "default";
/** Where the episode's default target comes from: set in the editor
 * (overrides.json `episode.target`), the series config's `series.target`, or
 * the built-in default. */
export type EpisodeTargetSource = "editor" | "series" | "default";

export interface MissingRef {
  /** the loader slot, e.g. "Picture 4" or "Audio 1" */
  slot: string;
  kind: "image" | "audio";
  /** relative to the episode (or its series config) */
  path: string;
  subject?: string;
  /** false: the shot can't be rendered without it, even with render anyway
   * (Wan 14B I2V's first frame); `why` says what to do instead */
  anyway?: boolean;
  why?: string;
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
  /** The episode's default video target now in force (the editor's episode
   * target, else the series config's `series.target`, else the built-in default). */
  target?: string | null;
  /** Where `target` comes from (absent from older servers). */
  target_source?: EpisodeTargetSource | null;
  /** The series config's `series.target` (null when it names none). */
  series_target?: string | null;
}

/** PUT /h3pipe/episode-target: "the episode's target info" (TODO(contract): shape). */
export interface EpisodeTargetResult {
  target?: string | null;
  target_source?: EpisodeTargetSource | null;
  series_target?: string | null;
  [key: string]: unknown;
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
  /** How each model param resolved to an installed file when the take was queued. */
  resolved?: Record<string, Resolution> | null;
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
  /** Phase 8: the shot's video target, shared by both passes. */
  target?: string | null;
}

export interface Effective {
  prompt: string;
  seed: Seed;
  seed_source: string;
  model: string;
  loras: Lora[] | null;
  steps: number;
  /** Phase 8: the target a render would use, and its size and length. */
  target?: string | null;
  width?: number | null;
  height?: number | null;
  length?: number | null;
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
  /** Phase 7/8 (see ShotStatus) */
  target?: string | null;
  built_target?: string | null;
  profile?: string | null;
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
  /** Phase 8: the video target for this run only, beating the override. Only
   * sent when set. */
  target?: string | null;
  /** Queue shots whose model file is another family than the target needs. Only sent when set. */
  allow_model_mismatch?: boolean;
}

export interface RenderSkip {
  shot: string;
  reason: string;
  take?: number;
  missing_refs?: MissingRef[];
  /** TODO(contract): the files that stopped a shot whose target isn't ready
   * ("blocked before a take is reserved, naming the file and where to get
   * it"). Read as `missing_files` or `missing` (entries like readiness's). */
  missing_files?: MissingFile[];
  missing?: MissingFile[];
  /** the shot's target, when the server names it */
  target?: string;
  /** the model checks that stopped the shot (h3jobs.check_models) */
  model_mismatch?: { param: string; file: string; family: string; label: string; message: string }[];
  warnings?: RenderWarningRaw[];
}

/** A render warning. The contract doesn't give a shape (see api.ts): read as a
 * string or an object with `shot` and `warning`/`message`/`text`. */
export type RenderWarningRaw = string | { shot?: string; warning?: string; message?: string; text?: string };

export interface RenderResult {
  queued: { shot: string; take: number; prompt_id: string; seed: Seed; seed_source: string; target?: string; warnings?: RenderWarningRaw[] }[];
  skipped: RenderSkip[];
  errors: { shot: string; error: string; take?: number; warnings?: RenderWarningRaw[] }[];
  /** e.g. audio downgraded to generate for a target with no voice reference */
  warnings?: RenderWarningRaw[];
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
  /** Phase 8: the shot's video target, shared by both passes; null = the built one. */
  target?: string | null;
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
  series_config: boolean;
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
  /** "frame": a keyframe cut out of a video take (POST /h3pipe/refs/keyframe) */
  source: "generated" | "imported" | "frame";
  /** where a "frame" take came from */
  from?: RefFrameSource;
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
  /** the file renders read; null when the series config names none (a voice-only character) */
  path: string | null;
  exists: boolean;
  sha1: string | null;
  used_by: Partial<Record<Pass, string[]>>;
  /** the prompt a generate would use now; null when the series config lacks what it needs */
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

/** A keyframe take's source: shot, take and pass, and the frame index used. */
export interface RefFrameSource {
  shot: string;
  take: number | null;
  pass: Pass | null;
  frame: number | null;
  frames?: number | null;
}

/** POST /h3pipe/refs/keyframe: a shot's first / last keyframe from a frame of a take. */
export interface RefKeyframeRequest {
  ep: string;
  pass: Pass;
  shot: string;
  which: "first" | "last";
  /** default: the previous (first) / next (last) shot in the cut */
  source_shot?: string | null;
  /** default: the take that shot's cut entry uses */
  source_take?: number | null;
  /** a frame number (negative from the end), "first" or "last"; default: last for first, first for last */
  frame?: number | "first" | "last" | null;
  /** null: picked only if the keyframe has no live file; true: always; false: never */
  pick?: boolean | null;
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

// ---------------------------------------------------------------------------
// targets (API.md "Targets (Phase 7)", "Phase 8 additions")
// ---------------------------------------------------------------------------

export type TargetKind = "video" | "image";

/** One binding entry: a graph widget (`class_type` + `field`, or for LoRAs
 * `name`/`strength`/`input`/`chain`), or `{"via": "loader"}`. */
export type TargetWidget =
  | { class_type: string; field?: string; name?: string; strength?: string; input?: string; chain?: boolean }
  | { via: string };

export interface TargetPreset {
  model?: string | null;
  lora?: string | null;
  loras?: Lora[] | null;
  steps?: number | null;
  width?: number | null;
  height?: number | null;
  [key: string]: unknown;
}

export interface Target {
  id: string;
  kind: TargetKind;
  label: string;
  default?: boolean;
  presets?: Partial<Record<Pass, TargetPreset>>;
  widgets?: {
    model?: TargetWidget;
    loras?: TargetWidget;
    /** tolerated alias */
    lora?: TargetWidget;
    steps?: TargetWidget;
    seed?: TargetWidget;
    [key: string]: TargetWidget | undefined;
  };
  workflow?: string;
  loader?: string;
  saver?: string;
  /** Phase 8: a short label ("H3", "LTX-2") */
  short?: string;
  /** Phase 8: what the target can use; `keyframes` lists the ends it reads ([] for none) */
  capabilities?: {
    keyframes?: string[];
    policies?: string[];
    voice_reference?: boolean;
    subject_refs?: boolean;
    prompt?: string;
    negative_prompt?: boolean;
    [key: string]: unknown;
  };
  template?: { fps?: number; frames?: { step?: number; base?: number; max?: number }; size_multiple?: number };
  /** The family each model param must be (target.json `models`), by param. */
  models?: Record<string, { family: string; label?: string; patterns?: string[]; folder?: string | null; tier?: RequirementTier }>;
  /** Only with `?ready=1`: what's installed, what's missing and where to get it. */
  readiness?: Readiness | null;
}

// ---------------------------------------------------------------------------
// readiness (API.md "Readiness, requirement tiers, and the episode target")
// ---------------------------------------------------------------------------

/** required: the target can't render without it; accelerator: renders fall back
 * to the slower `base` preset; optional: one feature is off. */
export type RequirementTier = "required" | "accelerator" | "optional";

export type ReadinessStatus = "ready" | "degraded" | "not_ready" | "unknown";

export interface MissingFile {
  param: string;
  tier: RequirementTier;
  /** the file the preset names */
  want: string;
  family?: string | null;
  /** the ComfyUI models folder it belongs in, e.g. "loras" */
  folder?: string | null;
  /** only from a trustworthy record; never guessed */
  url?: string | null;
  /** what to search for when there's no URL */
  source?: string | null;
  /** TODO(contract): for an optional file, the feature it enables. Not in the
   * contract; read if sent, else the param's label is used. */
  feature?: string | null;
}

export interface Resolution {
  want?: string | null;
  using?: string | null;
  how: "exact" | "family" | "base" | "off" | string;
}

export interface Readiness {
  status: ReadinessStatus;
  missing: MissingFile[];
  resolved: Record<string, Resolution>;
  features_off: string[];
  nodes_missing: string[];
}

/** One file of GET /h3pipe/models: how it stands against the param's family. */
export interface ModelFile {
  name: string;
  /** named like the family, its header says so, or anything else */
  match: "name" | "fingerprint" | "other";
  /** its header says another family: the server skips the shot unless allow_model_mismatch */
  mismatch: boolean;
  family: string | null;
  label: string;
  confidence: "metadata" | "tensors" | "name" | "unknown";
  detail: string;
  base?: string;
}

/** GET /h3pipe/models?target=…&param=… */
export interface ModelList {
  target: string;
  param: string;
  family: string;
  label: string;
  patterns: string[];
  /** false: the server can't read model files, so only names were checked */
  fingerprint: boolean;
  files: ModelFile[];
}

export interface TargetList {
  targets: Target[];
  default: Partial<Record<TargetKind, string>>;
}
