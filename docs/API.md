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
- A take's `fps` is the frame rate it was rendered at (its sidecar's `fps`; null for a take
  from before sidecars recorded it: the episode's). `cut.fps` is the cut take's, else the
  shot's target's. They differ from the episode's `fps` on Wan 2.2 14B (16 fps): the
  timeline and Play all time such a take as `frames / fps` of its own, and trims stay in
  the episode's frames. A shot's `seconds` is its build's `length` at its target's rate.
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
    keyframes, prompt ("sections" | "prose"; `minimax_h3_fl2va`: "fields"), negative_prompt, duration ("predict" | "script"),
    audio ("generate" | "none": the Wan targets make no sound)}`.
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

## The Wan 2.2 targets (as built)

Three more video targets, all silent: `wan22_i2v`, `wan22_ti2v`, `wan22_vace`. What the
existing routes now show:

- **`GET /h3pipe/targets`** lists them: `label` "Wan 2.2 14B I2V" / "Wan 2.2 5B TI2V" /
  "Wan 2.2 14B VACE (refs)", `short` "Wan I2V" / "Wan 5B" / "Wan+refs". `capabilities`:
  `audio: "none"`, `policies: ["silent"]`, `policy_fallback: "silent"`, `prompt: "prose"`,
  `negative_prompt: true`, `keyframes` `["first", "last"]` (I2V, VACE) or `["first"]` (5B),
  `subject_refs` true only for VACE. `template`: 14B `fps: 16.0`, `frames: {step: 4, base: 5,
  max: 161}`, `size_multiple: 16`; 5B `fps: 24.0`, `max: 241`, `size_multiple: 32`; all
  `size_fit: "snap"` under 720p. Presets: I2V final 832×480 / proxy 640×352, 4 steps with
  the lightx2v LoRA pair; 5B final 1280×704 / proxy 640×352, 20 / 12 steps; VACE final
  832×480 / proxy 640×352, 20 / 10 steps at cfg 3.5. `widgets` add `model_low` (the 14B's
  low noise model), `cfg`, `sampler`, `shift` (and VACE's `vace_strength`); `loras` is the
  high noise stage's spec (a 14B binding has one LoRA chain per stage: `stage` routes a
  LoRA, else its name's `high_noise` / `low_noise`, else both).
- **Shotlist entries** are like `ltx2`'s (`keyframes`, `negative`, a prose `prompt` with
  no sound and the lines as silent acting), with `audio_policy: "silent"` and
  `audio_intent` / `audio_note` on every shot; VACE entries add `panels` (the subjects, as
  `ltx2_ingredients`', without the plate). A shotlist's `defaults` carry `model_low`,
  `loras` (the 14B I2V preset's per-stage list, used when no `lora` names one), `cfg`,
  `split` (the fraction of the steps the high noise model samples) and the rest.
- **`missing_refs`** entries may carry `anyway: false` and `why`: the shot can't render
  without that ref even with `allow_missing_refs: true`. That is `wan22_i2v`'s first frame:
  `{"slot": "first frame", "kind": "image", "path": "refs/shots/sh050/first.png",
  "anyway": false, "why": "Wan 14B I2V needs a first frame: generate one or use continuity
  (or import one), or retarget to wan22_ti2v"}` (Phase 8.5 wording). **`POST /h3pipe/render`** skips such a shot either way,
  with `why` as its `reason`. The editor shows `why` and offers no render-anyway for it.
- **VACE's reference image** is composed when the shot is queued, like the ingredients
  sheet but on white (one panel per subject), uploaded as `h3pipe/<sha1>.png`, and kept as
  `<shot>_tNN_reference.png` (`slot: "reference image"`, `role: "reference"`); its panels'
  files are required refs (`slot: "reference panel N"`). Keyframes become VACE control
  frames.
- **Take sidecars** now carry `fps` on every take (the queuer writes it; the saver writes
  it too once ComfyUI has reloaded the node pack). Wan takes are mute mp4s at their
  target's fps (16 for 14B), with a `notes` entry saying the audio renders silent.
- **`h3assemble`** (and the `assemble` route) convert every clip to the episode's fps (the
  series config's `series.fps`) with ffmpeg's `fps` filter, timing each converted clip by
  its duration on the cut's running clock, lay silence under mute clips, and scale any clip
  of another size to the cut's.

## Readiness, requirement tiers, and the episode target (contract written before building, 2026-09-19)

### Requirement tiers (`target.json`)
Each model param in a target's `models` block gets a `tier`:

| tier | examples | if no matching file is installed |
|---|---|---|
| `required` | the diffusion model, text encoder, VAEs | the target is **not ready**: its shots are blocked before a take is reserved, naming the file and where to get it |
| `accelerator` | turbo / distilled LoRAs | the job falls back to the pass's **`base`** preset (`presets.<pass>.base`: `steps`, `cfg`, `sampler`, `loras: []`, … in the target's own terms). It is slower but works, and the take notes it. |
| `optional` | LTX duration head, voice ID-LoRA | only that feature is off (e.g. `dur: model` uses the estimate); the take notes it |

An unmarked param is `required`. A target may also declare `nodes` it needs beyond its
workflow's own classes.

### Resolving files by family
At queue time each model param resolves to an **installed** file:
1. the preset's exact file, if installed;
2. otherwise the best installed file of the same family (`targets/modelid.py`): a name match beats a fingerprint match; within a tie, prefer the same precision as the preset (fp8 / int8 / bf16 / fp16), then the shortest name.

The sidecar records `resolved: {param: {"want", "using", "how": "exact" | "family" | "base" | "off"}}`.
`h3render --dry-run --check-nodes` prints the same resolution.

### Downloads (`target.json` `downloads`)
`{"<file name>": {"folder": "diffusion_models", "url": "…" | null, "source": "…"}}`. A
URL is only given when it comes from a trustworthy record: a ComfyUI template's embedded
`properties.models` entry, or ComfyUI-Manager's model list. Otherwise `url` is null and
`source` says what to search for. Never guess a URL.

### `GET /h3pipe/targets?ready=1`
Each target gains `readiness` (cached briefly; computed off the event loop from
`/object_info` and the model folders):
```json
{"status": "ready" | "degraded" | "not_ready" | "unknown",
 "missing": [{"param": "loras", "tier": "accelerator", "want": "…safetensors",
              "family": "…", "folder": "loras", "url": "…" | null, "source": "…"}],
 "resolved": {"model": {"want": "…", "using": "…", "how": "exact" | "family"}},
 "features_off": ["dur: model (duration head)"],
 "nodes_missing": ["LTXVDurationPredictor"]}
```
- `degraded`: only accelerators or optional files are missing.
- `unknown`: ComfyUI didn't answer.

### `h3.py targets [<episode>] [--json]`
The same readiness as a table: status, what's missing, the folder, and the URL when known.

### The episode target
- **`overrides.json`** gains a top-level `"episode": {"target": "<id>"}`. It is the episode's default for every shot that has no target of its own (script `target:`, a profile's target, or a shot override). Precedence: request → shot override → shot/sequence script line or profile → **episode override** → `series.target` → `minimax_h3_ref2va`. Shots whose resolved target differs from what the build compiled are compiled at queue time, as a retarget is.
- **`PUT /h3pipe/episode-target`**, body `{ep, target: "<id>" | null}`. `null` clears it. Returns the episode's target info and emits `h3pipe.episode`.
- **`GET /h3pipe/episode`** gains:
  - `target`: the episode default now in force;
  - `target_source`: `"editor"` | `"series"` | `"default"`;
  - `series_target`;
  - per shot, `target` (already present) and `target_source`: `"request"` | `"override"` | `"script"` | `"episode"`.
- **`series.json` is never written by the editor.** The UI offers a copyable `"target": "<id>"` snippet for `series.series` to make the choice permanent.

### As built: where the build departs from the contract above

