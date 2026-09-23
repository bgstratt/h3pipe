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


def read_file(ep: str, file: str, path: str) -> Source:
    """A Source for a path given by name rather than found in the episode
    (h3align's `--script`, the series config it was pointed at). Its history
    copies still go to the episode's `_history/`."""
    with open(path, "rb") as fh:
        return Source(ep, file, path, fh.read())


def read_source(ep: str, file: str) -> Source:
    return read_file(ep, file, source_path(ep, file))


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


def script_spans(text: str, ids: tuple[set, set, dict] | None) -> tuple[dict, dict]:
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


def _ids(cfg: dict | None) -> tuple[set, set, dict] | None:
    """What the parser needs from a series config: the subject ids, the
    character ids and {variant: base} (`_parse`)."""
    from h3core.series_config import character_ids, subject_ids, variant_of
    if cfg is None:
        return None
    return subject_ids(cfg), character_ids(cfg), variant_of(cfg)


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
        from h3core.series_config import series_info
        subjects, chars, variants = _ids(cfg)
        story = parse_story(script_text, subjects, chars, series_info(cfg), variants)
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
    return save_history_bytes(ep, os.path.basename(src.path), src.data, keep)


def save_history_bytes(ep: str, name: str, data: bytes, keep: int = HISTORY_KEEP) -> str:
    """save_history for bytes already read: a copy named after `name` (also
    cut.json's, before a cut edit)."""
    d = history_dir(ep)
    os.makedirs(d, exist_ok=True)
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    dst, k = os.path.join(d, f"{name}.{stamp}"), 1
    while os.path.exists(dst):
        k += 1
        dst = os.path.join(d, f"{name}.{stamp}-{k}")
    with open(dst, "xb") as fh:
        fh.write(data)
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


def dump_series(raw: dict, like: str) -> str:
    """A series config as the editor writes it: 2-space indent, key order and
    non-ASCII text kept, a final newline if the file had one (h3promote's
    format, shared by every route that edits the series config)."""
    return json.dumps(raw, indent=2, ensure_ascii=False) + ("\n" if like.endswith("\n") else "")


def formatted(text: str) -> bool:
    """Whether a series config text is already what dump_series writes."""
    try:
        return dump_series(json.loads(text), text).rstrip("\n") == text.rstrip("\n")
    except ValueError:
        return False


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


# ---------------------------------------------------------------------------
# a new episode (docs/polish_Plan.md, "P5")
# ---------------------------------------------------------------------------

STARTER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "examples", "starter")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
SEP_RE = re.compile(r"[\\/]+")          # a config's paths may use either separator
# An episode named like one of these would be walked past by find_episodes (or,
# on Windows, is not a folder name at all), so it could never be opened again.
RESERVED = ({"refs", "renders", "renders_proxy", "shotlist", "views", "audio", "targets",
             "con", "prn", "aux", "nul"}
            | {f"com{n}" for n in range(1, 10)} | {f"lpt{n}" for n in range(1, 10)})


def check_name(name) -> str:
    """An episode folder name, or SourceError 400. It is also the script's stem
    (`ep02` -> `ep02.md`), which is what makes the episode discoverable."""
    if not isinstance(name, str) or not NAME_RE.match(name.strip()):
        raise SourceError(400, "the name must be a folder name: letters, digits, dot, dash "
                               "or underscore, starting with a letter or digit")
    name = name.strip()
    if name.lower() in RESERVED or name.endswith("."):
        raise SourceError(400, f"{name!r} can't be an episode folder name")
    return name


def episodes_under(parent: str) -> list[str]:
    """The episode folders directly under `parent`, by name, each with a series
    config of its own and a script."""
    out = []
    try:
        names = sorted(os.listdir(parent), key=str.lower)
    except OSError:
        return out
    for n in names:
        d = os.path.join(parent, n)
        if n.startswith((".", "_")) or not os.path.isdir(d):
            continue
        if os.path.isfile(os.path.join(d, "series.json")) and E.episode_script(d):
            out.append(d)
    return out


