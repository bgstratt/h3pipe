#!/usr/bin/env python3
"""
tools/sync_starter_doc.py — put `examples/starter/` into docs/AUTHORING.md's
opening example, so the pair the docs teach is the pair `h3.py new` writes
(tests/test_new_episode.py compares them; run this after editing either file,
then `python tools/make_prompts.py`).

    python tools/sync_starter_doc.py [--check]
"""
from __future__ import annotations

import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOC = os.path.join(ROOT, "docs", "AUTHORING.md")
STARTER = os.path.join(ROOT, "examples", "starter")
HEADING = "## Your first episode"
# the two fenced blocks under the heading: the series config, then the script
BLOCK = re.compile(r"```(json|)\n(.*?)```\n", re.S)


def read(path: str) -> str:
    with io.open(path, encoding="utf-8") as fh:
        return fh.read().replace("\r\n", "\n")


def doc_blocks(text: str) -> list[tuple[str, str]]:
    """[(language, body)] of the first two fenced blocks under the heading."""
    start = text.index(HEADING)
    return [(m.group(1), m.group(2)) for m in list(BLOCK.finditer(text, start))[:2]]


def wanted() -> list[tuple[str, str]]:
    return [("json", read(os.path.join(STARTER, "series.json"))),
            ("", read(os.path.join(STARTER, "ep01.md")))]


def updated(text: str) -> str:
    start = text.index(HEADING)
    out, at = text[:start], start
    for m, (lang, body) in zip(list(BLOCK.finditer(text, start))[:2], wanted()):
        out += text[at:m.start()] + f"```{lang}\n{body}```\n"
        at = m.end()
    return out + text[at:]


def main(argv: list[str]) -> int:
    text = read(DOC)
    if doc_blocks(text) == wanted():
        print("  -- docs/AUTHORING.md already shows examples/starter/")
        return 0
    if "--check" in argv:
        print("  !! docs/AUTHORING.md's first example is not examples/starter/; "
              "run python tools/sync_starter_doc.py")
        return 1
    with io.open(DOC, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(updated(text))
    print(f"  -> {DOC}  (from examples/starter/)")
    print("  -- now run python tools/make_prompts.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
