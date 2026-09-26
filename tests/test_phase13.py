"""
Phase 13 (docs/PLAN.md): an optional 2x upscale of a picked final take.

13a, a take's latent: whether a render keeps it (the request, else the series
config's `upscale.save_latents`, else final-only), only on a target whose
binding says where its latent comes from (`saver.latent`), and the build's
warning for a value it doesn't know. H3SaveShot's side (writing and loading
the file) is in test_save_node.py; the route's in test_api.py.
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
sys.path.insert(0, os.path.join(ROOT, "comfy_nodes"))
import h3build  # noqa: E402
import h3jobs as J  # noqa: E402
import h3pipe_api as A  # noqa: E402
import h3takes as T  # noqa: E402
import targets as TG  # noqa: E402
from h3core.series_config import (character_ids, load_series_config, series_info,  # noqa: E402
                                  subject_ids, variant_of)
from h3core.story import parse_story  # noqa: E402
from test_api import ApiTest  # noqa: E402

KITCHEN = os.path.join(HERE, "fixtures", "kitchen_sink")


class LatentDefaultTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.show = self._tmp.name
        self.ep = os.path.join(self.show, "ep01")
        os.makedirs(self.ep)

    def tearDown(self):
        self._tmp.cleanup()

    def config(self, upscale):
        with open(os.path.join(self.show, "series.json"), "w", encoding="utf-8") as fh:
            json.dump({"subjects": {}, **({"upscale": upscale} if upscale is not None else {})}, fh)

    def modes(self):
        return J.latent_default(self.ep, "final"), J.latent_default(self.ep, "proxy")

    def test_final_only_by_default(self):
        self.assertEqual(self.modes(), (True, False))      # no series config at all
        self.config(None)
        self.assertEqual(self.modes(), (True, False))
        self.config({})
        self.assertEqual(self.modes(), (True, False))

    def test_series_config_modes(self):
        for mode, want in (("final", (True, False)), ("always", (True, True)),
                           ("never", (False, False)), ("sometimes", (True, False))):
            self.config({"save_latents": mode})
            self.assertEqual(self.modes(), want, mode)

    def test_build_warns_about_a_mode_it_doesnt_know(self):
        cfg = load_series_config(os.path.join(KITCHEN, "series.json"))
        with open(os.path.join(KITCHEN, "script.md"), encoding="utf-8") as fh:
            story = parse_story(fh.read(), subject_ids(cfg), character_ids(cfg),
                                series_info(cfg), variant_of(cfg))
        quiet = h3build.story_warnings(story, cfg)
        self.assertFalse([w for w in quiet if "save_latents" in w])
        cfg["upscale"] = {"save_latents": "always"}
        self.assertEqual(h3build.story_warnings(story, cfg), quiet)
        cfg["upscale"] = {"save_latents": "yes"}
        extra = [w for w in h3build.story_warnings(story, cfg) if "save_latents" in w]
        self.assertEqual(len(extra), 1)
        self.assertIn("'yes'", extra[0])


class LatentBindingTest(unittest.TestCase):
    def test_h3_ref2va_names_its_sampler(self):
        b = TG.load_target("minimax_h3_ref2va", "video").binding
        self.assertEqual(b.saver["latent"], {"class_type": "SamplerCustomAdvanced", "output": 0})
        # the repo workflow has exactly one, so graph_for can link it
        g = J.graph_from(json.load(open(b.workflow, encoding="utf-8")))
        self.assertEqual(sum(v["class_type"] == "SamplerCustomAdvanced" for v in g.values()), 1)


class LatentRouteTest(ApiTest):
    def test_a_target_without_a_latent_source_keeps_none(self):
        """Asked for on a target whose binding names no latent: nothing is
        linked and the sidecar says why."""
        target = next(t.id for t in TG.list_targets("video")
                      if not t.binding.saver.get("latent"))
        self.ok(A.post_render(self.ctx, {"ep": self.ep, "pass": "final", "shots": ["sh010"],
                                         "target": target, "save_latent": True,
                                         "allow_missing_refs": True,
                                         "allow_model_mismatch": True}))
        g = self.comfy.graphs[-1]
        saver = next(v["inputs"] for v in g.values() if v["class_type"] == J.SAVER)
        self.assertNotIn("latent", saver)
        sc = T.get_take(self.ep, "final", "sh010", 1).sidecar
        self.assertNotIn("save_latent", sc)
        self.assertTrue(any("no latent is kept" in n for n in sc.get("notes", [])), sc)


if __name__ == "__main__":
    unittest.main()
