# h3pipe episode script — assistant instructions

Paste this into Cursor rules, Copilot instructions, a custom GPT, or any
system prompt. Generated from docs/AUTHORING.md by tools/make_prompts.py.

You are writing for the h3pipe pipeline. Follow this format exactly: the
output is compiled by `h3build.py`, and anything that does not match is a build error, not a
style preference. Validate with `h3build.py series.json <script> --check` and `--pace` before
calling a script finished.

## The one thing that shapes every decision

A screenplay is read once, front to back, by someone who remembers. A shot list is consumed
**once per shot, independently, by a model with no memory**. Shot 94 knows nothing about
shot 93. So:

- Every shot's action must stand on its own. "He keeps walking" means nothing — write
  "Riley walks down the dirt path, already halfway along."
- Never write "as before", "same as the last shot", "still holding the ball". State it fresh
  or it does not exist.
- Character descriptions live in the series config, written once, injected into every prompt they
  appear in. Never repeat a character's appearance in the script.

## The series config: series.json

```json
{
  "series": { "id": "gold_path", "title": "The Gold Path",
              "fps": 24, "width": 1344, "height": 768, "steps": 8 },
  "proxy":  { "width": 448, "height": 256, "steps": 4, "audio_mode": "generate" },
  "style":  { "look": "a 2D hand-drawn cartoon animation with clean black line art, flat solid colors and cel shading" },
  "speech": { "pace": "normal" },
  "subjects": {
    "riley": {
      "kind": "character",
      "name": "Riley",
      "pronoun": "his",
      "design": "an eight-year-old boy with cornrow braids, an oversized yellow hoodie, baggy grey shorts and worn high-top sneakers, drawn with thick confident outlines",
      "sheet": "refs/riley/riley_sheet_4panel.png",
      "voice": "fast, bright, always slightly too loud",
      "voice_sample": "audio/voices/riley_sample.wav"
    },
    "backpack": {
      "kind": "prop",
      "name": "Riley's backpack",
      "design": "a battered olive-green canvas backpack with two front buckles and a shoelace for a zipper pull",
      "sheet": "refs/props/backpack.png"
    }
  },
  "locations": {
    "street_gate": {
      "description": "a late-afternoon suburban street seen from the yard gate, dense treeline beyond, warm low sun raking in from the left and long shadows across the asphalt",
      "plate": "refs/_bg/street_gate.png"
    }
  },
  "audio": { "mode": "clone" }
}
```

