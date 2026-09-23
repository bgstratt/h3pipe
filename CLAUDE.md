# h3pipe

Script-to-episode pipeline for AI video on a local ComfyUI. Authored inputs are
`series.json` (the series config) and `epNN.md` (script); everything else is generated.

**Current work: `docs/PLAN.md`** — a shot/take editor inside ComfyUI (takes,
overrides, cut, then the UI), followed by making the pipeline model-agnostic (story
IR + targets). Read it before changing `h3build.py`, `h3render.py`, `kreagen.py` or
`comfy_nodes/`, and follow its phase order and exit checks. Phases 11 and 12 are in:
a target's workflow can be handed to ComfyUI and back, and a show can have targets of
its own (data only, proposed from a workflow by `h3inspect.py`).

## Layout

- `h3.py` — one CLI for every stage (`new`, `build`, `check`, `refs`, `render`, `assemble`, `all`)
- `h3core/` — model-free core: script parser → story IR (`shotlist/shots.json`), series config loading, speech pacing
- `h3build.py` — script + series config → story IR → H3 compile → `shotlist/*.json`, `refs_todo.*`
- `h3render.py` — queues shots on ComfyUI through the shot's target (`targets/video/<id>/`: template, recipe,
  binding, prompt writer, workflow; H3 is `minimax_h3_ref2va`). Ref images: `targets/image/krea2/`;
  voice refs: `targets/audio/ltx2_voice/`
- `h3takes.py` (take/cut/override files), `h3jobs.py` (plan + queue a take), `h3edit.py`
  (episode status, pick/override, the `takes`/`pick`/`override` commands),
  `h3refs.py` (refs as takes, driven by the series config), `h3track.py` (attach a recording, run h3align),
  `h3source.py` (read/check/save the script and
  series config; `new_episode` makes one from a template — `examples/starter/`, or the
  episode beside it), `h3promote.py` (overrides → script/series config) — shared by the CLI and the routes
- `comfy_nodes/h3pipe_api.py` + `h3pipe_routes.py` — the editor's HTTP API (`docs/API.md`);
  `web/` — the editor UI (React/Vite), built into `comfy_nodes/web/h3pipe-editor.js`
- `kreagen.py`, `mksheet.py` — reference images
- `h3inspect.py` — reads a ComfyUI workflow and proposes the `target.json` that drives it
  (a show's own targets live in `<show>/targets/<id>/target.json`; `targets/generic/` holds
  the `builtin:` code such a target uses instead of shipping Python)
- `h3align.py` — times the script against a dialogue recording
- `h3assemble.py` — review cut (ffmpeg)
- `h3peaks.py` — a media file's sound: has it any, duration, waveform peaks (the editor's
  `/h3pipe/peaks`); its `clip_audio` is assemble's `--audio auto` rule
- `comfy_nodes/` — the ComfyUI custom node pack (loader, info, save)
- `docs/AUTHORING.md` — the script/series config format (source of truth; `prompts/` is
  generated from it by `python tools/make_prompts.py`). Its opening example is
  `examples/starter/`, the pair `h3.py new` writes: copy it in with
  `python tools/sync_starter_doc.py` (`--check` in `tests/test_new_episode.py`), then
  regenerate `prompts/`
- `INSTALL.md` — setting up on a fresh machine; its model list is generated from each
  target's `downloads` by `python tools/make_models_md.py` (`--check` in `tests/test_docs.py`)

## Rules

- Pipeline scripts are **stdlib only** (Python 3.10+). Only `comfy_nodes/` may use
  torch/numpy/PIL (ComfyUI provides them).
- Never hand-edit generated files (`shotlist/`, `refs_todo.*`, `prompts/`, INSTALL.md's
  model block); change the script,
  the series config or the code and rebuild.
- Refactors must keep the golden outputs byte-identical (`tests/golden/`,
  `python -m pytest` or `python -m unittest discover -s tests`) unless the change is
  intended and the goldens are updated in the same commit with the reason stated
  (`python tests/test_golden.py --update`, then review the diff). Real-episode
  fixtures live in the gitignored `tests/local/`; never commit them.
- Seeds come from episode/sequence/shot ids (`stable_seed`); don't change that
  derivation — it's what makes untouched shots re-render identically.
- ComfyUI runs at `http://127.0.0.1:8188`; `refs` and `render` need it up.
- Windows is the dev machine: use `os.path`/`pathlib`, not hardcoded `/`.
- Terminology: `series.json` is the **series config**. Never call it the bible (retired 2026-09-18).
