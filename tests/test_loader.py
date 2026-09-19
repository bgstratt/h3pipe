"""H3ShotListLoader: missing references fail the shot, unless the frozen shot
says `missing_refs: blank` (a render asked for anyway)."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "comfy_nodes"))
try:
    import torch  # noqa: F401
    import h3_shotlist as N
except Exception as e:                                   # no torch here
    raise unittest.SkipTest(f"loader needs torch: {e}")

import h3jobs as J  # noqa: E402
from test_render import build_episode  # noqa: E402


class LoaderMissingRefsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        build_episode(self.root, refs=False)             # nothing on disk
        self.doc = J.load_shotlist(self.root, "proxy")

    def tearDown(self):
        self._tmp.cleanup()

    def frozen(self, shot_id: str, blank: bool) -> str:
        i = next(i for i, s in enumerate(self.doc["shots"]) if s["id"] == shot_id)
        job = J.plan_job(self.root, "proxy", self.doc, i,
                         J.RenderRequest(shot_id, allow_missing_refs=blank), {})
        self.assertTrue(job.missing)
        self.assertEqual(job.action, "render" if blank else "blocked")
        doc = J.frozen_shotlist(job)
        p = os.path.join(self.root, f"{shot_id}_{blank}.json")
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(doc, fh)
        return os.path.basename(p)

    def load(self, rel):
        return N.H3ShotListLoader().load(self.root, rel, 0, "auto", 24.0)

    def test_missing_refs_fail_without_the_flag(self):
        with self.assertRaises(FileNotFoundError):
            self.load(self.frozen("sh020", blank=False))

    def test_render_anyway_substitutes_grey(self):
        out = self.load(self.frozen("sh020", blank=True))   # ada, bo + kettle, no files
        refs, ref_bg, info = out[2:5], out[5], out[15]
        for r in (*refs, ref_bg):
            self.assertAlmostEqual(float(r.mean()), 0.5, places=3)
        self.assertEqual(tuple(ref_bg.shape[1:3]), (256, 448))   # the proxy's size
        self.assertIn("RENDERED WITHOUT", info)
        self.assertIn("Picture 4", info)

    def test_render_anyway_recompiled_loads(self):
        # with the series config beside the build, H3 recompiles the shot
        # without its missing refs: no subjects, no plate in the prompt
        import shutil
        from test_render import FIXTURE
        shutil.copy(os.path.join(FIXTURE, "series.json"), self.root)
        out = self.load(self.frozen("sh020", blank=True))
        prompt, info = out[1], out[15]
        self.assertNotIn("<Picture", prompt)
        self.assertIn("Ada has no reference image", prompt)
        self.assertIn("subjects: - (plate only)", info)
        self.assertIn("RENDERED WITHOUT: Picture 4", info)

    def test_render_anyway_drops_missing_recording(self):
        # sh110 is a dub shot: its recording is missing too
        out = self.load(self.frozen("sh110", blank=True))
        self.assertIsNone(out[6])
        self.assertIn("recording MISSING", out[15])


if __name__ == "__main__":
    unittest.main()
