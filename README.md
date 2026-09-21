# h3pipe — script to finished episode on a local ComfyUI

Write an episode as a screenplay-flavoured markdown file (`epNN.md`) plus a series config
(`series.json`). h3pipe parses the script into a model-free **story IR**
(`shotlist/shots.json`), compiles every shot for its **video target** (a model and its
ComfyUI workflow), generates the reference images and keyframes the shots need through an
**image target** and their voice samples through an **audio target**, renders each shot on
a local ComfyUI, and cuts the takes together.

You drive it from an editor inside ComfyUI (shots, takes, refs, the cut) or from one
command, `h3.py`. One shot per queue: nothing chains, so a bad shot is re-rendered alone
and the rest of the episode is untouched. Every take is kept with the exact settings that
made it.

New here? **[INSTALL.md](INSTALL.md)** goes from a clean ComfyUI to a rendered proxy
episode; **[docs/AUTHORING.md](docs/AUTHORING.md)** is the script and series config format.

### The documents

| | What it is | Read it when |
|---|---|---|
| **[INSTALL.md](INSTALL.md)** | What to install and where: the node pack, the workflows, the model files, and the commands that prove it runs | Setting the pipeline up, or a model is missing |
| **[docs/EDITOR.md](docs/EDITOR.md)** | Driving the editor inside ComfyUI: the order things happen in, every button, what click / double-click / right-click do in each panel, and the keyboard | Using the editor, or wondering what a control does |
| **[docs/AUTHORING.md](docs/AUTHORING.md)** | The format, and the whole of it: a first episode to copy, every field of the script and the series config, framing, timing, camera and the reference rules | Writing or fixing an episode. Read once before the first one |
| **[docs/SCRIPT_CONVERSION.md](docs/SCRIPT_CONVERSION.md)** | A spec-format screenplay scene worked all the way through to shots — what the series config absorbs, where the cuts fall and why, and what gets dropped | You have a screenplay and want it as an episode |
| **[docs/API.md](docs/API.md)** | The editor's HTTP API and the on-disk contracts behind it (takes, overrides, the cut, references) | Changing the editor, or driving the pipeline from your own code |
| **[docs/PLAN.md](docs/PLAN.md)** | The working plan: what is built, what was decided and why, what is open | Before changing the pipeline |
| **[CONTRIBUTING.md](CONTRIBUTING.md)** | House rules: stdlib only, the golden tests, how the generated docs are regenerated | Sending a change |

`prompts/SKILL.md` and `prompts/h3-script.instructions.md` are **generated** from
`docs/AUTHORING.md` by `python tools/make_prompts.py`, so an assistant drafting a script
reads exactly what you do. Edit the guide, never the copies — a test fails if they drift.

```
  you write                     generated                                  rendered
  ─────────                     ─────────                                  ────────
  series.json ─┐                shotlist/shots.json (story IR)
  epNN.md     ─┼─ h3build ─▶    shotlist/shotlist[.<target>].json ─ render ─▶ renders/<shot>/<shot>_tNN.mp4
  (recording) ─┘  (h3align)     refs_todo.json ─── refs / keyframes ─▶ refs/      │
                                                                               assemble ─▶ epNN.mp4
```

## Targets

A shot renders on MiniMax H3 Ref2VA unless the script (`target:`), a render profile, the
episode (chosen in the editor) or the series config (`series.target`) names another. A shot
retargeted in the editor or with `h3.py override` is compiled for its new target when it is
queued; no rebuild. The format and how to choose are in
[docs/AUTHORING.md](docs/AUTHORING.md).

| Video target | Label |
|---|---|
| `minimax_h3_ref2va` (default) | MiniMax H3 Ref2VA |
| `minimax_h3_fl2va` | MiniMax H3 FL2VA (first/last frames) |
| `ltx2` | LTX-2.5 distilled (text / keyframes to video + audio) |
| `ltx2_ingredients` | LTX-2.3 ingredients (character/plate refs) |
| `wan22_i2v` | Wan 2.2 14B I2V |
| `wan22_ti2v` | Wan 2.2 5B TI2V |
| `wan22_vace` | Wan 2.2 14B VACE (refs) |

| Image target | Label |
|---|---|
| `krea2` (default for refs) | Krea 2 turbo (text to image) |
| `z_image_turbo` | Z-Image Turbo (fast text to image) |
| `flux2_klein` | FLUX.2 Klein 9B (text to image) |
| `flux2_klein_edit` (default for keyframes, when installed) | FLUX.2 Klein 9B edit (uses reference images) |
| `flux_kontext` | FLUX.1 Kontext dev (edit, one reference) |
| `minimax_h3_still` | MiniMax H3 as an image model: renders 5 frames, keeps the first, up to 9 references |
| `qwen_image_21` | Qwen-Image 2.1: text to image with nothing to edit from, an edit of up to 16 reference images when there is. The only image target with a usable cfg, scheduler, denoise and negative prompt |

| Audio target | Label |
|---|---|
| `ltx2_voice` (default for voices) | LTX-2.5 audio-only: one speaking voice from a character's `voice` line |

Each target lives in `targets/<kind>/<id>/`: `target.json` (presets, the model files it
needs with their tier and download links, the workflow binding), its code (the prompt writer, the
compile), and `workflow.json`. The image and audio targets for refs, keyframes and voices come from
the series config's `refs` block (`target`, `keyframe_target`, `voice_target`), the editor, or
`--target`.

**What's installed?** With ComfyUI running, `python h3.py targets` prints one line per
target (`ready`, `degraded`, `not_ready`, `unknown`) and, under it, every missing file with
its models folder and download link. `--kind video|image|audio` narrows it, an episode folder
adds its series config (`python h3.py targets Shows\ep05`), `--json` prints data. A
missing required file skips the shot with its link; a missing accelerator (a turbo LoRA)
renders the slower base preset; the take says which. The editor's target picker and its
**What's missing** window show the same.

## Install

