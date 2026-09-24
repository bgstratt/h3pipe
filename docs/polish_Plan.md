# h3pipe — polish plan: operating a whole episode

`docs/PLAN.md` is the record of Phases 0–12: the editor, takes, overrides, the cut, targets,
and a show's own targets. The functionality is there — a person can author, build, make refs,
render, pick and assemble. What is thin is the **layer above one shot**: a proxy pass over a
240-shot sitcom is 60–80 minutes of rendering, and the editor currently says `37/240 · 12q`
about it. This is the pass that makes a long session legible, and gets a new user started
without leaving the tool.

Read `docs/PLAN.md` first for the architecture; the rules in `CLAUDE.md` still apply (stdlib
only outside `comfy_nodes/`, goldens byte-identical unless the change is intended and the
reason is stated).

**Status (2026-09-23).** P1, P2, P5, P8, P9a, P9's items 1, 3 and 4, and P10 (all three parts)
are built, each with an *As built* note under it saying what differed from the sketch; P3 is
dropped and P4 vacant. Every exit check that needs a real render session is still open — none of
this has been driven through the editor against a whole episode. Left: **P6** (voice cloning, an
investigation), **P7** (full-size verification, render sessions) and **P9**'s items 2, 5, 6
and 7.

## Decisions

| Decision | Why |
|---|---|
| **Progress is computed from take sidecars, not a new record** | every take already carries `queued`, `finished` and (2026-09-22) `save_ms`. A pass's throughput is derivable, so nothing new has to be written or kept in sync. |
| **A pass's rate is wall-clock throughput, not per-take duration** | with 240 prompts in ComfyUI's queue, a take's `queued`→`finished` includes waiting for the ones before it. `(latest finished − earliest queued) / finished count` is what a person actually experiences. |
| **"Re-render stale" uses the take's own seed** | the point is to see what the edit did. A new seed changes two things at once and makes the comparison worthless. |
| **No queue control of our own** (P3 dropped) | ComfyUI's queue already cancels and clears, and `h3takes.sweep_queued` marks any take whose job left the queue as failed (120 s grace), so nothing is left inconsistent. Building a second queue UI would duplicate ComfyUI's and could disagree with it. |
| **The starter template is a real file, not generated prose** | `examples/starter/` is what New episode copies *and* what the docs quote, so the two can't drift; a test builds it. |

## Phases

Each phase ends with its exit check passing. Don't start the next until it does.

**P1 — a pass you can watch: elapsed, rate, remaining**

- `web/src/lib/progress.ts` (new, pure): from the pass's shots and takes →
  `{done, total, queued, failed, elapsed_s, per_shot_s, remaining_s, since}`.
  - `done` = shots with a usable take, `total` = non-orphan shots, `queued` = takes still
    queued, `failed` = shots whose newest take failed.
  - `elapsed_s` = latest `finished` − earliest `queued` among takes of this pass that are part
    of the current run (a run = takes whose `queued` is within a gap threshold of each other,
    so yesterday's takes don't count).
  - `per_shot_s` = `elapsed_s / done_in_run`; `remaining_s` = `per_shot_s × (total − done)`.
    Both null until two takes have finished (one sample is not a rate).
- The Shots tab footer grows from `37/240 · 12q` to
  `37/240 · 12q · 24m elapsed · ~20s/shot · ~68m left`, with the arithmetic in the tooltip
  and the estimate marked `~`. Nothing queued and nothing running: show the last run's rate
  and no ETA. A failed take shows as `· 2 failed` linking to the first one.
- Where it goes: the same footer as today (`ShotsTab.tsx`), and the timeline's header, which
  is where a person watches a long pass.
- **Exit check:** mid-pass on FRTEST the elapsed and rate agree with a stopwatch within 10%,
  and the ETA lands within 15% over a 20-shot stretch. A fresh episode with no takes shows
  neither. Unit tests on `progress.ts` for: no takes, one take, a run after an idle gap,
  mixed passes, a failed take, and takes from before the current run.

