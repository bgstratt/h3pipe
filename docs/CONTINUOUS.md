# Continuous shots: `continuous: first` and `continuous: latent`

Status (2026-10-09): **designed, not built.** This file is the design record and the
checklist. As each phase lands, its contracts move to the docstrings, `docs/API.md` and
`docs/AUTHORING.md`, and this file shrinks to what's still open.

## Why

A shot that carries straight on from the one before it (the same moment, the camera still
running) needs to start from what the previous clip ended on. Otherwise the cut jumps:
people re-pose, light shifts, the camera resets.

Two half-mechanisms do this today, and they got mixed up:

- **`first: continuity`** is a keyframe method: the shot's first keyframe is cut from the
  previous take's last frame. It only works on a target that reads a first frame, so in
  practice the shot is written with `target: minimax_h3_fl2va` by hand. It works, but the
  fact that a shot *continues* is hidden inside a keyframe line.
- **`continuous: yes`** (on a `#` header) was meant to be real chaining on H3 Ref2VA,
  holding the previous clip's latent at the head of the next one. The "22 frames a shot"
  in AUTHORING and the build's cost warning come from that plan. **It was never wired into
  the render graph.** All it does is add one sentence to the prompt ("Continue the incoming
  action…"); H3 gets nothing from the previous take.

## The design

One field, on the shot that continues. The previous shot never needs to know.

```
## sh090
continuous: latent
overlap: 39          # optional: frames of the previous clip held (latent only)
```

| `continuous:` | What it does | Where it renders |
|---|---|---|
| `first` | The previous take's last frame is this shot's first keyframe. Today's `first: continuity`. | A target that reads a first frame. An H3 Ref2VA shot switches to H3 FL2VA automatically when its models are installed. |
| `latent` | The tail of the previous take's saved latent (video and audio) is held at the head of this shot's latent (noise mask 0), and H3 generates the rest. The held frames are trimmed after decode. This shot keeps its own refs and prompt. | H3 Ref2VA first (FL2VA possibly later). |
| *(absent)* | The shot doesn't continue. This is the default. | — |

- **"Previous" means the previous shot in the cut**, not the script. Moving a shot on the
  timeline changes what it continues from, as `first: continuity` does today.
- **`overlap:`** only matters for `latent`. Its default comes from series.json (proposed:
  `continuous: {"overlap": 39}` at the top level); a shot's own value beats it. `first` is
  always one frame.
- **On a `#` header** (a sequence's, not the episode's `=` line), `continuous:` is a
  shortcut for "every shot after the first **in this sequence**". It stops at the next `#`;
  there is no episode-wide default, since a new sequence is a new place or time. A shot can
  override it, or break the chain with `continuous: none`.
- **Across a sequence boundary**, only the shot itself can say it: `continuous:` written on
  the first shot of a sequence continues from the previous shot in the cut, the last of the
  sequence before. That's for the rare case where the same moment carries on.
- **`first:` goes back to meaning only the keyframe:** `generate`, `import`, `none` or a
  path. It no longer takes `continuity`.

### Old scripts (migration)

- `first: continuity` reads as `continuous: first`, and `continuous: yes` (header) as
  `continuous: latent`. `h3.py check` warns on each and says how to write it now. Nothing
  breaks while scripts are updated.
- Until `latent` is built (phase b), a `continuous: latent` shot is a build error that says
  so. So an old `continuous: yes` script needs its shots changed to `continuous: first`, or
  their `continuous` removed, before it builds. Note the "continue the incoming action"
  sentence goes away either way.
- The jump-cut check is left as it is.

### Targets declare what they can do

A target lists the continuity modes it supports (beside how it declares keyframes today),
e.g. `"continuous": ["first"]` on FL2VA, LTX-2 and Wan I2V, and `["latent"]` on Ref2VA.

- `continuous: first` on a target without it switches to that target's declared partner
  for first-frame continuity (Ref2VA → FL2VA) when the partner is ready (its models
  installed), with a note in the build. Otherwise it's an error naming what's needed.
- `continuous: latent` on a target without it is an error.

### Queueing

This is how `first` already works, and `latent` works the same way:

- Queueing a continuous shot reads what it needs from the previous shot **when its render
  starts**, not when it's queued: the last frame (`first`, the H3ContinuityFrame node) or
  the latent tail (`latent`, a new loader node), from whatever take the cut uses at that
  moment.
- A chain queued together goes in cut order. ComfyUI runs its queue in order, so each shot
  starts from the new take of the one ahead of it.
- A continuous shot whose previous shot has no take, and none coming, is an error.

### Latents must be kept

`latent` reads the previous take's `<take>.latent.safetensors`. Today only final takes keep
one (`upscale.save_latents`, final-only by default).

- Any take whose next shot in the cut is `continuous: latent` keeps its latent, **in both
  passes**. This is worked out when it's queued; the previous shot declares nothing.
- If the previous shot's current take has no latent, queueing the chained shot says so:
  re-render the previous shot (it then keeps one).

### Upscales

- **`first`: built** (`5a9e01d`). Every upscale keeps its last frame at the size it was made
  (`<take>.up_last.png`). A continuity shot's upscale reads it when it runs
  (`H3LoadTakeFrame`), falling back to the low-res keyframe. Master queues a source before
  its continuation and remakes either upscale when it was made before this. **To switch
  over:** it currently finds continuity by tracing the first keyframe back to the frame it
  was cut from. Once `continuous: first` exists, it should read the shot's field instead,
  keeping the trace for takes rendered before.
- **`latent`: per shot.** Shot N's 2x re-sample holds its head on shot N−1's *upscaled*
  latent tail, so the two upscales join exactly. That means an upscale keeps its re-sampled
  latent (`<take>.up.latent.safetensors`), but only when a `continuous: latent` shot
  follows it. It's read when shot N's upscale runs, so N−1's must be queued first, exactly
  like `up_last.png`.
- The alternative, obvpm's joint re-sample over a whole chain's concatenated latents, is
  seamless by construction, but re-rendering one shot would mean re-upscaling the whole
  chain. Rejected for now: it fights per-shot takes.

## Open questions

1. **`overlap` length: 22 or 39 frames.** obvpm's code says a latent held exactly (video and
   audio, noise mask 0) must sit on H3's shared audio/video grid. The smallest length that
   fits is 39 frames (≈1.6 s), and their 22-frame window is bumped to 39 when held. 22 works
   as a "guide": conditioning, not held exactly, so softer. Phase b prototypes both on one
   Porchlights chain; the default is set from what we see. A dub shot (audio from a
   recording) may only need the picture held, where 22 could be enough.
