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
  /** Phase 9b: the file whose sound the cut plays for this take (the mp4 when
   * it has an audio stream, else its `_h3.wav`, else null), relative to the episode. */
  audio?: string | null;
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
  /** Phase 9b: the shot's index in the pass's cut (absent from older servers). */
  order?: number | null;
  /** Phase 9b: the shot's index in script order (null for an orphan). */
  script_index?: number | null;
  /** Phase 9b: not where script order would put it (the timeline badges it). */
  out_of_order?: boolean;
  /** Phase 9d: the clip's audio source, as cut.json stores it (null / absent:
   * the clip's own take's sound). */
  audio?: CutAudioSource | null;
  /** Phase 9d: what will actually play, relative to the episode (null for
   * `none`, and for a source the server couldn't resolve). */
  audio_file?: string | null;
  /** Phase 9d: short text for the speaker badge ("sh020 t01", "line.wav"). */
  audio_why?: string | null;
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
  /** Phase 9b: the shot's dialogue window on the episode's `track`, seconds
   * (a shot timed against recorded dialogue); absent otherwise. */
  audio_in?: number | null;
  audio_out?: number | null;
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
  /** Phase 9b: the series config's recorded dialogue (`audio.track`), or null. */
  track?: Track | null;
}

/** Phase 9b: the episode's recorded dialogue track. */
export interface Track {
  /** relative to the episode (`../` for a parent-folder series config) */
  path: string;
  /** seconds (null when unknown) */
  duration?: number | null;
  /** sample rate (null when unknown) */
  rate?: number | null;
  /** the file is there (as built: false for a missing file; `duration` / `rate` are then null) */
  exists?: boolean;
  /** Phase 9c: a usable transcript sits beside the recording (`<rec>.words.json`,
   * newer than it): re-aligning then needs no Whisper, only ffmpeg. */
  words?: boolean;
  /** Phase 9c: how many of the pass's shots carry a dialogue window (0 until
   * the script has `audio:` lines and the episode is rebuilt). */
  aligned?: number;
}

/** Phase 9b: GET /h3pipe/peaks. */
export interface PeaksResult {
  /** seconds of the whole file */
  duration: number;
  bins: number;
  /** max absolute amplitude per bin, 0..255 */
  peaks: number[];
  /** the file has no audio stream (then `bins` 0 and `peaks` []) */
  silent?: boolean;
  /** as built: the range actually used, clamped to the file */
  start?: number | null;
  end?: number | null;
}

/** Phase 9b: what POST /h3pipe/cut/reset and /cut/copy touch. Phase 9d adds
 * "audio" (and "all" clears it too). */
export type CutWhat = "order" | "trims" | "audio" | "all";

// ---------------------------------------------------------------------------
// Phase 9d: a shot's audio from elsewhere
// ---------------------------------------------------------------------------

/** Where a clip's sound comes from: another take, a file, or silence. */
export type CutAudioKind = "take" | "file" | "none";

/**
 * A cut entry's `audio` (absent or null: the clip's own take's sound). The
 * audio is cut or padded with silence to the clip's length on the cut's
 * clock, so a clip's length never changes.
 */
export interface CutAudioSource {
  source: CutAudioKind;
  /** take: the shot it comes from (absent: this entry's own shot) */
  shot?: string;
  /** take: which take (its mp4's sound, else its `_h3.wav`) */
  take?: number;
  /** take: which pass the take is in */
  pass?: Pass;
  /** file: a media file inside the episode (`../` beside a parent-folder series config) */
  path?: string;
  /** seconds into the source where the audio begins (0 or more) */
  start?: number;
  /** seconds it is shifted against the picture (positive = later; the gap is silence) */
  offset?: number;
  /** a linear multiplier, 1.0 unchanged (0–4) */
  gain?: number;
}

// ---------------------------------------------------------------------------
// Phase 9c-A: attaching and aligning a recording
// ---------------------------------------------------------------------------

/** GET /h3pipe/align/ready: what h3align needs, in the Python that would run it. */
export interface AlignReady {
  ready: boolean;
  /** the ffmpeg on PATH, or null */
  ffmpeg: string | null;
  /** pip names, in order: ffmpeg, numpy, faster-whisper */
  missing: string[];
  /** the interpreter the answer is about (ComfyUI's own) */
  python: string;
  /** the pip line for the missing packages ("" when only ffmpeg is missing) */
  install: string;
  /** as built: what to do when ffmpeg is missing */
  ffmpeg_hint?: string;
  /** as built: the installed version ("" without metadata), null when it isn't installed */
  packages?: Record<string, string | null>;
  models?: { default?: string; choices?: string[] };
}

