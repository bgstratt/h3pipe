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
        # a video mask the latent has is kept (LTX's guide frames held at 0)
        guide = torch.ones(1, 1, 3, 6, 12)
        guide[:, :, -1] = 0
        with mock.patch.dict(sys.modules, fake_comfy_nested()):
            (out,) = UN.H3HoldAudio().hold({"samples": FakeNested((video, audio)),
                                            "noise_mask": FakeNested((guide, torch.ones_like(audio)))})
        vm, am = out["noise_mask"].tensors
        self.assertTrue(torch.equal(vm, guide))
        self.assertTrue(torch.equal(am, torch.zeros_like(audio)))

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
    def test_load_video_from_a_path(self):
        src = self.take_mp4()
        quoted = f'"{src}"'                                     # Explorer's "Copy as path"
        self.assertIs(UN.H3LoadVideo.VALIDATE_INPUTS("", quoted), True)
        self.assertIn("no such video", UN.H3LoadVideo.VALIDATE_INPUTS("", src + ".nope"))
        images, audio, fps, frames, source, out, latent, has_latent = UN.H3LoadVideo().load("", quoted)
        self.assertEqual(tuple(images.shape), (24, 48, 96, 3))
        self.assertEqual((fps, frames, source), (24.0, 24, src))
        self.assertEqual(out, self.p("sh010_t01.up.mp4"))         # beside the source
        self.assertEqual((latent, has_latent), (None, False))     # no latent beside it
        self.assertGreater(audio["waveform"].shape[-1], 0)

    @needs_ffmpeg
    def test_load_video_of_a_mute_clip_is_silence_of_its_length(self):
        src = self.take_mp4(audio=False)
        _, audio, fps, frames, _, _, _, _ = UN.H3LoadVideo().load("", src)
        self.assertEqual(audio["waveform"].shape[-1], int(44100 * frames / fps))
        self.assertEqual(float(audio["waveform"].abs().max()), 0.0)

    def fake_folder_paths(self):
        out = os.path.join(self.root, "output")
        fp = mock.MagicMock()
        fp.get_output_directory.return_value = out

        def save_path(prefix, base, w=0, h=0):
            sub, name = os.path.split(prefix)
            folder = os.path.join(base, sub)
            os.makedirs(folder, exist_ok=True)
            taken = [int(f[len(name) + 1:].split("_")[0]) for f in os.listdir(folder)
                     if f.startswith(name + "_") and f[len(name) + 1:].split("_")[0].isdigit()]
            return folder, name, max(taken, default=0) + 1, sub, prefix
        fp.get_save_image_path.side_effect = save_path
        return fp

    @needs_ffmpeg
    def test_save_render_writes_the_mp4_and_its_latent_under_one_name(self):
        """H3SaveRender's mp4 and latent share a counter, and H3LoadVideo finds
        the latent beside the mp4 and hands back the joint AV latent."""
        images = torch.rand(24, 48, 96, 3)
        audio = {"waveform": torch.zeros(1, 1, 44100), "sample_rate": 44100}
        video, aud = torch.randn(1, 4, 3, 6, 12), torch.randn(1, 8, 20)
        mods = {**fake_comfy_nested(), "folder_paths": self.fake_folder_paths()}
        with mock.patch.dict(sys.modules, mods):
            r1 = UN.H3SaveRender().save(images, "video/H3", 24.0, "review", "x264", audio,
                                        {"samples": FakeNested((video, aud))})
            r2 = UN.H3SaveRender().save(images, "video/H3", 24.0, "review", "x264")
            mp4 = r1["result"][0]
            self.assertTrue(mp4.endswith(os.path.join("video", "H3_00001_.mp4")))
            self.assertTrue(os.path.isfile(mp4[:-4] + ".latent.safetensors"))
            self.assertTrue(r2["result"][0].endswith("H3_00002_.mp4"))     # the next counter
            self.assertEqual(r1["ui"]["images"][0]["filename"], "H3_00001_.mp4")
            out = UN.H3LoadVideo().load("", mp4)
            self.assertTrue(out[7])                                         # has_latent
            self.assertTrue(torch.equal(out[6]["samples"].tensors[0], video))
            self.assertTrue(torch.equal(out[6]["samples"].tensors[1], aud))
            self.assertFalse(UN.H3LoadVideo().load("", mp4, use_latent=False)[7])
            self.assertFalse(UN.H3LoadVideo().load("", r2["result"][0])[7])   # none beside it
        self.assertEqual(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a",
                                         "-show_entries", "stream=codec_name", "-of", "csv=p=0",
                                         mp4], capture_output=True, text=True).stdout.strip(), "aac")
        self.assertIn("no such latent", UN.H3LoadVideo.VALIDATE_INPUTS("", mp4, True, mp4 + ".nope"))

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
    def test_save_upscale_delivers_an_exact_size(self):
        self.take_mp4()
        size = lambda p: subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
             "stream=width,height", "-of", "csv=p=0", p], capture_output=True, text=True).stdout.strip()
        for (w, h, fit) in ((80, 48, "crop"), (96, 64, "pad"), (192, 108, "crop")):
            with open(self.p("sh010_t01.up.json"), "w", encoding="utf-8") as fh:
                json.dump({"shot": "sh010", "take": 1, "status": "queued"}, fh)
            UN.H3SaveUpscale().save(clip(24), self.root, "renders/sh010/sh010_t01.mp4",
                                    "renders/sh010/sh010_t01.up.mp4", 24.0,
                                    "renders/sh010/sh010_t01.up.json", "x264", w, h, fit)
            self.assertEqual(size(self.p("sh010_t01.up.mp4")), f"{w},{h}")
            rec = json.load(open(self.p("sh010_t01.up.json"), encoding="utf-8"))
            self.assertEqual((rec["status"], rec["width"], rec["height"]), ("ok", w, h))


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
    def test_master_quality(self):
        both = {"h264_nvenc", "hevc_nvenc"}
        with mock.patch.object(UN, "nvenc_encoders", return_value=both):
            name, args = UN.encoder_args("auto", 3840, 2160, "master")   # x264 for a master
            self.assertEqual(name, "libx264")
            self.assertEqual(args[args.index("-crf") + 1], "12")
            name, args = UN.encoder_args("nvenc", 3840, 2160, "master")  # forced: NVENC's best
            self.assertEqual((name, args[args.index("-cq") + 1], args[args.index("-preset") + 1]),
                             ("h264_nvenc", "14", "p7"))
            self.assertEqual(UN.encoder_args("auto", 3840, 2160)[0], "h264_nvenc")   # review: as before
        self.assertIn("slow", UN.x264_args(3840, 2160, "master"))

    def test_encoder_choice(self):
        both = {"h264_nvenc", "hevc_nvenc"}
        with mock.patch.object(UN, "nvenc_encoders", return_value=both):
            self.assertEqual(UN.encoder_args("auto", 3840, 2176)[0], "h264_nvenc")
            # past NVENC H.264's 4096: auto stays H.264 (x264), which a browser plays
            self.assertEqual(UN.encoder_args("auto", 5376, 3072)[0], "libx264")
            name, args = UN.encoder_args("nvenc", 5376, 3072)           # forced: HEVC
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

    def test_fit_filter(self):
        self.assertEqual(UN.fit_size(1344, 768, 3840, 2160, "crop"), (3840, 2196))
        self.assertEqual(UN.fit_size(1344, 768, 3840, 2160, "pad"), (3780, 2160))
        self.assertIsNone(UN.fit_filter(3840, 2160, 3840, 2160))
        self.assertIsNone(UN.fit_filter(3840, 2160, 0, 0))
        self.assertEqual(UN.fit_filter(3840, 2196, 3840, 2160, "crop"), "crop=3840:2160")
        self.assertEqual(UN.fit_filter(2688, 1536, 3840, 2160, "pad"),
                         "scale=3780:2160:flags=lanczos,pad=3840:2160:(ow-iw)/2:(oh-ih)/2:black")