Everything above is implemented. Each point here is marked **[differs]** (the contract
said otherwise), **[added]** (the contract said nothing) or **[settled]** (the contract
left it open).

- **[added] `target.json`**: `models.<param>` may also carry `keep` (regexes whose captured
  word a substitute must share with the wanted file: a LoRA's step count, a Wan noise
  stage, LTX's `distilled` / `dev`), `exclude` (globs a substitute must not match: the H3
  SLA LoRAs), `default` (the file the workflow loads when nothing names one: H3 Ref2VA's
  text encoder and VAEs, krea2's) and `feature` (what an optional file switches on).
  `loras` is a models param (family + tier) on H3 Ref2VA / FL2VA, Wan 14B I2V and
  ingredients. `nodes` is `{class: {"tier", "feature"}}` or a list of required classes.
- **[settled] Tiers**: the accelerators are the H3 turbo LoRAs, the Wan I2V lightx2v pair
  and **the ingredients target's `model`** (the distilled LTX 2.3 checkpoint): its `base`
  names the dev checkpoint, which is then required. The LoRA in a pass's own `lora` slot
  (the preset's, or the series config's pass block's) counts as that target's
  accelerator even when it isn't named like the family (it then resolves exactly or falls
  back to the base). A LoRA from a profile or shot line that isn't installed is required.
- **[settled] Resolving by family**: a substitute must share the `keep` words and match no
  `exclude`; headers are read only when no name matches and the family has a header
  signature (not LoRAs); a header naming only a parent family (an H3 file named neither
  Ref2VA nor FL2VA) is not picked. A file listed in a subfolder of the models folder
  (`LTX-2.3\x.safetensors`) is the exact file. A folder ComfyUI can't list leaves its
  params unresolved, as before.
- **[added] Sidecar**: `resolved.loras` is `{"want": [names], "using": [names], "how",
  "tier"}` (lists); `resolved.<param>.tier`; `"base": true` on a take rendered with the
  base preset (whose `notes` say so). The base's LoRA loaders are removed from the graph
  (at strength 0 ComfyUI would still load the missing file). A `steps` value set for the
  shot (request or override) is kept over the base's, with a note. The frozen shotlist
  carries the base's values (`sampler`, `cfg`, ...) and any substitute file.
- **[settled] A missing required file** (`POST /h3pipe/render`): the shot is in `skipped`
  with `reason` ("model files not installed: text_encoder … is not installed
  (models/text_encoders; download https://…)"), `target`, and `missing_files`: `[{param,
  tier, want, family, folder, url, source}]`. No take is reserved. The CLI says the same
  (`h3render`: "blocked (model files not installed)"); a job's action is `missing_files`.
- **[added] `GET /h3pipe/targets`** (with or without `ready`): `models.<param>` adds `tier`
  (and `feature`), `presets.<pass>` adds `base`, and each target adds `downloads` (all
  static data from `target.json`). `ready` must be `1` / `0` (or `true` / `false`), else
  400; it combines with `kind`.
- **[added] `readiness`**: `missing[]` entries add `label` (the family's) and `passes`
  (which passes miss the file); an optional one always has `feature`, the same words as
  its `features_off` entry. `resolved` is the final pass's; `by_pass` has each pass's.
  `how` can also be `base` and `off`. An `unknown` readiness has `error`. An optional
  **node** missing (LTX's `LTXVDurationPredictor`) puts its feature in `features_off`
  and the class in `nodes_missing`, with no `missing[]` file entry. The node classes are
  the workflow's as a job keeps them (saver in place, widgets patched, pruned) plus the
  loader, the saver and `nodes`. The cache is per ComfyUI address; an unanswered
  `/object_info` isn't cached.
- **[added] `h3.py targets [<episode>] [--json] [--kind video|image] [--comfy URL]`**: exit
  1 when ComfyUI doesn't answer. Headers are read for substitutes only with
  `$COMFYUI_PATH` set. With an episode: its series config's pass blocks and
  `model_families` apply, and its episode target is marked.
- **[differs] `overrides.json` `episode`**: before, it was the episode's name (a string).
  Setting a target makes it `{"id": "<the name>", "target": "<id>"}`; clearing it puts the
  plain name back. A string `episode` means no episode target.
- **[settled] Pinning a shot to its built target**: `PUT /h3pipe/override` with
  `fields.target` equal to the built target clears the shot's retarget only while no
  episode target is set. With one, it is kept as the shot's own target
  (`target_source: "override"`); `null` clears it. The CLI's `--target built` does the
  same; `--target none` clears.
- **[differs] `target_source`** (per shot in `GET /h3pipe/episode`, and in `GET /h3pipe/shot`,
  which also has it now): besides request / override / script / episode, a shot on the
  series default is `"series"` (the series config names `series.target`) or `"default"`.
  "script" is judged from `shotlist/shots.json` and the series config's profiles (a
  `target:` line or a profile's target on the shot or its sequence); without a current
  shots.json, a built target other than the series config's counts as the script's.
- **[differs] `GET /h3pipe/episode` top-level `target`** is the episode default in force
  (the editor's, else `series.target`, else the default). It was `shotlist.json`'s target;
  the two are the same unless an episode target is set. `series_target` is `null` when the
  series config names none.
- **[settled] `PUT /h3pipe/episode-target`** returns `{target, target_source,
  series_target}` (the status fields). 400 for a target that isn't a video target, or a
  body without `target`; 404 for an episode with no build. CLI: `h3.py override <ep>
  --episode-target <id>`; `built` (or `none` / `series`) clears it.

## Phase 8.5: refs, image targets, keyframes as needed refs, negatives (contract written before building, 2026-09-19)

### Image targets
- `targets/image/<id>/`, alongside `krea2`, built from the user's saved workflows:
  - `z_image_turbo`: fast text-to-image;
  - `flux2_klein`: text-to-image, **and** `flux2_klein_edit`: image edit with reference images;
  - `flux_kontext`: edit, one reference;
  - optionally an Illustrious/SDXL text-to-image target.
- Each has presets, `models` tiers, downloads and readiness, like the video targets.
- `capabilities`: `{"mode": "t2i" | "edit", "max_refs": N}`.
- **Series defaults:** the series config's `refs` block, `{"target": "<image id>", "keyframe_target": "<image id>"}`.
  - The defaults are `krea2` and, when it is ready, `flux2_klein_edit` (else the refs `target`).
  - A per-ref override lives in `refs/_overrides.json` as `target`.
  - A generate request's `target` beats both.
- **`GET /h3pipe/targets?kind=image&ready=1`** lists them, as for video.

### Keyframes as needed refs
- **The build decides which shots need keyframes**, from each shot's resolved video target:
  - `capabilities.keyframes` says a target reads them;
  - `requires_first` (Wan 14B I2V) makes the first keyframe required.
- **Optional script lines** on a shot, or a sequence (as its default):
  - `first:` / `last:` with `continuity | generate | import | none`, or a path;
  - `none` means "don't use one", even when the target could.
- **`GET /h3pipe/refs`** lists keyframe refs for every shot that needs one, or whose script asks for one, even before any take exists. Each carries:
  - `need: "required" | "optional"`;
  - `method`: the script's, else `continuity` for a shot with a previous shot, else `generate`;
  - `shot`, `which`, and the `target` that will read it.
  
  The Refs tab's "this episode" filter includes them; "missing" includes only required ones plus any whose script asks.
- **`POST /h3pipe/refs/generate` on a keyframe ref** makes a still with the keyframe image target:
  - **Prompt:** from the shot's IR, with the look, the location description, the subjects' designs, and the action at that moment ("first": how the shot opens; "last": how it ends).
  - **Edit targets** also receive reference images: the picked character views (face or body by shot size) and the plate, up to `max_refs`.
  - **Size:** the shot's render size for its target and pass.
- **`method: continuity`** means "Generate missing" (and the CLI) call `keyframe_from_take` instead, when the previous shot has a usable take; otherwise it falls back to `generate`.
- **`DELETE /h3pipe/refs/pick?ep=…&ref=…[&view=…]` unpicks a ref:**
  - its live file is removed (the take stays);
  - for a keyframe this is **Clear**, and the shot no longer uses a keyframe;
  - for series refs it's allowed, but the UI warns.
  
  It emits `h3pipe.ref` with status `cleared`, then `h3pipe.episode`.

### Negatives
Any target (video or image) with a `negative` param takes, in order: the request's
`negative`, then the shot's `negative` override, then the episode's `negative.txt`, then
the series config's `negative`, then the target preset's.
- The take records `negative_source`.
- H3 targets have no negative param and are unaffected.
- `kreagen --negative-file` still beats everything for its run.
- The negative still has no effect at cfg ≤ 1 (turbo); the take notes that.

### Inspector: the refs a shot uses
**`GET /h3pipe/shot`** gains `refs_used`: `[{id, kind, role ("subject" | "plate" | "first" | "last" | "reference_sheet"), path, exists, need, thumb}]` for the shot's current target. A take's detail gains `reference_image`: the refsheet or VACE reference that the take rendered with, if kept.

### Small items
- **An estimated length** (`dur: model` before a take exists): episode shots carry `length_estimated: true`, and the UI marks them "≈".
- **Wan's `model_low`** gets a picker, like `model`: `GET /h3pipe/models?param=model_low`, and the override field `model_low`.

### Phase 8.5 as built

Everything above is implemented. Each point is marked **[differs]** (the contract said
otherwise), **[added]** (the contract said nothing; the UI agent's ten follow-up points are
here too) or **[settled]** (the contract left it open).

**Image targets**
- **[settled]** Built: `z_image_turbo` (saved `z-image-turbo-t2i.json`), `flux2_klein`
  (`image_flux2_text_to_image_9b.json`), `flux2_klein_edit`
  (`image_flux2_klein_9b_kv_image_edit.json`: its models are installed; the `_9b_base` edit
  needs `flux-2-klein-base-9b-fp8`, which isn't), `flux_kontext`
  (`flux_kontext_image_base.json`, trimmed to the branch its SaveImage reads). No
  Illustrious/SDXL target: the only SDXL workflow (`sdxl_simple_example.json`) is a
  base+refiner img2img canvas. Repo copies are API graphs with titled nodes, known by
  `h3pipe_*.json` names so a canvas can't leak in; the saved canvases' LoRA loaders are left
  out. Each passes `check_graph` against `tests/fixtures/workflows/object_info_image.json`
  (trimmed from the live `/object_info`).
- **[added]** `GET /h3pipe/targets` image entries: `capabilities` `{mode, max_refs,
  negative_prompt}` (krea2, Z-Image, Klein `t2i`/0; Klein edit `edit`/4; Kontext `edit`/1).
  Video entries add `capabilities.requires_first` (true only for `wan22_i2v`).
- **[settled]** Klein and Klein edit: the model is an **accelerator** (the installed
  distilled KV model, 4 steps, cfg 1); `base` is the base 9B at 20 steps, cfg 5 (the saved
  workflows' own). Z-Image and Kontext: all required. Families from the installed files'
  headers: `z-image` (Turbo by name), `flux2-klein-9b`, `flux1` (`-dev` / `-schnell` by
  tensors; `flux1-kontext-dev` by name under dev), `qwen3-4b`, `qwen3-8b`, `qwen3vl-4b`,
  `clip-l`, `t5-xxl`, `flux1-vae`, `flux2-vae`. Files resolve by family at queue time
  (`h3refs.resolve_job_models`, the video code); a missing required file is an `errors[]`
  entry with `missing_files`.
- **[added]** Reference images reach an edit graph through a chain titled "Reference 1 ..."
  that graph code repeats per image (or cuts out with none); each is uploaded as
  `h3pipe/<sha1>.png`. A character with no picked view gets that panel cut out of its live
  sheet (`h3_refsheet.py`, PIL).
- **[added] Image defaults.** `GET /h3pipe/refs` returns `{"refs": [...], "defaults":
  {"target", "target_source", "keyframe_target", "keyframe_target_source"}}`, sources
  `"editor"` | `"series"` | `"default"`. **`PUT /h3pipe/refs/defaults`** `{ep, target?,
  keyframe_target?}` (null clears; a key left out is kept) stores `overrides.json`
  `episode.refs_target` / `episode.keyframe_target` beside the video `episode.target` and
  returns `{"defaults": ...}`; 400 for a non-image target or a body with neither key.
  Precedence: request, then the ref's override (`target` in `refs/_overrides.json`, now a
  `PUT /h3pipe/refs/override` field), then the episode's, then the series config's `refs`,
  then built-in. "Ready" for the built-in keyframe default means every required file of
  `flux2_klein_edit` is among ComfyUI's model lists.
- **[added]** Each ref's `effective.target` is the image target a generate would use (a
  character's top-level `effective` is `{target}`; each view's has the rest). Ref-take
  sidecars add `target`, `negative`, `negative_source`, `values`, `resolved`, `notes`, and
  for a keyframe `references` (`[{role, subject|location, name, kind, view?, crop?, path,
  sha1}]`), `inputs`, `render_width`, `render_height`, `video_target`.
- **[added]** `POST /h3pipe/refs/generate` takes `target`, `negative` and `pass` (a
  keyframe's size: default `final`, the larger; the file serves both passes). `queued[]`
  entries carry `target`.

**Keyframes as needed refs**
- **[settled]** Script lines `first:` / `last:` on a shot or a `#` header; IR `first` /
  `last` on shots and sequences, omitted when unset. `none` removes an optional keyframe
  from the built entry's `keyframes` (a required one stays: the shot stays blocked).
- **[settled]** `method` defaults to `continuity` only for a **first** frame whose shot has a
  previous shot **in the same sequence** (across sequences the location changes); else
  `generate`. A path in the script is `method: "import"` with `import_path`.
- **[added]** Keyframe refs carry `shot`, `which`, `need`, `method`, `target` (the video
  target), `requested` (the script asks for one), `script` (the raw line), `reads` (whether
  the target reads that end), `import_path`, `cleared`, and with an edit keyframe target
  `edit_refs: [{id, role, view?, crop?, path}]` (exactly what a generate feeds, after
  `max_refs`). Needed keyframes list before any take exists; ones that only exist (a take,
  a live file) list as before, with `need: null`.
- **[differs] Size:** the shot's render size for its target and pass, **scaled up** (same
  aspect, multiples of 16) to the image target's `template.min_pixels` (786432 for the new
  targets; krea2 has none, so exact): a 640x352 proxy frame is generated at 1200x656. Every
  video target scales a keyframe to its frame. The sidecar records both sizes and a note.
