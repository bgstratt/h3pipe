"""
qwen_image_21: Qwen-Image 2.1 as an image target — one graph that is text to
image or an edit, decided by whether the ref has anything to edit from.

`TextEncodeQwenImage21` returns positive, negative and a LATENT spliced from
its reference images; `ComfySwitchNode` picks that latent or an
`EmptyLatentImage`. So what matters here is that references land on the
encoder's autogrow slots (`images.image_1`, counting from one), that the
switch follows, and that a ref with nothing to edit from still generates the
way krea2 would.
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
import targets as TG  # noqa: E402
from test_render import png_bytes  # noqa: E402

FIXTURE = os.path.join(HERE, "fixtures", "kitchen_sink")
TID = "qwen_image_21"
WORKFLOW = os.path.join(ROOT, "targets", "image", TID, "workflow.json")


def api_graph() -> dict:
    with open(WORKFLOW, encoding="utf-8") as fh:
        return J.graph_from(json.load(fh))


class TargetTest(unittest.TestCase):
    def setUp(self):
        self.t = TG.load_target(TID, "image")

    def test_it_is_an_image_target_that_reads_references(self):
        self.assertEqual(self.t.kind, "image")
        self.assertEqual(self.t.capabilities()["mode"], "edit")
        self.assertEqual(self.t.capabilities()["max_refs"], 16)
        self.assertIn(TID, {t.id for t in TG.list_targets("image")})

    def test_it_takes_a_negative_prompt(self):
        """The encoder has a real negative input, unlike the other edit
        targets — inert at the preset's cfg 1, live once cfg is raised."""
        self.assertTrue(self.t.capabilities()["negative_prompt"])

    def test_the_preset_is_the_shipped_template(self):
        p = self.t.presets["final"]
        self.assertEqual((p.steps, p.extra["cfg"]), (25, 1.0))
        self.assertEqual(p.extra["denoise"], 1.0)
        self.assertEqual(p.extra["resolution"], 0)

    def test_every_model_it_names_has_a_download(self):
        p = self.t.presets["final"]
        named = {p.model} | {v for v in p.extra.values()
                             if isinstance(v, str) and v.endswith(".safetensors")}
        for f in {n for n in named if n}:
            self.assertIn(f, self.t.spec["downloads"], f)

    def test_the_downloads_say_where_each_file_goes(self):
        for name, d in self.t.spec["downloads"].items():
            if name.startswith("_"):
                continue
            with self.subTest(file=name):
                self.assertIn(d["folder"], ("diffusion_models", "text_encoders", "vae"))
                self.assertTrue(d["url"].startswith("https://huggingface.co/"))
                self.assertTrue(d["url"].endswith(name), d["url"])


class GraphTest(unittest.TestCase):
    def setUp(self):
        self.g = api_graph()
        self.types = {v["class_type"] for v in self.g.values()}

    def test_it_has_both_ways_into_the_sampler(self):
        for t in ("TextEncodeQwenImage21", "EmptyLatentImage", "ComfySwitchNode",
                  "KSampler", "QwenImage21Cache", "VAEDecode"):
            self.assertIn(t, self.types, t)
        sw = next(v for v in self.g.values() if v["class_type"] == "ComfySwitchNode")
        enc = next(k for k, v in self.g.items() if v["class_type"] == "TextEncodeQwenImage21")
        empty = next(k for k, v in self.g.items() if v["class_type"] == "EmptyLatentImage")
        self.assertEqual(sw["inputs"]["on_false"], [enc, 2])    # the references' latent
        self.assertEqual(sw["inputs"]["on_true"], [empty, 0])   # text to image

    def test_the_sampler_takes_both_conditionings_from_the_encoder(self):
        enc = next(k for k, v in self.g.items() if v["class_type"] == "TextEncodeQwenImage21")
        ks = next(v for v in self.g.values() if v["class_type"] == "KSampler")
        self.assertEqual(ks["inputs"]["positive"], [enc, 0])
        self.assertEqual(ks["inputs"]["negative"], [enc, 1])

    def test_the_model_goes_through_the_prefix_cache(self):
        cache = next(k for k, v in self.g.items() if v["class_type"] == "QwenImage21Cache")
        ks = next(v for v in self.g.values() if v["class_type"] == "KSampler")
        self.assertEqual(ks["inputs"]["model"], [cache, 0])


