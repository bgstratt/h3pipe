#!/usr/bin/env python3
"""
h3issues.py — a pass's issues: the notepad you fill while watching a proxy
(docs/polish_Plan.md, "P10").

The loop it serves: render a cheap proxy to see structure, watch it, jot what is
wrong with each bad shot, hand the lot to an assistant, fix the script or the
series config, render again. The notes are **spent** at that point.

    add(ep, pass_, shot, note)      -> the issue, snapshotted
    list_issues(ep, pass_)          -> the issues, newest last, each with `addressed`
    export(ep, pass_)               -> markdown to paste into an assistant
    resolve(ep, ids) / clear(ep)    -> they are gone; the file is removed when empty

Everything lives in `<ep>/_issues.json`, which does not exist until the first
note and is meant to be emptied. It is the only store: the markdown is generated
from it on demand, one way, and nothing ever parses it back.

**An issue snapshots what produced the take** — the script's lines for that shot,
the compiled prompt, the reference files, the take's video. That is the whole
point: the note is about the shot *as it was rendered*, so an assistant sees
what caused the problem rather than whatever the script says after you have
started editing it. (Deriving the text at read time is right for anything
durable; here it would destroy the thing the note is about.)

An issue whose shot has since been rebuilt or re-rendered reads `addressed`.
That is computed on every read, never written, and nothing is deleted behind
your back: `clear(addressed_only=True)` is how the list empties.

Stdlib only.
"""
from __future__ import annotations

import json
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import h3edit as E  # noqa: E402
import h3jobs as J  # noqa: E402
import h3source as H  # noqa: E402
import h3takes as T  # noqa: E402

ISSUES_FILE = "_issues.json"
DEFAULT_PASS = "proxy"          # the pass a review starts from
VERSION = 1
MAX_NOTE = 4000


class IssueError(Exception):
    """A request that can't be done: `status` is the HTTP status the editor
    answers with (the same shape as h3source.SourceError)."""

    def __init__(self, status: int, message: str, **data):
        super().__init__(message)
        self.status = status
        self.data = data


def issues_path(ep: str) -> str:
    return os.path.join(ep, ISSUES_FILE)


