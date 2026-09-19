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
  /** the ComfyUI prompt id of a queued take (its progress events) */
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
  /** Phase 8.5: the length is an estimate (`dur: model` before a take exists):
   * the model decides it at render time. The UI marks it "≈". */
  length_estimated?: boolean;
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

/** PUT /h3pipe/episode-target: the episode's target fields, as GET /h3pipe/episode has them. */
export interface EpisodeTargetResult {
  target?: string | null;
  target_source?: EpisodeTargetSource | null;
  series_target?: string | null;
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
  /** Phase 8.5: the negative prompt the take rendered with, and where it came from */
  negative?: string | null;
  negative_source?: NegativeSource | null;
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
  /** Phase 8.5: the reference sheet (or VACE reference) the take rendered
   * with, relative to the episode, when it was kept. */
  reference_image?: string | null;
}

/** A pass's effective override, as shot_detail returns it (no base_hash). */
export interface Override {
  prompt?: string | string[] | null;
  seed?: Seed | null;
  model?: string | null;
  loras?: Lora[] | null;
  steps?: number | null;
  note?: string | null;
  /** Phase 8: the shot's video target, shared by both passes. (On a ref's
   * override: the image target it generates with.) */
  target?: string | null;
  /** Phase 8.5: the negative prompt, per pass (targets with a `negative` param). */
  negative?: string | null;
  /** Phase 8.5: a two-stage target's (Wan) low-noise model, per pass. */
  model_low?: string | null;
}

/**
 * Where a negative prompt comes from (API.md "Phase 8.5 as built"): the request,
 * the shot's override, the episode's negative.txt, the series config, the
 * target's preset, or none (a target without a negative param: H3, Klein edit).
 */
export type NegativeSource = "request" | "override" | "negative.txt" | "series" | "preset" | "none" | (string & {});

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
  /** Phase 8.5: the negative a render would use now (null for `none`), and where it comes from. */
  negative?: string | null;
  negative_source?: NegativeSource | null;
  /** a two-stage target's low-noise model a render would use now */
  model_low?: string | null;
  /** Phase 8: what to know before rendering (an ignored prompt override, an
   * audio fallback), and why the shot can't render on its target. */
  notes?: string[];
  error?: string;
}

/** What a ref is to a shot (API.md "Inspector: the refs a shot uses"). */
export type RefRole = "subject" | "plate" | "first" | "last" | "reference_sheet" | "voice" | "recording";

/** One ref a shot's current target reads (`GET /h3pipe/shot` `refs_used`). */
export interface RefUsed {
  /** null for a recording, or a plate the series config doesn't name */
  id: string | null;
  kind: string;
  role: RefRole | (string & {});
  /** the live file, relative to the episode (`../` beside a parent-folder series config) */
  path: string | null;
  exists: boolean;
  need?: KeyframeNeed | null;
  /** the picture to show, relative like `path`; null when the file isn't on disk */
  thumb?: string | null;
  /** the loader slot ("Picture 1", "sheet panel 2") */
  slot?: string;
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
  target_source?: ShotTargetSource | null;
  /** Phase 8.5: the refs the shot's current target reads. Absent from older servers. */
  refs_used?: RefUsed[];
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
  /** the files that stopped a shot whose target isn't ready (no take reserved) */
  missing_files?: MissingFile[];
  /** the shot's target (a missing-files skip) */
  target?: string;
  /** the model checks that stopped the shot (h3jobs.check_models) */
  model_mismatch?: { param: string; file: string; family: string; label: string; message: string }[];
}

/** POST /h3pipe/render. What a render will change (an audio fallback, an ignored
 * prompt override) isn't here: it is in shot detail's `effective.notes`
 * beforehand and the take's sidecar `notes` after. */
export interface RenderResult {
  queued: { shot: string; take: number; prompt_id: string; seed: Seed; seed_source: string; target?: string }[];
  skipped: RenderSkip[];
  errors: { shot: string; error: string; take?: number }[];
}

export interface TakeRef {
  ep: string;
  pass: Pass;
  shot: string;
  take: number;
}

/** POST /h3pipe/cancel: the take's new status (failed, "cancelled"). */
export interface CancelResult {
  shot: string;
  pass: Pass;
  take: number;
  status: TakeStatus;
  save_notes: string;
  finished: string | null;
}

/** POST /h3pipe/discard (Phase 8.6): the take's files moved to `_trash/`. */
export interface DiscardResult {
  shot: string;
  take: number;
  pass?: Pass;
  /** the files' new paths (under `_trash/`), relative to the episode */
  moved: string[];
  /** the pass's cut picked that take: the pick is gone (back to the latest usable) */
  cut_changed: boolean;
}

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
  /** Phase 8: the shot's video target, shared by both passes; null = the built one.
   * (A ref override: its image target.) */
  target?: string | null;
  /** Phase 8.5: per pass; null clears */
  negative?: string | null;
  model_low?: string | null;
}

