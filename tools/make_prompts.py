#!/usr/bin/env python3
"""
make_prompts.py — regenerate prompts/ from docs/AUTHORING.md.

The authoring guide is the single source. This wraps it in the packaging each
assistant expects, so the copies cannot drift from the document:

    prompts/SKILL.md                      Claude Code / Claude.ai skill
    prompts/h3-script.instructions.md     Cursor, Copilot, generic system prompt

Run it after editing docs/AUTHORING.md:

    python tools/make_prompts.py
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(ROOT, "docs", "AUTHORING.md")

DESCRIPTION = (
    "Write episode scripts and series configs for the h3pipe MiniMax H3 shot-list "
    "pipeline — the .md script format with sequences, shots and who/size/dur/plate/"
    "camera/sound fields, plus the series config (series.json) of subjects, locations and voices. "
    "Use when drafting or extending an episode, breaking a scene into shots, adding "
    "characters or locations to a series config, re-timing shots whose dialogue does not fit, "
    "or fixing a script h3build rejected."
)

PREAMBLE = """You are writing for the h3pipe pipeline. Follow this format exactly: the
output is compiled by `h3build.py`, and anything that does not match is a build error, not a
style preference. Validate with `h3build.py series.json <script> --check` and `--pace` before
calling a script finished."""


def body() -> str:
    with open(SOURCE, encoding="utf-8") as fh:
        text = fh.read()
    # drop the document's own title and lede; the wrappers supply their own
    parts = text.split("\n## ", 1)
    return "## " + parts[1] if len(parts) == 2 else text


def write(path: str, content: str) -> None:
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(content)
    print(f"  -> {os.path.relpath(path, ROOT)}")


def main() -> int:
    if not os.path.isfile(SOURCE):
        sys.exit(f"  !! {SOURCE} not found")
    text = body()
    out = os.path.join(ROOT, "prompts")
    os.makedirs(out, exist_ok=True)

    write(os.path.join(out, "SKILL.md"),
          "---\n"
          "name: h3-episode-script\n"
          f"description: {DESCRIPTION}\n"
          "---\n\n"
          "# Writing an episode for h3pipe\n\n"
          f"{PREAMBLE}\n\n{text}")

    write(os.path.join(out, "h3-script.instructions.md"),
          "# h3pipe episode script — assistant instructions\n\n"
          "Paste this into Cursor rules, Copilot instructions, a custom GPT, or any\n"
          "system prompt. Generated from docs/AUTHORING.md by tools/make_prompts.py.\n\n"
          f"{PREAMBLE}\n\n{text}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
