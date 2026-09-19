"""
h3core.story — the script parser: epNN.md text + the series config's ids -> story IR.

    parse_story(text, subject_ids, character_ids, series) -> ir.Episode
    parse_script(text, subject_ids, character_ids)        -> dict (legacy shape)

`parse_script` is the original parser and still returns the dict h3align and
the H3 compile code were written against. `parse_story` runs the same parser,
records each sequence's header line and each shot's line span, and converts the
result into the IR. The format itself is docs/AUTHORING.md.

Validation here is only what the script alone can decide (unknown names, bad
windows, malformed lines). Anything that depends on a model -- frame limits,
reference slots, audio policies -- is the target's job.
"""

from __future__ import annotations

import re

from .ir import Episode, Line, Sequence, Shot
from .speech import SPEECH_RATE

META_KEYS = {"who", "cast", "with", "props", "size", "audio", "dur", "duration",
             "camera", "sound", "music", "policy", "continuous", "text",
             "pace", "plate", "retention", "model", "lora", "steps", "extras",
             "target", "profile"}
SIZES = {"close", "cu", "medium", "ms", "wide", "ws"}

# Voice-only delivery markers. A speaker tagged with one of these is NOT added
# to the visible cast: they cost no reference slot and are never drawn. Without
# this, a phone voice from another house burns a slot and gets rendered into
# frame as a full character.
VO_TOKENS = {"vo", "voiceover", "voover"}
OS_TOKENS = {"os", "offscreen"}

# The script's `retention:` words -> the IR's neutral `preserve`.
PRESERVE = {"fully_copy": "strict", "partially_copy": "loose", "reference": "style"}


class ScriptError(Exception):
    def __init__(self, line_no: int, line: str, msg: str):
        super().__init__(f"line {line_no}: {msg}\n    | {line.strip()}")


def _norm_token(tok: str) -> str:
    return re.sub(r"[^a-z]", "", tok.lower())


def split_parenthetical(text: str) -> tuple[str, str]:
    """Return (mode, delivery) from a dialogue parenthetical.

    mode is "vo", "os" or "" (on screen). Everything that is not a voice
    marker stays as the delivery direction, so `(V.O., into phone)` yields
    ("vo", "into phone").
    """
    mode, keep = "", []
    for part in (p.strip() for p in (text or "").split(",")):
        if not part:
            continue
        t = _norm_token(part)
        if t in VO_TOKENS:
            mode = "vo"
        elif t in OS_TOKENS:
            mode = mode or "os"
        else:
            keep.append(part)
    return mode, ", ".join(keep)


# ---------------------------------------------------------------------------
# The parser
# ---------------------------------------------------------------------------

