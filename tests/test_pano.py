"""
P5: a location's 360 panoramas (h3refs.plan_pano): its live plate made one
equirectangular picture by the Qwen-Image 2.1 pano360 LoRA, kept as takes of
the location's `pano` pseudo-view, never picked into the plate.
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

import h3jobs as J  # noqa: E402
import h3refs as R  # noqa: E402
from test_render import png_bytes  # noqa: E402

FIXTURE = os.path.join(HERE, "fixtures", "kitchen_sink")


class PanoTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ep = os.path.join(self._tmp.name, "ks01")
        os.makedirs(self.ep)
        shutil.copy(os.path.join(FIXTURE, "series.json"), self.ep)
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

    def plan(self, **kw):
        return R.plan_generate(self.s, R.GenRequest("location:kitchen", pano={}, **kw),
                               rng=random.Random(0))

    def test_the_card_settings(self):
        (job,) = self.plan()
        self.assertEqual(job.target.id, R.PANO_TARGET)
        self.assertEqual(job.view, R.PANO_VIEW)
        self.assertEqual((job.width, job.height), R.PANO_SIZE)
        self.assertEqual(job.loras, [{"name": R.PANO_LORA, "strength": 1.0}])
        self.assertEqual((job.values["resolution"], job.values["latent"]), (1088, "empty"))
        self.assertTrue(job.prompt.startswith("Transform this set of images into an "
                                              "equirectangular 360 panorama. Scene: "))
        self.assertEqual([r["ref"] for r in job.references], ["location:kitchen"])

    def test_the_scene_is_one_short_sentence(self):
        p = R.pano_prompt("A long hall. With a second sentence.")
        self.assertTrue(p.endswith("Scene: A long hall."))
        long = R.pano_prompt(" ".join(["word"] * 80))
        self.assertEqual(len(long.split("Scene: ")[1].split()), R.PANO_WORDS)

    def test_the_qwen_graph_samples_an_empty_canvas(self):
        (job,) = self.plan()
        job.inputs = {"references": ["h3pipe/plate.png"]}
        with open(os.path.join(ROOT, "targets", "image", "qwen_image_21", "workflow.json"),
                  encoding="utf-8") as fh:
            g = R.image_graph(J.graph_from(json.load(fh)), job, None)
        sw = next(v for v in g.values() if v["class_type"] == "ComfySwitchNode")
        self.assertIs(sw["inputs"]["switch"], True)
        enc = next(v for v in g.values() if v["class_type"] == "TextEncodeQwenImage21")
        self.assertIn("images.image_1", enc["inputs"])
        empty = next(v for v in g.values() if v["class_type"] == "EmptyLatentImage")["inputs"]
        self.assertEqual((empty["width"], empty["height"]), R.PANO_SIZE)

    def test_it_lands_on_the_pano_view_and_is_never_picked(self):
        (job,) = self.plan()
        R.stage_references(self.s, job, None)
        take = R.start_gen(self.s, job)
        self.assertEqual(take.sidecar["source"], "pano")
        ref = R.find_ref(self.s, "location:kitchen")
        js = R.ref_json(self.s, ref)
        self.assertEqual([t["take"] for t in js["panos"]], [take.take])
        self.assertNotIn(take.take, [t["take"] for t in js["takes"] if t["source"] == "pano"])
        with self.assertRaisesRegex(R.RefError, "isn't a plate"):
            R.pick_take(self.s, ref, R.PANO_VIEW, take.take, force=True)

    def test_only_a_location(self):
        with self.assertRaisesRegex(R.RefError, "not a location"):
            R.plan_generate(self.s, R.GenRequest("subject:kettle", pano={}))


if __name__ == "__main__":
    unittest.main()


try:
    import numpy  # noqa: F401
    import PIL  # noqa: F401
    HAVE_NUMPY = True
except ImportError:
    HAVE_NUMPY = False


@unittest.skipUnless(HAVE_NUMPY, "the canvas helper needs numpy and PIL")
class KleinPanoTest(PanoTest):
    """`pano: {"engine": "klein"}`: the plate on a green canvas, outpainted by
    the ERP LoRA on the author's own graph (klein_erp_360)."""

    def test_the_plate_goes_on_a_green_canvas(self):
        (job,) = R.plan_generate(self.s, R.GenRequest("location:kitchen", pano={"engine": "klein"}),
                                 rng=random.Random(0))
        self.assertEqual(job.target.id, R.KLEIN_PANO_TARGET)
        self.assertEqual(job.view, R.PANO_VIEW)
        self.assertEqual((job.width, job.height), R.PANO_SIZE)
        self.assertEqual((job.steps, job.cfg), (20, 5.0))
        self.assertIn("base", job.model)
        self.assertEqual(job.loras, [{"name": R.KLEIN_PANO_LORA, "strength": R.KLEIN_PANO_STRENGTH}])
        self.assertEqual(job.prompt, R.KLEIN_PANO_PROMPT)
        canvas = job.references[0]["path"]
        self.assertEqual(R.image_size(canvas), R.PANO_SIZE)
        from PIL import Image
        im = Image.open(canvas).convert("RGB")
        self.assertEqual(im.getpixel((5, 5)), (0, 255, 0))          # a corner is green
        self.assertNotEqual(im.getpixel((1024, 512)), (0, 255, 0))   # the middle is the plate

    def test_the_graph_loads_the_canvas(self):
        (job,) = R.plan_generate(self.s, R.GenRequest("location:kitchen", pano={"engine": "klein"}),
                                 rng=random.Random(0))
        job.inputs = {"references": ["h3pipe/canvas.png"]}
        with open(os.path.join(ROOT, "targets", "image", "klein_erp_360", "workflow.json"),
                  encoding="utf-8") as fh:
            g = R.image_graph(J.graph_from(json.load(fh)), job, None)
        self.assertEqual(next(v for v in g.values() if v["class_type"] == "LoadImage")
                         ["inputs"]["image"], "h3pipe/canvas.png")
        ks = next(v for v in g.values() if v["class_type"] == "KSampler")["inputs"]
        self.assertEqual((ks["steps"], ks["cfg"], ks["sampler_name"]), (20, 5.0, "euler"))
        lo = [v for v in g.values() if v["class_type"] == "LoraLoaderModelOnly"]
        self.assertEqual([v["inputs"]["lora_name"] for v in lo], [R.KLEIN_PANO_LORA])

    def test_a_360_is_two_to_one(self):
        with self.assertRaisesRegex(R.RefError, "2:1"):
            R.plan_generate(self.s, R.GenRequest("location:kitchen", pano={"engine": "klein"},
                                                 size="1344x768"))
