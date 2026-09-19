"""
h3core.series_config — loading series.json, the series config.

Keys starting with `_` in `subjects` and `locations` are comments/disabled
entries and are dropped, as are non-object values. Everything else is returned
as the raw dict: targets read the parts they understand.
"""

from __future__ import annotations

import json


def load_series_config(path: str) -> dict:
    """Read and filter a series config. Raises FileNotFoundError, or ValueError (bad JSON,
    no subjects)."""
    with open(path, encoding="utf-8") as fh:
        series_cfg = json.load(fh)
    return series_config_from(series_cfg)


def series_config_from(series_cfg: dict) -> dict:
    """`load_series_config` for a series config already parsed (it is changed in
    place and returned). ValueError as there."""
    if not isinstance(series_cfg, dict):
        raise ValueError("series.json must be a JSON object")
    series_cfg["subjects"] = {k: v for k, v in series_cfg.get("subjects", {}).items()
                         if not k.startswith("_") and isinstance(v, dict)}
    series_cfg["locations"] = {k: v for k, v in series_cfg.get("locations", {}).items()
                          if not k.startswith("_") and isinstance(v, dict)}
    if not series_cfg["subjects"]:
        raise ValueError("series.json has no `subjects` block")
    return series_cfg


def subject_ids(series_cfg: dict) -> set[str]:
    return set(series_cfg["subjects"])


def character_ids(series_cfg: dict) -> set[str]:
    """Subjects that can speak (kind defaults to character)."""
    return {k for k, v in series_cfg["subjects"].items()
            if v.get("kind", "character") == "character"}


def _num(value, default, kind):
    """value as `kind`, or the raw value if it doesn't convert (never raises:
    the target validates the series config and reports it in its own words)."""
    if value is None:
        value = default
    try:
        n = kind(value)
    except (TypeError, ValueError):
        return value
    if isinstance(n, float) and n.is_integer():
        return int(n)
    return n


def series_info(series_cfg: dict) -> dict:
    """The episode-level picture format: {fps, width, height} (final pass)."""
    s = series_cfg.get("series", {})
    return {"fps": _num(s.get("fps"), 24, float),
            "width": _num(s.get("width"), 1344, int),
            "height": _num(s.get("height"), 768, int)}
