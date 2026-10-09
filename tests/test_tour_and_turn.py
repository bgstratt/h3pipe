"""
P5: a location's camera tour (h3tour.py) and a character's views turned from
one it has (h3refs.plan_edit `from_view` / `turn`).

The tour's ComfyUI run and its stills step are faked: the graph it queues,
what it refuses, and what happens to its holds (takes of the location's
`tour` view, never picked there, copied into an angle as a candidate) are
what is checked.
"""
from __future__ import annotations

import json
import os
import random
import shutil
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import h3refs as R  # noqa: E402
import h3tour as TOUR  # noqa: E402
from test_render import png_bytes  # noqa: E402

FIXTURE = os.path.join(HERE, "fixtures", "kitchen_sink")


class FakeComfy:
    def __init__(self, video: bytes = b"mp4"):
        self.queued, self.uploaded, self.video = [], [], video

    def upload_input(self, path, name):
        self.uploaded.append(name)
        return name

    def queue(self, g):
        self.queued.append(g)
        return f"pid{len(self.queued)}"

    def wait(self, pid, timeout):
        return {"92": {"images": [{"filename": "tour_00001.mp4", "subfolder": "h3pipe/tours",
                                   "type": "output"}]}}

    def view(self, f):
        return self.video


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ep = os.path.join(self._tmp.name, "ks01")
        os.makedirs(self.ep)
        with open(os.path.join(FIXTURE, "series.json"), encoding="utf-8") as fh:
            c = json.load(fh)
        c["locations"]["kitchen_on_sink"] = {"of": "kitchen", "description": "the sink",
                                             "plate": "refs/_bg/kitchen_on_sink.png"}
        with open(os.path.join(self.ep, "series.json"), "w", encoding="utf-8") as fh:
            json.dump(c, fh)
        shutil.copy(os.path.join(FIXTURE, "script.md"), os.path.join(self.ep, "ks01.md"))
        import h3edit as E
        self.assertTrue(E.build_episode(self.ep)["ok"])
        s = R.load_series(self.ep)
        self.png = os.path.join(self._tmp.name, "p.png")
        with open(self.png, "wb") as fh:
            fh.write(png_bytes(1344, 768))
        kitchen = R.find_ref(s, "location:kitchen")
        R.pick_take(s, kitchen, None, R.import_take(s, kitchen, None, self.png).take)
        ada = R.find_ref(s, "subject:ada")
        R.pick_take(s, ada, "01_threequarter", R.import_take(s, ada, "01_threequarter", self.png).take)
        self.s = R.load_series(self.ep)

    def tearDown(self):
        self._tmp.cleanup()