- `kind` is `character`, `prop` or `vehicle`. Only characters speak.
- `pronoun` is used in the lips-closed clause on voiceover shots.
- `audio.mode` is `clone` (H3 speaks the written lines in each character's sampled voice) or
  `source_track` (you supply a recorded mix; see `RECORDED_DIALOGUE.md`).
- Resolution must be a multiple of 32 on both axes. **1280×720 is illegal**, because 720 is
  not. 1344×768 is H3's native canvas.
- `steps`, `lora` and `model` are optional per pass; see the README's **Steps, model and LoRA**.
- `series.target` names the video model the episode renders on: `minimax_h3_ref2va`
  (MiniMax H3, the default: leave it out), `ltx2` (LTX-2.5 distilled), `ltx2_ingredients`
  (LTX-2.3 with your character sheets and plates), `minimax_h3_fl2va` (MiniMax H3 from
  first/last keyframes), or one of the silent Wan 2.2 models: `wan22_i2v` (14B, from a
  first frame), `wan22_ti2v` (5B, text or a first frame) or `wan22_vace` (14B with your
  character sheets). Single shots or sequences can render on another one; see
  **Rendering a shot on LTX-2**, **Rendering a shot on H3 from keyframes** and **Rendering a
  shot on Wan 2.2** below.

### Render profiles

A profile is a named render setup for a kind of shot, so you set it once instead of
repeating `model:` / `lora:` / `steps:` on every shot. Declare profiles in the series config
and pick one with `profile:` under a `#` sequence header or on a `##` shot:

```json
"profiles": {
  "dialogue_close": { "model": "h3_finetune_faces.safetensors",
                      "loras": ["turbo_8step.safetensors", "soft_light.safetensors:0.6"],
                      "steps": 8 },
  "action_wide":    { "steps": 10 }
}
```

- Every key is optional: `target`, `model`, `loras` (a list of LoRA names; `name:0.6`
  sets a strength, `"none"` means no LoRA) and `steps`.
- A profile applies to both passes, like a shot's own `model:` line.
- **What wins**, from weakest to strongest: the series config's `series` / `proxy` block →
  the sequence's profile → the sequence's own `model:` / `lora:` / `steps:` lines → the
  shot's profile → the shot's own lines. So `profile: dialogue_close` plus `steps: 9` on a
  shot renders the profile's model and LoRAs at 9 steps. A `lora:` line replaces the
  profile's whole LoRA list.
- A profile name that isn't in the series config is an error.

### Model files: `model_families`

Every model file a shot loads is checked against the model family its target needs
(LTX 2.5 for `ltx2`, H3 Ref2VA for `minimax_h3_ref2va`, ...) when it is queued. A file
named like the family passes. Any other name is identified from the file's own header
(the tensor names and shapes of a `.safetensors` file; the weights aren't loaded). If the
header says it's the right family, it renders with a note. If it says it's **another**
family, the shot is skipped ("wan2.2_i2v_low_noise_14B_fp8_scaled is Wan 2.2 I2V 14B
low-noise, but this LTX-2 target's model must be LTX 2.5") unless you render anyway.

A header can't tell every family apart. H3 Ref2VA and FL2VA files are identical inside, and
so are Wan 2.2's high- and low-noise experts and the LTX 2.3 and 2.5 upscalers. For
those, the name decides. If you rename or merge such a file, add its name to the series
config so the pipeline knows which one it is:

```json
"model_families": {
  "ltx2.5": ["my_ltx25_merge*"],
  "minimax-h3-ref2va": ["h3_faces_finetune*"]
}
```

- Keys are family ids: `minimax-h3-ref2va`, `minimax-h3-fl2va`, `ltx2.5`, `ltx2.3`,
  `ltx2.5-latent-upscaler`, `krea2`, `wan2.2-i2v-14b-high`, `wan2.2-i2v-14b-low`, ...
  (`targets/modelid.py` lists them all; each target's `target.json` `models` block says
  which one each of its model settings needs).
- Values are file-name patterns: `*` matches anything, case doesn't matter. They add to
  the target's own patterns; they don't replace them.
- A matching name is trusted without reading the file, so keep the patterns specific.

### Which model? Readiness and downloads

Run `python h3.py targets` (with ComfyUI running) before you pick a target. It prints one
line per target, `ready`, `degraded`, `not_ready` or `unknown` (ComfyUI didn't answer),
and under it every file that target is missing, with the models folder it goes in and
where to download it:

```
  ltx2                 degraded   missing 1 optional
      optional    duration_head ltx-2.5-duration-head-bf16.safetensors  [feature off: dur: model (duration head)]
                  -> models/model_patches/   https://huggingface.co/Lightricks/LTX-2.5/...
```

Add an episode (`python h3.py targets Shows\ep05`) to use its series config (the pass
blocks' model and LoRA, `model_families`) and mark the episode's target. `--json` prints
the same as data.

- **Required** files (the model, text encoder, VAEs): without one the target can't render.
  Its shots are skipped when you queue them, and the message names the file, the folder
  and the download link.
- **Accelerators** (the turbo LoRAs, LTX 2.3's distilled checkpoint): without one the
  shot still renders, on the pass's slower `base` settings (more steps, no turbo LoRA),
  and the take says so.
- **Optional** files (the LTX duration head): only their feature is off (`dur: model`
  renders the estimate).

A link is only given when ComfyUI's own templates, your saved workflows or
ComfyUI-Manager's model list record it. Otherwise the line says to search for the exact
file name. If you have a file of the same family under another name (another
precision, a merge), it is used instead, and the take notes which file it was.

**Which target a shot renders on**, most specific first:

1. the render request (the redo dialog, `h3render --target`);
2. the shot's override (the editor, or `h3.py override Shows\ep05 sh020 --target ltx2`);
3. the script: a `target:` line, or a profile's target, on the shot or its sequence;
4. the **episode target** set in the editor, or with
   `python h3.py override Shows\ep05 --episode-target ltx2` (`built` clears it). It is
   kept in the episode's `overrides.json`, not in `series.json`. While it is set, a shot
   can be pinned to its built target from the editor (or `--target built`).
5. `series.target` in `series.json`;
6. `minimax_h3_ref2va`.

A shot whose target is not the one it was built for is compiled for its new target when
it is queued, so you don't need to rebuild. To make an episode target permanent, put
`"target": "<id>"` in `series.json`'s `series` block and rebuild.

### Reference images: the `refs` block

Reference images (character views, props, plates) and shot keyframes are made by an
**image model**. The series config can say which:

```json
"refs": {"target": "krea2", "keyframe_target": "flux2_klein_edit"}
```

| image target | what it is |
|---|---|
| `krea2` | Krea 2 turbo, text to image (the default for refs) |
| `z_image_turbo` | Z-Image Turbo, fast text to image |
| `flux2_klein` | FLUX.2 Klein 9B, text to image |
| `flux2_klein_edit` | FLUX.2 Klein 9B with up to 4 reference images (the default for keyframes when it is installed; otherwise keyframes use the refs model) |
| `flux_kontext` | FLUX.1 Kontext dev, with one reference image |

The editor can choose per episode (kept in `overrides.json`, not here) and per ref, and a
generate request can name one; most specific wins. `python h3.py targets --kind image` says
which are installed and what to download for the rest. A ref is worded the same whichever
model draws it.

### Negative prompts

Targets that take a negative prompt (LTX-2, Wan 2.2 and the image models; not H3) use, most
specific first: the render request's, the shot's override (the editor, per pass), the
episode's **`negative.txt`** (a text file beside the script), the series config's
`"negative": "..."`, then the model's own. The take records which one it used. Turbo and
distilled models sample without guidance (cfg 1): there a negative does nothing, and the
take says so.

### Writing the `design` sentence

This is the highest-leverage sentence in the pipeline. It is injected verbatim into every
prompt the character appears in, and the model sheet is generated from it.

Describe **what a model sheet artist could draw**. Age and build, silhouette, hair, wardrobe
with specific colors, one or two distinctive accessories, line quality. Concrete nouns.

Weak: *"a mischievous kid with boundless energy who never sits still"* — none of that is
drawable, and it crowds out detail that is.

Location descriptions work the same way and must include the light: time of day, direction,
quality. The plate is generated from that sentence.

### A location is one angle, not one place

One plate per location means every cut rebuilds the same view, and characters appear to pop
into an unchanged picture instead of being discovered by a camera. Author two to four angles
per place and let each shot name its own with `plate:`:

```
office_desk    the desk against the wall, bulb overhead
office_lid     looking up at the propped lid and the moonlight
office_boxes   the wall of file boxes
office_wide    the whole interior at once
```

**Time of day is part of the location.** `street_day` and `street_night` are two entries with
two plates, because the plate carries the light.

## The script: epNN.md

```
= ep01  The Gold Path

# sq01  street_gate

## sh010
who: huey
size: wide
dur: 3.04
Huey drifts to a stop at the edge of the yard and looks off down the empty street.
camera: pushes in on him at slow speed
sound: late-afternoon suburban ambience, distant birds, a faint breeze

## sh020
who: riley, huey
with: backpack
size: medium
dur: 4.46
plate: street_yard
Riley runs into frame from the left and pulls up hard beside Huey.
Huey does not turn his head.
camera: arcs around behind them with small amplitude at slow speed
RILEY (breathless): There's gold at the end of the path.
HUEY (flat): There is not.
MOM (V.O.): Shoes off in the house!
sound: running footsteps on grass, fabric movement
```

| Line | Meaning |
|---|---|
| `= id  Title` | episode header, once at the top |
| `# sqNN  location` | sequence; the location must be a series config key |
| `## shNNN` | a shot. **Every `##` is a cut** — no `CUT TO:` needed |
| `who: a, b` | characters on screen, becoming `<Picture 1..3>` in this order |
| `with: x, y` | props and vehicles, taking the next free slot |
| `size: close/medium/wide` | shot size; decides how much of the sheet is used |
| `plate: street_gate` | this shot's angle; defaults to the sequence's location |
| `dur: 3.04` | duration in seconds, from the grid below |
| `dur: auto` | derive the duration from the dialogue at this shot's pace |
| `dur: model` / `dur: model 3-8` | let the video model choose the length when it renders, optionally between 3 and 8 seconds; see **Letting the model time a shot** |
| `audio: 3.10-7.40` | window on a locked dialogue mix; sets the duration instead |
| `pace: slow/normal/fast` | 3.6 / 4.4 / 5.4 syllables per second |
| `camera: pushes in…` | camera move, **without** the words "The camera" |
| `sound: …` | ambience and physical action sounds |
| `music: …` | audience-only score; omit for none |
| `extras: …` | other people in frame, described; see **Crowds and extras** |
| `model:`, `lora:`, `steps:` | per-shot render overrides; also valid under a `#` header |
| `profile: dialogue_close` | a render profile from the series config; also valid under a `#` header |
| `target: ltx2` | the video model for this shot or sequence (`minimax_h3_ref2va`, `ltx2`, `ltx2_ingredients`, `minimax_h3_fl2va`, `wan22_i2v`, `wan22_ti2v` or `wan22_vace`); see below |
| `first: continuity` / `last: generate` | how this shot's first / last keyframe is made: `continuity`, `generate`, `import`, `none`, or a path to an image; also valid under a `#` header (the default of its shots); see **Keyframes** |
| `NAME: line` | dialogue from someone on screen |
| `NAME (V.O.): line` | voiceover: speaks, is not drawn, costs no reference slot |
| `NAME (O.S.): line` | off-screen: in the space, outside the frame |
| `continuous: yes` | under a `#` header: the sequence is one unbroken take |
| `// text` | comment |

Any other line is action prose.

### Rendering a shot on LTX-2

`target: ltx2` on a `##` shot, under a `#` header, or in a profile renders that shot (or
sequence) on LTX-2.5 instead of the series target; any other name is an error. One episode
can mix the two: the build writes the LTX shots to their own shotlist, and the editor, the
render command and the review cut treat the episode as one. What changes for an LTX shot:

- **No reference pictures.** LTX takes no character sheets or plates. Everyone in `who:` /
  `with:` is described in words from their `design`, and the place from the location's
  `description`, so those sentences carry the look on their own. A first- and/or last-frame
  keyframe (`refs/shots/<shot>/first.png`, `last.png`, imported in the editor's Refs tab)
  pins the picture when you have one; without them the shot is text-to-video.
- **Its own grid:** `8k + 1` frames (9, 17, 25, … 73 = 3.04s, 97 = 4.04s at 24fps), up to
  about 20 seconds. The same `dur:` snaps to a slightly different length than on H3.
- **Sound is always generated with the picture.** LTX takes no voice sample and no recording:
  a shot the series would `clone` or `dub` renders with `generate` instead, and the build
  and the take say so. Each speaker's `voice` line is what shapes the voice.
- **One paragraph of prose**, written for you: the look, then the framing and the camera,
  then who is in frame and what they do, the lines with who says them and how, then the
  sound and music. Write `camera:` for a move you want; without it the camera stays still.
- **Size** comes from the series config's pass blocks, snapped down to a multiple of 64 and
  kept under 1 MP (1344×768 stays; H3's 480×272 proxy becomes 448×256). Its model, LoRA
  and steps are the target's own: a series written for H3 doesn't hand them H3's.

To try a shot on LTX without touching the script, retarget it from the editor or with
`h3.py override <ep> sh040 --target ltx2` (`--target built` undoes it).

### Rendering a shot on LTX-2 with its refs: `target: ltx2_ingredients`

`target: ltx2_ingredients` renders on LTX-2.3 with the "ingredients" IC-LoRA, which keeps a
recurring character looking like its sheet (and a prop like its picture, the set like its
plate). Everything above about LTX-2 holds (generated sound, `camera:`, retargeting), except:

- **Your refs are used, and required.** Each shot gets a reference sheet, made when it is
  queued from the picked refs: one panel per element, tiling the frame, in order: each character in
  `who:` (one panel of its 4-panel sheet: the face on a one-character `close`/`cu` shot, the
  three-quarter body otherwise, as on H3), then each prop and vehicle in `with:`, then the
  location's plate. A shot whose ref is missing is blocked, as on H3. Rendering anyway
  leaves that element off the sheet (it is still described in words); a shot left with no
  refs at all renders text-only, without the IC-LoRA. The sheet is kept beside the take
  (`<shot>_tNN_refsheet.png`), and re-picking a view marks the take stale.
- **Give important things their own panel.** The model only reproduces what is on the
  sheet, and bigger panels carry over better: a crowded shot (many `who:`/`with:`) gets
  small panels. Keep the plate clean.
- **The shot's own length**, on LTX's `8k + 1` grid from 2 s (49 frames) to 20 s (481 frames)
  at 24 fps. The IC-LoRA was trained on 121 frames (5.04 s), so any other length gets a
  soft warning in `--check` (identity may weaken); it still renders. Longer than 20 s is an
  error: split the shot.
- **Size is the model's**: 768×448 for the final, 512×288 for the proxy, whatever the series
  config's pass blocks say.
- **A two-part prompt**, written for you: `Reference sheet:` names each panel in order (the
  character's `name` and `design` and which view, the prop's, the location's
  `description`), then `Generated video:` is the LTX-2 paragraph, where someone on the
  sheet is only named.
- A `lora:` line or profile written for another model doesn't remove the IC-LoRA: it is
  put back first in the list, and the take says so.

### Rendering a shot on H3 from keyframes: `target: minimax_h3_fl2va`

`target: minimax_h3_fl2va` renders on MiniMax H3's first/last-frame model. It is H3 (same
grid, same sizes, same turbo-LoRA presets and speaker tags), but it takes **no reference
pictures**: what pins the look is the shot's keyframes. What changes for such a shot:

- **Keyframes instead of sheets.** `refs/shots/<shot>/first.png` is the first frame and
  `last.png` the last. Both are optional; with neither the shot is text-to-video. They come
  from **continuity** (`python h3.py keyframe <ep> <shot>`: the previous shot's last frame
  as this one's first, or `--last` for the next shot's first frame as this one's last; the
  editor's Refs tab does the same) or from an **import** in the Refs tab. The render adds
  the model's alignment line for whichever it has, so there is nothing to write for them.
- **Subjects in words.** No `<Picture N>` slots: everyone in `who:` / `with:` is described
  from their `design`, and the place from the location's `description` (all of it on a
  wide, what is behind them on a medium, a shallow slice on a close-up). A keyframe wins
  where it and the words differ.
- **The H3 prompt, without the picture sections.** Written for you in H3's three fields:
  `integrated_multimodal_description` (the look, the framing, who is in frame, the action,
  `camera:`, then each line as `<d>[English] …</d>` with a speaker id), then
  `overall_soundscape` (`sound:`) and `non_diegetic_music` (`music:`).
- **Sound:** `generate` as usual. `dub` and `dub_keep_foley` work: the shot's `audio:` window
  of the recording (`audio.track`) is cut and anchored at the start of the shot, so the
  mouths follow your recording; `dub` makes it the whole soundtrack, `dub_keep_foley` lays
  `sound:` under it. `retention:` has no effect here. `clone` has no voice-sample slot on
  this model: those shots render with `generate` (each speaker's `voice` line shapes the
  voice), and the build and the take say so.
- **Length:** H3's `17k + 5` grid. The model is trained on about 5 to 15 seconds (124 to
  362 frames); longer shots render with a warning. No `continuous: yes` chaining: continue
  a shot from the one before with its first keyframe instead.
- **Size** comes from the series config's pass blocks (multiples of 32). Model, LoRA and
  steps are the target's own unless it is the series target.

To try a shot this way without touching the script, retarget it from the editor or with
`h3.py override <ep> sh040 --target minimax_h3_fl2va`.

### Rendering a shot on Wan 2.2: `wan22_i2v`, `wan22_ti2v`, `wan22_vace`

Three Wan 2.2 targets, for pictures without sound:

- **`wan22_i2v`** (label "Wan 2.2 14B I2V"): the 14B image-to-video pair (a high noise model,
  then a low noise one, each with its 4-step turbo LoRA). It animates a picture you give
  it: **a first frame is required** (`refs/shots/<shot>/first.png`: generated from the
  shot's description, cut from the previous shot by continuity, or imported; see
  **Keyframes**). A shot without one is blocked, and rendering anyway can't help: "Wan 14B
  I2V needs a first frame: generate one or use continuity (or import one), or retarget to
  wan22_ti2v". A `last.png` too makes it a first/last-frame shot.
- **`wan22_ti2v`** ("Wan 2.2 5B TI2V"): the small 5B model, text-to-video, or from a first
  frame when there is one. Nothing blocks it: the fast, cheap choice for proxies, and
  where an I2V shot without a first frame can go.
- **`wan22_vace`** ("Wan 2.2 14B VACE (refs)"): the 14B VACE pair, which keeps your
  characters looking like their sheets. When the shot is queued, one reference picture is
  made from the picked refs: one panel per subject on white (each character in `who:`,
  the face panel on a one-character `close`/`cu` shot, else the three-quarter body; then
  each prop and vehicle in `with:`). The plate isn't used: the place is described in words.
  A missing sheet blocks the shot, as on H3; rendering anyway leaves it off. First and last
  keyframes are optional and pin the ends of the shot. No turbo LoRA exists for it here,
  so it renders at 10 steps for a proxy and 20 for a final: slower than the other two.

What every Wan shot has in common:

- **No sound at all.** Wan makes none. Every shot renders a mute mp4 whatever its
  `policy:` says (the take's notes say so), and the review cut lays silence under it. A
  dialogue shot gets a `--check` warning ("no audio or lip-sync on Wan"): the lines are
  **acted silently** (the prompt says who talks and how, mouth moving), so the recording or
  another take's sound goes on at the edit. Keep dialogue shots on a target with sound if
  you need lip-sync.
- **Its own frame rate.** The 14B models (`wan22_i2v`, `wan22_vace`) render **16 fps**; the
  5B renders 24. Lengths are on a `4k + 1` grid at that rate: 5 s is 81 frames at 16 fps,
  121 at 24. The models were trained on 5 s shots; a longer one renders with a warning,
  and past 10 s it must be split. A 16 fps take in a 24 fps episode is converted when the
  cut is assembled (frames repeated, never sped up), and the editor's timeline and Play all
  time it by its own duration.
- **Prose prompts**, written for you: the look, the framing and `camera:`, who is in frame
  from their `design`, the action, on-screen text, then the lines as silent acting. No
  `sound:` or `music:`: they are ignored. Wan's standard (Chinese) negative prompt is
  added for you.
- **Size is the model's own** (Wan is trained at 480p and 720p, far above H3's proxy):
  `wan22_i2v` and `wan22_vace` 832×480 final, 640×352 proxy (multiples of 16); `wan22_ti2v`
  1280×704 final, 640×352 proxy (multiples of 32). A cut mixing sizes is scaled to the
  episode's size when assembled.
- **A `lora:` line or profile** replaces the preset's LoRAs. On the 14B models there are
  two chains, one per stage: a LoRA whose name says `high_noise` / `low_noise` goes to that
  stage, any other to both.

To try a shot on Wan without touching the script, retarget it from the editor or with
`h3.py override <ep> sh040 --target wan22_ti2v`.

### Keyframes: `first:` and `last:`

Some video targets start (or end) a shot on a picture you give them: its **keyframes**,
`refs/shots/<shot>/first.png` and `last.png`. Which shots need one follows from the target
each shot renders on:

- `wan22_i2v` **requires** a first frame (it animates it);
- `ltx2`, `minimax_h3_fl2va`, `wan22_ti2v` (first only) and `wan22_vace` use them when they
  exist (**optional**);
- `minimax_h3_ref2va` and `ltx2_ingredients` don't read keyframes.

The Refs tab lists every keyframe a shot needs, before anything exists, with how it will be
made. You choose that with a line on the shot, or under a `#` header for all its shots:

```
# sq02  workshop
first: continuity

## sh030
last: generate
first: refs/stills/sh030_open.png
```

| value | meaning |
|---|---|
| `continuity` | the previous shot's last frame (the default for a shot with a previous shot in its sequence) |
| `generate` | a still made from the shot's own description (the default otherwise) |
| `import` | you'll import one (the Refs tab) |
| a path | this image is imported as the keyframe (relative to the episode) |
| `none` | no keyframe, even where the target could use one (a required one stays required) |

**Generating a keyframe** writes a still from the shot: the look, the framing and the
location, who is in frame (their `design`), and the action at that moment: for `first` how
the shot opens (the action's first sentence, about to happen); for `last` how it ends (its
last sentence, finished). Write the action as a short sequence of sentences and both ends
come out right. Dialogue isn't drawn. The keyframe model (the series config's
`refs.keyframe_target`, else FLUX.2 Klein edit when it is installed) also gets **the picked
character views** (the face on a one-character close-up, else the three-quarter body), the
props and **the plate** as reference images, so the characters look like their sheets; the
prompt names each one ("image 1 is Ada: draw this character exactly as in image 1").

`python h3.py keyframe Shows\ep05 --missing` fills every required keyframe and every one the
script asks for (continuity when the previous shot has a usable take, else a still);
`h3.py keyframe Shows\ep05 sh030 --generate` makes one still; `--clear` takes a keyframe away
(the shot renders without one, and nothing puts it back until you pick one).

### Letting the model time a shot: `dur: model`

`dur: model` hands the shot's length to the video model: LTX-2.5 reads the prompt and
predicts how long the shot naturally runs, then renders that. `dur: model 3-8` keeps the
prediction between 3 and 8 seconds (without a range: 1 to 20).

- **The build still needs a length** for the cut and the runtime, so it writes an
  **estimate**: the `dur: auto` length when the shot has dialogue, else 5 seconds, inside
  your range. The take records the real length it came out at, and the editor's timeline
  and Play all use that once it's rendered.
- **Only `ltx2` can predict**, and only once its duration head is installed (the file
  `ltx-2.5-duration-head-bf16.safetensors` in ComfyUI's `models/model_patches`). Until then,
  and on every other target (H3, `ltx2_ingredients`, Wan), the shot renders at the estimate:
  `--check` warns and the take's notes say so.
- Use it for silent action and establishing shots whose length you don't care to pick. A
  dialogue shot is better with `dur: auto` or an `audio:` window, which keep the lines
  their room.

## Durations land on a grid

H3 only accepts `17k + 5` frames. Anything else rounds **up**, and you pay for frames you
throw away. At 24fps these are free:

| Frames | Seconds | Use for |
|---|---|---|
| 39 | **1.62** | a single word, a snap |
| 56 | **2.33** | a quick reaction, one short line |
| 73 | **3.04** | the workhorse shot |
| 90 | **3.75** | a beat with a little air |
| 107 | **4.46** | two lines of dialogue |
| 124 | **5.17** | a long beat, a small piece of business |
| 192 | **8.00** | rare: a sustained action run |

Render cost scales as roughly `duration^1.29`, so long shots are *less* efficient per second
than short ones. Cartoon pacing, 2 to 5 seconds, is both better filmmaking and cheaper.

## Dialogue has to fit the shot

This is the easiest way to wreck an episode, and it does not announce itself: H3 will fit any
line into any window by speeding the delivery up. The words are all there, the lip sync is
fine, and the performance is gone.

A 2.33s shot holds about **seven syllables**. Never guess — the compiler measures it:

```
python h3build.py series.json ep01.md --pace
```

Three ways out of a crammed shot, in the order worth trying:

1. **Split it.** A line needing six seconds usually wanted a reaction cut in the middle
   anyway, and the split buys the second angle the scene was missing.
2. **Cut words.** Over-packed shots are usually over-written.
3. **Lengthen it.** `dur: auto` derives the length and snaps it to the grid.

`pace: fast` marks a rush that is deliberate and silences the warning.

## Camera moves use H3's vocabulary

The `camera:` line is inserted after the words "The camera", so write the verb phrase only.
Compose from **motion + amplitude + speed**; the last two are optional.

**Motion:** zooms in / out · pushes in / pulls out · pans left / right · trucks left / right ·
tilts up / down · pedestals up / down · arcs around · tracks · holds a static shot · shakes
slightly / strongly · takes the subject's POV · rolls clockwise / counterclockwise

**Amplitude:** with small amplitude · with large amplitude
**Speed:** at slow speed · at fast speed

"Arcs around them with large amplitude at fast speed" beats "swoops around dramatically",
because it names a move the model was trained on.

## Breaking a scene into shots

A scene with three paragraphs of action is six to ten shots, and deciding where they fall is
the job.

**Cut when** the subject changes, a new beat lands, someone new speaks, time skips, or the
framing must change materially. **Don't cut** when only the distance shifts a little — use a
camera move. A cut should bring new information; a cut to the same thing slightly closer is a
jump cut, and it will render as one.

A workable rhythm for a dialogue scene: wide establishing → medium two-shot for the exchange →
close on the reaction that matters → back out.

**Open every shot on movement.** The model has no memory of the previous cut, so a shot whose
action describes a *position* renders that position and then looks for something to do with
it. Write the state change: "Dex swings his boots onto the desk and settles back", not "Dex
sits with his boots on the desk".

Reserve `camera: holds a static shot` for reactions where stillness is the point. On a wide
you have just cut to, a slow push gives the space depth, and costs nothing extra.

Shot IDs go `sh010, sh020, sh030`, leaving gaps so you can insert `sh015` later. Renumbering
changes seeds and invalidates renders. An ID is unique across the whole episode, not just its
sequence: continue the numbering (`sh110` in the next sequence, or keep counting) rather than
restarting at `sh010`.

## Reference slots

Every shot hands H3 four images. Slots 1–3 are the subjects — characters first, then props
and vehicles, in the order `who:` and `with:` list them. **Slot 4 is always the location
plate.**

That caps you at **three subjects on screen**, and a fourth is an error, not a silent drop.
When a scene has four characters, stage one out of frame, split the shot, or describe the
extra in the action text without referencing it. Crowds cost nothing that way — they just
won't look the same from shot to shot.

A `(V.O.)` or `(O.S.)` speaker costs no slot, is never drawn, and needs no sheet — only a
`voice` in the series config. A shot may have no `who:` at all: an establishing plate with narration
over it.

### Crowds and extras

By default a shot's prompt states that exactly one person appears in it per referenced
character, which is what stops H3 drawing the same face twice. The moment the action puts
someone else in frame — a dance partner, a mob, a crowd at a door — that sentence contradicts
the action, and H3 resolves the contradiction by **duplicating the referenced character**,
often with interlace-looking smearing where the copies overlap.

`extras:` names those people, so the prompt keeps identity locked for the subject and allows
everyone else:

```
## sh090
who: eleanor_30
extras: one masked man in black tails, his back mostly to camera, and far-off dancers blurred into bokeh
Eleanor circles a masked man in black tails, one hand in his, laughing as she turns around him.
camera: arcs around Eleanor with small amplitude at slow speed
```

Two habits go with it. **Keep people out of the plate**: a location described as "full of
masked dancers" is rebuilt as figures that compete with the subject and feed the duplication
— describe the empty space and let the far background fall off into bokeh. And **name the
subject in the camera move**: "arcs around them" leaves the model to decide who "them" is.

`size:` also decides how hard the plate pulls: `partially_preserved` on a wide or medium,
`weak_reference` on a close-up, because a plate declared authoritative behind a face competes
with the sheet in exactly the shot where identity matters most.

Add a prop to `with:` only when its exact design matters and recurs. A generic mug is better
described in the action text, because every referenced prop spends a slot.

## Sequences

A sequence is one location, continuously. A new place — or the same place at a different time
of day — starts a new `#` sequence. Intercutting is just alternating sequences: `# sq03
kitchen`, `# sq04 phone_room`, `# sq05 kitchen`.

Keep a sequence intact and vary the shots with `plate:` rather than splitting a scene into
one-shot sequences, which re-seeds every shot in it.

`continuous: yes` chains a sequence into one unbroken take, carrying motion across the joins.
Use it rarely: it costs 22 frames per shot after the first, which is 30% of a 3-second shot.

## What goes on which line

These map to different fields in the generated prompt, and mixing them dilutes all three.

- **action** — what is visibly happening, present tense, concrete and physical. Not
  motivation, not backstory, not what a character feels.
- **camera** — the move only.
- **sound** — ambience and physical action sounds: footsteps, fabric, doors, wind, impacts.
  Not dialogue, not score.
- **music** — audience-only score. Omit it on most shots; scoring is usually a conform
  decision.

## Finish by validating

```
python h3build.py series.json ep01.md --check     # counts, runtime, references, warnings
python h3build.py series.json ep01.md --pace      # dialogue pacing per shot
```

Errors name the line and quote it. Common ones: a name in `who:` that is not in the series config; a
location with no plate; an ALL-CAPS `NAME:` line for someone who cannot speak (usually a typo,
which would otherwise become action prose); more than three subjects; a shot with neither
`dur:` nor `audio:`; `dur: auto` on a shot with no dialogue; a `dur: model` range that isn't
`min-max` seconds with min below max.

A script that does not compile is not a draft, it is a bug. Fix and re-run until it is clean.