export interface OverrideRequest {
  ep: string;
  pass: Pass;
  shot: string;
  both: boolean;
  fields: OverrideFields;
}

/** PUT / DELETE /h3pipe/override: each pass's override (with its `stale`),
 * the target the next render uses and the one the build compiled for. */
export interface OverrideResult {
  override: Partial<Record<Pass, Override & { stale?: boolean }>>;
  target?: string;
  built_target?: string;
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
  /** only when the request asks for files (`files=image|audio`) */
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
  /** the character view it belongs to (null for every other ref) */
  view?: string | null;
  status: TakeStatus;
  /** finished with a file: it can be picked */
  usable?: boolean;
  seed: Seed | null;
  seed_source?: string | null;
  /** the candidate image, relative like `path`; null for a voice (see `audio`) or no file */
  image: string | null;
  /** a voice's candidate recording (then `image` is null) */
  audio?: string | null;
  /** "frame": a keyframe cut out of a video take (POST /h3pipe/refs/keyframe) */
  source: "generated" | "imported" | "frame";
  /** where a "frame" take came from */
  from?: RefFrameSource;
  note: string;
  prompt?: string | null;
  model?: string | null;
  steps?: number | null;
  loras?: Lora[] | null;
  overrides?: string[];
  queued?: string | null;
  finished?: string | null;
  save_notes?: string;
  comfy_prompt_id?: string | null;
  /** image size, once the saver or the import has written it */
  width?: number | null;
  height?: number | null;
  /** the image target it was generated with */
  target?: string | null;
  /** an upload's file name (POST /h3pipe/refs/import, multipart) */
  original_name?: string;
}

/** A ref's override as `/refs` lists it: the field names, `stale`, and the values. */
export interface RefOverrideInfo {
  fields: string[];
  stale: boolean;
  values?: Override;
}

export interface RefView {
  /** 01_threequarter | 02_side | 03_back | 04_face */
  view: string;
  picked: number | null;
  /** Clear (DELETE /h3pipe/refs/pick) removed its pick; auto-pick leaves it alone */
  cleared?: boolean;
  /** what this view generates with now */
  prompt?: string | null;
  /** the view's override: its values are the character's own fields, then the view's */
  override?: RefOverrideInfo;
  effective?: RefEffective | null;
  takes: RefTake[];
}

/** What a generate (seed_mode auto, nothing else set) would use now. A
 * character's top-level `effective` is just `{target}`: each view has the rest. */
export interface RefEffective {
  prompt?: string;
  /** null when a generate would pick a new seed */
  seed?: Seed | null;
  seed_source?: string;
  model?: string | null;
  loras?: Lora[] | null;
  steps?: number | null;
  width?: number | null;
  height?: number | null;
  /** the image target a generate uses now */
  target?: string | null;
}

/** Phase 8.5: whether a shot's target needs a keyframe to render. */
export type KeyframeNeed = "required" | "optional";
/** How a keyframe is filled: the script's `first:` / `last:` line, else
 * continuity (a shot with a previous shot), else generate. A path is also
 * allowed (the script names a file). */
export type KeyframeMethod = "continuity" | "generate" | "import" | "none" | (string & {});

/** A reference image an edit target receives with a keyframe generate. */
export interface EditRef {
  /** null for a composite (a single-reference target gets one collage of the parts) */
  id: string | null;
  role?: "subject" | "plate" | "composite" | (string & {});
  view?: string | null;
  /** null for a composite until a generate composes it */
  path?: string | null;
  /** a composite's name, and what it is made of */
  name?: string;
  parts?: { role?: string; subject?: string; location?: string; name?: string; kind?: string; view?: string | null; path?: string | null }[];
}

/** Where an image-target default comes from: set in the editor for the episode
 * (PUT /h3pipe/refs/defaults), the series config's `refs` block, or built in. */
export type RefDefaultSource = "editor" | "series" | "default";

