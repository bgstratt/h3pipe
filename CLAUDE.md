# h3pipe

Script-to-episode pipeline for AI video on a local ComfyUI. Authored inputs are
`series.json` (bible) and `epNN.md` (script); everything else is generated.

**Current work: `docs/PLAN.md`** — a shot/take editor inside ComfyUI (takes,
overrides, cut, then the UI), followed by making the pipeline model-agnostic (story
IR + targets). Read it before changing `h3build.py`, `h3render.py`, `kreagen.py` or
`comfy_nodes/`, and follow its phase order and exit checks.

## Layout

- `h3.py` — one CLI for every stage (`build`, `check`, `refs`, `render`, `assemble`, `all`)
- `h3core/` — model-free core: script parser → story IR (`shotlist/shots.json`), bible loading, speech pacing
- `h3build.py` — script + bible → story IR → H3 compile → `shotlist/*.json`, `refs_todo.*`
- `h3render.py` — queues shots on ComfyUI via `workflows/H3_Ref2VA_Shotlist_v1.json`
- `kreagen.py`, `mksheet.py` — reference images
- `h3align.py` — times the script against a dialogue recording
- `h3assemble.py` — review cut (ffmpeg)
- `comfy_nodes/` — the ComfyUI custom node pack (loader, info, save)
- `docs/AUTHORING.md` — the script/bible format (source of truth; `prompts/` is
  generated from it by `python tools/make_prompts.py`)
- `h3plan.py` — legacy, don't extend

## Rules

- Pipeline scripts are **stdlib only** (Python 3.10+). Only `comfy_nodes/` may use
  torch/numpy/PIL (ComfyUI provides them).
- Never hand-edit generated files (`shotlist/`, `refs_todo.*`); change the script,
  the bible or the code and rebuild.
- Refactors must keep the golden outputs byte-identical (`tests/golden/`,
  `python -m pytest` or `python -m unittest discover -s tests`) unless the change is
  intended and the goldens are updated in the same commit with the reason stated
  (`python tests/test_golden.py --update`, then review the diff). Real-episode
  fixtures live in the gitignored `tests/local/`; never commit them.
- Seeds come from episode/sequence/shot ids (`stable_seed`); don't change that
  derivation — it's what makes untouched shots re-render identically.
- ComfyUI runs at `http://127.0.0.1:8188`; `refs` and `render` need it up.
- Windows is the dev machine: use `os.path`/`pathlib`, not hardcoded `/`.
