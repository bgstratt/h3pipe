"""
minimax_h3_still: the video model used as an image target (docs/PLAN.md, Phase
10b). It renders the shortest clip the H3 node allows and keeps frame 0, so a
variant's view can be edited out of the character's own sheet without a second
model stack installed.

The graph is checked structurally — it is the Ref2VA workflow with the
shot-shaped parts removed, so what matters is that the model chain survived,
that the shot list is gone, that the references land on the H3 node's autogrow
slots, and that frame 0 is what reaches the saver.
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
TID = "minimax_h3_still"
WORKFLOW = os.path.join(ROOT, "targets", "image", TID, "workflow.json")
VIDEO_WORKFLOW = os.path.join(ROOT, "targets", "video", "minimax_h3_ref2va", "workflow.json")


def api_graph(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return J.ui_to_api(json.load(fh))


class TargetTest(unittest.TestCase):
    def setUp(self):
        self.t = TG.load_target(TID, "image")

    def test_it_is_an_image_target_that_reads_references(self):
        self.assertEqual(self.t.kind, "image")
        self.assertEqual(self.t.capabilities()["mode"], "edit")
        self.assertEqual(self.t.capabilities()["max_refs"], 9)
        self.assertIn(TID, {t.id for t in TG.list_targets("image")})

    def test_it_is_not_offered_as_a_video_target(self):
        self.assertNotIn(TID, {t.id for t in TG.list_targets("video")})

    def test_the_preset_renders_the_shortest_clip_the_node_allows(self):
        p = self.t.presets["final"]
        self.assertEqual(p.extra["length"], 5)
        self.assertEqual(p.steps, 8)

    def test_every_model_it_names_has_a_download(self):
        named = {v for k, v in self.t.presets["final"].extra.items()
                 if isinstance(v, str) and v.endswith(".safetensors")}
        named.add(self.t.presets["final"].model)
        named.add(self.t.presets["final"].lora)
        for f in {n for n in named if n}:
            self.assertIn(f, self.t.spec["downloads"], f)


class GraphTest(unittest.TestCase):
    def setUp(self):
        self.g = api_graph(WORKFLOW)
        self.types = {v["class_type"] for v in self.g.values()}

    def test_the_model_chain_is_the_video_targets(self):
        """Same nodes, so this is the stack the pipeline already renders with."""
        video = {v["class_type"] for v in api_graph(VIDEO_WORKFLOW).values()}
        chain = {"UNETLoader", "LoraLoaderModelOnly", "MiniMaxH3SigmaShift", "H3SLAAttention",
                 "CLIPLoader", "VAELoader", "MiniMaxH3ReferenceToVideo", "RandomNoise",
                 "BasicGuider", "KSamplerSelect", "BasicScheduler", "SamplerCustomAdvanced",
                 "VAEDecode"}
        self.assertTrue(chain <= video)
        self.assertTrue(chain <= self.types)

    def test_nothing_shot_shaped_survives(self):
        for gone in ("H3ShotListLoader", "H3ShotInfo", "H3SaveShot", "CreateVideo",
                     "SaveVideo", "VAEDecodeAudio"):
            self.assertNotIn(gone, self.types, gone)

    def test_the_saver_gets_frame_zero(self):
        frame = next(v for v in self.g.values() if v["class_type"] == "ImageFromBatch")
        self.assertEqual((frame["inputs"]["batch_index"], frame["inputs"]["length"]), (0, 1))
        decode = next(k for k, v in self.g.items() if v["class_type"] == "VAEDecode")
        self.assertEqual(frame["inputs"]["image"][0], decode)
        save = next(v for v in self.g.values() if v["class_type"] == "SaveImage")
        self.assertEqual(save["inputs"]["images"][0],
                         next(k for k, v in self.g.items()
                              if v["class_type"] == "ImageFromBatch"))

    def test_the_h3_node_takes_its_values_from_widgets(self):
        """No shot list to drive them any more."""
        h3 = next(v for v in self.g.values()
                  if v["class_type"] == "MiniMaxH3ReferenceToVideo")
        for k in ("prompt", "width", "height", "length"):
            self.assertNotIsInstance(h3["inputs"][k], list, k)
        self.assertEqual(h3["inputs"]["length"], 5)

    def test_the_reference_slot_is_spelled_as_the_video_path_spells_it(self):
        """The autogrow group's dotted name is what the server already takes."""
        video = next(v for v in api_graph(VIDEO_WORKFLOW).values()
                     if v["class_type"] == "MiniMaxH3ReferenceToVideo")
        self.assertIn("ref_images.ref_image_0", video["inputs"])
        h3 = next(v for v in self.g.values()
                  if v["class_type"] == "MiniMaxH3ReferenceToVideo")
        self.assertIn("ref_images.ref_image_0", h3["inputs"])


