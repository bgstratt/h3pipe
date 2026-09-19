# h3pipe editor API

HTTP routes the ComfyUI node pack registers on ComfyUI's own server
(`PromptServer.instance.routes`), for the editor extension in `web/`. Same host
and port as ComfyUI (default `http://127.0.0.1:8188`). The routes are thin: every one
calls a pipeline function (`h3edit`, `h3jobs`, `h3takes`), which already has tests.
This file is the contract between the routes (`comfy_nodes/`) and the UI (`web/`).
Change it first when either side needs something new.

## Conventions

- Prefix `/h3pipe/`. JSON in and out (`Content-Type: application/json`), UTF-8.
- **Episodes are named by absolute folder path**, `ep`, in the query string for GET
  and in the body for writes. Every `ep` must be inside a configured root (see
  `/h3pipe/config`), or the route answers 403. Relative `path`s are relative to `ep`,
  use forward slashes, and must stay inside it (no `..`).
- **`pass`** is `"final"` or `"proxy"`. Default `"proxy"`.
- **Seeds are strings** in every request and response. Built seeds are 63-bit and
  JavaScript numbers lose precision above 2^53. Never parse them to a JS number.
- Errors: status 4xx/5xx with `{"error": "message"}`. 400 bad input, 403 path outside
  roots, 404 unknown episode/shot/take/file, 409 conflict (e.g. picking an unusable
  take), 500 anything else. The message is written for a person.
- Blocking work (file scans, hashing, ffmpeg, h3build) runs off the event loop
  (`run_in_executor`). No route blocks ComfyUI.

## Config

### `GET /h3pipe/config`
```json
{"roots": ["C:/Users/bgstr/ComfyProjects"], "comfy": "http://127.0.0.1:8188",
 "version": 1}
```
Stored in ComfyUI's user folder as `user/default/h3pipe/config.json`. If that file
doesn't exist, `roots` comes from `$H3PIPE_ROOTS` (`os.pathsep`-separated), else `[]`.

### `PUT /h3pipe/config`
Body `{"roots": [...]}`. The roots must exist. Returns the new config.

## Episodes

### `GET /h3pipe/episodes`
`h3edit.find_episodes(roots)`:
```json
[{"ep": "C:\\...\\DeanStories\\ep05", "name": "ep05", "series": "…", "title": "…",
  "built": {"final": true, "proxy": true}, "shots": 49, "script": "ep05.md"}]
```

### `GET /h3pipe/episode?ep=…&pass=proxy`
`h3edit.episode_status(ep, pass)`, with seeds turned into strings. First it sweeps
queued takes whose ComfyUI job is gone (`h3takes.sweep_queued`, with `as_of` taken
*before* reading the queue; see below). Shots come in **cut order**:
```json
{"episode": "ep05", "title": "…", "pass": "proxy", "fps": 24.0, "width": 448,
 "height": 256, "folder": "renders_proxy",
 "shots": [{
   "shot": "sh020", "orphan": false, "sequence": "sq01", "length": 107,
   "seconds": 4.458, "size": "medium", "subjects": ["dean"], "audio_policy": "generate",
   "cut": {"take": 1, "picked": true, "pass": "proxy", "placeholder": false,
           "usable": true, "trim_in": 0, "trim_out": 0, "locked": false, "note": "",
           "in_cut_file": true, "frames": 107},
   "override": {"fields": ["prompt", "seed"], "stale": false},
   "takes": [{"take": 1, "status": "ok", "has_video": true,
              "seed": "6430499148929255544", "seed_source": "stable", "frames": 107, "note": "",
              "overrides": [], "stale": [],
              "thumb": "renders_proxy/sh020/sh020_t01.jpg",
              "strip": "renders_proxy/sh020/sh020_t01_strip.jpg",
              "mp4": "renders_proxy/sh020/sh020_t01.mp4",
              "queued": "2026-09-18T18:00:16-05:00", "finished": "…",
              "comfy_prompt_id": "…",
              "save_notes": "…"}]
 }]}
```
- `status` is `queued` | `ok` | `failed`.
- `stale` holds any of `script`, `ref`, `preset`; `unknown` means a take from before
  sidecars.
