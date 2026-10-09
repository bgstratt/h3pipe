"""
A continuity shot's upscale (h3upscale.set_continuity): its take started from
the previous shot's last low-res frame, so its upscale starts from that shot's
UPSCALED last frame (kept by H3SaveUpscale's `last_frame`, read when it runs by
H3LoadTakeFrame), and the two upscales meet on one picture instead of jumping
in colour at the cut. Master queues a source before the shot that continues it.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "comfy_nodes"))

import h3master as M  # noqa: E402
import h3takes as T  # noqa: E402
import h3upscale as U  # noqa: E402
import test_phase13 as P13  # noqa: E402
from test_api import ApiTest  # noqa: E402


class GraphTest(ApiTest):
    final_take = P13.UpscaleTest.final_take
    base = P13.UpscaleTest.base

    def test_every_upscale_keeps_its_last_frame(self):
        t = self.final_take()
        g = U.upscale_graph(self.base(), U.plan_upscale(self.ep, t))
        self.assertTrue(g["up_save"]["inputs"]["last_frame"].endswith(".up_last.png"))
        self.assertEqual(os.path.normpath(os.path.join(self.ep, g["up_save"]["inputs"]["last_frame"])),
                         os.path.normpath(t.paths.up_last))

    def test_a_continuity_shot_starts_from_the_sources_upscale(self):
        src = self.final_take("sh010")
        t = self.final_take("sh020")
        with mock.patch.object(U, "continuity_source", return_value=(src, "h3pipe/low.png")):
            up = U.plan_upscale(self.ep, t)
        self.assertIs(up.first_from, src)
        self.assertTrue(any("sh010 t01's upscaled last frame" in n for n in up.notes))
        self.assertEqual(U.settings_of(up)["first_from"], "sh010 t01")
        g = {"7": {"class_type": "LoadImage", "inputs": {"image": "h3pipe/low.png"}},
             "8": {"class_type": "LoadImage", "inputs": {"image": "h3pipe/last.png"}}}
        U.continuity_frame(g, up)
        self.assertEqual(g["7"]["class_type"], "H3LoadTakeFrame")
        self.assertEqual(g["7"]["inputs"]["fallback"], "h3pipe/low.png")
        self.assertTrue(g["7"]["inputs"]["image_file"].endswith("sh010_t01.up_last.png"))
        self.assertEqual(g["8"]["class_type"], "LoadImage")          # the last keyframe is left

    def test_no_source_no_change(self):
        t = self.final_take()
        with mock.patch.object(U, "continuity_source", return_value=None):
            up = U.plan_upscale(self.ep, t)
        self.assertIsNone(up.first_from)
        self.assertNotIn("first_from", U.settings_of(up))


class OrderTest(unittest.TestCase):
    def row(self, shot, target, src=None):
        take = SimpleNamespace(shot=shot, take=1)
        job = SimpleNamespace(take=take, first_from=src, method="latent", target=SimpleNamespace(id=target),
                              then_method="pixel", then_model="", seedvr2_model="", pixel_model="")
        return SimpleNamespace(job=job, shot=shot)

    def test_target_order_puts_a_continuation_after_its_source(self):
        a = self.row("sh010", "minimax_h3_fl2va")
        b = self.row("sh020", "minimax_h3_ref2va")
        c = self.row("sh030", "minimax_h3_fl2va", src=b.job.take)       # continues sh020
        out = M.in_order([a, b, c], "target")
        shots = [r.shot for r in out]
        self.assertLess(shots.index("sh020"), shots.index("sh030"))
        self.assertEqual(M.in_order([a, b, c], "cut"), [a, b, c])


class NodeTest(unittest.TestCase):
    def setUp(self):
        try:
            import torch  # noqa: F401
            from PIL import Image  # noqa: F401
            import h3_upscale
        except Exception as e:                  # noqa: BLE001 (no torch here)
            self.skipTest(f"needs torch and PIL: {e}")
        self.N = h3_upscale
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def test_a_kept_frame_loads_back(self):
        import torch
        frame = torch.zeros(4, 6, 3)
        frame[..., 0] = 1.0
        path = os.path.join(self._tmp.name, "renders", "sh010_t01.up_last.png")
        self.N.save_frame(frame, path)
        img, mask = self.N.H3LoadTakeFrame().load(self._tmp.name, "renders/sh010_t01.up_last.png", "")
        self.assertEqual(tuple(img.shape), (1, 4, 6, 3))
        self.assertAlmostEqual(float(img[0, 0, 0, 0]), 1.0, places=2)
        self.assertEqual(tuple(mask.shape), (1, 4, 6))

    def test_no_frame_and_no_fallback_says_so(self):
        with self.assertRaisesRegex(FileNotFoundError, "isn't there yet"):
            self.N.H3LoadTakeFrame().load(self._tmp.name, "renders/missing.png", "")


if __name__ == "__main__":
    unittest.main()
