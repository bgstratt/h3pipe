#!/usr/bin/env python3
"""
make_prompts.py — regenerate prompts/ and the script-writing skill from docs/AUTHORING.md.

The authoring guide is the single source. This wraps it in the packaging each
assistant expects, so the copies cannot drift from the document:

    prompts/SKILL.md                      the skill's text alone (Claude Code / Claude.ai)
    prompts/h3-script.instructions.md     Cursor, Copilot, generic system prompt
    build/skill/h3pipe-episode-script/    the whole skill, ready to install:
        SKILL.md                          the same text, plus what the folder bundles
        references/script_conversion.md    docs/SCRIPT_CONVERSION.md (a screenplay scene
                                          worked through to shots)
        scripts/h3build.py, h3core/, targets/
                                          the build, to validate a script with --check
                                          where the pipeline isn't installed
    build/skill/h3pipe-episode-script.zip the folder zipped, to upload on claude.ai
                                          (Settings > Capabilities > Skills)

prompts/ is committed; build/ is not. Run it after editing docs/AUTHORING.md or
docs/SCRIPT_CONVERSION.md:

    python tools/make_prompts.py
    python tools/make_prompts.py --no-bundle     # prompts/ only

For Claude Code, copy build/skill/h3pipe-episode-script/ into .claude/skills/.

Stdlib only.
"""
from __future__ import annotations

import argparse
import fnmatch
import os
import shutil
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(ROOT, "docs", "AUTHORING.md")
BREAKDOWN = os.path.join(ROOT, "docs", "SCRIPT_CONVERSION.md")

NAME = "h3pipe-episode-script"

DESCRIPTION = (
    "Write episode scripts and series configs for h3pipe, the script-to-episode AI video "
    "pipeline on ComfyUI. Covers the epNN.md script format (sequences, shots, ALL-CAPS "
    "dialogue lines, who/with/size/dur/plate/camera/sound fields, target lines and first/last "
    "keyframe lines) and the series config (series.json) of subjects, locations, voices, profiles "
    "and targets. Shots render on a video target chosen per shot or episode, MiniMax H3 by "
    "default, or LTX-2, Wan 2.2 and others. Use it whenever the user wants to write, draft, "
    "outline or extend an episode, break a screenplay or scene into shots, adapt spec-format "
    "pages, add characters, props or location angles to a series config, choose which model "
    "renders a shot, re-time shots whose dialogue doesn't fit, or fix a script h3build "
    "rejected, even if they never name h3build or the file formats."
)

PREAMBLE = """You are writing for h3pipe. Follow this format exactly: the output is compiled
by `h3build.py`, and anything that does not match is a build error, not a style preference.

Before writing anything, ask for the series config (`series.json`) if the series has one.
Adding a character means adding to that file, not inventing a new one: if you write `riley`
when the series config already has `riley_freeman`, every reference path breaks. With no
series config yet you are starting a series: write both files.

Write the film, not the model. Leave `target:` lines out unless a shot needs something only
one model does (see **Choosing a video target**); advice that holds for one target only is
marked with that target's name. Validate with `h3build.py series.json <script> --check` and
`--pace` before calling a script finished."""

BUNDLE_NOTE = """## Files in this skill

- `references/script_conversion.md`: a spec-format screenplay scene worked through to shots, with
  where the cuts land and why. Read it when adapting existing script pages rather than
  writing shots directly.
- `scripts/h3build.py` (with `scripts/h3core/` and `scripts/targets/`): the build, bundled
  so a script can be validated anywhere:

  ```
  python <this skill>/scripts/h3build.py series.json ep01.md --check
  python <this skill>/scripts/h3build.py series.json ep01.md --pace
  ```

  It needs Python 3.10+ and nothing else, and writes nothing with `--check` / `--pace`. If
  the user's project has its own h3pipe checkout, use that one's `h3build.py` (or
  `python h3.py check <episode>`): it is the source of truth, and this copy is
  `{version}`."""

# What scripts/ carries: the build and what it imports. Workflows are only
# read when rendering, so they stay out.
BUNDLE_FILES = ["h3build.py"]
BUNDLE_DIRS = ["h3core", "targets"]
BUNDLE_SKIP = ["__pycache__", "*.pyc", "workflow.json"]