class PatchGraphTest(unittest.TestCase):
    def setUp(self):
        self.t = TG.load_target(TID, "image")
        self.g = api_graph()

    def enc(self):
        return next(v for v in self.g.values()
                    if v["class_type"] == "TextEncodeQwenImage21")

    def slots(self):
        return sorted(k for k in self.enc()["inputs"] if k.startswith("images."))

    def switch(self):
        return next(v for v in self.g.values()
                    if v["class_type"] == "ComfySwitchNode")["inputs"]["switch"]

    def test_one_reference_fills_the_first_slot_and_flips_the_switch(self):
        self.t.patch_graph(self.g, None, {"references": ["a.png"]})
        self.assertEqual(self.slots(), ["images.image_1"])      # counts from one
        self.assertIs(self.switch(), False)                     # the references' latent
        load = self.enc()["inputs"]["images.image_1"][0]
        self.assertEqual(self.g[load]["inputs"]["image"], "a.png")

    def test_more_references_grow_the_group(self):
        self.t.patch_graph(self.g, None, {"references": ["a.png", "b.png", "c.png"]})
        self.assertEqual(self.slots(),
                         ["images.image_1", "images.image_2", "images.image_3"])
        got = [self.g[self.enc()["inputs"][s][0]]["inputs"]["image"] for s in self.slots()]
        self.assertEqual(got, ["a.png", "b.png", "c.png"])

    def test_no_references_is_text_to_image(self):
        self.t.patch_graph(self.g, None, {"references": []})
        self.assertEqual(self.slots(), [])
        self.assertIs(self.switch(), True)                      # the empty latent
        self.assertFalse(any(v["class_type"] == "LoadImage" for v in self.g.values()))

    def test_it_never_exceeds_max_refs(self):
        self.t.patch_graph(self.g, None, {"references": [f"{i}.png" for i in range(20)]})
        self.assertEqual(len(self.slots()), self.t.capabilities()["max_refs"])


class JobTest(unittest.TestCase):
    """The two jobs this target exists to serve, end to end."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ep = os.path.join(self._tmp.name, "ks01")
        os.makedirs(self.ep)
        shutil.copy(os.path.join(FIXTURE, "series.json"), self.ep)
        shutil.copy(os.path.join(FIXTURE, "script.md"), os.path.join(self.ep, "ks01.md"))
        import h3edit as E
        self.assertTrue(E.build_episode(self.ep)["ok"])
        s = R.load_series(self.ep)
        base = R.find_ref(s, "subject:ada")
        src = os.path.join(self._tmp.name, "v.png")
        with open(src, "wb") as fh:
            fh.write(png_bytes(64, 64))
        for v in R.VIEW_TAGS:
            t = R.import_take(s, base, v, src)
            R.pick_take(s, base, v, t.take)
        self.s = R.load_series(self.ep)

    def tearDown(self):
        self._tmp.cleanup()

    def build(self, ref_id: str, view: str):
        job = R.plan_generate(self.s, R.GenRequest(ref_id, view, target=TID),
                              rng=random.Random(0))[0]
        job.inputs = {"references": [f"ref{i}.png" for i in range(len(job.references))]}
        return job, R.image_graph(api_graph(), job, os.path.join(self._tmp.name, "side.json"))

    def node(self, g, ct):
        return next(v for v in g.values() if v["class_type"] == ct)

    def test_a_variant_view_is_an_edit_of_the_bases_view(self):
        job, g = self.build("subject:ada_wet", "03_back")
        self.assertEqual([r["name"] for r in job.references], ["Ada"])
        enc = self.node(g, "TextEncodeQwenImage21")
        self.assertIn("images.image_1", enc["inputs"])
        self.assertIs(self.node(g, "ComfySwitchNode")["inputs"]["switch"], False)
        self.assertIn("The reference image is Ada:", enc["inputs"]["prompt"])
        self.assertEqual(job.seed, R.seed_for("ada"))

    def test_a_plain_character_view_is_text_to_image(self):
        job, g = self.build("subject:bo", "01_threequarter")
        self.assertEqual(job.references, [])
        self.assertIs(self.node(g, "ComfySwitchNode")["inputs"]["switch"], True)
        self.assertTrue(self.node(g, "TextEncodeQwenImage21")["inputs"]["prompt"]
                        .startswith("A single character reference view"))
        empty = self.node(g, "EmptyLatentImage")["inputs"]
        self.assertEqual((empty["width"], empty["height"]), (1024, 1024))

    def test_the_take_is_saved_as_an_image(self):
        _job, g = self.build("subject:ada_wet", "03_back")
        self.assertTrue(self.node(g, "H3SaveRefTake")["inputs"]["sidecar"])
        self.assertFalse(any(v["class_type"] == "SaveImage" for v in g.values()))

    def test_the_job_values_reach_the_sampler(self):
        job, g = self.build("subject:ada_wet", "03_back")
        ks = self.node(g, "KSampler")
        self.assertEqual(ks["inputs"]["seed"], job.seed)
        self.assertEqual(ks["inputs"]["steps"], job.steps)
        self.assertEqual(ks["inputs"]["denoise"], 1.0)


if __name__ == "__main__":
    unittest.main()
