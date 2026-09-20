# Contributing

Issues and pull requests are welcome.

- The pipeline scripts use the Python standard library only. Please keep it that way, so
  `h3build`, `h3render` and `h3assemble` run anywhere without a virtualenv. `ffmpeg` and
  `ffprobe` are the only external tools, and only `h3assemble` needs them.
- `comfy_nodes/h3_shotlist.py` runs inside ComfyUI, so torch and numpy are fair game there.
- Shot IDs, seeds and the shotlist schema are load-bearing: a change that re-seeds shots
  invalidates renders people may already have approved. Call that out in the PR.
- Test a change with `python h3build.py examples/series_example.json examples/script_example.md --check`
  before opening a PR. If it touches rendering, say what you rendered to verify it.
- `prompts/` and the skill in `build/skill/` are generated. Edit `docs/AUTHORING.md` (and
  `docs/BREAKDOWN.md`) and run `python tools/make_prompts.py`.
- INSTALL.md's model list is generated too. If you change a target's `models`, `presets`
  or `downloads`, run `python tools/make_models_md.py` and commit the result;
  `tests/test_docs.py` fails when it is out of date (`--check` does the same without
  writing). Never add a download URL you cannot trace to a ComfyUI template, a saved
  workflow or ComfyUI-Manager's model list — leave `url` null and say so in `source`.
