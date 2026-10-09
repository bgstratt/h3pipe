"""
qwen_rapid_aio: Phr00t's Qwen-Image-Edit Rapid AIO as an image target -- one
checkpoint, references on the stock TextEncodeQwenImageEditPlus's image1..image3
of both encoders, four steps at cfg 1.
"""
from __future__ import annotations

import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import h3jobs as J  # noqa: E402
import targets as TG  # noqa: E402

TID = "qwen_rapid_aio"


def graph() -> dict:
    with open(os.path.join(ROOT, "targets", "image", TID, "workflow.json"), encoding="utf-8") as fh:
        return J.graph_from(json.load(fh))


class RapidAioTest(unittest.TestCase):
    def setUp(self):
        self.t = TG.load_target(TID, "image")

    def nodes(self, g, ct):
        return [v for v in g.values() if v["class_type"] == ct]

    def test_an_edit_target_of_three_pictures(self):
        c = self.t.capabilities()
        self.assertEqual((c["mode"], c["max_refs"]), ("edit", 3))
        p = self.t.presets["final"]
        self.assertEqual((p.steps, p.extra["cfg"], p.extra["sampler"], p.extra["scheduler"]),
                         (4, 1.0, "er_sde", "beta"))
        self.assertEqual(self.t.spec["downloads"][p.model]["folder"], "checkpoints")

    def test_both_encoders_read_every_picture(self):
        g = graph()
        self.t.patch_graph(g, None, {"references": ["a.png", "b.png", "c.png", "d.png"]})
        loads = self.nodes(g, "LoadImage")
        self.assertEqual(sorted(v["inputs"]["image"] for v in loads), ["a.png", "b.png", "c.png"])
        for enc in self.nodes(g, "TextEncodeQwenImageEditPlus"):
            wired = [g[enc["inputs"][f"image{i}"][0]]["inputs"]["image"] for i in (1, 2, 3)]
            self.assertEqual(wired, ["a.png", "b.png", "c.png"])

    def test_no_pictures_is_text_to_image(self):
        g = graph()
        self.t.patch_graph(g, None, {})
        self.assertEqual(self.nodes(g, "LoadImage"), [])
        for enc in self.nodes(g, "TextEncodeQwenImageEditPlus"):
            self.assertNotIn("image1", enc["inputs"])

    def test_the_binding_sets_the_job(self):
        g = graph()
        b = self.t.binding
        for name, v in (("prompt", "Edit image 1."), ("steps", 6), ("width", 1344),
                        ("sampler", "euler_ancestral"), ("model", "x.safetensors")):
            J.patch_param(g, b, name, v)
        pos = next(v for v in self.nodes(g, "TextEncodeQwenImageEditPlus")
                   if v["_meta"]["title"] == "Positive prompt")
        self.assertEqual(pos["inputs"]["prompt"], "Edit image 1.")
        neg = next(v for v in self.nodes(g, "TextEncodeQwenImageEditPlus")
                   if v["_meta"]["title"] == "Negative prompt")
        self.assertEqual(neg["inputs"]["prompt"], "")
        ks = self.nodes(g, "KSampler")[0]["inputs"]
        self.assertEqual((ks["steps"], ks["sampler_name"]), (6, "euler_ancestral"))
        self.assertEqual(self.nodes(g, "EmptySD3LatentImage")[0]["inputs"]["width"], 1344)
        self.assertEqual(self.nodes(g, "CheckpointLoaderSimple")[0]["inputs"]["ckpt_name"],
                         "x.safetensors")


if __name__ == "__main__":
    unittest.main()