def load(ep: str) -> dict:
    """The file, or an empty one. A file that isn't readable is an error rather
    than a fresh start: losing a list of notes silently is the one thing this
    must not do."""
    p = issues_path(ep)
    if not os.path.isfile(p):
        return {"version": VERSION, "items": []}
    try:
        with open(p, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as e:
        raise IssueError(500, f"{p} can't be read: {e}") from None
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise IssueError(500, f"{p} is not an issue list")
    if int(data.get("version") or 0) > VERSION:
        raise IssueError(500, f"{p} was written by a newer h3pipe "
                              f"(version {data['version']}, this is {VERSION})")
    data.setdefault("version", VERSION)
    return data


def save(ep: str, data: dict) -> None:
    """Write the file, or remove it when nothing is left: an episode with no
    issues has no issue file."""
    p = issues_path(ep)
    if not data.get("items"):
        if os.path.isfile(p):
            os.remove(p)
        return
    data["version"] = VERSION
    H.atomic_write(p, (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))


# ---------------------------------------------------------------------------
# what an issue records
# ---------------------------------------------------------------------------

def script_excerpt(ep: str, shot: str) -> str:
    """The script's lines for one shot, as they are now. Empty when the script
    can't be read or doesn't name it (the note is still worth keeping)."""
    try:
        src = H.read_source(ep, "script")
        spans = {s["id"]: s for s in H.script_shots(src.text, ep)}
    except Exception:
        return ""
    span = spans.get(shot)
    if not span:
        return ""
    lines = src.text.split("\n")
    return "\n".join(lines[span["line"] - 1:span["end_line"]]).rstrip()


def _shot_entry(ep: str, pass_: str, shot: str) -> dict:
    try:
        doc, idx = J.find_shot(ep, pass_, shot)
    except KeyError:
        raise IssueError(404, f"{shot} is not in {J.shotlist_rel(pass_)}") from None
    return doc["shots"][idx]


def cut_take(ep: str, pass_: str, shot: str) -> int | None:
    """The take the cut plays for this shot -- what a person watching the pass
    is looking at. None when the cut says nothing (then the newest usable)."""
    for e in (T.load_cut(ep).get(pass_) or []):
        if isinstance(e, dict) and e.get("shot") == shot and e.get("take"):
            return int(e["take"])
    return None


def snapshot(ep: str, pass_: str, shot: str, take: int | None = None) -> dict:
    """What produced the take: the script's lines, the compiled prompt, the
    references and the take's video. Read once, kept as it was."""
    entry = _shot_entry(ep, pass_, shot)
    detail = E.shot_detail(ep, pass_, shot)
    if take is None:
        take = cut_take(ep, pass_, shot)
    if take is None:
        usable = [t for t in detail["takes"] if t.get("status") == "ok"]
        take = usable[-1]["take"] if usable else None
    t = next((x for x in detail["takes"] if x["take"] == take), None)
    eff = detail.get("effective") or {}
    return {
        "shot": shot,
        "pass": pass_,
        "take": take,
        "shot_hash": J.story_hash(entry),
        "target": eff.get("target"),
        "script": script_excerpt(ep, shot),
        "prompt": eff.get("prompt") or "",
        "seed": eff.get("seed"),
        "refs": [{"role": r.get("role"), "id": r.get("id"), "path": r.get("path")}
                 for r in (detail.get("refs_used") or [])],
        "take_file": ((t.get("files") or {}).get("mp4") if t else None),
    }


def check_note(note) -> str:
    if not isinstance(note, str) or not note.strip():
        raise IssueError(400, "the note is the issue: say what is wrong with the shot")
    if len(note) > MAX_NOTE:
        raise IssueError(400, f"the note is longer than {MAX_NOTE} characters")
    return note.strip()


def add(ep: str, pass_: str, shot: str, note: str, take: int | None = None) -> dict:
    """Note what is wrong with a shot as it was rendered. Raises IssueError 404
    for a shot that isn't in the pass, 400 for an empty note."""
    note = check_note(note)
    item = dict(snapshot(ep, pass_, shot, take), id=uuid.uuid4().hex[:8],
                note=note, when=T.now())
    data = load(ep)
    data["items"].append(item)
    save(ep, data)
    return item


def addressed(ep: str, item: dict, docs: dict | None = None) -> bool:
    """Whether the shot has moved on since the note: it was rebuilt (its story
    hash changed) or rendered again (a newer take exists). Computed on read."""
    pass_ = item.get("pass") or DEFAULT_PASS
    shot = item.get("shot") or ""
    try:
        if docs is None:
            entry = _shot_entry(ep, pass_, shot)
        else:
            key = (pass_, shot)
            if key not in docs:
                docs[key] = _shot_entry(ep, pass_, shot)
            entry = docs[key]
    except IssueError:
        return True                     # the shot is gone from the pass
    if item.get("shot_hash") and J.story_hash(entry) != item["shot_hash"]:
        return True
    if item.get("take"):
        newer = [t for t in T.list_takes(ep, pass_, shot)
                 if t.take > int(item["take"]) and t.status == "ok"]
        return bool(newer)
    return False


def list_issues(ep: str, pass_: str | None = None) -> list[dict]:
    """The issues, in the order they were noted, each with `addressed`."""
    docs: dict = {}
    out = []
    for item in load(ep)["items"]:
        if pass_ and item.get("pass") != pass_:
            continue
        out.append(dict(item, addressed=addressed(ep, item, docs)))
    return out


def resolve(ep: str, ids: list[str]) -> int:
    """Drop the named issues. Returns how many went."""
    data = load(ep)
    want = {str(i) for i in ids}
    keep = [x for x in data["items"] if x.get("id") not in want]
    gone = len(data["items"]) - len(keep)
    if gone:
        data["items"] = keep
        save(ep, data)
    return gone


def clear(ep: str, pass_: str | None = None, addressed_only: bool = False) -> int:
    """Empty the notepad (or one pass of it, or only what has been addressed)."""
    data = load(ep)
    docs: dict = {}

    def drop(item: dict) -> bool:
        if pass_ and item.get("pass") != pass_:
            return False
        return addressed(ep, item, docs) if addressed_only else True

    keep = [x for x in data["items"] if not drop(x)]
    gone = len(data["items"]) - len(keep)
    if gone:
        data["items"] = keep
        save(ep, data)
    return gone


# ---------------------------------------------------------------------------
# the export: one document to paste into an assistant
# ---------------------------------------------------------------------------

INSTRUCTION = (
    "These are notes taken while watching a rendered pass of an episode. For each shot, "
    "propose the smallest edit to the script (`{script}`) or the series config "
    "(`series.json`) that would fix what the note describes. The script excerpt is the "
    "shot as it was rendered; the prompt below it is what the pipeline compiled from that "
    "excerpt, so a wording problem is usually in the excerpt and an identity or layout "
    "problem is usually in the referenced pictures. Don't rewrite what the note doesn't "
    "mention."
)


def export_markdown(ep: str, pass_: str | None = None, include_addressed: bool = False) -> str:
    """The issues as one pasteable document. Generated from the file every time;
    nothing reads it back, so it carries no markers."""
    items = [x for x in list_issues(ep, pass_) if include_addressed or not x["addressed"]]
    name = os.path.basename(os.path.normpath(os.path.abspath(ep)))
    script = os.path.basename(E.episode_script(ep) or f"{name}.md")
    head = [f"# {name}: {len(items)} issue{'' if len(items) == 1 else 's'}"
            + (f" ({pass_} pass)" if pass_ else ""),
            "",
            INSTRUCTION.replace("{script}", script),
            ""]
    if not items:
        return "\n".join(head + ["*Nothing noted.*", ""])
    for x in items:
        head += _issue_markdown(x)
    return "\n".join(head)


def _issue_markdown(x: dict) -> list[str]:
    out = [f"## {x['shot']}" + (f" · take {x['take']}" if x.get("take") else "")
           + (f" · {x['target']}" if x.get("target") else ""),
           "",
           x["note"], ""]
    if x.get("addressed"):
        out += ["*(the shot has been rebuilt or re-rendered since this note)*", ""]
    if x.get("script"):
        out += ["**The script, as rendered:**", "", "```", x["script"], "```", ""]
    if x.get("prompt"):
        out += ["**What the pipeline compiled from it:**", "", "```", x["prompt"], "```", ""]
    refs = [r for r in (x.get("refs") or []) if r.get("path")]
    if refs:
        out += ["**Reference pictures it used:**", ""]
        out += [f"- {r.get('role') or 'ref'}: `{r['path']}`"
                + (f" ({r['id']})" if r.get("id") else "") for r in refs]
        out += [""]
    if x.get("take_file"):
        out += [f"Rendered file: `{x['take_file']}`", ""]
    return out


def export_json(ep: str, pass_: str | None = None, include_addressed: bool = False) -> dict:
    items = [x for x in list_issues(ep, pass_) if include_addressed or not x["addressed"]]
    return {"episode": os.path.abspath(ep), "pass": pass_,
            "instruction": INSTRUCTION.replace(
                "{script}", os.path.basename(E.episode_script(ep) or "the script")),
            "issues": items}


# ---------------------------------------------------------------------------
# the CLI: python h3.py issues <episode> ...
# ---------------------------------------------------------------------------

USAGE = """  !! usage: python h3.py issues <episode> [--proxy | --final]
       --add <shot> "what is wrong"   note a shot (--take N for a particular one)
       --add-from <file>              one per line: "sh0140: the truck is on the wrong side"
       (nothing)                      list them, addressed ones marked
       --export [--json] [-o FILE]    one document to paste into an assistant
       --resolve <id> [<id> ...]      drop those issues
       --clear [--addressed]          empty the notepad (or only what was addressed)"""


def _lines_from(path: str) -> list[tuple[str, str]]:
    """`sh0140: the truck is on the wrong side` per line; blank lines and `#`
    comments skipped. Raises IssueError 400 on a line with no shot."""
    try:
        with open(path, encoding="utf-8") as fh:
            raw = fh.read()
    except OSError as e:
        raise IssueError(400, f"{path} can't be read: {e}") from None
    out = []
    for n, line in enumerate(raw.replace("\r\n", "\n").split("\n"), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        shot, sep, note = line.partition(":")
        if not sep or not shot.strip() or not note.strip():
            raise IssueError(400, f"{path} line {n}: expected `<shot>: what is wrong`")
        out.append((shot.strip(), note.strip()))
    return out


def cmd_issues(root: str, argv: list[str]) -> int:
    pass_ = DEFAULT_PASS
    add_shot = add_note = add_from = out_file = None
    take = None
    want, as_json, do_clear, only_addressed, resolve_ids = None, False, False, False, []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--proxy", "--final"):
            pass_ = a[2:]
        elif a == "--add" and i + 2 < len(argv):
            add_shot, add_note = argv[i + 1], argv[i + 2]
            i += 2
        elif a == "--add-from" and i + 1 < len(argv):
            add_from = argv[i + 1]
            i += 1
        elif a == "--take" and i + 1 < len(argv):
            take = int(argv[i + 1]) if argv[i + 1].isdigit() else None
            i += 1
        elif a == "--export":
            want = "export"
        elif a == "--json":
            as_json = True
        elif a in ("-o", "--out") and i + 1 < len(argv):
            out_file = argv[i + 1]
            i += 1
        elif a == "--resolve":
            while i + 1 < len(argv) and not argv[i + 1].startswith("-"):
                resolve_ids.append(argv[i + 1])
                i += 1
        elif a == "--clear":
            do_clear = True
        elif a == "--addressed":
            only_addressed = True
        else:
            print(f"  !! unknown flag {a}\n{USAGE}")
            return 2
        i += 1

    try:
        if add_shot:
            item = add(root, pass_, add_shot, add_note, take)
            print(f"  -- {item['shot']}"
                  + (f" take {item['take']}" if item.get("take") else "")
                  + f": {item['note']}")
            print(f"     ({item['id']}, {len(item['script'].splitlines())} script lines kept)")
            return 0
        if add_from:
            n = 0
            for shot, note in _lines_from(add_from):
                item = add(root, pass_, shot, note, None)
                print(f"  -- {item['shot']}: {item['note']}")
                n += 1
            print(f"\n  -- {n} noted from {add_from}")
            return 0
        if resolve_ids:
            print(f"  -- {resolve(root, resolve_ids)} resolved")
            return 0
        if do_clear:
            gone = clear(root, pass_ if only_addressed else None, only_addressed)
            print(f"  -- {gone} cleared" + (" (addressed only)" if only_addressed else ""))
            return 0
        if want == "export":
            text = (json.dumps(export_json(root, pass_), indent=2, ensure_ascii=False)
                    if as_json else export_markdown(root, pass_))
            if out_file:
                H.atomic_write(os.path.abspath(out_file), (text + "\n").encode("utf-8"))
                print(f"  -> {os.path.abspath(out_file)}")
            else:
                print(text)
            return 0
        items = list_issues(root, pass_)
        if not items:
            print(f"  -- no issues noted for the {pass_} pass")
            return 0
        for x in items:
            mark = " (addressed)" if x["addressed"] else ""
            print(f"  {x['id']}  {x['shot']}"
                  + (f" t{x['take']:02d}" if x.get("take") else "     ")
                  + f"  {x['note']}{mark}")
        n_addr = sum(1 for x in items if x["addressed"])
        print(f"\n  -- {len(items)} issue{'' if len(items) == 1 else 's'}"
              + (f", {n_addr} addressed (--clear --addressed)" if n_addr else ""))
        return 0
    except IssueError as e:
        print(f"  !! {e}")
        return 1


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(USAGE)
        raise SystemExit(2)
    raise SystemExit(cmd_issues(os.path.abspath(sys.argv[1]), sys.argv[2:]))
