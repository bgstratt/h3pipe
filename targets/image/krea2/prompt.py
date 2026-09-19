"""
krea2: the wording of every reference image, the one source for it.

A video target decides the SHAPE of a ref it needs (a 4-view character sheet,
an object on a plain background, a location plate: targets.RefRequest); this
module words it for the image model. h3build writes the refs_todo prompts
through `ref_prompt`, and kreagen and the editor generate with `view_prompt`,
`object_prompt` and `plate_prompt` (re-exported by h3refs).

Stdlib only.
"""
from __future__ import annotations

# A character sheet is generated as four square views and stitched (a 4096x1024
# canvas is far outside any image model's training distribution). The views,
# in sheet order, left to right:
VIEWS = [
    ("01_threequarter", "a three-quarter view of the full figure from head to feet, "
                        "turned slightly toward the viewer's left, standing straight "
                        "with arms relaxed at the sides"),
    ("02_side",         "a direct side profile of the full figure from head to feet, "
                        "facing the viewer's right, standing straight with arms "
                        "relaxed at the sides"),
    ("03_back",         "the full figure seen from directly behind, head to feet, "
                        "standing straight with arms relaxed at the sides"),
    ("04_face",         "a head-and-shoulders close-up, facing the viewer, "
                        "neutral expression"),
]
VIEW_TAGS = [tag for tag, _ in VIEWS]
VIEW_DESC = dict(VIEWS)

VIEW_TMPL = ("A single character reference view on a plain flat neutral background, "
             "no scene and no props, the whole figure inside the frame with margin "
             "on every side: {view}. {design}. Drawn as {look}. Output {w}x{h}.")


def view_prompt(view: str, design: str, look: str, w: int, h: int) -> str:
    """The prompt for one view of a character sheet (`view` is a VIEWS tag)."""
    return VIEW_TMPL.format(view=VIEW_DESC[view], design=design, look=look, w=w, h=h)


def sheet_prompt(design: str, look: str) -> str:
    """The whole 4-panel sheet, described for someone making it by hand
    (refs_todo). Generating uses view_prompt four times instead."""
    return (f"A character model sheet on a plain flat background: FOUR panels side "
            f"by side in a single horizontal strip, left to right — three-quarter "
            f"body, side profile full body, back view full body, and a "
            f"head-and-shoulders facial close-up. The SAME character in all four. "
            f"{design}. Drawn as {look}. "
            f"Output 4096x1024 or larger.")


def object_prompt(design: str, look: str) -> str:
    """A prop or vehicle reference."""
    return (f"A single clean three-quarter view of one object on a plain flat "
            f"background, no scene around it. {design}. Drawn as "
            f"{look}. Output 1024x1024 or larger.")


def plate_prompt(look: str, description: str) -> str:
    """A location's background plate."""
    return (f"A background plate drawn as {look}. An empty establishing "
            f"view of {description}. No characters, no props, no figures in frame — "
            f"the environment only. Wide framing that shows the layout of the space.")


def ref_prompt(target, req, series_cfg: dict) -> str:
    """The wording of one ref request (targets.RefRequest) as refs_todo shows
    it. Values are read in the order the build always read them, so a series
    config missing one reports the same key."""
    e = req.entry
    if req.shape == "plate":
        return plate_prompt(look=series_cfg['style']['look'], description=e['description'])
    if req.shape == "sheet":
        if req.views != len(VIEWS):
            raise ValueError(f"{target.id} words a {len(VIEWS)}-view sheet, not {req.views}")
        return sheet_prompt(e['design'], series_cfg['style']['look'])
    if req.shape == "object":
        return object_prompt(e['design'], series_cfg['style']['look'])
    raise ValueError(f"{target.id} can't word a {req.shape!r} reference")