*As built (2026-09-22).* `web/src/lib/progress.ts` (pure, 16 tests in
`web/test/progress.test.ts`): `passProgress` → `{done, total, queued, failed, todo, elapsed_s,
per_shot_s, remaining_s, run_takes, running}`, with `progressLine` / `progressTitle` for the
text and the tooltip. Shown in the Shots tab beside the filter box (`ShotsTab.tsx`) and in the
timeline header (`Timeline.tsx`), reading `37/240 · 12q · 60s · ~20s/shot · ~40s left`. As
designed: the rate is wall-clock per finished take, a gap of `RUN_GAP_S` starts a new run, and
no rate is claimed until two takes of a run have finished. **The exit check is still yours to
run** — the stopwatch comparison mid-pass needs a real pass.

**P2 — re-render stale (n)**

- A shot is *stale-fixable* when its newest usable take's `stale` says `script`, `preset`,
  `ref` or `target` — the reasons a re-render settles. `unknown` (a take from before sidecars)
  is not offered, because re-rendering it proves nothing.
- A button beside **Render missing (n)**: **Re-render stale (n)**, which queues those shots
  with `redo: true` and `seed_mode: "same"` (the decision above), and names them in its
  tooltip. Disabled with "nothing is stale" when the count is zero.
- The existing `realStale` helper in `web/src/lib/format.ts` already decides the badge; this
  reuses it so the button and the badge can never disagree.
- **Exit check:** change one line of dialogue in FRTEST's script, rebuild, and the button
  offers exactly the shots whose prompts changed (compare against `git diff` of the
  shotlists). Click it; when the takes finish, no shot is badged `stale: script` and each new
  take's seed equals its parent's. A rebuild with no script change offers nothing.

*As built (2026-09-22).* `staleShots` / `staleReasons` in `web/src/lib/progress.ts` and
`renderStale()` in `web/src/actions.ts`, behind **Re-render stale (n)** beside **Render
missing (n)** in the Shots tab's build bar. It queues with `redo: true` and
`seed_mode: "same"`, so each shot renders at its built seed and only your edit shows; the
tooltip names the reasons ("12 script, 3 ref"). A shot whose newest take is only `unknown`, or
that already has a take queued, is left out. The seed mode survives the missing-refs detour —
there is a test for exactly that, because losing it there would silently roll new seeds.
**The exit check is still yours to run** (change a line in FRTEST, rebuild, confirm the button
offers exactly the changed shots).

**P3 — dropped: cancel the queue.** ComfyUI's own queue does it, and the sweep keeps our
takes honest afterwards (see Decisions). Recorded so nobody builds it twice.

**P4 — vacant.** Voice cloning was P4 in the first sketch; it moved to P6, because it is an
investigation rather than a polish item.

**P5 — New episode, from a template**

The only part of the flow the editor can't get you into: today step one is writing
`series.json` and `epNN.md` by hand, outside the tool. This makes a working episode in one
click, which is also the fastest way for a new user to see the whole loop.

- `examples/starter/` (new): a deliberately small, correct pair — one character, one prop, one
  location, three shots, one of them with dialogue. It must build with no warnings and it must
  be honest about references (it will list them as missing, which is the next thing to do).
  `examples/` already has `script_example.md` + `series_example.json`, which are a fuller pair
  used for illustration; the starter is smaller on purpose (a first render, not a tour) and
  whether it replaces them or sits beside them is settled when it is written.
- **`POST /h3pipe/episode/new`** `{root, name, series_id?, title?}`:
  - `root` must be one of the configured roots (403 otherwise, as every other path check);
    `name` becomes the folder and the script's stem (`ep01` → `ep01.md`).
  - 409 when the folder exists; 400 for a name that isn't a safe folder name.
  - Copies the starter, rewrites the series id/title and the episode id, returns the episode
    as `GET /h3pipe/episodes` lists it, and emits `h3pipe.episode`.
- Editor: **New episode…** in the Project folders window (where roots are chosen), which then
  selects it and offers **Build**. After building, the refs tab is where the user goes next —
  say so in the empty state rather than leaving them on an empty bin.
- CLI: `h3.py new <root>/<name>` does the same, so it is testable without the UI.
- **Exit check:** from an empty root, New episode… → Build succeeds → `refs_todo` lists the
  starter's pictures → the bin shows three shots, each blocked on refs with a clear reason.
  A test asserts the shipped starter passes `h3build --check` with zero warnings, and
  `docs/AUTHORING.md`'s opening example is the starter file itself (a docs test compares them).

*As built (2026-09-23).* `h3source.new_episode(parent, name, series_id, title)`, the route
`POST /h3pipe/episode/new`, `python h3.py new <folder>\<name>`, and **New episode…** in the
Project folders window (`web/src/lib/newEpisode.ts` for the rules the dialog applies before
asking). Differences from the sketch above, all deliberate:

