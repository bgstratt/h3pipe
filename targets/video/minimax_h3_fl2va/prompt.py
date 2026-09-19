"""
The MiniMax H3 FL2VA prompt: H3's base-mode format (T2VA / I2VA / FL2VA /
L2VA in H3's prompt-writing spec), the Ref2VA six sections without the parts
that need reference pictures.

Ref2VA's `subject_definitions`, `summary` and `retention_analysis` exist to
tie <Subject N> to <Picture N> and say how much of each picture to keep. FL2VA
has no reference slots, so those sections go; what is left is the spec's three
core fields, in its order:

    integrated_multimodal_description: [Shot 1] <look>, <a size> of <where> ...
        <Name> is <design>. <action> The camera ... <Name>, with a voice that
        is <voice>, (S1) says, <delivery>: <d>[English] <line></d> ...

    overall_soundscape: ...

    non_diegetic_music: ...

Subjects are described in words from the series config's `design`; dialogue
keeps H3's speaker ids and <d>[English] ...</d> tags, voiceover its exact
phrase and the lips-closed clause. The location is scaled to the framing: all
of it on a wide, what is behind the subjects on a medium, a shallow slice on a
close-up.

Keyframes are known only at queue time (a keyframe may be picked after the
build), so the built prompt is the text-to-video one and `with_keyframes`
adds the spec's alignment line at render time: first frame only is I2VA
("For the target video, at 0.00 seconds into the target video, <Picture 1>
(from [Shot 1]) is fully referenced."), both is FL2VA ("How the reference
pictures align with the target video — Picture 1 ... 0.00-second mark ...;
Picture 2 ... S.SS-second mark"), last only is L2VA. The text encoder labels
the frames <Picture 1> and <Picture 2> in that order (comfy's minimax
tokenizer), which is what the line names. A sentence at the end of the
description says how the shot starts on / lands in them.

Stdlib only.
"""
from __future__ import annotations

import re

SIZE_WORDS = {"close": "close-up", "cu": "close-up", "medium": "medium shot",
              "ms": "medium shot", "wide": "wide shot", "ws": "wide shot"}
CLOSE = ("close", "cu")
WIDE = ("wide", "ws")

FIELDS = ("integrated_multimodal_description:", "overall_soundscape:", "non_diegetic_music:")