**[INSTALL.md](INSTALL.md) is the full walkthrough** — requirements, the node pack, the
workflows, every model file with its folder and download link, and a first episode end to
end. The short version:

```
git clone https://github.com/<you>/h3pipe.git
mklink /J C:\path\to\ComfyUI\custom_nodes\ComfyUI-H3-Shotlist C:\path\to\h3pipe\comfy_nodes
```

(`ln -s` on Linux/macOS; if you copy the folder instead, set `H3PIPE_HOME` to the repo for
ComfyUI.) Restart ComfyUI: the pack brings the loader and save nodes, the editor's routes
(`docs/API.md`) and the editor itself (`comfy_nodes/web/h3pipe-editor.js`).

The pipeline itself needs nothing installed — Python 3.10+, standard library only — plus
`ffmpeg` and `ffprobe` on PATH for `assemble`, the peaks and the audio work. `pip install
faster-whisper numpy` adds `align`, `pip install demucs` the `dub_keep_foley` foley bed.

Run `python h3.py` from the repo (or by its path from anywhere; it finds the scripts beside
it). Each episode lives in its own folder (`Shows\ep05`) holding its script and
`series.json`.

## Models

Each target's `target.json` lists the files it needs (`models`: what each file is and its
tier, required / accelerator / optional) and a download link for each one it has a
trustworthy record of (`downloads`). **[INSTALL.md](INSTALL.md#4-models) has the whole list**,
grouped by model family with folders and links, generated from those records by
`python tools/make_models_md.py`. `python h3.py targets` checks them against your ComfyUI.
See also **Which model? Readiness and downloads** in [docs/AUTHORING.md](docs/AUTHORING.md).

The default target, H3 Ref2VA, from
[Comfy-Org/MiniMax-H3](https://huggingface.co/Comfy-Org/MiniMax-H3) with the turbo LoRAs
from [lightx2v/Minimax-h3-Turbo](https://huggingface.co/lightx2v/Minimax-h3-Turbo):

| Role | File used here |
|---|---|
| H3 Ref2VA unet | `minimax_h3_ref2va_pruned_int8_convrot.safetensors` |
| Turbo LoRA (final, 8 steps) | `minimax_h3_ref2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors` |
| Turbo LoRA (proxy, 4 steps) | `minimax_h3_ref2v_lightx2v_turbo_4step_v0.1_resized_avg_rank_20_bf16.safetensors` |
| Text encoder | `qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors` |
| Video / audio VAE | `minimax_h3_video_vae_fp16` / `minimax_h3_audio_vae_fp32` |

Swap any of them per pass or per shot; see **Steps, model and LoRA**.

## The editor

Open ComfyUI after installing the node pack:

- **h3 Shots** (sidebar): pick the project folders (roots) and the episode, the pass
  (final / proxy) and the episode's model; build; every shot with its takes, status and
  stale marks; render, redo, cancel, pick the take the cut uses, discard a take to
  `renders[_proxy]/_trash/`.
- **h3 Refs** (sidebar): every character view, prop, plate, voice and shot keyframe the
  series config and script call for, with candidates (takes) to generate, import, compare,
  discard and pick, and each ref's prompt override and image target.
- **h3 Timeline** (bottom panel): the cut as an editable track — drag to reorder, drag the
  clip edges to trim frame by frame, lock a clip, undo/redo, copy the order or the trims
  from the other pass, **Play all** (watch the cut straight from its takes, no ffmpeg) and
  **Export** (assemble the mp4).
- **Script** and **Series config** (floating): the two authored files, edited in place with
  live checking, Ctrl-S to save and rebuild, and a copy of the old version kept in
  `_history/`. The Script window follows the selected shot both ways.
- **Viewer** (floating): a shot's takes with A/B compare (side by side or wipe), Play all,
  and a ref's candidates.
- **Inspector** (floating): the selected shot: target, overrides (prompt, seed, model,
  LoRAs, steps, per pass), refs used, missing refs.
- **Recording** (floating): attach the episode's dialogue recording — browse the ComfyUI
  machine, type a path, or drop a file in — and run `h3align` against it, with what to
  install if it can't run, live progress, and the report.
- **Audio from…** (floating): a clip's picture with another clip's sound, a file from the
  episode, or silence, with both waveforms drawn and the offset draggable.
- **Promote** (dialog): which of the episode's overrides can move into the script or the
  series config, the diffs of both, and Confirm to write them and rebuild.
- **What's missing** (floating): which model files a target lacks, with folders and links.

**[docs/EDITOR.md](docs/EDITOR.md) is the guide to driving all of it**: the order to do
things in, what every button does, what click, double-click and right-click mean in each
panel, and the keyboard shortcuts.

The project roots are kept in ComfyUI's user folder (`user/default/h3pipe/config.json`),
else read from `H3PIPE_ROOTS`. Everything the editor does is a file in the episode folder
(`overrides.json`, `cut.json`, take sidecars, `_history/`), and the commands below do the
same.

## Quick start (command line)

First time on this machine? [INSTALL.md](INSTALL.md) walks the same path from a clean
ComfyUI, with an episode you can paste in.

```
python h3.py build    Shows\ep05             # story IR, shotlists, reference work orders
python h3.py check    Shows\ep05             # validate and check dialogue pacing (writes nothing)
python h3.py targets  Shows\ep05             # which targets this ComfyUI can render
python h3.py refs     Shows\ep05             # character views, props, plates
python h3.py keyframe Shows\ep05 --missing   # keyframes the shots' targets need
python h3.py render   Shows\ep05 --proxy     # low-res animatic
python h3.py assemble Shows\ep05 --proxy     # join it into one mp4
python h3.py render   Shows\ep05             # full-res
python h3.py assemble Shows\ep05
```

Several episodes at once: list the folders (`Shows\ep06 Shows\ep07`) or use `--each` with a
parent folder (`python h3.py build Shows --each`). Every flag after the episode goes
straight through to the underlying script, and every script has `--help` with the full
list (`python h3render.py --help`, and so on). `python h3.py all <episode> --proxy` runs
build, refs, render and assemble in one go; add `--skip-build` to leave the shotlists on
disk alone. ComfyUI must be running for `targets`, `refs`, `keyframe --generate` /
`--missing` and `render`.

| Command | What it does |
|---|---|
| `h3.py build` / `check` | `h3build.py`: script + series config → story IR → each target's shotlist, `refs_todo.md/.json`; `check` runs `--check` and `--pace` |
| `h3.py refs` | `kreagen.py`: generates missing reference images as takes and picks them |
| `h3.py keyframe` | a shot's first/last keyframe: from the previous shot's take (continuity), `--generate`, `--missing`, `--clear` |
| `h3.py render` | `h3render.py`: queues shots through their targets, one take each |
| `h3.py assemble` | `h3assemble.py`: the review cut, in `cut.json` order |
| `h3.py align` | `h3align.py`: times the script against a dialogue recording |
| `h3.py takes` / `pick` / `override` | takes and why they're stale; the take the cut uses; per-shot tweaks and retargeting (`h3edit.py`) |
| `h3.py cut` | edit the cut itself: `--show`, `--order`, `--move … --before`, `--trim SH IN OUT`, `--lock`/`--unlock`, `--reset`, `--copy-from final\|proxy`, `--audio` (a clip's sound from another take, a file, none or its own) |
| `h3.py discard` | move a take to `renders[_proxy]/_trash/<shot>/`; a cut pick of it goes back to `latest` |
| `h3.py promote` | `h3promote.py`: which overrides can move into the script / series config, with the diffs; `--all` or `--item` moves them, drops those overrides and rebuilds |
| `h3.py targets` | readiness and downloads per target |

## The scripts

| Script | What it does | Reads | Writes |
|---|---|---|---|
| `h3.py` | One command for every step; passes any extra flags through | an episode folder | — |
| `h3build.py` | Parses the script into the story IR, picks each shot's target and compiles it: prompts, frame counts, seeds, reference slots, audio settings. Checks pacing | `series.json`, `epNN.md` | `shotlist/shots.json`, `shotlist.json`, `shotlist.<target>.json`, `_proxy` twins, `refs_todo.md/.json` |
| `kreagen.py` | Generates every missing reference image through the image target and saves it where the series config names | `refs_todo.json`, `series.json` | `refs/…` (takes in `refs/_takes/`) |
| `mksheet.py` | Joins four character views into one 4096×1024 sheet (kreagen calls it) | 4 images | `refs/<char>/<char>_sheet_4panel.png` |
| `h3render.py` | Queues each shot on ComfyUI through its target's workflow, waits, skips finished shots | `shotlist*.json`, `overrides.json`, the workflows | `renders/` or `renders_proxy/` |
| `h3edit.py` | `takes`, `pick`, `override`, `keyframe`, `discard`, `cut`, `targets` (through `h3.py`) | the episode | `cut.json`, `overrides.json`, `refs/shots/` |
| `h3promote.py` | Moves an override into the script or the series config where it belongs, and rebuilds | `overrides.json`, `epNN.md`, `series.json` | updated script and series config, `_history/` |
| `h3align.py` | Times the script against a dialogue recording and writes the `audio:` windows | recording, `epNN.md`, `series.json` | updated script and series config, `align_report.md` |
| `h3assemble.py` | Joins the rendered shots in cut order (`cut.json`, else script order), trimming timed shots to their windows and using each clip's chosen audio | `shotlist*.json`, `cut.json`, renders | `renders/epNN.mp4`, `epNN_shots.txt` |

`h3core/` (parser, story IR, series config, speech pacing), `h3jobs.py`, `h3takes.py`,
`h3refs.py`, `h3source.py` (the authored files), `h3track.py` (recordings and alignment)
and `h3peaks.py` (waveforms) are the library the commands and the editor's routes share.

### What you write

- **`series.json`** — the series config: style, characters and props (with a `design`
  sentence each), locations (one entry per camera angle, with the light), voices, audio
  mode, and optionally the series target, render profiles, the `refs` image targets and a
  `negative` prompt. A character who changes clothes gets a second entry with `of:` — a
  **wardrobe variant**, its own `design` and its own sheet, everything else inherited. See
  `examples/series_example.json`.
- **`epNN.md`** — the script: `# sq` sequences, `## sh` shots with `who:`, `with:`, `size:`,
  `dur:` or `audio:`, `plate:`, `camera:`, `sound:`, `music:`, optionally `target:`,
  `first:` / `last:`, action prose and `NAME:` lines. See `examples/script_example.md`.
- **`negative.txt`** (optional, beside the script): the episode's negative prompt, for the
  targets that take one.

**[docs/AUTHORING.md](docs/AUTHORING.md) is the full format**: every field, choosing a
target, keyframes, the frame grids, the syllable budget, the camera vocabulary, how to
break a scene into shots, giving two people in one scene an angle each, and the
reference-slot rules. Read it once before writing an episode.

Only three things are actually required of a script: the `= ep01 Title` header, at least one
`# sq01 <location>` naming a location the series config defines, and per `## shNNN` a unique
id, action or dialogue, and a length (`dur:` seconds / `auto` / `model`, or an `audio:`
window). Everything else has a default — a speaker joins the cast from their own `NAME:`
line, `size:` is medium, `plate:` is the sequence's location, and the camera holds still.
The series config must have a `series` block, `style.look`, `subjects` (each with `name`,
and `design` unless they are only ever a voice), and `locations` with a `description` and a
`plate` each. `--check` names whichever is missing.

Drafting with an AI assistant: `python tools/make_prompts.py` packages the guide as a skill,
`h3pipe-episode-script`. `build/skill/h3pipe-episode-script/` is the whole skill (the
guide, a worked screenplay conversion from `docs/SCRIPT_CONVERSION.md`, and a copy of `h3build` to
validate with): copy the folder into `.claude/skills/` for Claude Code, or upload
`build/skill/h3pipe-episode-script.zip` on claude.ai. `prompts/SKILL.md` is the skill's
text alone, and `prompts/h3-script.instructions.md` the same guide for Cursor, Copilot or
any system prompt. Edit the docs, not the copies. Whatever writes the script, `--check`
and `--pace` are what say it is correct.

Change the series config or script and run `build` again rather than editing the generated
files: `steps:`, `model:`, `lora:`, `profile:` and `target:` are script fields, so
experiments survive a rebuild, and `overrides.json` holds the editor's tweaks. If you do
hand-edit a shotlist, `all --skip-build` and the individual stages leave it alone.

### h3build

```
python h3.py build Shows\ep05           # final + proxy
python h3.py check Shows\ep05           # --check and --pace, writes nothing
```

- Snaps every shot to its target's legal lengths (H3: 17k+5 frames at 24fps, so 2.33, 3.04,
  3.75, 4.46, 5.17s…; LTX: 8k+1; Wan: 4k+1).
- `dur: auto` sizes a shot from its dialogue. `--pace` flags lines crammed into too short a shot.
- Seeds come from the episode, sequence and shot IDs, so untouched shots render identically after edits.
- `refs_todo.md` lists every image and voice sample still missing, with the prompt to make it.
- Writes `defaults.steps`, `defaults.model` and `defaults.lora` into each shotlist, and warns
  when a shot overrides them or when the step count does not match the LoRA.

#### Steps, model and LoRA

Each pass has its own sampling setup, and the render stage applies it per shot, in memory —
the workflow file on disk is never rewritten. The table is H3 Ref2VA's; every target has
its own presets in its `target.json`, and a series written for H3 doesn't hand another
target H3's model, LoRA or steps.

| | final | proxy |
|---|---|---|
| steps | 8 | 4 |
| turbo LoRA | `minimax_h3_ref2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors` | `minimax_h3_ref2v_lightx2v_turbo_4step_v0.1_resized_avg_rank_20_bf16.safetensors` |
| model | `minimax_h3_ref2va_pruned_int8_convrot.safetensors` | same |

A distilled LoRA is trained on a fixed set of timesteps, so it does its best work at its own
step count: the 8-step v1.0 was distilled at 768p for 8 steps, and the proxy keeps the 4-step
v0.1 both for speed and because its 544p training is closer to the animatic's size.

Override per pass in the series config (`series` for the final, `proxy` for the animatic):

```json
"series": { "steps": 8, "model": "minimax_h3_ref2va_pruned_bf16.safetensors" },
"proxy":  { "steps": 4, "lora": "…4step_v0.1….safetensors" }
```

Override one shot — or one sequence, by putting the line under the `#` header — in the script:

```
## sh170
model: minimax_h3_ref2va_pruned_bf16.safetensors
lora: none
steps: 30
```

`lora: none` (or `off`) sets the LoRA strength to 0 instead of swapping files, which renders
that shot on the base model — the honest comparison for what the turbo LoRA costs you.
A shot's line beats its sequence's, which beats the pass default. `--check` lists every
override so an experiment left in a script cannot surprise you later.

Names must match ComfyUI's dropdowns exactly, subfolder prefix included. A wrong name fails
the first shot instead of quietly rendering with something else. ComfyUI reloads whenever the
model or LoRA changes, so group experiments rather than scattering them through an episode.

### kreagen

```
python h3.py refs Shows\ep05 --list        # what would run
python h3.py refs Shows\ep05 --only dean   # one asset first
python h3.py refs Shows\ep05               # everything missing, most-needed first
python h3.py refs Shows\ep05 --voices      # also the voice samples, on the audio target
```

- Skips anything already on disk. `--redo` makes a new take (kept in `refs/_takes/`) and
  leaves the live file alone unless you add `--pick`; combine it with `--only`. `--all`
  works from the whole series config instead of `refs_todo.json`.
- `--only` matches part of the file path, so `dean` also matches `dean_grown` — which is
  also how a character and their wardrobe variants regenerate together.
- **Wardrobe variants come second.** A variant's views are edited from the character's own
  (see **Reference sheets**), and a whole pass plans every job before any take is picked —
  so in the first pass the variant has nothing to edit from and is drawn cold. Run the pass,
  let the character's views pick (anything with no live file takes its first usable
  candidate automatically), then regenerate just the variant on a model that reads
  references: `--only gina_towel --target flux2_klein_edit --redo`. Set that target once on
  the ref instead — in the editor's per-ref target picker — and every later generate of it
  uses the edit model while everything else stays on krea2. The new take does **not** go
  live on its own once the variant already has a file: pick it (`--pick`, or in the Refs
  tab).
- **Voices.** Images only, unless `--voices`: then each character's voice ref is generated
  on the audio target (`--voice-target`, `--voice-seconds`; default `ltx2_voice`) into
  `refs/voices/<char>.wav`, as takes like any other ref. `--from-take sh020:2:1.5-6.0`
  needs no model at all — it cuts those seconds out of a rendered take's sound into a
  voice candidate.
- `--discard subject:ada:02_side:2` moves a candidate to `refs/_takes/_trash/` (nothing is
  deleted; if it was the pick, the ref is cleared).
- Each character is made as four square views sharing a seed, then joined by mksheet.
  Every view is a take in `refs/_takes/subject__<char>/`, so one angle can be redone and
  re-picked in the editor's Refs tab (h3refs.py).
- Negative prompts do nothing on krea2 turbo: no negative field, and no guidance branch at
  cfg 1. kreagen leaves a workflow's negative side as saved, so a NAG or negpip setup passes
  through; `--negative-file` only bites above `--cfg 1.0`, on a model that expects guidance.
  H3 takes no negative prompt at all. Details in `KREA_PIPE.md`. The other image targets
  take the episode's `negative.txt` or the series config's `negative` (inert at cfg 1 too);
  `--negative-file` beats both for its run.
- `--target z_image_turbo` (or any image target) draws with another model; `--clear
  location:kitchen` unpicks a ref (its file goes, the takes stay).
- No style LoRA by default. `--lora <file> --lora-strength 0.7` adds one, `--unet` swaps the
  image model. Match the LoRA to the look in `series.json`: a realism LoRA helps live action
  and hurts a flat 2D show.
- **With krea2, drives your own ComfyUI workflow when it finds one.** Save a text-to-image graph as
  `krea2_refs_t2i.json` among ComfyUI's workflows (or set `$KREA_WORKFLOW`, or pass `--workflow`), and
  kreagen sets the prompt, size, seed, steps and cfg on it per image instead of using its
  built-in graph. That is the no-Python way to change the model, LoRA, sampler or scheduler
  for reference art. `--no-workflow` forces the built-in graph.

  The graph needs one KSampler, one EmptyLatentImage and one SaveImage; positive and negative
  are found by following the sampler's own links. At cfg 1.0 the negative encoder is replaced
  with `ConditioningZeroOut`, since guidance is off and encoding it would be wasted; above 1.0
  with `--negative-file`, a graph wired straight to `ConditioningZeroOut` gets a text encoder
  put back. `targets/image/krea2/workflow.json` is a working example with no LoRA — point its
  loaders at your own models. `--lora` splices a LoRA node into a graph that has none, or
  sets the one that is already there. If a graph carries a step-distilled LoRA, pass the
  matching `--steps`.

### h3render

```
python h3.py render Shows\ep05 --list                  # what would run
python h3.py render Shows\ep05 --proxy                 # animatic, to renders_proxy\
python h3.py render Shows\ep05 --only sh040,sh050 --redo
python h3.py render Shows --each --proxy               # every episode
```

- Runs each shot through its target's workflow: for H3, the same graph as the canvas, with
  Shot List Loader and Save Shot pointed at the take; for the others, every value patched
  into the target's graph. One queue per take.
- Must run on the ComfyUI PC. It checks the render folders to skip finished shots, so Ctrl-C
  and re-running resumes where it stopped.
- Every take gets a sidecar, `<shot>_tNN.json`, holding its seed, model, LoRAs, steps, the refs
  it used (with checksums) and its status (`queued`, `ok`, `failed`). It also gets
  `<shot>_tNN.shotlist.json`, the exact shot it rendered. Nothing about a take is lost when the
  script changes.
- `--redo` renders a new take (t02, t03…) and never overwrites. `--take N` overwrites that take.
- **Seeds:** a shot's first take uses its built seed. `--redo` picks a **new** seed, so a redo
  is really a new attempt. `--same-seed` keeps the built one, `--seed N` uses N, and
  `--new-seed` forces a fresh one.
- Takes the model, LoRA and step count from each shot, falling back to the shotlist defaults,
  then applies the episode's `overrides.json` (see `h3.py override` below). `--model`, `--lora`
  and `--steps` override all of that for one run. `--lora` repeats to stack LoRAs
  (`--lora a.safetensors --lora b.safetensors:0.6`); the extras are chained after the
  workflow's LoRA loader. `--note` stores a remark in each take's sidecar.
  It prints what it will use, and warns when one episode needs more than one model or LoRA.
- **Targets.** Each shot renders with its own target's workflow: H3 shots through Shot List
  Loader, `ltx2` shots with every value patched into the LTX-2.5 graph. `--target ltx2`
  renders the chosen shots on another target for this run (their story is compiled for it
  on the spot, no rebuild); `h3.py override <ep> <shot> --target ltx2` does it for every
  run. An LTX shot's keyframes (`refs/shots/<shot>/first.png`, `last.png`) are uploaded into
  ComfyUI's `input/h3pipe/` first. `--dry-run --check-nodes` also asks the running ComfyUI
  (`/object_info`) whether it knows every node and input of the graph.
- Failed shots are reported and skipped; `--stop-on-error` halts instead. A shot whose
  reference images are still missing is **blocked**, and the report names the exact files;
  `--allow-missing-refs` renders it against flat grey stand-ins.
- Other flags: `--panel-mode`, `--save-frames` / `--no-frames`, `--no-review-copy`, `--comfy URL`,
  `--workflow`, `--allow-model-mismatch`, `--dry-run` (writes the API job to `h3render_graph.json`).
- Each target's workflow comes from the running ComfyUI's saved workflows (the name in its
  `target.json` binding: `H3_Ref2VA_Shotlist_v1.json` for H3, `krea2_refs_t2i.json` for
  reference images), so it always matches your ComfyUI's node versions; failing that,
  `$COMFYUI_PATH`'s workflows folder, then the copy in this repo. `--workflow` and the
  target's own env var (`$H3_WORKFLOW`, `$KREA_WORKFLOW`, …) beat all of those. Every
  target's name and variable is in [INSTALL.md](INSTALL.md#3-workflows). Keep the saved
  `krea2_refs_t2i.json` free of style LoRAs (experiment under another name), since the
  references must follow the series config's look.
- It converts the canvas workflow to API format itself. If ComfyUI rejects it, save
  **Workflow → Export (API)** and pass that file with `--workflow`.

### h3assemble

```
python h3.py assemble Shows\ep05 --proxy --check   # report only
python h3.py assemble Shows\ep05 --partial         # join what exists so far
```

- Follows the episode's `cut.json` if there is one (a list per pass of
  `{"shot", "take", "pass", "trim_in", "trim_out", "audio"}` entries). The list sets the order and,
  with `take: N`, the exact take. Without `cut.json`, or for a shot the list leaves out,
  a shot goes in script order and uses its latest usable take: status `ok` and the mp4 is
  there, so a `--redo` still queued or failed is skipped. Takes from before sidecars count
  as ok.
- A picked take that is queued, failed or has no mp4 makes the shot missing, and the report
  says why. `--take N` forces take N for every shot and beats `cut.json`.
- Entries for shots deleted from the script are skipped with a note. An entry with
  `"pass": "proxy"` in the final list is a placeholder: the proxy take is scaled to the final
  size. `trim_in`/`trim_out` drop frames from the head and tail.
- The pass comes from the shotlist's name (`_proxy` means proxy); `--pass` overrides it.
- `--check` lists the resolved cut (order, take, pass, trims, placeholders, orphans) and
  writes nothing. `--name` sets the output filename, `--shotlist` picks the proxy files,
  `--subfolder` reads this pass's takes from another folder.
- Mute clips (dub, clone) get their `_h3.wav` added so the join works.
  `--audio auto|mp4|h3|none|master` chooses the sound for the clips that have none of
  their own. A clip **can** have its own: `cut.json`'s `audio` lays another take's sound,
  a file's, or silence under it, cut or padded to the clip so the length never changes
  (the Timeline's **Audio from…**, or `h3.py cut --audio`). `--audio master` and `none`
  override those; the others leave them alone.
- Shots with an `audio:` window are trimmed to it (re-encoded, x264 CRF 16) so the cut lines up
  with the recording. `--no-trim` keeps the padding.
- `epNN_shots.txt` lists every shot's start time in the cut.
- This is a review cut. Mix the final audio in Resolve.

### Takes, picks and overrides

```
python h3.py takes    Shows\ep05 --proxy          # every take: status, seed, why stale, which is in the cut
python h3.py pick     Shows\ep05 sh020 1          # the cut uses take 1 of sh020 (writes cut.json)
python h3.py pick     Shows\ep05 sh020 latest     # back to the newest usable take
python h3.py override Shows\ep05 sh020 --dump-prompt > sh020.txt     # the prompt it would use
python h3.py override Shows\ep05 sh020 --prompt-file sh020.txt --seed 1234
python h3.py override Shows\ep05 sh020 --lora a.safetensors --lora b.safetensors:0.6 --steps 10
python h3.py override Shows\ep05 sh020 --clear prompt
python h3.py render   Shows\ep05 --only sh020 --redo                 # renders with the override
python h3.py discard  Shows\ep05 sh020 2          # take 2 goes to renders\_trash\sh020\
```

- `takes` marks a take **stale** when rendering the shot now would differ: `script` (the
  shot changed in the script or series config), `ref` (a reference image or voice changed) or
  `preset` (model, LoRA, steps or size defaults changed). Stale is a hint, not an error.
- `overrides.json` is how you tweak one shot without touching the script: the exact prompt,
  a seed, model, LoRAs, steps. Prompt, model, LoRAs and steps are per pass (final unless
  `--proxy`; `--both` sets both), because the proxy's prompt differs when it generates its
  audio. The seed applies to both passes. A rebuild never loses an override. If the shot has
  changed since you wrote it, `takes` and `override` flag it STALE, and it is still applied.
- Model, LoRAs and steps belong in the script when they're decisions about the film.
  `overrides.json` is for the tweak loop. When a tweak turns out to be a decision,
  `h3.py promote` moves it into the script or the series config for you — it prints the
  plan and the diffs first, and `--all` (or `--item shot:sh020:steps`) writes them, drops
  those overrides and rebuilds. The old files are kept in `<episode>\_history\`.

### Editing the cut

```
python h3.py cut Shows\ep05 --proxy --show                     # the cut: order, takes, trims, audio
python h3.py cut Shows\ep05 --proxy --move sh050 --before sh020
python h3.py cut Shows\ep05 --proxy --trim sh020 4 0           # drop 4 frames off the head
python h3.py cut Shows\ep05 --proxy --lock sh020               # reordering leaves it alone
python h3.py cut Shows\ep05 --copy-from proxy order            # take the proxy's order into the final
python h3.py cut Shows\ep05 --proxy --audio sh020 take sh030:2 # sh020's picture, sh030 take 2's sound
python h3.py cut Shows\ep05 --proxy --audio sh020 file audio\line_b.wav --at 0.4 --gain 1.2
```

`cut.json` holds it all — order, the take each shot uses, trims, locks and per-clip audio —
per pass, and `h3assemble` follows it. The editor's Timeline writes the same file.

## Recorded dialogue (lip sync)

Full guide: `RECORDED_DIALOGUE.md`.

```
pip install faster-whisper numpy                    # once; the model downloads on first run
python h3.py align    Shows\ep05 audio\ep05_dialogue.wav --dry-run
python h3.py align    Shows\ep05 audio\ep05_dialogue.wav
python h3.py build    Shows\ep05
python h3.py render   Shows\ep05 --proxy
python h3.py assemble Shows\ep05 --proxy --audio master          # picture over the recording
```

- The editor does the same from its **Recording** window: attach the file (browse the
  ComfyUI machine, type a path, or drop it in), then align, with progress and the report.
  It runs h3align in whichever Python has `faster-whisper` and `numpy` — see
  [INSTALL.md](INSTALL.md#1-requirements) if it picks the wrong one.
- h3align transcribes the recording with word timings, matches it to the script, and cuts it
  into one continuous run of windows with cuts at the quietest point of each pause.
- It writes `audio: in-out` on every shot, points the series config at the recording, keeps the
  old copy of each in `<episode>\_history\` (the editor's history), and writes `align_report.md`.
- Speaking shots then default to `dub_keep_foley`: H3 lip-syncs to your line and keeps its own
  sound effects as `_foley.wav`.
- The `retention` setting controls how much of your line H3 copies:

| `retention` | What H3 does with the line | Default for |
|---|---|---|
| `fully_copy` | Your clip is the shot's whole soundtrack; no sound effects added | `dub` |
| `partially_copy` | Copies the line exactly and adds ambience and action sounds | `dub_keep_foley` |
| `reference` | Borrows voice and timing only; loose lip sync | — |

Set it per shot (`retention: fully_copy`) or for the episode (`"audio": {"retention": ...}` in
the series config). `clone` and `generate` shots don't use it.

## Project layout

```
<episode>/
  series.json                  series config (you write)
  epNN.md                      script (you write)
  negative.txt                 negative prompt for targets that take one (optional)
  shotlist/shots.json          generated: the story IR (model-free)
  shotlist/shotlist.json       generated: the series target's shots
  shotlist/shotlist.<target>.json   generated: shots on another target
  shotlist/*_proxy.json        generated: the same for the proxy pass
  refs_todo.md / .json         generated work order
  overrides.json               editor / `h3.py override`: per-shot tweaks, retargets, episode target
  cut.json                     editor / `h3.py cut` / `pick`: per pass, the order, the take
                               each shot uses, trims, locks and each clip's audio source
  align_report.md              generated: what h3align matched (h3.py align)
  _history/                    old copies of the script, series config and cut.json,
                               kept by the editor, h3promote and h3align
  _cache/peaks/                generated: cached waveforms for the timeline
  refs/<char>/<char>_sheet_4panel.png   horizontal 4-panel strip
  refs/props/<name>.png                 single clean object image
  refs/_bg/<location>.png               background plate -> <Picture 4>
  refs/voices/<char>.wav                a generated voice sample (audio target)
  refs/_takes/<ref>/…_tNN.png/.json     every ref candidate (h3refs)
  refs/_takes/_trash/<ref>/             discarded ref candidates
  refs/_picks.json, _overrides.json     which take is live; ref prompt/seed/target tweaks
  refs/shots/<shot>/first.png, last.png a shot's keyframes
  audio/voices/<char>_sample.wav        clone mode
  audio/<episode>_dialogue.wav          recorded dialogue (h3align)
  renders/<shot_id>/
    <shot_id>_t01.mp4
    <shot_id>_t01_h3.wav
    <shot_id>_t01_foley.wav      (dub_keep_foley only)
    <shot_id>_t01.jpg            mid-frame thumbnail (480 px long side)
    <shot_id>_t01_strip.jpg      8 frames side by side, for hover scrub
    <shot_id>_t01.json           take sidecar: written queued, closed by Save Shot
    <shot_id>_t01.shotlist.json  the exact shot that was rendered
    frames/<shot_id>_t01_%06d.png
  renders/_trash/<shot_id>/      discarded takes (`h3.py discard`; nothing is deleted)
  renders_proxy/...            same layout for the animatic
```

---

# Reference: the H3 Ref2VA target

How the default target works. The other targets are described in
[docs/AUTHORING.md](docs/AUTHORING.md) and their own `targets/video/<id>/` code.

## The Ref2VA ComfyUI workflow (`H3_Ref2VA_Shotlist_v1.json`)

h3render drives this graph for you. To run it by hand on the canvas:

1. Point **Shot List Loader** and **Save Shot** at the same `project_root`.
2. Set **Shot Index** `control_after_generate` to `increment`.
3. Set batch count to the shot count Shot Info reports. Queue once.

Re-render one shot: set the index, switch to `fixed`, bump **take**, queue.

### The nodes

Installed with the node pack (see **Install**); load
`targets/video/minimax_h3_ref2va/workflow.json` to see the graph. When
`comfy_nodes/h3_shotlist.py` changes, restart ComfyUI (copy it over again first if you
copied the folder rather than linking it).

No audio dependencies. The nodes read and write PCM wav with the Python
standard library and fall back to ffmpeg for mp3/m4a/flac. In particular they
do **not** call `torchaudio.save`, which since torchaudio 2.13 routes through
torchcodec and raises `ImportError` when it is missing — you do not need to
install torchcodec.

## Reference slots

Slots 1–3 carry the shot's **subjects** — characters first, then props and
vehicles, in the order the script lists them. Slot 4 always carries the
**location plate**.

There is no style reference image. The style is already carried by every sheet
and plate (all drawn in it) and stated in the prompt text, so a dedicated style
slot buys redundant conditioning. The background buys concrete layout, palette,
lighting direction and depth for *this* shot — and keeps one location looking
like one place across every cut in a sequence.

The background is written into the prompt as a proper `<Subject N>` sourced
from `<Picture 4>`, because H3's spec lists scenes and environments as Subject
content; a bare `<Picture N>` means "use this exact frame as a keyframe", which
is not what a location plate is for. Its retention marker is
`partially_preserved` — layout and light are kept, framing is not.

## Reference sheets

Author each character as a **horizontal 4-panel strip** — 3/4 body, side, back,
face close-up — at 4096×1024 or larger. `ref_image_size: max` scales the *short*
side to 2048, so a horizontal strip keeps every panel sharp where a square grid
would halve it.

Each shot uses only **one panel** of each sheet (`panel_mode: auto`): the face
close-up for a single-character close shot, the three-quarter body everywhere
else. A reference image with more than one figure in it gets drawn as more than
one person, so multi-panel strips are no longer fed by default. `full`, `pair`,
`face` and `body` force a crop (`h3render --panel-mode`). The socket-to-prompt
mapping is fixed, so prompts are stable regardless of cast size:

| Socket | 1 subject | 2 subjects | 3 subjects |
|---|---|---|---|
| `<Picture 1>` | subject 1 | subject 1 | subject 1 |
| `<Picture 2>` | background | subject 2 | subject 2 |
| `<Picture 3>` | background | background | subject 3 |
| `<Picture 4>` | background | background | background |

**A change of clothes is a second subject**, not a note in the prompt. Give it `of:` in the
series config and its own `design`; it inherits the character's name, voice and pronoun, and
its sheet path is derived from theirs (`refs/gina/gina_sheet_4panel.png` →
`refs/gina/gina_towel_sheet_4panel.png`). The script names the variant on `who:` and goes on
calling the character by name in the dialogue. The point is that the retention marker stays
`fully_preserved` against a sheet that is actually wearing the towel: H3 preserves a subject
whole — face, hair, proportions *and* wardrobe — so the only way to contradict the sheet is
to weaken the whole subject, which loosens the face in order to change the clothes.

A variant's views are generated **from the character's own**: the towel's back panel is an
edit of their back panel, on any image target that reads references (`flux2_klein_edit`,
`flux_kontext`, `minimax_h3_still`). On a text-to-image target there is nothing to edit
from, so it draws the variant from its `design` on the character's seed — closer than a
fresh draw, but not the same thing.

Props and vehicles are single clean images on a flat background and are never
panel-cropped; only character sheets get `panel_mode`. Unused
subject sockets take the background plate — it is smaller than a character
strip and reinforces the location rather than biasing identity toward whichever
subject got duplicated.

## Voices without bodies

`NAME (V.O.):` and `NAME (O.S.):` mark a line as spoken by someone who is not
on screen. Such a speaker is **not** added to the visible cast: they cost no
reference slot, are never drawn, and need no character sheet — only a `voice`
description in the series config (and a `voice_sample` if the shot uses `clone`).

Only `who:` puts a character on screen. Before this existed, any speaking name
was auto-added to the cast, so a phone voice from another house burned a slot
and got rendered into frame.

| Form | Meaning | Slot? | Prompt |
|---|---|---|---|
| `RILEY: line` | on screen, speaking | yes | `<Subject N> (Sx) says,` |
| `RILEY (V.O.): line` | voiceover / narration / phone | only if also in `who:` | `says in an off-screen voiceover` |
| `RILEY (O.S.): line` | in the space, outside frame | only if also in `who:` | `says from off-screen, outside the frame` |

A `(V.O.)` speaker who *is* in `who:` — a character narrating over their own
shot — additionally gets H3's required lips-closed clause, using the series config's
optional `pronoun` field, so H3 doesn't animate their mouth over the narration.
A speaker with no visual reference is identified by their voice description
followed by `(Sx)`, per H3's spec, rather than an invented `<Subject>`.

A shot may have no `who:` at all — an establishing plate with voiceover over
it. The location then fills all four slots and becomes `<Subject 1>`.

## Audio references

`ref_audio_0..2` carry one clip each, and how many are used depends on the
policy:

| Policy | Clips fed | What each one is |
|---|---|---|
| `clone` | **one per speaker** | that character's voice-timbre sample |
| `dub` / `dub_keep_foley` | one | the slice of the recorded mix, which already contains every voice in the shot |
| `generate` | none | — |

The per-speaker rule is the one that bites: a two-hander in `clone` mode needs
two samples, and feeding one voices the second character from the first one's
sample. Unused sockets receive null. H3 takes at most three standalone audio
references, so a `clone` shot is capped at three speakers and the compiler
errors above that rather than silently dropping one.

## Audio policies

Set per shot with `policy:` in the script, else from the series config's `audio.mode`.
H3 emits **one mixed track** — there are no stems — which is what these policies work
around.

| Policy | Reference fed | mp4 audio | Use for |
|---|---|---|---|
| `generate` | none | H3's mix | Non-dialogue shots. H3's synced foley is genuinely good and free. |
| `dub` | your recorded line | **mute** | Dialogue. H3 drives lip-sync; the clean vocal goes in at conform. |
| `dub_keep_foley` | your recorded line | foley bed | Dialogue where you want H3's synced foley under your own vocal. Needs demucs. |
| `clone` | a voice sample | mute | Incidentals and the proxy pass. H3 speaks the `<d>` line in the sampled timbre. |

`generate` deliberately passes **None**, not silence — a silent clip on a
*reference* socket tells H3 to match silence.

A dub shot also has a `retention` setting (`fully_copy`, `partially_copy`, `reference`); see
[Recorded dialogue](#recorded-dialogue-lip-sync).

## Speech pacing

A shot's length has to hold its dialogue. H3 will fit any line into any window
by speeding the delivery up, so an over-packed shot doesn't fail — it just
comes back sounding like a chipmunk reading a contract.

```bash
python3 h3build.py series.json ep01.md --pace     # every dialogue shot, measured
```

The model counts syllables, adds a beat at each speaker change plus air at the
head and tail, and reports the rate the shot forces on the performance.
Conversational English is ~4.0 syllables/second; brisk cartoon delivery ~4.4;
deliberate patter ~5.4. Past 5.8 it stops reading as a person talking fast.

| Field | Meaning |
|---|---|
| `pace: slow\|normal\|fast` | per shot, or `speech.pace` in the series config. 3.6 / 4.4 / 5.4 syl/s. |
| `dur: auto` | derive the length from the dialogue at that pace, then snap to the grid. |

`--check` and a normal compile emit the same findings as warnings, each with
the `dur:` that would fit and how many syllables the window actually holds.
Three ways out of a crammed shot: lengthen it, cut words, or split it in two —
and splitting is often the right answer, because a line that needs 6 seconds
usually wanted a reaction cut in the middle of it anyway.

## Resolution

`1344×768` is H3's native canvas and both axes are multiples of 32.
**1280×720 is not legal** — 720 is not divisible by 32, and the loader raises
rather than letting H3 fail deep in the graph. Crop to 1344×756 and upscale at
conform for true 16:9.

## What you get out

`VAEDecode` emits the full frame batch as IMAGE *before* anything muxes it, so
`save_frames` gives you a PNG sequence, and H3's audio is always written
separately as `_h3.wav`. **There is no alpha channel** — compositing here means
grading, effects and titles, not lifting a character off a background. Budget
16–50 GB of PNG per 12-minute episode at 1344×768.

The mp4 is pinned with `-frames:v` and never uses `-shortest`: H3's audio often
runs a few milliseconds short of `length/fps`, and `-shortest` silently drops
the final frame, which breaks conform.

Save Shot also writes two JPEGs straight from the frames in memory: `_tNN.jpg`
(the middle frame) and `_tNN_strip.jpg` (8 evenly spaced frames, for hover
scrub). Its optional `sidecar` input takes the path of the take's `_tNN.json`
(absolute, or relative to `project_root`), which the queuer writes as `queued`;
the node closes it with `status` (`ok` or `failed`), `finished`, `frames`, the
names of the files it wrote and its status line, and leaves every other field
alone. A failure in the thumbnails or the sidecar is reported in the status
line, never raised, so it can't cost you the mp4.

---

## License

MIT — see [LICENSE](LICENSE).

The models the targets load (MiniMax H3, LTX, Wan, the turbo LoRAs, the image models) come
with their own licenses; this repository only contains the tooling around them.
