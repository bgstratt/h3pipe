# h3pipe — plan: a shot/take editor, then model-agnostic targets

Status: planning, not started. Written 2026-09-18, revised the same day after review.
This is the working plan for the next round of development. `CLAUDE.md` points here.

## Goals

1. **Script + bible in, episode out, with a tight tweak loop.** You write `epNN.md` and
   `series.json`; everything else can be driven from a UI inside ComfyUI: generate
   refs, render shots, tweak a shot's prompt/seed/model/LoRA/steps, redo, compare
   takes, pick the take each shot uses, assemble.
2. **Nothing is lost.** Every take is kept with the exact settings that produced it.
   Picking an earlier take never deletes a later one. UI tweaks survive a rebuild.
3. **Model-agnostic core.** The script and bible describe *the film*; nothing in them
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
| **Refs are driven by the bible and have takes too** | Refs belong to the series, not an episode, and you want to lock designs before a script uses them. Candidates are takes; the picked one is copied to the path the bible names. |
| ComfyUI-Sonder-Editor is **inspiration only** | Borrow ideas (model templates, recipes, frozen job provenance, take gallery/compare). No code: it is GPL-3. |
| Prompt formats are **code**, everything else about a target is **data** | Writing an H3 prompt is real logic; a declarative DSL would only move that complexity somewhere worse. |

## Project layout

Today each episode folder holds its own `series.json` and `refs/`, so several episodes
duplicate the bible and refs drift apart. Target layout:

```
Shows/gold_path/
  series.json            the bible
  refs/                  picked refs, at the paths the bible names
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

The bible is looked up in the episode folder first, then its parent, so today's
per-episode layout keeps working. Bible paths are relative to the folder holding
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
 series.json ─── refs ─▶ refs/_takes/<ref>/… ─ pick ─▶ refs/<path the bible names>
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
        "base_hash": "sha1 of the shot entry this was written against",
        "prompt": "…full compiled text, or null…",
        "seed": null,
        "final": {"model": null, "loras": null, "steps": null},
        "proxy": {"model": null, "loras": null, "steps": null},
        "note": ""
      }
    }
  }
}
```

- Overrides are keyed by target because compiled text only makes sense for one target.
- A null field means "use the built value".
- When a rebuild changes the shot so that `base_hash` no longer matches, the override is
  **stale**. It still applies (you asked for it), but the inspector flags it and shows a
  diff between what the build now produces and your text. You then keep it, re-base it
  or drop it.
- **Promote to script** (a later phase) writes story-level edits back into `epNN.md`
  and drops the matching override.

Precedence for any render parameter: target default → preset (final/proxy) → series
→ sequence → shot (script) → profile → `overrides.json` → the redo dialog.

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

- **The Refs tab lists every subject and location in the bible**, not just what one
  episode's `refs_todo.json` needs. `refs_todo` becomes an episode filter and badge
  ("blocks 12 shots in ep05").
- **A ref's candidates are takes**, stored in `refs/_takes/<ref>/<ref>_tNN.png` with
  sidecars (the same shape as video takes: prompt, seed, model, LoRAs). Picking a take
  copies it to the path the bible names, and every video take records that file's sha1,
  so re-picking a ref marks dependent takes stale (`ref`).
- **Character sheets:** each of the four views has its own takes. A pick per view, then
  `mksheet` stitches them.
- **Import:** any image can be imported as a ref take (hand-drawn, from elsewhere).
- **Prompt tweaks:** ref prompt and seed tweaks go in `refs/_overrides.json`, the same
  shape as shot overrides, keyed by ref.
- **Two fixes in `kreagen` along the way:**
  - It gets the character id from the bible entry, not by guessing from the filename.
    Today `basename.split("_sheet")[0]` silently skips any sheet not named
    `<id>_sheet…`.
  - There is one source for sheet prompt wording. Today `h3build.need()` writes a
    4-panel prompt that `kreagen` ignores.

## 5. The editor UI