def read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def body() -> str:
    text = read(SOURCE)
    # drop the document's own title and lede; the wrappers supply their own
    parts = text.split("\n## ", 1)
    return "## " + parts[1] if len(parts) == 2 else text


def write(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(content)
    print(f"  -> {os.path.relpath(path, ROOT)}")


def skill_text(text: str, extra: str = "") -> str:
    # the frontmatter is YAML: an unquoted value can't hold ": "
    assert ": " not in DESCRIPTION and "#" not in DESCRIPTION, DESCRIPTION
    return ("---\n"
            f"name: {NAME}\n"
            f"description: {DESCRIPTION}\n"
            "---\n\n"
            "# Writing an episode for h3pipe\n\n"
            f"{PREAMBLE}\n\n{text}" + (f"\n{extra}\n" if extra else ""))


def version() -> str:
    try:
        out = subprocess.run(["git", "-C", ROOT, "describe", "--always", "--dirty"],
                             capture_output=True, text=True, timeout=10)
        v = out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        v = ""
    return f"h3pipe {v}" if v else "a snapshot of h3pipe"


def skipped(name: str) -> bool:
    return any(fnmatch.fnmatch(name, pat) for pat in BUNDLE_SKIP)


def bundle(text: str) -> str:
    out = os.path.join(ROOT, "build", "skill")
    folder = os.path.join(out, NAME)
    if os.path.isdir(folder):
        shutil.rmtree(folder)
    write(os.path.join(folder, "SKILL.md"),
          skill_text(text, BUNDLE_NOTE.replace("{version}", version())))
    # the breakdown's lede points into the repo; in the skill it points at SKILL.md
    title, _lede, rest = read(BREAKDOWN).split("\n\n", 2)
    bd = (f"{title}\n\n"
          "A worked example for SKILL.md. The input is ordinary spec-format script pages;\n"
          "the output is the shot list. The interesting part is not the syntax, it's where\n"
          f"the cuts land and why.\n\n{rest}")
    write(os.path.join(folder, "references", "script_conversion.md"), bd)

    scripts = os.path.join(folder, "scripts")
    os.makedirs(scripts)
    for f in BUNDLE_FILES:
        shutil.copy2(os.path.join(ROOT, f), os.path.join(scripts, f))
    for d in BUNDLE_DIRS:
        shutil.copytree(os.path.join(ROOT, d), os.path.join(scripts, d),
                        ignore=lambda _dir, names: [n for n in names if skipped(n)])
    print(f"  -> {os.path.relpath(scripts, ROOT)}{os.sep} "
          f"({', '.join(BUNDLE_FILES + [d + '/' for d in BUNDLE_DIRS])})")

    # The zip holds the folder itself, as claude.ai's skill upload expects.
    # Fixed timestamps and sorted entries: the same sources give the same zip.
    zpath = os.path.join(out, f"{NAME}.zip")
    entries = []
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames.sort()
        for n in sorted(filenames):
            full = os.path.join(dirpath, n)
            entries.append((full, os.path.relpath(full, out).replace(os.sep, "/")))
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as zf:
        for full, arc in entries:
            info = zipfile.ZipInfo(arc, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            with open(full, "rb") as fh:
                zf.writestr(info, fh.read())
    print(f"  -> {os.path.relpath(zpath, ROOT)} ({len(entries)} files, "
          f"{os.path.getsize(zpath) // 1024} KB)")
    return zpath


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-bundle", action="store_true",
                    help="write prompts/ only, not build/skill/")
    args = ap.parse_args()
    for p in (SOURCE, BREAKDOWN):
        if not os.path.isfile(p):
            sys.exit(f"  !! {p} not found")
    text = body()
    out = os.path.join(ROOT, "prompts")

    write(os.path.join(out, "SKILL.md"), skill_text(text))
    write(os.path.join(out, "h3-script.instructions.md"),
          "# h3pipe episode script — assistant instructions\n\n"
          "Paste this into Cursor rules, Copilot instructions, a custom GPT, or any\n"
          "system prompt. Generated from docs/AUTHORING.md by tools/make_prompts.py.\n\n"
          f"{PREAMBLE}\n\n{text}")
    if not args.no_bundle:
        bundle(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