2. **Audio across a `latent` join.** obvpm crossfades held audio over 8 ticks (0.2 s). Check
   whether that's needed when the audio is generated, and confirm a dub shot ignores it.
3. **FL2VA + `latent`.** Possibly useful later (keyframes *and* latent hold); not in scope.

## Checklist

### (a) The field, the migration, the auto-switch (`first` working exactly as today)

- [ ] `h3core/story.py`: parse `continuous:` on shots and `#` headers (`first`, `latent`,
      `none`) and `overlap:`; reject other values with a sentence.
- [ ] `h3core/ir.py`: carry them on the shot (header default resolved, `none` breaking it).
      `first: continuity` → `continuous: first`; header `continuous: yes` → `latent`, with
      warnings collected for `h3.py check`.
- [ ] `first:` no longer takes `continuity` in new scripts (old ones read as above).
- [ ] series.json: top-level `continuous: {"overlap": N}`, checked on load.
- [ ] Targets: `continuous` modes in target.json (`first` on FL2VA, LTX-2, LTX-2
      ingredients, Wan I2V; `latent` on Ref2VA), and a first-frame partner on Ref2VA (FL2VA).
- [ ] h3build / compile: `continuous: first` builds on the partner when it's ready, with a
      note; an error naming what's needed when not. `continuous: latent` errors "not built
      yet" until (b).
- [ ] Remove the fake chaining: Ref2VA's `_continuation` sentence, `chain_frames` and its
      warning (`targets/__init__.py` `continuous_warning`). Update the golden fixtures that
      used `continuous: yes`, with the reason in the commit.
- [ ] Keyframe and continuity code (`h3refs` keyframe needs and methods, the
      H3ContinuityFrame path, the editor's keyframe badges) reads `continuous: first`
      instead of `first: continuity`.
- [ ] Upscale: `continuity_source` uses the shot's `continuous: first` when present (the
      keyframe trace stays for older takes).
- [ ] Editor: wherever it shows or sets "continuity" (keyframe method labels, Refs tab
      keyframe rows, the inspector) speaks of `continuous: first`.
- [ ] Docs: AUTHORING (the fields table, Keyframes, "Cutting within a location" item 5,
      Sequences), `prompts/` regenerated, the h3pipe episode-script skill's references,
      `examples/` if any use the old forms.
- [ ] Tests: parsing, migration warnings, header default and `none`, the auto-switch (ready
      / not ready), latent "not built yet", goldens.
- [ ] Migrate the real scripts (Porchlights and the others) with `h3.py check`'s advice;
      confirm each still builds the same shots on the same targets.

### (b) `continuous: latent` (prototype on one Porchlights chain, then build)

- [ ] Prototype: a hand-built Ref2VA graph holding the previous take's latent tail (video
      and audio, noise mask 0) at the head of the new latent; trim after decode. Try 22 and
      39 frames; look at the join. Answer the two open questions.
- [ ] comfy_nodes: a loader node that reads the previous take's latent when the render
      runs, slices its tail, and builds the head-held latent and noise mask (audio feather
      if needed).
- [ ] Ref2VA compile and graph: the node wired in for a `continuous: latent` shot, the
      length grown by `overlap` (on H3's frame grid), the trim after decode, the take's
      sidecar recording what it continued from.
- [ ] Keeping latents: a take whose next shot in the cut is `continuous: latent` keeps one
      in both passes; queueing a chained shot whose previous take has none says to
      re-render it.
- [ ] Queue order: chains in cut order, as for `first`.
- [ ] Docs and tests.

### (c) `continuous: latent` upscales

- [ ] An upscale keeps its re-sampled latent (`<take>.up.latent.safetensors`) when a
      `continuous: latent` shot follows it.
- [ ] A `continuous: latent` shot's re-sample holds its head on the previous shot's upscaled
      latent tail, read when it runs; master queues sources first and remakes upscales
      made before this.
- [ ] Docs and tests; a live check on a Porchlights chain, stepping across each join.