/** POST /h3pipe/track: attach (or clear) the episode's dialogue recording. */
export interface TrackResult {
  track: Track | null;
  /** the series config's hash after the write */
  hash: string;
  /** the rebuild; it FAILS when the script has no `audio:` windows yet — that
   * is "align to finish", not an error (API.md "Phase 9c-A as built") */
  build: BuildResult | null;
  /** the recording's path relative to the episode */
  path?: string | null;
  /** it was copied into `<ep>/audio/` (false: used where it already was) */
  copied?: boolean;
  /** the whole series config was rewritten in the promote's format */
  reformatted?: boolean;
}

export interface AlignRequest {
  ep: string;
  /** a path relative to the episode; without it, the series config's `audio.track` */
  track?: string | null;
  model?: string | null;
  /** snap the windows to the frame grid (default true) */
  snap?: boolean;
  dry_run?: boolean;
  pass?: Pass;
}

/** One shot in an align report: null times mean it keeps its `dur:`. */
export interface AlignChange {
  shot: string;
  audio_in: number | null;
  audio_out: number | null;
  note?: string;
}

/** POST /h3pipe/align. */
export interface AlignResult {
  ok: boolean;
  dry_run: boolean;
  /** align_report.md's text (a dry run has it too) */
  report: string;
  /** "align_report.md", or null on a dry run */
  report_path: string | null;
  changes: AlignChange[];
  notes: string[];
  recording: string | null;
  duration: number | null;
  /** a cached transcript was used (no Whisper ran) */
  words: boolean;
  script_hash: string | null;
  series_hash: string | null;
  track: Track | null;
  build: BuildResult | null;
  /** everything h3align printed (progress lines removed) */
  log: string;
}

/** The body of POST /h3pipe/align's 409: a dependency isn't installed. */
export interface AlignMissing {
  error: string;
  missing: string[];
  install: string;
  python: string;
  ffmpeg: string | null;
  /** a transcript was found: no Whisper is needed, only what's still listed */
  words?: boolean;
}

/** The `h3pipe.align` event, while a run is going. */
export interface AlignEvent {
  ep: string;
  stage: "transcribe" | "match" | "write" | (string & {});
  pct: number;
  text: string;
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
  /** As built: set (with no passes at all) when the build couldn't start —
   * no series.json, or the folder has more than one candidate script
   * ("can't tell which .md in the folder is the script"). */
  error?: string;
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
  /** Phase 9d: this clip's audio from elsewhere (absent / null: its own). */
  audio?: CutAudioSource | null;
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
  /**
   * the reference images this take was generated FROM, in order; `[]` when it
   * was generated from the prompt alone. On a target that does both (Qwen-Image
   * 2.1) this is the only way to tell an edit from a text-to-image generate.
   */
  references?: { id?: string | null; name?: string | null; view?: string | null; path?: string | null }[];
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
  /** "frame": a keyframe cut out of a video take (POST /h3pipe/refs/keyframe);
   * "from_take" (Phase 9c): a voice sample cut out of a take's sound */
  source: "generated" | "imported" | "frame" | "from_take";
  /** where a "frame" or "from_take" take came from */
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
  // ---- Phase 9c-B: a voice candidate ----
  /** how long it was asked to be (whole seconds after the grid snapped it) */
  seconds?: number | null;
  /** the line it says */
  line?: string | null;
  /** where that line came from */
  line_source?: VoiceLineSource | null;
}

/** Where a generated voice's line comes from: the character's longest line in
 * this episode's script, the fixed neutral sentence, this call's prompt, or
 * the ref's prompt override. */
export type VoiceLineSource = "script" | "neutral" | "request" | "override" | (string & {});

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
  /** the image target a generate uses now (an audio one for a voice) */
  target?: string | null;
  // ---- Phase 9c-B, a voice ref ----
  /** how long a generate would be, after the target's grid snapped it */
  seconds?: number | null;
  /** the line it would say, and where that line comes from */
  line?: string | null;
  line_source?: VoiceLineSource | null;
  /** the audio target's longest sample */
  max_seconds?: number | null;
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
  /** Phase 9c-B: the audio target voice refs generate with */
  voice_target?: string | null;
  voice_target_source?: RefDefaultSource | null;
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
  /**
   * a wardrobe variant's base: the subject it is a variant of (`of:` in the
   * series config), null for everything else. A variant keeps that character's
   * `name` — it is the same character, and that name goes into every prompt —
   * so this is what tells the two rows apart.
   */
  of?: string | null;
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
  /** Phase 9c-B: how many seconds of voice (a voice ref only; 400 on anything
   * else, or outside the audio target's range). Only sent when set. */
  seconds?: number | null;
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