/** GET /h3pipe/refs `defaults`: the image targets refs and keyframes generate with. */
export interface RefDefaults {
  /** the image target series refs generate with */
  target?: string | null;
  target_source?: RefDefaultSource | null;
  /** the image target keyframes generate with */
  keyframe_target?: string | null;
  keyframe_target_source?: RefDefaultSource | null;
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
  /** the id as a file-name stem (`:` as `__`) */
  key?: string;
  /** the series config subject it belongs to (null for a location or a keyframe) */
  subject?: string | null;
  /** false when this ref can't be generated; `why_not` says why */
  can_generate?: boolean;
  why_not?: string | null;
  override: RefOverrideInfo;
  /** a character's four views; `[]` for every other ref */
  views?: RefView[];
  takes: RefTake[];
  picked: number | null;
  /** Clear removed its pick (auto-pick leaves it alone until something is picked) */
  cleared?: boolean;
  /** the ref's own override values (the same as `override.values`) */
  override_values?: Override;
  /** what a generate uses now; null when it can't be generated */
  effective?: RefEffective | null;
  /** the series config's prompt before any override (a character's: its sheet text) */
  built_prompt?: string;
  // ---- Phase 8.5: keyframes as needed refs ----
  /** required (e.g. Wan 14B I2V's first frame) or optional; absent from older
   * servers, and null for a keyframe no target reads */
  need?: KeyframeNeed | null;
  method?: KeyframeMethod | null;
  shot?: string | null;
  which?: "first" | "last" | null;
  /** a keyframe: the video target that will read it */
  target?: string | null;
  /** the script's `first:` / `last:` line asks for this keyframe (so
   * "missing" and Generate missing include it when optional) */
  requested?: boolean;
  /** the script's raw `first:` / `last:` value */
  script?: string | null;
  /** whether the shot's target reads that end */
  reads?: boolean | null;
  /** a path the script names (method "import") */
  import_path?: string;
  /** with an edit keyframe target: exactly the references a generate feeds it */
  edit_refs?: EditRef[];
}

export interface RefList {
  refs: Ref[];
  /** the episode's image targets (null when the series config's `refs` block is bad) */
  defaults?: RefDefaults | null;
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
  /** Phase 8.5: an image target for this call, beating the ref's override and
   * the series default. Only sent when set. */
  target?: string | null;
}

export interface RefGenerateResult {
  queued: { ref: string; view: string | null; take: number; prompt_id: string; seed: Seed; target?: string }[];
  errors: { ref?: string; view?: string | null; error: string; take?: number }[];
}

/** POST /h3pipe/refs/generate-missing (Phase 8.6). */
export interface RefGenerateMissingRequest {
  ep: string;
  pass?: Pass;
  /** default both */
  kinds?: ("series" | "keyframe")[];
  target?: string | null;
  keyframe_target?: string | null;
  /** plan only: the same shape, nothing queued (take / prompt_id null) */
  dry_run?: boolean;
}

/** How Generate missing filled a ref: a generated candidate, a frame from the
 * neighbouring take (continuity), or the script's file (import). */
export type MissingMethod = "generate" | "continuity" | "import" | (string & {});

export interface RefGenerateMissingResult {
  /** a still (method generate); a dry run's take / prompt_id are null, and seed when a new one would be drawn */
  queued: { ref: string; view?: string | null; take: number | null; prompt_id: string | null; seed?: Seed | null; seed_source?: string | null; target?: string | null; method?: MissingMethod }[];
  /** filled at once: a continuity frame or the script's file (a dry run's take is null) */
  picked: { ref: string; take: number | null; method?: MissingMethod; view?: string | null }[];
  skipped: { ref: string; reason: string; view?: string | null }[];
  /** as `/refs/generate`'s errors (`missing_files` when a model isn't installed) */
  errors: { ref?: string; view?: string | null; error: string; take?: number; missing_files?: MissingFile[] }[];
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
  /** Phase 8.6: pick it at once */
  pick?: boolean;
}

/** POST /h3pipe/refs/import as multipart/form-data (Phase 8.6, drag and drop). */
export interface RefUploadRequest {
  ep: string;
  ref: string;
  view?: string | null;
  /** pick it at once (sent as "1") */
  pick?: boolean;
  file: Blob;
  /** the file's name (a File has its own) */
  name?: string;
}

/** POST /h3pipe/refs/discard (Phase 8.6): move a candidate to `_trash/`. */
export interface RefDiscardRequest {
  ep: string;
  ref: string;
  view?: string | null;
  take: number;
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
  take: number | null;
  /** queued | ok | failed | picked, or cleared (DELETE /h3pipe/refs/pick) */
  status: TakeStatus | "picked" | "cleared" | (string & {});

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
    /** Phase 8.5, image targets: text-to-image, or an edit with reference images */
    mode?: "t2i" | "edit" | (string & {});
    /** Phase 8.5, image edit targets: how many reference images it takes */
    max_refs?: number;
    /** Phase 8.5, video: the first keyframe is required (Wan 14B I2V) */
    requires_first?: boolean;
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
  /** an optional file: the feature it enables (the same words as its `features_off` entry) */
  feature?: string | null;
  /** the family's label */
  label?: string | null;
  /** which passes miss the file */
  passes?: Pass[];
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
