---
name: h3pipe-episode-script
description: Write episode scripts and series configs for h3pipe, the script-to-episode AI video pipeline on ComfyUI. Covers the epNN.md script format (sequences, shots, ALL-CAPS dialogue lines, who/with/size/dur/plate/camera/sound fields, target lines and first/last keyframe lines) and the series config (series.json) of subjects, locations, voices, profiles and targets. Shots render on a video target chosen per shot or episode, MiniMax H3 by default, or LTX-2, Wan 2.2 and others. Use it whenever the user wants to write, draft, outline or extend an episode, break a screenplay or scene into shots, adapt spec-format pages, add characters, props or location angles to a series config, choose which model renders a shot, re-time shots whose dialogue doesn't fit, or fix a script h3build rejected, even if they never name h3build or the file formats.
---

# Writing an episode for h3pipe

You are writing for h3pipe. Follow this format exactly: the output is compiled
by `h3build.py`, and anything that does not match is a build error, not a style preference.

Before writing anything, ask for the series config (`series.json`) if the series has one.
Adding a character means adding to that file, not inventing a new one: if you write `riley`
when the series config already has `riley_freeman`, every reference path breaks. With no
series config yet you are starting a series: write both files.

Write the film, not the model. Leave `target:` lines out unless a shot needs something only
one model does (see **Choosing a video target**); advice that holds for one target only is
marked with that target's name. Validate with `h3build.py series.json <script> --check` and
`--pace` before calling a script finished.

## Your first episode