- `thumb`, `strip` and `mp4` are null when the file is missing.
- `length` / `seconds` are the build's. A take's `frames` is its real frame count, from
  the saver (its sidecar's `frames`), or null (not rendered, or a take from before the
  saver wrote it). `cut.frames` is the cut take's `frames` when that take is usable, else
  null. They differ for a `dur: model` shot: the build writes an estimate
  (`length_estimated` on the entry) and the model picks the length when it renders (the
  sidecar's `length_source` is `predicted`, versus `estimate` or `script`). The
  timeline and Play all use `cut.frames` / the take's `frames` at `fps` when present.
- The strip is `STRIP_FRAMES` (8) cells of equal width, left to right, evenly spaced
  through the clip. Hover scrub picks a cell from the pointer's x position.

### `GET /h3pipe/shot?ep=…&pass=…&shot=sh020`
`h3edit.shot_detail(ep, pass, shot)`, seeds as strings. It has:
- `built`: the shotlist entry.
- `built_prompt`: text.
- `override`: this pass's effective override, without `base_hash`.
- `override_stale`.
- `effective`: what a render would use now: `prompt`, `seed`, `seed_source`, `model`,
  `loras`, `steps`.
- `takes`: each with its full `sidecar` and `files` (relative paths of mp4, thumb,
  strip, shotlist, h3_wav).

### `POST /h3pipe/build`
Body `{"ep"}`. Runs `h3build` for the final pass, then the proxy pass, on the
episode's series config and script (`h3edit.episode_series_config` / `episode_script`).
```json
{"ok": true, "passes": {"final": {"ok": true, "report": "…stdout…", "error": ""},
                        "proxy": {"ok": true, "report": "…", "error": ""}}}
```
A script error returns `ok: false` with the message (it names the line) in `error`.
The status is still 200: a script that doesn't compile is a result, not a server error.

## Files

### `GET /h3pipe/file?ep=…&path=renders_proxy/sh020/sh020_t01.mp4`
Streams a file inside `ep`, with HTTP Range support (aiohttp `FileResponse`) so
`<video>` can seek. The content type comes from the extension. The response carries
`Cache-Control: no-cache`, because a take re-rendered into the same number
(`--take N`) keeps its name.

## Rendering

### `POST /h3pipe/render`
Queues takes on ComfyUI's own queue and returns without waiting.
```json
{"ep": "…", "pass": "proxy", "shots": ["sh020", "sh030"],
 "redo": true,
 "seed_mode": "auto", "seed": null,
 "model": null, "loras": null, "steps": null, "prompt": null,
 "parent_take": 2, "note": "calmer kettle"}
```
- These fields map to `h3jobs.RenderRequest`, with `seed` as a string.
  - `seed_mode` is one of `auto`, `new` or `same`.
  - `redo: false` skips shots that already have a usable or queued take, as the CLI does.
  - `null` means "not set in this request", so the built value and `overrides.json`
    apply.
- The workflow comes from `h3jobs.resolve_workflow(None, WORKFLOW_NAME, <this
  server>)`: the copy saved in ComfyUI, else the repo copy. (Phase 7: the shotlist's
  target's binding names it, `h3jobs.target_workflow`; for H3 that is still
  `H3_Ref2VA_Shotlist_v1.json` among ComfyUI's saved workflows, and the repo copy is
  now `targets/video/minimax_h3_ref2va/workflow.json`.)
- For each shot the route calls `plan_job`, then `start_job`, `graph_for` and
  queue. It queues by POSTing to this server's own `/prompt` with a `client_id`
  of `h3pipe`, from an executor, and then calls `mark_queued`.
- A failure while queueing marks that take failed (`mark_failed`) and is reported
  for that shot. The other shots still queue.
```json
{"queued": [{"shot": "sh020", "take": 3, "prompt_id": "…", "seed": "…",
             "seed_source": "new"}],
 "skipped": [{"shot": "sh030", "reason": "has a usable take (pass redo: true)"}],
 "errors": [{"shot": "sh040", "error": "…"}]}
```

### `POST /h3pipe/cancel`
Body `{"ep", "pass", "shot", "take"}`. If the take's job is pending, it's deleted
from the queue (`POST /queue {"delete": [id]}`). If it's running, ComfyUI is
interrupted. Either way the take is marked `failed` with `save_notes: "cancelled"`.
Returns the take's new status.

## Cut and overrides

### `PUT /h3pipe/pick`
Body `{"ep", "pass", "shot", "take": 3 | null, "from_pass": null | "final" | "proxy"}`.
Uses `h3takes.pick`. A take that isn't usable answers 409 unless `"force": true`.
`take: null` goes back to the latest usable take. Returns `{"cut": <cut.json>}`.

### `PUT /h3pipe/cut`
Body `{"ep", "pass", "entries": [{"shot", "take"?, "pass"?, "trim_in"?, "trim_out"?,
"locked"?, "note"?}, …]}`. Replaces that pass's list, for reordering and trims.
Unknown shots are allowed; they become orphans. Returns `{"cut": …}`.

### `PUT /h3pipe/override`
Body:
```json
{"ep": "…", "pass": "proxy", "shot": "sh020", "both": false,
 "fields": {"prompt": "…", "seed": "12345", "model": null, "loras": [...],
            "steps": 10, "note": "…"}}
```
- Only the fields present are changed; `null` clears a field.
- `prompt`, `model`, `loras` and `steps` are per pass (`both: true` sets both
  passes); `seed` and `note` are shared.
- Pass fields are stamped with that pass's `base_hash` (`h3jobs.story_hash` of the
  shot as that pass builds it now), exactly as `h3.py override` does.
- Returns `{"override": {"final": {...}, "proxy": {...}}}`, each the effective
  override without `base_hash`, plus `stale` per pass.

### `DELETE /h3pipe/override?ep=…&shot=…[&pass=…]`
Removes the shot's override: one pass's fields if `pass` is given, else everything.

## Assemble

### `POST /h3pipe/assemble`
Body `{"ep", "pass", "partial": true}`. Runs `h3assemble` off the loop and returns
when it's done (can take minutes).
```json
{"ok": true, "output": "renders_proxy/ep05_proxy.mp4", "report": "…stdout…"}
```

## Live updates

The UI listens on ComfyUI's own websocket through the frontend `api` object. That
gives `progress`, `executing`, `executed` and `execution_error` for any prompt id it
knows from `/h3pipe/render`. Two custom events come from the node pack:

- **`h3pipe.take`**, sent by `H3SaveShot` right after it closes a sidecar, and by the
  routes whenever they change a take's status (queued, cancelled, swept):
  `{"ep": "<abs path>", "pass": "proxy", "shot": "sh020", "take": 3,
    "status": "ok", "thumb": "renders_proxy/sh020/sh020_t03.jpg"}`.
  The saver learns `ep` from `project_root` and `pass` from the subfolder
  (`renders_proxy` means proxy, anything else final).
- **`h3pipe.episode`**, `{"ep": "<abs path>"}`, sent after a build, pick, cut or override
  change, so every open editor view can refetch.

## Models

The UI reads ComfyUI's own lists: `GET /models/diffusion_models` (and
`/models/unet` on older installs) for the model picker, `GET /models/loras` for LoRAs.
No h3pipe route is needed. (Later: `GET /h3pipe/models` groups a target's model picker by
model family; see **Model families** at the end.)

## As built (Phase 2): readings of the points above that were ambiguous

- **`POST /h3pipe/cancel`:** 409 if the take isn't `queued`. A finished take is never
  overwritten.
- **Sweep in `GET /h3pipe/episode`:** for a queued take whose job has left the queue,
  `/history` decides:
  - an execution error marks the take `failed` with ComfyUI's exception message;
  - success with the sidecar still `queued` (a save node from before sidecars) closes it
    from disk (`h3jobs.finish_job`);
  - no history at all falls back to `sweep_queued`.
