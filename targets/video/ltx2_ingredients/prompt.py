"""
The LTX-2.3 ingredients prompt: the two labelled parts the IC-LoRA was
trained on (the model card, quoted in the template's notes):

    Reference sheet: <description of the panels in the sheet — characters, props, location>

    Generated video: <description of the action / shot you want generated>

The first part names each panel of the shot's sheet in order, from the series
config: a character by its `name` and `design` and which view of it the panel
is, a prop or vehicle by its `name` and `design`, the plate by the location's
`description`. The second part is the ltx2 target's prose paragraph (style,
framing and camera, who is in frame, the action, the lines, the sound), except
that a subject on the sheet is only named there: the sheet shows what it looks
like. A subject left off the sheet (its ref missing, rendering anyway) keeps
its design in the prose, and a shot with no panels at all is plain ltx2 prose.

Stdlib only.
"""
from __future__ import annotations

import copy

from targets.video.ltx2.prompt import _sentence
from targets.video.ltx2.prompt import build_prompt as prose

VIEW_WORDS = {"body": "shown full-body in a three-quarter view",
              "face": "shown in a face close-up"}


def panel_text(panel: dict, series_cfg: dict) -> str:
    """One panel's sentence."""
    if panel.get("location"):
        loc = series_cfg.get("locations", {}).get(panel["location"], {})
        env = (loc.get("description") or "").strip().rstrip(".")
        return _sentence(f"the location, {env}" if env else "the location")
    e = series_cfg.get("subjects", {}).get(panel["subject"], {})
    name = e.get("name", panel["subject"])
    design = (e.get("design") or "").strip().rstrip(".")
    parts = [name] + ([design] if design else [])
    if panel.get("view") in VIEW_WORDS:
        parts.append(VIEW_WORDS[panel["view"]])
    return _sentence(", ".join(parts))


def sheet_text(panels: list[dict], series_cfg: dict) -> str:
    return " ".join(panel_text(p, series_cfg) for p in panels)


def build_prompt(shot, seq, series_cfg: dict, panels: list[dict]) -> str:
    """The prompt for one shot (`shot` an h3core.ir.Shot, `seq` its
    ir.Sequence) whose sheet has `panels` (the entry's `panels`)."""
    if not panels:
        return prose(shot, seq, series_cfg)
    on_sheet = {p["subject"] for p in panels if p.get("subject")}
    named = copy.deepcopy(series_cfg)
    for s in on_sheet:
        if s in named.get("subjects", {}):
            named["subjects"][s].pop("design", None)
    return (f"Reference sheet: {sheet_text(panels, series_cfg)}\n\n"
            f"Generated video: {prose(shot, seq, named)}")