- **The body takes `parent`, not `root`.** A next episode goes beside its neighbours, which
  is usually a show folder *inside* a root, not the root itself. It is checked the same way
  (absolute, inside a configured root, else 403).
- **The template is the newest episode there, not always the starter.** Copying the
  neighbour's series config is the thing that makes this useful for episode two of a series
  you are writing; the starter is for episode one. The script that comes with a copied
  config is a one-shot skeleton naming that config's own first character and location, so it
  builds and is obviously a placeholder. `audio.track` is the only line rewritten (it
  follows the episode name).
- **Every episode gets its own series config**, even under a show that has one, because that
  is the layout the shared-refs decision needs: `../refs/...` shares the pictures, a config
  per episode keeps the cast lists separate.
- **`title` titles the episode, not the series.** `series_id` and the series' title are only
  written when this is the first episode.
- **The starter sits beside the existing examples** (`examples/starter/`), which stay as the
  fuller illustration, and `tools/sync_starter_doc.py` copies it into `docs/AUTHORING.md`
  (`--check` in `tests/test_new_episode.py`). Editing either means running that, then
  `python tools/make_prompts.py`.
- **One bug fixed on the way:** `find_episodes` required a series config *in* the folder, so
  an episode relying on the documented parent-folder fallback was invisible in the editor
  while the CLI rendered it happily. It now accepts the parent's config when the script is
  named after the folder.
- The empty shot bin of an unbuilt episode now says what Build does and that Refs comes
  next, rather than being blank.
- Tests: `tests/test_new_episode.py` (31), `NewEpisodeTest` in `tests/test_api.py` (6),
  `web/test/newEpisode.test.ts` (10). **The exit check above is still yours to run** —
  nothing here has been through the editor against a real show.

**P8 — supplying references you already have**

*The premise this phase started with was wrong, and the correction is the phase.* P8 was
written as "drag-and-drop and Upload… onto ref, view and keyframe slots (`docs/PLAN.md`,
Phase 8.6 'Left'). The multipart route exists; this is the UI half." It isn't: 8.6 lists
that work in its **done** list, and it is there — a drop and an **Upload…** button on a
prop/plate/voice row, on each of a character's four view columns, on a keyframe row, on the
Inspector's keyframe tiles, and on the recording. What is thin is *volume* and *characters*.

First, what already works, because it bounds this phase (measured 2026-09-23): **a picture
copied into the folder `series.json` names is fully supplied.** A starter episode with three
hand-placed PNGs builds `references 3/3 on disk`, `refs_todo` marks them `exists: true`,
renders read the live file, and `stale: ref` keeps working — `stale_reasons` compares the
path and sha1 a take's own sidecar recorded against the file as it is now
(`h3jobs.py:1054`), and never consults `_picks.json`. Explorer is a legitimate way to supply
a reference and `docs/EDITOR.md` already says hand-made refs are the expected case. So
nothing here is blocked; what follows is consistency and volume.

Four gaps, found by reading the code (2026-09-23):

| Gap | Where it bites |
|---|---|
| **The editor refuses for a character what the folder accepts.** A drop or Upload lands fine on a prop, a plate, a voice or a keyframe, but a character row says "open it and drop onto a view" — `import_take` requires a real view (`h3refs.py:1587`). | A finished 4-panel sheet can only arrive through Explorer. That works, but it arrives as one bare file: no second candidate to A-B against, no revert, no discard-to-`_trash`, no sidecar recording where it came from — and on a ComfyUI that isn't this machine, no route at all. |
| **One file per drop** (`e.dataTransfer.files?.[0]` in `Upload.tsx` and `Track.tsx`). | Dropping four view images, or ten plates, takes the first one and drops the rest on the floor silently. |
| **Nothing matches files to slots by name.** | 28 subjects and 40 locations (Porchlights) means 68 drops onto 68 rows, each of which has to be found first. |
| **A clip's audio (Phase 9d) takes a file picker but not a drop**, though the episode's recording takes both. | Inconsistent in a way that reads as a bug. |

Minor, same area: no Ctrl+V paste into a slot, and a dropped **folder** is ignored (only
`dataTransfer.files`, never `webkitGetAsEntry`).

**The decisions**

