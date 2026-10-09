"""
Angles of a location (`of:`): the same place from another camera. Loading
checks them; an edit target generates an angle's plate from its master's, so
every angle is visibly the same place; a text-to-image target draws it from
its description as before.
"""
from __future__ import annotations

import json
import os
import random
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import h3refs as R  # noqa: E402
from h3core.series_config import location_master, series_config_from  # noqa: E402
from test_render import png_bytes  # noqa: E402

FIXTURE = os.path.join(HERE, "fixtures", "kitchen_sink")


def cfg(**locs):
    return {"series": {"id": "s", "title": "S"}, "style": {"look": "photoreal"},
            "subjects": {"ada": {"name": "Ada", "design": "a tall woman"}},
            "locations": {"bay": {"description": "an ambulance bay at night"}, **locs}}


class LoadTest(unittest.TestCase):
    def test_an_angle_names_its_master(self):
        c = series_config_from(cfg(bay_drive={"of": "bay", "description": "from the drive"}))
        self.assertEqual(location_master(c, "bay_drive"), "bay")
        self.assertEqual(location_master(c, "bay"), "bay")

    def test_what_is_refused(self):
        for locs, why in (({"x": {"of": "nowhere", "description": "d"}}, "not a location"),
                          ({"x": {"of": "x", "description": "d"}}, "not itself"),
                          ({"x": {"of": "bay", "description": "d"},
                            "y": {"of": "x", "description": "d"}}, "itself an angle"),
                          ({"x": {"of": 3, "description": "d"}}, "names the location")):
            with self.subTest(locs=locs), self.assertRaisesRegex(ValueError, why):
                series_config_from(cfg(**locs))


class GenerateTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ep = os.path.join(self._tmp.name, "ks01")
        os.makedirs(self.ep)
        with open(os.path.join(FIXTURE, "series.json"), encoding="utf-8") as fh:
            c = json.load(fh)
        c["locations"]["kitchen_on_sink"] = {
            "of": "kitchen", "description": "looking along the counter at the sink under the window",
            "plate": "refs/_bg/kitchen_on_sink.png"}
        with open(os.path.join(self.ep, "series.json"), "w", encoding="utf-8") as fh:
            json.dump(c, fh)
        shutil.copy(os.path.join(FIXTURE, "script.md"), os.path.join(self.ep, "ks01.md"))
        import h3edit as E
        self.assertTrue(E.build_episode(self.ep)["ok"])
        s = R.load_series(self.ep)
        plate = os.path.join(self._tmp.name, "plate.png")
        with open(plate, "wb") as fh:
            fh.write(png_bytes(1344, 768))
        kitchen = R.find_ref(s, "location:kitchen")
        R.pick_take(s, kitchen, None, R.import_take(s, kitchen, None, plate).take)
        self.s = R.load_series(self.ep)

    def tearDown(self):
        self._tmp.cleanup()

    def plan(self, target):
        return R.plan_generate(self.s, R.GenRequest("location:kitchen_on_sink", target=target),
                               rng=random.Random(0))[0]

    def test_an_edit_target_starts_from_the_masters_plate(self):
        job = self.plan("flux2_klein_edit")
        self.assertEqual([r["location"] for r in job.references], ["kitchen"])
        self.assertIn("Draw that same place from another camera position: looking along the "
                      "counter", job.prompt)
        self.assertIn("move only the camera", job.prompt)

    def test_a_text_to_image_target_draws_it_from_words(self):
        job = self.plan("krea2")
        self.assertEqual(job.references, [])
        self.assertTrue(job.prompt.startswith("A background plate drawn as"))

    def test_the_master_is_drawn_as_before(self):
        job = R.plan_generate(self.s, R.GenRequest("location:kitchen", target="flux2_klein_edit"),
                              rng=random.Random(0))[0]
        self.assertEqual(job.references, [])

    def test_the_listing_names_the_master(self):
        ref = R.find_ref(self.s, "location:kitchen_on_sink")
        self.assertEqual(R.ref_json(self.s, ref)["of"], "kitchen")


if __name__ == "__main__":
    unittest.main()
