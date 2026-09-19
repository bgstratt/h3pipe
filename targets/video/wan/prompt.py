"""
The Wan 2.2 prompt: one paragraph of plain prose, for every Wan target.

Wan's UMT5 text encoder reads a plain description (Wan's prompt guide: the
subject and what it looks like, the scene, the motion, the camera, the look).
The structure is the ltx2 target's (targets/video/ltx2/prompt.py), minus
everything about sound: Wan makes no audio, so there is no soundscape, music
or quoted dialogue. A line of dialogue becomes silent acting, so the picture
still carries the performance the edit lays the voice over:

    Style: <look>. A <size> of <location description>, <camera>. <Name> is
    <design>. <action> <Name> talks, <delivery>, mouth moving with the words.

A voiceover by someone in frame keeps their lips closed; a line from off
screen adds nothing (nobody in frame speaks it). The negative prompt is a
preset value (Wan's standard Chinese one, from ComfyUI's Wan templates).

Stdlib only.
"""
from __future__ import annotations

from targets.video.ltx2.prompt import SIZE_WORDS, _and, _article, _sentence


def acting(shot, book: dict) -> list[str]:
    """The dialogue as silent acting: one sentence per run of lines by one
    speaker, in script order."""
    out, last = [], None
    closed: set[str] = set()
    for d in shot.dialogue:
        e = book.get(d.speaker) or {}
        name = e.get("name", d.speaker)
        delivery = (d.delivery or "").strip().rstrip(".")
        if d.mode == "vo":
            if d.speaker in shot.cast and d.speaker not in closed:
                closed.add(d.speaker)
                out.append(_sentence(f"{name} stays silent, lips closed"))
            last = None
            continue
        if d.mode == "os":
            last = None
            continue
        if d.speaker == last:
            continue
        last = d.speaker
        out.append(_sentence(f"{name} talks{', ' + delivery if delivery else ''}, "
                             f"mouth moving with the words"))
    return out


def build_prompt(shot, seq, series_cfg: dict) -> str:
    """The prose prompt for one shot (`shot` an h3core.ir.Shot, `seq` its
    ir.Sequence)."""
    book = series_cfg.get("subjects", {})
    look = ((series_cfg.get("style") or {}).get("look") or "").strip().rstrip(".")
    loc = series_cfg.get("locations", {}).get(shot.plate or seq.location, {})
    env = (loc.get("description") or "").strip().rstrip(".")
    parts = []
    if look:
        parts.append(f"Style: {look}.")

    # the framing, where, and how the camera moves (static unless the script says)
    size = SIZE_WORDS.get(shot.size, "medium shot")
    cam = (shot.camera or "").strip().rstrip(".")
    camera = f"the camera {cam}" if cam else "the camera remains static"
    parts.append(_sentence(f"{_article(size)}{' of ' + env if env else ''}, {camera}"))

    # who is in frame, in words
    def name_of(s: str) -> str:
        return (book.get(s) or {}).get("name", s)

    on_screen = list(shot.cast) + [p for p in shot.props if p not in shot.cast]
    plain = []
    for s in on_screen:
        design = ((book.get(s) or {}).get("design") or "").strip().rstrip(".")
        if design:
            parts.append(_sentence(f"{name_of(s)} is {design}"))
        else:
            plain.append(name_of(s))
    if plain:
        parts.append(_sentence(f"{_and(plain)} {'is' if len(plain) == 1 else 'are'} in frame"))
    if shot.extras:
        parts.append(_sentence(f"Also in frame are {shot.extras}, unnamed background figures "
                               f"who look nothing like "
                               f"{_and([name_of(s) for s in shot.cast]) or 'anyone named'}"))

    # the action, on-screen text, then the lines as silent acting
    if shot.action:
        parts.append(_sentence(shot.action))
    if shot.text:
        parts.append(f'Visible on-screen text reads "{shot.text}".')
    parts += acting(shot, book)
    return " ".join(p for p in parts if p)