| Decision | Why |
|---|---|
| **A sheet supplied *through the editor* is a take, on a reserved pseudo-view `sheet`** | not because a file on disk is wrong — it works, see above — but because a take is what buys the three things Explorer can't: a second candidate to compare against, `_trash/` instead of deletion, and a sidecar recording the original name. `take_base(ref, "sheet")` already gives `subject__walker_sheet_t01.png` with no collision. A hand-placed sheet stays exactly as valid: it shows as the live file with no takes behind it, which is what it is. |
| **Supplying a sheet does not disable the four views** | they are different routes to the same file, and a series will use both (a supplied sheet for the lead, generated views for a day player). Whichever was picked last is what is on disk; `_picks.json` records which, and the row says so. A view pick after a supplied sheet re-stitches and replaces it — with a confirm, not a refusal. |
| **Matching by name is shown before it happens** | routing 12 files by guessing their names is only safe if the guess is on screen first. One table: file → slot, with the unmatched listed and nothing uploaded until Supply is pressed. |
| **The route stays one file per request** | `POST /h3pipe/refs/import` is already right; many files are N sequential requests from the client with one progress line. No new contract, and a partial failure names the file that failed. |
| **A matcher is a pure function with its own tests** | it is the only part of this that can be wrong in a way a person won't notice until a render comes back with the wrong character in it. |

**The work, in order**

**8a — a sheet is suppliable (server).** `SHEET_VIEW = "sheet"`, accepted by `check_view`
for a ref with views wherever a view is *read or written* (import, pick, list, discard) and
refused wherever one is *generated* (`refs generate`, `generate-missing`): a sheet is not a
thing the pipeline draws, it is four views stitched.
- `pick_take(s, ref, "sheet", n)` copies the take to `ref.file` with no stitching and
  records `picks["refs"][<id>]["sheet"] = {take, sha1, picked}`, setting the block's `sha1`
  the way `stitch_sheet` does, so `stale: ref` keeps working unchanged.
- `GET /h3pipe/refs` gains, per character, `sheet: {exists, live: "sheet" | "stitched" | null,
  takes: [...]}` beside its `views`, so the tab can show what is in force.
- CLI, and the way this gets tested without the editor:
  `python h3.py supply <episode> <file-or-folder> [--ref id[:view]] [--dry-run]` — one file
  into a named slot, or a folder matched by name (8b). It prints the match table and what it
  wrote. This is also the fastest path for a show that already has 68 pictures on disk.

**8b — the matcher (pure, tested).** `web/src/lib/supply.ts` and the same rules in
`h3refs.py` for the CLI: a file name → a slot, by slug.
- `walker_sheet_4panel.png`, `walker_sheet.png`, `walker.png` → `subject:walker` sheet.
- `walker_02_side.png`, `walker_side.png` → that view of `subject:walker`.
- `highway_dawn.png` → `location:highway_dawn` plate; `_bg/highway_dawn.png` likewise.
- `walker.wav` → `subject:walker`'s voice, when the ref is audio.
- Case, spaces, dashes and underscores are equivalent; the extension decides image vs audio;
  a name matching two slots is **unmatched**, never guessed.
- It reports `{matched: [{file, ref, view, why}], unmatched: [{file, why}]}`. `why` is a
  sentence a person can argue with, because they will.

**8c — the UI.** All of it reuses `DropSlot`; none of it invents a second upload path.
- The character row stops refusing: it becomes the **sheet** slot (drop or **Upload sheet…**),
  with the row's badge saying `supplied sheet t02` or `stitched from four views`.
- Every `DropSlot` takes **all** the dropped files: one file behaves as today; two or more go
  through the match table, so dropping four views on a character does the right thing.
- A drop anywhere on the **Refs tab background** is the bulk path: the match table, a
  **Supply n files** button, a per-file progress line, and a summary toast. Folders are
  accepted (`webkitGetAsEntry`, one level deep, images and audio only).
- Ctrl+V into a focused slot pastes an image from the clipboard (one file, same path).
- The clip-audio dialog gets a `DropSlot` around its file row, so Phase 9d matches the
  recording.

**8d — what it costs.** `MAX_UPLOAD` is 64 MB a file and stays there; the bulk path uploads
sequentially so a 68-file supply cannot flood ComfyUI's event loop, and it reports
`12/68 · walker_sheet_4panel.png` while it goes.

**Exit checks**

