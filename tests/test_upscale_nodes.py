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


class FakeUpscaleModel:
    """An upscale model's shape (scale, patcher.load_device, callable on BCHW):
    repeats pixels `scale` times and counts the batches it sees."""

    def __init__(self, scale: int = 4):
        import types
        self.scale = scale
        self.patcher = types.SimpleNamespace(load_device="cpu")
        self.calls: list[int] = []

    def __call__(self, x):
        self.calls.append(int(x.shape[0]))
        return x.repeat_interleave(self.scale, 2).repeat_interleave(self.scale, 3)


def fake_upscaler_modules() -> dict:
    """sys.modules entries for comfy.utils and comfy.model_management, so
    H3PixelUpscale runs outside ComfyUI. `loads` counts load_models_gpu calls."""
    import types
    comfy = types.ModuleType("comfy")
    utils = types.ModuleType("comfy.utils")
    mm = types.ModuleType("comfy.model_management")

    def common_upscale(s, w, h, method, crop):
        return torch.nn.functional.interpolate(s, size=(h, w), mode="bilinear", align_corners=False)

    def tiled_scale(samples, function, tile_x=64, tile_y=64, overlap=8, upscale_amount=4,
                    out_channels=3, output_device="cpu", pbar=None):
        return function(samples)

    class ProgressBar:
        def __init__(self, total):
            self.total, self.done = total, 0

        def update(self, n):
            self.done += n

    utils.common_upscale, utils.tiled_scale, utils.ProgressBar = common_upscale, tiled_scale, ProgressBar
    mm.loads = []
    mm.load_models_gpu = lambda models, **kw: mm.loads.append(len(models))
    mm.intermediate_device = lambda: "cpu"

    def raise_non_oom(e):
        raise e
    mm.raise_non_oom = raise_non_oom
    comfy.utils, comfy.model_management = utils, mm
    return {"comfy": comfy, "comfy.utils": utils, "comfy.model_management": mm}


class EncoderTest(unittest.TestCase):
    def test_encoder_choice(self):
        both = {"h264_nvenc", "hevc_nvenc"}
        with mock.patch.object(UN, "nvenc_encoders", return_value=both):
            self.assertEqual(UN.encoder_args("auto", 3840, 2176)[0], "h264_nvenc")
            name, args = UN.encoder_args("auto", 5376, 3072)             # past NVENC H.264's 4096
            self.assertEqual(name, "hevc_nvenc")
            self.assertIn("hvc1", args)
            self.assertEqual(UN.encoder_args("nvenc", 1920, 1088)[0], "h264_nvenc")
            self.assertEqual(UN.encoder_args("x264", 1920, 1088)[0], "libx264")
        with mock.patch.object(UN, "nvenc_encoders", return_value=set()):
            self.assertEqual(UN.encoder_args("auto", 1920, 1088)[0], "libx264")
            with self.assertRaises(RuntimeError):
                UN.encoder_args("nvenc", 1920, 1088)
        # a frame past 4K gets x264's faster preset
        self.assertIn("fast", UN.x264_args(5376, 3072))
        self.assertIn("medium", UN.x264_args(3840, 2160))

    @needs_ffmpeg
    def test_forced_x264_writes_and_says_so(self):
        with tempfile.TemporaryDirectory() as root:
            out = os.path.join(root, "a.mp4")
            self.assertEqual(UN.encode_stream(clip(12), out, 24.0, "x264"), "libx264")
            n = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
                                "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", out],
                               capture_output=True, text=True).stdout.strip()
            self.assertEqual(n, "12")


class PixelNodeTest(unittest.TestCase):
    def test_batches_resized_to_the_target(self):
        frames = clip(10)                                          # 10 x 48 x 96
        mods, model = fake_upscaler_modules(), FakeUpscaleModel(4)
        with mock.patch.dict(sys.modules, mods):
            (out,) = UN.H3PixelUpscale().upscale(frames, model, 192, 96, chunk=4)
        self.assertEqual(tuple(out.shape), (10, 96, 192, 3))       # 4x model, 2x out
        self.assertEqual(model.calls, [4, 4, 2])
        # the model is loaded once for the clip, not once per batch
        self.assertEqual(mods["comfy.model_management"].loads, [1])
        # frame order and content kept: frame i's red is i/9
        for i in (0, 5, 9):
            self.assertAlmostEqual(float(out[i, :, :, 0].mean()), i / 9, delta=0.01)

    @needs_ffmpeg
    def test_a_mute_take_still_loads(self):
        with tempfile.TemporaryDirectory() as root:
            out = os.path.join(root, "t.mp4")
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                            "testsrc=size=96x48:rate=24:duration=1", "-frames:v", "24",
                            "-c:v", "libx264", "-pix_fmt", "yuv420p", out], check=True)
            images, audio = UN.H3LoadTakeVideo().load(root, "t.mp4", "")
        self.assertEqual(int(images.shape[0]), 24)
        self.assertEqual(float(audio["waveform"].abs().sum()), 0.0)  # silence


if __name__ == "__main__":
    unittest.main()
