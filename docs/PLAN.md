# h3pipe — plan: a shot/take editor, then model-agnostic targets

Status (2026-09-19): Phases 0–3 and 5–8 done; Phase 4 (evaluate) continues through use. Video targets:
`minimax_h3_ref2va` (default), `minimax_h3_fl2va` (H3 from first/last keyframes; dub anchors the recording),
`ltx2` (LTX-2.5; `dur: model` predicts length once the duration head is installed), `ltx2_ingredients`
(LTX-2.3 + IC-LoRA reference sheet: identity), `wan22_i2v` / `wan22_ti2v` / `wan22_vace` (Wan 2.2, silent,
16/24 fps; assemble converts fps). Model files are checked against each target's family (name patterns +
safetensors header). Keyframe continuity is in the CLI, the routes and the editor. Readiness (backend,
2026-09-19): each target's model params have tiers (required / accelerator / optional), accelerated presets a
`base`, and `downloads` from trustworthy records only; queue time resolves every file to an installed one of its
family (a missing turbo LoRA renders the base preset, a missing required file skips the shot with its download
link); `GET /h3pipe/targets?ready=1` and `h3.py targets` report what each target is missing; the episode target
(`overrides.json` `episode.target`) sits between the script and `series.target`. See **Readiness and the
episode target (as built)** below. Open items: the per-target lists under Phase 8 / Wan / shot lengths, and the
Phase 9 editor items.
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