- **[settled] Prompt** (`targets/image/common.py keyframe_prompt`): an edit target's
  references named first ("Image 1 is Bolt: draw this character exactly as in image 1 ...",
  "Image 2 is the background plate: ..."); then "The first/last frame of a <size> of
  <location description>, one still picture. Drawn as <look>." and each subject's design;
  then the moment: first = the action's first sentence "at the very start of the action"
  (the rest in parentheses, "not yet shown"); last = its last sentence "after the action is
  over, ... completed". Speakers are "about to speak" / "have just spoken"; no dialogue
  text; "No text, captions, speech bubbles or borders."
- **[added] Reference choice:** characters (script order), then props and vehicles, then the
  plate, each only when its file exists; a character gives `04_face` on a single-subject
  `close`/`cu` shot, else `01_threequarter`: its picked take of that view, else that panel
  of its live sheet.
- **[settled] "Generate missing"** in the CLI: `h3.py keyframe <ep> --missing [--proxy]
  [--dry-run] [--target T]` fills required keyframes and any the script asks for, not live
  and not cleared: continuity (`keyframe_from_take`) when the previous shot has a usable take
  in the pass's cut, else a still; a script path is imported and picked. `h3.py keyframe <ep>
  <shot> --generate [--first|--last] [--target T] [--pick|--no-pick] [--dry-run]` makes one
  still. No new route: the UI calls `/refs/keyframe` and falls back to `/refs/generate` on
  409 / 400.

**Clear**
- **[added]** `DELETE /h3pipe/refs/pick` returns the ref as `/h3pipe/refs` lists it;
  `h3pipe.ref` carries the take that was picked (null if none). 400 for a voice.
  `_picks.json` records `"cleared": "<time>"` on the ref (on a character's view,
  `views.<view> = {"cleared"}`, and the stitched sheet is removed); auto-pick (`GET
  /h3pipe/refs`, and `keyframe_from_take` with `pick: null`) skips a cleared ref until
  anything is picked. Every ref and view in the listing carries `cleared`.
- **[added] CLI:** `h3.py keyframe <ep> <shot> --clear [--first|--last]`; `kreagen --clear
  REF[:VIEW]` (repeatable) for series refs.

**Negatives**
- **[differs] `negative_source` values:** `"request"` | `"override"` | `"negative.txt"` |
  `"series"` | `"preset"` | `"none"` (a target without a negative param: H3, Klein edit).
  `kreagen --negative-file` records `"request"`. The series config's is a top-level
  `"negative": "..."`.
- **[added]** The shot override field `negative` is per pass (like `prompt`; `""` is an
  explicit empty negative, null clears); `POST /h3pipe/render` takes `negative` for one run.
  Video sidecars on a negative-taking target record `negative_source`; a non-preset negative
  lands in the frozen shotlist and, at cfg <= 1, adds a note. Wan's "Negative prompt"
  `CLIPTextEncode` feeds `WanImageToVideo.negative` (checked).
- **[added]** `GET /h3pipe/shot` `effective` adds `negative` (null for `none`) and
  `negative_source`, and `model_low` on a two-stage target.

**Inspector and small items**
- **[settled] `refs_used`** entries also carry `slot`; `role` can also be `"voice"` (a clone
  sample) or `"recording"` (a dub track); `id` is null for a recording or a plate the series
  config doesn't name. `path` and `thumb` are relative to the episode (`../` beside a
  parent-folder series config), `thumb` null when the file isn't on disk. The
  `reference_sheet` entry (ltx2_ingredients, wan22_vace) points at the latest take's kept
  sheet or reference, else null.
- **[added]** A take's `reference_image` is relative to the episode, null when not kept.
- **[settled]** `length_estimated: true` appears only on estimated shots.
- **[added]** `model_low` is a per-pass override field; `GET /h3pipe/models?target=...&param=
  model_low` works for `wan22_i2v` and `wan22_vace` (`target` is required, as for `model`).

## Phase 8.6: look-back (contract written before building, 2026-09-19)

Loose ends from Phases 1-8, finished before Phase 9. Backend and UI build against this in
parallel; the backend writes a "Phase 8.6 as built" section with [differs]/[added]/[settled].

### Discarding takes and ref candidates
Nothing is deleted: a discarded file moves to a `_trash/` folder beside it, keeping its
relative name, so a mistake can be undone by hand. Discarding is refused (409) for a take
that is `queued` (cancel it first).
- **`POST /h3pipe/discard`** `{ep, shot, take, pass}`: moves the take's sidecar and every
  output it names (video, audio, reference image, thumbnails) from
  `renders[_proxy]/<shot>/` to `renders[_proxy]/_trash/<shot>/`. If `cut.json` picks that
  take for that pass, the pick is removed (the cut falls back to the latest usable take).
  Returns `{"shot", "take", "moved": [paths relative to the episode], "cut_changed": bool}`
  and emits `h3pipe.episode`.
- **`POST /h3pipe/refs/discard`** `{ep, ref, view?, take}`: moves the candidate (sidecar and
  image/audio) from `refs/_takes/...` to `refs/_takes/_trash/...`. If it is the ref's (or
  view's) pick, the ref is cleared exactly as `DELETE /h3pipe/refs/pick` does. Returns the
  ref as `GET /h3pipe/refs` lists it and emits `h3pipe.ref`.
- `GET /h3pipe/episode` and `GET /h3pipe/refs` never list anything under `_trash/`.
- **CLI:** `h3.py discard <ep> <shot> <take> [--proxy]`; `kreagen --discard REF[:VIEW]:TAKE`.

### Generate missing, one route
- **`POST /h3pipe/refs/generate-missing`** `{ep, pass?, kinds?: ["series", "keyframe"],
  target?, keyframe_target?, dry_run?: false}`: queues one candidate for every series ref
  that is missing (no live file, not cleared, no queued candidate), and fills every needed
  keyframe as `h3.py keyframe --missing` does (continuity when the previous shot has a usable
  take in the pass's cut, else a still; a script path is imported and picked). Returns
  `{"queued": [{ref, view, take, prompt_id, seed, target, method}], "picked": [{ref, take,
  method}], "skipped": [{ref, reason}], "errors": [...]}`. `dry_run` returns the same
  shape with nothing queued (take/prompt_id null). The UI's "Generate missing" button uses
  this instead of its two-step fallback.

### Uploading a ref (drag and drop)
- **`POST /h3pipe/refs/import`** also accepts `multipart/form-data` with fields `ep`, `ref`,
  `view` (optional), `pick` (optional, `"1"` picks it) and `file` (an image, or audio for a
  voice). Same result as the JSON form: a new take with `source: "imported"`, plus
  `original_name`. 400 for a file type the ref can't use; 413 over 64 MB. The JSON form
  also takes `pick`.

### Keyframe polish
- The keyframe prompt states the framing more strongly (a close-up must fill the frame with
  the face; the size word is repeated in the "Drawn as" sentence and as a closing line).
- A single-reference edit target (`max_refs: 1`, Kontext) gets one **composed reference**:
  the characters' reference panels pasted over the plate (side by side, bottom-aligned,
  about two thirds of the frame height), made by `h3_refsheet.py` and uploaded like any
  reference; the sidecar's `references` records `role: "composite"` with its parts.

### Phase 8.6 as built

Everything above is implemented. As for 8.5, each point is **[differs]** (the contract
said otherwise), **[added]** (the contract said nothing) or **[settled]** (the contract
left it open). Nothing here changes a field the contract named.

**Discarding**
- **[settled]** `POST /h3pipe/discard`: `pass` defaults to `proxy`, as on every route; the
  response also carries `pass`. `moved` lists the files' **new** paths (under `_trash/`),
  relative to the episode. What moves: the sidecar, every file sharing the take's stem
  (`.mp4`, `.jpg`, `_strip.jpg`, `.shotlist.json`, `_h3.wav`, a kept `_reference.png` or
  sheet), and any other file the sidecar names in the shot folder. 404 for no such take,
  409 for a queued one ("cancel it first").
- **[settled] The cut:** every `cut.json` entry, in either pass's list, whose take comes
  from the discarded pass and names that take loses its `take`. A placeholder keeps its
  `pass`, so it falls back to the other pass's latest usable take. `cut_changed` says
  whether anything was dropped.
- **[added] Numbers aren't reused.** The next take of that shot (or ref, or view) skips
  every number still in the trash, so `t03` is never two different takes. If a name is
  already in the trash anyway (an explicit `--take N` re-render discarded twice), the
  whole set goes into `_trash/<shot>/<YYYYmmdd-HHMMSS>/`. Nothing is overwritten.
- **[added] `POST /h3pipe/refs/discard`** needs `view` for a character (else 400), and is
  404 for no such take and 409 for a queued one. `h3pipe.ref` carries status
  **`discarded`**, then `cleared` if it was the pick, then `h3pipe.episode`. A voice's take
  is only moved: voices aren't cleared, so a picked voice keeps its live file.
- **[settled] `_trash/` is never listed.** Video takes are listed per shot folder, so
  `renders/_trash/` is never read. Ref takes live under `refs/_takes/<key>/`, and the
  sweep of queued ref takes skips `refs/_takes/_trash/`.
- **[added] CLI:** `h3.py discard <ep> <shot> <take> [--proxy]` accepts `3` or `t03`.
  `kreagen --discard REF[:VIEW]:TAKE` is repeatable and accepts `location:kitchen:3`,
  `subject:ada:02_side:t2` or `shot:sh020:first:1`. `kreagen --clear` and `--discard` no
  longer need `refs_todo.json`.

**Generate missing**
- **[settled] Which series refs count as missing:** those this episode uses in `pass`
  (`used_by`, which is what the Refs tab's plan used), with no live file and not cleared.
  A ref is skipped if any of its candidates is queued, or has finished and waits to be
  picked (auto-pick takes it on the next `GET /h3pipe/refs`). This is the UI's
  `inFlight` rule. A character missing all four views gets one generate with
  `view: null` (the four share a seed). Otherwise each missing view is queued alone, and
  a skip for one view carries `view`. A ref that can't be generated (a voice) is in
  `skipped` with the reason.
- **[settled] `pass`** defaults to `proxy`, like `/refs/keyframe`. It sets both the cut
  that continuity reads and the render size a keyframe still is made for, as
  `h3.py keyframe --missing` does. `kinds` defaults to both kinds.
- **[settled] Keyframes:** a keyframe is filled only if it is required or asked for by the
  script, and it must have no live file and not be cleared (`missing_keyframes`, shared
  with the CLI). It is skipped if a candidate is queued or waiting.
  - **Continuity:** the frame is cut, and the result goes into `picked` (it is always
    picked, since the keyframe had no live file). If the frame can't be cut (no usable
    take, or a bad source), a still is queued instead. If ffmpeg is missing, the keyframe
    goes to `errors`.
  - **Still:** it goes into `queued` with `method: "generate"`, and `target` is the image
    target.
  - **Script path:** the file is imported and picked (`method: "import"`).

  The CLI and the route share `h3refs.keyframe_plan`, `import_keyframe` and
  `missing_keyframes`. The CLI waits for each still; the route doesn't.
- **[added]** `queued[]` entries also carry `seed_source`. In a dry run, `seed` is null
  when a new random seed would be drawn, and continuity and import entries appear in
  `picked` with `take: null`. `errors[]` are `queue_generate`'s, so they include
  `missing_files` when a model isn't installed.
- **[added] Events:** `h3pipe.ref` with status `queued` for each queued candidate, then
  `ok` and `picked` for each picked one, then `h3pipe.episode`. A dry run emits nothing.
- **[added] Errors:** 404 when `pass` has no build. 400 for a bad `kinds`, a `target` /
  `keyframe_target` that isn't an image target, or a `dry_run` that isn't a boolean.

**Upload**
- **[settled]** The aiohttp adapter streams the `file` part to a temporary file, never
  holding it in memory, and deletes it after the handler. The handler takes it through
  the same `import_take` as the JSON form. **413** comes either from a `Content-Length`
  over 65 MB (answered before anything is read), or from passing 64 MB while streaming.
  **400** covers an extension the ref can't use (the type is judged by the upload's file
  name), a `file` field that isn't a file, and a body that is neither JSON nor multipart.
- **[added]** Ref-take sidecars and take entries carry `original_name` only for uploads;
  an upload's sidecar has `source_path: null`. The form also takes `note`.
- **[settled] `pick`:** a form sends `"1"` / `"true"` / `"yes"` / `"on"` or `"0"` /
  `"false"` / `"no"` / `"off"` / `""`; JSON sends `true`, `false` or null. When picked,
  the events are `ok` then `picked`, and the returned take is re-read after the pick. A
  stitch failure is 500, as on `PUT /h3pipe/refs/pick`.

**Keyframe polish**
- **[settled] The wording** (`targets/image/common.py`): after "The first/last frame of a
  <size> of <place>, one still picture." comes a framing sentence:
  - **close-up:** "Framing: a close-up. Ada's face fills the frame, large: from the chin to
    the top of the head, cut off at the shoulders. No full body, no wide view of the
    room." With several characters it reads "Ada and Bo's faces fill"; with only props,
    they fill the frame; with nothing, one detail of the place;
  - **medium shot:** "... seen from the waist up, filling most of the frame's height" (no
    sentence without characters);
  - **wide shot:** "The whole place in view, ... small and full-length in it."

  "Drawn as <look>, framed as a close-up." and a closing "Framing: a close-up." repeat the
  size. The golden outputs don't contain keyframe prompts, so they are unchanged.
- **[settled] When a composite is made:** for a target whose `max_refs` is 1, when more
  than one reference image is on disk.
  - **Parts:** the figures (characters, then props and vehicles, at most 4), then the
    plate. Without a plate, the figures go on white. With one image only, that image is
    sent as it is.
  - **Layout:** the composite is the keyframe's generation size. The plate covers the
    frame. The figures sit side by side, centred, standing on the bottom edge, two thirds
    of the frame tall (scaled down together if they'd be wider than the frame less 3%
    each side), 2% of the width apart. Each is pasted as a rectangle, background included.
- **[added]** The composite is kept at `refs/_takes/<key>/_composites/<sha1>.png`.
  - **Sidecar:** it records `{role: "composite", kind, name, path, sha1, parts: [{role,
    subject | location, name, kind, view?, crop?, path, sha1}]}`.
  - **Listing:** a keyframe's `edit_refs` shows `{id: null, role: "composite", path:
    null, name, parts: [...]}`. There is no file until a generate composes one.
  - **Prompt:** "The image shows Ada, Bo, and the diner kettle in front of the background
    (kitchen): draw each of them exactly as in the image (the same face, body, clothes
    and colours), and use its background's setting, layout, colours and light for the
    scene. The image is only a reference collage: pose and frame each of them for this
    shot."
- **[added] Without PIL:** composing runs `comfy_nodes/h3_refsheet.py` as a subprocess of
  the running Python, as the ingredients sheet does. Under ComfyUI (the routes) that
  Python has PIL, and so does a CLI whose Python has it.
  - **Fallback:** when there is no PIL, the job sends one part alone. That is the first
    figure not cut from a sheet, else the plate, else the first figure.
  - **The prompt** is rewritten for that one image, unless it was typed or overridden.
  - **The sidecar's `notes`** say so: "couldn't compose one reference from N (...); sent
    Ada alone".

## Phase 9a: script and series config windows, promote (contract written before building, 2026-09-19)

The editor can now show and edit the episode's two authored files and move overrides into
them. This replaces the old rule "the editor never writes series.json": it writes it only
through these routes, only on an explicit save or promote, and always keeps the previous
version. Backend and UI build against this in parallel; the backend writes "Phase 9a as
built" with [differs]/[added]/[settled].

### Reading and checking
- **`GET /h3pipe/source?ep=…&file=script|series`** → `{"file", "path"` (relative to the
  episode; `../series.json` for a parent-folder series config), `"text", "hash"` (sha1 of the
  bytes), `"mtime", "shots": [{"id", "line", "end_line"}]` (script only; from the parser's
  source spans, empty if it doesn't parse)`}`. `&hash_only=1` returns just `{file, hash,
  mtime}` (the UI polls this on focus to notice edits made outside ComfyUI).
- **`POST /h3pipe/source/check`** `{ep, file, text}`: parses and checks **without writing**.
  A script is checked against the series config on disk; a series config is checked on its
  own (valid JSON, loads) and then with the script on disk. Returns `{"ok", "errors":
  [{"file", "line", "col"?, "message"}], "warnings": [{...same}], "shots": [...]}` — the
  same messages `h3.py check` prints, with 1-based line numbers into the given text. A JSON
  syntax error carries its line/col.

### Saving
- **`PUT /h3pipe/source`** `{ep, file, text, base_hash, rebuild: true}`:
  - 409 `{"error": "changed on disk", "hash", "text"}` if the file's hash isn't `base_hash`
    (edited outside meanwhile). The UI offers reload or overwrite (overwrite = resend with
    the new `base_hash`).
  - A series config that isn't valid JSON is refused (400 with line/col). A script with
    errors **is** saved (it's the user's file), and the check result comes back.
  - Before writing, the old file is copied to `<ep>/_history/<name>.<YYYYmmdd-HHMMSS>`
    (the newest 30 per file are kept). The write is atomic (temp file + replace), keeping
    the file's existing line endings and encoding (UTF-8, BOM kept if present).
  - `rebuild: true` runs the same build as `POST /h3pipe/build` after a successful write.
  - Returns `{"hash", "check": {...as above}, "build": {...as /build} | null}` and emits
    `h3pipe.episode`.

### Promote
Moves overrides that the authored files can express into them, then drops those overrides.
What can't be expressed stays in `overrides.json`, with the reason.
- **`GET /h3pipe/promote?ep=…[&shot=…]`** → a plan:
  `{"items": [{"id", "scope": "shot" | "episode" | "ref", "shot"?, "ref"?, "view"?,
  "field", "value", "dest": "script" | "series", "line"? , "summary"}], "left": [{"scope",
  "shot"?, "ref"?, "field", "reason"}], "diffs": {"script": "<unified diff>", "series":
  "<unified diff>"}, "hashes": {"script", "series"}}`.
- **`POST /h3pipe/promote`** `{ep, items: [ids] | "all", hashes: {script, series}}`: 409 as
  for `PUT /h3pipe/source` if either file changed since the plan. Writes the files (with
  `_history/` copies), removes exactly the promoted override fields, rebuilds, and returns
  `{"promoted": [ids], "left": [...], "hashes", "build"}`; emits `h3pipe.episode` (and
  `h3pipe.ref` for promoted ref overrides).
- **What maps where** (the backend confirms each against the parser and the series config
  loader, and lists anything else under `left`):
  - Shot `target` → a `target:` line in that shot's block.
  - Shot `model` / `loras` / `steps` → `model:` / `lora:` / `steps:` lines, only when the
    script line would mean the same thing (e.g. the value is the same in both passes, or the
    script line only affects the pass the override is for); else `left` with the reason.
  - Shot `prompt` (compiled text), `seed`, `negative`, `note` → `left` (compiled text has no
    script form; a seed is kept by picking the take; a negative is per target/pass).
  - Episode `target` → the series config's series-wide target; `refs_target` /
    `keyframe_target` → its `refs` block.
  - Ref overrides → the matching subject/location/view fields of the series config where
    the loader has one (design sentences, per-view prompts, `target`); else `left`.
- Script edits are line-level: an existing `key:` line in the shot's own block (not the
  sequence header's) is replaced, else a new line is inserted after the `key:` lines that
  open the shot's block (or right after `## shot`), never below its dialogue. Nothing else in the file changes. Series config
  edits rewrite the JSON with 2-space indent, keeping key order and non-ASCII text; if the
  file wasn't already formatted that way the diff says so.

