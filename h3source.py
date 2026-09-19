#!/usr/bin/env python3
"""
h3source.py — an episode's two authored files, the script (epNN.md) and the
series config (series.json), as the editor reads, checks and saves them
(docs/API.md, "Phase 9a").

    read_source(ep, "script" | "series")      -> Source (text, hash, path, ...)
    check_text(ep, "script" | "series", text) -> {"ok", "errors", "warnings", "shots"}
    save_source(ep, file, text, base_hash)    -> {"hash", "check"}  (Conflict on a stale hash)

The check never writes: it parses the given text with the file on disk beside
it (h3core's parser and series config loader) and compiles the final pass the
way `h3build --check` does (h3build.compile_groups), collecting the same
errors and warnings with 1-based line numbers into the file they are about.

A save keeps the file as it was: its line endings (CRLF or LF, whichever it
had) and a UTF-8 BOM if it had one. The text on the wire always has LF line
endings and no BOM. Before writing, the old file is copied to
<ep>/_history/<name>.<YYYYmmdd-HHMMSS> (the newest HISTORY_KEEP per file are
kept), and the write is atomic (a temp file beside it, then os.replace).

Stdlib only.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import h3edit as E  # noqa: E402

FILES = ("script", "series")
HISTORY = "_history"
HISTORY_KEEP = 30
BOM = b"\xef\xbb\xbf"


class SourceError(Exception):
    """A request that can't be done: `status` is the HTTP status the editor
    answers with, `data` extra fields for its body."""

    def __init__(self, status: int, message: str, **data):
        super().__init__(message)
        self.status = status
        self.data = data


class Conflict(SourceError):
    """The file changed on disk since the caller read it (409)."""

    def __init__(self, src: "Source"):
        super().__init__(409, "changed on disk", file=src.file, hash=src.hash, text=src.text)


# ---------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------

class Source:
    """One authored file as it is on disk now."""

    def __init__(self, ep: str, file: str, path: str, data: bytes):
        self.ep, self.file, self.path, self.data = ep, file, path, data
        self.bom = data.startswith(BOM)
        body = data[len(BOM):] if self.bom else data
        try:
            raw = body.decode("utf-8")
        except UnicodeDecodeError as e:
            raise SourceError(500, f"{os.path.basename(path)} is not UTF-8 text "
                                   f"(byte {e.start}): the editor can't open it") from None
        # the line ending most of the file uses (a mixed file is made uniform)
        self.crlf = raw.count("\r\n") * 2 > raw.count("\n")
        self.text = normalize(raw)
        self.hash = hashlib.sha1(data).hexdigest()
        self.mtime = os.stat(path).st_mtime

    @property
    def rel(self) -> str:
        """The path relative to the episode, forward slashes (../series.json
        for a parent-folder series config)."""
        return E.rel(self.ep, self.path) or os.path.basename(self.path)

    def encode(self, text: str) -> bytes:
        """`text` (any line endings) as this file stores it."""
        text = normalize(text)
        if self.crlf:
            text = text.replace("\n", "\r\n")
        return (BOM if self.bom else b"") + text.encode("utf-8")


def normalize(text: str) -> str:
    """LF line endings, no BOM: the text as the editor handles it."""
    if text.startswith("\ufeff"):
        text = text[1:]
    return text.replace("\r\n", "\n").replace("\r", "\n")


def source_path(ep: str, file: str) -> str:
    """The episode's script or series config. SourceError 400 for another
    `file`, 404 when the episode has none (or can't tell which .md)."""
    if file not in FILES:
        raise SourceError(400, f"file must be 'script' or 'series', not {file!r}")
    if file == "script":
        p = E.episode_script(ep)
        if not p:
            raise SourceError(404, f"can't tell which .md in {ep} is the script")
    else:
        p = E.episode_series_config(ep)
        if not p:
            raise SourceError(404, f"no series.json in {ep} or its parent folder")
    return p


def read_source(ep: str, file: str) -> Source:
    p = source_path(ep, file)
    with open(p, "rb") as fh:
        return Source(ep, file, p, fh.read())


def source_json(src: Source, hash_only: bool = False) -> dict:
    """GET /h3pipe/source."""
    if hash_only:
        return {"file": src.file, "hash": src.hash, "mtime": src.mtime}
    out = {"file": src.file, "path": src.rel, "text": src.text, "hash": src.hash,
           "mtime": src.mtime}
    if src.file == "script":
        out["shots"] = script_shots(src.text, src.ep)
    return out


# ---------------------------------------------------------------------------
# checking
# ---------------------------------------------------------------------------

def json_key_lines(text: str) -> dict[tuple, int]:
    """{path of every object key and array item: its 1-based line} of a JSON
    text, e.g. ("series", "steps") -> 7. Best effort: a text that isn't JSON
    gives what was read before the error."""
    out: dict[tuple, int] = {}
    stack: list[list] = []            # [kind "{" | "[", key or index, expecting a key]
    line, i, n = 1, 0, len(text)

    def path() -> tuple:
        return tuple(fr[1] for fr in stack
                     if not (fr[1] is None or (fr[0] == "[" and fr[1] < 0)))

    def value_start():
        if stack and stack[-1][0] == "[":
            stack[-1][1] += 1
            out[path()] = line

    while i < n:
        c = text[i]
        if c == "\n":
            line += 1
            i += 1
        elif c in " \t\r:":
            i += 1
        elif c == ",":
            if stack and stack[-1][0] == "{":
                stack[-1][2] = True
            i += 1
        elif c == '"':
            j = i + 1
            while j < n and text[j] not in '"\n':
                j += 2 if text[j] == "\\" else 1
            s = text[i:j + 1]
            if stack and stack[-1][0] == "{" and stack[-1][2]:
                try:
                    key = json.loads(s)
                except ValueError:
                    key = s.strip('"')
                stack[-1][1], stack[-1][2] = key, False
                out[path()] = line
            else:
                value_start()
            i = j + 1
        elif c in "{[":
            value_start()
            stack.append([c, None if c == "{" else -1, c == "{"])
            i += 1
        elif c in "}]":
            if stack:
                stack.pop()
            i += 1
        else:                         # a number, true, false, null
            value_start()
            while i < n and text[i] not in ",]}\n \t\r":
                i += 1
    return out


def _series_line(lines: dict[tuple, int], message: str) -> int | None:
    """The series config line a message is about, when it names a key:
    profile 'x', refs.target, series.steps, ...; None otherwise."""
    for rx, head in ((r"profile '([^']+)'", "profiles"), (r"\brefs\.(\w+)", "refs"),
                     (r"\bseries\.(\w+)", "series"), (r"\bproxy\.(\w+)", "proxy")):
        m = re.search(rx, message)
        if m and (head, m.group(1)) in lines:
            return lines[(head, m.group(1))]
    for key in ("subjects", "locations", "series", "profiles", "refs"):
        if f"`{key}`" in message and (key,) in lines:
            return lines[(key,)]
    return None


# a message about one shot: which of its lines to point at, by the words in it
_SHOT_KEY_WORDS = (("overrides model", "model"), ("overrides lora", "lora"),
                   ("overrides steps", "steps"), ("target '", "target"),
                   ("profile '", "profile"), ("`dur:", "dur"), ("retention", "retention"), ("policy", "policy"), ("location '", "plate"),
                   ("subjects", "with"), ("speakers", None))


def _shot_line(text: str, span: tuple[int, int], message: str) -> int:
    """The line in a shot's span a message is most likely about (a `target:`
    line for an unknown target, ...), else the shot's `##` line."""
    lines = text.split("\n")
    first, last = span
    for words, key in _SHOT_KEY_WORDS:
        if key and words in message:
            for n in range(last, first - 1, -1):
                raw = lines[n - 1] if n - 1 < len(lines) else ""
                m = re.match(r"^([a-z_]+)\s*:", raw)
                if m and (m.group(1) == key or (key == "dur" and m.group(1) == "duration")):
                    return n
    return first


def script_spans(text: str, ids: tuple[set, set] | None) -> tuple[dict, dict]:
    """({shot id: (line, end_line)}, {sequence id: line}) from the parser;
    ({}, {}) when the text doesn't parse."""
    from h3core.story import ScriptError, _parse
    if ids is None:
        return {}, {}
    try:
        ep, seq_lines, spans = _parse(text, *ids)
    except ScriptError:
        return {}, {}
    return spans, {sq["id"]: ln for sq, ln in zip(ep["sequences"], seq_lines)}


def _ids(cfg: dict | None) -> tuple[set, set] | None:
    from h3core.series_config import character_ids, subject_ids
    if cfg is None:
        return None
    return subject_ids(cfg), character_ids(cfg)


def _load_cfg(ep: str) -> dict | None:
    """The series config on disk, filtered (None if it doesn't load)."""
    from h3core.series_config import load_series_config
    p = E.episode_series_config(ep)
    try:
        return load_series_config(p) if p else None
    except (OSError, ValueError):
        return None


def script_shots(text: str, ep: str, cfg: dict | None = None) -> list[dict]:
    """[{"id", "line", "end_line"}] of a script text, in script order (empty
    when it doesn't parse against the series config)."""
    spans, _ = script_spans(text, _ids(cfg if cfg is not None else _load_cfg(ep)))
    return [{"id": k, "line": a, "end_line": b}
            for k, (a, b) in sorted(spans.items(), key=lambda kv: kv[1][0])]


def check_text(ep: str, file: str, text: str) -> dict:
    """POST /h3pipe/source/check: `text` as the episode's `file`, checked
    beside the other file on disk, writing nothing. A script is parsed against
    the series config and compiled; a series config must be JSON and load,
    then the script on disk is checked with it."""
    from h3core.series_config import series_config_from
    from h3core.story import ScriptError, parse_story
    import h3build as B
    import targets as TG

    if file not in FILES:
        raise SourceError(400, f"file must be 'script' or 'series', not {file!r}")
    text = normalize(text)
    errors: list[dict] = []
    warnings: list[dict] = []
    if file == "script":
        script_text = text
        try:
            series_text = read_source(ep, "series").text
        except SourceError as e:
            return {"ok": False, "errors": [{"file": "series", "line": None,
                                             "message": str(e)}],
                    "warnings": [], "shots": []}
    else:
        series_text = text
        try:
            script_text = read_source(ep, "script").text
        except SourceError:
            script_text = None

    def result(shots=()):
        return {"ok": not errors, "errors": errors, "warnings": warnings,
                "shots": list(shots) if file == "script" else []}

    # 1. the series config: JSON, then the loader
    try:
        raw = json.loads(series_text)
    except json.JSONDecodeError as e:
        errors.append({"file": "series", "line": e.lineno, "col": e.colno,
                       "message": f"series.json is not valid JSON: {e.msg}"})
        return result()
    key_lines = json_key_lines(series_text)
    try:
        cfg = series_config_from(raw)
    except ValueError as e:
        errors.append({"file": "series", "line": _series_line(key_lines, str(e)),
                       "message": str(e)})
        return result()
    if script_text is None:
        return result()

    # 2. the script
    spans, seq_lines = script_spans(script_text, _ids(cfg))
    shots = [{"id": k, "line": a, "end_line": b}
             for k, (a, b) in sorted(spans.items(), key=lambda kv: kv[1][0])]
    try:
        from h3core.series_config import character_ids, series_info, subject_ids
        story = parse_story(script_text, subject_ids(cfg), character_ids(cfg), series_info(cfg))
    except ScriptError as e:
        errors.append({"file": "script", "line": e.line_no, "message": e.msg})
        return result(shots)

    def where(message: str) -> dict:
        """{"file", "line"} for a build message: a shot's or sequence's line
        in the script, else a key's in the series config, else no line."""
        m = re.match(r"^(?:shot\s+)?([A-Za-z0-9_\-.]+)\s*[:\s]", message)
        if m and m.group(1) in spans:
            return {"file": "script", "line": _shot_line(script_text, spans[m.group(1)],
                                                         message)}
        m = re.match(r"^sequence\s+([A-Za-z0-9_\-.]+)", message)
        if m and m.group(1) in seq_lines:
            return {"file": "script", "line": seq_lines[m.group(1)]}
        line = _series_line(key_lines, message)
        if line is not None or "series.json" in message or message.startswith("series"):
            return {"file": "series", "line": line}
        return {"file": "script", "line": None}

    # 3. the build: every target the episode uses compiles its shots (final
    # pass, as `h3.py check` runs it)
    try:
        built = B.compile_groups(story, cfg, "final")
    except (ValueError, KeyError, TG.TargetError) as e:
        msg = e.args[0] if isinstance(e, KeyError) and e.args else str(e)
        errors.append(dict(where(str(msg)), message=str(msg)))
        return result(shots)
    seen = set()
    for _, _, rep in built:
        for w in rep.get("warnings", []):
            if w not in seen:
                seen.add(w)
                warnings.append(dict(where(w), message=w))
    return result(shots)


# ---------------------------------------------------------------------------
# saving
# ---------------------------------------------------------------------------

def history_dir(ep: str) -> str:
    return os.path.join(ep, HISTORY)


def save_history(ep: str, src: Source, keep: int = HISTORY_KEEP) -> str:
    """Copy the file as it is now to <ep>/_history/<name>.<YYYYmmdd-HHMMSS>
    (-2, -3... when a copy that second exists), then keep the newest `keep`
    copies of that name. Returns the copy's path."""
    d = history_dir(ep)
    os.makedirs(d, exist_ok=True)
    name = os.path.basename(src.path)
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    dst, k = os.path.join(d, f"{name}.{stamp}"), 1
    while os.path.exists(dst):
        k += 1
        dst = os.path.join(d, f"{name}.{stamp}-{k}")
    with open(dst, "xb") as fh:
        fh.write(src.data)
    prune_history(ep, name, keep)
    return dst


def history(ep: str, name: str) -> list[str]:
    """The history copies of file `name`, oldest first."""
    d = history_dir(ep)
    if not os.path.isdir(d):
        return []
    rx = re.compile(re.escape(name) + r"\.(\d{8}-\d{6})(?:-(\d+))?$")
    found = []
    for f in os.listdir(d):
        m = rx.match(f)
        if m:
            found.append(((m.group(1), int(m.group(2) or 1)), os.path.join(d, f)))
    return [p for _, p in sorted(found)]


def prune_history(ep: str, name: str, keep: int = HISTORY_KEEP) -> None:
    for p in history(ep, name)[:-keep] if keep > 0 else []:
        try:
            os.remove(p)
        except OSError:
            pass


def atomic_write(path: str, data: bytes) -> None:
    """Write `data` to `path` through a temp file beside it and os.replace. A
    file held open elsewhere (Windows) is retried briefly."""
    d = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix=".tmp_", suffix=os.path.splitext(path)[1], dir=d)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        for attempt in range(5):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.1 * (attempt + 1))
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def write_source(src: Source, text: str) -> str:
    """Write `text` over `src` (history copy first, atomic, the file's line
    endings and BOM kept). Nothing is written when the bytes wouldn't
    change. Returns the new hash."""
    data = src.encode(text)
    if data == src.data:
        return src.hash
    save_history(src.ep, src)
    atomic_write(src.path, data)
    return hashlib.sha1(data).hexdigest()


def save_source(ep: str, file: str, text: str, base_hash: str | None) -> dict:
    """PUT /h3pipe/source without the rebuild: Conflict (409) when the file's
    hash isn't `base_hash`; SourceError 400 for a series config that isn't
    JSON (with its line/col). A script with errors is saved. Returns
    {"hash", "check", "changed"}."""
    if not isinstance(text, str):
        raise SourceError(400, "text must be the file's text")
    src = read_source(ep, file)
    if base_hash != src.hash:
        raise Conflict(src)
    text = normalize(text)
    if file == "series":
        try:
            json.loads(text)
        except json.JSONDecodeError as e:
            raise SourceError(400, f"series.json is not valid JSON: {e.msg} "
                                   f"(line {e.lineno}, column {e.colno}); not saved",
                              line=e.lineno, col=e.colno) from None
    new_hash = write_source(src, text)
    return {"hash": new_hash, "check": check_text(ep, file, text),
            "changed": new_hash != src.hash}