def _parse(text: str, subject_ids: set[str],
           character_ids: set[str]) -> tuple[dict, list[int], dict[str, tuple[int, int]]]:
    """(episode dict, header line per sequence, shot id -> (first, last) line).

    A shot's span runs from its `##` header to the last line that belongs to it
    (meta, dialogue or action); blank and comment lines after that are not part
    of it. Line numbers are 1-based, as in error messages.
    """
    ep = {"id": None, "title": "", "sequences": []}
    seq = None
    shot = None
    upper = {c.upper(): c for c in character_ids}
    first_line: dict[str, int] = {}     # shot id -> line it was first used on
    seq_lines: list[int] = []
    spans: dict[str, tuple[int, int]] = {}

    def close_shot():
        nonlocal shot
        if shot is not None:
            shot["action"] = " ".join(shot["_action"]).strip()
            del shot["_action"]
            if not shot["action"] and not shot["dialogue"]:
                raise ScriptError(shot["_line"], f"## {shot['id']}",
                                  f"shot '{shot['id']}' has no action and no dialogue")
            spans[shot["id"]] = (shot.pop("_line"), shot.pop("_end"))
            seq["shots"].append(shot)
            shot = None

    for n, raw in enumerate(text.splitlines(), start=1):
        line = raw.rstrip()
        if not line.strip() or line.strip().startswith("//"):
            continue

        # ---- episode header ---------------------------------------------
        if line.startswith("= "):
            parts = line[2:].split(None, 1)
            ep["id"] = parts[0]
            ep["title"] = parts[1].strip() if len(parts) > 1 else ""
            continue

        # ---- sequence ----------------------------------------------------
        if line.startswith("# ") and not line.startswith("## "):
            close_shot()
            parts = line[2:].split()
            if len(parts) < 2:
                raise ScriptError(n, line, "sequence needs an id and a location: `# sq01 street`")
            seq = {"id": parts[0], "location_key": parts[1],
                   "continuous": False, "shots": []}
            ep["sequences"].append(seq)
            seq_lines.append(n)
            continue

        # ---- shot --------------------------------------------------------
        if line.startswith("## "):
            if seq is None:
                raise ScriptError(n, line, "shot appears before any `# sequence`")
            close_shot()
            sid = line[3:].strip().split()[0]
            # Renders, takes, overrides and the cut are all keyed by shot id,
            # so a repeat (even in another sequence) would share one render
            # folder.
            if sid in first_line:
                raise ScriptError(n, line, f"shot id '{sid}' is already used on line "
                                           f"{first_line[sid]}; shot ids must be unique "
                                           f"in an episode")
            first_line[sid] = n
            shot = {"id": sid, "cast": [], "props": [], "size": "medium",
                    "dialogue": [], "_action": [], "_line": n, "_end": n}
            continue

        if seq is None:
            raise ScriptError(n, line, "content before the first `# sequence`")
        if shot is not None:
            shot["_end"] = n

        # ---- dialogue: NAME: line   /   NAME (delivery): line -------------
        m = re.match(r"^([A-Z][A-Z0-9_ '\-]*?)\s*(?:\(([^)]*)\))?\s*:\s*(.+)$", line)
        if m:
            name = m.group(1).strip()
            if name.upper() not in upper:
                # An ALL-CAPS "NAME:" line is unambiguously an attempt at
                # dialogue. Falling through would silently fold it into the
                # action prose, so a typo'd or non-speaking name must be caught
                # here rather than shipped into a prompt as narration.
                lower = name.lower()
                if lower in subject_ids:
                    raise ScriptError(
                        n, line,
                        f"'{lower}' is a {'prop' if lower not in upper.values() else 'subject'} "
                        f"in series.json, not a character — only characters can speak. "
                        f"Change its kind to 'character', or write this as action.")
                raise ScriptError(
                    n, line,
                    f"'{name}' is not a character in series.json "
                    f"({', '.join(sorted(upper.values()))}). Typo, or write it as action.")
        if m and m.group(1).strip().upper() in upper:
            if shot is None:
                raise ScriptError(n, line, "dialogue outside a `## shot`")
            who = upper[m.group(1).strip().upper()]
            mode, delivery = split_parenthetical(m.group(2))
            shot["dialogue"].append({
                "who": who, "mode": mode,
                "delivery": delivery,
                "line": m.group(3).strip(),
            })
            # Only an on-screen speaker joins the visible cast.
            if not mode and who not in shot["cast"]:
                shot["cast"].append(who)
            continue

        # ---- key: value --------------------------------------------------
        m = re.match(r"^([a-z_]+)\s*:\s*(.*)$", line)
        if m and m.group(1) in META_KEYS:
            key, val = m.group(1), m.group(2).strip()
            target = shot if shot is not None else seq

            if key == "continuous":
                if shot is not None:
                    raise ScriptError(n, line, "`continuous:` belongs under `# sequence`, not a shot")
                seq["continuous"] = val.lower() in ("yes", "true", "1", "on")
            elif key in ("who", "cast", "with", "props"):
                if shot is None:
                    raise ScriptError(n, line, f"`{key}:` outside a `## shot`")
                names = [c.strip() for c in val.split(",") if c.strip()]
                for c in names:
                    if c not in subject_ids:
                        raise ScriptError(n, line,
                                          f"'{c}' is not in series.json's subjects "
                                          f"({', '.join(sorted(subject_ids))})")
                bucket = "cast" if key in ("who", "cast") else "props"
                merged: list[str] = []
                for c in names + shot[bucket]:
                    if c not in merged:
                        merged.append(c)
                shot[bucket] = merged
            elif key == "size":
                if val.lower() not in SIZES:
                    raise ScriptError(n, line, f"size '{val}' must be one of {sorted(SIZES)}")
                shot["size"] = val.lower()
            elif key == "audio":
                m2 = re.match(r"^([\d.]+)\s*-\s*([\d.]+)$", val)
                if not m2:
                    raise ScriptError(n, line, "audio window must look like `3.10-7.40`")
                a, b = float(m2.group(1)), float(m2.group(2))
                if b <= a:
                    raise ScriptError(n, line, f"audio window ends ({b}) before it starts ({a})")
                shot["audio_in"], shot["audio_out"] = a, b
            elif key in ("dur", "duration"):
                v = val.strip().lower()
                if v == "auto":
                    shot["duration_auto"] = True
                elif v == "model" or v.startswith("model "):
                    shot["duration_model"] = _model_clamp(v[len("model"):].strip(), n, line)
                else:
                    try:
                        shot["duration"] = float(val)
                    except ValueError:
                        raise ScriptError(
                            n, line,
                            f"duration '{val}' is not a number, `auto` or `model`")
            elif key == "pace":
                if val.strip().lower() not in SPEECH_RATE:
                    raise ScriptError(n, line,
                                      f"pace '{val}' must be one of "
                                      f"{sorted(SPEECH_RATE)}")
                shot["pace"] = val.strip().lower()
            else:
                target[key] = val
            continue

        # ---- action prose -------------------------------------------------
        if shot is None:
            if seq is not None and not seq["shots"]:
                continue          # scene-setting prose under a sequence header
            raise ScriptError(n, line, "action text outside a `## shot`")
        shot["_action"].append(line.strip())

    close_shot()
    if not ep["id"]:
        raise ScriptError(1, "", "script needs an episode header: `= ep01  Title`")
    if not ep["sequences"]:
        raise ScriptError(1, "", "script has no sequences")
    return ep, seq_lines, spans