- **`POST /h3pipe/assemble` and `POST /h3pipe/build`:** a failure is 200 with
  `ok: false` and a top-level `error`. Build with no series config or no script returns
  `{"ok": false, "error", "passes": {}}`. Build always runs both passes.
- **`POST /h3pipe/render`:**
  - `shots: null` renders every shot.
  - `loras` also accepts `"name:strength"` strings.
  - `skipped` and `errors` entries carry `take` when one was reserved.
  - The `h3pipe.take` event sent at queue time says `queued`.
- **`PUT /h3pipe/override`:**
  - Pass fields on a pass that isn't built answer 409.
  - A shot in no build answers 404.
  - An empty `note` or `model` clears the field.
  - `DELETE` with `pass` keeps the shared `seed` and `note`.
- **`PUT /h3pipe/cut`:** duplicate shots and unknown entry fields answer 400.
- **Not handled yet:** ComfyUI multi-user mode (config always lives in `default/`),
  and a TLS-fronted ComfyUI (self-queueing uses `http://`).
- **Aliases:** every route also answers under `/api/h3pipe/...`.

## Round 2 additions

### `GET /h3pipe/browse?path=…`
A folder picker for choosing roots and episodes. It isn't limited to the roots,
because this is how roots are chosen. It lists folder names only, never file
contents.
```json
{"path": "C:\Users\bgstr\ComfyProjects\DeanStories", "parent": "C:\Users\bgstr\ComfyProjects",
 "episode": false, "truncated": false,
 "dirs": [{"name": "ep05", "path": "C:\…\ep05", "episode": true, "series_config": true}]}
```
- No `path` gives the starting points: the home folder and the drives on Windows.
  `parent` is then `null`; at a drive root it is `""`.
- `episode` means the folder has a `series.json` and a script; `series_config` means it has
  a `series.json`.
- Status is 404 if the folder doesn't exist, 403 if it can't be read.

### Missing references
- **`GET /h3pipe/episode`:** every shot carries `"missing_refs": [{"slot": "Picture 4",
  "kind": "image" | "audio", "path": "refs/_bg/x.png", "subject"?: "dean"}]`. These are
  the references its render needs that aren't on disk (`h3jobs.missing_refs`); empty
  means ready.
- **`POST /h3pipe/render`** takes `"allow_missing_refs": false` by default.
  - **When false**, a shot with missing refs isn't queued. It appears in `skipped`
    with a `reason` naming the missing refs, plus the same `missing_refs` list.
  - **When true, it renders anyway.** The loader replaces a missing picture with flat
    mid-grey, and a missing voice sample or recording with no audio reference. For H3
    that's close to text-to-video. The take's sidecar lists what was missing in
    `missing_refs` (slots).
  - The prompt still names the missing pictures. Writing a prompt without them is
    model-specific, so it belongs to the Phase 7 target adapters. (Done in Phase 7: see
    **Render anyway, target-aware** below. Grey stand-ins are now the fallback.)
- The CLI equivalent is `h3render --allow-missing-refs`. Without it, blocked shots are
  listed and skipped.

## References (Phase 5)

A **ref** is any conditioning input a render reads that the pipeline generates or
you supply. Refs are the same thing whatever model consumes them. Each has a `scope`:

| scope | kinds | named in | id |
|---|---|---|---|
| `series` | `character`, `prop`, `vehicle` (series config subjects), `location` (series config locations), `voice` (a subject's `voice_sample`) | the series config | `subject:<id>`, `location:<id>`, `voice:<id>` |
| `shot` | `keyframe`: `first` / `last` frame of one shot, for FL2V and I2V models | the script (a later field) or the editor | `shot:<shot>:first`, `shot:<shot>:last` |

- **Series refs are listed from the series config**, not from `refs_todo`. A character nobody
  uses yet can still be generated. `refs_todo` becomes a filter: "used by this
  episode", and which shots it blocks.
- **Shot keyframes:** Phase 5 builds their storage, takes and pick. Generating them
  (a still from the shot's prompt, or the previous shot's last frame) and feeding
  them to an FL2V target come with that target. Their home is
  `refs/shots/<shot>/<first|last>.png`. (The previous shot's last frame is built: see
  **Continuity keyframes** at the end.)
- A **ref take** is one generated or imported candidate. It lives in
  `refs/_takes/<ref key>/<ref key>[_<view>]_tNN.png`, with a sidecar `…_tNN.json` in
  the video takes' format (status, seed, prompt, model, LoRAs, steps, queued,
  finished, and `source`: `generated` or `imported`). The ref key is the id with `:`
  replaced by `__`.
- A **character** has views (`01_threequarter`, `02_side`, `03_back`, `04_face`, as
  in `kreagen.VIEWS`). Each view has its own takes and pick. Picking a view that
  completes the set stitches the sheet with `mksheet` into the series config's `sheet` path.
- **Picking** a take copies it to the path the series config names, which is the file renders
  read. The pick is recorded in `refs/_picks.json`, so the UI knows which take is
  live. Re-picking changes the file's sha1, so video takes that used the old file
  show `ref`-stale.
- **Ref overrides** (prompt, seed, model, LoRAs, steps) live in
  `refs/_overrides.json`, keyed by ref id (and view), in the same shape as shot
  overrides.

### `GET /h3pipe/refs?ep=…`
```json
{"refs": [{
   "id": "subject:dean", "scope": "series", "kind": "character", "name": "Dean",
   "path": "refs/dean/dean_sheet_4panel.png", "exists": true, "sha1": "…",
   "used_by": {"final": ["sh010", "sh020"], "proxy": ["sh010", "sh020"]},
   "prompt": "…the prompt a generate would use now…",
   "override": {"fields": [], "stale": false},
   "views": [{"view": "01_threequarter", "picked": 2,
              "takes": [{"take": 1, "status": "ok", "seed": "…", "image": "refs/_takes/…_t01.png",
                         "source": "generated", "note": ""}]}],
   "takes": [], "picked": null
 }]}
```
- Characters have `views`; every other ref has `takes` and `picked` at top level.
- `exists` means the file renders read is on disk.
- A `voice` ref lists `takes` for imported audio only; nothing generates voices yet.

