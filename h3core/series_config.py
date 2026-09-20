"""
h3core.series_config — loading series.json, the series config.

Keys starting with `_` in `subjects` and `locations` are comments/disabled
entries and are dropped, as are non-object values. Everything else is returned
as the raw dict: targets read the parts they understand.

A subject with `of: <another subject>` is a **variant** — the same character or
object in a different wardrobe or state, with its own reference sheet
(docs/PLAN.md, Phase 10). Loading resolves it into a complete ordinary subject
entry, so nothing downstream has to know variants exist: a target compiling a
shot whose cast names `gina_towel` reads the same shape of entry it reads for
`gina`, and gets the towel's sheet and the towel's words.

What a variant inherits, and what it must not:

    everything the base has     the variant's own keys win, so `voice`,
                                `voice_sample`, `pronoun` and `kind` come
                                across and one character keeps one voice
    `design`                    REQUIRED on the variant: inheriting it would
                                describe the old wardrobe while every check
                                passed
    `sheet`                     NEVER inherited: derived from the base's path
                                with the base id swapped for the variant's
                                (refs/gina/gina_sheet_4panel.png ->
                                refs/gina/gina_towel_sheet_4panel.png), or
                                stated by the variant. Inheriting it would
                                render the base's clothes.

`of` is kept on the resolved entry: `variant_of()` reads it, the parser uses it
to bind a base's dialogue to the variant on screen (h3core.story), and the refs
listing uses it to give a variant its own sheet but no voice of its own
(h3refs.series_refs).
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
    series_cfg["subjects"] = _resolve_variants(series_cfg["subjects"])
    return series_cfg


def _split_name(path: str) -> tuple[str, str]:
    """(everything up to and including the last separator, the last segment).
    Both separators whatever the platform: series config paths are written with
    '/' and read on Windows."""
    cut = max(path.rfind("/"), path.rfind("\\"))
    return path[:cut + 1], path[cut + 1:]


def _variant_sheet(vid: str, base_id: str, base_sheet: str) -> str:
    """A variant's own sheet path, from the base's: the base id in the file
    name swapped for the variant's, the folder left alone (one character's
    sheets stay together). ValueError when the name doesn't carry the id."""
    head, name = _split_name(base_sheet)
    if base_id not in name:
        raise ValueError(
            f"subject '{vid}' is a variant of '{base_id}', whose `sheet` "
            f"('{base_sheet}') doesn't have '{base_id}' in its file name, so the "
            f"variant's own path can't be derived from it: give '{vid}' a `sheet`")
    return head + name.replace(base_id, vid, 1)


def _resolve_variants(subjects: dict) -> dict:
    """Materialize every `of:` variant into a complete subject entry (see the
    module docstring). A new dict in the same order; entries without `of` are
    passed through untouched. ValueError names the subject at fault."""
    if not any(v.get("of") for v in subjects.values()):
        return subjects                     # the usual series: nothing to resolve
    out = {}
    for vid, entry in subjects.items():
        base_id = entry.get("of")
        if not base_id:
            out[vid] = entry
            continue
        if base_id == vid:
            raise ValueError(f"subject '{vid}' has `of: {vid}`: `of` names the subject this "
                             f"one is a variant OF, not itself")
        base = subjects.get(base_id)
        if base is None:
            raise ValueError(f"subject '{vid}' has `of: {base_id}`, which is not a subject in "
                             f"series.json ({', '.join(sorted(subjects))})")
        if base.get("of"):
            raise ValueError(f"subject '{vid}' is a variant of '{base_id}', which is itself a "
                             f"variant (of '{base['of']}'): a variant is always a variant of "
                             f"the original subject")
        if not str(entry.get("design") or "").strip():
            raise ValueError(f"subject '{vid}' is a variant of '{base_id}' and needs its own "
                             f"`design`: the wardrobe or state that makes it a variant is "
                             f"exactly what the description has to say")
        kind = entry.get("kind", base.get("kind", "character"))
        if kind != base.get("kind", "character"):
            raise ValueError(f"subject '{vid}' is a {kind} but '{base_id}', the subject it is "
                             f"a variant of, is a {base.get('kind', 'character')}: a variant "
                             f"is the same thing in a different state")
        merged = dict(base)
        merged.pop("sheet", None)           # never inherited: see the module docstring
        merged.update(entry)
        merged["kind"] = kind
        if not str(merged.get("sheet") or "").strip():
            base_sheet = str(base.get("sheet") or "").strip()
            if not base_sheet:
                raise ValueError(f"subject '{vid}' is a variant of '{base_id}', which has no "
                                 f"`sheet` to derive one from: give '{vid}' a `sheet`")
            merged["sheet"] = _variant_sheet(vid, base_id, base_sheet)
        out[vid] = merged
    return out


def variant_of(series_cfg: dict) -> dict:
    """{variant id: the id it is a variant of}; empty for a series with none.

    Safe on a series config that hasn't been through the loader (h3align reads
    the raw JSON, which still has its `_note` strings), so it never assumes an
    entry is an object."""
    return {k: v["of"] for k, v in series_cfg.get("subjects", {}).items()
            if isinstance(v, dict) and v.get("of")}


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
