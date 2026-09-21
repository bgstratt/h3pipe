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

# What an EDIT of one view can actually carry over from it, and what that view
# must not be talked into showing. A back view has no face in it: asking to
# keep "the face exactly as in the reference" is a contradiction the model
# resolves the only way it can — by turning the character around. The `design`
# sentence describes a face too (it has to, it serves all four views), so the
# back and side views say plainly what is not visible.
VIEW_KEEP = {
    "01_threequarter": ("the face, hair, build, proportions and line quality", ""),
    "02_side":         ("the profile of the face, the hair, build, proportions and line "
                        "quality",
                        " This view is a side profile: only one side of the face is "
                        "visible, and the figure must stay in profile rather than turning "
                        "toward the viewer."),
    "03_back":         ("the hair, the shape of the head, the build, proportions and line "
                        "quality",
                        " This view is from directly behind: the face is NOT visible and "
                        "the figure must not be turned toward the viewer, whatever the "
                        "description says about the face. Draw the back of the head and "
                        "the back of what they are wearing."),
    "04_face":         ("the face, hair and line quality", ""),
}

# How each view is FRAMED. Three of the four want the whole figure in shot; the
# fourth is a close-up and must not, or one clause cancels the other and the
# model draws a full figure, because that is the instruction it can satisfy
# while still showing a head. It sits in the shared wording, so every image
# target had the same un-zoomed close-up.
VIEW_FRAME = {
    "01_threequarter": "the whole figure inside the frame with margin on every side",
    "02_side":         "the whole figure inside the frame with margin on every side",
    "03_back":         "the whole figure inside the frame with margin on every side",
    "04_face":         "framed close on the head and shoulders and cropped at the chest, "
                       "the head filling most of the frame, NOT the whole figure",
}

VIEW_TMPL = ("A single character reference view on a plain flat neutral background, "
             "no scene and no props, {frame}: {view}. {design}.{caveat} Drawn as "
             "{look}. Output {w}x{h}.")


def view_prompt(view: str, design: str, look: str, w: int, h: int) -> str:
    """The prompt for one view of a character sheet (`view` is a VIEWS tag).

    One `design` sentence serves all four views, so it describes a face even
    for the two views that can't show one. The view's caveat (VIEW_KEEP) says
    so, or the back view comes back with the character turned around to make
    the description true. The framing is the view's too (VIEW_FRAME): the
    close-up is the one view that must NOT hold the whole figure."""
    return VIEW_TMPL.format(view=VIEW_DESC[view], design=design, look=look, w=w, h=h,
                            caveat=VIEW_KEEP[view][1], frame=VIEW_FRAME[view])


def view_edit_prompt(view: str, design: str, look: str, w: int, h: int,
                     base_name: str, word: str = "reference image") -> str:
    """One view of a wardrobe variant, generated as an EDIT of the same view of
    the subject it is a variant of (docs/PLAN.md, Phase 10b).

    The brief is what to CHANGE, not what to draw: everything the reference
    already settles — hair, build, proportions, line quality, the view itself,
    and the face where the view has one — is named as fixed, so the only thing
    left for the model to invent is the wardrobe the description asks for.
    Generating the same view cold is what makes the face drift between a
    character and their variant.

    What is named as fixed is per view (VIEW_KEEP): see the note there for why
    the back view must not be told to preserve a face."""
    keep, caveat = VIEW_KEEP[view]
    return (f"The {word} is {base_name}: {VIEW_DESC[view]}. Redraw that same character in "
            f"that same view and at the same scale, keeping {keep} exactly as they are in "
            f"the {word}, and changing only what this description changes: {design}."
            f"{caveat} Keep the plain flat neutral background, no scene and no props, "
            f"{VIEW_FRAME[view]}. Drawn as {look}. Output {w}x{h}.")


def sheet_prompt(design: str, look: str, base: dict | None = None) -> str:
    """The whole 4-panel sheet, described for someone making it by hand
    (refs_todo). Generating uses view_prompt four times instead.

    `base` is the subject this one is a wardrobe variant of (`of:`), as
    {"name", "sheet"}: the note tells whoever makes the sheet — a person or a
    batch run — to start from that image rather than from nothing, which is
    what holds the face across a change of clothes. It is an instruction about
    where to start, never part of what the picture shows, so it goes last and
    only here: view_prompt feeds a text-to-image model, where naming a file on
    disk is noise (docs/PLAN.md, Phase 10b wires the real edit path)."""
    out = (f"A character model sheet on a plain flat background: FOUR panels side "
           f"by side in a single horizontal strip, left to right — three-quarter "
           f"body, side profile full body, back view full body, and a "
           f"head-and-shoulders facial close-up. The SAME character in all four. "
           f"{design}. Drawn as {look}. "
           f"Output 4096x1024 or larger.")
    if base and base.get("sheet"):
        out += (f" This is {base.get('name', 'the same character')} in a different state: "
                f"start from the existing sheet at {base['sheet']} and change only what "
                f"the description above changes, so the face, build and line quality stay "
                f"identical.")
    return out


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
        return sheet_prompt(e['design'], series_cfg['style']['look'],
                            base=(series_cfg.get('subjects') or {}).get(e.get('of')))
    if req.shape == "object":
        return object_prompt(e['design'], series_cfg['style']['look'])
    raise ValueError(f"{target.id} can't word a {req.shape!r} reference")