### `POST /h3pipe/refs/generate`
Queues one candidate per call. It returns without waiting, like `/render`.
```json
{"ep": "…", "ref": "subject:dean", "view": "02_side" | null, "count": 1,
 "seed_mode": "auto" | "new" | "same", "seed": null, "prompt": null, "model": null,
 "loras": null, "steps": null, "note": ""}
```
- A character with `view: null` queues all four views, sharing one seed (as
  `kreagen` does).
- `count` greater than 1 queues that many candidates, each with a new seed.
- The workflow is `krea2_refs_t2i.json`, found through `resolve_workflow`. (Phase 7: the
  `krea2` image target's binding; the repo copy is `targets/image/krea2/workflow.json`.)
- A new save node, `H3SaveRefTake`, takes the place of the workflow's `SaveImage`.
  It writes the take's image and closes its sidecar, as `H3SaveShot` does, and sends
  `h3pipe.ref` `{"ep", "ref", "view", "take", "status"}`.
- Returns `{"queued": [{"ref", "view", "take", "prompt_id", "seed"}], "errors": [...]}`.

### `PUT /h3pipe/refs/pick`
Body `{"ep", "ref", "view"?, "take"}`. Copies the take into place, stitching the
sheet when all four views are picked. Returns the ref as `/refs` lists it. 409 if the
take isn't usable.

### `POST /h3pipe/refs/import`
Adds an image (or, for `voice`, an audio file) as a new take with `source:
"imported"`. Body `{"ep", "ref", "view"?, "source_path"}`: a file on the ComfyUI
machine. A multipart upload (`file`) comes later, with drag and drop. Returns the new
take.

### `PUT /h3pipe/refs/override` and `DELETE /h3pipe/refs/override`
Same shape as the shot override routes, keyed by `ref` (and `view`).

### References: as built (Phase 5), where the text above left room
- **Extra fields on a ref:** `key`, `subject`, `can_generate`, `why_not`, and `effective`
  (prompt, seed, seed_source, model, loras, steps, width, height). `override.values`.
  `views: []` for refs that aren't characters. Each view has its own `prompt`,
  `override` and `effective`.
- **A character's top-level `prompt`** is the 4-panel sheet text (as in `refs_todo`).
  What each view actually generates with is in that view's entry.
- **Extra fields on a ref take:** `view`, `usable`, `audio` (voices: `audio` set,
  `image: null`), `seed_source`, `prompt`, `model`, `loras`, `steps`, `width`,
  `height`, `queued`, `finished`, `comfy_prompt_id`, `save_notes`, `overrides`.
- **Refs live next to the series config.** A series config in the parent folder gives paths like
  `../refs/…`, and `/h3pipe/file` serves those only inside that series config's folder and a
  configured root.
- **Keyframes** are listed once they exist (a live file or a take). Any shot in a build
  can import or pick one; a shot in no build is 404.
- **`h3pipe.ref` status values:** `queued`, `ok`, `failed`, `picked` (after a pick or an auto-pick).
- **Auto-pick:** `GET /h3pipe/refs` gives a ref with NO live file its first finished candidate (`h3refs.auto_pick`), per view for a character, stitching the sheet once all four views have one. A ref whose file exists is never replaced without an explicit pick. This is `kreagen`'s rule for a missing file.
- **Ref overrides** have one level: the ref, or a view, not per pass. A character's
  `prompt` override needs a view. The response is `{"override": {...fields, "stale"}}`.
  `base_hash` is the sha1 of the built prompt.
- **Generate:**
  - `count` 1–16. Only the first candidate follows `seed_mode` or a typed or pinned
    seed; the rest get new seeds.
  - `auto` keeps the stable seed until the ref has a usable take.
  - `prompt` with a character and `view: null` is 400; voice and keyframe generates are
    400.
  - Pick accepts `force: true`.
- **Import:** images png/jpg/jpeg/webp, audio wav/mp3/flac/ogg/m4a. The take keeps its
  extension, and picking copies the bytes to the series config's path.

### Round 2 contract fixes (after merging the Refs tab)
- **`GET /h3pipe/browse?path=…&files=image|audio`** also lists matching files as
  `files: [{name, path, size}]`, for import. `files` is absent without the parameter;
  an unknown type answers 400.
- **Each ref in `GET /h3pipe/refs`** also carries `override_values` (the same as
  `override.values`) and `built_prompt` (the series config's prompt before any override). The
  UI reads these flat names.
- **Already served, which the UI can use once its TODOs are cleared:** `comfy_prompt_id`
  on ref takes and episode takes, `effective` on refs and on each character view, and
  per-view `prompt`/`override`/`effective`. Ref files beside a parent-folder series config
  come through `/h3pipe/file` as `../refs/…` paths.

## Targets (Phase 7)

Everything model-specific lives in a **target**: `targets/video/<id>/` for a shot model,
`targets/image/<id>/` for a reference-image model (`targets/__init__.py`). Today there is
one of each: `minimax_h3_ref2va` (MiniMax H3 Ref2VA) and `krea2`. A built shotlist
belongs to one video target, the series config's `series.target` (default
`minimax_h3_ref2va`). Episodes that mix targets arrive with Phase 8. (Phase 8: they have;
`ltx2` is the second video target. See **Phase 8 as built** at the end.)

### `GET /h3pipe/targets[?kind=video|image]`
Every target, for pickers. Nothing here is per-episode.
```json
{"targets": [{
   "id": "minimax_h3_ref2va", "kind": "video", "label": "MiniMax H3 Ref2VA",
   "default": true,
   "presets": {"final": {"model": "…", "lora": "…", "steps": 8, "width": 1344, "height": 768},
               "proxy": {"model": null, "lora": "…", "steps": 4, "width": 480, "height": 272}},
   "widgets": {"model": {"class_type": "UNETLoader", "field": "unet_name"},
               "loras": {"class_type": "LoraLoaderModelOnly", "name": "lora_name",
                         "strength": "strength_model", "input": "model", "chain": true},
               "steps": {"via": "loader"}, "seed": {"via": "loader"}},
   "workflow": "H3_Ref2VA_Shotlist_v1.json",
   "loader": "H3ShotListLoader", "saver": "H3SaveShot",
   "template": {"fps": 24.0, "frames": {"step": 17, "base": 5, "max": 3592},
                "size_multiple": 32}},
  {"id": "krea2", "kind": "image", "…": "…"}],
 "default": {"video": "minimax_h3_ref2va", "image": "krea2"}}
```
- `presets` are the target's own defaults. The series config's `series` / `proxy` blocks
  and profiles override them per episode; `GET /h3pipe/shot`'s `effective` is what a
  render of that shot would actually use.
- `widgets` is the binding: which node class and widget takes each render parameter.
  `{"via": "loader"}` means the target's loader reads it from the frozen shotlist, so
  the graph isn't patched for it. A picker for a `{"class_type", "field"}` widget can
  list ComfyUI's own choices from `/object_info/<class_type>`.
- `presets.proxy.model: null` means the proxy renders the series model (then the final
  preset's).
- An unknown `kind` answers 400.

### `target` and `profile` on shots and takes
- **`GET /h3pipe/episode`** has a top-level `"target"`. Each shot has `"target"` (the
  video target it renders on) and `"profile"` (the render profile it was built with, or
  `null`). Each take has `"target"` from its sidecar (`null` for a take from before
  sidecars).
- **`GET /h3pipe/shot`** has `"target"` and `"profile"`. `built` carries `profile`, and a
  profile's LoRA list as `loras`, when the shot has them.
- **Take sidecars** record `"target"`: the job's real target id, not a constant. The frozen
  shotlist beside the take records it too, at the top level.
- `overrides.json` is keyed by target already (`shots.<shot>.<target>`); the routes read
  and write the shot's own target's block.

### Render profiles
A profile is a named `{target?, model?, loras?, steps?}` in the series config's
`profiles` block, picked with `profile:` on a sequence or a shot (docs/AUTHORING.md).
Build folds it into the shotlist, so the routes see the result: `model`, `steps`, and
`loras` (a list) or `lora` on the built shot, plus `profile` naming it. Precedence, weakest
first: target preset → series config pass block → sequence profile → sequence lines →
shot profile → shot lines → `overrides.json` → the request.

### Render anyway, target-aware
`POST /h3pipe/render` with `"allow_missing_refs": true` now asks the shot's target to
write the shot **without** the missing refs, when it can:
- **H3 can.** A missing subject picture drops out of the `<Picture N>` slots and the
  subject is described in words (its series config `design`). A missing plate leaves
  `<Picture 4>` unmentioned and describes the location in words; the loader still feeds
  flat grey into that input. A missing voice sample or recording makes the shot
  `generate` (no audio reference; voices come from each `voice` line). The frozen
  shotlist carries this recompiled shot.
- **The recompile needs the build to be current.** It reads `shotlist/shots.json` and the
  series config, and first checks that they still build the exact shot in the shotlist.
  If they don't, or the target can't recompile, the render falls back to grey stand-ins
  with the built prompt (the Phase 5 behaviour).
- **A prompt override still wins:** the text you wrote is rendered as is.
- The take's sidecar says what happened: `missing_refs` (the slots, as before),
  `missing_mode` (`"recompiled"` or `"blank"`), and `missing_note` (why it fell back to
  `blank`). The last two are only present on a take rendered anyway.

## Phase 8 additions: retargeting a shot (contract written before building)

- **`PUT /h3pipe/override`:** `fields.target` sets a shot's video target, e.g.
  `"ltx2"`, or `null` to go back to the built target. It is **shared by both passes**,
  like `seed`. The shot's IR (`shotlist/shots.json`) is recompiled for that target at
  queue time. The target's own prompt, model, LoRA and steps defaults apply. The
  per-pass `prompt` override is ignored for a retargeted shot, because a prompt written
  for one model isn't valid for another. The response and the override view show
  `target`.
- **`POST /h3pipe/render`** takes an optional `"target"` for one run, beating the
  override.
- **`GET /h3pipe/episode`** and **`GET /h3pipe/shot`:** each shot's `target` is the one
  its next render will use: request, then override, then script, then series config.
  `built_target` is what the build compiled it for. Shot detail's `effective` also
  carries `target` and that target's `width`/`height`/`length`.
- **Takes** record the `target` they rendered with (already true since Phase 7).
- **`GET /h3pipe/targets`** is what the UI's target picker lists, video targets only.
  Model and LoRA pickers filter by the chosen target's binding widgets.

## Phase 8 as built

Everything in **Phase 8 additions** above is implemented as written. What the text left
open, and what was added:

- **`PUT /h3pipe/override`:**
  - `fields.target` must name a video target (`GET /h3pipe/targets?kind=video`), else 400.
    Setting the shot's built target, or `null`, clears the retarget.
  - Pass fields (`prompt`, `model`, `loras`, `steps`) and `seed` / `note` go to the block
    of the target the shot renders on **after** the request's `target` change, and are
    stamped against that target's entry (the retargeted one when it is retargeted). So one
    request can retarget a shot and set its LTX steps.
  - The response adds `"target"` (what the next render uses) and `"built_target"` beside
    `"override"`. Each pass's view carries `target` when the shot is retargeted.
  - A pass field on a retargeted shot whose IR no longer compiles (the build is out of date)
    answers 409.
- **`DELETE /h3pipe/override`** without `pass` also clears the retarget. The response adds
  `target` and `built_target`.
- **`POST /h3pipe/render`:**
  - `target` (a video target id or `null`) beats the override for this run; an unknown one is 400.
  - Each shot renders with its own target's workflow, resolved once per request. A target
    whose workflow can't be read no longer fails the whole request with 500: each of its
    shots is reported in `errors`, and shots on other targets still queue.
  - A shot that can't be retargeted (shots.json / the series config no longer build it:
    rebuild) is reported in `errors` with the reason.
  - `queued` entries carry `"target"`.
  - An LTX shot's keyframes (`refs/shots/<shot>/first.png`, `last.png`, when they exist)
    are uploaded to ComfyUI's input folder as `h3pipe/<sha1>.png` (through ComfyUI's own
    `POST /upload/image`, so it works however ComfyUI is installed) before the job is queued.
  - A retargeted shot's takes on its *old* target don't count as done: `redo: false` still
    renders it on the new one.