class TourTest(Base):
    def test_the_graph_gets_the_frame_move_length_and_seed(self):
        comfy = FakeComfy()
        ref = R.find_ref(self.s, "location:kitchen")
        (side,) = TOUR.start_tour(self.s, ref, "The camera turns left and holds", comfy, 5)
        g = comfy.queued[0]
        by = {v["class_type"]: v["inputs"] for v in g.values()}
        self.assertEqual(by["LoadImage"]["image"], comfy.uploaded[0])
        self.assertTrue(by["MiniMaxH3ImageToVideo"]["prompt"].startswith(
            "The camera turns left and holds. Nothing in the place moves"))
        self.assertEqual(by["PrimitiveFloat"]["value"], 5.0)
        self.assertEqual(by["RandomNoise"]["noise_seed"], side["seed"])
        self.assertEqual((side["status"], side["comfy_prompt_id"]), ("queued", "pid1"))
        self.assertEqual([d["tour"] for d in TOUR.list_tours(ref)], [1])

    def test_what_is_refused(self):
        ref = R.find_ref(self.s, "location:kitchen")
        for args, why in ((("", 6, 1), "needs a move"), (("go", 30, 1), "seconds"),
                          (("go", 6, 9), "count")):
            with self.subTest(args=args), self.assertRaisesRegex(TOUR.TourError, why):
                TOUR.check_request(ref, *args)
        with self.assertRaisesRegex(TOUR.TourError, "not a location"):
            TOUR.check_request(R.find_ref(self.s, "subject:ada"), "go", 6, 1)
        with self.assertRaisesRegex(TOUR.TourError, "no live plate"):
            TOUR.check_request(R.find_ref(self.s, "location:kitchen_on_sink"), "go", 6, 1)

    def test_holds_land_on_the_tour_view_and_copy_into_an_angle(self):
        comfy = FakeComfy()
        ref = R.find_ref(self.s, "location:kitchen")
        (side,) = TOUR.start_tour(self.s, ref, "turn left and hold", comfy)
        holds_dir = os.path.join(self._tmp.name, "holds")
        os.makedirs(holds_dir)
        found = {"fps": 24, "holds": []}
        for k in (1, 2):
            p = os.path.join(holds_dir, f"hold_{k:02d}.png")
            shutil.copy(self.png, p)
            found["holds"].append({"hold": k, "start": k - 1.0, "end": k - 0.5, "frame": k,
                                   "sharpness": 1.0, "path": p})
        seen = []
        with mock.patch.object(TOUR, "find_holds", return_value=found):
            side = TOUR.finish_tour(self.s, ref, side, comfy, on_hold=seen.append)
        self.assertEqual(side["status"], "ok", side.get("error"))
        self.assertEqual(len(side["holds"]), 2)
        self.assertEqual(len(seen), 2)
        js = R.ref_json(self.s, ref)
        self.assertEqual([t["take"] for t in js["tour_holds"]], side["holds"])
        self.assertEqual(js["tours"][0]["status"], "ok")
        with self.assertRaisesRegex(R.RefError, "isn't picked where it is"):
            R.pick_take(self.s, ref, R.TOUR_VIEW, side["holds"][1])
        angle = R.find_ref(self.s, "location:kitchen_on_sink")
        t = R.copy_take(self.s, ref, R.TOUR_VIEW, side["holds"][1], angle)
        self.assertEqual(t.sidecar["copied_from"]["source"], "tour")
        self.assertIn("tour t01 hold 2", t.sidecar["note"])

    def test_a_failed_tour_says_why(self):
        comfy = FakeComfy()
        comfy.wait = mock.Mock(side_effect=RuntimeError("out of memory"))
        ref = R.find_ref(self.s, "location:kitchen")
        (side,) = TOUR.start_tour(self.s, ref, "turn left and hold", comfy)
        side = TOUR.finish_tour(self.s, ref, side, comfy)
        self.assertEqual(side["status"], "failed")
        self.assertIn("out of memory", side["error"])


class TurnTest(Base):
    def test_a_view_is_turned_from_another(self):
        req = R.GenRequest("subject:ada", "03_back",
                           edit={"take": None, "from_view": "01_threequarter", "turn": True})
        (job,) = R.plan_generate(self.s, req, rng=random.Random(0))
        self.assertEqual(job.view, "03_back")
        self.assertEqual(job.references[0]["view"], "01_threequarter")
        self.assertEqual(job.target.id, R.TURN_TARGET)
        self.assertEqual(job.prompt, R.VIEW_TURNS["03_back"])
        self.assertEqual(job.loras, [{"name": R.TURN_LORA, "strength": R.TURN_STRENGTH}])
        self.assertEqual(job.edit["view"], "01_threequarter")
        self.assertTrue(job.edit["turn"])

    def test_turning_is_for_a_characters_views(self):
        with self.assertRaisesRegex(R.RefError, "no views"):
            R.plan_generate(self.s, R.GenRequest("location:kitchen", prompt="x",
                                                 edit={"take": None, "from_view": "02_side"}))
        with self.assertRaisesRegex(R.RefError, "no picked take"):
            R.plan_generate(self.s, R.GenRequest("subject:ada", "01_threequarter",
                                                 edit={"take": None, "from_view": "02_side",
                                                       "turn": True}))


if __name__ == "__main__":
    unittest.main()