1. **The sheet path, end to end.** Take any character with no picks, drop a 4-panel PNG on
   its row: it becomes take 1, goes live, the row says `supplied sheet t01`, and a render of a
   shot with that character uses it. `python h3.py takes` and the Refs tab agree about it.
2. **Precedence is honest.** With a supplied sheet live, pick all four views: the confirm says
   the sheet will be replaced, and after it the row says `stitched from four views`. Discard
   the sheet take: the live file is untouched (it is the stitch now) and nothing is stale.
3. **Bulk supply on a real show.** Drop a folder of Porchlights refs on the Refs tab. Every
   file that should match does, nothing is routed to the wrong slot, and the unmatched ones
   say why. `--dry-run` on the CLI prints the same table.
4. **Multi-file into one slot.** Drop four view files on a character: four views, four picks,
   one stitch, no manual step.
5. **The Explorer route still works, untouched.** A character whose sheet was copied into the
   folder by hand, with no takes and no `sheet` block in `_picks.json`, still shows as live,
   still renders, and is not badged missing or stale. This is the regression this phase is most
   likely to cause, because it changes that row from "refuses drops" to "is a slot".
6. **Nothing else regressed.** The existing single-file drops and Upload… buttons behave exactly
   as before, `python -m pytest` is green, and the goldens are untouched (this phase writes no
   shotlist).

Unit tests: the matcher (names that match, names that shouldn't, ambiguity, audio vs image,
case and separators), `SHEET_VIEW` through import/pick/discard/list, refusal on generate, and
the sheet's effect on `stale: ref`.

*As built (2026-09-23).* All four parts, in the order above.

- **8a.** `h3refs.SHEET_VIEW` (`"sheet"`), accepted by `check_view(..., allow_sheet=True)` from
  import, pick, discard and clear (and by those four routes through `_view(..., sheet=True)`),
  refused everywhere a view is generated or worded. Picking a sheet copies it to the live file
  with no stitch and records the pick beside the views in `_picks.json`. Two guards worth
  knowing: a clear or discard of a supplied sheet removes the live file **only if that pick
  still wrote it**, so a stitch that replaced it survives; and `live_from` judges from the
  file's own sha1, which is what makes a hand-placed sheet report honestly as "nobody's".
  *Deviation:* the listing carries a character's sheet takes as the ref's own `takes` /
  `picked` (previously always empty for a character) plus `live_from`, rather than the
  `sheet: {...}` sub-object the sketch proposed — the editor already had a place for a ref's
  own takes, so the row needed no new shape.
- **8b.** `h3refs.match_files`, **one implementation**, reached by the editor through
  `POST /h3pipe/refs/match` (names only, 500 at a time, writes nothing) and by the CLI
  directly. *Deviation:* the sketch had the rules in `web/src/lib/supply.ts` *and* in Python;
  that would have been two copies of the one thing that must not drift, so the server decides
  and the editor displays. The mock keeps a deliberately simple stand-in for the dev page,
  labelled as such. Validated against Porchlights ep01: 110 of its 110 own reference file
  names match their own slot, nothing ambiguous.
- **8c.** Every `DropSlot` takes all the dropped files and follows a dropped folder
  (`web/src/lib/dropped.ts`, entries read before the drop event ends, two levels, 500 files);
  a character's row is the sheet slot with **Upload sheet…**; the Refs tab is the bulk drop
  zone with **Supply files…**; the Supply window shows the table and uploads one file at a
  time; Ctrl+V pastes onto a slot; the clip-audio picker takes a drop, so Phase 9d matches the
  recording.
- **8d.** `MAX_UPLOAD` unchanged at 64 MB; the bulk path is sequential by construction (a test
  asserts only one upload is ever in flight).
- **`python h3.py supply <ep> <file-or-folder>... [--ref id[:view]] [--no-pick] [--dry-run]`**
  was added beyond the sketch: it makes the matcher usable today on a show whose pictures are
  already on disk, and testable without the UI.
- Tests: `tests/test_supply.py` (42), `RefMatchTest` in `tests/test_api.py` (7),
  `web/test/dropped.test.ts` (9), `web/test/supply.test.ts` (12). Full suites green (875
  Python, 466 web). **The exit checks are still yours to run** — nothing here has been through
  the editor against a real show.

**P10 — a pass's issues: the notepad you fill while watching a proxy**

