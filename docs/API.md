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
           "in_cut_file": true},
   "override": {"fields": ["prompt", "seed"], "stale": false},
   "takes": [{"take": 1, "status": "ok", "has_video": true,
              "seed": "6430499148929255544", "seed_source": "stable", "note": "",
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
episode's bible and script (`h3edit.episode_bible` / `episode_script`).
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
  server>)`: the copy saved in ComfyUI, else the repo copy.
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
No h3pipe route is needed.

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
  `ok: false` and a top-level `error`. Build with no bible or no script returns
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
 "dirs": [{"name": "ep05", "path": "C:\…\ep05", "episode": true, "bible": true}]}
```
- No `path` gives the starting points: the home folder and the drives on Windows.
  `parent` is then `null`; at a drive root it is `""`.
- `episode` means the folder has a `series.json` and a script; `bible` means it has
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
    model-specific, so it belongs to the Phase 7 target adapters.
- The CLI equivalent is `h3render --allow-missing-refs`. Without it, blocked shots are
  listed and skipped.

## References (Phase 5)

A **ref** is any conditioning input a render reads that the pipeline generates or
you supply. Refs are the same thing whatever model consumes them. Each has a `scope`:

| scope | kinds | named in | id |
|---|---|---|---|
| `series` | `character`, `prop`, `vehicle` (bible subjects), `location` (bible locations), `voice` (a subject's `voice_sample`) | the bible | `subject:<id>`, `location:<id>`, `voice:<id>` |
| `shot` | `keyframe`: `first` / `last` frame of one shot, for FL2V and I2V models | the script (a later field) or the editor | `shot:<shot>:first`, `shot:<shot>:last` |

- **Series refs are listed from the bible**, not from `refs_todo`. A character nobody
  uses yet can still be generated. `refs_todo` becomes a filter: "used by this
  episode", and which shots it blocks.
- **Shot keyframes:** Phase 5 builds their storage, takes and pick. Generating them
  (a still from the shot's prompt, or the previous shot's last frame) and feeding
  them to an FL2V target come with that target. Their home is
  `refs/shots/<shot>/<first|last>.png`.
- A **ref take** is one generated or imported candidate. It lives in
  `refs/_takes/<ref key>/<ref key>[_<view>]_tNN.png`, with a sidecar `…_tNN.json` in
  the video takes' format (status, seed, prompt, model, LoRAs, steps, queued,
  finished, and `source`: `generated` or `imported`). The ref key is the id with `:`
  replaced by `__`.
- A **character** has views (`01_threequarter`, `02_side`, `03_back`, `04_face`, as
  in `kreagen.VIEWS`). Each view has its own takes and pick. Picking a view that
  completes the set stitches the sheet with `mksheet` into the bible's `sheet` path.
- **Picking** a take copies it to the path the bible names, which is the file renders
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
- The workflow is `krea2_refs_t2i.json`, found through `resolve_workflow`.
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
