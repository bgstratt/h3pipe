#!/usr/bin/env python3
"""
Build the editor's mock fixtures from a real, rendered episode.

    python web/scripts/make_fixtures.py <episode folder>

Writes web/src/mock/fixtures.json (episode_status and shot_detail for both passes,
seeds as strings, exactly as docs/API.md serves them) and copies each take's
thumb, strip and mp4 into web/mock/media/ under its basename. Dev-only: the
production bundle never includes any of it. Stdlib only; run from the repo root.
"""
from __future__ import annotations

import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, REPO)

import h3edit as E  # noqa: E402
import h3takes as T  # noqa: E402

WEB = os.path.dirname(HERE)
MEDIA = os.path.join(WEB, "mock", "media")
FIXTURES = os.path.join(WEB, "src", "mock", "fixtures.json")
FAKE_EP = "C:\\Shows\\DeanStories\\ep05"


def stringify_seeds(o):
    if isinstance(o, dict):
        return {k: (str(v) if k == "seed" and isinstance(v, int) and not isinstance(v, bool)
                    else stringify_seeds(v)) for k, v in o.items()}
    if isinstance(o, list):
        return [stringify_seeds(v) for v in o]
    return o


def main(ep: str) -> int:
    ep = os.path.abspath(ep)
    os.makedirs(MEDIA, exist_ok=True)
    out = {"ep": FAKE_EP, "summary": None, "status": {}, "detail": {}, "shotlist": {}}
    summ = E.episode_summary(ep)
    summ["ep"] = FAKE_EP
    out["summary"] = summ
    media = set()
    for ps in T.PASSES:
        if not summ["built"][ps]:
            continue
        st = stringify_seeds(E.episode_status(ep, ps))
        out["status"][ps] = st
        out["detail"][ps] = {}
        for s in st["shots"]:
            if s["orphan"]:
                continue
            d = stringify_seeds(E.shot_detail(ep, ps, s["shot"]))
            out["detail"][ps][s["shot"]] = d
            for t in d["takes"]:
                for k, rel in t["files"].items():
                    if k in ("mp4", "thumb", "strip"):
                        media.add(rel)
                    elif k == "shotlist":
                        with open(os.path.join(ep, rel), encoding="utf-8") as fh:
                            out["shotlist"][rel] = stringify_seeds(json.load(fh))
    for rel in sorted(media):
        shutil.copyfile(os.path.join(ep, rel), os.path.join(MEDIA, os.path.basename(rel)))
    with open(FIXTURES, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(out, fh, indent=1, ensure_ascii=False)
        fh.write("\n")
    print(f"fixtures.json: {len(out['status'])} pass(es); {len(media)} media file(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