The loop this serves: render a cheap proxy to see structure, watch it, jot what is wrong with
each bad shot, hand the lot to an assistant, fix the script or the series config, render again.
The notes are **spent** at that point. They describe the shot *as it was rendered*, not the shot
as it will be.

Two things already in the tree are near misses, and neither fits: a take's `note` (set at render
time, in the sidecar, records intent) and a cut entry's `note` (per shot per pass, but it lives
in `cut.json`, which is durable, and mixes "trim this tighter" with "the truck is on the wrong
side"). A pass's issue list has a different lifetime from everything around it, so it gets its
own file.

**The decisions**

| Decision | Why |
|---|---|
| **Snapshot the script lines and the compiled prompt at note time** | the note is about what produced *that* take. Deriving the current text at read time — the right call for anything durable, and what this plan first proposed — would destroy the thing the note is about the moment you start editing. |
| **Going stale is the expected end, not a failure** | a shot that is rebuilt or re-rendered shows its issues as `addressed?`; they are never deleted behind your back. **Clear addressed** empties them when you say so. |
| **Its own file, per episode, both passes in it** | `cut.json` is the cut, sidecars are takes. An `_issues.json` that is usually empty and gets emptied on purpose belongs nowhere else. |
| **Markdown export by default, `--json` beside it** | the script excerpt is multi-line: in JSON it is `\n`-escaped soup you can't eyeball before pasting, and models echo the escaping back. Markdown also carries its own instruction line, so the paste needs no preamble. JSON stays for the day this gets automated. |
| **The bundle carries the refs the shot used** | "her face is wrong" is a sheet problem about half the time. An assistant handed only the prompt will confidently rewrite prose that was never the cause. |

**The work, in order**

**10a — the file and the module.** `h3issues.py` (stdlib, shared by the CLI and the routes):
`<ep>/_issues.json` = `{"version": 1, "items": [...]}`, each item
`{id, shot, pass, take, note, when, shot_hash, script, prompt, refs: [{slot, path}], take_file}`.
- `add(ep, pass, shot, note, take=None)` snapshots: the script's lines for that shot (the spans
  `h3source.script_spans` already gives), `effective.prompt` from `h3edit.shot_detail`, the
  refs `refs_used` lists, and the take's mp4 path. `take` defaults to the cut's take.
- `addressed(item, ep)` is true when the shot's `shot_hash` has moved or a newer take exists —
  computed on read, never written.
- `resolve(ep, ids)` / `clear(ep, pass=None, addressed_only=False)`; `list_issues(ep, pass=None)`.
- Atomic writes through `h3source.atomic_write`, and the file is never created until the first
  note (an episode with no issues has no file).

**10b — the CLI.**
```
python h3.py issues <episode> [--proxy] --add sh0140 "Kell enters from the wrong side"
python h3.py issues <episode> [--proxy]                 # the list, addressed ones marked
python h3.py issues <episode> [--proxy] --export [--json] [-o FILE]
python h3.py issues <episode> --clear [--addressed]
```
The export's markdown: a one-line instruction, then per issue a heading (`## sh0140 · take 2`),
the note, the script excerpt in a fenced block, the compiled prompt, and the reference files by
slot. One document, pasteable.

**10c — the editor.** Writing an issue has to be fast enough to do while watching:
- the timeline's clip context menu gets **Add issue…**, prefilled with the clip's shot and take,
  and a key (`i`) on the focused clip opens the same box.
- the shot bin's context menu gets it too, and a shot with issues shows a small badge with the
  count.
- an **Issues** window: the list for this pass, each with its note and shot, `addressed?` faded,
  buttons for **Copy export** (the markdown, to the clipboard), **Resolve** and
  **Clear addressed**. Nothing else — it is a notepad, not a tracker.
- routes: `GET /h3pipe/issues?ep=&pass=`, `POST /h3pipe/issues` (add), `DELETE /h3pipe/issues`
  (resolve / clear), `GET /h3pipe/issues/export?ep=&pass=&format=md|json`.

**Exit checks**

1. **The loop, once, on a real episode.** Render a proxy pass, add issues to three bad shots
   from the timeline while it plays, `--export`, paste it into an assistant, apply its script
   edits, rebuild, re-render those shots: the three issues now read `addressed?` and
   **Clear addressed** empties the file.
2. **The snapshot holds.** After editing the script for a noted shot, its issue still shows the
   wording that produced the take, not the new text.
