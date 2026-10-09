"""
P4: a ref's settings beyond prompt / seed / model / LoRAs / steps -- the
picture's size, cfg, the negative and the sampler knobs (`params`) -- set as an
override (refs/_overrides.json) or per request, each only where the image target
has it.
"""
from __future__ import annotations

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

FIXTURE = os.path.join(HERE, "fixtures", "kitchen_sink")


class SettingsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ep = os.path.join(self._tmp.name, "ks01")
        os.makedirs(self.ep)
        shutil.copy(os.path.join(FIXTURE, "series.json"), self.ep)
        shutil.copy(os.path.join(FIXTURE, "script.md"), os.path.join(self.ep, "ks01.md"))
        import h3edit as E
        self.assertTrue(E.build_episode(self.ep)["ok"])
        self.s = R.load_series(self.ep)

    def tearDown(self):
        self._tmp.cleanup()

    def override(self, ref_id, view=None, **fields):
        ref = R.find_ref(self.s, ref_id)
        data = R.load_overrides(ref.home)
        R.set_ref_override(data, ref, view, fields, None)
        R.save_overrides(ref.home, data)
        return data

    def plan(self, ref_id, view=None, **kw):
        return R.plan_generate(self.s, R.GenRequest(ref_id, view, **kw), rng=random.Random(0))

    def test_size_cfg_and_sampler_from_the_override(self):
        self.override("location:kitchen", size="1536x640", cfg=3.5,
                      params={"sampler": "er_sde", "scheduler": "beta"})
        (job,) = self.plan("location:kitchen", target="qwen_image_21")
        self.assertEqual((job.width, job.height), (1536, 640))
        self.assertEqual(job.cfg, 3.5)
        self.assertEqual((job.values["sampler"], job.values["scheduler"]), ("er_sde", "beta"))
        self.assertTrue({"size", "cfg", "params"} <= set(job.overridden))

    def test_the_request_beats_the_override(self):
        self.override("location:kitchen", size="1536x640", cfg=3.5)
        (job,) = self.plan("location:kitchen", size="1024x1024", cfg=1.0)
        self.assertEqual((job.width, job.height, job.cfg), (1024, 1024, 1.0))

    def test_a_knob_the_target_lacks_is_noted_not_sent(self):
        self.override("location:kitchen", params={"denoise": 0.8})
        (job,) = self.plan("location:kitchen", target="flux2_klein_edit")
        self.assertNotIn("denoise", job.values)
        self.assertTrue(any("no denoise setting" in n for n in job.notes))

    def test_a_negative_override(self):
        self.override("location:kitchen", negative="people, text")
        (job,) = self.plan("location:kitchen", target="qwen_image_21")
        self.assertEqual((job.negative, job.negative_source), ("people, text", "override"))

    def test_a_view_names_its_own_size(self):
        self.override("subject:ada", "04_face", size="768x1024")
        (job,) = self.plan("subject:ada", "04_face")
        self.assertEqual((job.width, job.height), (768, 1024))
        self.assertIn("Output 768x1024", job.prompt)
        (side,) = self.plan("subject:ada", "02_side")
        self.assertEqual((side.width, side.height), (1024, 1024))

    def test_bad_values_are_refused(self):
        for fields, why in (({"size": "big"}, "WIDTHxHEIGHT"), ({"size": "100x100"}, "256"),
                            ({"cfg": "3"}, "cfg"), ({"cfg": 99}, "cfg"),
                            ({"params": {"lora": 1}}, "not lora"),
                            ({"params": {"denoise": "high"}}, "number"),
                            ({"negative": 3}, "negative")):
            with self.subTest(fields=fields), self.assertRaisesRegex(R.RefError, why):
                self.override("location:kitchen", **fields)
        with self.assertRaisesRegex(R.RefError, "voice"):
            self.override("voice:ada", size="512x512")

    def test_clearing_a_field(self):
        self.override("location:kitchen", cfg=2.0)
        data = self.override("location:kitchen", cfg=None)
        self.assertNotIn("location:kitchen", data["refs"])

    def test_effective_and_the_take_show_the_settings(self):
        self.override("location:kitchen", cfg=2.5, params={"sampler": "euler_ancestral"})
        ref = R.find_ref(self.s, "location:kitchen")
        eff = R.effective(self.s, ref, None, R.load_overrides(ref.home))
        self.assertEqual(eff["cfg"], 2.5)
        (job,) = self.plan("location:kitchen", target="qwen_image_21")
        take = R.start_gen(self.s, job)
        js = R.take_json(self.ep, ref, R.get_take(ref, None, take.take))
        self.assertEqual(js["cfg"], 2.5)
        self.assertEqual(js["params"]["sampler"], "euler_ancestral")


if __name__ == "__main__":
    unittest.main()