def refs_folder(ep: str, raw: dict) -> str | None:
    """Where this config's pictures go: the folder its first reference path
    names `refs` in. `../refs/...` (the shared layout) puts it beside the
    episode, `refs/...` inside it."""
    paths = [s.get("sheet") for s in (raw.get("subjects") or {}).values() if isinstance(s, dict)]
    paths += [loc.get("plate") for loc in (raw.get("locations") or {}).values()
              if isinstance(loc, dict)]
    for p in paths:
        if not isinstance(p, str) or not p:
            continue
        parts = [x for x in re.split(SEP_RE, p) if x not in ("", ".")]
        if "refs" in parts:
            return os.path.abspath(os.path.join(ep, *parts[:parts.index("refs") + 1]))
    return None


def _first(block: dict, kind: str | None = None):
    for key, val in (block or {}).items():
        if key.startswith("_") or not isinstance(val, dict):
            continue
        if kind is None or val.get("kind") == kind:
            return key, val
    return None, None


def skeleton_script(raw: dict, name: str, title: str) -> str | None:
    """One shot that builds, using this series' own first character and
    location -- a place to start writing, not a story. None when the config has
    no character or no location to name (then the caller ships the starter)."""
    who, subject = _first(raw.get("subjects") or {}, "character")
    where, _loc = _first(raw.get("locations") or {})
    if not who or not where:
        return None
    person = (subject or {}).get("name") or who
    person = person[:1].upper() + person[1:]         # it opens a sentence
    return (f"= {name}  {title}\n"
            f"\n"
            f"// A place to start: one shot that builds. Write the episode over it --\n"
            f"// docs/AUTHORING.md is the format, `python h3.py check` says what is wrong,\n"
            f"// and every subject and location comes from series.json by short name.\n"
            f"\n"
            f"# sq01  {where}\n"
            f"\n"
            f"## sh010\n"
            f"who: {who}\n"
            f"size: medium\n"
            f"dur: 3\n"
            f"{person} stands still, looking off to one side.\n"
            f"camera: holds static\n"
            f"sound: the room's own quiet\n")


def new_episode(parent: str, name: str, series_id: str | None = None,
                title: str | None = None) -> dict:
    """Make `<parent>/<name>/` with a series config and a script that build.

    The template is the newest episode already under `parent` -- its series
    config copied, so the cast, look and render profiles carry over, with a
    skeleton script to write into -- else the shipped `examples/starter` pair.
    Either way the new episode gets a series config of its own, which is what
    lets one episode add a character without touching its neighbours.

    `title` titles the new episode (the script's `= <id>  <title>` line).
    `series_id` and the series' own title are the series', so they are only
    written when this is the first episode: a next episode belongs to the
    series its neighbour already names.

    SourceError 400 for a name that isn't a folder name or a parent that isn't
    a folder, 409 when `<parent>/<name>` already exists. Returns
    {"ep", "template", "from", "files", "refs", "check", "episode"}."""
    name = check_name(name)
    if not parent or not isinstance(parent, str) or not os.path.isdir(parent):
        raise SourceError(400, f"no folder at {parent!r} to make the episode in")
    ep = os.path.join(os.path.abspath(parent), name)
    if os.path.exists(ep):
        raise SourceError(409, f"{ep} is already there")

    sibling = (episodes_under(parent) or [None])[-1]
    raw = _template_config(sibling, series_id, title) if sibling \
        else _template_config(STARTER, series_id, title, series=True)
    ep_title = title or raw["series"].get("title") or name
    script = None
    if sibling:
        _rename_track(raw, os.path.basename(os.path.normpath(sibling)), name)
        script = skeleton_script(raw, name, ep_title)
    template = "episode" if sibling and script else "starter"
    if script is None:
        if sibling:                                  # its config named nobody to write about
            raw = _template_config(STARTER, series_id, title, series=True)
            ep_title = title or raw["series"].get("title") or name
        script = _retitled(_read_text(os.path.join(STARTER, "ep01.md")), name, ep_title)

    os.makedirs(ep)
    try:
        atomic_write(os.path.join(ep, "series.json"), dump_series(raw, "\n").encode("utf-8"))
        atomic_write(os.path.join(ep, f"{name}.md"), normalize(script).encode("utf-8"))
        refs = refs_folder(ep, raw)
        if refs:
            os.makedirs(refs, exist_ok=True)
    except BaseException:
        for f in ("series.json", f"{name}.md"):
            try:
                os.remove(os.path.join(ep, f))
            except OSError:
                pass
        try:
            os.rmdir(ep)
        except OSError:
            pass
        raise
    return {"ep": ep, "template": template,
            "from": os.path.abspath(sibling) if template == "episode" else STARTER,
            "files": ["series.json", f"{name}.md"], "refs": refs,
            "check": check_text(ep, "script", script),
            "episode": E.episode_summary(ep)}


