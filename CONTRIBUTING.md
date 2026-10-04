# Contributing

Issues and pull requests are welcome.

- The pipeline scripts use the Python standard library only. Please keep it that way, so
  `h3.py` and everything it runs work anywhere without a virtualenv. `ffmpeg` and
  `ffprobe` are the only external tools (assemble, master, publish, the waveform peaks and
  the audio work need them).
- `comfy_nodes/` runs inside ComfyUI, so torch, numpy and PIL are fair game there.
- Shot IDs, seeds and the shotlist schema are load-bearing: a change that re-seeds shots
  invalidates renders people may already have approved. Call that out in the PR.
- Run the tests before opening a PR: `python -m pytest`, or `python -m unittest discover -s
  tests` with nothing installed. The golden outputs in `tests/golden/` must stay
  byte-identical unless the change means to alter them; then regenerate them with
  `python tests/test_golden.py --update`, review the diff and say why in the PR. If a change
  touches rendering, say what you rendered to verify it.
- The editor UI lives in `web/` (React/Vite); `npm run build` there writes the committed
  bundle `comfy_nodes/web/h3pipe-editor.js`, so commit both. `npm test` runs its tests.
- `prompts/` and the skill in `build/skill/` are generated. Edit `docs/AUTHORING.md` (and
  `docs/SCRIPT_CONVERSION.md`) and run `python tools/make_prompts.py`.
- INSTALL.md's model list is generated too. If you change a target's `models`, `presets`
  or `downloads`, run `python tools/make_models_md.py` and commit the result;
  `tests/test_docs.py` fails when it is out of date (`--check` does the same without
  writing). Never add a download URL you cannot trace to a ComfyUI template, a saved
  workflow or ComfyUI-Manager's model list — leave `url` null and say so in `source`.
