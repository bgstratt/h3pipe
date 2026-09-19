# h3pipe — script to finished episode with MiniMax H3

Write an episode as a screenplay-flavoured markdown file plus a series config (`series.json`), and these
scripts compile it into a shot list, generate every reference image it needs, render each
shot on a local ComfyUI, and cut the results together.

Built for MiniMax H3 Ref2VA on ComfyUI. One shot per queue: nothing chains, so a bad shot
is re-rendered alone and the rest of the episode is untouched.

Run the commands from the folder that holds your projects, with these scripts beside it.
Each episode lives in its own folder, for example `Shows\ep05`.

## Requirements

- Python 3.10+ (standard library only for the pipeline scripts; the ComfyUI node uses torch,
  which ComfyUI already provides)
- A local [ComfyUI](https://github.com/comfyanonymous/ComfyUI) with the MiniMax H3 Ref2VA
  model, its CLIP and both VAEs, and a turbo LoRA. See **Models**
- `ffmpeg` and `ffprobe` on PATH for `h3assemble`
- Optional: `pip install demucs` for the `dub_keep_foley` audio policy

## Install

```
git clone https://github.com/<you>/h3pipe.git
cd h3pipe
```

Copy `comfy_nodes/` into `ComfyUI/custom_nodes/ComfyUI-H3-Shotlist/` and restart ComfyUI,
then load `targets/video/minimax_h3_ref2va/workflow.json` (save it in ComfyUI as `H3_Ref2VA_Shotlist_v1.json`). `h3render`
finds that workflow in ComfyUI's saved workflows, else in the repo; set `H3_WORKFLOW` to
override, or pass `--workflow`.

`kreagen.py` generates reference images with a Krea2 image stack; the model file names are
constants at the top of that file, and you will want to point them at whatever image model
you have. Everything else in the pipeline is model-agnostic.

## Models

| Role | File used here |
|---|---|
| H3 Ref2VA unet | `minimax_h3_ref2va_pruned_int8_convrot.safetensors` |
| Turbo LoRA (final, 8 steps) | `minimax_h3_ref2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors` |
| Turbo LoRA (proxy, 4 steps) | `minimax_h3_ref2v_lightx2v_turbo_4step_v0.1_resized_avg_rank_20_bf16.safetensors` |
| Text encoder | `qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors` |
| Video / audio VAE | `minimax_h3_video_vae_fp16` / `minimax_h3_audio_vae_fp32` |

Models come from [Comfy-Org/MiniMax-H3](https://huggingface.co/Comfy-Org/MiniMax-H3) and the
turbo LoRAs from [lightx2v/Minimax-h3-Turbo](https://huggingface.co/lightx2v/Minimax-h3-Turbo).
Swap any of them per pass or per shot — see **Steps, model and LoRA**.

```
  you write                  generated                      rendered
  ─────────                  ─────────                      ────────
  series.json  ─┐
  epNN.md      ─┼─ h3build ─▶ shotlist/shotlist.json ─ h3render ─▶ renders/shNNN/*.mp4 ─ h3assemble ─▶ epNN.mp4
  (recording) ──┘  (h3align)  refs_todo.json ────────── kreagen ──▶ refs/  (images H3 needs)
```

## Quick start

```
cd <your projects folder>

python h3.py build    Shows\ep05             # shotlists + reference work orders
python h3.py check    Shows\ep05             # validate and check dialogue pacing
python h3.py refs     Shows\ep05             # generate character sheets, props, plates
python h3.py render   Shows\ep05 --proxy     # low-res animatic
python h3.py assemble Shows\ep05 --proxy     # join it into one mp4
python h3.py render   Shows\ep05             # full-res
python h3.py assemble Shows\ep05
```

Several episodes at once: list the folders (`Shows\ep06 Shows\ep07`) or use
`--each` with a parent folder (`python h3.py build Shows --each`).
Every flag after the episode goes straight through to the underlying script, and every
script has `--help` with the full list — `python h3render.py --help`, and so on.
`python h3.py all <episode> --proxy` runs build, refs, render and assemble in one go;
add `--skip-build` to leave the shotlists on disk alone (see **Steps, model and LoRA**).
ComfyUI must be running for `refs` and `render`.

## The scripts

| Script | What it does | Reads | Writes |
|---|---|---|---|
| `h3.py` | One command for every step below; passes any extra flags through | an episode folder | — |
| `h3build.py` | Compiles the series config and script into shots: H3 prompts, frame counts, seeds, reference slots, audio settings. Checks pacing | `series.json`, `epNN.md` | `shotlist/shotlist.json`, `shotlist_proxy.json`, `refs_todo.md/.json` |
| `kreagen.py` | Generates every missing reference image with krea2 on ComfyUI and saves it where h3build expects it | `refs_todo.json`, `series.json` | `refs/…` (takes in `refs/_takes/`) |
| `mksheet.py` | Joins four character views into one 4096×1024 sheet (kreagen calls it) | 4 images | `refs/<char>/<char>_sheet_4panel.png` |
| `h3render.py` | Queues each shot on ComfyUI through `H3_Ref2VA_Shotlist_v1.json`, waits, skips finished shots | `shotlist*.json`, the workflow | `renders/` or `renders_proxy/` |
| `h3align.py` | Times the script against a dialogue recording and writes the `audio:` windows | recording, `epNN.md`, `series.json` | updated script and series config, `align_report.md` |
| `h3assemble.py` | Joins the rendered shots in cut order (`cut.json`, else script order), trimming timed shots to their windows | `shotlist*.json`, renders | `renders/epNN.mp4`, `epNN_shots.txt` |
| `h3plan.py` | Legacy: chained-plan compiler for the looping Contex-Loop graph (see the end of this file) | an episode plan JSON (format documented in that file) | `build/` |

### What you write

- **`series.json`** — the series config: style, characters and props (with a `design` sentence each),
  locations (one entry per camera angle, with the light), voices, and audio mode. See
  `examples/series_example.json`.
- **`epNN.md`** — the script: `# sq` sequences, `## sh` shots with `who:`, `with:`, `size:`,
  `dur:` or `audio:`, `plate:`, `camera:`, `sound:`, `music:`, action prose and `NAME:` lines.
  See `examples/script_example.md`.

**[docs/AUTHORING.md](docs/AUTHORING.md) is the full format**: every field, the frame grid, the
syllable budget, the camera vocabulary, how to break a scene into shots, and the reference-slot
rules. Read it once before writing an episode.

Drafting with an AI assistant: `prompts/` holds the same guide packaged as a Claude skill
(`prompts/SKILL.md`, copy into `.claude/skills/h3-episode-script/`) and as plain instructions
for Cursor, Copilot or any system prompt (`prompts/h3-script.instructions.md`). Both are
generated from the authoring guide by `python tools/make_prompts.py`, so edit the guide, not
the copies. Whatever writes the script, `--check` and `--pace` are what say it is correct.

Change the series config or script and run `build` again rather than editing the generated files —
`steps:`, `model:` and `lora:` are script fields now, so experiments survive a rebuild.
If you do hand-edit a shotlist, `all --skip-build` and the individual stages leave it alone.

### h3build

```
python h3.py build Shows\ep05           # final + proxy
python h3.py check Shows\ep05           # --check and --pace, writes nothing
```

- Snaps every shot to H3's legal lengths (17k+5 frames at 24fps: 2.33, 3.04, 3.75, 4.46, 5.17s…).
- `dur: auto` sizes a shot from its dialogue. `--pace` flags lines crammed into too short a shot.
- Seeds come from the episode, sequence and shot IDs, so untouched shots render identically after edits.
- `refs_todo.md` lists every image and voice sample still missing, with the prompt to make it.
- Writes `defaults.steps`, `defaults.model` and `defaults.lora` into each shotlist, and warns
  when a shot overrides them or when the step count does not match the LoRA.

#### Steps, model and LoRA

Each pass has its own sampling setup, and the render stage applies it per shot, in memory —
the workflow file on disk is never rewritten.

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
```

- Skips anything already on disk. `--redo` makes a new take (kept in `refs/_takes/`) and
  leaves the live file alone unless you add `--pick`; combine it with `--only`. `--all`
  works from the whole series config instead of `refs_todo.json`.
- `--only` matches part of the file path, so `dean` also matches `dean_grown`.
- Each character is made as four square views sharing a seed, then joined by mksheet.
  Every view is a take in `refs/_takes/subject__<char>/`, so one angle can be redone and
  re-picked in the editor's Refs tab (h3refs.py).
- Negative prompts do nothing on krea2 turbo: no negative field, and no guidance branch at
  cfg 1. kreagen leaves a workflow's negative side as saved, so a NAG or negpip setup passes
  through; `--negative-file` only bites above `--cfg 1.0`, on a model that expects guidance.
  H3 takes no negative prompt at all. Details in `KREA_PIPE.md`.
- No style LoRA by default. `--lora <file> --lora-strength 0.7` adds one, `--unet` swaps the
  image model. Match the LoRA to the look in `series.json`: a realism LoRA helps live action
  and hurts a flat 2D show.
- **Drives your own ComfyUI workflow when it finds one.** Save a text-to-image graph as
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

- Runs the same graph as the canvas: points Shot List Loader and Save Shot at each take and
  queues it.
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
- Failed shots are reported and skipped; `--stop-on-error` halts instead.
- Other flags: `--panel-mode`, `--save-frames` / `--no-frames`, `--no-review-copy`, `--comfy URL`,
  `--workflow`, `--dry-run` (writes the API job to `h3render_graph.json`).
- The workflow comes from the running ComfyUI's saved workflows
  (`H3_Ref2VA_Shotlist_v1.json`; `kreagen` uses `krea2_refs_t2i.json`), so it always matches
  your ComfyUI's node versions. Failing that, `$COMFYUI_PATH`, then the copy in this repo
  (`targets/video/minimax_h3_ref2va/workflow.json`, `targets/image/krea2/workflow.json`). `--workflow` or `$H3_WORKFLOW` / `$KREA_WORKFLOW` beat all of those. Keep the
  saved `krea2_refs_t2i.json` free of style LoRAs (experiment under another name), since the
  references must follow the series config's look.
- It converts the canvas workflow to API format itself. If ComfyUI rejects it, save
  **Workflow → Export (API)** and pass that file with `--workflow`.

### h3assemble

```
python h3.py assemble Shows\ep05 --proxy --check   # report only
python h3.py assemble Shows\ep05 --partial         # join what exists so far
```

- Follows the episode's `cut.json` if there is one (a list per pass of
  `{"shot", "take", "pass", "trim_in", "trim_out"}` entries). The list sets the order and,
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
- Mute clips (dub, clone) get their `_h3.wav` added so the join works. `--audio h3|none|master`
  chooses the sound.
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
  `overrides.json` is for the tweak loop.

## Recorded dialogue (lip sync)

Full guide: `RECORDED_DIALOGUE.md`.

```
pip install faster-whisper                                             # once; model downloads on first run
python h3.py align    Shows\ep05 audio\ep05_dialogue.wav --dry-run
python h3.py align    Shows\ep05 audio\ep05_dialogue.wav
python h3.py build    Shows\ep05
python h3.py render   Shows\ep05 --proxy
python h3.py assemble Shows\ep05 --proxy --audio master          # picture over the recording
```

- h3align transcribes the recording with word timings, matches it to the script, and cuts it
  into one continuous run of windows with cuts at the quietest point of each pause.
- It writes `audio: in-out` on every shot, points the series config at the recording, backs both files
  up as `.bak`, and writes `align_report.md`.
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
  shotlist/shotlist.json       generated
  shotlist/shotlist_proxy.json generated
  refs_todo.md / .json         generated work order
  refs/<char>/<char>_sheet_4panel.png   horizontal 4-panel strip
  refs/props/<name>.png                 single clean object image
  refs/_bg/<location>.png               background plate -> <Picture 4>
  refs/_takes/<ref>/…_tNN.png/.json     every ref candidate (h3refs)
  refs/_picks.json, _overrides.json     which take is live; ref prompt/seed tweaks
  audio/voices/<char>_sample.wav        clone mode
  audio/<episode>_dialogue.wav          recorded dialogue (h3align)
  renders/<shot_id>/
    <shot_id>_t01.mp4
    <shot_id>_t01_h3.wav
    <shot_id>_t01_foley.wav      (dub_keep_foley only)
    <shot_id>_t01.jpg            mid-frame thumbnail (480 px long side)
    <shot_id>_t01_strip.jpg      8 frames side by side, for hover scrub
    <shot_id>_t01.json           take sidecar: written queued, closed by Save Shot
    frames/<shot_id>_t01_%06d.png
  renders_proxy/...            same layout for the animatic
```

---

# Reference

## The Ref2VA ComfyUI workflow (`H3_Ref2VA_Shotlist_v1.json`)

h3render drives this graph for you. To run it by hand on the canvas:

1. Point **Shot List Loader** and **Save Shot** at the same `project_root`.
2. Set **Shot Index** `control_after_generate` to `increment`.
3. Set batch count to the shot count Shot Info reports. Queue once.

Re-render one shot: set the index, switch to `fixed`, bump **take**, queue.

### Install

Copy `comfy_nodes/` into `ComfyUI/custom_nodes/ComfyUI-H3-Shotlist/`, restart,
then load `targets/video/minimax_h3_ref2va/workflow.json`. Optional: `pip install demucs` for the
`dub_keep_foley` policy. When `comfy_nodes/h3_shotlist.py`
changes, copy it over again and restart ComfyUI.

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

Set per shot in `shotlist.json`. H3 emits **one mixed track** — there are no
stems — which is what these policies work around.

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

# Legacy: `h3plan.py`

`h3plan.py` compiles an episode plan JSON — script, cast and dialogue-track timings in one file,
in the format its own docstring describes — into `MiniMaxH3ChainPlan` payloads for the looping
Contex-Loop graph (`Looping MiniMax H3 Seamless Chain V2`). The shot-list pipeline above replaced
it for cut coverage; use it only for genuinely continuous chained takes. No example ships with the
repo, and it does not read `series.json` or the script format the rest of the pipeline uses.

```
python h3plan.py episode.json --report-only
python h3plan.py episode.json -o build/
python h3plan.py episode.json -o build_proxy/ --proxy
```

Each `build/plans/*.json` holds the plan string, the node's widget values, the scheduled reference
table and per-shot seeds and frame counts.

### What h3plan enforces

**Chain within a beat, cut between beats.** `continuous: true` on a sequence
compiles to one chained plan carrying motion context. `continuous: false` —
the default for cut coverage — compiles to one independent single-shot plan per
shot: zero overlap cost, renders in any order, re-render one shot alone.

**The 17k+5 frame grid.** Every length snaps up to a legal H3 value
(5, 22, 39, 56, 73, 90 … 3592). Grid-native durations at 24fps are 2.33s,
3.04s, 3.75s, 4.46s, 5.17s. Anything over 149.67s is rejected, not silently
emitted for H3 to refuse.

**Audio-first.** Shots with `audio_in` / `audio_out` take their duration from
the locked dialogue track. `duration` is the fallback for shots without speech.

**Reference budget.** H3 degrades past ~4 active picture references per scene.
Slots are allocated by shot size: a close-up on one character gets face sheet +
turnaround; everything else gets one turnaround per character. The prompt text
and the scheduled reference table are generated together, so they cannot drift.

**Deterministic seeds.** Derived from `episode/sequence/shot` IDs. Recompiling
after a script change leaves untouched shots bit-identical, so their chain
checkpoints stay valid.

---

## License

MIT — see [LICENSE](LICENSE).

MiniMax H3, the turbo LoRAs and any image model you point `kreagen.py` at come with their
own licenses; this repository only contains the tooling around them.