3. **Nothing lingers.** An episode that has never had an issue has no `_issues.json`; after
   `--clear` the file is gone or empty, and `cut.json`, the sidecars and the shotlists are
   untouched.
4. Unit tests: add/list/resolve/clear, the `addressed?` rule (hash moved, newer take, both,
   neither), the markdown and JSON exports, a note on a shot that is not in the pass (refused),
   and an `_issues.json` that is corrupt or from a future version (reported, not crashed).

*As built (2026-09-23), 10a and 10b.* `h3issues.py` and `python h3.py issues <ep> [--proxy]`
with `--add <shot> <note>`, `--add-from <file>`, `--export [--json] [-o FILE]`, `--resolve <id>`
and `--clear [--addressed]`. 40 tests in `tests/test_issues.py`. Notes from the build:

- **`--add-from` was added beyond the plan**: one `sh0140: the truck is on the wrong side` per
  line. It closes the gap that made a hand-editable markdown store tempting — you can type a
  batch in any editor — without making the snapshot itself hand-editable.
- **`addressed` is per shot, not per episode.** A rebuild on its own addresses nothing; the
  shot's own `story_hash` has to move, or a newer usable take has to exist. Two tests pin that,
  because "rebuild marks everything done" would make the notepad useless.
- **A note needs no take.** "This shot shouldn't exist" is worth recording before anything has
  rendered, so `take` may be null and the snapshot still carries the script and the prompt.
- **A corrupt or future-version `_issues.json` is an error, not a fresh start.** Silently
  losing a list of notes is the one failure this must not have.
- Verified by hand on a built episode: noted two shots, edited the script for one of them,
  rebuilt — that one read `addressed` and the other did not; the addressed one's snapshot still
  showed the *old* wording (exit check 2), and `--clear --addressed` left the other alone.
*As built (2026-09-23), 10c.* Routes `GET/POST/DELETE /h3pipe/issues` and
`GET /h3pipe/issues/export`, the note box, the Issues window, a flag button in the h3 Shots
toolbar with the open count, and **Add issue…** in every clip and shot menu. 10 route tests and
16 in `web/test/issues.test.ts`.

- **The key is `n`, not `i`.** `i` and `o` are trim-in and trim-out at the playhead
  (`cutActions.cutKey`) — the binding every NLE shares and this editor documents. Taking `i` for
  "issue" would have broken a reflex to save a letter; `n` is for note.
- **A box, not an inline field.** Asked for first as a light inline input, settled as the small
  dialog because right-clicking a clip has to reach the same thing, and one component serving
  both beats two that drift. Enter saves, Shift+Enter is a newline, Esc closes.
- **The note box keeps what you typed when the server refuses**, and won't send twice while a
  save is in flight (both tested) — a refusal that silently ate a sentence would be the worst
  failure this surface could have.
- The editor **degrades quietly** on a node pack without the routes: no notepad, no error noise
  (tested).
- `Issue.addressed` is never stored client-side either: the list asks for it on every read, so
  the fade can't go stale.

## Later — to define once P1, P2 and P5 are in

Written down so they don't get lost, deliberately unspecified until the phases above land.

- **P6 — voice cloning.** The one promise the pipeline doesn't keep: `LTXVReferenceAudio` is
  wired and tested but produces silence, so `clone` depends on samples supplied by hand
  (`docs/PLAN.md`, Phase 9c). An investigation at the node level, not a UI job.
- **P7 — full-size verification.** Built and never proved at final resolution: a real
  `dur: model` LTX render with the duration head, FL2VA with a recording (dub), and
  final-size looks on FL2VA / LTX / Wan. Each is a render session with notes, not code.
