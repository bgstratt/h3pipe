# h3pipe — plan: a shot/take editor, then model-agnostic targets

Status (2026-09-19): Phases 0–3 and 5–8 done; Phase 4 (evaluate) continues through use. Targets:
`minimax_h3_ref2va` (default), `ltx2` (LTX-2.5, text/keyframes), `ltx2_ingredients` (LTX-2.3 + IC-LoRA
reference sheet from the picked refs: character identity on LTX). Keyframe continuity (the previous shot's
last frame becomes this shot's first) is in the CLI, the routes and the editor. Next candidates: Wan 2.2
(VACE, with references), H3 FL2VA (uses keyframes), the Phase 9 editor items. The open LTX items are under
**Phase 8 — as built**.
This is the working plan for the next round of development. `CLAUDE.md` points here.

## Goals

1. **Script + series config in, episode out, with a tight tweak loop.** You write `epNN.md` and
   `series.json`; everything else can be driven from a UI inside ComfyUI: generate
   refs, render shots, tweak a shot's prompt/seed/model/LoRA/steps, redo, compare
   takes, pick the take each shot uses, assemble.
2. **Nothing is lost.** Every take is kept with the exact settings that produced it.
   Picking an earlier take never deletes a later one. UI tweaks survive a rebuild.
3. **Model-agnostic core.** The script and series config describe *the film*; nothing in them
   should assume MiniMax H3 or Krea. Adding a model means adding a *target*, not
   editing the pipeline. Different kinds of shot can use different models/LoRAs.
4. **Nothing breaks on the way.** Every refactor is proven by comparing generated
   output byte-for-byte against the current pipeline.

## Non-goals (for now)

- A general NLE. The unit of work is the **shot**, not an arbitrary time range. No
  prompt sections spanning ranges, driver lanes or latent masks. The cut is an
  ordered list of shots with a chosen take each — a linear sequence editor.
- Replacing the real edit. `h3assemble` stays a review cut; final conform happens in
  an NLE.
- Porting to .NET or Rust. The core stays Python (stdlib only for pipeline scripts).

## Decisions

| Decision | Why |
|---|---|
| **Usable UI first, refactor second** | The value is the tweak/take loop. Takes, overrides and the cut don't depend on the IR/targets refactor, so they ship on H3 first; the refactor then follows what the UI turned out to need. |
| Core stays Python | The pipeline and ComfyUI are Python; backend routes import the pipeline as a library. |
| UI is a ComfyUI frontend extension: TypeScript + React + Vite, **sidebar tabs + a bottom panel + a floating take viewer** | Lives where rendering lives. Each part (shot bin, timeline, viewer, inspector, refs) is a self-contained component talking only to the REST API, so they can be rearranged into a fullscreen layout later if the sidebar proves cramped. Keep Comfy-specific glue in one thin module. |
| Backend = routes on ComfyUI's aiohttp server (`PromptServer.instance.routes`) | Same process and port; reuses its queue, websocket progress and model lists. |
| One workflow per target, one shot per queue, no per-shot nodes on the canvas | The graph is plumbing, not a timeline. |
| **UI tweaks live in `overrides.json`, not the script** | Story edits (action, camera, dialogue) are model-free and belong in `epNN.md`. Compiled edits (the exact H3 prompt text, seed, model, LoRAs, steps) are target-specific and belong in an editor-owned sidecar. A "promote to script" action moves story edits across later. |
| **Redo uses a new seed by default, recorded in the take** | Today a redo reuses `stable_seed` and mostly reproduces the last take. Take 1 still uses `stable_seed` (the derivation is unchanged); redo offers new (default) / same / typed seed. |
| **Every render is frozen** | At queue time the job's exact one-shot shotlist is written beside the take. The take can always be reproduced or used as the starting point of a redo, whatever the script says now. |
| **Refs are driven by the series config and have takes too** | Refs belong to the series, not an episode, and you want to lock designs before a script uses them. Candidates are takes; the picked one is copied to the path the series config names. |
| ComfyUI-Sonder-Editor is **inspiration only** | Borrow ideas (model templates, recipes, frozen job provenance, take gallery/compare). No code: it is GPL-3. |
| Prompt formats are **code**, everything else about a target is **data** | Writing an H3 prompt is real logic; a declarative DSL would only move that complexity somewhere worse. |

## Project layout

Today each episode folder holds its own `series.json` and `refs/`, so several episodes
duplicate the series config and refs drift apart. Target layout:

```
Shows/gold_path/
  series.json            the series config
  refs/                  picked refs, at the paths the series config names
  refs/_takes/<ref>/     ref candidates + sidecars
  ep01/
    ep01.md              the script
    overrides.json       UI tweaks            (editor-owned)
    cut.json             order + chosen takes (editor-owned)
    shotlist/            generated
    refs_todo.*          generated
    renders/<shot>/      takes
    renders_proxy/<shot>/
```

The series config is looked up in the episode folder first, then its parent, so today's
per-episode layout keeps working. Series config paths are relative to the folder holding
`series.json`; build writes them into the shotlist relative to the episode.

## Architecture

```
 authored                 generated (never hand-edit)            editor-owned        runtime
 ────────                 ───────────────────────────            ────────────        ───────
 series.json ─┐
 epNN.md     ─┼─ build ─▶ shotlist.json / _proxy ──┐
 (recording) ─┘          (later: shots.json IR     │
                          + shotlist.<target>)     ├─ queue ─▶ renders/<shot>/<shot>_tNN.*
                                 overrides.json ───┘              (mp4, frozen shotlist,
                                                                    sidecar, thumbs)
 series.json ─── refs ─▶ refs/_takes/<ref>/… ─ pick ─▶ refs/<path the series config names>
                                                                         │
                                                  cut.json ─ assemble ─▶ epNN.mp4
```

## 1. Takes

A take is `renders/<shot>/<shot>_tNN.*`:

| File | Written by | When |
|---|---|---|
| `_tNN.shotlist.json` | queue (CLI or route) | at queue time: a complete one-shot shotlist (defaults, cast, the shot with overrides applied). The loader reads it at index 0, so the node needs no override logic. |
| `_tNN.json` | queue, then `H3SaveShot` | sidecar. Written `status: queued` at queue time, so it **reserves the take number** (two redos queued back to back can't collide). The saver sets `ok`, frames and `finished`. |
| `_tNN.mp4` | `H3SaveShot` | as today |
| `_tNN.jpg`, `_tNN_strip.jpg` | `H3SaveShot` | mid-frame thumbnail, and a strip of ~8 frames for hover scrub (PIL, frames are already in memory) |

The saver gets the sidecar path as an input. It does not need to know the target,
because the sidecar already holds everything the job needed. A job that never reaches
the saver (error, interrupt, ComfyUI restart) is swept when takes are listed: a `queued`
sidecar whose `comfy_prompt_id` is neither in `/queue` nor successful in `/history`
becomes `failed`.

```json
{
  "shot": "sh020", "take": 3, "pass": "final", "target": "minimax_h3_ref2va",
  "status": "ok",
  "model": "…", "loras": [{"name": "…", "strength": 1.0}], "steps": 8,
  "seed": 1743920155, "seed_source": "stable | new | typed | take:2",
  "width": 1344, "height": 768, "frames": 107,
  "shot_hash": "sha1 of the shot's built shotlist entry (the IR hash once Phase 6 lands)",
  "overrides": {"prompt": true, "seed": false},
  "refs": [{"slot": "Picture 1", "path": "refs/riley/riley_sheet_4panel.png", "sha1": "…"}],
  "preset_hash": "sha1…",
  "parent_take": 2,
  "comfy_prompt_id": "…", "queued": "…", "started": "…", "finished": "…",
  "note": ""
}
```

**Stale** means the take no longer matches what a render would use now. The badge says
why: `script` (the `shot_hash` changed), `ref` (a ref's sha1 changed) or `preset`
(model/LoRA/steps defaults changed). Stale is information, not an error. Takes without a
sidecar still work, with provenance marked unknown.

`existing_takes` counts sidecars as well as mp4s, so a queued take holds its number.

## 2. Overrides — `epNN/overrides.json`

Editor-owned; hand-editable. Applied at queue time on top of the built shotlist, never
by build, so the build goldens are unaffected.

```json
{
  "episode": "ep01",
  "shots": {
    "sh020": {
      "minimax_h3_ref2va": {
        "seed": null,
        "note": "",
        "final": {"base_hash": "story hash of the final build of the shot this was written against",
                  "prompt": "…full compiled text, or null…",
                  "model": null, "loras": null, "steps": null},
        "proxy": {"base_hash": "…", "prompt": null, "model": null, "loras": null, "steps": null}
      }
    }
  }
}
```

- Overrides are keyed by target because compiled text only makes sense for one target.
- The prompt and `base_hash` are **per pass**. The built prompt differs between passes
  (a proxy with `audio_mode: generate` has different audio sections from a final that
  clones), so a shared prompt override was wrong for one of them. Found in Phase 1.
  The seed is shared, because both passes use the same seeds on purpose.
- `base_hash` is the shot's *story hash*: the built entry minus model/LoRA/steps. So a
  series-wide steps change doesn't make every prompt override stale.
- A null field means "use the built value".
- When a rebuild changes the shot so that `base_hash` no longer matches, the override is
  **stale**. It still applies (you asked for it), but the inspector flags it and shows a
  diff between what the build now produces and your text. You then keep it, re-base it
  or drop it.
- **Promote to script** (a later phase) writes story-level edits back into `epNN.md`
  and drops the matching override.

Precedence for any render parameter: target preset (final/proxy) → the series
config's pass block (`series` / `proxy`) → sequence profile → sequence lines → shot
profile → shot lines (script) → `overrides.json` → the redo dialog.

Changed in Phase 7 (it used to read "… → shot (script) → profile → …"): a profile now
sits just *below* the explicit lines of the level that names it. A profile is a bundle
of defaults, and the natural edit is "this shot uses `dialogue_close`, but at 9 steps";
with the profile above the shot's lines, that `steps: 9` would be silently ignored. And
a shot's profile beats its sequence's lines, because it is the more specific choice.
`target` layers the same way, starting from the series config's `series.target`.

## 3. The cut — `epNN/cut.json`

An **ordered list**, so the cut can differ from script order.

```json
{
  "episode": "ep01",
  "final": [
    {"shot": "sh010", "take": 2},
    {"shot": "sh020", "take": 3, "trim_in": 0, "trim_out": 0, "locked": true, "note": ""},
    {"shot": "sh030", "pass": "proxy", "take": 1}
  ],
  "proxy": []
}
```

- **No `cut.json`, or a shot not in it:** the shot is placed in script order and uses its
  latest `ok` take. That is today's behaviour, so `h3assemble` keeps working.
- **A shot new to the script:** it is inserted after its script predecessor.
- **A shot deleted from the script:** its entry is kept, flagged as orphaned and skipped
  by assemble.
- **Placeholders:** an entry may name a take from the other pass, e.g. a proxy standing
  in for a missing final. Assemble scales it to the cut's size, and the timeline badges
  it as a placeholder.

## 4. References

- **The Refs tab lists every subject and location in the series config**, not just what one
  episode's `refs_todo.json` needs. `refs_todo` becomes an episode filter and badge
  ("blocks 12 shots in ep05").
- **A ref's candidates are takes**, stored in `refs/_takes/<ref>/<ref>_tNN.png` with
  sidecars (the same shape as video takes: prompt, seed, model, LoRAs). Picking a take
  copies it to the path the series config names, and every video take records that file's sha1,
  so re-picking a ref marks dependent takes stale (`ref`).
- **Character sheets:** each of the four views has its own takes. A pick per view, then
  `mksheet` stitches them.
- **Import:** any image can be imported as a ref take (hand-drawn, from elsewhere).
- **Prompt tweaks:** ref prompt and seed tweaks go in `refs/_overrides.json`, the same
  shape as shot overrides, keyed by ref.
- **Two fixes in `kreagen` along the way:**
  - It gets the character id from the series config entry, not by guessing from the filename.
    Today `basename.split("_sheet")[0]` silently skips any sheet not named
    `<id>_sheet…`.
  - There is one source for sheet prompt wording. Today `h3build.need()` writes a
    4-panel prompt that `kreagen` ignores.

## 5. The editor UI

| Surface | Contents |
|---|---|
| Sidebar **Shots** tab | Episode picker; bin grouped by sequence → shot, expanding to takes; badges (stale, override, queued/rendering, placeholder, no take). |
| Sidebar **Inspector** (the selected shot) | Built values (read-only); the override editor: prompt text with a diff against the built text, seed, model/LoRA/steps pickers (from ComfyUI's model lists); Render / Redo buttons; pass toggle. |
| Sidebar **Refs** tab | Series config subjects and locations; picked ref and candidates; generate / redo / import / pick. |
| Sidebar **Queue** tab | Jobs from this episode with live progress (ComfyUI websocket); cancel. |
| Bottom panel **Timeline** | The cut: take thumbnails in cut order, width ∝ duration, hover scrub; pass toggle; later, drag to reorder and the dialogue waveform. |
| Floating **Take viewer** | One shot: a large player, all its takes as a strip below, click to load A / B for side-by-side or wipe compare, "Use this take", "Redo from this take". Opens by double-clicking a thumbnail. |

**Thumbnail context menu** (in the bin, timeline and viewer strip): Play · Compare with…
· Use this take · Redo from this take (opens the redo dialog pre-filled with that
take's prompt, seed and settings) · Show details (the sidecar) · Reveal in folder.

**Redo dialog:**
- Seed: new (default) / same / typed.
- Model, LoRAs, steps.
- Prompt: a toggle between the current override and that take's prompt.
- Pass (proxy or final).
- A "save these as the shot's override" checkbox, on by default.

## Phases

Each phase ends with its exit check passing. Don't start the next until it does.

**Phase 0 — safety net** ✅ done 2026-09-18
- `git init`, current state committed.
- Fixtures:
  - `example` (`examples/`: source_track, `audio:` windows).
  - `tests/fixtures/kitchen_sink`: synthetic, and reaches every branch no real episode
    uses (per-pass/sequence/shot model, LoRA and steps; explicit policies and
    retentions; continuous; `extras:`; V.O. with a pronoun; props and vehicles;
    crammed/tight pacing).
  - Three real episodes in `tests/local/fixtures`, which is gitignored:
    TrashPanda ep01, DeanStories ep05, WishTest ep02.
  - `tests/fixtures/errors/*.md`: 16 scripts that must fail.
- Goldens per fixture: both passes' shotlist and refs_todo (.json/.md), plus the stdout of
  `--check` (both passes: this is where the warnings are) and `--pace`. CRLF is
  normalised to LF; everything else compared byte for byte.
- `tests/test_golden.py`; `python tests/test_golden.py --update` rewrites the goldens.
- Found on the way:
  - No real script repeats a shot id, but the parser allows it, and renders
    (and soon takes, overrides and the cut) are keyed by shot id. Rejected in Phase 1.
  - The shotlists on disk in the real episodes predate the current `h3build`
    (the extras clause was reworded, `defaults.model` was added), so rebuilding them
    changes their prompts.

**Phase 1 — takes, overrides, cut (CLI)** ✅ done 2026-09-18
- Built:
  - `h3takes.py`: the on-disk contract.
  - `h3jobs.py`: planning, frozen shotlists, sidecars, graph patching, the ComfyUI client.
  - `h3render` rebuilt on `h3jobs`.
  - `H3SaveShot`: closes the sidecar and writes thumbnails.
  - `h3assemble`: follows `cut.json`.
  - `h3edit.py`: `episode_status`, plus `h3.py takes / pick / override`.
  - Duplicate shot ids are rejected.
- Tests: 91, including `h3render` and `h3.py` end to end against a fake ComfyUI
  (`tests/test_render.py`, `tests/test_edit.py`).
- Real-ComfyUI exit check passed on a copy of DeanStories ep05 (proxy), 2026-09-18:
  - First renders: sidecars closed by the node, the built seed used, thumbnail
    480×274, an 8-frame strip, and exact frame counts.
  - `--redo`: new seed.
  - A prompt override plus a pinned seed rendered from the frozen shotlist.
  - `pick` t01, then assemble: the cut is t01 + t02 + a pre-sidecar take, 321/321 frames.
  - Hard-killing h3render and interrupting the job left the take `queued`; `h3.py takes`
    swept it to `failed`, and the next render plans it as a retry.
- The install now runs the repo directly: `custom_nodes\ComfyUI-H3-Shotlist` is a junction to
  `comfy_nodes/`. The old copy is at `C:\AI\ComfyUI\ComfyUI-H3-Shotlist.bak`, and the stale
  pipeline copy is at `workflows\h3pipe.old`. Run `h3.py` from the repo, and pass `--workflow`
  pointing at the installed `H3_Ref2VA_Shotlist_v1.json`. It matches the installed ComfyUI
  version (`CreateVideo` has two more widgets than the repo copy).
- Notes for later phases:
  - Built seeds are 63-bit (`stable_seed`), and JavaScript numbers lose precision above
    2^53. The routes must send seeds as strings, and the editor must never parse them
    into numbers. New seeds from redo stay below 2^53.
  - `sweep_queued` needs the time the queue snapshot was taken (`as_of`). Routes must
    record it before fetching `/queue`.
  - `h3edit.episode_status()` is already the JSON the shot bin needs. Serve it as is.
  - Stale reasons split into `script` (story hash), `ref` (reference file sha1s) and
    `preset` (model/LoRA/steps/size).
- A `jobs` module shared by `h3render` and the future routes: plan the take, apply
  overrides, write the frozen shotlist and the `queued` sidecar, patch the graph, queue.
- Seed policy: take 1 = `stable_seed`; `--redo` = new seed unless `--same-seed` / `--seed N`.
- `H3SaveShot`: new `sidecar` input; finalises the sidecar and writes the thumbnails.
- LoRAs as a list: extra `LoraLoaderModelOnly` nodes are inserted into the API graph
  after the workflow's one, so no workflow needs a fixed number of slots. `lora:` in
  the script keeps meaning the single (turbo) LoRA.
- `overrides.json` read at queue time; `cut.json` read by `h3assemble`.
- Build rejects a shot id used twice in one episode (an intended change: add an error
  fixture and its golden).
- `h3.py takes <ep>` (list with stale reasons), `pick <ep> <shot> <take>`,
  `override <ep> <shot> --seed/--model/--steps/--prompt-file`.
- Exit: goldens byte-identical; on a real episode: redo gives a new seed, two redos
  queued at once get different take numbers, an overridden prompt renders and survives
  a rebuild, assemble uses a non-latest take, a script edit marks the take `script`-stale.

**Running Phases 2, 3 and 6 in parallel (decided 2026-09-18, to get through the plan
faster).** The routes (Phase 2) and the UI (Phase 3) share one contract, `docs/API.md`,
written first. The UI develops against a mock of it until the routes land. The story-IR
extraction (Phase 6) is a pure refactor of `h3build` behind the goldens and touches
neither, so it runs ahead of its turn. Phase 4 (evaluate) still gates Phases 7 and 8.

**Phase 2 — backend routes**
- `comfy_nodes/` registers `/h3pipe/...`: list episodes; shots (built values + overrides
  + stale); takes and thumbnails; queue render/redo; set/clear override; set pick and
  cut order; refs list and status; cancel.
- Routes are thin: they call the Phase 1 `jobs` module and the pipeline as a library.
  Only `comfy_nodes/` imports torch/PIL.
- Exit: every editor action can be done with `curl`, and a take queued by route is
  indistinguishable on disk from one queued by the CLI.

**Phase 3 — editor v1**
- React + Vite extension under `web/` (built into the node pack's `WEB_DIRECTORY`).
- In order: Shots tab + Timeline (read-only) → take viewer + context menu + "use this
  take" → inspector with overrides + redo dialog + live progress → Queue tab.
- Exit: review and fix a real episode (swap takes, tweak prompts, redo, compare,
  assemble) without touching the CLI or the canvas.

**Phase 4 — evaluate**: first look done 2026-09-18. Your verdicts:
- Timeline: good. **Add Play all**: play the cut in order from the takes themselves, with
  no assemble required. Assemble stays as an optional export.
- Compare in the viewer: right as it is.
- **Drop the Queue tab.** ComfyUI's own queue is enough; keep the live progress badges
  in the bin and timeline.
- **The Inspector becomes a floating window** like the viewer, not a sidebar tab.
- **Roots and episodes are chosen with a folder browser**, not by typing a path. The
  server lists folders, because the browser can't see server paths.
- **Refs aren't in the editor yet (Phase 5).** Rendering ep38 before any refs existed
  queued 17 takes that all failed with "background plate not found". The editor must show
  missing refs and refuse to render a shot that is missing any.

**Phase 4 — evaluate** (the original checklist)
Use it on a real episode, then decide and write down here:
- Is the sidebar + bottom panel too cramped? If so, arrange the same components as a
  fullscreen overlay.
- Is the floating viewer enough for judging takes?
- Is reordering the cut wanted now? Trims?
- Which override fields actually get used, and which are missing?
- What should the Refs tab look like, now that the take model has been used?

**Phase 5 — references**: backend merged 2026-09-18 (`h3refs.py`, `H3SaveRefTake`, the
ref routes, `kreagen` on takes); the Refs tab is in progress.
- **Known gap:** a series config in the episode's *parent* folder. `h3refs` resolves ref paths
  against the series config's folder, but `h3build` writes the series config's paths into the shotlist
  unchanged, and the loader resolves them against the episode. Fix: `h3build` rebases
  ref paths to be relative to the episode (an intended golden change, needing a
  parent-folder series config fixture). Today's episodes each have their own `series.json`, so this
  only matters for the series-folder layout.

**Phase 5 — references** (the original bullets)
- Series/episode layout: look up the series config in the parent folder; paths resolve relative
  to the series config.
- Ref takes, picks, per-view sheet picks, import, `refs/_overrides.json`; the two
  `kreagen` fixes above; ref routes and the Refs tab.
- Exit: generate a new character's sheet from the series config alone (no episode uses it yet);
  re-picking a ref marks dependent video takes `ref`-stale.

**Phase 6 — extract the story IR, no behaviour change**
- ✅ New package (`h3pipe/` or `core/`): `story.py` (parse → IR), `ir.py` (dataclasses +
  JSON), `series_config.py`.
- ✅ `h3build.py` becomes: parse → IR → the current H3 compile code (still in place).
- ✅ Write `shotlist/shots.json` alongside the existing outputs; `shot_hash` becomes the
  IR hash (not yet: that is `h3jobs`, left for after Phases 2/3 land).
- Done 2026-09-18 as `h3core/` (plus `speech.py` for pacing); compile reads the IR through
  `h3build.legacy_episode`, and the goldens changed only by gaining `shots.json`. The
  as-built shape (`h3core/ir.py`'s docstring) differs slightly from the sketch below.
- Exit: goldens byte-identical; `shots.json` contains no H3 vocabulary.

**Phase 7 — targets, H3 only** — ✅ done 2026-09-18 (live check passed: a CLI render and an editor-route render on the
real ComfyUI, a `kreagen` dry run, and workflows still read from ComfyUI's saved copies)
- `targets/` loader + `Target` protocol; move the H3 code per the table in *Targets*.
- `jobs` / `graph_for` driven by `binding`; `kreagen` driven by `targets/image/krea2`.
- Render profiles (see *Targets*).
- Exit: goldens byte-identical; render and refs still work on a real episode, from the
  CLI and the UI.
- As built:
  - `targets/__init__.py`: `load_target`, `list_targets`, `episode_target`, `Target`
    (`template`, `recipe`, `binding`, `presets`, `compile_episode`, `compile`,
    `required_refs`, `ref_slots`, `compile_without`, `ref_prompt`, `describe`),
    `Template`, `Binding`, `Preset`, `RefRequest`, and the profile layering.
  - `targets/video/minimax_h3_ref2va/`: `target.json`, `compile.py` (the old
    `compile_episode`, per shot), `prompt.py` (`build_prompt`), `workflow.json` (moved
    from `workflows/`; ComfyUI's saved copy is still found by its old name).
  - `targets/image/krea2/`: `target.json` (the model stack), `prompt.py` (all ref
    wording), `graph.py` (the built-in graph and patching), `workflow.json`.
  - `h3build` is parse → IR → `episode_target` → `target.compile_episode` → write. It
    keeps `snap_up`, `RETENTIONS`, `legacy_episode`, `compile_episode`, `FINAL_*`/`PROXY_*`
    and `SIZE_HINT` as compatibility names.
  - `h3jobs.graph_for` / `apply_loras` follow the job's target's binding; frozen shotlists
    and sidecars record the real target id.
  - `GET /h3pipe/targets`; `target` (and `profile`) on episode shots, shot detail and takes.
  - Proof: every golden byte-identical, local real episodes included; the only golden
    changes are kitchen_sink's new profile sequence and two new error cases. The queued
    graph, frozen shotlist and sidecar of every kitchen_sink shot were snapshotted from
    the pre-Phase-7 code (`tests/golden/graphs/`) and still match, except for the frozen
    shotlist's new top-level `target`.
  - Stretch, done: render anyway is target-aware (API.md, *Render anyway, target-aware*).

**Phase 8 — a second video target**
- LTX 2.3 or Wan 2.2: its template, recipe, binding and a prose `prompt.py`.
- Fix every place the H3 assumptions leak out — that's the point of this phase.
  Overrides and takes are keyed by target already.
- Exit: the same episode builds and renders a proxy pass on both targets; a single shot
  can be retargeted from the inspector.
- ✅ Built 2026-09-19 (backend and CLI; the inspector's picker is the `web/` half). See
  **Phase 8 — as built** below.

**Phase 8 — as built**
- **Subgraphs** (`h3jobs.flatten_subgraphs`, run by `ui_to_api`): a node whose `type` is a
  `definitions.subgraphs` id is replaced by its inner nodes (ids `<instance>:<inner>`, as
  ComfyUI names them; nested subgraphs expand the same way). Each subgraph input is wired
  to whatever feeds the instance; an unfed input takes the instance's promoted widget value
  (`widgets_values`), else, for `proxyWidgets` saves, the inner widget's value, and the
  frontend's rule that the value reaches sockets too (the ingredients template's switch) is
  kept. Server primitives (`PrimitiveInt`/`Boolean`/`String[Multiline]`/`Float`) are inlined
  into widgets and kept as nodes where they feed a socket (`ComfyMathExpression`); Reroutes
  and bypassed nodes pass through (by matching type); muted nodes drop out. Refused with a
  `WorkflowError`: a bypassed subgraph instance, a bypassed node with no input of its output
  type, promoted widget values that don't line up. `check_graph` validates a graph against
  `/object_info` (every class known, every link and required input present); the user's
  three LTX saves are fixtures and pass it. The H3 graph snapshot is unchanged.
- **`targets/video/ltx2/`** (the 2.5 distilled I2V template, as saved in ComfyUI):
  - Template: `8k+1` frames up to 481, fps `"series"`, sizes multiples of **64** (two-stage:
    the base latent is half size and needs 32), `size_fit: snap`, max 1 MP / 1536 long side.
  - Binding: no loader; every value is a widget. Params can name several widgets (both
    `RandomNoise` seeds, length into the video and audio latents, fps into conditioning,
    audio latent and saver) and pick among nodes of one class with `feeds` / `title` /
    `all`, and `scale` (the half-size base latent). `saver.replace` puts `H3SaveShot` in
    place of `CreateVideo` (images from `VAEDecodeTiled`, audio from `LTXVAudioVAEDecode`)
    and drops `SaveVideo`/`PreviewAny`; `prune` drops everything the saver doesn't need,
    which takes the prompt enhancer out. LoRAs are inserted after the `UNETLoader` when the
    workflow has no loader (`insert_after`). The text encoder, VAEs and upscaler are params
    too, filled from the preset.
  - Recipe: no refs are required. `keyframes: [first, last]` are optional ref slots
    (`refs/shots/<shot>/first|last.png`); `patch_graph` feeds `first` to both
    `LTXVImgToVideoInplace` (0.7 base, 1.0 refine), or splices them out for text-to-video,
    and adds `last` as an `LTXVAddGuide` (frame -1) plus `LTXVCropGuides` per stage.
    Images reach `LoadImage` as `input/h3pipe/<sha1>.png` through ComfyUI's
    `POST /upload/image` (CLI and routes alike; no path guessing).
  - Audio: `recipe.policies: [generate]` and `policy_fallback` are the declared capability;
    `Target.audio_policy(intent)` is what compiles check. clone / dub / dub_keep_foley
    shots render with generate, with `audio_intent`/`audio_note` on the entry, a build
    warning and the note in the take's sidecar.
  - Prompt (`prompt.py`): one paragraph in the LTX-2 caption style (from ComfyUI's
    `TextGenerateLTX2Prompt` system prompts): `Style: <look>.`, then shot size, location and
    camera (static when the script names no move), each subject's `design`, extras, the
    action, on-screen text, each line quoted with speaker, voice (first line only) and
    delivery, then sound and music. Negative prompt is a preset value.
  - Presets: final 768×512, proxy 448×256, both the int8 distilled model, gemma4 12B text
    encoder, the 2.5 VAEs and x2 upscaler. `steps: 8` is recorded, not patched (the
    distilled `ManualSigmas` schedule is fixed). A series written for another target lends
    `ltx2` only its sizes, not its model/LoRA/steps (`Target.preset`).
- **Mixed-target episodes:** `targets.shot_targets` / `episode_targets` replace Phase 7's
  error. Build compiles each target's own shots (`compile_episode(..., only=ids)`; H3 keeps
  each shot's place in its sequence, so its entries are byte-identical to a full build) and
  writes `shotlist.<target>[_proxy].json` beside the series target's files (unchanged, and
  always written); refs_todo merges every target's requests. `h3jobs.load_shotlists` /
  `find_shot` / `episode_shots` (script order) are how everything finds a shot:
  `plan_episode`, `episode_status`, `shot_detail`, `queue_shots`, sweeps, picks, the refs'
  `used_by` and keyframe listing, and `h3assemble` (which also checks a take's frames
  against its own sidecar length).
- **Retargeting:** `overrides.json` `shots.<id>.target` (shared by both passes;
  `h3takes.shot_target` / `set_shot_target`) or a request's `target`. `h3jobs.retarget`
  compiles the shot's IR from `shots.json` for that target at queue time, after checking
  the build is current (the built target must reproduce the built entry, as
  `compile_without` does). Overrides come from the new target's block; any prompt override
  is ignored and the sidecar says so; a take on another target doesn't count as done;
  `stale` gains `target`. The routes, `h3render --target` and `h3.py override --target`
  all use it.
- **Leaks fixed:** `h3align` snaps each shot to its own target's grid; LTX entries carry the
  neutral keys the bin reads (`length`, `subjects`, `audio_policy`, `background`, `seed`,
  `steps`, `prompt`); `h3.py override` writes the shot's current target's block; `h3render`
  resolves each job's target's workflow (`--workflow` applies to `--target`'s, else the
  series target's) and gains `--dry-run --check-nodes`; `--pace` uses each shot's grid.
- **Live check (2026-09-19, a scratch copy of DeanStories ep05, proxy):** three LTX renders
  from the CLI on the running ComfyUI, no restart and no new node: text-only via
  `--target ltx2` (sh050, 73 frames, 39 s), first keyframe via `h3.py override --target`
  (sh040, 18 s), first + last keyframes via `--target` (sh030, 21 s). Each: sidecar `ok`,
  73 frames (8k+1), 448×256 with AAC audio, thumbnail and strip written; the last frame
  matched the last keyframe. The mixed H3/LTX proxy cut assembled.
- **Left for full LTX parity:**
  - ~~**Subject references (IC-LoRA "ingredients").**~~ **Done** (2026-09-19): the
    `ltx2_ingredients` target; see **LTX-2.3 ingredients — as built** below. The design
    that follows is kept as the reasoning. Design: a third ref shape,
    `reference_sheet`, per shot: one composite image on black, one clean panel per element
    (each character's face + body from its picked views, each prop, the plate), made by an
    image-target job (`mksheet` can already stitch; it needs a layout and a black ground).
    The `ltx2_ingredients` target (2.3 dev + `ltx-2.3-22b-ic-lora-ingredients` LoRA at
    ~1.4) binds it to the template's `RepeatImageBatch` → `ResizeAndPadImage` path as a
    static control video at the output size, ≥121 frames, and writes the two-part prompt
    ("Reference sheet: … / Generated video: …") from the same IR: the first half lists the
    panels from the series config's `design`s, the second is today's prose. Its bucket is
    768×448, 121 frames, 24 fps, so the template pins those and shots longer than 5 s split.
    `required_refs` returns the sheet (blocking, like H3's pictures); the sheet's sha1 goes
    in the sidecar so re-picking a view marks the take `ref`-stale.
  - **Final-quality settings:** the final preset is the distilled model at 768×512 with the
    template's sigmas. Worth evaluating: the 2.3 dev model + distilled LoRA (0.75) as a
    `final` profile, series-size finals (1344×768 fits the 1 MP cap), exposing the
    `ManualSigmas` schedules as a preset value, and whether 25 fps (LTX's native) should be
    offered per series.
  - **Keyframe generation:** a still from the shot's prose prompt through the image target
    is still to do. **Continuity is done (2026-09-19):** `h3refs.keyframe_from_take` cuts
    the previous shot's last frame (cut order, the take the cut uses; or any shot, take and
    frame) into a `shot:<id>:first` take (`source: "frame"`), and the next shot's first
    frame into `last`; `h3.py keyframe`, `POST /h3pipe/refs/keyframe`, and in the editor the
    shot/take context menu (the viewer passes its frame), the inspector's Keyframes section
    and the Refs tab. A take rendered without an optional keyframe is now `ref`-stale once
    the keyframe exists. Live check on the scratch ep05: sh050's first frame from sh040 t03's
    last frame, then an LTX proxy render: its frame 0 is within 27.4 dB PSNR (mean abs error
    7.7/255) of the keyframe. No route yet to unpick or delete a ref take.
  - The editor's target picker (Inspector and redo dialog) against `GET /h3pipe/targets`.
  - Per-target pass blocks in the series config (`targets.ltx2.series` / `.proxy`) if one
    series needs different LTX model/steps than the target's presets.
  - `H3SaveShot` still names the generated mix `_h3.wav` for any target.

**LTX-2.3 ingredients — as built** (`targets/video/ltx2_ingredients/`, from the user's
saved `template_ltx2_3_ic_lora_ingredients.json`; label "LTX-2.3 ingredients
(character/plate refs)", short `LTX+refs`)
- **Template:** the IC-LoRA's bucket, checked against the template's notes (the workflow's
  own defaults are 1280×720 at 25 fps, 5 s × 25 = 125 reference frames, which it crops to
  121): fps 24; frames `{step 8, base 121, max 121}`, so **every shot is exactly 121 frames**
  (8k+1). The reference video must be ≥121 frames (model card) and no longer than the
  output (`LTXVAddGuide` asserts it), so a shorter output isn't possible without leaving
  the bucket: short shots pad up (a warning; dialogue windows trim back in assemble),
  longer ones are a build error ("split the shot"). Sizes are multiples of 32 (one
  stage, no upscaler); another target's series block lends no size. (**Superseded
  2026-09-19**, see **Shot lengths — as built**: ComfyUI's only rule is that the guide is
  no longer than the output, so a shot now renders its own length, 49–481 frames.)
- **Presets:** both `ltx-2.3-22b-distilled-fp8.safetensors` (checkpoint, audio VAE and
  text-encoder projection all read it) + `ltx-2.3-22b-ic-lora-ingredients-0.9.safetensors`
  at 1.0 (the template's; the card's 1.4 is for dev at 30 steps) as the preset LoRA,
  `gemma_3_12B_it_fp4_mixed.safetensors`, KSampler 8 steps (patched: `steps` is a real
  widget here). Final 768×448, proxy 512×288 (the bucket × 2/3, on the 32 grid). A dev
  final (dev + distilled LoRA 0.75) is left to a profile.
- **Binding:** widgets as in `ltx2`; `model` goes to the three loaders; width/height to the
  latent and `ResizeAndPadImage`; length to the latent, the audio latent and
  `RepeatImageBatch`; `H3SaveShot` replaces `CreateVideo`; prune drops the enhancer
  (`TextGenerateLTX2Prompt`, its Gemma LoRA, the switch), the size/length maths and the
  previews. The flattened, patched graph passes `check_graph` against the trimmed
  `/object_info` fixture (unchanged: it already had every class).
- **Recipe:** entries carry `panels` (characters in `who:` order, then props/vehicles, then
  the plate; a character's view is H3's rule: face on a one-character close-up, else the
  three-quarter body). Every panel's file is a required ref (`needed`/`blocked_shots` in
  refs_todo, blocked at queue time like H3). Render anyway = `compile_without`: missing
  elements leave the sheet and the `Reference sheet:` half; none left = text-only (the
  graph drops the guide, crop and IC-LoRA; the prompt is plain `ltx2` prose; the take's
  notes say so). A LoRA list from a script line or profile that lacks the IC-LoRA gets it
  put back first, with a note.
- **The sheet** is composed at queue time, not by a node (no ComfyUI restart):
  `h3jobs.stage_inputs` calls the target's new optional `stage_inputs(job, comfy)`, which
  runs `comfy_nodes/h3_refsheet.py` (PIL) as a subprocess of the running Python (clear
  error without PIL), uploads the PNG through `POST /upload/image`, and leaves it in
  `job.staged`; `start_job` moves it into the take as `<shot>_tNN_refsheet.png` and appends
  it (sha1, `role: "sheet"`) to the sidecar's `refs`, after the panel files' own entries,
  which are what make a take `ref`-stale. A dry run composes nothing.
- **Layout:** the panels tile the whole frame in rows or columns (whichever leaves least
  black and crops least), thin black lines between, **no black border or bands**: the
  first live render, from a sheet with black bands above and below its row, came back
  letterboxed exactly where the bands were (the IC-LoRA reads the reference at the output's
  size and position). The plate is cover-cropped; a figure only at its sides (≥60% kept).
- **Prompt:** `Reference sheet: <one sentence per panel: name, design, view>\n\nGenerated
  video: <the ltx2 paragraph>`, where subjects on the sheet are named, not re-described.
- **Live check (2026-09-19, scratch copy of ep05, proxy, CLI `--target`):** dry run with
  `--check-nodes` clean against the running ComfyUI (23 nodes). sh020 (Dean, medium, old
  layout): 36 s, 121 frames 512×288 + AAC, sidecar ok, thumb/strip/refsheet; Dean on
  model (cap and goggles, blue shirt, suspenders) but the frame letterboxed like the sheet.
  sh210 (Dean + Whiskers, new layout): 21 s, full frame; Dean very close to his sheet
  (cap, goggles, suspenders, the red knee patch), Whiskers recognisable (green body, face,
  antennae, cheek tufts) but its patterned wings came out as a white glow. sh060 (face
  close-up): 21 s; Dean's face matches the face panel, framed through the sill's window.
- **Left:** a dev-model final profile and whether the card's LoRA 1.4 / STG help;
  per-character face + body panels (the card's advice) instead of H3's one-panel rule;
  ~~shots longer than the bucket~~ (done: any length to 20 s, with a warning off 121);
  the editor showing the take's refsheet.

**Shot lengths — as built** (2026-09-19): LTX shots aren't forced into 5-second chunks, and
`dur: model` lets LTX-2.5 choose.
- **`ltx2_ingredients` renders the shot's own length.** Checked in ComfyUI's
  `comfy_extras/nodes_lt.py`: `LTXVAddGuide` crops a guide to 8n+1 frames and asserts
  `latent_idx + guide_frames <= latent_length`, i.e. the guide is no longer than the output;
  nothing enforces ≥121 (that is the model card's training bucket). The sheet already loops
  through `RepeatImageBatch`, whose `amount` is bound to `length`, so the template is
  `frames {step 8, base 49, max 481}` (2–20 s on the 8k+1 grid). Lengths other than
  `recipe.trained_frames` (121) get one soft `--check` warning listing them ("the IC-LoRA was
  trained at 121 frames; identity may weaken at other lengths"); past 481 frames is the
  "split the shot" error. The old "renders the whole bucket; trim it" warning is gone.
  `tests/golden/ltx2_ingredients` changed for this (lengths, `delivered_s`/`pad_frames`,
  the warnings).
- **`dur: model [min-max]`** (script) → `timing: {"model": true, "min"?, "max"?}` (IR). Every
  other `dur:` form, and every existing golden (the local real episodes included), is
  byte-identical. The build writes an **estimate** (`targets.duration_estimate`): the
  `dur: auto` length for a dialogue shot, else the preset's `default_seconds` (5), inside
  the clamp; the entry gets `length_estimated: true`. `print_pacing` measures the estimate.
- **Capability:** target.json `capabilities.duration: "predict"`, only on `ltx2`
  (`Target.duration`, and `capabilities.duration` in `GET /h3pipe/targets`). Its entries also
  carry `duration_predict: {min_seconds, max_seconds}` (the script's clamp, else the preset's
  1–20 s, capped at the template's 481 frames, at least the node's 0.5 s). H3 and
  `ltx2_ingredients` (LTX 2.3, no duration head) render the estimate: a `--check` warning
  and a take note.
- **Preset values** `duration_head` (`ltx-2.5-duration-head-bf16.safetensors`),
  `default_seconds`, `min_seconds`, `max_seconds` (`targets.DURATION_PRESET_KEYS`) are read
  from the target at build/queue time and kept out of the shotlist's `defaults`, so a
  shotlist without `dur: model` shots is unchanged.
- **Queue time** (`h3jobs.plan_duration`, run by `stage_inputs`): the head must be among
  `/object_info/ModelPatchLoader`'s `name` choices and `LTXVDurationPredictor` known.
  Yes: `graph_for` adds `ModelPatchLoader` + `LTXVDurationPredictor` (model and positive from
  the binding's `duration_predictor`: what feeds `LTXVDualCFGGuider.model` and
  `LTXVConditioning.positive`; `frame_rate` the job's fps) and links `num_frames` into every
  widget of the binding's `length` param (video and audio latents). No, or no ComfyUI to
  ask: the estimate renders, with a note ("LTX-2 duration head not installed
  (models/model_patches: …); used the estimate N s"). Never a failure. `h3render --dry-run
  --check-nodes` asks too (read-only).
- **Sidecar:** `length_source` `script` | `estimate` | `predicted` on every take (the H3
  graph snapshot test allows the new key, `script`, as it did Phase 7's `target`); the saver's
  `frames` is the real length. `h3assemble` already cut by the frames on disk (ffprobe); a
  predicted take is no longer reported as a frame-count mismatch against its estimate.
- **Status and editor:** `episode_status` adds `takes[].frames` and `cut.frames` (the
  usable cut take's saved frame count). The timeline's clip widths, sequence lengths and
  total, and Play all's clip lengths use them at the episode fps (`shotSeconds`,
  `framesOf`), so a predicted 8.3 s take shows at 8.3 s.
- **Live check (2026-09-19, scratch copy of ep05, proxy, CLI):** sh030 (Bolt, `dur: 3.04`)
  `--target ltx2_ingredients`: 73 frames (was 121), 512×288, sidecar `frames` 73, 33 s; Bolt
  on model (coffee-can body and stripes, chain arms, bottle-cap eyes, spring legs; hands
  came out gloved rather than clothespins). sh050 rewritten `dur: model 1-3` (H3 build:
  the warning; estimate 3 s) `--target ltx2`: rendered at the estimate, 73 frames, sidecar
  `length_source: estimate` and the not-installed note. The predictor graph (head forced)
  passes `check_graph` against the live `/object_info` (35 nodes). The partial proxy cut
  assembled with no frame-count mismatch.
- **Left:** install `ltx-2.5-duration-head-bf16.safetensors` in `models/model_patches`
  (from `Lightricks/LTX-2.5`, `model_patches/`, 3.8 MB) and render one `dur: model` shot for
  real; an editor mark for an estimated length.
- **Later, not built: a `clone` audio policy for LTX.** ComfyUI's `LTXVReferenceAudio`
  ("LTXV Reference Audio (ID-LoRA)", `nodes_lt.py`) transfers a speaker's identity: inputs
  `model`, `positive`, `negative`, `reference_audio` (AUDIO, ~5 s recommended, its training
  length), `audio_vae` (the LTXV audio VAE, which encodes it into the conditioning),
  `identity_guidance_scale` (default 3.0; each step runs an extra pass without the
  reference and amplifies the difference; 0 turns that off), plus `start_percent` /
  `end_percent`; outputs the patched model and both conditionings. It needs an LTX ID-LoRA
  file loaded on the model, which the user doesn't have yet. With it, `ltx2` could declare
  `clone` (the subject's `voice_sample` as the reference) instead of falling back to
  `generate`.

**Phase 9 — later**
- Script pane: `epNN.md` in a text editor with live `--check` errors beside the lines;
  save → rebuild.
- Promote to script (needs the parser to record each shot's line span).
- Drag-reorder and trims in the timeline; play-through of the cut; master dialogue
  waveform under the timeline.
- Series config editing in the UI (design sentences → regenerate refs).

## Story IR — `shotlist/shots.json` (Phase 6)

Output of parsing, before any model decision. Durations are **seconds**; no frame
grid, no `<Picture N>`, no H3 vocabulary. Sketch (fields follow the current parser):

```jsonc
{
  "episode": "ep01", "title": "The Gold Path",
  "series": {"fps": 24, "width": 1344, "height": 768, "target": "minimax_h3_ref2va"},
  "sequences": [{
    "id": "sq01", "location": "street", "continuous": false,
    "shots": [{
      "id": "sh020",
      "cast": ["riley", "huey"],          // on-screen characters, script order
      "props": [],                        // with:
      "plate": "street",                  // location/angle key into the series config
      "size": "medium",
      "camera": "pushes in with small amplitude at slow speed on the two of them",
      "action": "Riley runs into frame from the left and stops hard beside Huey…",
      "dialogue": [
        {"speaker": "riley", "delivery": "breathless and certain", "mode": "on",
         "line": "There's gold at the end of the path."},
        {"speaker": "huey", "delivery": "flat", "mode": "on", "line": "There is not."}
      ],
      "sound": "running footsteps on grass…", "music": null,
      "timing": {"audio_in": 3.10, "audio_out": 7.40},   // or {"seconds": 3.0} / {"auto": true, "pace": "normal"}
      "audio": "dub_keep_foley",           // intent: generate | dub | dub_keep_foley | clone
      "preserve": "loose",                 // neutral retention: strict | loose | style
      "seed_key": "ep01/sq01/sh020",
      "target": null,                      // per-shot target override
      "profile": null,                     // named render profile
      "overrides": {"model": null, "loras": null, "steps": null}
    }]
  }]
}
```

Neutral pieces already in `h3build.py` that move here unchanged: `parse_script`,
`split_parenthetical`, `syllables`, `pacing`, `speech_seconds`, `forced_rate`,
`stable_seed`, `ScriptError`, the VO/OS rules.

Vocabulary to neutralise:
- `retention: fully_copy | partially_copy | reference` → `preserve: strict | loose | style`
  (H3 target maps it back; keep accepting the old words in the script as aliases).
- `policy:` values are already reasonable intents — keep them; each target declares
  which it supports and fails loudly on the rest.

## Targets — `targets/<kind>/<id>/` (Phase 7)

A target bundles four things (Sonder's split plus a workflow binding):

| Part | Answers | H3 today |
|---|---|---|
| **template** | legal lengths, sizes, fps | 17k+5 frames @24fps, max 3592, width/height %32 (`snap_up`, `GRID_*`) |
| **recipe** | how subjects/plates/voices become inputs | Pictures 1–3 subjects (4-panel sheets, `panel_view`), Picture 4 plate, ≤3 voice refs |
| **binding** | which workflow, which node/widget takes model/LoRAs/steps/seed/inputs | `H3_Ref2VA_Shotlist_v1.json`, `UNETLoader.unet_name`, `LoraLoaderModelOnly` (`h3render.graph_for`) |
| **prompt** (code) | the text structure | `build_prompt` — six-section Ref2VA prompt |

Plus **presets** (final/proxy: model, LoRAs, steps, size) — today the `FINAL_*`/`PROXY_*`
constants and the `series`/`proxy` blocks of `series.json` — and **profiles**: named
bundles of target + model + LoRAs + steps (e.g. `dialogue_close`, `action_wide`)
declared in `series.json` and picked per sequence or shot with `profile:`, so a kind of
shot gets its model setup without repeating it on every shot.

```
targets/
  video/minimax_h3_ref2va/
    target.json        template, recipe, binding, presets
    prompt.py          compile(shot_ir, series_cfg, ctx) -> CompiledShot
    workflow.json      the graph (moved from workflows/)
  image/krea2/
    target.json
    prompt.py          reference prompts: character sheet views, prop, plate
    workflow.json      krea2_refs_t2i.json
```

Interface (sketch):

```python
class Target(Protocol):
    id: str
    kind: Literal["video", "image"]
    template: Template        # snap(seconds) -> frames, validate(width, height)
    recipe: Recipe            # pack(shot, series_cfg) -> slots, required refs
    binding: WorkflowBinding  # graph + widget map
    presets: dict[str, Preset]
    def compile(self, shot: ShotIR, series_cfg: SeriesConfig, preset: Preset) -> CompiledShot: ...
    def required_refs(self, shot: ShotIR, series_cfg: SeriesConfig) -> list[RefRequest]: ...
```

`binding` example:

```json
{
  "workflow": "workflow.json",
  "loader": {"class_type": "H3ShotListLoader"},
  "saver":  {"class_type": "H3SaveShot"},
  "params": {
    "model": {"class_type": "UNETLoader", "field": "unet_name"},
    "loras": {"class_type": "LoraLoaderModelOnly", "name": "lora_name",
              "strength": "strength_model", "chain": true}
  }
}
```

`chain: true` means additional LoRAs are inserted as more loader nodes after the bound
one. The editor's pickers come from `binding.params` plus ComfyUI's model lists, so they
work for any target.

Target selection (as built in Phase 7): `series.target` (default `minimax_h3_ref2va`) →
sequence profile → sequence `target:` → shot profile → shot `target:` → (later) a UI
override. Phase 7 builds one target per episode: a shot that resolves to any other target
is a build error that says so ("Episodes that mix targets arrive with Phase 8").
(Phase 8: mixed episodes build; the UI override is `overrides.json`'s shot `target`, and
only an unknown target is an error.)

**Phase 8 design: per-target shotlists.** Build groups shots by resolved target. The
series target keeps writing `shotlist/shotlist.json` / `shotlist_proxy.json` exactly as
now; every other target writes `shotlist/shotlist.<target>.json` /
`shotlist.<target>_proxy.json` with only its shots, and a top-level `"target"`
(`h3jobs.shotlist_target` already reads it; its absence means the default). `shots.json`
stays one file. `refs_todo` merges every target's ref requests (the image refs are the
same series refs; only a target's recipe can add, e.g., keyframes). Then:
- `h3jobs.load_shotlist(root, pass_)` becomes "the shotlist holding this shot":
  `plan_job` takes (root, pass, shot id) and finds the file; `plan_episode` walks all of
  them in `shots.json` order.
- `h3edit.episode_status` merges them in cut order; each shot already carries `target`.
- Seeds don't change: `stable_seed` is per shot id, whatever the target.
- Retargeting one shot from the inspector writes `target` into `overrides.json`'s shot
  block (not per-target); the next build or a queue-time recompile moves it.

Things in `h3build.py` that are H3 leaking into the core today, and where they go:

| Now | Goes to | Phase 7 |
|---|---|---|
| `snap_up`, grid constants, `%32` check | H3 template | moved: `target.json` `template` + `targets.Template` |
| slot ordering, `panels`/`panel_view`, `background` = Picture 4 | H3 recipe | moved: `recipe` + `compile.py` |
| max 3 voice refs in clone mode | H3 recipe (validation) | moved: `recipe.voice_slots` |
| `continuous` chaining costs 22 frames warning | H3 template (warning hook) | moved: `template.continuous` + `Template.continuous_warning` |
| `RETENTIONS`, `RETENTION_DEFAULT` | H3 prompt/recipe mapping from `preserve` | moved: `recipe.retention` |
| `build_prompt` | `targets/video/minimax_h3_ref2va/prompt.py` | moved |
| `FINAL_*`, `PROXY_*`, the LoRA-steps mismatch warning | H3 presets | moved: `presets`; the check in `compile.py` |
| `need(...)` reference prompts (4-panel sheet 4096x1024, prop 1024x1024) | split: the *shape* of the ref (4-panel sheet) is the video recipe's `RefRequest`; the *wording* belongs to the image target | moved: `RefRequest` from `compile.py`, wording in `krea2/prompt.py`; refs_todo's size hints are `recipe.size_hints` |
| `kreagen.VIEWS`, `VIEW_TMPL`, `SAMPLER`, workflow constants | `targets/image/krea2/` | moved; `h3refs` re-exports them |
| `h3jobs.ref_slots` (Picture 1–3/4, Audio 1–3) | H3 recipe | moved (found in Phase 7) |
| `h3jobs` `UNETLoader` / `LoraLoaderModelOnly` / loader / saver names | binding | moved |

Found and left in Phase 7:
- `comfy_nodes/h3_shotlist.py` (the H3 loader) keeps its own `snap_up`, /32 check and
  slot logic. It *is* the H3 target's loader node (`binding.loader`), per-target by the
  open question below; a second target brings its own loader.
- `h3align` snaps with `h3build.snap_up`, i.e. H3's grid, whatever the series target.
  It needs the episode's target (`targets.video_target(series_cfg).template`).
  (Fixed in Phase 8: each shot snaps to its own target's grid, `h3align.shot_grids`.)
- `h3edit.episode_status` exposes `audio_policy`, `subjects` and `length` straight from
  the built H3 entry; an LTX shotlist must carry the same keys or the bin changes.
  (Fixed in Phase 8: `ltx2` entries carry them.)
- `refs_todo.json` still calls each ref's size hint `target` (a name from before targets
  existed); renaming it is a golden change.
- `h3render` and the Refs routes resolve the workflow of the *default* target's binding
  (`h3jobs.WORKFLOW_NAME`) for the CLI; the render route already uses the shotlist's
  target. Harmless with one target per kind. (Fixed in Phase 8 for `h3render` and the
  render route: each job's own target. The Refs routes still use the one image target.)
- The kreagen views are the krea2 target's way to make a 4-view sheet; a 4-view sheet is
  still what every character ref *is* (`h3refs`, `mksheet`). That's the series ref's
  shape, fine while every video target consumes sheets.

## Phase 8: what LTX needs that Phase 7 doesn't cover

(Written before Phase 8; kept as the reasoning. What was built is **Phase 8 — as built**.)

- **Template:** an `8k+1` grid is expressible (`frames: {step: 8, base: 1, max: …}`), but
  LTX also wants width/height multiples of 32 *and* a max resolution per model, and may
  prefer a different fps (25/30): `Template` has no max size and treats fps as fixed.
- **Recipe:** H3's slots are named pictures; LTX takes *image conditioning at frame
  indices* (first/last keyframe, optional middle frames) with a strength each. A recipe
  needs "conditioning items" (`{ref, frame_index, strength}`) rather than slot names, and
  `RefRequest` needs the `keyframe` shape (`shot:<id>:first|last` refs exist in h3refs
  but nothing generates them). A shot with no keyframe must still render (T2V).
- **Subjects without slots:** LTX has no identity refs, so characters are described in
  words (the `_unreferenced` path in the H3 prompt is the same idea) or given a
  first-frame still generated from the sheet + plate — a new image-target job (img2img /
  composition), not one kreagen does today.
- **Prose prompt:** `prompt.py` returns one string, not six sections; the editor's diff
  and the loader already accept a string.
- **Audio:** LTX 2.x generates audio with the video but takes no voice reference, so
  `clone` / `dub` have to be rejected or mapped to `generate` + a recording laid over in
  assemble. Policies are target-declared (`recipe.policies`) but nothing validates a
  shot's policy against the target yet.
- **Binding:** LTX's graph has no `H3ShotListLoader`; either a generic loader node or an
  LTX loader that reads the same frozen shotlist, and the seed/steps are probably patched
  on the sampler nodes directly (`{"class_type", "field"}` params, already supported).
  Frames/size are widgets too (`EmptyLTXVLatentVideo.length/width/height`): add them to
  `binding.params` and have `graph_for` patch every `{"class_type","field"}` param from
  the job, not only model/LoRAs/steps/seed.
- **Two-stage / upscaler passes and distilled vs dev models:** presets hold one
  model + one LoRA string; LTX setups often need a model *and* a separate upscaler or
  distilled LoRA per pass. `Preset.extra` can carry them, but `plan_job` only reads
  model/lora/steps.
- **Per-target shotlists** (design above), and the override writer using the shot's
  target (`h3edit.set_shot_override` takes `target`; the CLI still passes the default).

## Source of truth

| File | Owner | Rule |
|---|---|---|
| `series.json`, `epNN.md` | you | authored. The editor writes here only via "promote to script" (Phase 9). |
| `overrides.json`, `refs/_overrides.json`, `cut.json` | you / the editor | editor-owned, hand-editable, never touched by build |
| `shots.json`, `shotlist*.json`, `refs_todo.*` | build | generated; overwritten every build; never hand-edit |
| take files (frozen shotlist, sidecar, mp4, thumbs) | queue / render | write-once, except the sidecar's status/`finished`/`note` |
| `refs/<picked path>` | pick | a copy of the picked ref take |

## Open questions

- Generic loader node vs one loader per target. H3's loader does real work (panel
  cropping, audio policy). Start with per-target loaders; revisit after Phase 8.
- Package name/layout: keep flat scripts as thin CLIs over a package, or restructure fully?
- `h3plan.py` (legacy chained compiler): keep, move to `legacy/`, or delete?
- Where does the editor live: this repo (`comfy_nodes/` + `web/`) or its own repo?
- Take cleanup: a "discard take" that moves files to `renders/_trash/` rather than deleting?
- Layered LoRAs from several levels (series + profile + shot): does a lower level
  replace the list or append to it? Default to replace until real use says otherwise.
  (Phase 7 implements replace: the most specific level that names LoRAs wins outright.)

## Defaults to revisit in Phase 4

These were chosen without strong evidence; overrule them once the UI is in use.
- The cut may mix passes (a proxy as placeholder for a missing final).
- Reordering is allowed anywhere in the cut, not only within a sequence.
- The redo dialog's "save as override" checkbox is on by default.
- The script pane (Phase 9) is preferred over a field-by-field inspector that writes the
  script.
