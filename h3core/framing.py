"""
framing.py — a shot's `size:`: the names a script may use, what each frames,
and which of three families it belongs to.

A script writes any of the names below (case, spaces and hyphens don't
matter: `MCU`, `medium close-up` and `medium-close-up` are one size). The
shot keeps the name as written, so a shotlist built before a name existed
reads the same; everything that acts on the size goes through canonical().

The family decides what the targets do with the reference images (how much of
the location plate a shot keeps, the face or body panel of a sheet), so the
new in-between sizes reuse the three families' handling:

    close    ecu, cu, mcu
    medium   ms, cowboy, mws
    wide     fs, ws, ews

The words are what the prompt says. H3's own guide names most of these
framings; for the ones it doesn't (cowboy, full, extreme wide) the extent
says in plain words what the frame holds, so a model that doesn't know the
term still frames it.

Stdlib only.
"""
from __future__ import annotations

import re

# canonical size: (term, how much of the subject it holds or None, family)
SIZES = {
    "ecu":    ("extreme close-up", "a single detail filling the frame", "close"),
    "cu":     ("close-up", None, "close"),
    "mcu":    ("medium close-up", "from the chest up", "close"),
    "ms":     ("medium shot", None, "medium"),
    "cowboy": ("cowboy shot", "from mid-thigh up", "medium"),
    "mws":    ("medium-wide shot", "from the knees up", "medium"),
    "fs":     ("full shot", "from head to toe", "wide"),
    "ws":     ("wide shot", None, "wide"),
    "ews":    ("extreme wide shot", "small within the vastness of the setting", "wide"),
}

# every name a script may write, normalised (see _key), to its canonical size
ALIASES = {
    "ecu": "ecu", "xcu": "ecu", "extremecloseup": "ecu", "extremeclose": "ecu",
    "cu": "cu", "close": "cu", "closeup": "cu",
    "mcu": "mcu", "mediumcloseup": "mcu", "mediumclose": "mcu",
    "ms": "ms", "medium": "ms", "mediumshot": "ms", "mid": "ms", "midshot": "ms",
    "cowboy": "cowboy", "cowboyshot": "cowboy", "americanshot": "cowboy",
    "mws": "mws", "mediumwide": "mws", "mediumwideshot": "mws", "mls": "mws",
    "mediumlong": "mws", "mediumlongshot": "mws",
    "fs": "fs", "full": "fs", "fullshot": "fs",
    "ws": "ws", "wide": "ws", "wideshot": "ws", "ls": "ws", "long": "ws", "longshot": "ws",
    "ews": "ews", "xws": "ews", "els": "ews", "xls": "ews", "extremewide": "ews",
    "extremewideshot": "ews", "extremelong": "ews", "extremelongshot": "ews",
}

FAMILIES = ("close", "medium", "wide")


def _key(name: str) -> str:
    return re.sub(r"[^a-z]", "", (name or "").lower())


def canonical(name: str | None) -> str | None:
    """The canonical size a script name means ("Medium close-up" -> "mcu"),
    or None when it isn't one."""
    return ALIASES.get(_key(name or ""))


def is_size(name: str | None) -> bool:
    return canonical(name) is not None


def family(name: str | None, default: str = "medium") -> str:
    """close, medium or wide; `default` for a size that isn't one (an old or
    hand-edited shotlist)."""
    c = canonical(name)
    return SIZES[c][2] if c else default


def term(name: str | None, default: str = "medium shot") -> str:
    """The framing's name in words: "medium close-up"."""
    c = canonical(name)
    return SIZES[c][0] if c else default


def extent(name: str | None) -> str | None:
    """What the frame holds of the subject ("from the chest up"), or None for
    the three sizes whose name says it."""
    c = canonical(name)
    return SIZES[c][1] if c else None


def article(words: str) -> str:
    """'A medium shot', 'An extreme close-up'."""
    return ("An " if words[:1].lower() in "aeiou" else "A ") + words


def in_sizes(name: str | None, names) -> bool:
    """Whether `name` is one of `names` (a target's face_sizes, say), any
    spelling of either."""
    c = canonical(name)
    return c is not None and c in {canonical(n) for n in (names or ())}


def describe() -> str:
    """The sizes and their short names, for an error message."""
    short = {c: [a for a, v in ALIASES.items() if v == c and len(a) <= 6] for c in SIZES}
    return ", ".join(f"{SIZES[c][0]} ({'/'.join(dict.fromkeys([c] + short[c]))})"
                     for c in SIZES)