### Phase 9a as built

Everything above is implemented (`h3source.py`: read, check, save; `h3promote.py`: the
plan and apply, and `python h3.py promote`). Each point is **[differs]**, **[added]** or
**[settled]**, as for 8.6. No field the contract named changes shape except where marked
[differs].

**Reading**
- **[settled]** `text` always has LF line endings and no BOM; `hash` is the sha1 of the
  bytes on disk (BOM and CRLF included), so it is what `base_hash` must be. `mtime` is
  seconds since the epoch (a float, `os.stat`). A file that isn't UTF-8 is a 500 naming
  the byte.
- **[settled]** `shots` comes from the parser against the series config on disk; it is
  `[]` when either doesn't parse. `path` is `../series.json` for a parent-folder series
  config, as the contract said. An unknown `file` is 400; no script (or no way to tell
  which `.md` it is) or no series config is 404.

**Checking**
- **[settled]** `errors` / `warnings` entries are `{"file": "script" | "series", "line":
  <1-based> | null, "message"}`, plus `"col"` for a JSON syntax error only. A parser error
  gives its own line, and `message` is its text **without** the `line N:` prefix and the
  echoed `| line` (the line is in `line`). A build error or warning about a shot
  (`shot sh020: …`, `sh020: …`, `sh040 overrides steps: 10`) points at the line the
  message is about when it names one in that shot's block (`target:`, `profile:`, `dur:`,
  `retention:`, `policy:`, `plate:`, `with:`, `model:`, `lora:`, `steps:`), else at the
  shot's `##` line; `sequence sq04: …` at the `#` line. A series config message points at
  the key it names (`profile 'x'`, `refs.x`, `series.x`, `proxy.x`, a missing block's
  name) and is `line: null` when it names none (e.g. "series.json has no `subjects`
  block").
- **[settled]** The check is `h3.py check`'s: parse, pick targets, compile the **final**
  pass of every target the episode uses (`h3build.compile_groups`, which the build now
  uses too), in memory. Warnings are the report's `!` lines, each once. The build stops at
  its first error, so `errors` has at most one entry. `--pace` isn't repeated: its
  CRAMMED/tight verdicts are already warnings.
- **[settled]** Checking a series config: JSON, then the loader; if it loads, the script
  on disk is checked with it, and those messages carry `"file": "script"` and lines into
  the script on disk. `shots` is `[]` for `file: series`.

**Saving**
- **[settled] Line endings:** the file keeps the ending most of its lines use (CRLF or LF;
  a mixed file comes out uniform) and its BOM if it had one. A BOM at the start of the sent
  text is dropped (the file's own rule decides).
- **[settled]** Text that would write the same bytes writes nothing (no history copy, the
  hash unchanged); `check` still comes back, and `build` too when `rebuild` is true.
- **[settled] History:** `<ep>/_history/<file name>.<YYYYmmdd-HHMMSS>`, with `-2`, `-3`…
  when a copy that second exists; the newest 30 per file name are kept. A parent-folder
  series config's copies also go in the **episode's** `_history/` (the episode finder
  skips `_` folders).
- **[settled]** The JSON refusal is 400 `{"error", "line", "col"}` and writes nothing. A
  series config that is JSON but doesn't load is saved, with the check's errors.
- **[added]** The 409 body also has `"file"` (which file changed). `rebuild` defaults to
  true; `base_hash` is required (400 without it).
- **[settled]** The write is a temp file beside the file, then `os.replace` (retried
  briefly while Windows reports the file busy).

**Promote: the plan**
- **[settled] Item ids:** `shot:<shot>:<field>` (`target`, `model`, `loras`, `steps`),
  `episode:<field>` (`target`, `refs_target`, `keyframe_target`) and
  `ref:<ref id>:prompt`. `value` is the override's value (`loras` is the list); `summary`
  shows the line or key as it will be written, e.g. ``sh030: `lora: x.safetensors:0.5` (the
  override of both passes)``. `line` (script items) is the line **in the promoted script**
  (every item of the plan applied).
- **[differs] A character's prompt is one item, not one per view:** the design sentence
  is every view's, so it promotes only when all four views have a prompt override and all
  four change the design the same way; the item has no `view`. (Props, vehicles and
  locations have one prompt and one item.)
- **[added]** `left` entries may carry `"pass"` (a per-pass field left in one pass: a
  prompt, a negative, `model_low`) and `"ref"` / `"view"` for ref overrides.
- **[settled]** `diffs.script` / `diffs.series` are `""` when that file doesn't change.
  When the series config wasn't already in the promote's format (2-space indent, key order
  and non-ASCII kept; e.g. the fixture's one-line arrays), `diffs.series` starts with one
  line, `# series.json wasn't formatted with a 2-space indent: promoting rewrites the whole
  file that way`, before the `---`/`+++` header.
- **[settled] `&shot=`** narrows the plan to that shot's overrides: no episode or ref items.
- **[settled]** A script or series config that doesn't parse, load or build is 400 (fix it
  first): a plan needs both.

**Promote: what maps where (the final rules)**
- Shot **`target`** (the retarget) → `target: <id>` in the shot's block.
- Shot **`model` / `loras` / `steps`**, from the override block of the target the shot
  renders on now → `model:` / `lora:` / `steps:`. A script line sets **both passes** (the
  compilers layer it above the pass preset for final and proxy alike), so the item needs
  the same value in both passes: both overridden alike, or one pass overridden and the
  other already rendering that value. `loras` → `lora: name` (strength 1), `lora:
  name:0.6`, or `lora: none` for `[]`; a stack of two or more is `left` (a script line
  holds one LoRA: use a profile's `loras`).
- **left, always, for shots:** `prompt` (compiled text), `seed` (keep it by picking the
  take), `note`, `negative` (per target and pass), `model_low` (no script line); and every
  field of an override block written for a target the shot doesn't render on now (a script
  line would give it to the other model).
- Episode **`target`** → the series config's `series.target`, **unless** its `series` /
  `proxy` blocks set `model`, `lora` or `steps` for the old series target: as the series
  target, the new one would inherit them (`Target.preset` applies the pass blocks to the
  series target only), so it is `left`, naming the keys.
- Episode **`refs_target` / `keyframe_target`** → `refs.target` / `refs.keyframe_target`.
- A ref's **`prompt`** → the subject's `design` or the location's `description`, when the
  override is the built prompt with only that sentence replaced (it occurs once in the
  built prompt, and the override keeps everything around it); a character needs all four
  views, agreeing (above). The summary lists the shots that show it: their compiled
  prompts use the same sentence and change too.
- **left, always, for refs:** `seed`, `model`, `loras`, `steps`, `note`, `target` (the
  series config has no per-ref settings; `refs.target` is every ref's), a keyframe's
  prompt (written from its shot) and a voice's.
- **The check (every plan):** the promoted files are parsed and compiled in memory, and
  every shot must render with the same target, model, LoRAs and steps in both passes as it
  does now (h3jobs' precedence: the shot compiled for its effective target, then the
  override for that target), and each promoted line must parse back as written. Each item
  is checked alone, then all together; one that fails goes to `left` with what would
  change (`sh010 would render differently (final: steps 9 → 8)`). A ref item is checked by
  generating its prompt from the changed series config with no override: it must equal the
  override's text, in every view. `tests/test_phase9a.py` asserts the end-to-end version
  through `h3jobs.plan_job` on disk, before and after a promote.

**Promote: apply**
- **[settled]** `items` must be ids the plan (for the same `shot`, if given) offers; an
  unknown id is 400 ("ask for the plan again"). The chosen subset is checked again on its
  own; anything that no longer passes comes back in `left`, unpromoted.
- **[added]** An optional `shot` in the body narrows the plan as `&shot=` does.
- **[settled]** 409 is `{"error": "changed on disk", "file", "hash", "text"}` for the first
  file (script, then series) whose hash isn't the plan's; nothing is written. `hashes` is
  required (400 without it).
- **[settled] Order:** the script, then the series config (each only if it changes, each
  with a `_history/` copy), then `overrides.json` (the promoted fields in both passes of
  that target's block, the retarget, the episode fields), then `<series
  home>/refs/_overrides.json` (the promoted prompts and their `base_hash`), then the build.
  `hashes` in the answer are the files' new hashes; `build` is null when nothing was
  promoted.
- **[added]** `h3pipe.ref` for a promoted ref override has `"status": "promoted"`,
  `view: null`, `take: null`; `h3pipe.episode` follows any promote.
- **[settled] Known side effects:** a take rendered with a promoted model/LoRA/steps
  override shows `preset` stale afterwards (a take's `preset_hash` is of the built values,
  which now include the promoted ones), although a render uses the same values. A promoted
  design changes the compiled prompts of the shots that show that subject or location
  (listed in the summary), so their takes go `script` stale. An episode item whose series
  config is shared by a parent folder says so in its summary: every episode that uses it
  changes.

**CLI**
- **[added]** `python h3.py promote <ep> [<shot>]` prints the plan (items, `left` with
  reasons, both diffs) and writes nothing; `--all` or `--item ID` (repeatable) promotes and
  rebuilds; `--dry-run` shows the plan even with those.

## Phase 9b: timeline editing (contract written before building, 2026-09-19)

`cut.json` already holds order, trims and locks, `PUT /h3pipe/cut` writes them, and assemble
and Play all honour them; the editor just never wrote them. 9b adds the editing, the
waveforms, and play-through polish. Backend and UI build in parallel; the backend writes
"Phase 9b as built".

### Cut edits (backend)
- **`PUT /h3pipe/cut`** (as before) also: 400 for a negative or non-integer trim, and for
  trims that leave less than one frame of a take whose frame count is known; the previous
  `cut.json` goes to `<ep>/_history/cut.json.<stamp>` (newest 30), as source saves do. Emits
  `h3pipe.episode`.
- **`POST /h3pipe/cut/reset`** `{ep, pass, what: "order" | "trims" | "all"}`: script order
  (picks, locks and notes kept) and/or zero trims. Returns `{"cut"}`.
- **`POST /h3pipe/cut/copy`** `{ep, from, to, what: "order" | "trims" | "all"}`: copies one
  pass's order/trims onto the other (trims converted by frame rate when the passes' fps
  differ; picks never copied). Returns `{"cut"}`.
- **`GET /h3pipe/episode`**: each shot's `cut` also carries `order` (its index in the pass's
  cut), `script_index`, and `out_of_order` (true when it's not where script order would put
  it); top level adds `track`: `{"path", "duration", "rate"}` or null (the series config's
  recorded dialogue, `audio.track`), and each shot with a dialogue window carries
  `audio_in` / `audio_out` (seconds on that track) if it doesn't already.
- **`locked`** entries: `PUT /h3pipe/pick` answers 409 for a locked shot unless `force`;
  the UI also refuses to move or trim them.
- **CLI:** `h3.py cut <ep> [--proxy] (--show | --order sh010,sh030,... | --move SH (--before|
  --after) SH | --trim SH IN OUT | --lock SH | --unlock SH | --reset order|trims|all |
  --copy-from final|proxy [order|trims|all])`.

### Waveforms (backend)
- **`GET /h3pipe/peaks?ep=…&path=…&bins=N[&start=S&end=E]`**: `path` is a media file relative
  to the episode (a take's mp4 or wav, or the `track`). Returns `{"duration", "bins",
  "peaks": [0..255, ...]}` — max absolute amplitude per bin, mono, over `start..end` seconds
  (default the whole file); `peaks: []` and `"silent": true` for a file with no audio
  stream. Computed once per file at 200 bins/second (stdlib `wave` for PCM wav, else
  `ffmpeg -ac 1 -ar 8000 -f s16le`), cached in `<ep>/_cache/peaks/<sha1 of path+size+
  mtime>.json`, then resampled to `bins`. 404 for a missing file, 400 for a path outside
  the episode (and its parent-folder series config's folder).
- **Takes** in `GET /h3pipe/episode` carry `audio`: the file whose sound the cut plays for
  that take (the mp4 if it has an audio stream, else its `_h3.wav`, else null) — the same
  rule as assemble's `--audio auto`.

### Timeline (UI)
- **Reorder:** drag a clip between clips (drop marker; Esc cancels); Alt+←/→ moves the
  selected clip. Clips can move across sequences; an out-of-order clip gets a badge. Menu:
  "Reset order", "Clear trims", "Copy order/trims from the other pass".
- **Trims:** drag a clip's left/right edge (frame-snapped; tooltip shows frames and seconds;
  at least one frame stays); I / O set trim-in / trim-out at the playhead while Play all is
  on that clip; numeric trims in the Inspector's cut section. The thumbnail strip shows the
  trimmed part dimmed.
- **Undo/redo** (Ctrl+Z / Ctrl+Shift+Z) of cut edits for the session; each edit is one
  `PUT /h3pipe/cut`.
- **Locked** clips show a lock and refuse moves, trims and re-picks (unlock from the menu).
- **Play-through:** click or drag in the ruler to seek (starts Play all paused there if it
  isn't open); Space play/pause; J / K / L; the playhead follows. With a `track`, an
  "Audio: clips | recording" toggle plays the recording under the cut instead (the clip
  audio muted), as `h3assemble --audio master` does; it warns when trims or reordering
  make the recording drift.
- **Waveforms:** a toggleable lane under the clips: each clip's own audio (`take.audio`),
  or, for a shot with a dialogue window when the audio toggle is on "recording", its slice
  of the track (`audio_in`..`audio_out`, trims applied). Peaks are fetched per clip at the
  zoom's resolution and cached.

### Phase 9b as built

The backend parts above are implemented (`h3edit`: the cut edits, the status fields and
`h3.py cut`; `h3peaks.py`: sound detection, durations and peaks; the routes stay thin).
Each point is **[differs]**, **[added]** or **[settled]**, as for 9a. No field the contract
named changes shape.

**`PUT /h3pipe/cut`**
- **[settled] The one-frame check** is against the take the entry would use (its `take` from
  its `pass`, else the latest usable one), counting its `frames` (the sidecar's) in the
  pass's cut frames; a take at another rate (Wan 14B's 16 fps) counts by duration,
  `round(frames × cut fps / take fps)`. A take whose length isn't known (nothing rendered, a
  picked take that isn't usable, a sidecar without `frames`) isn't checked. The dialogue-window
  trim assemble applies first is not counted: trims are against the take as `cut.frames` shows
  it. The 400 names the shot. A float, a string or a boolean trim is 400 too.
- **[settled] History:** `<ep>/_history/cut.json.<YYYYmmdd-HHMMSS>` (`-2`, `-3`… within a
  second), the newest 30, through the same helper as source saves. A write that would change
  nothing writes nothing and makes no copy; there is no copy when there was no `cut.json`.
  PUT, reset, copy and `h3.py cut` make copies; **picks and discards don't** (they would push
  the edits out of the 30).
- **[settled]** The server doesn't refuse moving or trimming a locked entry in a PUT (the UI
  does; `h3.py cut` refuses without `--force`).

**`POST /h3pipe/cut/reset` and `/cut/copy`**
- **[settled]** Both write the pass's full list (every shot named, as a pick does), answer
  `{"cut"}` and send `h3pipe.episode`. 400 for a bad `what` or pass, or `from` = `to`; 404
  when a pass they touch isn't built.
- **[settled] Reset order** is the script order of the pass's build (every target's
  shotlists); orphans go after the script's shots, in their current order.
- **[differs] A locked entry keeps its trims** on "trims" / "all" resets and on a copy of
  trims: the lock protects them as it protects the pick. Its place in the order still
  changes.
- **[settled] Copy order:** the target pass's entries sorted by their place in the source
  pass's cut; a shot only the target has stays right after the entry it follows now. Picks,
  placeholder passes, locks and notes stay the target's own.
- **[settled] Copy trims:** each pass counts in assemble's cut rate (the series config's
  `series.fps`, else that pass's shotlist's `defaults.fps`, else 24), so they differ only
  when the series config sets no `fps`; trims are then `round(trim × to fps / from fps)`.
  Trims that would leave less than one frame of the target's take (when its length is known)
  are cut down, `trim_out` first, rather than refused.

**`GET /h3pipe/episode`**
- **[settled]** `cut.order` is the 0-based index in the pass's resolved cut (orphans
  included); `cut.script_index` the index in the pass's script order (every target's shots),
  null for an orphan. `cut.out_of_order` is true for the entries outside the longest run
  that is in script order: moving one shot flags just that shot; swapping two neighbours
  flags one of them. Orphans are never flagged.
- **[added] `track.exists`.** `track` is `{"path", "duration", "rate", "exists"}` from the
  series config's `audio.track` (relative to the episode, as `h3align` writes it). `path` is
  relative to the episode with forward slashes (`../audio/x.wav` above it; absolute on another
  drive, which `/h3pipe/peaks` can't serve). `duration` (seconds) and `rate` (Hz) are null
  when the file is missing or unreadable (an empty placeholder). `track` is null when the
  series config names no track.
- **[settled]** `audio_in` / `audio_out` are the build's window (the shotlist entry's), in
  seconds on the track; a shot with no window has neither key.
- **[settled] `take.audio`** is a path relative to the episode, or null. Whether an mp4 has
  sound is read from its boxes (`moov/trak/mdia/hdlr` of type `soun`), not ffprobe, and cached
  per (path, size, mtime) for the process; an empty or unreadable mp4 counts as silent.
  **h3assemble now uses the same functions** (`h3peaks.clip_audio` for `--audio auto` and
  `mp4`, `h3peaks.has_audio`), so its check moved from ffprobe to that box read too
  (ffprobe remains the fallback for files that aren't mp4/mov/m4a or wav).

**`PUT /h3pipe/pick`**
- **[added]** The locked 409's body also has `"locked": true`; its message says to unlock
  or force. The lock is per pass (a final lock doesn't stop a proxy pick). `force: true`
  passes both the not-usable and the locked 409s. A discard still clears a locked entry's
  pick of the take it discards (the take is gone).

**`GET /h3pipe/peaks`**
- **[added] `start` / `end`** in the answer: the range actually used (clamped to the file).
  A silent file answers `{"duration", "bins": 0, "peaks": [], "silent": true, "start",
  "end"}`; `duration` is the container's (null if nothing can tell).
- **[settled] `bins`** is optional: the default is the cached resolution over the range (200
  a second). 1 to 100000, else 400. `start` / `end` are optional, ≥ 0, clamped to the file;
  `end` ≤ `start` is 400. Each output bin is the max of the cached bins it overlaps; with
  more bins than the range has, a bin repeats the nearest one.
- **[settled] Scale:** `round(|sample| / 32767 × 255)`. The stdlib path (PCM wav, 8/16/24/
  32-bit integer) takes the max over the channels; ffmpeg's path is the `-ac 1` mix. A float
  wav, or any wav `wave` can't open, goes through ffmpeg.
- **[settled] No audio stream** is judged from the mp4 boxes or the wav header, else ffprobe;
  with no ffprobe, a file that is neither counts as silent. A file with sound and no ffmpeg on
  PATH is 500 naming ffmpeg; an ffmpeg failure is 500 with its message.
- **[settled] Cache:** `<ep>/_cache/peaks/<sha1 of "path|size|mtime_ns">.json`, where `path`
  is the requested path with forward slashes and no empty or `.` parts. It holds `{"version":
  1, "path", "duration", "rate": 200, "silent", "peaks": <hex, one byte a bin>}`. Old
  entries aren't pruned. An episode folder that can't be written still gets its answer.
- **[settled] Paths:** 400 for a path that is absolute or climbs out, and also for one that
  leads out through a link (`/h3pipe/file` answers 403 there; the contract said 400 here).
  `../` is allowed into a parent-folder series config's folder, as for `/h3pipe/file`. 404
  for a missing file.

**`h3.py cut`**
- **[settled]** With no action it shows the cut (`--show`): order, take, trims, frames and
  flags (`locked`, `picked`, `placeholder(pass)`, `OUT OF ORDER`, `orphan`). After each edit it
  shows the cut again.
- **[settled]** `--order SH,SH,…` puts those shots first, in that order; the rest follow in
  their current order. `--copy-from PASS [WHAT]` (default `all`) copies onto the pass
  `--proxy` picks (final without it). `--move` and `--trim` refuse a locked shot without
  `--force`. A refusal exits 1, a usage error 2.
