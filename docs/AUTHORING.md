# Writing an episode for h3pipe

Everything the renderer does comes from two files you write by hand: `series.json`, the
series config, and `epNN.md`, the script. This document is the whole format. It is written to be
useful to a person, and to be pasted into an AI assistant's instructions — `prompts/` holds
generated copies for that.

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
  (MiniMax H3, the default: leave it out), `ltx2` (LTX-2.5 distilled) or `ltx2_ingredients`
  (LTX-2.3 with your character sheets and plates). Single shots or sequences can render on
  another one; see **Rendering a shot on LTX-2** below.

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
| `audio: 3.10-7.40` | window on a locked dialogue mix; sets the duration instead |
| `pace: slow/normal/fast` | 3.6 / 4.4 / 5.4 syllables per second |
| `camera: pushes in…` | camera move, **without** the words "The camera" |
| `sound: …` | ambience and physical action sounds |
| `music: …` | audience-only score; omit for none |
| `extras: …` | other people in frame, described; see **Crowds and extras** |
| `model:`, `lora:`, `steps:` | per-shot render overrides; also valid under a `#` header |
| `profile: dialogue_close` | a render profile from the series config; also valid under a `#` header |
| `target: ltx2` | the video model for this shot or sequence (`minimax_h3_ref2va`, `ltx2` or `ltx2_ingredients`); see below |
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
- **One length: 121 frames (5.04 s).** The IC-LoRA was trained on 768×448, 121 frames,
  24 fps, so every shot renders exactly that. A shorter shot is padded up to 5.04 s (a
  dialogue window is trimmed back in the review cut; trim others in the cut) and a longer
  one is an error: split it.
- **Size is the model's**: 768×448 for the final, 512×288 for the proxy, whatever the series
  config's pass blocks say.
- **A two-part prompt**, written for you: `Reference sheet:` names each panel in order (the
  character's `name` and `design` and which view, the prop's, the location's
  `description`), then `Generated video:` is the LTX-2 paragraph, where someone on the
  sheet is only named.
- A `lora:` line or profile written for another model doesn't remove the IC-LoRA: it is
  put back first in the list, and the take says so.

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
`dur:` nor `audio:`; `dur: auto` on a shot with no dialogue.

A script that does not compile is not a draft, it is a bug. Fix and re-run until it is clean.
