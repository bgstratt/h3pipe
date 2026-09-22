"""
What the three Wan 2.2 targets share. The compile itself is
`builtin:video_prose` (targets/generic/video_prose.py, where this file's
compile used to live); this binds it to Wan's wording — dialogue as silent
acting, and the `--check` line that says so names Wan rather than the target's
own short label — and re-exports the helpers each target's compile.py uses for
its graph surgery (wan22_i2v, wan22_ti2v, wan22_vace).

Anything not about Wan in particular belongs in video_prose, so a custom target
made from a user's own workflow gets it too.

Stdlib only.
"""
from __future__ import annotations

from targets.generic.video_prose import (                # noqa: F401  (re-exported)
    Ctx, PLATE, SILENT, Style, abs_path, compile_entry, consumers, is_series_target,
    keyframe_path, new_id, of, one, ordered, panel_view, panels, plate_request, ref_slots,
    required_refs, split_stages, subject_request,
)
from targets.generic import video_prose as G

from .prompt import build_prompt

# Wan's own wording: the writer that turns dialogue into silent acting, and the
# warning that names Wan (not "Wan 5B" / "Wan+refs", which is what the target's
# own short label would give).
STYLE = Style(build_prompt,
              silent_dialogue=("{n} dialogue shot(s): no audio or lip-sync on Wan "
                               "(the lines are acted silently): {ids}"))


def compile_episode(target, story, series_cfg: dict, pass_: str,
                    only: "set[str] | None" = None, absent=None) -> tuple[dict, dict]:
    return G.compile_episode(target, story, series_cfg, pass_, only, absent, STYLE)


def compile_shot(target, shot_ir, series_cfg: dict, preset, ctx=None) -> dict:
    return G.compile_shot(target, shot_ir, series_cfg, preset, ctx, STYLE)


def compile_without(target, story, series_cfg: dict, pass_: str, entry: dict,
                    missing: list[dict]) -> dict:
    return G.compile_without(target, story, series_cfg, pass_, entry, missing, STYLE)