class FinishTest(unittest.TestCase):
    """The pixel upscale's finish: frequency split, keep soft, box shrink, grain."""

    def src(self, h=24, w=32, seed=0):
        g = torch.Generator().manual_seed(seed)
        return torch.rand((2, 3, h, w), generator=g)

    def test_blur_keeps_flat_and_shape(self):
        x = torch.full((1, 3, 20, 30), 0.4)
        y = UN.gaussian_blur(x, 3.0)
        self.assertEqual(tuple(y.shape), (1, 3, 20, 30))
        self.assertTrue(torch.allclose(y, x, atol=1e-5))
        self.assertTrue(torch.equal(UN.gaussian_blur(x, 0), x))

    def test_frequency_split_takes_colour_from_the_source(self):
        import torch.nn.functional as F
        src = self.src()
        base = F.interpolate(src, size=(48, 64), mode="bicubic", align_corners=False)
        detail = UN.gaussian_blur(base, 3) - base          # some high-band "model detail"
        tinted = (base - detail * 0.5 + 0.15).clamp(0, 1)  # the model shifted the colour
        out = UN.finish(src, tinted, frequency_split=True)
        low = lambda x: UN.gaussian_blur(x, UN.BAND_SIGMA * 2)
        # colour and tone: the source's; the tint is gone
        self.assertLess(float((low(out) - low(base)).abs().mean()), 0.03)
        self.assertGreater(float((low(tinted) - low(base)).abs().mean()), 0.1)
        # detail: the model's
        hi = lambda x: x - low(x)
        self.assertLess(float((hi(out) - hi(tinted)).abs().mean()), 0.03)
        self.assertTrue(torch.equal(UN.finish(src, tinted, frequency_split=False), tinted))

    def test_keep_soft_fades_detail_only_where_the_source_is_soft(self):
        src = torch.full((1, 3, 24, 32), 0.5)
        src[:, :, :, 16:] = self.src()[:1, :, :, 16:]     # right half detailed, left flat
        up = torch.rand((1, 3, 48, 64), generator=torch.Generator().manual_seed(3))
        out = UN.finish(src, up, frequency_split=False, keep_soft=1.0,
                        level=float(UN.detail_map(src).max()))
        hi = lambda x: (x - UN.gaussian_blur(x, 3)).abs().mean()
        self.assertLess(float(hi(out[..., :, 4:24])), float(hi(up[..., :, 4:24])) * 0.25)
        self.assertGreater(float(hi(out[..., :, 44:60])), float(hi(up[..., :, 44:60])) * 0.5)

    def test_resize_to_box_averages_whole_factors(self):
        x = torch.rand((1, 3, 8, 12))
        called = []
        lan = lambda t, w, h: called.append((w, h)) or torch.zeros((1, 3, h, w))
        y = UN.resize_to(x, 4, 6, lan)
        self.assertEqual(called, [])
        self.assertTrue(torch.allclose(y[0, :, 0, 0], x[0, :, :2, :2].mean(dim=(1, 2))))
        UN.resize_to(x, 6, 9, lan)                                  # 1.5x down: lanczos
        self.assertEqual(called, [(9, 6)])
        self.assertIs(UN.resize_to(x, 8, 12, lan), x)

    def test_grain(self):
        x = torch.full((3, 3, 16, 16), 0.5)
        a, b = UN.add_grain(x, 0.05, 7, 0), UN.add_grain(x, 0.05, 7, 0)
        self.assertTrue(torch.equal(a, b))                           # a redo repeats it
        self.assertFalse(torch.equal(a[0], a[1]))                    # but frame to frame it moves
        self.assertTrue(torch.equal(a[0, 0], a[0, 1]))               # monochrome
        self.assertFalse(torch.equal(UN.add_grain(x, 0.05, 8, 0), a))
        self.assertIs(UN.add_grain(x, 0, 7, 0), x)
        self.assertGreaterEqual(UN.detail_scale(self.src().movedim(1, -1), "cpu"), 0.02)


class FinishNodeTest(unittest.TestCase):
    def test_finish_node_on_someone_elses_upscale(self):
        import torch.nn.functional as F
        src = torch.rand((5, 24, 32, 3), generator=torch.Generator().manual_seed(1))
        big = F.interpolate(src.movedim(-1, 1), size=(48, 64), mode="bicubic", align_corners=False)
        tinted = (big + 0.12).clamp(0, 1).movedim(1, -1)              # a colour shift to take out
        (out,) = UN.H3FinishUpscale().finish(tinted, src, chunk=2)
        self.assertEqual(tuple(out.shape), (5, 48, 64, 3))
        low = lambda x: F.avg_pool2d(x.float().movedim(-1, 1), 8)
        self.assertLess(float((low(out) - low(big.movedim(1, -1))).abs().mean()),
                        float((low(tinted) - low(big.movedim(1, -1))).abs().mean()) * 0.5)
        (same,) = UN.H3FinishUpscale().finish(tinted, src, frequency_split=False)
        self.assertIs(same, tinted)                                  # nothing asked: untouched


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