**MiniMax H3 FL2VA — as built** (`targets/video/minimax_h3_fl2va/`, from ComfyUI's H3
image-to-video template as the user saved it, `video_minimax_h3_i2v.json`, known to the
binding as `h3pipe_minimax_h3_fl2va.json` so an edited canvas can't leak in; label "MiniMax
H3 FL2VA (first/last frames)", short `H3 FL2V`)
- **Template:** H3's (fps 24, 17k+5 up to 3592 frames, sizes /32), without the continuous
  chain (a shot continues the last through its first keyframe). A shot past the trained
  362 frames is a build warning.
- **Binding:** no loader; widgets as in `ltx2`: prompt / width / height / length on
  `MiniMaxH3ImageToVideo`, `BasicScheduler.steps`, `RandomNoise`, `KSamplerSelect`
  (`sampler`, a preset value), the UNET, CLIP and both VAEs. The template has no LoRA
  loader: one is inserted after the UNETLoader (`insert_after`, as `ltx2`). `H3SaveShot`
  replaces `CreateVideo`; prune drops `SaveVideo`, the size selector and the duration maths.
  Checked with `check_graph` against a trimmed `/object_info` from the running ComfyUI
  (`tests/fixtures/workflows/object_info_h3_fl2va.json`).
- **Presets:** final `minimax_h3_fl2va_pruned_int8_convrot` + `minimax_h3_fl2v_turbo_8step_v1.0`,
  8 steps, `res_multistep` (the t2v template's lightning branch), 1344×768; proxy the 4-step
  lightx2v v0.1 LoRA, 4 steps, `euler` (the user's 4-step H3 workflows), 448×256. Not the
  SLA LoRA (it needs the block-sparse attention node).
- **Recipe:** no subject pictures (subjects and place in words). Keyframes
  `shot:<id>:first|last`, optional, staged and uploaded by `h3jobs.stage_inputs` like
  `ltx2`'s, wired straight into `first_frame` / `last_frame` (the node resizes; the
  template's 1 MP pre-scale goes); neither = text-to-video. `capabilities.keyframes`
  `["first", "last"]`.
- **Prompt:** H3's base-mode format (the prompt-writing spec's T2VA/I2VA/FL2VA/L2VA: the
  Ref2VA six sections minus the three that exist for `<Picture N>` references):
  `integrated_multimodal_description: [Shot 1] <look>, <size> ...`, subjects from their
  `design`, the location scaled to the framing, `(Sn)` speaker ids, `<d>[English] …</d>`,
  the voiceover phrase and lips-closed clause; then `overall_soundscape` and
  `non_diegetic_music`. Keyframes are known only at queue time, so the target's
  `stage_inputs` adds the spec's alignment line for the frames the render has (I2VA,
  FL2VA or L2VA wording; comfy's tokenizer labels the frames `<Picture 1>`, `<Picture 2>`
  in first/last order) and a landing sentence; idempotent, and the frozen shotlist carries
  the prompt the graph got.
- **Audio:** policies `generate`, `dub`, `dub_keep_foley`; `clone` falls back to generate
  with a note (no voice-reference slot). Dub: `MiniMaxH3AddGuide` takes audio as well as
  images (the model packs it as conditioning audio on the target's time axis, like a
  keyframe's latent), so the target cuts the shot's `audio_in`–`audio_out` slice (stdlib
  `wave`, ffmpeg for other formats, mono made stereo), uploads it with the same
  `/upload/image` route (`LoadAudio` reads `h3pipe/<sha1>.wav`), anchors it at frame 0
  and keeps it in the take as `<shot>_tNN_dub.wav`. The recording is a required ref:
  missing blocks; render anyway recompiles the shot as generate (`compile_without`).
- **Live check (2026-09-19, scratch copy of ep05, proxy, CLI `--target minimax_h3_fl2va`
  after `--dry-run --check-nodes`, 16 nodes clean):** 42 s for all three, no restart.
  sh050, first keyframe from continuity: frame 0 vs keyframe 24.2 dB PSNR (mean abs error
  10.0/255). sh040, first + a last made with `h3.py keyframe sh040 --last` (sh050's first
  frame): frame 0 23.3 dB (10.6), frame 72 vs the last keyframe 24.2 dB (9.7). sh060,
  text-only, 90 frames. Each: sidecar `ok`, frames on the 17k+5 grid (73, 73, 90),
  448×256, 24 fps, stereo AAC, thumbnail and strip written. Frame 0 keeps the keyframe's
  composition and drawing but isn't a pixel copy (H3 conditions on it; LTX's in-place
  first frame measured 27.4 dB).
- **Left:** a live dub render (ep05 has no recording; the wiring is tested offline);
  whether `retention:` should map onto anything here; the anchored dialogue's lip sync
  quality; an FL2VA final at 8 steps checked by eye.

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

**Wan 2.2 — as built** (2026-09-19; `targets/video/wan22_i2v`, `wan22_ti2v`, `wan22_vace`,
shared code in `targets/video/wan/`: `common.py` the compile, ref slots and graph helpers,
`prompt.py` the prose)
- **Workflows:** API graphs in each target folder (no switches, primitives or subgraphs; every
  node a binding names has a `_meta.title`), known to the binding by `h3pipe_wan22_*.json`
  names so a canvas can't leak in. `wan22_i2v` is ComfyUI's Wan 2.2 14B I2V template as the
  user saved it (`video_wan2_2_14B_i2v.json`), subgraph flattened, its "4steps LoRA?"
  switches resolved to the LoRA branch. `wan22_ti2v` has the nodes of the user's
  `image_to_video_wan22_5B.json` (Wan22ImageToVideoLatent, KSampler uni_pc), ending in
  CreateVideo/SaveVideo instead of its WEBM/WEBP previews. `wan22_vace` was built from the
  node set (none was saved): the Fun VACE high/low pair, one `WanVaceToVideo`, the two
  `KSamplerAdvanced`, `TrimVideoLatent` on the node's `trim_latent`. All three pass
  `check_graph` against the live `/object_info` (trimmed into
  `tests/fixtures/workflows/object_info_wan.json`) in every keyframe/reference combination.
- **Binding format, extended for two stages** (14B): `model` is the high noise UNETLoader
  and `model_low` the low one (widget params with a `title` selector); `loras` may be a
  **list of LoRA specs, one per stage** (`title` picks the stage's loader, `stage` names
  it; `insert_after` may name a `title` too, for VACE, which has no loaders).
  `h3jobs.graph_for` applies one chain per spec with `h3jobs.loras_for`: a LoRA goes to the
  stage its `stage` names, else by its name (`*high_noise*` / `*low_noise*`), else to both;
  an empty list leaves each stage's loader at strength 0. `binding.stages`
  (`{high|low: {sampler, model}}` titles) is read by `wan/common.split_stages`
  (`patch_graph`): the high noise sampler runs steps `[0, split)` with noise, the low
  `[split, 10000)`, `split = round(steps x preset split)` (0.5), at least one step each.
  The preset can name a per-stage LoRA list (`loras` in `defaults`): `h3jobs.plan_job` uses
  it when no `lora` string names one (a script `lora:` line or profile still replaces it).
  `preset_hash` includes `model_low` / `loras` only when a shotlist has them.
- **Templates:** `4k+1` frames; 14B at **16 fps** (the template's CreateVideo and its
  `floor(s * 16 + 1)`), 5B at **24** (the saved workflow's savers), whatever `series.fps`
  says. Trained 81 / 121 frames (a warning past them), max 161 / 241 (10 s; an error:
  split). Sizes: multiples of 16 (14B: WanImageToVideo / WanVaceToVideo's step) or 32 (5B:
  Wan22ImageToVideoLatent's), snapped under 720p. Presets: 14B 832x480 final (Wan's 480p;
  the template says 640x640), 640x352 proxy; 5B 1280x704 / 640x352. Another target's
  series block lends **no size** (H3's 448x256 proxy is far below Wan's 480p).
- **wan22_i2v:** the lightx2v 4-step LoRA pair at 1.0, 4 steps split 2/2, cfg 1, euler,
  shift 5. The first keyframe is **required with no render-anyway**: its ref slot carries
  `anyway: false` and `why` ("Wan 14B I2V needs a first frame: use continuity or import
  one, or retarget to wan22_ti2v"); `plan_job` keeps such a shot `blocked` even with
  `allow_missing_refs`, `Job.blocked_reason()` is what `h3render`, `queue_shots` (the
  render route's `skipped[].reason`) and the editor show, and `episode_status` passes
  `anyway`/`why` in `missing_refs`. The build warns once per episode. A last keyframe too
  swaps `WanImageToVideo` for `WanFirstLastFrameToVideo` (same inputs plus `end_image`).
- **wan22_ti2v:** 20 / 12 steps, cfg 5, uni_pc, shift 8; text-to-video, or its optional
  first keyframe into `start_image` (disconnected, LoadImage pruned, without one).
- **wan22_vace:** no turbo LoRA fits it (the lightx2v pair is for the I2V models), so cfg 3.5,
  20 steps final / **10 proxy** (5 + 5), euler, shift 8. **Identity:** `WanVaceToVideo`
  takes ONE `reference_image` (the first of a batch, scaled and centre-cropped to the render
  size, encoded as an extra leading latent frame that `TrimVideoLatent` removes). It is
  composed at queue time by `comfy_nodes/h3_refsheet.py` (new `background: "white"`: VACE
  pads its references on a white canvas), one panel per subject at the render size (the
  ingredients' view rule), **no plate** (`reference_image.plate: false`; a scene beside the
  subjects reads as another subject; the prose places them), kept as
  `<shot>_tNN_reference.png`. Panels are required refs (blocked like ingredients; render
  anyway drops them; none left disconnects the input). **Keyframes** are the standard VACE
  first/last control: `control_video` = [first] + mid-grey `EmptyImage` frames + [last]
  (`ImageScale` centre crop to size, `ImageBatch`), `control_masks` = `ImageToMask` of
  black (keep) / white (generate) frames.
- **Audio:** `capabilities.audio: "none"` (new in `Target.capabilities`, `"generate"`
  elsewhere); `recipe.policies: ["silent"]` with `policy_fallback` silent, so every intent
  renders silent with an `audio_note` in the entry and the take's `notes`. The build warns
  once ("makes no sound: N shot(s) render silent") and once for dialogue ("no audio or
  lip-sync on Wan (the lines are acted silently)"). `H3SaveShot` gets no `audio` input and
  writes a mute mp4; assemble lays silence under it.
- **Prompt** (`wan/prompt.py`): the `ltx2` paragraph's structure (look, framing and camera,
  subjects from `design`, extras, action, on-screen text) without sound or music; each run
  of lines is acted silently ("Ada talks, warmly, mouth moving with the words."), a V.O.
  by someone in frame keeps their lips closed, an O.S. line adds nothing. Negative prompt:
  Wan's standard Chinese one from ComfyUI's templates (a preset value).
- **fps mixing:** every sidecar records `fps` (`h3jobs.sidecar_for`; `H3SaveShot` also writes
  it, from the next ComfyUI start; the H3 graph snapshot allows the new key at 24.0).
  `h3assemble` converts each clip to the episode fps (`series.fps`, else the shotlist's, else
  24) with `fps=<rate>` + a cloned-tail `tpad` in `conform` (no speed change), judging a
  converted clip by duration on a running clock (`acc_s` exact seconds, `acc_f` frames laid:
  each clip's frames are `round((acc_s + its seconds) * fps) - acc_f`, so the cut never
  drifts more than half a frame), and also scales any clip whose size isn't the cut's (a
  mixed-target cut used to concat mismatched sizes). `episode_status` adds `takes[].fps` and
  `cut.fps` (the take's, else the shot's target's) and computes `seconds` at the shot's own
  rate; the editor's `shotSeconds`, `trimWindow` (a `clipFps`) and Play all use them; the
  viewer steps a frame at the take's rate.
- **Model families:** each `target.json` has `models: {param: {patterns, family, class_type,
  field}}` (`model`, `model_low`, `text_encoder`, `vae`) with the family ids the model
  identification uses.
- **Live check (2026-09-19, scratch copy of ep05, proxy, CLI `--target` after `--dry-run
  --check-nodes`, no restart):** `wan22_ti2v` sh060 text-only: 21 s, 77 frames at 24 fps,
  640x352, mute; Dean from his `design` alone (cap, goggles, blue shirt). `wan22_i2v` sh050
  from its continuity first frame: 21 s (both 14B models loaded), 49 frames at 16 fps,
  frame 0 vs the keyframe 32.9 dB PSNR (mean abs error 4.0/255); Whiskers crawls in beside
  Dean's jar. `wan22_vace` sh020 with Dean's sheet: 66 s at 10 steps / cfg 3.5, 65 frames at
  16 fps; Dean on model (cap, blue shirt, suspenders, khaki trousers, brown boots) though
  seen from behind and in a darker workshop than the style asks. sh060 on `wan22_i2v`
  without a first frame: blocked with the reason. The mixed H3 / LTX / ingredients / Wan
  proxy cut (7 shots) assembled in 4 s: 665 frames, 27.708 s video, 27.729 s audio, the
  16 fps clip laid as 73 frames; at 0, 1, 1.5, 2.5 and 3 s into it the cut's frame
  matches the source frame of that time (no speed change); four clips of other sizes
  (512x288 ingredients, 640x352 Wan) scaled to 448x256.
- **Left:** a turbo LoRA for VACE (a lightx2v T2V A14B pair) to bring its proxy near the
  others' speed; whether the plate helps VACE as a second reference (ComfyUI's node takes
  one image; several would need the latent-concatenating node); Wan's negative prompt
  says "风格/画作" (style, painting), which may fight a cartoon look (a series-specific
  negative is a preset value away); final-quality renders (832x480 I2V/VACE, 1280x704 5B)
  not yet tried; the editor's model picker for `model_low`; `H3SaveShot` writing `fps`
  needs a ComfyUI restart to take effect (the queuer's value covers it until then).

**Phase 8.5 — refs and keyframes** (planned 2026-09-19; contract in `docs/API.md`, "Phase 8.5")
- **More image models for refs, chosen like video targets:** `z_image_turbo`, `flux2_klein` (t2i),
  `flux2_klein_edit` (edit with reference images), `flux_kontext`, from the user's saved
  workflows. The series config's `refs` block holds the defaults; the UI can override them per ref.
- **Keyframes are needed refs, decided by the build from each shot's target** (Wan I2V needs a
  first frame; LTX / H3 FL2V can use first/last). The optional script lines `first:` / `last:`
  (`continuity | generate | import | none | <path>`) choose how each is filled. Generate makes a
  still from the shot's own description; an edit model also gets the picked character views
  and plate, so identity carries into the keyframe.
- **Clear:** unpick a ref (a keyframe: the shot stops using one).
- **Negatives:** the episode's `negative.txt` feeds every target that takes a negative (Wan,
  LTX, image models). Precedence: request → shot → `negative.txt` → series config → preset.
- **Inspector:** a read-only strip of the refs the shot uses, and the reference sheet a take
  rendered with. Generating and picking stay in the Refs tab.
- **Small:** a "≈" mark on shots whose length is an estimate; a picker for Wan's low-noise model;
  image targets resolve model files by family at queue time.
- **Dropped:** one shared series config per series. It lives in each episode folder, and copies
  are cheap.
- **Afterwards:** revisit Phases 1–8 for anything missing or worth doing, then Phase 9.

**Phase 8.5 — backend as built** (2026-09-19; docs/API.md "Phase 8.5 as built" lists every
deviation)
- **Image targets** `z_image_turbo`, `flux2_klein`, `flux2_klein_edit` (Klein 9B KV: its models
  are installed, the 9B base edit's aren't), `flux_kontext`, from the user's saved workflows as
  titled API graphs (`h3pipe_*.json` names; the canvases' LoRAs left out). Shared code:
  `targets/image/common.py` (krea2's wording for series refs, the keyframe prompt, the
  reference chains). No Illustrious/SDXL target (no saved t2i workflow fits). modelid gained
  Z-Image, Klein 9B, FLUX.1 (dev/schnell; Kontext by name), Qwen3 4B/8B, Qwen3-VL 4B, CLIP-L,
  T5-XXL and the FLUX.1/FLUX.2 VAEs, from headers only. Readiness on this machine: all five
  image targets ready.
- **h3refs** generates through whichever image target applies (request → ref override →
  episode (`overrides.json` `episode.refs_target` / `keyframe_target`) → series config `refs`
  → krea2 / Klein edit when ready), resolving files by family (`resolve_job_models`) and
  uploading an edit target's references. kreagen gained `--target` and `--clear`.
- **Keyframes as needed refs** (`h3refs.keyframe_needs`), `first:` / `last:` in the script and
  IR (omitted when unset: every golden unchanged), the keyframe prompt, `h3.py keyframe
  --generate / --missing / --clear`, `DELETE /h3pipe/refs/pick` with a sticky clear.
- **Negatives** (`h3jobs.negative_for`), `refs_used`, `reference_image`, `length_estimated`,
  the `model_low` / `negative` override fields, `PUT /h3pipe/refs/defaults`.
- **Goldens:** only `tests/golden/wan22_i2v/kitchen_sink*.json` changed (intended): the Wan
  first-frame warning now says "generate one or use continuity (or import one)".
- **Live check (2026-09-19, scratch copy of ep05, 4 renders):** `z_image_turbo` plate of
  `workshop_bench` (1344x768, 11 s; on the look, vise, gears, bolt can, window). `flux2_klein_edit`
  sh030 first frame with Bolt's face panel cut from his sheet + the plate (1200x656 for a
  640x352 Wan proxy, 13 s): Bolt on model (glass lantern head with teal cap, bottle-cap eyes,
  striped coffee-can body, chain arms, clothespin hands, spring legs, wind-up key) in the
  plate's workshop; framed medium rather than the script's close-up. `wan22_i2v` proxy of sh030
  from that frame (after `--clear` of a stale last frame): 49 frames at 16 fps in 27 s, pushes
  in, clothespin hands wave, stays on model. `flux_kontext` (one reference, the face panel):
  face and body colours right, but hands drawn as gloves and an invented kitchen set (no plate
  at `max_refs` 1).
- **Left:** a keyframe framing hint stronger than the size word (Klein drew the close-up
  medium); Kontext's single reference could be a composed sheet (character + plate); the editor
  side of `PUT /h3pipe/refs/defaults` (the UI agent's); `--missing` from the editor as one
  route, if the UI's keyframe-then-generate fallback proves clumsy.

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

## Model families (as built)

Nothing used to check that a model file suits its target: the picker listed every file in
the loader's folder, and names vary by conversion (int8 / fp8 / nvfp4, "convrot", "comfy",
renamed by hand). Now:

- **Declared:** `target.json` `models: {"<param>": {"family", "patterns"}}` for every
  param that takes a model file (`folder` / `class_type` / `field` default from the
  binding's loader, `targets.MODEL_FOLDERS`). The series config's `model_families` adds
  name patterns per family.
- **Fingerprint:** `targets/modelid.py` reads only the safetensors header (8-byte length +
  JSON). Signatures were built from the installed files' headers (2026-09-19), nothing else:

  | Family | Signature | Confidence |
  |---|---|---|
  | `minimax-h3` (Ref2VA, FL2VA) | `adaln_t_table`, `video_patch_proj`, `audio_patch_proj`, `condition_proj`, `token_refiner.*`, 50 `blocks` | tensors. **Ref2VA and FL2VA are identical** (same names, shapes, dtypes, offsets): the name decides |
  | `ltx2.3` / `ltx2.5` | AV transformer (`patchify_proj`, `audio_patchify_proj`, `av_ca_*`, `audio_ff`); 2.5 adds `keyframes_abs_pos_embedding` and drops the video FF biases | metadata (`model_version` 2.3.0 / 2.3.rc1 / 2.5.0) and tensors agree. Told apart by header (one 2.5 file to compare) |
  | LTX video VAE 2.3 / 2.5 | 2.3 `decoder.up_blocks`, 2.5 `decoder.det_stages` + `diff_blocks` | metadata + tensors |
  | LTX audio VAE 2.3 / 2.5 | `audio_vae.*` + `vocoder.*` | same tensors: metadata only |
  | LTX latent upscaler 2.3 / 2.5 | `res_blocks`, `upsampler`, `final_conv` [128, …] | same tensors, no metadata: name only |
  | LTX 2.5 text encoder | Gemma 4 (vocab 262144) + `text_embedding_projection` | tensors |
  | `gemma3-12b` (LTX 2.3's) | vocab 262208, 48 layers, SigLIP vision tower | tensors |
  | `qwen3vl-32b` (H3's) | `visual.deepstack_merger_list`, merger out 5120, q_proj 8192 | tensors |
  | MiniMax H3 VAEs | video: `decoder.register_tokens`; audio: `pre_block`, `dec_in_proj` | metadata key + tensors |
  | `wan2.2-i2v-14b` (high / low) | `patch_embedding` [5120, 36], 40 blocks, no `img_emb` | tensors; **high and low are identical**: name |
  | `wan2.2-vace-14b` (high / low) | `vace_patch_embedding`, `vace_blocks`, patch in 16 | tensors; high/low by name |
  | `wan2.2-ti2v-5b` / `wan2.2-fun-inpaint-5b` | `patch_embedding` [3072, 48] / [3072, 100], 30 blocks | tensors |
  | Wan 2.1 / 2.2 VAE, UMT5-XXL | latent 16 vs 48 channels; `shared` [256384, 4096] | tensors (Qwen-Image's VAE reads as Wan 2.1's) |
  | `krea2` | `txtfusion.*`, `first`, `last`, `tmlp`, `tproj` | tensors (fine-tunes pass too) |

  int8 "convrot", fp8 "scaled", nvfp4 and "comfy" repacks keep the tensor names; a
  `model.diffusion_model.` prefix is stripped. Where the header can't narrow a family, a
  child family is picked by name (builtin hints, the targets' patterns, the series config's).
  Wan 2.2 T2V 14B, Wan 2.1 and the LTX duration head aren't installed, so they have no
  signature (unknown: warned, never blocked).
- **Queue time** (`h3jobs.check_models`, from `h3edit.queue_shots` and `h3render`): name
  match passes; a fingerprint match passes with a sidecar note; another family blocks the
  shot (action `mismatch`, skipped with the reason) unless `allow_model_mismatch`; unknown
  warns. A target family `modelid` doesn't know is never blocked (nothing to compare).
- **Routes / UI:** `GET /h3pipe/targets` carries `models`; `GET /h3pipe/models` lists one
  param's files by match; the picker groups them and the redo dialog offers "Render anyway
  (model mismatch)". See docs/API.md **Model families**.
- Not done: reference images (`h3refs`, krea2) aren't checked at queue time, only listed.

## Readiness and the episode target (as built)

The goal: know what's missing for a target, and where to download it, before rendering;
and pick a target for a whole episode. docs/API.md **Readiness, requirement tiers, and the
episode target** is the contract; its "as built" notes list where the build differs.

- **Tiers** (`target.json` `models.<param>.tier`). Accelerators: the H3 Ref2VA / FL2VA turbo
  LoRAs, the Wan 2.2 I2V lightx2v pair, and LTX-2.3 ingredients' distilled checkpoint.
  Optional: the LTX-2.5 duration head. Everything else is required. No voice ID-LoRA is
  referenced by any target, so none is marked. New params where a workflow loads a file on
  its own (`default`): H3 Ref2VA's text encoder and VAEs, krea2's text encoder and VAE.
  `loras` is a models param now (family, tier, `keep` / `exclude`).
- **Base presets** (`presets.<pass>.base`, never written to a shotlist), with their sources:
  - H3 Ref2VA and FL2VA: no LoRA, 20 steps, `res_multistep`, no CFG. From ComfyUI's H3
    templates (`video_minimax_h3_r2v.json`, `_i2v`, `_t2v`), full branch: 'Int (Full)' 20,
    KSamplerSelect `res_multistep`, BasicGuider.
  - Wan 2.2 14B I2V: no LoRA, 20 steps split 10 / 10, cfg 3.5 (euler / simple, shift 5). From
    the non-LoRA branch of ComfyUI's `video_wan2_2_14B_i2v.json` ('Int (Steps)' 20,
    'Int (split_step)' 10, 'Float (CFG)' 3.5).
  - LTX-2.3 ingredients: `ltx-2.3-22b-dev-fp8`, IC-LoRA at 1.4, 30 steps, cfg 4.0. From the
    model card, as ComfyUI's ingredients template quotes it in 'Note: IC-LoRA'. ComfyUI's
    LTX-2.3 templates have no undistilled path.
- **Resolution** (`targets.resolve_models`, `h3jobs.resolve_models`): exact file (also in a
  subfolder), else the best installed file of the family (name before header; same
  precision, then shortest name; `keep` words such as the step count, noise stage or
  distilled vs dev must match; never a parent family the header can't narrow). The LoRA in
  a pass's own `lora` slot counts as the accelerator whatever it is called. Runs in
  `queue_shots` and `h3render` before `check_models`; the sidecar records `resolved`.
- **Readiness** (`h3edit.readiness`): from `/object_info` (loader choices, node classes of
  the pruned workflow + target.json `nodes`). The route caches it for 30 s.
- **Episode target**: `overrides.json` `{"episode": {"id", "target"}}`; `h3jobs.target_choice`
  is the precedence; the retarget path compiles such shots at queue time.
- **Live check (2026-09-19, read-only):** `h3.py targets` on the dev machine: every target
  ready except `ltx2`, degraded (the duration head isn't installed; its link comes from
  ComfyUI-Manager's model list). `h3render --dry-run --check-nodes` on a scratch copy of
  ep05 sh020: exact files; with a LoRA named `…ref2v_turbo_4step_v0.1_comfyui_bf16` (not
  installed) the installed lightx2v 4-step Ref2V LoRA stood in; with a 2-step one (none of
  that family) the base preset ran: no LoRA loader in the graph, `res_multistep`; both
  graphs passed `/object_info`.
- **Left:** the editor's readiness view and episode-target picker (the UI agent's side);
  `h3refs` / kreagen don't resolve krea2's files at queue time yet (readiness covers them).

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
