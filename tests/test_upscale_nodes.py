"""Phase 13b's nodes (comfy_nodes/h3_upscale.py), run outside ComfyUI.

H3HoldAudio's mask, H3LoadTakeLatent round-tripping what H3SaveShot kept,
H3LoadTakeVideo's frames and audio, and H3SaveUpscale: the picture encoded,
the take's audio stream copied on bit for bit, and the .up.json closed. Needs
torch, numpy and PIL; the ones that make real video also need ffmpeg.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

try:
    import numpy as np  # noqa: F401
    import torch
except ImportError as exc:                               # pragma: no cover
    raise unittest.SkipTest(f"the upscale node tests need torch and numpy: {exc}")

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, "comfy_nodes"))
import h3_shotlist as N  # noqa: E402
import h3_upscale as UN  # noqa: E402
from test_save_node import FakeNested, clip, fake_comfy_nested  # noqa: E402

HAVE_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
needs_ffmpeg = unittest.skipUnless(HAVE_FFMPEG, "ffmpeg not on PATH")


def audio_md5(path: str) -> str:
    return subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-map", "0:a", "-c", "copy",
                           "-f", "md5", "-"], capture_output=True, text=True, check=True).stdout


class UpscaleNodesTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        os.makedirs(os.path.join(self.root, "renders", "sh010"))

    def tearDown(self):
        self._tmp.cleanup()

    def p(self, name):
        return os.path.join(self.root, "renders", "sh010", name)

    def take_mp4(self, audio=True) -> str:
        """A 24-frame take, with a sine-tone AAC track unless `audio` is False."""
        out = self.p("sh010_t01.mp4")
        cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
               "testsrc=size=96x48:rate=24:duration=1"]
        if audio:
            cmd += ["-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-c:a", "aac"]
        cmd += ["-frames:v", "24", "-c:v", "libx264", "-pix_fmt", "yuv420p", out]
        subprocess.run(cmd, check=True, capture_output=True)
        return out

    def test_hold_audio_masks_the_audio_stream_only(self):
        video, audio = torch.randn(1, 4, 3, 6, 12), torch.randn(1, 8, 20)
        with mock.patch.dict(sys.modules, fake_comfy_nested()):
            (out,) = UN.H3HoldAudio().hold({"samples": FakeNested((video, audio)), "x": 1})
        vm, am = out["noise_mask"].tensors
        self.assertTrue(torch.equal(vm, torch.ones_like(video)))    # the picture re-sampled
        self.assertTrue(torch.equal(am, torch.zeros_like(audio)))   # the audio kept
        self.assertEqual(out["x"], 1)
        with mock.patch.dict(sys.modules, fake_comfy_nested()), self.assertRaises(ValueError):
            UN.H3HoldAudio().hold({"samples": video})

    def test_load_take_latent(self):
        video, audio = torch.randn(1, 4, 3, 6, 12), torch.randn(1, 8, 20)
        N.save_latent({"samples": FakeNested((video, audio))}, self.p("sh010_t01.latent.safetensors"))
        with mock.patch.dict(sys.modules, fake_comfy_nested()):
            (lat,) = UN.H3LoadTakeLatent().load(self.root, os.path.join(
                "renders", "sh010", "sh010_t01.latent.safetensors"))
        self.assertTrue(torch.equal(lat["samples"].tensors[0], video))
        self.assertTrue(torch.equal(lat["samples"].tensors[1], audio))

    @needs_ffmpeg
    def test_load_take_video(self):
        self.take_mp4()
        images, audio = UN.H3LoadTakeVideo().load(self.root, "renders/sh010/sh010_t01.mp4", "")
        self.assertEqual(tuple(images.shape), (24, 48, 96, 3))
        self.assertTrue(0.0 <= float(images.min()) and float(images.max()) <= 1.0)
        self.assertGreater(audio["sample_rate"], 0)
        self.assertGreater(audio["waveform"].shape[-1], 0)

    @needs_ffmpeg
    def test_save_upscale_copies_the_takes_audio(self):
        src = self.take_mp4()
        with open(self.p("sh010_t01.up.json"), "w", encoding="utf-8") as fh:
            json.dump({"shot": "sh010", "take": 1, "status": "queued", "scale": 2.0}, fh)
        (status,) = UN.H3SaveUpscale().save(
            clip(24), self.root, "renders/sh010/sh010_t01.mp4", "renders/sh010/sh010_t01.up.mp4",
            24.0, "renders/sh010/sh010_t01.up.json")
        self.assertIn("take's audio copied", status)
        out = self.p("sh010_t01.up.mp4")
        self.assertEqual(audio_md5(out), audio_md5(src))           # bit for bit
        rec = json.load(open(self.p("sh010_t01.up.json"), encoding="utf-8"))
        self.assertEqual(rec["status"], "ok")
        self.assertEqual((rec["frames"], rec["width"], rec["height"]), (24, 96, 48))
        self.assertEqual((rec["mp4"], rec["audio"], rec["scale"]), ("sh010_t01.up.mp4", "copied", 2.0))
        self.assertEqual([f for f in os.listdir(os.path.dirname(out)) if f.startswith(".tmp_")], [])

    @needs_ffmpeg
    def test_save_upscale_of_a_mute_take(self):
        self.take_mp4(audio=False)
        (status,) = UN.H3SaveUpscale().save(
            clip(24), self.root, "renders/sh010/sh010_t01.mp4", "renders/sh010/sh010_t01.up.mp4",
            24.0, "renders/sh010/sh010_t01.up.json")
        self.assertIn("the take is mute", status)
        self.assertTrue(os.path.isfile(self.p("sh010_t01.up.mp4")))
        rec = json.load(open(self.p("sh010_t01.up.json"), encoding="utf-8"))
        self.assertEqual((rec["status"], rec["audio"]), ("ok", "none"))


if __name__ == "__main__":
    unittest.main()
