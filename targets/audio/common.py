"""
What every audio target shares (targets/audio/<id>/): the wording of a voice
reference, and the line the voice is asked to say.

A voice ref (`voice:<subject>`) used to be import-only: someone recorded a
person and dropped the wav in. An audio target generates it instead, as an
image target generates a character sheet. The brief is written once, here, so
switching the voice target changes the model, not the brief:

    A clean voice recording of one speaker ... <Name> is <design>.
    Voice: <the series config's `voice` line>. <Name> says: "<line>"

`line` is what the voice should say. The pipeline picks the character's
longest line in this episode's script (`longest_line`), so the sample is in
the character's own words and long enough to carry an identity; a character
with no dialogue yet gets NEUTRAL_LINE. The take's sidecar records the
sentence (`line`), so a candidate can be judged against what it was asked to
say.

Stdlib only.
"""
from __future__ import annotations

# A character who says nothing in this episode still needs something to say:
# one sentence, plain words, a wide spread of vowels and consonants.
NEUTRAL_LINE = ("Hello — this is how I sound when I am just talking, calmly and "
                "clearly, one sentence after another.")

# What a voice sample must not be. An audio target with a `negative` widget
# takes it from its preset; this is the wording they share.
NEGATIVE = ("music, song, singing, instrumental, score, crowd, applause, second voice, "
            "noise, static, hum, distortion, clipping, heavy reverb, echo, silence")


def _clean(text: str) -> str:
    return " ".join((text or "").split())


def _sentence(text: str) -> str:
    text = _clean(text)
    if not text:
        return ""
    text = text[0].upper() + text[1:]
    return text if text[-1] in ".!?\"'" else text + "."


def longest_line(story, subject: str) -> str | None:
    """The longest thing `subject` says anywhere in an episode's story IR, as
    one line of text; None when they say nothing (or there is no story)."""
    best = ""
    for sq in (story.sequences if story is not None else []):
        for sh in sq.shots:
            for d in sh.dialogue:
                if d.speaker != subject:
                    continue
                text = _clean(getattr(d, "line", "") or "")
                if len(text) > len(best):
                    best = text
    return best or None


def voice_line(story, subject: str) -> tuple[str, str]:
    """(the line to say, where it came from: "script" | "neutral")."""
    line = longest_line(story, subject)
    return (line, "script") if line else (NEUTRAL_LINE, "neutral")


def voice_prompt(name: str, voice: str = "", design: str = "", line: str = "",
                 seconds: float | None = None) -> str:
    """The brief for one generated voice sample. `name` is the character's,
    `voice` the series config's `voice` line, `design` their design (its first
    sentence: what they look like matters only as far as it says how they
    sound), `line` what they should say."""
    line = _clean(line) or NEUTRAL_LINE
    parts = [f"A clean voice recording of one speaker, close to the microphone, in a "
             f"quiet room: one voice only, no music, no background noise, no other "
             f"voices, no sound effects."]
    who = _clean(design).rstrip(".")
    if who:
        # one sentence of the design: the rest is about how they look
        who = who.split(". ")[0]
        parts.append(_sentence(f"{name} is {who}"))
    if _clean(voice):
        parts.append(_sentence(f"Voice: {_clean(voice)}"))
    if seconds:
        # whole seconds: the brief says how long, not how long to the frame
        parts.append(f"About {round(float(seconds)):g} seconds of speech.")
    parts.append(f"{name} says: \"{line}\"")
    return " ".join(p for p in parts if p)