Two files in one folder — and `python h3.py new Shows\ep01` writes both of them for
you (so does **New episode…** in the editor's project folders). This is what it writes,
and it is also `examples/starter/`, so you can read it before you run anything.

`Shows\ep01\series.json` first. `audio.mode: generate` means the model invents the
voices from each character's `voice` line, so nothing has to be recorded first. The
pictures are named `../refs/...`, one level up: that way every episode of the show
shares one `refs` folder while each keeps a series config of its own (a config in the
episode wins, the one beside it is the fallback), which is what lets episode four add
a character without touching the first three.

The folder can be called anything — `ep01` here, but `s2ep01`, `s3ep07` or `pilot`
just as well, which is how a show with seasons keeps them apart. Name the script
after the folder (`s2ep01\s2ep01.md`) and everything finds it: that is the only
name that still works when the episodes share one `series.json` in the folder above
them, and it is what lets `python h3.py build Shows --each` pick up every episode
under a show at once.

```json
{
  "series": {
    "id": "first_light",
    "title": "First Light",
    "fps": 24,
    "width": 1344,
    "height": 768
  },
  "proxy": { "width": 448, "height": 256 },
  "style": {
    "look": "a 2D hand-drawn cartoon animation with flat 2D illustration, clean black line art, flat solid colors, and cel-shaded artwork"
  },
  "subjects": {
    "ada": {
      "kind": "character",
      "name": "Ada",
      "pronoun": "her",
      "design": "a nine-year-old girl with short curly black hair, a green raincoat, and yellow rain boots, drawn with thick confident outlines",
      "sheet": "../refs/ada/ada_sheet_4panel.png",
      "voice": "bright, quick, a little breathless"
    },
    "lantern": {
      "kind": "prop",
      "name": "the porch lantern",
      "design": "a dented brass hurricane lantern with a smoke-stained glass chimney and a wire handle",
      "sheet": "../refs/props/lantern.png"
    }
  },
  "locations": {
    "porch": {
      "description": "a wooden front porch at dawn, wet boards, a hanging lamp still lit, mist over the lawn beyond",
      "plate": "../refs/_bg/porch.png"
    }
  },
  "audio": { "mode": "generate" }
}
```

`Shows\ep01\ep01.md`:

```
= ep01  First Light

# sq01  porch

## sh010
who: ada
size: wide
dur: 3
Ada steps out onto the wet porch and stops, looking at the mist on the lawn.
camera: holds a static wide shot
sound: dawn birdsong, dripping water, a faint breeze

## sh020
who: ada
with: lantern
size: medium
dur: 4
Ada lifts the lantern off its hook and turns the flame down until it gutters out.
camera: holds static
sound: a metal hook ringing, the soft pop of a flame going out

## sh030
who: ada
size: close
dur: auto
Ada grins and pulls her hood up.
camera: pushes in with small amplitude at slow speed
ADA: It rained all night and nobody saw it but me.
sound: rustling raincoat, dripping water, distant birds
```

Build it with `python h3.py check Shows\ep01` and then `python h3.py build
Shows\ep01`; [INSTALL.md](../INSTALL.md) has the rest of the commands, and
`docs/SCRIPT_CONVERSION.md` works a real screenplay scene through to
shots if you are starting from one (the script-writing skill bundles it as
`references/script_conversion.md`).

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

## Choosing a video target

| target | what pins the look | sound | keyframes | length grid | good for |
|---|---|---|---|---|---|
| `minimax_h3_ref2va` (default) | your sheets and plate, as four reference pictures | generated, dubbed to a recording, or cloned from voice samples | not read | `17k+5` frames at 24 fps | recurring characters who talk; lip sync to your recording |
| `minimax_h3_fl2va` | the shot's first/last keyframes, plus words | generated or dubbed (no clone) | optional | `17k+5` at 24 fps, best 5–15 s | picking up exactly where the previous shot ended |
| `ltx2` | words, plus keyframes when there are any | always generated | optional | `8k+1` at 24 fps, up to ~20 s | fast text-to-video with sound; `dur: model` |
| `ltx2_ingredients` | a reference sheet made from your refs (required) | always generated | not read | `8k+1` at 24 fps, 2–20 s, best 5.04 s | identity on LTX |
| `wan22_i2v` | a first frame (required) | none | first required, last optional | `4k+1` at 24 fps, best ≤ 3 s | animating a still |
| `wan22_ti2v` | words, or a first frame | none | first optional | `4k+1` at 24 fps | cheap proxies; where an I2V shot without a first frame can go |
| `wan22_vace` | a reference picture made from your sheets | none | optional | `4k+1` at 24 fps, best ≤ 3 s | identity without sound |

- **Leave the target out** unless the choice is a decision about the film. Shots with no
  `target:` render on the episode's target (set in the editor) or the series config's
  `series.target`, so a whole episode can be moved to another model without touching the
  script.
- **Name it** (`target:` on a shot, or under a `#` header for its sequence) when a shot
  needs something only one model does: a silent insert on Wan, a continuity shot on
  `minimax_h3_fl2va`, a shot whose length you want LTX to choose (`dur: model`).
- **Dialogue needs a target with sound**, and lip sync to a recording needs an H3 target.
  Wan acts the lines silently.
- **On a target that takes no reference pictures** (`ltx2`, `minimax_h3_fl2va`, `wan22_i2v`,
  `wan22_ti2v`; `wan22_vace` takes the sheets but not the plate), the `design` and location
  `description` sentences are what the model knows about the look, apart from any
  keyframe. Keep them complete.
- A keyframe is a picture: plan `first:` / `last:` lines (see **Keyframes**) for the
  targets that read them.

Details per target are under **Rendering a shot on …** below; `python h3.py targets` says
which ones your ComfyUI can render.

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

- `style.look` opens every shot's description ("The target video is …"), so it sets the
  photography of the whole series. See **Writing the `look` line**.
- `kind` is `character`, `prop` or `vehicle`. Only characters speak.
- A character's `sheet` is a **four-panel strip** (three-quarter body, side, back, face),
  and a shot gets one panel of it: the three-quarter body, or the face on a one-character
  close-up. A picture you supply that is **one image** of the character (a portrait, a
  full-length shot on white) needs `"sheet_panels": 1`, or it is cut into quarters and the
  model gets a strip from its left edge. With it the picture is sent whole on H3 Ref2VA.
- `members` makes a character a **group**: one picture of that many people (2–12), cast
  like any character and costing one slot. See **A group: the same extras every shot**.
- `of` makes the subject a **wardrobe variant** of another one — the same character in
  different clothes, the same prop in a different state. See **A wardrobe change is a new
  subject**.
- `pronoun` is used in the lips-closed clause on voiceover shots.
- `audio.mode` sets how dialogue shots sound: `clone` (the model speaks the written lines in
  each character's sampled voice), `source_track` (the default: you supply a recorded mix,
  `audio.track`, and shots dub to it; see `RECORDED_DIALOGUE.md`) or `generate` (the model
  invents the voices from each `voice` line; no recordings or samples needed).
  `proxy.audio_mode` can use another mode for the animatic (`generate` is the usual one),
  `audio.default_policy` forces one policy for every dialogue shot, and `audio.retention`
  sets the dub `retention:` default. A target that can't do a mode says so and generates.
- `upscale.save_latents` decides which renders keep their latent for an upscale later:
  `"final"` (the default: the final pass is what gets upscaled; a proxy take can still be,
  through the VAE), `"always"` or `"never"` (to
  save the disk space, a few MB a take; an upscale then goes through the VAE).
- Resolution must be a multiple of 32 on both axes for H3. **1280×720 is illegal**, because
  720 is not. 1344×768 is H3's native canvas. The other targets snap it to their own sizes.
  Whether the final pass renders at the size you deliver, or smaller and is upscaled, is
  **Final resolution** below.
- `steps`, `lora` and `model` are optional per pass; see the README's **Steps, model and LoRA**.
- `series.target` names the video model the episode renders on: `minimax_h3_ref2va`
  (MiniMax H3, the default: leave it out), `ltx2` (LTX-2.5 distilled), `ltx2_ingredients`
  (LTX-2.3 with your character sheets and plates), `minimax_h3_fl2va` (MiniMax H3 from
  first/last keyframes), or one of the silent Wan 2.2 models: `wan22_i2v` (14B, from a
  first frame), `wan22_ti2v` (5B, text or a first frame) or `wan22_vace` (14B with your
  character sheets). Single shots or sequences can render on another one; see
  **Rendering a shot on LTX-2**, **Rendering a shot on H3 from keyframes** and **Rendering a
  shot on Wan 2.2** below.

### Final resolution: render at size, or render small and upscale

`series.width` / `series.height` is the size every final take renders at, and
`proxy.width` / `proxy.height` the proxy pass's. The usual setups:

| | final | proxy | shape | a 3 s take | upscaled (picks only) | delivers |
|---|---|---|---|---|---|---|
| **Render at size** (the default) | 1344×768 | 448×256 | 7:4 | ~53 s | not needed | 1344×768 as rendered |
| **Render small, upscale to 1080p** | 960×544 | 448×256 | ≈7:4 | ~20 s | re-sample 2x → 1920×1088, ~25–35 s | 1920×1080 (8 rows cropped) |
| **Render 16:9, upscale to 1080p or 4K** | 1024×576 | 512×288 | 16:9 | ~23–24 s (est.) | re-sample 2x → 2048×1152 (scaled down to 1080p; for 4K, then an upscale model) | 1920×1080 or 3840×2160, exactly |
| **Render at size, master 1440p** | 1344×768 | 960×544 (8 steps) | 7:4 | ~53 s | re-sample 2x → 2688×1536, ~75 s | 2560×1440 (scaled down, 23 rows cropped) |

(Measured on an RTX 5090 with the 8-step turbo LoRA; the 1024×576 time is estimated from
its 13% more pixels than 960×544, since attention grows a little faster than the pixel
count.)

**The shape matters as much as the size.** 1344×768 and 448×256 are 7:4 (1.75), not 16:9
(1.78): H3 sizes are multiples of 32, and 1344×768 is the model's native size. A 7:4 take
delivered at a 16:9 size is either cropped (1344×768 to 4K loses 18 rows top and bottom,
1.6%) or padded (30 px of black each side); the upscale's **Output size** does either
(`--deliver 4k --fit crop|pad`; the dialog's Crop to fill / Pad with bars). 1024×576 is the
16:9 size on H3's 32 grid (512×288 its proxy), so nothing is lost at 1080p or 4K, at the
cost of a slightly smaller frame than 1344×768. The LTX and Wan targets snap a size down to
their own grid (LTX-2's is 64: a 512×288 proxy renders there at 512×256, a little wider than
16:9); the H3 targets refuse a size off theirs, naming the nearest.

- **Render small and upscale** when the episode renders on `minimax_h3_ref2va` or
  `minimax_h3_fl2va` with the latent upscaler installed (INSTALL.md, **The upscaler**), on
  `ltx2`, which upscales with its own second stage and needs nothing extra, or on
  `ltx2_ingredients` with LTX-2.3's latent upsampler installed (INSTALL.md, **For
  upscaling**; `python h3.py targets` says `upscale ready` for each). Every take you try costs under half as much, and only the ones the cut
  keeps are upscaled, once, after the cut is locked. The upscale re-samples the take from
  late in its schedule under its own prompt, references and seed, with its audio held, so
  the performance and the lip sync are the take's; it adds detail a 960×544 frame is short
  of (hands, small props, texture). It can't add a face the render didn't draw: at 960×544 a
  face in a medium-wide or wider is a few of H3's 16-pixel cells, and the re-sample
  sharpens what's there (see **Faces need room**). A live-action series that lives on
  faces and dialogue renders at 1344×768 (**Delivering 1440p** below).
- **Render at size** when shots render on Wan (`wan22_i2v`, `wan22_ti2v`, `wan22_vace`:
  they re-sample, a pixel model then their own sampler, but slowly: minutes a shot), when
  the upscaler isn't installed, or when 1344×768 is the delivery. Upscaling a 1344×768 take to 2688×1536 works
  but adds little: that frame already holds most of what the model can draw.
- Pick one per series. A single take can still render at another size: the New take
  dialog's **Size** (that take only; `h3.py render --size 1344x768`), or the shot's override
  (the inspector's **Size**, `h3.py override --size 1344x768`: every later take of that pass).
  A master with `"scale": "auto"` re-samples each take by its own size, so a cut that mixes
  960×544 and 1344×768 takes delivers one 1080p master. Switching the series size later only
  changes the takes rendered after the switch.

**Writing a new series config for someone**, ask once which setup they want, unless they
already said (a delivery size, "upscale", "fast iterations", "4K"): render at size
(1344×768), render small for 1080p (960×544), 16:9 for exact 1080p/4K (1024×576, proxy
512×288), or render at size and master 1440p (1344×768, proxy 960×544) for a live-action
series with close dialogue, where faces need the pixels. With no answer, write 1344×768: it renders on every target with nothing extra
installed. When they name a delivery size, say what shape the frame will be and whether
the delivery crops or pads it. For an existing series config, never change the size
unasked.

```json
"series": { "id": "porchlight", "title": "Porchlight", "fps": 24,
            "width": 960, "height": 544, "steps": 8 }
```

For 16:9 (exact 1080p or 4K):

```json
"series": { "id": "porchlight", "title": "Porchlight", "fps": 24,
            "width": 1024, "height": 576, "steps": 8 },
"proxy": { "width": 512, "height": 288 }
```

An upscale starts 7/8 of the way through the take's schedule (step 7 of 8), which keeps a
speaking mouth exactly as the take had it. A shot with no dialogue can take more detail with
`h3.py upscale <ep> --only sh100 --redo --detail 2` (two steps earlier: more change, check
it).

Takes on a show's own targets (and any take, by choice) can still be upscaled by the
**pixel method**: an upscale model (RealESRGAN, UltraSharp, from
ComfyUI's `models/upscale_models`) over the frames, with the take's audio copied on. It is
fast and needs nothing but the model, but it only sharpens what's there; it draws no new
detail the way a re-sample does. `h3.py upscale <ep> --method pixel` uses it for any take.

**SeedVR2** is the third method, for any take too: a one-step video restoration model
(ComfyUI's own nodes; files in INSTALL.md). It gives the sharpest single frames of the three,
in about a minute a shot, with no prompt (so nothing ties it to the take's performance but
the picture); its frames change a little more from one to the next than a re-sample's, and
the colour finish after it halves that. `h3.py upscale <ep> --method seedvr2` (the 7B;
`--seedvr2-model 3b` for the smaller one). It can also follow a re-sample in the same job,
in place of an upscale model: `h3.py upscale <ep> --then-seedvr2 --deliver 4k` (the dialog's
Then: SeedVR2), so the take is redrawn under its own prompt with its lips held, then
restored to the output size.

`--scale` is any number above 1, up to 4 (a pixel upscale lands on even sides, an H3
re-sample on the 32 grid, so 1.5x, 2x, 3x of 1024×576 all work; the LTX targets' is 2x
only). For 4x with generated detail, re-sample then upscale in one go:
`h3.py upscale <ep> --then-pixel RealESRGAN_x2.pth` (re-sample 2x, then the model 2x more).
The editor's Upscale dialog offers the same, and shows the size each choice makes. An
upscale you already have can be taken further later with the pixel method alone:
`h3.py upscale <ep> --only sh100 --method pixel --from-upscale` (the dialog's "On top of the
existing upscale").

A pixel model has a fixed factor of its own (RealESRGAN_x2 makes 2x, RealESRGAN_x4 4x), but
the upscale is resized to whatever scale you ask for: RealESRGAN_x2 at 1.5x is the model's
2x shrunk to 1.5x, which is sharp and cheap. So pair a 2x model with 1.5x or 2x and a 4x
model with 3x or 4x (a 4x model at 1.5x does four times the work to throw most of it away).
**Output size** (`--deliver 1080p|1440p|4k|WxH`, `--fit crop|pad`; the dialog's Output
size) makes the upscale exactly that size instead of a multiple: the last step that can
make any size (the upscale model, SeedVR2) is scaled to cover it (crop) or fit inside it
(pad), and the rest is trimmed or barred. A 4K master from a 1344×768 take: `h3.py upscale
<ep> --then-pixel RealESRGAN_x2.pth --deliver 4k` (re-sample 2x to 2688×1536, the model to
3840×2196, 36 rows cropped); from 1024×576 the same makes 3840×2160 with nothing cropped. A
re-sample alone keeps its own scale and is resized to the output size (a downscale is fine;
a stretch up is noted, and an upscale model after it is the better way). Every upscale of a
cut at the same output size assembles without resizing. Past 4K the output is heavy to make
and to play: up to 4096 wide upscales encode on the GPU (NVENC); past it they're written
with x264 on the CPU, which takes a couple of minutes but plays in the editor (the GPU
encoder can only write HEVC that big, which browsers often show as black).

A pixel model's output is finished before it's saved. By default its colour and tone come
from the original frames and only its fine detail from the model (upscale models shift
colour a little; this keeps an upscaled clip matching the cut around it). Two more, off by
default: **keep soft areas soft** fades the detail the model invents where the original was
out of focus, so shallow depth of field stays shallow (`--keep-soft 1`), and **grain** puts
back the film grain the model scrubbed away (`--grain 0.02` is light).

### Masters: one recipe for every shot

A **master** is the locked cut delivered at one size, every shot upscaled the same way.
The series config says how, once, in `upscale.master` (the **recipe**):

```json
"upscale": { "save_latents": "final",
  "master": { "deliver": "4k", "fit": "crop", "quality": "master",
    "finish": { "frequency_split": true },
    "targets": {
      "minimax_h3_*": { "method": "latent", "then": "RealESRGAN_x2.pth" },
      "ltx2*":        { "method": "latent" },
      "wan22_*":      { "method": "seedvr2" },
      "*":            { "method": "pixel", "pixel_model": "RealESRGAN_x2.pth" } } } }
```

- `deliver` (1080p, 1440p, 4k or WxH), `fit` (crop or pad) and `quality` (`master`: x264
  CRF 12, for delivery; `review` is quicker) are the whole master's: one size, one encoding.
- `targets` has a section per video target (an id or a glob; the exact id wins, then the
  most specific glob, then `"*"`). A take is always upscaled through its own target: an H3
  shot re-samples with H3, an LTX shot with LTX. A section takes `method` (latent, pixel,
  seedvr2), `scale`, `detail`, `pixel_model`, `seedvr2_model`, `then` (an upscale model
  file, or `"seedvr2"`, after a re-sample), `then_scale`, and the finish (`keep_soft`,
  `grain`); `finish` sets those for every section. `"scale": "auto"` (a re-sample alone)
  picks, per take, the smallest scale on the target's grid that covers `deliver`: 2x for a
  960×544 take, 1.5x for a 1344×768 one (2016×1152, scaled down to 1920 and cropped), so an
  episode that renders some shots at 1344×768 masters with no wasted pixels.
- `window` (seconds) is for a long H3 re-sample. A take longer than one window is sampled
  in overlapping windows along time (4 s with 1 s of overlap by default), so a 2x
  re-sample of 1344×768 (2688×1536) fits a 32 GB card at any length. Shorter takes are
  sampled in one pass, as before. Set a longer window if your card holds it, or `0` to
  turn windowing off. It needs the `comfyui-obvpm-timeline` node pack (its H3 Context
  Windowing); without it, a long shot's upscale says so instead of running out of memory.
- A shot can have its own recipe over its target's (a dialogue close-up kept light, a wide
  given `detail: 1`, a shot SeedVR2 got wrong sent to the pixel method): the editor's
  Upscale dialog, **Choose for this run**, then **Save as sh020's recipe**.

`h3.py master Shows\ep05 --wait` (or the cut menu's **Master…**) goes through the cut:

- a shot with no upscale, one made from an older take, or one that failed is upscaled by
  the recipe;
- a fresh upscale made with the recipe's settings is used as it is;
- a fresh upscale made with **other settings** (an older recipe, or before upscales
  recorded theirs) is **kept**, not redone: `--conform` (the dialog's Conform) redoes those;
- an upscale marked **Keep** (the take menu's **Keep upscale**, `h3.py upscale --only sh020
  --keep`) is never redone by Master, even with `--conform`;
- a shot with no usable pick, or a kept upscale at another size, is a **gap**: no master is
  made until it's fixed, unless `--allow-gaps`.

Then it assembles the master from the upscales into `Shows\ep05\master\`
(`ep05_master_3840x2160.mp4`, with `--prores` a ProRes 422 HQ `.mov` too; a master already
made gets its `.mov` without assembling again from `--prores-only`, or the dialog's
**ProRes from this master**) and writes
`ep05_master.md` / `.json`: every shot's take, target, recipe and status. It never changes
the cut: lock the picks first. Several episodes, or a whole show folder, master in one go
(`h3.py master Shows`); an episode with gaps is skipped, not half-made. `--check` shows the
plan without queueing anything.

The upscales are queued **in cut order** by default: watch them land, and if one is off you
can stop there knowing everything before it is good. For a batch you'll leave running
(overnight, over lunch), `--order target` (the dialog's Queue: Grouped by target) runs the
shots that load the same model together, so ComfyUI loads each model once instead of at
every change of target; the dialog remembers the choice.

**Writing a new series config**, add an `upscale.master` block for the delivery they named
(ask once if they didn't: 1080p is the usual; 1440p for a series rendered at 1344×768; 4K
when they say so). The patterns below are
the best value in time and quality we've measured; say which you chose and why.

#### Delivering 1080p

Render small and re-sample 2x; nothing after it. Per picked take the re-sample costs about
as much as one 960×544 render, and it redraws detail at full size under the take's own
prompt, references and seed with its audio held, which no pixel upscale of a bigger render
matches. Rendering at 1344×768 costs every take ~53 s instead of ~20 s and still needs an
upscale to reach 1080p. With about 4 takes a shot, a 100-shot episode is roughly 3 hours
this way against about 7 at 1344×768 (extrapolated from 3–5 s shots: master one real
episode before planning a season on it).

- **960×544** (proxy 448×256) when the shots are mostly H3: the re-sample makes 1920×1088,
  cropped by 8 rows (0.7%, invisible).
- **1024×576** (proxy 512×288) when many shots are on LTX, or for a touch more crispness:
  it's on both H3's 32 grid and LTX's 64 (LTX snaps 960×544 to 960×512, whose 2x is
  stretched about 5% to fill 1080), and the 2048×1152 re-sample is scaled down to exactly
  1920×1080. About 15–25% more time a take and an upscale.
- Wan shots: SeedVR2 7B (about a minute a shot), or a pixel model (about 40 s) when time
  matters more; Wan's own re-sample takes minutes and isn't worth it at 1080p.
- Some shots at **1344×768** (dialogue-heavy mediums the small render doesn't hold): give
  the H3 section `"scale": "auto"`. Those takes re-sample 1.5x to 2016×1152 (about the
  pixels of a 960×544 take's 2x), scaled down to 1920 wide and cropped by 18 rows; the
  960×544 takes still re-sample 2x. A fixed 2x would take them to 2688×1536 only to throw
  most of it away.

```json
"upscale": { "save_latents": "final",
  "master": { "deliver": "1080p", "fit": "crop", "quality": "master",
    "finish": { "frequency_split": true },
    "targets": {
      "minimax_h3_*": { "method": "latent" },
      "ltx2*":        { "method": "latent" },
      "wan22_*":      { "method": "seedvr2", "seedvr2_model": "7b" },
      "*":            { "method": "pixel", "pixel_model": "RealESRGAN_x2.pth" } } } }
```

`save_latents: "final"` (the default) lets the re-sample start from the take's latent
instead of re-encoding its video. A wide shot without dialogue that looks soft can take its
own recipe with `detail: 1`; don't raise `detail` for every shot, it can change a speaking
mouth.

#### Delivering 1440p

Render at 1344×768 and re-sample 2x; nothing after it. The take holds faces and mouths a
960×544 render can't (see **Faces need room**), and the 2x re-sample to 2688×1536 is
scaled down about 5% to 2560×1463 and cropped by 23 rows, so the master is slightly
supersampled. Measured on an RTX 5090: **about 75 s a shot**, and cleaner than re-sampling
1.5x and finishing with RealESRGAN, which took 210–230 s (the upscale model's pass at
1440p is the slow part). Uploaded to YouTube at 1440p, it also streams better to viewers
watching at 1080p than a 1080p upload does.

- **Proxy at 960×544, on the 8-step LoRA at 8 steps** (`"proxy": {"width": 960, "height":
  544, "steps": 8, "lora": "minimax_h3_ref2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors"}`):
  a proxy then looks like a final, so a good one can stand in. Its 2x is only 1920×1088,
  stretched to 1440p: give such a shot its own recipe at `"scale": 2.75` (960→2640), on a
  short shot only.
- **Long shots:** a 2x re-sample of 1344×768 is heavy. Up to about 6–7 s it fits a 32 GB
  card; a 10 s shot at 2x ran out of VRAM and stalled. Give long shots their own recipe at
  `"scale": 1.5` with `"then": "RealESRGAN_x2.pth"`.
- Keep `"scale": 2`, not `"auto"`: auto would also choose 2x for a 1344×768 take, but takes
  at other sizes would re-sample differently.

```json
"upscale": { "save_latents": "final",
  "master": { "deliver": "1440p", "fit": "crop", "quality": "master",
    "finish": { "frequency_split": true },
    "targets": {
      "minimax_h3_*": { "method": "latent", "scale": 2 },
      "ltx2*":        { "method": "latent", "scale": 2 },
      "wan22_*":      { "method": "seedvr2", "seedvr2_model": "7b" },
      "*":            { "method": "pixel", "pixel_model": "RealESRGAN_x2.pth" } } } }
```

#### Delivering 4K

The same small render, the same 2x re-sample, then an upscale model the rest of the way:
`"then": "RealESRGAN_x2.pth"` with `"deliver": "4k"`. From 960×544 the model makes an
exact 2x of the 1920×1088 re-sample (3840×2176, 16 rows cropped), so it runs at its own
factor with no resize; from 1024×576 it takes 2048×1152 to exactly 3840×2160. Measured (RTX 5090, 3 s shots,
master quality): **about 3 minutes a shot** on H3 (the re-sample ~30 s, the model's pass at
4K ~2 minutes, the encode ~11 s), nearer 4 on LTX, about 2 for a Wan shot through
RealESRGAN_x4, against well under a minute a shot for 1080p. For a 100-shot episode that's
some 5 hours of upscaling at 4K against under an hour at 1080p: master 1080p as you go and
4K when it's asked for.

- Don't re-sample 4x in one go: a single refine step on a 4x latent is unmeasured and heavy
  on VRAM. Re-sample 2x, then a model.
- The model after the re-sample is the steadiest frame to frame. SeedVR2 there
  (`"then": "seedvr2"`) gives the sharpest stills but is several minutes a shot at 4K:
  give it to the shots that earn it, as their own recipe, not the whole series.
- Rendering at 1344×768 for 4K works (re-sample 2x to 2688×1536, the model the last 1.43x)
  but costs every take more and the re-sample more, for little.
- Wan shots: a 4x model (`RealESRGAN_x4.pth`, about 3x from 1280×704), or SeedVR2 for the few
  that need it.

```json
"upscale": { "save_latents": "final",
  "master": { "deliver": "4k", "fit": "crop", "quality": "master",
    "finish": { "frequency_split": true },
    "targets": {
      "minimax_h3_*": { "method": "latent", "then": "RealESRGAN_x2.pth" },
      "ltx2*":        { "method": "latent", "then": "RealESRGAN_x2.pth" },
      "wan22_*":      { "method": "pixel", "pixel_model": "RealESRGAN_x4.pth" },
      "*":            { "method": "pixel", "pixel_model": "RealESRGAN_x4.pth" } } } }
```

**Both 1080p and 4K** from one cut: a take keeps one upscale, so make the 4K master and
scale it down for 1080p, rather than upscaling every shot twice (switching the recipe's
`deliver` makes the 1080p upscales the wrong size for a 4K master, and `--conform` replaces
them).

`fit: crop` unless they'd rather keep the whole frame with bars (`pad`).

### Post-process: the `post` block

A **post** finishes a shot's upscale, at the same size: a clean-up pass over the whole
frame, then motion blur. It is for what H3's latents leave behind even after the re-sample:
blocky edges and shadows, and moving things that smear in blocks instead of blurring.

```json
"post": { "master": { "enhance": "production", "motion_blur": 0.3 } }
```

- `enhance`:
  - `"production"` is SeedVR2 7B at the upscale's size, the one to use. It was chosen on
    Porchlights ep01 against the upscale alone, an earlier re-sample start, SeedVR2 3B, an
    upscale model and SUPIR. It cleaned edges, lettering, hairnets and faces in motion
    best, and took about 4.5 minutes a 5 s shot at 1080p. At 1440p it's about 100 seconds per second of
    footage (11 minutes for a 6.6 s shot, against 4 for its 2x re-sample), so post the shots
    that need it, or plan an overnight run.
  - `"draft"` is an upscale model at 1x: a quick sharpen that also sharpens the blocks.
  - `"cinematic"` is SUPIR: about 30 minutes a shot, frame by frame.
  - `"none"` skips the clean-up.
- `motion_blur`: a fraction of the frame interval, 0 to 1. 0.3 smooths blocky motion; 0.5 is
  a 180° film shutter. Still areas are left alone.
- A shot can have its own over it in overrides.json: `"post": {"sh040": {"motion_blur": 0},
  "sh050": {"enhance": "none"}}`.
- Face detailing (redrawing faces with an image model) was tried and dropped: it changed who
  the characters were (docs/POST_PROCESSING.md).

`h3.py post Shows\ep05` posts every upscaled shot of the cut by the recipe (`--only`,
`--redo`, or `--enhance` / `--blur` for a one-off), and the editor's take and cut menus
have **Post-process…**. The Master dialog's **Post-process** says what the master is made from:

- **Off**: the upscales.
- **Where present** (`h3.py master Shows\ep05 --post present`): each shot's fresh post where
  it has one, its upscale otherwise; nothing is post-processed and no `post` block is needed.
  The way to post only the shots that need it: upscale, scrub the cut, **Post-process…** the
  ones that need it from the take menu, then master.
- **All by recipe** (`h3.py master Shows\ep05 --post --wait`): every shot by the `post`
  block; a shot without an upscale gets its post queued right behind the upscale.

**Writing a new series config**, add a `post` block with `"enhance": "production"` and
`"motion_blur": 0.3` when the master is 1080p or 1440p. Leave it out if they haven't asked
for it, or if the episode will be mastered at 4K: a post at 4K hasn't been measured.

### Series intro and outro: the `publish` block

The show's opening and closing are two clips made once for the whole series, not shots
in each script: every episode then opens on the same picture, and a model never has to
spell the episode's name. Put them in the show folder as `_titles\INTRO.mp4` and
`_titles\OUTRO.mp4`, and every master (`h3.py master`, or Master in the editor) is made
with them: the intro, the episode, the outro, with the episode's title from its `=` line
written under the series title the clips carry, in one font, size and colour for every
episode. Without the files a master is made plain. `python h3.py publish Shows\ep05` does
the same to the review cut, into `ep05\publish\` (the editor's Publish button).

The cut's picture is copied, not re-encoded: only the two title clips are encoded, to
match it, and the result is checked frame by frame at each join (a cut that can't be
matched is re-encoded whole, and publish says why). To use other clips, name them in the
series config, relative to the episode folder:

```json
"publish": { "intro": "../_titles/INTRO.mp4", "outro": "../_titles/OUTRO.mp4" }
```

An optional `subtitle` object sets `font`,
`size` and `y` (fractions of the frame height), `color`, `glow`, and the fades in seconds:
`intro_in`, `outro_in`, `outro_out` (see `h3publish.py` for the defaults).

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

### Reference images and voices: the `refs` block

Reference images (character views, props, plates) and shot keyframes are made by an
**image model**; a character's voice sample is made by an **audio model**. The series
config can say which:

```json
"refs": {"target": "krea2", "keyframe_target": "flux2_klein_edit",
         "voice_target": "ltx2_voice"}
```

| image target | what it is |
|---|---|
| `krea2` | Krea 2 turbo, text to image (the default for refs) |
| `z_image_turbo` | Z-Image Turbo, fast text to image |
| `flux2_klein` | FLUX.2 Klein 9B, text to image |
| `flux2_klein_edit` | FLUX.2 Klein 9B with up to 4 reference images (the default for keyframes when it is installed; otherwise keyframes use the refs model) |
| `flux_kontext` | FLUX.1 Kontext dev, with one reference image |
| `minimax_h3_still` | MiniMax H3 — the video model, rendering 5 frames and keeping the first, with up to 9 reference images. No second model stack to install if you already render on H3 |
| `qwen_image_21` | Qwen-Image 2.1 — one model for both jobs: text to image for a character's own views, an edit of up to 16 reference images for a variant's. Takes a real negative prompt, and its cfg, scheduler and denoise are ordinary sampler settings |

| audio target | what it is |
|---|---|
| `ltx2_voice` | LTX-2.5 audio-only: one speaking voice from the character's `voice` line and a line from the script (the default for voices) |

A **variant**'s view (a subject with `of:`) is generated as an edit of the same view of the
character it is a variant of, on any target that reads reference images — so its back panel
is drawn from their back panel, and the face carries. On a text-to-image target there is
nothing to edit from, so it falls back to drawing the variant from its `design` on the
base's seed; write that sentence as close to the original's as the change allows, because
there the wording is all the identity there is.

The editor can choose per episode (kept in `overrides.json`, not here) and per ref, and a
generate request can name one; most specific wins. `python h3.py targets --kind image`
(or `--kind audio`) says which are installed and what to download for the rest. A ref is
worded the same whichever model makes it.

A character with no `voice_sample` still has a voice ref: generating one and picking it
writes `refs/voices/<id>.wav` **and** adds the `voice_sample` line to `series.json` for
you (the only thing the editor writes there). `python h3.py refs <ep> --voices` does the
same from the command line, and `--from-take sh020:3:1.2-6.4` makes a sample out of a
line the model already spoke in a take, with no model at all.

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
quality. The plate is generated from that sentence. **Name the sources**: the pendant lamp
over the table, the chandeliers, the TV's glow, the streetlight through the blinds. A room
lit by things you can point at renders as a lit set, with pools of light and shadow
between them; "warm lighting" renders as an evenly lit showroom.

### Writing the `look` line

`style.look` is the first sentence of every shot's description, so it is the series'
photography. Write it the way a director of photography would brief a crew: what the lens,
the focus, the light and the film stock do. Adjectives about quality ("cinematic",
"beautiful", "high quality") give the model nothing to draw.

Weak: *"a cinematic prestige drama with moody lighting"*.

Strong: *"a naturalistic live-action prestige drama shot on 35mm anamorphic lenses: shallow
depth of field with the background falling into soft bokeh, low-key light from the practical
lamps in each room, deep soft shadows with lifted blacks, muted desaturated color and fine
film grain"*.

Each clause is something the model can see: shallow focus and bokeh separate the subject
from the room and soften what the model draws worst, low-key practical light gives the frame
contrast and shape, and grain gives the image the texture of film. Keep it to one sentence;
the per-shot light belongs in the location descriptions and the action.

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

**Write the angles as one place.** Each plate is drawn from its own `description`, so unless
those sentences share fixed features they describe several rooms with the same name rather
than one room from several sides. Anchor every angle to the same things: *"the kitchen from
the hallway door, sink under the window on the left, the living-room arch behind camera"* and
*"the kitchen from the sink, the hallway door on the right, the arch beyond it"*. That is what
makes the geography hold together across a cut.

**Tie the angles to one master with `of`.** Words alone still give each angle its own
walls and furniture. Give one entry the plate you like (the master) and mark the others as
angles of it:

```json
"ambulance_bay_night":    {"description": "the ambulance bay of a county hospital at night ...",
                           "plate": "../refs/_bg/ambulance_bay_night.png"},
"ambulance_bay_on_drive": {"of": "ambulance_bay_night",
                           "description": "looking back down the covered drive from the doors ...",
                           "plate": "../refs/_bg/ambulance_bay_on_drive.png"}
```

- An angle inherits nothing. It keeps its own `description` and `plate`, and shots still name
  it with `plate:`.
- On an edit model (Klein, Qwen, Rapid AIO, H3 still), an angle's plate is generated **from
  the master's plate**: the same building, materials and light, with only the camera moved.
  On a text-to-image model (Krea 2) it is drawn from its words as before.
- The Refs tab lists the angles under their master.
- An angle's master must be a location that isn't itself an angle.

### Two people talking need an angle each

This is the one that spoils scenes most often, and the build warns about it:

```
sequence sq11: Skye and Dr. Walter each get their own shot on the same plate (cafe_couch), so
they are drawn against one background from one view and the cut reads as a single camera
rather than a reverse angle. Give the location an angle per speaker and name it with `plate:`.
```

One plate is one picture. Two singles built on it are drawn against the same background from
the same view, so cutting between them looks like one locked-off camera that people step into
and out of — not a conversation. No wording fixes it, because the plate is a picture and the
prompt is only words.

Author the scene as three entries and name one per shot:

```
cafe_couch          the whole seating area, both of them in it — the establishing shot
cafe_couch_on_skye  past Walter's shoulder to Skye on the left of the couch, window behind her
cafe_couch_on_walt  past Skye's shoulder to Walter in the armchair, the counter behind him
```

```
## sh768
who: skye, walter
plate: cafe_couch
size: medium
dur: 3.04
They settle in with their cups.

## sh772
who: skye
plate: cafe_couch_on_skye
size: close
dur: 2.33
Skye watches him over the rim of her cup, her eyes off to frame right where he sits.
SKYE: You're early.
```

**Keep them on one side of the line.** Decide who sits on which side and keep it: if Skye is
on the left of the two-shot, she stays screen-left in her single and looks off to frame
right, and Walter looks off to frame left. Write that into the **action** line — *"her eyes
off to frame right where he sits"*, *"he answers without looking up, his eyes down and to
frame left"*. There is no field for it; the action sentence reaches every target's prompt and
is where a real script would put it anyway. Without it a model does what it was trained to do
and talks to the camera.

The same applies to a character alone in a room who is meant to be aware of someone off
screen: say where they are looking, and say that it is not at the camera.

### A wardrobe change is a new subject

The same idea for people. A character's clothes live in their `design` sentence and in their
model sheet, so a character who changes clothes is a second subject with `of:` — a
**variant** — not a note in the action line:

```json
"gina": {
  "kind": "character", "name": "Gina", "pronoun": "her",
  "design": "a woman in her forties, dark hair cut short, a grey mechanic's coverall over a black tee, steel-toed boots",
  "sheet": "refs/gina/gina_sheet_4panel.png",
  "voice": "low and dry, never hurried",
  "voice_sample": "audio/voices/gina_sample.wav"
},
"gina_towel": {
  "of": "gina",
  "design": "a woman in her forties, dark hair cut short and soaked flat, a white bath towel wrapped and tucked at the chest, bare feet"
}
```

A variant needs exactly two lines: `of:` and its own `design`. Everything else — `kind`,
`name`, `pronoun`, `voice`, `voice_sample` — is inherited, so one character still has one
voice, and a `sheet` path is derived from the original's (`refs/gina/gina_sheet_4panel.png`
becomes `refs/gina/gina_towel_sheet_4panel.png`). Give it its own `sheet` line to put it
somewhere else. Generate that sheet like any other reference; starting from the original's
approved views holds the face far better than generating it cold.

In the script, name the variant where it is on screen and go on calling the character by
their name:

```
## sh120
who: gina_towel
size: medium
dur: 3.04
Gina leans out of the bathroom door, one hand on the frame.
GINA (low): Don't come in.
```

`who:` takes the variant; `GINA:` still speaks, and the line is given to whichever of her is
in the shot. A shot names one or the other, never both, and never two variants of the same
subject — cut the shot where the change happens.

**Why not a `wear:` line on the shot?** Because the reference sheet would still show the old
clothes, and the prompt would have to argue with the picture. H3 preserves a subject as a
whole — its `fully_preserved` marker covers face, hair, proportions *and* wardrobe — so the
only way to contradict the sheet is to weaken the whole subject, which loosens the face in
order to change the clothes. A variant keeps the identity lock and swaps the picture. It also
costs nothing on the other targets: LTX-2 and Wan have no reference images and read the
`design` sentence, which is already the variant's.

A variant is for a state a shot is *in*, not a change it *makes*.

### Never show the change itself

No video model today can take a hat off, pull on a glove or shrug out of a jacket and keep it
off. The item comes back, doubles, or melts into the hands, and the next shot puts the sheet's
version straight back on him. So **a shot's wardrobe is fixed from its first frame to its last,
and it is what the subject's sheet shows.** Don't write dressing or undressing into an action
line: not taking off a hat, gloves or glasses, not hanging up a coat, not stepping into
coveralls or tying on an apron. Small things matter most, because they are the easiest to
forget. A mesh glove pulled off in one shot is back on in the next, because nothing told the
next shot it was gone.

When the story needs the change, cut around it:

- **Start it, then cut.** Show only the beginning (his hand going to the brim, fingers on the
  zipper) and end the shot there. The next shot is already in the new state.
- **Land on an insert.** Cut to the item or the hands, not the face: the hard hat held against
  his chest, framed below the chin, or a bare forearm hanging the navy jacket on a locker hook.
  Make the item a prop (`with: hard_hat`) and keep the character out of `who:`, or frame it
  tight enough that only the hands show. An insert has no face to drift and needs no new sheet.
- **Cut away.** Go to the other character's line or reaction, then come back to the variant.
- **Change off screen.** End one scene in the old clothes and open the next in the new ones.
  Luis walks into the locker room in his jacket, and the next scene finds him on the line as
  `luis_plant`.
- **Don't change at all.** The gesture is often not needed. Glasses can be looked over or
  pushed up instead of taken off, and a man can fold his hands and wait with his hat still on.

Once the new state is on screen it holds. Every later shot of that character names the
variant until another cut brings the old clothes back, so add the variant (a design sentence
that says what is gone: "no hard hat, a hairnet over short grey hair") before writing the
shots that need it. Its sheet is an edit of the original's views, so it's cheap.

For example, the hat coming off in Kemp's office. Written as an action, the hat is on again
two shots later:

```
## sh320
who: luis_plant
Standing just inside the door, Luis takes off his hard hat and holds it in both hands, ...
```

Instead, let Kemp's shot before it be the cut-away, and have Luis enter his next shot already
bareheaded, holding the hat as a prop:

```
## sh320
who: luis_plant_bareheaded
with: hard_hat
Standing just inside the door, Luis holds his hard hat in both hands against his chest, ...
```

and every later shot of him in the office is `luis_plant_bareheaded` too.

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
| `who: a, b` | characters on screen, in this order (on H3 Ref2VA they become `<Picture 1..3>`); `cast:` is the same |
| `with: x, y` | props and vehicles, after the characters; `props:` is the same |
| `size: mcu` | shot size, from extreme close-up to extreme wide (`ecu`, `cu`/`close`, `mcu`, `ms`/`medium`, `cowboy`, `mws`, `fs`/`full`, `ws`/`wide`, `ews`; the full names work too). Opens the shot's prompt as its framing ("A medium close-up frames… from the chest up"), and decides how much of the location and of the sheet is used. See **Faces need room** |
| `plate: street_gate` | this shot's angle; defaults to the sequence's location |
| `dur: 3.04` | duration in seconds, from the grid below (`duration:` is the same) |
| `dur: auto` | derive the duration from the dialogue at this shot's pace |
| `dur: model` / `dur: model 3-8` | let the video model choose the length when it renders, optionally between 3 and 8 seconds; see **Letting the model time a shot** |
| `audio: 3.10-7.40` | window on a locked dialogue mix; sets the duration instead |
| `pace: slow/normal/fast` | 3.6 / 4.4 / 5.4 syllables per second |
| `camera: pushes in…` | camera move, **without** the words "The camera" |
| `sound: …` | ambience and physical action sounds |
| `music: …` | audience-only score; omit for none |
| `extras: …` | other people in frame, described; see **Crowds and extras** |
| `text: OPEN 24 HOURS` | on-screen text the picture must show (a sign, a note, a screen), quoted into the prompt. Never the series title or credits: those are added in the edit, not rendered into a shot |
| `policy: generate` | this shot's audio: `generate` (the model makes all of it), `dub` (lip sync to the recording's `audio:` window, which becomes the soundtrack), `dub_keep_foley` (the same, keeping the model's sound effects under it) or `clone` (spoken in each speaker's `voice_sample`). Without it, dialogue shots follow `audio.mode` and silent ones generate |
| `retention: partially_copy` | how closely a dubbed shot copies the recording: `fully_copy`, `partially_copy` or `reference` (H3 Ref2VA; dub shots only) |
| `model:`, `lora:`, `steps:` | per-shot render overrides; also valid under a `#` header |
| `profile: dialogue_close` | a render profile from the series config; also valid under a `#` header |
| `target: ltx2` | the video model for this shot or sequence (`minimax_h3_ref2va`, `ltx2`, `ltx2_ingredients`, `minimax_h3_fl2va`, `wan22_i2v`, `wan22_ti2v` or `wan22_vace`); see below |
| `first: continuity` / `last: generate` | how this shot's first / last keyframe is made: `continuity`, `generate`, `import`, `none`, or a path to an image; also valid under a `#` header (the default of its shots); see **Keyframes** |
| `NAME: line` | dialogue from someone on screen (the name is a character's key, in capitals) |
| `NAME (breathless): line` | a delivery direction, written into the prompt |
| `NAME (V.O.): line` | voiceover: speaks, is not drawn, costs no reference slot |
| `NAME (O.S.): line` | off-screen: in the space, outside the frame |
| `NAME (V.O., on the truck radio): line` | a voice marker and a delivery together; the note describes the voice and is never spoken |
| `continuous: yes` | under a `#` header: the sequence is one unbroken take |
| `// text` | comment |

Any other line is action prose. Prose under a `#` header before its first shot is a
scene-setting note and is ignored. A line under a `#` header (`model:`, `lora:`, `steps:`,
`profile:`, `target:`, `first:`, `last:`) is the default for every shot in the sequence; a
shot's own line beats it.

### Rendering a shot on LTX-2

`target: ltx2` on a `##` shot, under a `#` header, or in a profile renders that shot (or
sequence) on LTX-2.5 instead of the series target; any other name is an error. One episode
can mix the two: the build writes the LTX shots to their own shotlist, and the editor, the
render command and the review cut treat the episode as one. What changes for an LTX shot:

- **Your refs are used when they are there.** Everyone in `who:` / `with:` is described in
  words from their `design` and the place from the location's `description`, so the
  sentences carry the look on their own — and when the shot's subjects and plate have
  picked refs on disk **and** the LTX-2.5 ingredients IC-LoRA is installed, those refs are
  also drawn onto one reference sheet that conditions the clip, exactly as
  `target: ltx2_ingredients` does (see below for the sheet's layout). It is decided when
  the shot is queued, never at build time, so nothing in the shotlist changes; the take
  says which panels it used, keeps the sheet as `<shot>_tNN_refsheet.png`, and re-picking a
  view marks it stale. Without the refs or without the LoRA the shot renders from the
  prompt alone, as it always did. **No panel is ever required**: a missing one just leaves
  that element off the sheet. To turn the sheet off for a shot, sequence or profile, write
  `lora: none` (`"loras": ["none"]` in a profile) — the opposite of `ltx2_ingredients`,
  where the IC-LoRA is put back because the target is pointless without it.
  A first- and/or last-frame keyframe (`refs/shots/<shot>/first.png`, `last.png`, imported
  in the editor's Refs tab) pins the picture when you have one; without keyframes and
  without a sheet the shot is text-to-video.
- **Its own grid:** `8k + 1` frames (9, 17, 25, … 73 = 3.04s, 97 = 4.04s at 24fps), up to
  about 20 seconds. The same `dur:` snaps to a slightly different length than on H3.
- **Sound is always generated with the picture.** LTX takes no voice sample and no recording:
  a shot the series would `clone` or `dub` renders with `generate` instead, and the build
  and the take say so. Each speaker's `voice` line is what shapes the voice.
- **One paragraph of prose**, written for you: the look, then the framing and the camera,
  then who is in frame and what they do, the lines with who says them and how, then the
  sound and music. Write `camera:` for a move you want; without it the camera stays still.
- **Size** comes from the series config's pass blocks, snapped down to a multiple of 64 and
  kept under 1 MP (1344×768 stays; a 480×272 proxy becomes 448×256). Its model, LoRA
  and steps are the target's own: a series written for H3 doesn't hand them H3's.
- **Two transformers: fast by default, or a quality pass.** The default is the *distilled*
  LTX-2.5 transformer, which was distilled for a fixed 8-step schedule at guidance 1 — a
  `steps:` line can't change that, and the take says so. Name the *dev* (non-distilled)
  transformer as the model and LTX-2 renders the **quality profile** instead: 30 steps with
  real guidance, for crisper lines and steadier faces. It costs very little — the extra
  steps run on the half-size base pass, and a 4-second 768×512 shot measured 39 s either
  way. Set it wherever a model is set — a profile for a kind of
  shot, a `model:` line on a shot or sequence, the series config's `series` block for the
  whole episode, or the editor's model picker for one shot:

  ```json
  "profiles": {
    "quality": { "target": "ltx2",
                 "model": "ltx-2.5-22b-dev-transformer-comfy-int8-convrot.safetensors" }
  }
  ```

  Then `profile: quality` on a `##` shot or a `#` header. A `steps:` line on such a shot
  wins over the profile's 30. The dev file is optional: without it installed, `h3.py
  targets` says the feature is off and everything else still renders.

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
- **Its frame rate, and keeping shots short.** All three render at **24 fps**. The 14B
  models (`wan22_i2v`, `wan22_vace`) are specified at 16 fps, but their motion is paced for
  24: at 16 a take plays as slow motion, so h3pipe times and saves them at 24. Lengths are on
  a `4k + 1` grid: 3 s is 73 frames. The 14B models were trained on 81 frames, **3.4 s** at
  24, and their prompt adherence falls off after that: a longer shot renders with a
  warning, and past 161 frames (6.7 s) it must be split. **Write Wan shots of 3 s or less**,
  and cut a longer action into two shots. (A take rendered before this at 16 fps keeps its
  own rate: assemble and Play all time it by what it recorded.)
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

**A render keeps continuity current, and a whole chain queues at once.** A
`first: continuity` shot's first frame is cut when its render **starts**, from the take
the cut uses at that moment for the shot before it (the H3ContinuityFrame node in its
graph). ComfyUI runs its queue in order, so queue sh080, sh090 and sh110 together and each
starts on the new last frame of the one ahead of it; the model swaps between targets on the
way. The take's sidecar records what it started from (`continuity`). A chain is queued in
cut order whatever order it was asked for. Before queueing, a keyframe that doesn't exist
yet is cut from the previous shot's current take so the graph has a frame to wire; a
continuity shot whose previous shot has no take at all and none coming is an error. The
Refs tab marks a keyframe **out of date** when the previous shot's take has changed since
it was cut; a keyframe you imported or generated is left alone. "Before" is the cut's
order, not the script's: move a shot on the timeline and its continuity comes from its new
neighbour.

**Continuity is for a shot that picks up the previous one's picture**: the same framing
carrying on, or a match on action. After a real cut (a new subject, a new angle, a
different size) the previous shot's last frame is the wrong opening picture, so write
`first: generate` on that shot. A good pattern is `first: generate` under the `#` header and
`first: continuity` on the shots that continue.

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

Every video model takes only certain frame counts, and a `dur:` between two of them rounds
**up**, so you pay for frames you throw away. The grid is the target's: `17k + 5` frames on
the H3 targets, `8k + 1` on LTX, `4k + 1` on Wan (at 24 fps; best kept to 3 s). The build
snaps each shot to its own target's grid, and `--check` warns about wasted padding.

Write for H3's grid, the default; the other grids are fine-grained enough that these
lengths land close on them too. At 24fps these are free on H3:

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

This is the easiest way to wreck an episode, and it does not announce itself: a model that
speaks (H3, LTX) will fit any line into any window by speeding the delivery up. The words
are all there, the lip sync is fine, and the performance is gone. On Wan, which acts the
lines silently, the recording laid in at the edit still needs the room.

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

Every target inserts the `camera:` line after the words "The camera", so write the verb
phrase only. Without one, the camera holds still. The vocabulary below is H3's training
vocabulary; the other models read it as plain English.
Compose from **motion + amplitude + speed**; the last two are optional.

**Motion:** zooms in / out · pushes in / pulls out · pans left / right · trucks left / right ·
tilts up / down · pedestals up / down · arcs around · tracks · holds a static shot · shakes
slightly / strongly · takes the subject's POV · rolls clockwise / counterclockwise

**Amplitude:** with small amplitude · with large amplitude
**Speed:** at slow speed · at fast speed

"Arcs around them with large amplitude at fast speed" beats "swoops around dramatically",
because it names a move the model was trained on.

Film terms translate into that vocabulary:

| You mean | Write |
|---|---|
| dolly in / dolly out (back) | `pushes in…` / `pulls out…` |
| slide, crab | `trucks left…` / `trucks right…` |
| pivot, swivel | `pans left…` / `pans right…` |
| whip pan | `pans right with large amplitude at fast speed` |
| crane / jib up or down | `pedestals up…` / `pedestals down…` |
| orbit | `arcs around <name>…` |
| follow, lead | `tracks <name> as she walks…` (in front of or behind, in the action) |
| handheld | `shakes slightly` |
| over the shoulder | not a move: frame it in the action, "over Ada's shoulder, Bo faces camera", with both in `who:` |
| rack focus | not a move: say it in the action, "the focus shifts from the cup to Ada's face" |

Staging (where people stand, who faces camera, how a scene's angles cut together) is the
director's craft and is best learned from a book like Christopher Kenworthy's *Master
Shots*; this guide covers only how to say the result to the model.

On a wide full of big architecture (a plant, a street front, a church), prefer holding still,
a pan or a slow push over a track, truck or arc. A camera that moves through the space makes
the model redraw the buildings as it goes, and they build and unbuild themselves on screen.

## Breaking a scene into shots

A scene with three paragraphs of action is six to ten shots, and deciding where they fall is
the job.

**Cut when** the subject changes, a new beat lands, someone new speaks, time skips, or the
framing must change materially. **Don't cut** when only the distance shifts a little — use a
camera move. A cut should bring new information; a cut to the same thing slightly closer is a
jump cut, and it will render as one (see **Cutting within a location**).

A workable rhythm for a dialogue scene: wide establishing → medium two-shot for the exchange →
close on the reaction that matters → back out. Keep the wide short and empty of anything
that has to read on a face (see **Faces need room**).

**Open every shot on movement.** The model has no memory of the previous cut, so a shot whose
action describes a *position* renders that position and then looks for something to do with
it. Write the state change: "Dex swings his boots onto the desk and settles back", not "Dex
sits with his boots on the desk".

### Faces need room

`size:` is written into the prompt as the shot's framing (`A close-up frames <Subject 1>…`,
`A medium shot frames…`, `A wide shot takes in…`), so it is what the model frames, not a
label. Choose it by what the shot has to show:

| `size:` | Framing | Holds | Use it for |
|---|---|---|---|
| `ecu` | extreme close-up | one detail: the eyes, a hand on the latch | an insert; tension |
| `cu` (`close`) | close-up | the face, chin to hairline | the reaction or line that matters |
| `mcu` | medium close-up | chest up | **most dialogue**: face and a little body language |
| `ms` (`medium`) | medium shot | waist up | two-shots, business at a counter |
| `cowboy` | cowboy shot | mid-thigh up | standing confrontations, a hand at the hip |
| `mws` | medium-wide shot | knees up | walking and talking, a figure with their surroundings |
| `fs` (`full`) | full shot | head to toe | a whole-body action: a trip, a dance step, a fall |
| `ws` (`wide`) | wide shot | the subject in the room | geography, entrances |
| `ews` | extreme wide shot | the subject small in a big setting | scale, isolation, establishing a place |

The full names work too (`size: medium close-up`), and so do `ls` (long shot) and `els`. The
in-between sizes put what the frame holds into the prompt ("from the chest up"), so a model
that doesn't know the term still frames it. For the plate and the sheet, `ecu`/`mcu` act like
a close-up, `cowboy`/`mws` like a medium, and `fs`/`ews` like a wide.

**A face needs pixels to have a face.** H3 draws in cells of 16×16 pixels. On a wide of a
standing person, the face is a few cells across, too few for eyes and a mouth: it comes out
soft and smeared, and an upscale sharpens the smear rather than finding the face. At 960×544
it is worse than at 1344×768. Measured on a 1344-wide full-body walk, the face was 50 pixels
tall: three cells.

- **Dialogue, reactions, and anything the audience must read on a face: `mcu`, `cu`, or
  `ms`.** A medium close-up gives the face a few hundred pixels at 960×544; a close-up fills
  the frame with it.
- **Wides (`fs`, `ws`, `ews`) carry geography, entrances, crowds and big physical action**,
  where nobody's expression has to read: a figure crossing a room, a back to camera, two
  people seen from across a lot. Don't hold a wide on a face and expect the face to be there.
- **Cut in for the face.** A fall, a collision or an entrance plays as `fs` for the action →
  `cu` on the face that reacts → `mws` or `ms` for the result, not one wide shot that holds the
  face at a distance.

The prompt also scales the location to the framing: a wide describes the whole place around
the subjects, a medium the part behind them, and a close-up only a magnified, out-of-focus
slice of it. That is why a close-up on H3 has the room behind the face, softly, and not a
studio backdrop.

### Cutting within a location

Every shot is generated on its own, so a shot never continues the one before it: it poses
everyone from scratch. Cut between two shots that look alike and the eye reads it as a
glitch, with people jerking into new positions or appearing out of nowhere. That's a jump
cut. Two consecutive shots with **the same people on the same plate** need one of these
between them:

1. **A new angle.** Give the second shot another `plate:` of the place. A real change of
   camera position (film's 30-degree rule) makes the new pose read as a new view. This is
   the cheapest fix and why a location wants several angles.
2. **A size two or more steps away**, along `ecu` `cu` `mcu` `ms` `cowboy` `mws` `fs` `ws`
   `ews`: `mws` → `mcu`, `ms` → `cu`, `ws` → `ms`. One step (`ms` → `mcu`, `fs` → `ws`) reads
   as the camera twitching.
3. **A cutaway**, then back: the other character's reaction, an `ecu` insert of hands or a
   prop, what she's looking at. Under a second is enough (H3's shortest take is 0.92 s), and
   after it the return can be in any pose.
4. **A cut on action.** Open the second shot mid-movement ("already turning to the door"),
   not at rest: movement across the cut hides the change of pose. It helps the other three;
   on its own it seldom saves a cut.
5. **Continue the picture** when the second shot really is the same moment carrying on:
   `first: continuity` on H3 FL2VA (`target: minimax_h3_fl2va`) opens the shot on the
   previous shot's last frame, so nothing re-poses (see **Rendering a shot on H3 from
   keyframes** and **Keyframes**). On H3 Ref2VA, `continuous: yes` under the `#` header chains
   a whole sequence instead, at 22 frames a shot after the first. Either way, each take is
   upscaled on its own, so the frame where one hands off to the next can differ slightly
   between the two upscales.

`h3.py check` warns about consecutive shots that do none of the first two and don't continue:
the same people, the same plate, sizes less than two steps apart, the second not
`first: continuity` and the sequence not `continuous`. It can't see a cutaway or a cut on
action in the action text, so treat it as a question to answer, not an error.

Worked through: Ada at the counter, then closer for her line. As written, a jump cut:

```
## sh020
who: ada
size: ms

## sh030
who: ada
size: mcu
```

With Bo's reaction between them, and the return two steps tighter:

```
## sh020
who: ada
size: ms

## sh025
who: bo
size: mcu

## sh030
who: ada
size: cu
```

### Every shot starts from an empty plate

Plates are drawn empty, and the model sees one shot at a time. Whatever the scene established
three cuts ago — who is in the car, the diners at the other tables, that Harold sat down — is
gone unless this shot says it again.

- **Everyone physically in frame is in `who:`**, even mostly unseen: the driver of a two-shot
  in a car is both people, the passenger's shoulder included. Leave one out and the plate's empty
  seat is all H3 gets, so they vanish for a shot and come back on the next. The same goes for
  anyone the action touches or looks at in frame: "Harold reaches for Cora's elbow" with no
  Cora in `who:` gets an invented stranger.
- **Repeat `extras:` on every shot of a populated room**, closes included ("diners at the far
  tables, blurred behind him"). It does not carry over from the previous shot, and without it
  the room is empty on the cut.
- **Say the posture.** A plate of a kitchen table is an empty table, and the sheet shows the
  character standing. Open on the posture plus the movement: "Seated at the kitchen table,
  Harold turns his head slowly…", not "Harold turns his head slowly…".
- **Give every moving object a hand.** "The briefcase opens" opens by itself; "Mills lifts the
  lid of the briefcase" puts Mills in `who:` and in frame.
- **Settle carried props.** If a character's `design` has a bag on the arm and the shot seats
  them, set the bag down in the action ("her handbag set beside the ledger"), or H3 draws a
  second, standing copy of them to carry it.
- **Write to the plate's angle.** A plate shot from the visitors' side sees the back of the
  monitor; if the screen matters, have someone turn it.
- **One or two physical steps per shot.** Crossing a lot, climbing steps and knocking is three
  shots, not one 4-second shot; crammed motion smears into interlace-like lines. Start the shot
  where its plate is.
- **Pick people up where the last shot left them.** If sh160 parks the truck by the building,
  sh170 doesn't walk him across the lot. Name the spot in the first shot ("parks at the far
  end of the lot") so the next one can start from it.
- **The crowd is already there.** On an empty plate, people written as arriving materialise
  in the middle of the shot. Write them as present from the first frame: "the line already
  running, a worker at every station".

### Vehicles in motion

A moving vehicle with a loose description wanders: it swerves toward the people in frame,
speeds up when it should stop, and smears.

- **Give it one clean path**: direction across frame, lane and speed. "The pickup rolls slowly
  left to right in the near lane, never leaving the road."
- **Arriving and getting out are separate shots.** One shot drives in; the next opens with the
  door already opening.
- **Parking is its own beat**, and the shot starts with the vehicle already slowing. Say where
  it stops, so the next shot can pick up from there.

Reserve `camera: holds a static shot` for reactions where stillness is the point. On a wide
you have just cut to, a slow push gives the space depth, and costs nothing extra.

Shot IDs go `sh010, sh020, sh030`, leaving gaps so you can insert `sh015` later. Renumbering
changes seeds and invalidates renders. An ID is unique across the whole episode, not just its
sequence: continue the numbering (`sh110` in the next sequence, or keep counting) rather than
restarting at `sh010`.

**A shot's ID is its identity**, beyond its seed: its takes, its overrides and everything the
cut says about it (the pick, trims, lock, audio source, and whether it's left out of the cut)
are kept by ID. So when revising a script:

- **the same shot, rewritten:** keep its ID. Its takes are flagged stale where the prompt
  changed, and the cut's decisions about it still apply;
- **a different shot:** give it a new ID, and never reuse an ID you removed; the old one's
  takes and cut decisions (a pick, trims, "left out") would land on the new shot;
- **a shot added between two others:** an in-between ID (`sh065` between `sh060` and `sh070`),
  so every other shot keeps its ID;
- **a shot removed:** just delete it; its takes stay on disk, and its cut entry is skipped.
  To try the cut without a shot, leave it out of the cut in the editor instead of deleting it.

## Reference slots (H3 Ref2VA)

This section is about the default target, `minimax_h3_ref2va`. The other targets take no
slots (their limits are under **Rendering a shot on …**), but the three-subject rule is a
good habit everywhere: a shot about more than three things is usually two shots.

Every shot hands H3 four images. Slots 1–3 are the subjects — characters first, then props
and vehicles, in the order `who:` and `with:` list them. **Slot 4 is always the location
plate.**

That caps you at **three subjects on screen**, and a fourth is an error, not a silent drop.
When a scene has four characters, stage one out of frame, split the shot, or describe the
extra in the action text without referencing it. Crowds cost nothing that way — they just
won't look the same from shot to shot, unless they are a group (one slot for all of them;
see **A group: the same extras every shot**).

A `(V.O.)` or `(O.S.)` speaker costs no slot, is never drawn, and needs no sheet — only a
`voice` in the series config. A shot may have no `who:` at all: an establishing plate with narration
over it.

### Crowds and extras

On H3 Ref2VA a shot's prompt states that exactly one person appears in it per referenced
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

### A group: the same extras every shot

`extras:` people are invented fresh every shot, so they change sex, size and colour from
cut to cut. When the same few people recur — the ladies watching from the edge of the
ballroom, a family at the next table — make them one **group** subject: a character with
`members`, whose picture holds all of them.

```json
"ladies": {
  "kind": "character",
  "name": "the ladies",
  "members": 4,
  "design": "four young women in Regency ball gowns: one in pale blue with dark curls, one in rose with a feathered headband, one in white with red hair, one in green silk with long gloves",
  "sheet": "../refs/ladies/ladies.png"
}
```

Cast it like anyone else (`who:` or `with:`). It takes one slot however many people it
holds, which is how a shot gets more than three people who stay the same: two named
characters and a group of four is three subjects. The prompt defines it as a picture of N
different people and counts its members in the shot's headcount ("Exactly six characters
appear in this shot: <Subject 1>, <Subject 2> and the four members of <Subject 3>"), which
is what keeps H3 from drawing the same woman twice. Put where they are in the action text
("the ladies watch from the far wall, soft in the background").

- **The picture is one whole image** of all of them (`sheet_panels: 1` is implied; a group is
  never a four-panel sheet). Generated, it is a row of everyone head to feet, apart and facing
  the viewer, at the plate's size. A picture you supply works the same way; keep every face
  and outfit visible, since a person half-hidden in the reference is one the model invents.
- **Describe each member** in `design`, with differences in age, build, hair and colour. A group
  of four identical gowns is read as one person four times.
- It works on every target. H3 Ref2VA defines it in the headcount. LTX-2, Wan and H3 from
  keyframes call it "N different people, each appearing once" in their prose, and their
  reference sheets carry its picture whole, never cut to one panel.
- A group can speak (`voice` is one voice for all of them: a murmur, children giggling).
- Twelve is the limit. A crowd bigger than that belongs in `extras:`.

Two habits go with it. **Keep people out of the plate**: a location described as "full of
masked dancers" is rebuilt as figures that compete with the subject and feed the duplication
— describe the empty space and let the far background fall off into bokeh. And **name the
subject in the camera move**: "arcs around them" leaves the model to decide who "them" is.

**Extras in the lead's wardrobe take the lead's face.** The prompt already tells H3 the extras
look nothing like the subject, but twenty white smocks and hard hats around a man in a white
smock and hard hat still turn into twenty of him. When the extras share the lead's uniform,
vary them in `extras:`: men and women, different ages and builds, hairnets instead of hard
hats, faces turned away or soft in the background.

On H3 Ref2VA, `size:` also decides what the plate means (besides the framing words; see
**Faces need room**): on a wide or medium the room's layout
is kept, and on a close-up the background is a zoomed-in crop of the plate — the part of the room
right behind the subject — and never the plain backdrop of the character sheet. A close-up
still leaves the room's layout to the shots around it, so stage the space on a medium first.

Add a prop to `with:` only when its exact design matters and recurs. A generic mug is better
described in the action text, because every referenced prop spends a slot.

## Sequences

A sequence is one location, continuously. A new place — or the same place at a different time
of day — starts a new `#` sequence. Intercutting is just alternating sequences: `# sq03
kitchen`, `# sq04 phone_room`, `# sq05 kitchen`.

Keep a sequence intact and vary the shots with `plate:` rather than splitting a scene into
one-shot sequences, which re-seeds every shot in it.

`continuous: yes` chains a sequence into one unbroken take on H3 Ref2VA, carrying motion
across the joins. Use it rarely: it costs 22 frames per shot after the first, which is 30% of a 3-second shot.

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

(`python h3.py check <episode folder>` runs both.) The check compiles every shot for the
target it renders on, so it also reports each target's limits.

Errors name the line and quote it. Common ones: a name in `who:` that is not in the series config; a
location with no plate; an ALL-CAPS `NAME:` line for someone who cannot speak (usually a typo,
which would otherwise become action prose); more than three subjects on H3 Ref2VA; a shot
with neither `dur:` nor `audio:`; `dur: auto` on a shot with no dialogue; a `dur: model`
range that isn't `min-max` seconds with min below max; a `target:` or `profile:` name that
doesn't exist; a `first:` / `last:` that isn't a method or an image path. Warnings cover
crammed dialogue, wasted grid padding, and what a target can't do (a dialogue shot on Wan,
a `clone` shot on LTX).

A script that does not compile is not a draft, it is a bug. Fix and re-run until it is clean.