| Surface | Contents |
|---|---|
| Sidebar **Shots** tab | Episode picker; bin grouped by sequence → shot, expanding to takes; badges (stale, override, queued/rendering, placeholder, no take). |
| Sidebar **Inspector** (the selected shot) | Built values (read-only); the override editor: prompt text with a diff against the built text, seed, model/LoRA/steps pickers (from ComfyUI's model lists); Render / Redo buttons; pass toggle. |
| Sidebar **Refs** tab | Bible subjects and locations; picked ref and candidates; generate / redo / import / pick. |
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

**Phase 1 — takes, overrides, cut (CLI)**
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

**Phase 4 — evaluate**
Use it on a real episode, then decide and write down here:
- Is the sidebar + bottom panel too cramped? If so, arrange the same components as a
  fullscreen overlay.
- Is the floating viewer enough for judging takes?
- Is reordering the cut wanted now? Trims?
- Which override fields actually get used, and which are missing?
- What should the Refs tab look like, now that the take model has been used?

**Phase 5 — references**
- Series/episode layout: look up the bible in the parent folder; paths resolve relative
  to the bible.
- Ref takes, picks, per-view sheet picks, import, `refs/_overrides.json`; the two
  `kreagen` fixes above; ref routes and the Refs tab.
- Exit: generate a new character's sheet from the bible alone (no episode uses it yet);
  re-picking a ref marks dependent video takes `ref`-stale.

**Phase 6 — extract the story IR, no behaviour change**
- New package (`h3pipe/` or `core/`): `story.py` (parse → IR), `ir.py` (dataclasses +
  JSON), `bible.py`.
- `h3build.py` becomes: parse → IR → the current H3 compile code (still in place).
- Write `shotlist/shots.json` alongside the existing outputs; `shot_hash` becomes the
  IR hash.
- Exit: goldens byte-identical; `shots.json` contains no H3 vocabulary.

**Phase 7 — targets, H3 only**
- `targets/` loader + `Target` protocol; move the H3 code per the table in *Targets*.
- `jobs` / `graph_for` driven by `binding`; `kreagen` driven by `targets/image/krea2`.
- Render profiles (see *Targets*).
- Exit: goldens byte-identical; render and refs still work on a real episode, from the
  CLI and the UI.

**Phase 8 — a second video target**
- LTX 2.3 or Wan 2.2: its template, recipe, binding and a prose `prompt.py`.
- Fix every place the H3 assumptions leak out — that's the point of this phase.
  Overrides and takes are keyed by target already.
- Exit: the same episode builds and renders a proxy pass on both targets; a single shot
  can be retargeted from the inspector.

**Phase 9 — later**
- Script pane: `epNN.md` in a text editor with live `--check` errors beside the lines;
  save → rebuild.
- Promote to script (needs the parser to record each shot's line span).
- Drag-reorder and trims in the timeline; play-through of the cut; master dialogue
  waveform under the timeline.
- Bible editing in the UI (design sentences → regenerate refs).

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
      "plate": "street",                  // location/angle key into the bible
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
    prompt.py          compile(shot_ir, bible, ctx) -> CompiledShot
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
    recipe: Recipe            # pack(shot, bible) -> slots, required refs
    binding: WorkflowBinding  # graph + widget map
    presets: dict[str, Preset]
    def compile(self, shot: ShotIR, bible: Bible, preset: Preset) -> CompiledShot: ...
    def required_refs(self, shot: ShotIR, bible: Bible) -> list[RefRequest]: ...
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

Target selection: `series.json` default → profile → sequence → shot `target:` → UI
override. Output file per target: `shotlist/shotlist.<target>.json` (+ `.proxy`). Keep
writing `shotlist.json` / `shotlist_proxy.json` for the default target until the node
and the `jobs` module are updated.

Things in `h3build.py` that are H3 leaking into the core today, and where they go:

| Now | Goes to |
|---|---|
| `snap_up`, grid constants, `%32` check | H3 template |
| slot ordering, `panels`/`panel_view`, `background` = Picture 4 | H3 recipe |
| max 3 voice refs in clone mode | H3 recipe (validation) |
| `continuous` chaining costs 22 frames warning | H3 template (warning hook) |
| `RETENTIONS`, `RETENTION_DEFAULT` | H3 prompt/recipe mapping from `preserve` |
| `build_prompt` | `targets/video/minimax_h3_ref2va/prompt.py` |
| `FINAL_*`, `PROXY_*`, the LoRA-steps mismatch warning | H3 presets |
| `need(...)` reference prompts (4-panel sheet 4096x1024, prop 1024x1024) | split: the *shape* of the ref (4-panel sheet) is the video recipe's `RefRequest`; the *wording* belongs to the image target |
| `kreagen.VIEWS`, `VIEW_TMPL`, `SAMPLER`, workflow constants | `targets/image/krea2/` |

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

## Defaults to revisit in Phase 4

These were chosen without strong evidence; overrule them once the UI is in use.
- The cut may mix passes (a proxy as placeholder for a missing final).
- Reordering is allowed anywhere in the cut, not only within a sequence.
- The redo dialog's "save as override" checkbox is on by default.
- The script pane (Phase 9) is preferred over a field-by-field inspector that writes the
  script.