def _sentence(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    text = text[0].upper() + text[1:]
    return text if text[-1] in ".!?\"'" else text + "."


def _article(phrase: str) -> str:
    return ("an " if phrase[:1].lower() in "aeiou" else "a ") + phrase


def _and(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + (", and " if len(items) > 2 else " and ") + items[-1]


def build_prompt(shot, seq, series_cfg: dict, policy: str = "generate") -> str:
    """The three-field prompt for one shot (`shot` an h3core.ir.Shot, `seq`
    its ir.Sequence), for the audio policy it renders with: dub makes the
    anchored recording the whole soundtrack, dub_keep_foley lays the
    soundscape under it."""
    book = series_cfg.get("subjects", {})
    look = ((series_cfg.get("style") or {}).get("look") or "").strip().rstrip(".")
    loc = series_cfg.get("locations", {}).get(shot.plate or seq.location, {})
    env = (loc.get("description") or "").strip().rstrip(".")
    name = lambda s: (book.get(s) or {}).get("name", s)  # noqa: E731
    on_screen = list(shot.cast) + [p for p in shot.props if p not in shot.cast]

    # ---- [Shot 1]: style, framing, where -----------------------------------
    size = SIZE_WORDS.get(shot.size, "medium shot")
    who = _and([name(s) for s in on_screen])
    if shot.size in CLOSE:
        where = (f"{_article(size)} frames {who} against a shallow, out-of-focus slice of {env}"
                 if who else f"{_article(size)} holds on a detail of {env}")
    elif shot.size in WIDE:
        where = (f"{_article(size)} takes in {env}, with {who} in it" if who
                 else f"{_article(size)} takes in {env}")
    else:
        where = (f"{_article(size)} frames {who} against {env}"
                 if who else f"{_article(size)} frames {env}")
    if not env:
        where = f"{_article(size)} frames {who}" if who else _article(size)
    first = f"{look}, {where}" if look else where
    parts = [_sentence(first)]

    # subjects in words
    for s in on_screen:
        design = ((book.get(s) or {}).get("design") or "").strip().rstrip(".")
        if design:
            parts.append(_sentence(f"{name(s)} is {design}"))
    if shot.extras:
        named = _and([name(s) for s in shot.cast]) or "anyone named"
        parts.append(_sentence(f"Also in frame are {shot.extras.strip().rstrip('.')}: unnamed "
                               f"background figures who look nothing like {named} and are "
                               f"never duplicates of them"))

    if shot.action:
        parts.append(_sentence(shot.action))
    cam = (shot.camera or "").strip().rstrip(".")
    parts.append(f"The camera {cam}." if cam else
                 "The camera holds a static shot with fixed composition.")

    # ---- dialogue ------------------------------------------------------------
    spoken: list[str] = []
    for d in shot.dialogue:
        if d.speaker not in spoken:
            spoken.append(d.speaker)
    spk = {c: f"(S{i + 1})" for i, c in enumerate(spoken)}
    recorded = policy in ("dub", "dub_keep_foley")
    introduced: set[str] = set()
    for d in shot.dialogue:
        e = book.get(d.speaker) or {}
        voice = (e.get("voice") or "").strip()
        first_time = d.speaker not in introduced
        introduced.add(d.speaker)
        if d.speaker in on_screen:
            label = (f"{name(d.speaker)}, with a voice that is {voice}, {spk[d.speaker]}"
                     if first_time and voice else f"{name(d.speaker)} {spk[d.speaker]}")
        elif first_time and voice:
            label = f"An off-screen voice that is {voice} {spk[d.speaker]}"
        elif first_time:
            label = f"The off-screen voice of {name(d.speaker)} {spk[d.speaker]}"
        else:
            label = f"The same off-screen voice {spk[d.speaker]}"
        verb = {"vo": "says in an off-screen voiceover",
                "os": "says from off-screen, outside the frame"}.get(d.mode, "says")
        mods = (["in the recorded voice"] if recorded else []) + (
            [d.delivery.strip()] if (d.delivery or "").strip() else [])
        line = (f"{label} {verb}{''.join(', ' + m for m in mods)}: "
                f"<d>[English] {d.line}</d>")
        if d.mode == "vo" and d.speaker in on_screen:
            # the spec's clause, only when there is a mouth on screen to keep shut
            pron = (e.get("pronoun") or "their").strip()
            line += f" while {pron} lips remain completely closed."
        parts.append(line)
    if shot.text:
        parts.append(f'Visible on-screen text reads "{shot.text}".')

    # ---- soundscape and music ------------------------------------------------
    sound = (shot.sound or "").strip().rstrip(".") or (
        "continuous room tone and environmental ambience with synchronized physical "
        "action sounds")
    if policy == "dub":
        sound = ("the only audio is the recorded dialogue anchored from the start of the "
                 "video; no ambience, effects or music are added")
    elif policy == "dub_keep_foley":
        sound = f"{sound[0].lower() + sound[1:]}, laid under the recorded dialogue"
    music = (shot.music or "").strip().rstrip(".")

    return "\n\n".join([
        f"{FIELDS[0]} [Shot 1] " + " ".join(p for p in parts if p),
        f"{FIELDS[1]} {_sentence(sound)}",
        f"{FIELDS[2]} {_sentence(music) if music else 'N/A'}",
    ])


# ---------------------------------------------------------------------------
# keyframes: added at queue time, for the frames the render actually has
# ---------------------------------------------------------------------------

_ALIGN = ("How the reference pictures align with the target video", "For the target video, at ")
_LANDINGS = {
    ("first",): (" The shot develops forward from <Picture 1>, its first frame, keeping the "
                 "identity, clothing, colors, positions and composition it shows."),
    ("last",): (" Toward the end, everything settles into the exact poses, positions, camera "
                "angle, lighting and composition established by <Picture 1>, the last frame."),
    ("first", "last"): (" The shot begins in the position and framing established by Picture 1 "
                        "and, by the end of the shot, settles into the poses, spacing and "
                        "composition established by Picture 2."),
}


def alignment_line(roles: tuple[str, ...], frames: int, fps: float) -> str:
    """The spec's first line for the keyframes a render has ("" for none)."""
    end = f"{frames / fps:.2f}"
    if roles == ("first",):
        return ("For the target video, at 0.00 seconds into the target video, <Picture 1> "
                "(from [Shot 1]) is fully referenced.")
    if roles == ("first", "last"):
        return ("How the reference pictures align with the target video — Picture 1 (from "
                "Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from "
                f"Shot 1) aligns with the {end}-second mark of the target video.")
    if roles == ("last",):
        return ("How the reference pictures align with the target video — <Picture 1> (from "
                f"[Shot 1]) aligns with the {end}-second mark of the target video.")
    return ""


def without_keyframes(prompt: str) -> str:
    """`prompt` with what with_keyframes added taken out again."""
    text = prompt
    first, sep, rest = text.partition("\n\n")
    if sep and first.startswith(_ALIGN):
        text = rest
    for landing in _LANDINGS.values():
        text = text.replace(landing, "")
    return text


def with_keyframes(prompt: str, roles, frames: int, fps: float) -> str:
    """The prompt for a render whose keyframes are `roles` (a subset of
    first / last): the alignment line first, then a blank line, then the
    fields, with the landing sentence at the end of the description. Works on
    an overridden prompt too (the sentence then goes before
    overall_soundscape, or at the end). Idempotent: an earlier call's
    additions are replaced."""
    text = without_keyframes(prompt)
    key = tuple(r for r in ("first", "last") if r in set(roles or ()))
    if not key:
        return text
    landing = _LANDINGS[key]
    m = re.search(r"\n\n" + re.escape(FIELDS[1]), text)
    if m:
        body = text[:m.start()].rstrip() + landing + text[m.start():]
    else:
        body = text.rstrip() + landing
    return alignment_line(key, frames, fps) + "\n\n" + body
