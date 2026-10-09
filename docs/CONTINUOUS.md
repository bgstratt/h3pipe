# Continuous shots: `continuous: first` and `continuous: latent`

Status (2026-10-09): **phase (a) built** (`continuous: first`, the migration, the
auto-switch); `latent` (b) and its upscales (c) are next. This file is the design record and the
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

1. **`overlap` length: settled, 39 frames** (prototype, 2026-10-09: ep02 sh520 → sh530 on a
   scratch copy). Both versions opened exactly on sh520's last frame. The 39-frame hold,
   video and audio, then carried on coherently: Hector kept sipping, Denise walked in and
   past, the camera tracked her, as the action says. The 22-frame video-only hold had
   Denise pop in beside Hector by frame 20 and duplicated her from frame 80. Default 39;
   `overlap:` takes the other hold lengths that slice cleanly off H3's latent (17j+5: 22,
   56, 73, ...). Cost: a 124-frame shot renders 175 frames (the hold plus the grid
   rounding, trimmed off), about 40% more time. The original question, for the record: obvpm's code says a latent held exactly (video and
   audio, noise mask 0) must sit on H3's shared audio/video grid. The smallest length that
   fits is 39 frames (≈1.6 s), and their 22-frame window is bumped to 39 when held. 22 works
   as a "guide": conditioning, not held exactly, so softer. Phase b prototypes both on one
   Porchlights chain; the default is set from what we see. A dub shot (audio from a
   recording) may only need the picture held, where 22 could be enough.
2. **Audio across a `latent` join.** obvpm crossfades held audio over 8 ticks (0.2 s). Check
   whether that's needed when the audio is generated, and confirm a dub shot ignores it.
3. **FL2VA + `latent`.** Possibly useful later (keyframes *and* latent hold); not in scope.

## Checklist

### (a) The field, the migration, the auto-switch (`first` working exactly as today) — built

Decisions made while building it:

- **No hidden default any more.** A shot on a keyframe target used to open on the previous
  shot's last frame just by having one before it in its sequence. Now only
  `continuous: first` does; otherwise the first keyframe is `generate`. (The real scripts
  checked, Porchlights ep01, write `first: continuity` on every such shot, so nothing
  rendered changes.)
- **`continuous: first` is the existing machinery.** It makes the shot's first keyframe
  method `continuity` (`ir.Shot.keyframe`), so the keyframe cutting, H3ContinuityFrame, the
  editor's badges and the upscale fix all work unchanged.
- **The switch to FL2VA happens at build time**, whatever is installed, so a build doesn't
  depend on ComfyUI being up. If FL2VA's models are missing, the render's readiness check
  says so, as for any target. A shot that names its own target isn't switched (an error).
- **A target supports `first` when it reads a first keyframe**; only Ref2VA declares
  anything (`continuous.modes: ["latent"]`, `continuous.first: "minimax_h3_fl2va"`).
- **The upscale keeps tracing the keyframe** to its source take: that covers shots rendered
  before and after the change alike, so it doesn't need to read the field.

- [x] `h3core/story.py`: parse `continuous:` on shots and `#` headers (`first`, `latent`,
      `none`) and `overlap:`; reject other values with a sentence.
- [x] `h3core/ir.py`: carry them on the shot (header default resolved, `none` breaking it).
      `first: continuity` → `continuous: first`; header `continuous: yes` → `latent`, with
      warnings collected for `h3.py check`.
- [x] `first:` no longer takes `continuity` in new scripts (old ones read as above).
- [x] series.json: top-level `continuous: {"overlap": N}`, checked on load.
- [x] Targets: `continuous` modes in target.json (`first` on FL2VA, LTX-2, LTX-2
      ingredients, Wan I2V; `latent` on Ref2VA), and a first-frame partner on Ref2VA (FL2VA).
- [x] h3build / compile: `continuous: first` builds on the partner when it's ready, with a
      note; an error naming what's needed when not. `continuous: latent` errors "not built
      yet" until (b).
- [x] Remove the fake chaining: Ref2VA's `_continuation` sentence, `chain_frames` and its
      warning (`targets/__init__.py` `continuous_warning`). Update the golden fixtures that
      used `continuous: yes`, with the reason in the commit.
- [x] Keyframe and continuity code (`h3refs` keyframe needs and methods, the
      H3ContinuityFrame path, the editor's keyframe badges) reads `continuous: first`
      instead of `first: continuity`.
- [x] Upscale: `continuity_source` uses the shot's `continuous: first` when present (the
      keyframe trace stays for older takes).
- [x] Editor: wherever it shows or sets "continuity" (keyframe method labels, Refs tab
      keyframe rows, the inspector) speaks of `continuous: first`.
- [x] Docs: AUTHORING (the fields table, Keyframes, "Cutting within a location" item 5,
      Sequences), `prompts/` regenerated, the h3pipe episode-script skill's references,
      `examples/` if any use the old forms.
- [x] Tests: parsing, migration warnings, header default and `none`, the auto-switch (ready
      / not ready), latent "not built yet", goldens.
- [x] Migrate the real scripts (Porchlights and the others) with `h3.py check`'s advice;
      confirm each still builds the same shots on the same targets. (Only Porchlights ep01
      used the old form: 14 lines; its shotlists build identically.)

### (b) `continuous: latent` (prototype on one Porchlights chain, then build)

