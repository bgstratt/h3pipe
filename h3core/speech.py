"""
h3core.speech — how long dialogue takes to say, and how fast a window forces it.

A shot's length has to hold its dialogue at a rate a person can follow. A video
model will happily fit any line into any window by speeding the delivery up,
which is exactly the failure this measures: the words are all there and the
performance is gone.

Rates are syllables per second of actual speech. Conversational English runs
~4.0; brisk cartoon delivery ~4.4; deliberate patter ~5.4. Past ~5.8 it stops
reading as a person talking fast and starts reading as a sped-up recording.

Dialogue here is a list of dicts with at least "who" and "line" (the parser's
shape), so the same functions serve the IR adapter and h3align.
"""

from __future__ import annotations

import re

SPEECH_RATE = {"slow": 3.6, "normal": 4.4, "fast": 5.4}
RATE_CEILING = 5.8
GAP_SPEAKER = 0.30      # beat at each change of speaker
GAP_SAME = 0.15         # beat between two lines from the same mouth
HEAD_AIR = 0.35         # air before the first word, so the shot doesn't open mid-syllable
TAIL_AIR = 0.30         # air after the last, so the cut doesn't clip the tail

_VOWELS = "aeiouy"


def syllables(text: str) -> int:
    """Vowel-group count. Crude, but stable and within ~10% on dialogue."""
    total = 0
    for word in text.split():
        w = re.sub(r"[^a-z]", "", word.lower())
        if not w:
            continue
        n, prev_v = 0, False
        for ch in w:
            v = ch in _VOWELS
            if v and not prev_v:
                n += 1
            prev_v = v
        if w.endswith("e") and n > 1 and not w.endswith(("le", "ee", "ye")):
            n -= 1
        total += max(1, n)
    return total


def pacing(dialogue: list[dict]) -> tuple[int, float]:
    """(syllable load, seconds of non-speech the shot owes) for a shot."""
    if not dialogue:
        return 0, 0.0
    syl = sum(syllables(d["line"]) for d in dialogue)
    pauses = HEAD_AIR + TAIL_AIR
    for a, b in zip(dialogue, dialogue[1:]):
        pauses += GAP_SPEAKER if a["who"] != b["who"] else GAP_SAME
    return syl, pauses


def speech_seconds(dialogue: list[dict], pace: str = "normal") -> float:
    """How long this dialogue needs to land at the given pace."""
    syl, pauses = pacing(dialogue)
    if not syl:
        return 0.0
    return syl / SPEECH_RATE[pace] + pauses


def forced_rate(dialogue: list[dict], seconds: float) -> float:
    """The syllable rate the model is being asked to hit to fit this window."""
    syl, pauses = pacing(dialogue)
    if not syl:
        return 0.0
    return syl / max(0.10, seconds - pauses)