def _model_clamp(spec: str, n: int, line: str) -> dict:
    """`dur: model`'s optional clamp, `3-8` (seconds): {"min", "max"}, or {}."""
    if not spec:
        return {}
    m = re.match(r"^(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)$", spec)
    if not m:
        raise ScriptError(n, line, f"`dur: model {spec}`: the clamp must look like "
                                   f"`dur: model 3-8` (min-max seconds)")
    lo, hi = float(m.group(1)), float(m.group(2))
    if lo <= 0 or hi <= lo:
        raise ScriptError(n, line, f"`dur: model {spec}`: needs 0 < min < max seconds")
    return {"min": lo, "max": hi}


def parse_script(text: str, subject_ids: set[str], character_ids: set[str]) -> dict:
    """Parse the screenplay-flavoured script into an episode structure (the
    legacy dict: what h3align reads)."""
    return _parse(text, subject_ids, character_ids)[0]


# ---------------------------------------------------------------------------
# Parser dict -> IR
# ---------------------------------------------------------------------------

def _overrides(d: dict, unparsed: dict) -> dict:
    """`model:` / `lora:` / `steps:` as written. An empty value means "not set",
    as it always has. A steps value that isn't an integer goes to `unparsed`."""
    out = {"model": d.get("model") or None, "lora": d.get("lora") or None, "steps": None}
    if "steps" in d:
        try:
            out["steps"] = int(d["steps"])
        except (TypeError, ValueError):
            unparsed["steps"] = d["steps"]
    return out


def _shot_ir(ep_id: str, seq: dict, sh: dict, span: tuple[int, int]) -> Shot:
    unparsed: dict = {}
    if "audio_in" in sh:
        timing = {"audio_in": sh["audio_in"], "audio_out": sh["audio_out"]}
    elif sh.get("duration_model") is not None:
        timing = {"model": True, **sh["duration_model"]}
    elif sh.get("duration_auto"):
        timing = {"auto": True}
    elif "duration" in sh:
        timing = {"seconds": sh["duration"]}
    else:
        timing = None
    ret = (sh.get("retention") or "").strip().lower()
    preserve = PRESERVE.get(ret)
    if ret and preserve is None:
        unparsed["preserve"] = sh["retention"]
    overrides = _overrides(sh, unparsed)
    return Shot(
        id=sh["id"], cast=list(sh["cast"]), props=list(sh["props"]),
        plate=sh.get("plate") or seq["location_key"], size=sh["size"],
        camera=sh.get("camera") or None, action=sh["action"],
        dialogue=[Line(speaker=d["who"], mode=d["mode"] or "on",
                       delivery=d["delivery"], line=d["line"]) for d in sh["dialogue"]],
        sound=sh.get("sound") or None, music=sh.get("music") or None,
        extras=sh.get("extras") or None, text=sh.get("text") or None,
        timing=timing, pace=sh.get("pace"), audio=sh.get("policy") or None,
        preserve=preserve, seed_key=f"{ep_id}/{seq['id']}/{sh['id']}",
        overrides=overrides, source={"line": span[0], "end_line": span[1]},
        unparsed=unparsed, target=sh.get("target") or None,
        profile=sh.get("profile") or None)


def episode_from_parsed(ep: dict, seq_lines: list[int],
                        spans: dict[str, tuple[int, int]],
                        series: dict | None = None) -> Episode:
    sequences = []
    for seq, line in zip(ep["sequences"], seq_lines):
        unparsed: dict = {}
        overrides = _overrides(seq, unparsed)
        sequences.append(Sequence(
            id=seq["id"], location=seq["location_key"], continuous=seq["continuous"],
            overrides=overrides, source={"line": line},
            shots=[_shot_ir(ep["id"], seq, sh, spans[sh["id"]]) for sh in seq["shots"]],
            unparsed=unparsed, target=seq.get("target") or None,
            profile=seq.get("profile") or None))
    return Episode(id=ep["id"], title=ep["title"], series=dict(series or {}),
                   sequences=sequences)


def parse_story(text: str, subject_ids: set[str], character_ids: set[str],
                series: dict | None = None) -> Episode:
    """Parse a script into the story IR. `series` ({fps, width, height}, from
    series_config.series_info) is carried on the episode header."""
    ep, seq_lines, spans = _parse(text, subject_ids, character_ids)
    return episode_from_parsed(ep, seq_lines, spans, series)