/** A keyframe take's source: shot, take and pass, and the frame index used.
 * Phase 9c: a voice cut out of a take carries `start` / `end` seconds instead
 * of a frame. */
export interface RefFrameSource {
  shot: string;
  take: number | null;
  pass: Pass | null;
  frame?: number | null;
  frames?: number | null;
  /** seconds into the take's sound (source "from_take") */
  start?: number | null;
  end?: number | null;
}

/** Where a voice sample was cut from (the answer's `source`, as built). */
export interface VoiceClipSource {
  shot: string;
  take: number | null;
  pass: Pass | null;
  start: number;
  end: number;
  /** the file the sound came from, relative to the episode */
  file?: string | null;
}

/** POST /h3pipe/refs/voice-from-take: a line a take already spoke as a sample. */
export interface VoiceFromTakeRequest {
  ep: string;
  ref: string;
  shot: string;
  take: number;
  pass: Pass;
  start: number;
  end: number;
  /** null (the default): pick it only when the voice has no live file */
  pick?: boolean | null;
  note?: string;
}

/**
 * The ref as `/refs` lists it, plus what the cut became. Careful: as built,
 * the answer's `picked` is a BOOLEAN (whether the new candidate went live),
 * not the ref listing's picked take number, which it overwrites — so the
 * editor refetches the listing instead of using this as a Ref.
 */
export type VoiceFromTakeResult = Omit<Ref, "picked"> & {
  picked: boolean;
  /** the new candidate */
  take: number;
  /** where the sound came from. As built this is an OBJECT (the contract only
   * said "source"): the shot, take, pass, span and the file it was cut from. */
  source?: VoiceClipSource | null;
  /** the pick wrote `voice_sample` into the series config */
  series_changed?: boolean;
};

/** PUT /h3pipe/refs/pick: the ref, and whether the series config gained a
 * `voice_sample` line (Phase 9c-B). */
export type RefPickResult = Ref & { series_changed?: boolean };

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

export type TargetKind = "video" | "image" | "audio";

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
    /** Phase 9c-B, audio targets: it can copy a voice from a sample
     * (`ltx2_voice` says false: the ID-LoRA weights aren't installed) */
    reference_audio?: boolean;
    /** Phase 9c-B, audio targets: the longest sample it generates, seconds */
    max_seconds?: number;
    [key: string]: unknown;
  };
  template?: { fps?: number; frames?: { step?: number; base?: number; max?: number }; size_multiple?: number };
  /** The family each model param must be (target.json `models`), by param. */
  models?: Record<string, { family: string; label?: string; patterns?: string[]; folder?: string | null; tier?: RequirementTier }>;
  /** Only with `?ready=1`: what's installed, what's missing and where to get it. */
  readiness?: Readiness | null;
  /** Phase 11: which workflow this target's next render uses. */
  graph?: TargetGraph;
  /** Phase 12: a show's own target, from `<show>/targets/<id>/target.json`. */
  custom?: boolean;
  /** Phase 12: saved but not proved yet — no probe render has succeeded, so the
   * shot and episode pickers leave it out. */
  draft?: boolean;
}

/** Phase 12b: one saved ComfyUI workflow (GET /h3pipe/workflows). */
export interface WorkflowFile {
  name: string;
  /** a workflow one of h3pipe's own targets already drives */
  target?: boolean;
}

/** Phase 12b: POST /h3pipe/targets/inspect — the target.json it proposes, and
 * what it could not work out. `proposal` is saved as-is once confirmed. */
export interface TargetProposal {
  proposal: Record<string, unknown>;
  matched: Record<string, string>;
  ambiguous: {
    param: string;
    ask: string;
    candidates: { node: string; class_type: string; field: string; title?: string; value?: unknown }[];
  }[];
  warnings: string[];
  models: { param: string; file: string; family?: string; folder?: string; label?: string }[];
  /** node classes outside ComfyUI core, by the pack they come from */
  nodes: Record<string, string>;
  /** widgets no param covers: they keep the graph's own value */
  unbound?: { class_type: string; field: string; value?: unknown; title?: string }[];
  /** the graph itself is wrong: nothing can be saved until these are fixed */
  problems: string[];
  can_save?: boolean;
}

/** PUT / DELETE /h3pipe/targets/custom. */
export interface CustomTargetResult {
  ok?: boolean;
  id?: string;
  path?: string;
  draft?: boolean;
  deleted?: boolean;
  targets?: { id: string; kind: string; label: string; short: string; draft: boolean; workflow: string; path: string }[];
}

/**
 * Phase 11: the graph a target renders with (GET /h3pipe/targets `graph`).
 * `source` is where it comes from, in the order a render resolves it: the
 * target's env var, a workflow saved in the running ComfyUI, $COMFYUI_PATH's
 * workflows folder, then the repo's own copy.
 */