class PatchGraphTest(unittest.TestCase):
    def setUp(self):
        self.t = TG.load_target(TID, "image")
        self.g = api_graph(WORKFLOW)

    def h3(self, g=None):
        g = self.g if g is None else g
        return next(v for v in g.values()
                    if v["class_type"] == "MiniMaxH3ReferenceToVideo")

    def slots(self, g=None):
        return sorted(k for k in self.h3(g)["inputs"] if k.startswith("ref_images."))

    def test_one_reference_fills_the_first_slot(self):
        self.t.patch_graph(self.g, None, {"references": ["a.png"]})
        self.assertEqual(self.slots(), ["ref_images.ref_image_0"])
        load = self.h3()["inputs"]["ref_images.ref_image_0"][0]
        self.assertEqual(self.g[load]["inputs"]["image"], "a.png")

    def test_more_references_grow_the_group(self):
        self.t.patch_graph(self.g, None, {"references": ["a.png", "b.png", "c.png"]})
        self.assertEqual(self.slots(), ["ref_images.ref_image_0", "ref_images.ref_image_1",
                                        "ref_images.ref_image_2"])
        got = [self.g[self.h3()["inputs"][s][0]]["inputs"]["image"] for s in self.slots()]
        self.assertEqual(got, ["a.png", "b.png", "c.png"])
        self.assertEqual(sum(1 for v in self.g.values()
                             if v["class_type"] == "LoadImage"), 3)

    def test_no_references_leaves_text_to_image(self):
        self.t.patch_graph(self.g, None, {"references": []})
        self.assertEqual(self.slots(), [])
        self.assertFalse(any(v["class_type"] == "LoadImage" for v in self.g.values()))

    def test_it_never_exceeds_max_refs(self):
        self.t.patch_graph(self.g, None, {"references": [f"{i}.png" for i in range(12)]})
        self.assertEqual(len(self.slots()), self.t.capabilities()["max_refs"])


class JobTest(unittest.TestCase):
    """A variant's view, end to end, on this target."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ep = os.path.join(self._tmp.name, "ks01")
        os.makedirs(self.ep)
        shutil.copy(os.path.join(FIXTURE, "series.json"), self.ep)
        shutil.copy(os.path.join(FIXTURE, "script.md"), os.path.join(self.ep, "ks01.md"))
        import h3edit as E
        self.assertTrue(E.build_episode(self.ep)["ok"])
        self.s = R.load_series(self.ep)
        base = R.find_ref(self.s, "subject:ada")
        src = os.path.join(self._tmp.name, "v.png")
        with open(src, "wb") as fh:
            fh.write(png_bytes(64, 64))
        for v in R.VIEW_TAGS:
            t = R.import_take(self.s, base, v, src)
            R.pick_take(self.s, base, v, t.take)

    def tearDown(self):
        self._tmp.cleanup()

    def graph(self, ref_id: str, view: str):
        job = R.plan_generate(self.s, R.GenRequest(ref_id, view, target=TID),
                              rng=random.Random(0))[0]
        job.inputs = {"references": [f"ref{i}.png" for i in range(len(job.references))]}
        return job, R.image_graph(api_graph(WORKFLOW), job,
                                  os.path.join(self._tmp.name, "side.json"))

    def test_a_variant_view_edits_the_bases_view(self):
        job, g = self.graph("subject:ada_wet", "03_back")
        self.assertEqual([r["name"] for r in job.references], ["Ada"])
        h3 = next(v for v in g.values()
                  if v["class_type"] == "MiniMaxH3ReferenceToVideo")
        self.assertIn("ref_images.ref_image_0", h3["inputs"])
        self.assertIn("The reference image is Ada:", h3["inputs"]["prompt"])
        self.assertEqual((h3["inputs"]["width"], h3["inputs"]["height"]), (1024, 1024))
        self.assertEqual(h3["inputs"]["length"], 5)

    def test_the_job_values_reach_the_right_nodes(self):
        job, g = self.graph("subject:ada_wet", "03_back")
        seed = next(v for v in g.values() if v["class_type"] == "RandomNoise")
        steps = next(v for v in g.values() if v["class_type"] == "BasicScheduler")
        self.assertEqual(seed["inputs"]["noise_seed"], job.seed)
        self.assertEqual(steps["inputs"]["steps"], job.steps)
        self.assertEqual(job.seed, R.seed_for("ada"))

    def test_the_take_is_saved_as_an_image(self):
        _job, g = self.graph("subject:ada_wet", "03_back")
        saver = next(v for v in g.values() if v["class_type"] == "H3SaveRefTake")
        self.assertTrue(saver["inputs"]["sidecar"])
        self.assertFalse(any(v["class_type"] == "SaveImage" for v in g.values()))

    def test_a_plain_character_view_has_nothing_to_edit_from(self):
        job, g = self.graph("subject:bo", "01_threequarter")
        self.assertEqual(job.references, [])
        self.assertFalse(any(v["class_type"] == "LoadImage" for v in g.values()))
        h3 = next(v for v in g.values()
                  if v["class_type"] == "MiniMaxH3ReferenceToVideo")
        self.assertFalse([k for k in h3["inputs"] if k.startswith("ref_images.")])


if __name__ == "__main__":
    unittest.main()
