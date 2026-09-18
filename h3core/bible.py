"""
h3core.bible — loading series.json.

Keys starting with `_` in `subjects` and `locations` are comments/disabled
entries and are dropped, as are non-object values. Everything else is returned
as the raw dict: targets read the parts they understand.
"""

from __future__ import annotations

import json


def load_bible(path: str) -> dict:
    """Read and filter a bible. Raises FileNotFoundError, or ValueError (bad JSON,
    no subjects)."""
    with open(path, encoding="utf-8") as fh:
        bible = json.load(fh)
    bible["subjects"] = {k: v for k, v in bible.get("subjects", {}).items()
                         if not k.startswith("_") and isinstance(v, dict)}
    bible["locations"] = {k: v for k, v in bible.get("locations", {}).items()
                          if not k.startswith("_") and isinstance(v, dict)}
    if not bible["subjects"]:
        raise ValueError("the bible has no `subjects` block")
    return bible


def subject_ids(bible: dict) -> set[str]:
    return set(bible["subjects"])


def character_ids(bible: dict) -> set[str]:
    """Subjects that can speak (kind defaults to character)."""
    return {k for k, v in bible["subjects"].items()
            if v.get("kind", "character") == "character"}


def _num(value, default, kind):
    """value as `kind`, or the raw value if it doesn't convert (never raises:
    the target validates the bible and reports it in its own words)."""
    if value is None:
        value = default
    try:
        n = kind(value)
    except (TypeError, ValueError):
        return value
    if isinstance(n, float) and n.is_integer():
        return int(n)
    return n


def series_info(bible: dict) -> dict:
    """The episode-level picture format: {fps, width, height} (final pass)."""
    s = bible.get("series", {})
    return {"fps": _num(s.get("fps"), 24, float),
            "width": _num(s.get("width"), 1344, int),
            "height": _num(s.get("height"), 768, int)}