export interface TargetGraph {
  /** the workflow file name the binding looks up */
  name: string;
  source: "env" | "comfy" | "saved" | "repo" | "none";
  /** the path, or the saved workflow it was read from */
  where: string;
  /** the environment variable that would win, and whether it is set */
  env?: string;
  env_set?: boolean;
  /** a copy is saved in this ComfyUI under `name` (it may not be the one in force) */
  installed: boolean;
  /** the graph in force isn't the repo's copy, ignoring what a job patches */
  differs: boolean;
  /** the repo copy's path, "" when the target ships none */
  repo?: string;
  /** why a candidate couldn't be read (the rest still applies) */
  error?: string;
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


/**
 * What one model param resolved to when the take was queued (targets/__init__.py).
 * A file name — except the `loras` param, whose want/using are LISTS of names.
 */
export interface Resolution {
  want?: string | string[] | null;
  using?: string | string[] | null;
  how: "exact" | "family" | "base" | "off" | string;
}

export interface Readiness {
  status: ReadinessStatus;
  missing: MissingFile[];
  resolved: Record<string, Resolution>;
  features_off: string[];
  nodes_missing: string[];
}

/** POST / DELETE /h3pipe/workflow/install (Phase 11). */
export interface WorkflowInstallResult {
  ok?: boolean;
  target?: string;
  /** POST: the name it was saved as */
  installed?: string;
  /** POST: whether that name is the one renders pick up */
  renders?: boolean;
  /** DELETE: whether a saved copy was there */
  deleted?: boolean;
  name?: string;
  graph?: TargetGraph;
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

// ---------------------------------------------------------------------------
// Phase 9a: the script and series config windows, promote
// ---------------------------------------------------------------------------

/** Which authored file: the episode's epNN.md, or its series.json. */
export type SourceFile = "script" | "series";

/** A shot's line span in the script (1-based, inclusive). */
export interface SourceShotSpan {
  id: string;
  line: number;
  end_line: number;
}

/** GET /h3pipe/source */
export interface SourceDoc {
  file: SourceFile;
  /** relative to the episode (`../series.json` for a parent-folder series config) */
  path: string;
  text: string;
  /** sha1 of the bytes on disk */
  hash: string;
  mtime: number;
  /** script only; empty if it doesn't parse */
  shots: SourceShotSpan[];
}

/** GET /h3pipe/source?…&hash_only=1 */
export interface SourceHash {
  file: SourceFile;
  hash: string;
  mtime: number;
}

/** One error or warning of a check, 1-based line (and col) into the checked text.
 * As built: `file` is "script" | "series"; `line` is null for a series config
 * message that names no key; `col` only for a JSON syntax error. */
export interface SourceMessage {
  /** which file it's in (a check can report on the other file too) */
  file: string;
  line: number | null;
  col?: number;
  message: string;
}

/** POST /h3pipe/source/check (and `check` of PUT /h3pipe/source) */
export interface SourceCheck {
  ok: boolean;
  errors: SourceMessage[];
  warnings: SourceMessage[];
  shots: SourceShotSpan[];
}

export interface SourceSaveRequest {
  ep: string;
  file: SourceFile;
  text: string;
  base_hash: string;
  rebuild: boolean;
}

/** PUT /h3pipe/source */
export interface SourceSaveResult {
  hash: string;
  check: SourceCheck;
  build: BuildResult | null;
}

/** The body of a 409 from PUT /h3pipe/source or POST /h3pipe/promote. */
export interface SourceConflictBody {
  error: string;
  /** as built: which file changed */
  file?: SourceFile;
  hash: string;
  text: string;
}

export type PromoteScope = "shot" | "episode" | "ref";

export interface PromoteItem {
  id: string;
  scope: PromoteScope;
  shot?: string;
  ref?: string;
  view?: string | null;
  field: string;
  value: unknown;
  dest: SourceFile;
  line?: number;
  summary: string;
}

export interface PromoteLeft {
  scope: PromoteScope;
  shot?: string;
  ref?: string;
  view?: string | null;
  /** as built: a per-pass field left in one pass (a prompt, a negative, model_low) */
  pass?: Pass | null;
  field: string;
  reason: string;
}

export interface PromoteHashes {
  script: string;
  series: string;
}

/** GET /h3pipe/promote */
export interface PromotePlan {
  items: PromoteItem[];
  left: PromoteLeft[];
  diffs: { script: string; series: string };
  hashes: PromoteHashes;
}

/** POST /h3pipe/promote */
export interface PromoteResult {
  promoted: string[];
  left: PromoteLeft[];
  hashes: PromoteHashes;
  build: BuildResult | null;
}