- **P9a — a shared live file, done 2026-09-23.** Was on the P9 list as "warn at pick time when
  the live file sits outside the episode"; built first because the layout P5 settled on makes it
  a daily foot-gun. `GET /h3pipe/refs` gains `shared_with` (the other episodes reading this
  ref's live file) and `live_owner` (whose pick wrote the copy there now, or null for one this
  tool didn't write), both from `h3refs.shared_context`, memoised on the configs and
  `_picks.json` it reads. Findings that shaped it:
  - **In a real show everything is shared** — 110 of ep01's 110 refs, with nine other episodes
    — so a "shared" badge would have been noise on every row. The badge shows only when the
    live file is *not* this episode's doing (`from ep03`, `not from here`), and the confirm is
    silent for the common case of re-picking your own.
  - **Generating is safe and the question that started this assumed it wasn't**: candidates are
    episode-local and `auto_pick` refuses to act when a live file exists. Only pick, upload or
    supply with `pick`, the fourth view's stitch, clear and discard-of-the-live-take reach a
    neighbour.
  - **The unrecoverable case is a hand-placed file** (`live_owner: null`): no candidate anywhere,
    so overwriting it loses it. That is the one the wording is blunt about.
  - Nothing refuses at the route: reporting is the contract, the editor confirms
    (`web/src/lib/shared.ts`, 12 tests), and `kreagen --clear` wants `--yes`.
  - Tests: `TestSharedLiveFiles` in `tests/test_supply.py` (12), `web/test/shared.test.ts` (12).
- **P9 — the small polish, in one sweep.** Audited against the code 2026-09-23 (the list had
  been copied out of Phase 8.6's "Left" and two of its items had since moved to 8.6's *done*
  list). What is actually open, all of it a `TODO(contract)` the editor already works around:
  1. ~~**Shot detail has no pre-override model/LoRAs/steps.**~~ **Done 2026-09-23:**
     `built_values` on `GET /h3pipe/shot` — `effective`'s shape with every override dropped,
     from the same planner, so a field can be compared directly. Absent when no override
     applies (it would repeat `effective`). The inspector shows "Built: steps 4 · model …"
     above the override form (`builtDiff` in `web/src/lib/overrideForm.ts`, 8 tests).
  2. **No prompt override per target.** A retargeted shot's per-pass prompt override is
     ignored, so the inspector shows `effective.prompt` read-only.
  3. ~~**`GET /h3pipe/shot` takes no `target`.**~~ **Done 2026-09-23:** it takes one, and
     answers for a one-off run on that target (the retarget the queue path already did, now
     for display). The frame grid is the target's, so the length is real: one shot measured
     448×256/124 frames on H3, 448×256/129 on LTX, 640×352/125 on Wan — none of which a
     preset could have told the dialog. The detail cache keys on the target, so the shot's
     own detail stays cached.
  4. ~~**A character view's overrides don't say which fields are the view's own**, and a view
     has no `built_prompt`.~~ **Done 2026-09-23:** each view carries `override.own` (its own
     fields, a subset of the merged `fields`) and `built_prompt` (the series config's wording
     for that view). The per-view editor names what it inherits from the character — "Its steps
     come from Ada and are shared by all four views" — and a prompt edit can now be diffed and
     reverted, which was the real bug: the client was sending `built_prompt: ""` for an
     overridden view, so Diff and "Series config" had nothing to work from.
     Tests: `ViewOverrideProvenanceTest` in `tests/test_api_refs.py` (7).
  5. **A placeholder cut entry's take media.** Its take lives in the other pass and
     `episode_status` gives the number but not the thumb/strip/mp4, so the UI loads the other
     pass's whole status to draw one clip. (In `web/src/api.ts`'s TODOs but missing from this
     list until the audit.)
  6. **The target wizard doesn't offer a two-stage LoRA chain or a list-valued param** — it
     binds the first widget and says so; `frames.max` stays a guess the probe render tests.
  7. **12d — subject reference sheets for a show's own target**, deferred on purpose in Phase 12.

  *Came off the list in the audit, already built:* the stronger keyframe framing wording
  (`targets/image/common.py`, `framing()`) and Kontext's composed character+plate reference
  (`comfy_nodes/h3_refsheet.py`'s composite mode, `h3refs.edit_refs`) — both finished in 8.6.
  *Also done 2026-09-23:* the three stale `TODO(contract)` notes in `web/src/api.ts` are gone —
  the two the work above closed, and the one claiming no route puts a media file in the episode
  for a clip's audio (`POST /h3pipe/audio/import` has been served since 9d, and the Audio
  from… window's **Upload…** has been using it all along; nothing in the UI needed changing).
  Three TODOs remain, matching items 2, 5 and 6.
  (The Wan VACE turbo LoRA came off this list on 2026-09-22: lightx2v's Wan2.2-Lightning T2V
  A14B 4-step pair is wired as its accelerator, 4 steps at cfg 1 with the old 20 / 10 at cfg
  3.5 kept as `base`. Left for a look: whether Seko V2 beats the 250928 release on stylised
  work, which is a render comparison, not code.)