Built (2026-10-09). How it fits together:

- **Build.** A latent shot's entry gets `hold`: its overlap (the shot's, the header's, the
  series config's `continuous.overlap`, else the target's `continuous.latent.overlap`, 39),
  rounded **up** to 17j+5 with a check warning when that changes it. Its render is
  `snap(asked + hold)` frames; the take keeps the **end** of it, `length = render - hold`,
  so up to 16 frames over what was asked, like any H3 snap. The end, not the start: the
  take's latent tail is then its video tail, which a shot after it holds.
- **Loader.** Renders `snap(asked + hold)` frames; a dub's recording window starts `hold`
  frames earlier, so the voices land under the shot's own frames.
- **Graph** (`h3jobs.chain_latent`, from target.json `continuous.latent`): H3ChainLatent
  between MiniMaxH3ReferenceToVideo's latent and the sampler, fed both VAEs; H3ChainTrim
  on everything the decoders fed (the saver, the review copy).
- **At run time** the node asks `h3refs.chain_source` for the take the cut uses for the
  previous shot NOW (keyframe_source's rule): its kept latent, else (none, another size,
  not an H3 latent) its last `hold` frames and their sound encoded through the VAEs. So no
  take ever has to be re-rendered to be continued. The sidecar records `continued_from`.
- **Queueing** (`h3refs.chains_before_render`, inside refresh_continuity): a chain queues
  in cut order and waits on the take ahead of it; nothing to continue is an error.
- **Upscale** (phase c's minimum): a held take re-samples its whole kept latent and
  H3ChainTrim cuts it back before the saver; without a kept latent it can't be upscaled
  (its frames alone don't rebuild the render).

- [x] `overlap:` (and series.json `continuous.overlap`) rounds up to a 17j+5 hold, with a
      warning; default 39.

- [x] Prototype: a hand-built Ref2VA graph holding the previous take's latent tail (video
      and audio, noise mask 0) at the head of the new latent; trim after decode. Try 22 and
      39 frames; look at the join. Answer the two open questions.
- [x] comfy_nodes: a loader node that reads the previous take's latent when the render
      runs, slices its tail, and builds the head-held latent and noise mask (audio feather
      if needed).
- [x] Ref2VA compile and graph: the node wired in for a `continuous: latent` shot, the
      length grown by `overlap` (on H3's frame grid), the trim after decode, the take's
      sidecar recording what it continued from.
- [x] ~~Keeping latents~~: dropped. A previous take without a kept latent is encoded from
      its last frames and sound instead (the same VAE route an upscale uses), so any take
      can be continued: a proxy, an old take, one from another target.
- [x] Queue order: chains in cut order, as for `first`.
- [x] Docs and tests (AUTHORING.md **Continuous shots**, API.md, the `continuous` fixture's
      sq03, tests/test_latent_chain.py).
- [x] A live render through the pipeline (scratch copy of ep02, sh520 -> sh530, 2026-10-09):
      from the kept latent it reproduced the prototype take (1.3/255 mean difference); from
      the frames it rendered its own, as seamless (joins step 8.2-8.4 against sh520's own
      7.9-9.1). Queueing both shots at once is unit-tested only.
- [x] Staleness: a latent take is stale `chain` (h3edit.chain_changed) when the cut puts
      another shot before it, uses another take of that shot, or that take's mp4 changed
      (`continued_from.sha1`).

### (c) `continuous: latent` upscales

- [x] A held take's upscale re-samples the whole render and trims it (built with b).
- [x] An upscale keeps its re-sampled latent (`<take>.up.latent.safetensors`) when a
      `continuous: latent` shot follows it in the cut (h3upscale.set_chain `keep_latent`;
      H3SaveUpscale's `latent` / `latent_file`).
- [x] A `continuous: latent` shot's re-sample holds its head on the previous shot's upscaled
      latent tail, read when it runs (`held_from`, from the take's `continued_from`;
      H3ChainLatent with `missing_ok`, video only: H3HoldAudio holds the sound), and its
      upscale record says whether it held (`chain_hold`); master queues sources first
      (in_order) and remakes a source with no kept latent or a chain that wasn't held
      (h3master.chain_rows). With a then-step (SeedVR2, a pixel model) after the
      re-sample, each shot goes through it on its own: the join can drift a little there.
- [x] Docs and tests (tests/test_latent_chain.py); a live check (scratch copy of ep02,
      sh520 t02 -> sh530 t06, Porchlights' master recipe, 2026-10-09): sh520's upscale kept
      its latent (57 MB), sh530's held 12 steps of it (`chain_hold.held`). The upscaled
      join steps 9.15 held, 9.46 unheld, against sh520's own 9.2-10.9 frame to frame; the
      colour either side is the same both ways. On this shot the hold changes little: a
      chained take's latent already opens on the previous take's frames, and the re-sample
      starts at step 7 of 8. It should count more where an upscale invents detail (faces,
      texture). Also measured: queueing sh520 and sh530 at once, sh530 waited on and held
      sh520's new take (join 8.9 against 9.3-10.5).
- [x] Nothing writes a take's or an upscale's record while its job runs: the chain node's
      `record` goes to the saver (H3SaveShot `chain`, H3SaveUpscale `chain_hold`). Writing
      it from the node raced the queuer's prompt id on an idle ComfyUI and lost it (the
      upscale then read as failed though it ran).
