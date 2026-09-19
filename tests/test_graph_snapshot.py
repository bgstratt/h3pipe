"""
The queued graph for every kitchen_sink shot, both passes, against a snapshot.

Phase 7 moved the H3 code behind a target (targets/video/minimax_h3_ref2va).
The snapshot in tests/golden/graphs/ was captured from the code BEFORE that
move (commit ce5911c), so this test is the proof that a queued job is
unchanged: the API graph `graph_for` patches, the frozen one-shot shotlist the
loader reads and the take's sidecar (minus its queue time).

    python -m pytest tests/test_graph_snapshot.py
    python tests/test_graph_snapshot.py --update   # intended changes only

Shots added to the fixture after the snapshot was taken are not in it and are
skipped here; every shot that IS in it must still be built and match.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
import h3jobs as J  # noqa: E402
from test_render import build_episode  # noqa: E402

SNAPSHOT = os.path.join(HERE, "golden", "graphs", "kitchen_sink.json")
PASSES = ("final", "proxy")


def _norm(obj, root: str):
    """`obj` with the temp episode folder replaced by <ROOT> and paths in '/'."""
    if isinstance(obj, dict):
        return {k: _norm(v, root) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_norm(v, root) for v in obj]
    if isinstance(obj, str):
        s = obj.replace(root, "<ROOT>")
        return s.replace("\\", "/") if ("<ROOT>" in s or os.sep in s) else s
    return obj


def capture() -> dict:
    """{pass: {shot: {graph, shotlist, sidecar}}} from the current code."""
    base, _ = J.resolve_workflow(None, J.WORKFLOW_NAME, None, prefer_repo=True)
    out: dict = {}
    with tempfile.TemporaryDirectory() as root:
        build_episode(root)
        for ps in PASSES:
            doc = J.load_shotlist(root, ps)
            out[ps] = {}
            for i, shot in enumerate(doc["shots"]):
                job = J.plan_job(root, ps, doc, i, J.RenderRequest(shot["id"]), {})
                take = J.start_job(job)
                graph = J.graph_for(base, job, take)
                with open(take.paths.shotlist, encoding="utf-8") as fh:
                    frozen = json.load(fh)
                with open(take.paths.sidecar, encoding="utf-8") as fh:
                    sidecar = json.load(fh)
                sidecar.pop("queued", None)
                out[ps][shot["id"]] = _norm({"graph": graph, "shotlist": frozen,
                                             "sidecar": sidecar}, root)
    return out


class GraphSnapshotTest(unittest.TestCase):
    maxDiff = None

    def test_graphs_unchanged(self):
        with open(SNAPSHOT, encoding="utf-8") as fh:
            want = json.load(fh)
        got = capture()
        for ps in PASSES:
            for sid, w in want[ps].items():
                with self.subTest(pass_=ps, shot=sid):
                    self.assertIn(sid, got[ps], f"{sid} is no longer built")
                    # Phase 7's one intended addition: the frozen shotlist
                    # records its target (the sidecar always did)
                    frozen = got[ps][sid]["shotlist"]
                    if "target" not in w["shotlist"]:
                        self.assertEqual(frozen.pop("target", None), "minimax_h3_ref2va")
                    # and the sidecar's length_source (`dur: model`): a
                    # script-timed shot's is "script"
                    if "length_source" not in w["sidecar"]:
                        self.assertEqual(got[ps][sid]["sidecar"].pop("length_source", None),
                                         "script")
                    for part in ("graph", "shotlist", "sidecar"):
                        self.assertEqual(got[ps][sid][part], w[part], f"{ps} {sid} {part}")


if __name__ == "__main__":
    if "--update" in sys.argv:
        os.makedirs(os.path.dirname(SNAPSHOT), exist_ok=True)
        with open(SNAPSHOT, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(capture(), fh, ensure_ascii=False, indent=1, sort_keys=True)
        print(f"  -> {os.path.relpath(SNAPSHOT, ROOT)}")
    else:
        unittest.main()
