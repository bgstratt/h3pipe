"""
The LTX-2 prompt: one flowing paragraph of prose, no <Picture N>.

LTX-2 was trained on long audio-visual captions, and its prompt enhancer
(ComfyUI's TextGenerateLTX2Prompt, whose system prompts spell the style out)
asks for: the visual style first ("Style: ..."), then the shot type, camera
motion and viewpoint woven into the prose ("a medium shot frames ... as the
camera slowly pushes in"; if there is none, say the camera remains static,
never invent one), then the subjects with their visible appearance, the action
in chronological order, dialogue quoted exactly with who says it and how, and
the soundscape and music integrated rather than labelled. No headings, no
"The scene opens with", no smell or touch. This writer produces that
paragraph from the story IR and the series config, without calling the LLM:

    Style: <look>. A <size> of <location description>, <camera>. <Name> is
    <design>, and <Name> is <design>. <action> <Name>, in a voice that is
    <voice>, says, <delivery>: "<line>" ... <sound>. <music> plays underneath.

Subjects are described in words (the series config's `design`): LTX takes no
identity reference. A shot rendered from a first-frame keyframe still carries
the full description; the picture wins where they differ.

Stdlib only.
"""
from __future__ import annotations

SIZE_WORDS = {"close": "close-up", "cu": "close-up", "medium": "medium shot",
              "ms": "medium shot", "wide": "wide shot", "ws": "wide shot"}


def _sentence(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    text = text[0].upper() + text[1:]
    return text if text[-1] in ".!?\"'" else text + "."


def _and(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + (", and " if len(items) > 2 else " and ") + items[-1]


def _article(phrase: str) -> str:
    return ("an " if phrase[:1].lower() in "aeiou" else "a ") + phrase


def build_prompt(shot, seq, series_cfg: dict) -> str:
    """The prose prompt for one shot (`shot` an h3core.ir.Shot, `seq` its
    ir.Sequence)."""
    book = series_cfg.get("subjects", {})
    look = ((series_cfg.get("style") or {}).get("look") or "").strip()
    loc = series_cfg.get("locations", {}).get(shot.plate or seq.location, {})
    env = (loc.get("description") or "").strip()
    parts = []
    if look:
        parts.append(f"Style: {look}.")

    # camera first: the framing, where, and how the camera moves
    size = SIZE_WORDS.get(shot.size, "medium shot")
    cam = (shot.camera or "").strip().rstrip(".")
    camera = f"the camera {cam}" if cam else "the camera remains static"
    parts.append(_sentence(f"{_article(size)}{' of ' + env if env else ''}, {camera}"))

    # who is in frame, in words
    book_name = lambda s: (book.get(s) or {}).get("name", s)  # noqa: E731
    on_screen = list(shot.cast) + [p for p in shot.props if p not in shot.cast]
    plain = []
    for s in on_screen:
        design = ((book.get(s) or {}).get("design") or "").strip().rstrip(".")
        if design:
            parts.append(_sentence(f"{book_name(s)} is {design}"))
        else:
            plain.append(book_name(s))
    if plain:
        parts.append(_sentence(f"{_and(plain)} {'is' if len(plain) == 1 else 'are'} in frame"))
    if shot.extras:
        parts.append(_sentence(f"Also in frame are {shot.extras}, unnamed background figures "
                               f"who look nothing like {_and([book_name(s) for s in shot.cast]) or 'anyone named'}"))

    # the action, then what is said
    if shot.action:
        parts.append(_sentence(shot.action))
    if shot.text:
        parts.append(f'Visible on-screen text reads "{shot.text}".')
    introduced: set[str] = set()
    for d in shot.dialogue:
        e = book.get(d.speaker) or {}
        name = e.get("name", d.speaker)
        voice = (e.get("voice") or "").strip()
        who = name
        if d.speaker not in introduced and voice:
            who = f"{name}, in a voice that is {voice},"
        introduced.add(d.speaker)
        if d.mode == "vo":
            verb = "says in an off-screen voiceover"
            if d.speaker in shot.cast:
                pron = (e.get("pronoun") or "their").strip()
                verb += f", {pron} lips staying closed"
        elif d.mode == "os":
            verb = "says from off-screen, outside the frame"
        else:
            verb = "says"
        delivery = (d.delivery or "").strip()
        line = f'{who} {verb}{", " + delivery if delivery else ""}: "{d.line.strip()}"'
        parts.append(line[0].upper() + line[1:])

    # the soundscape and music, integrated
    sound = (shot.sound or "").strip().rstrip(".")
    parts.append(_sentence(f"the sound is {sound}") if sound else
                 "The ambient sound of the place follows the action.")
    music = (shot.music or "").strip().rstrip(".")
    parts.append(_sentence(f"{music} plays underneath") if music else "There is no music.")
    return " ".join(p for p in parts if p)