- **`GET /h3pipe/episode`:**
  - Shots come from every target's shotlist, in script order (`shots.json`), merged with
    `cut.json` as before.
  - Each shot has `target` (the next render's: override, else build) and `built_target`.
    `override.fields` includes `"target"` when the shot is retargeted. `missing_refs` is the
    next render's (an `ltx2` shot needs none: its keyframes are optional).
  - `retarget_error` (a string) is present when the shot is retargeted but its IR can't be
    compiled for that target: rebuild the episode.
  - `length`, `seconds`, `subjects`, `audio_policy`, `size` are still the **built** entry's.
    The next render's length is in shot detail's `effective.length`.
  - A take's `stale` can also hold **`target`**: the take was rendered on another target than
    the shot's next render uses. The other reasons are judged against the entry the take's
    own target would render now.
  - Top-level `target` is the series target (`shotlist.json`'s).
- **`GET /h3pipe/shot`** adds `built_target`. `effective` adds `target`, `width`, `height`,
  `length`, and, when there is something to say, `notes` (e.g. "the prompt override was
  ignored: …", "audio clone renders as generate on LTX-2: …") and `error` (the shot can't
  be rendered on its target). `override` carries `target` when retargeted. `built` stays the
  built entry; for a retargeted shot `effective.prompt` is the new target's prompt.
- **`GET /h3pipe/targets`** adds, per target:
  - `short`: a short label ("H3", "LTX-2").
  - `capabilities`: `{policies, policy_fallback, voice_reference, subject_refs,
    keyframes, prompt ("sections" | "prose"; `minimax_h3_fl2va`: "fields"), negative_prompt, duration ("predict" | "script")}`.
  - `template` may carry `fps: "series"` (the target renders at the series config's fps),
    `max_size` (`{long_side, pixels}`) and `size_fit: "snap"`.
  - `widgets` values are always single specs (for a param patched into several widgets,
    the first). An `ltx2` widget spec may carry `feeds` ([class, input]: which of several
    nodes of that class), `all` or `scale`; a picker only needs `class_type` and `field`.
    `ltx2` has no `steps` widget: its sampling schedule is fixed in the workflow.
- **Take sidecars** may add `built_target` (retargeted), `inputs` (`{role: name in
  ComfyUI's input folder}` of the keyframes used) and `notes` (a list of strings: audio
  fallbacks, an ignored prompt override, a `steps` value the target doesn't use). An `ltx2`
  take's `refs` lists its keyframes with `role` and `optional: true`, `sha1: null` when the
  file didn't exist.
- **Shot lengths (`dur: model`)**: `capabilities.duration` is `"predict"` (only `ltx2`: the
  model's duration head can choose the length) or `"script"`. Every take sidecar has
  `length_source`: `script` (the build's length), `estimate` (`dur: model` rendered at the
  build's estimate: the target can't predict, or the duration head isn't installed; a
  `notes` entry says which) or `predicted` (the saver's `frames` is the length; `length`
  is the estimate). A `dur: model` entry has `length_estimated: true`, and on a predicting
  target `duration_predict: {min_seconds, max_seconds}`.
- **Files:** an episode that mixes targets has `shotlist/shotlist.<target>.json` and
  `shotlist.<target>_proxy.json` beside `shotlist.json` / `shotlist_proxy.json`, each with a
  top-level `"target"`. `shotlist.json` is always the series target's, even when no shot is
  left on it.

## Continuity keyframes

A shot's first keyframe is usually the previous shot's last frame, so the cut runs on
without a jump. `ltx2` reads `shot:<shot>:first` / `last` (Phase 8), and so does
`minimax_h3_fl2va` (see its section below); `minimax_h3_ref2va` doesn't use keyframes (`GET /h3pipe/targets`: `capabilities.keyframes` is `[]`).

### `POST /h3pipe/refs/keyframe`
Cuts one frame out of a video take with ffmpeg and adds it as a new take of the shot's
keyframe ref.
```json
{"ep": "…", "pass": "proxy", "shot": "sh020", "which": "first",
 "source_shot": null, "source_take": null, "frame": null, "pick": null, "note": ""}
```
- **`which`**: `"first"` (default) or `"last"`: the keyframe written.
- **`source_shot`**: default the neighbouring shot **in the pass's cut order**
  (`cut.json` reconciled with the script, orphans skipped): the previous shot for
  `first`, the next shot for `last`.
- **`source_take`**: default the take that shot's cut entry uses, as `GET /h3pipe/episode`
  reports it in `cut.take`: its pick (a placeholder's take comes from the other pass), else
  its latest usable take. A given `source_take` is a take of `pass`.
- **`frame`**: a frame number (0-based, in decode order; negative counts from the end,
  `-1` being the last), `"first"` or `"last"`. Default `"last"` for `which: first`, `"first"`
  for `which: last`. The frame is exact: ffmpeg's `select` counts decoded frames.
- **`pick`** (added): `null` (default) picks the new take only when the keyframe has no
  live file yet (the auto-pick rule); `true` always picks it; `false` never does.
- **Returns** the ref as `GET /h3pipe/refs` lists it.
- **Events:** `h3pipe.ref` `{ref, view: null, take, status: "ok"}`, then `status: "picked"`
  if it was picked, then `h3pipe.episode`.
- **Errors:** 400 for a shot with no previous (next) shot in the cut, a bad `which` /
  `frame` / `pick`, or a frame outside the take; 404 for a shot in no build or a
  `source_take` that doesn't exist; 409 when the source shot has no usable take, or its cut
  entry names one that isn't usable (queued, failed, no mp4); 500 without ffmpeg / ffprobe
  on ComfyUI's PATH.
- **The take's sidecar** has `source: "frame"` and `source_shot`, `source_take`,
  `source_pass`, `source_frame` (the index used), `source_frames` (the take's frame count),
  `source_mp4` (relative to the episode) and `source_sha1`. `width`/`height` are the
  frame's. `GET /h3pipe/refs` shows such a take with
  `"from": {"shot", "take", "pass", "frame", "frames"}`.
- **Staleness:** video takes record each keyframe's sha1 (`refs` in the sidecar), so
  re-extracting and picking a new frame marks the takes that used the old one `ref`-stale.
  Also new: a take rendered **without** an optional keyframe (sha1 `null`) is `ref`-stale
  once that keyframe exists, because a render would now use it.
- The CLI is `python h3.py keyframe <ep> <shot> [--from-prev | --from <shot>[:take]]
  [--first | --last] [--frame N] [--proxy] [--pick | --no-pick]` (final pass unless
  `--proxy`, like the other edit commands).
- **Not yet:** there is no route to unpick or delete a ref take, so the editor has no
  "Clear" for a keyframe.

## The `ltx2_ingredients` target (as built)

A third video target: LTX-2.3 with the IC-LoRA "ingredients", which keeps characters,
props and the set looking like their refs. Nothing new in the routes; what the existing
ones now show:

- **`GET /h3pipe/targets`** lists it: `label` "LTX-2.3 ingredients (character/plate refs)",
  `short` "LTX+refs". `capabilities` gains `reference_sheet` (true only here), and
  `subject_refs` is true for it. `template`: `fps: 24`, `frames: {step: 8, base: 121, max:
  121}` (every shot is 121 frames), `size_multiple: 32`. Presets: final 768×448, proxy
  512×288, both `ltx-2.3-22b-distilled-fp8.safetensors` + `ltx-2.3-22b-ic-lora-ingredients-0.9`
  at 1.0, 8 steps, text encoder `gemma_3_12B_it_fp4_mixed`. Its widgets include `steps`
  (the template's KSampler), and `model` is patched into all three loaders that read the
  checkpoint.
- **Shotlist entries** carry `panels`: the reference sheet, in order, each
  `{"subject", "kind", "path", "view"?: "body" | "face"}` or `{"location", "kind": "plate",
  "path"}`. `prompt` is `"Reference sheet: …\n\nGenerated video: …"` (a shot with no panels:
  plain LTX-2 prose).
- **`missing_refs`** (episode listing, render `skipped`) lists the panels' files that
  aren't on disk, with slots `"sheet panel N"` and `subject` or `location`. They block the
  shot like H3's pictures. `allow_missing_refs: true` recompiles the shot without them
  (`missing_mode: "recompiled"`): they leave the sheet and the `Reference sheet:` half, and
  a shot left with none renders text-only (the take's `notes` say so).
- **Queueing** (`POST /h3pipe/render`, `h3render`): before the take is reserved, the sheet
  is composed from the live ref files at the render size (no text, one panel per element
  tiling the sheet with thin black lines between them; `comfy_nodes/h3_refsheet.py`, run
  as a subprocess of the running Python, which needs PIL: ComfyUI's has it) and uploaded
  as `h3pipe/<sha1>.png`.
  Composing fails clearly when PIL is missing; the shot is reported in `errors`.
- **Take files and sidecar:** the sheet is kept as `<shot>_tNN_refsheet.png` beside the
  mp4. The sidecar's `refs` lists each panel's file with its `sha1` (so re-picking a view
  makes the take `ref`-stale) and then the sheet itself (`slot: "reference sheet"`,
  `role: "sheet"`, its path in the take folder and `sha1`); `inputs` is
  `{"sheet": "h3pipe/<sha1>.png"}`. When a LoRA list from a script line or profile didn't
  name the IC-LoRA, it is put back first and `notes` says so.

## The `minimax_h3_fl2va` target (as built)

A fourth video target: MiniMax H3 first/last-frame to video + audio, on ComfyUI's H3
image-to-video template (`MiniMaxH3ImageToVideo`; with no keyframe it is text-to-video).
Nothing new in the routes; what the existing ones now show:

- **`GET /h3pipe/targets`** lists it: `label` "MiniMax H3 FL2VA (first/last frames)",
  `short` "H3 FL2V". `capabilities`: `keyframes: ["first", "last"]`, `policies:
  ["generate", "dub", "dub_keep_foley"]`, `policy_fallback: "generate"` (clone),
  `subject_refs` / `voice_reference` / `negative_prompt` false, `prompt: "fields"`.
  `template` is H3's (`fps: 24`, `frames: {step: 17, base: 5, max: 3592}`,
  `size_multiple: 32`); the build warns past the model's trained 362 frames. Presets: final
  1344×768, `minimax_h3_fl2va_pruned_int8_convrot` + `minimax_h3_fl2v_turbo_8step_v1.0` at
  8 steps (`res_multistep`); proxy 448×256, the 4-step lightx2v v0.1 LoRA at 4 steps
  (`euler`). `widgets` add `sampler` (KSamplerSelect) and have `steps` (BasicScheduler).
- **Shotlist entries** are like `ltx2`'s: `keyframes` `{first, last}` (the conventional
  paths), no `panels`, no `negative`; `audio_intent` / `audio_note` on a clone shot. The
  `prompt` is H3's base-mode format: `integrated_multimodal_description: [Shot 1] …`,
  `overall_soundscape: …`, `non_diegetic_music: …`, with no `<Picture N>` (subjects in
  words).
- **Keyframes** come from continuity (`POST /h3pipe/refs/keyframe`) or an import, and are
  optional. At queue time the prompt gets H3's alignment line for the frames the render
  has, first line then a blank line: first only "For the target video, at 0.00 seconds
  into the target video, <Picture 1> (from [Shot 1]) is fully referenced."; first and last
  "How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns
  with the 0.00-second mark …; Picture 2 (from Shot 1) aligns with the S.SS-second mark …";
  last only the same with `<Picture 1>` at S.SS. The take's frozen shotlist has that
  prompt; `effective.prompt` in `GET /h3pipe/shot` is the built one.
- **Dub:** a `dub` / `dub_keep_foley` shot's `refs` add `slot: "dialogue recording"` (the
  series config's `audio.track`, or the shot's `audio_file`; `kind: "audio"`, required,
  so a missing file blocks it; rendering anyway recompiles it as `generate`). At queue time
  its `audio_in`–`audio_out` slice is cut (stdlib `wave`; ffmpeg for other formats; mono
  made stereo), uploaded as `h3pipe/<sha1>.wav`, anchored at frame 0 by a
  `MiniMaxH3AddGuide`, and kept as `<shot>_tNN_dub.wav` (`slot: "dialogue slice"`, `role:
  "audio"`). The sidecar's `inputs` then has `audio` beside `first` / `last`.

## Model families

Each target declares the model family every model setting must be (`target.json`
`models`: `{"<param>": {"family", "patterns"}}`, for `model`, `text_encoder`, `video_vae`,
`audio_vae`, `upscaler`, `duration_head` and, on two-stage targets, `model_high` /
`model_low`). A file passes by name (case-insensitive globs, plus the series config's
`model_families`), else by its safetensors header (`targets/modelid.py`: tensor names and
key shapes, `model_version` / other metadata; the weights are never read). Header results
are cached by (path, size, mtime) in `<ComfyUI user dir>/default/h3pipe/modelid_cache.json`.

### `GET /h3pipe/targets`: `models`
Each target has `"models": {"<param>": {"family", "label", "patterns", "folder"}}`, e.g.
`ltx2`'s `"model": {"family": "ltx2.5", "label": "LTX 2.5", "patterns": ["ltx-2.5*",
"*ltx*2.5*", "*ltx*2_5*"], "folder": "diffusion_models"}`. `{}` for a target that declares
none.

### `GET /h3pipe/models?target=ltx2&param=model[&ep=…]`
One model param's files, as ComfyUI lists them (`folder_paths.get_filename_list` of the
param's folder), each checked against the family:
```json
{"target": "ltx2", "param": "model", "family": "ltx2.5", "label": "LTX 2.5",
 "patterns": ["ltx-2.5*", "*ltx*2.5*", "*ltx*2_5*"], "folder": "diffusion_models",
 "class_type": "UNETLoader", "field": "unet_name", "fingerprint": true,
 "files": [
  {"name": "ltx-2.5-22b-distilled-transformer-comfy-int8-convrot.safetensors",
   "match": "name", "mismatch": false, "family": "ltx2.5", "label": "LTX 2.5",
   "confidence": "name", "detail": ""},
  {"name": "wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors", "match": "other",
   "mismatch": true, "family": "wan2.2-i2v-14b-low", "label": "Wan 2.2 I2V 14B low-noise",
   "confidence": "name", "base": "wan2.2-i2v-14b",
   "detail": "wan2.2_i2v_low_noise_14B_fp8_scaled is Wan 2.2 I2V 14B low-noise (tensors + name), but this LTX-2 target's model must be LTX 2.5"},
  {"name": "z_image_turbo_bf16.safetensors", "match": "other", "mismatch": false,
   "family": null, "label": "", "confidence": "unknown", "detail": "…"}]}
```
- `match` is `name` (named like the family; nothing read), `fingerprint` (the header says
  it is the family, or a parent family the header can't narrow, e.g. an H3 merge named
  neither Ref2VA nor FL2VA) or `other`. `files` lists `name`, then `fingerprint`, then
  `other`, each group in ComfyUI's order.
- `mismatch: true`: the header says another family. A render with that file is skipped
  unless the request says `allow_model_mismatch`.
- `confidence` is how the family was found: `metadata`, `tensors`, `name` (the header's
  family narrowed to a variant by the name; `base` is the header's family) or `unknown`.
- `fingerprint: false`: the server couldn't resolve model paths, so only names were checked.
- Headers are read only for files whose name doesn't match, then cached; the handler runs
  off the event loop like every route.
- `param` defaults to `model`. A target that declares no family for the param, or an
  unknown target, answers 400. `ep` (optional, inside a root) adds that series config's
  `model_families` patterns.

### `POST /h3pipe/render`: `allow_model_mismatch`
- Before a take is reserved, every model file the job loads is checked
  (`h3jobs.check_models`; paths through ComfyUI's `folder_paths`):
  - name match: nothing to say;
  - no name match, the header says the family: renders, with a note in the sidecar's `notes`;
  - unknown header, or not found: renders, with a note;
  - **another family**: the shot is skipped, `{"shot", "reason": "model mismatch: …
    (pass allow_model_mismatch: true to render anyway)", "model_mismatch": [check, …]}` in
    `skipped`. A check is `{"param", "file", "family", "label", "patterns", "match",
    "found", "message", "block"}`.
- `allow_model_mismatch: true` (a bool; anything else is 400) renders it anyway; the
  sidecar's `notes` say "model mismatch, rendered anyway: …".
- The CLI does the same (`h3render --allow-model-mismatch`), finding files under
  `$COMFYUI_PATH/models` and caching in the temp folder; without `$COMFYUI_PATH` it checks
  names only, and a name that doesn't match is a note. `--dry-run --check-nodes` lists
  every check.

### The editor
The model picker lists the files of the target's family first, then an "Other files
(unverified)" group. Picking one of those shows the file's `detail`. When it is a
`mismatch`, the redo dialog offers "Render anyway (model mismatch)", which sends
`allow_model_mismatch: true`. A render that skips a shot for a mismatch shows a warning
toast with the reason. A server without `/h3pipe/models` keeps the flat list.