def _template_config(src: str, series_id: str | None, title: str | None,
                     series: bool = False) -> dict:
    """The template's series config. `series` (a first episode, so the series is
    being started here) lets the caller's id and title name the series too."""
    raw = json.loads(_read_text(os.path.join(src, "series.json")))
    if not isinstance(raw, dict) or not isinstance(raw.get("series"), dict):
        raise SourceError(400, f"{os.path.join(src, 'series.json')} is not a series config")
    if series and series_id:
        raw["series"]["id"] = series_id
    if series and title:
        raw["series"]["title"] = title
    return raw


def _read_text(path: str) -> str:
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError as e:
        raise SourceError(400, f"{path} can't be read: {e}") from None
    if data.startswith(BOM):
        data = data[len(BOM):]
    return normalize(data.decode("utf-8"))


def _retitled(script: str, name: str, title: str) -> str:
    """The template script's `= <id>  <title>` line, for the new episode."""
    lines = script.split("\n")
    for i, line in enumerate(lines):
        if line.startswith("="):
            lines[i] = f"= {name}  {title}".rstrip()
            break
    return "\n".join(lines)


def _rename_track(raw: dict, old: str, new: str) -> None:
    """A dialogue recording is per episode, so a copied config's track follows
    the name (`audio/ep01_dialogue.wav` -> `audio/ep02_dialogue.wav`). Only
    that: everything else in the config is the series', not the episode's."""
    audio = raw.get("audio")
    if not isinstance(audio, dict):
        return
    track = audio.get("track")
    if isinstance(track, str) and old and old in track:
        audio["track"] = track.replace(old, new)


def cmd_new(argv: list[str]) -> int:
    """`python h3.py new Shows\\ep02 [--title "..."] [--series-id ...]` — the CLI
    side of new_episode: the path's folder is the show, its name the episode."""
    args, title, series_id = [], None, None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--title", "--series-id") and i + 1 < len(argv):
            if a == "--title":
                title = argv[i + 1]
            else:
                series_id = argv[i + 1]
            i += 2
            continue
        if a.startswith("-"):
            print(f"  !! unknown flag {a}")
            return 2
        args.append(a)
        i += 1
    if len(args) != 1:
        print('  !! usage: python h3.py new <folder>\\<name> [--title "..."] [--series-id <id>]')
        return 2
    path = os.path.abspath(args[0])
    parent, name = os.path.dirname(path), os.path.basename(path)
    try:
        res = new_episode(parent, name, series_id=series_id, title=title)
    except SourceError as e:
        print(f"  !! {e}")
        return 1
    where = "the episode beside it" if res["template"] == "episode" else "the starter template"
    print(f"  -- {res['ep']}  (from {where})")
    for f in res["files"]:
        print(f"     {f}")
    if res["refs"]:
        print(f"  -- reference pictures go in {res['refs']}")
    check = res["check"]
    for e in check.get("errors") or []:
        print(f"  !! {e.get('message', e)}")
    for w in check.get("warnings") or []:
        print(f"  ~~ {w.get('message', w)}")
    print(f"  -- next: python h3.py build {res['ep']}")
    return 0 if check.get("ok", True) else 1
