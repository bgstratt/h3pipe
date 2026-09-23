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

## Later — to define once P1, P2 and P5 are in

Written down so they don't get lost, deliberately unspecified until the phases above land.

- **P6 — voice cloning.** The one promise the pipeline doesn't keep: `LTXVReferenceAudio` is
  wired and tested but produces silence, so `clone` depends on samples supplied by hand
  (`docs/PLAN.md`, Phase 9c). An investigation at the node level, not a UI job.
- **P7 — full-size verification.** Built and never proved at final resolution: a real
  `dur: model` LTX render with the duration head, FL2VA with a recording (dub), and
  final-size looks on FL2VA / LTX / Wan. Each is a render session with notes, not code.
- **P8 — drag-and-drop and Upload… onto ref, view and keyframe slots** (`docs/PLAN.md`,
  Phase 8.6 "Left"). The multipart route exists; this is the UI half.
- **P9 — the small polish, in one sweep.** Shot detail's pre-override model/LoRAs/steps;
  per-target prompt override; `GET /h3pipe/shot` taking a `target` so the redo dialog can size
  a one-off; per-view character overrides and their `built_prompt`; a stronger keyframe framing
  hint; Kontext's composed character+plate reference; the target wizard's two-stage LoRA chains
  and list-valued params; and 12d, subject references for a show's own target.
  (The Wan VACE turbo LoRA came off this list on 2026-09-22: lightx2v's Wan2.2-Lightning T2V
  A14B 4-step pair is wired as its accelerator, 4 steps at cfg 1 with the old 20 / 10 at cfg
  3.5 kept as `base`. Left for a look: whether Seko V2 beats the 250928 release on stylised
  work, which is a render comparison, not code.)
