"""H3SaveShot: thumbnails, and closing the take's sidecar per h3takes' contract.

Runs the node outside ComfyUI. Needs torch, numpy and PIL (skipped without
them); the tests that expect a real mp4 also need ffmpeg on PATH.
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
    import numpy as np
    import torch
    from PIL import Image
except ImportError as exc:                               # pragma: no cover
    raise unittest.SkipTest(f"H3SaveShot tests need torch, numpy and PIL: {exc}")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "comfy_nodes"))
import h3_shotlist as N  # noqa: E402
import h3render  # noqa: E402
import h3takes as T  # noqa: E402

HAVE_FFMPEG = shutil.which("ffmpeg") is not None
needs_ffmpeg = unittest.skipUnless(HAVE_FFMPEG, "ffmpeg not on PATH")

H, W = 48, 96                      # 2:1, so the scaled sizes are exact


def clip(n: int) -> torch.Tensor:
    """n frames whose red channel encodes the frame number (0 -> 0, n-1 -> 1)."""
    x = torch.zeros((n, H, W, 3), dtype=torch.float32)
    for i in range(n):
        x[i, :, :, 0] = i / max(1, n - 1)
        x[i, :, :, 1] = 0.5
        x[i, :, :, 2] = 1.0 - i / max(1, n - 1)
    return x


def red_of(n: int, i: int) -> float:
    return 255.0 * i / max(1, n - 1)


class SaveNodeTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def save(self, images, shot="sh020", take=1, subfolder="renders", **kw):
        return N.H3SaveShot().save(images, shot, "generate", self.root, subfolder,
                                   take, 24.0, False, audio=None, **kw)

    def reserve(self, shot="sh020", **fields):
        base = {"status": "queued", "queued": T.now(), "seed": 1743920155,
                "prompt": "Riley — at the window, «quoted»", "target": T.DEFAULT_TARGET,
                "comfy_prompt_id": "abc-123"}
        base.update(fields)
        return T.reserve_take(self.root, "final", shot, base)

    def assert_strip(self, path, n):
        idx = N.H3SaveShot.strip_indices(n)
        with Image.open(path) as im:
            self.assertEqual(im.size, (192 * len(idx), 96))
            px = np.asarray(im.convert("RGB"), dtype=np.float32)
        for j, i in enumerate(idx):
            cell = px[20:76, j * 192 + 20:(j + 1) * 192 - 20]
            self.assertAlmostEqual(float(cell[..., 0].mean()), red_of(n, i), delta=4,
                                   msg=f"cell {j} should be frame {i}")

    # -- the contract's pieces ---------------------------------------------

    def test_strip_indices(self):
        self.assertEqual(N.STRIP_FRAMES, T.STRIP_FRAMES)
        self.assertEqual(N.H3SaveShot.strip_indices(24), [1, 4, 7, 10, 13, 16, 19, 22])
        self.assertEqual(N.H3SaveShot.strip_indices(8), list(range(8)))
        self.assertEqual(N.H3SaveShot.strip_indices(3), [0, 1, 2])
        self.assertEqual(N.H3SaveShot.strip_indices(1), [0])
        self.assertEqual(N.H3SaveShot.strip_indices(107)[0], 6)
        self.assertEqual(N.H3SaveShot.strip_indices(107)[-1], 100)

    def test_input_is_last_optional_with_empty_default(self):
        opt = N.H3SaveShot.INPUT_TYPES()["optional"]
        self.assertEqual(list(opt)[-1], "sidecar")
        self.assertEqual(opt["sidecar"], ("STRING", {"default": ""}))

    def test_saved_workflow_still_converts(self):
        path = os.path.join(REPO, "targets", "video", "minimax_h3_ref2va", "workflow.json")
        with open(path, encoding="utf-8") as fh:
            api = h3render.ui_to_api(json.load(fh))
        savers = [v for v in api.values() if v["class_type"] == "H3SaveShot"]
        self.assertEqual(len(savers), 1)
        spec = N.H3SaveShot.INPUT_TYPES()
        inputs = savers[0]["inputs"]
        self.assertLessEqual(set(spec["required"]), set(inputs))
        self.assertLessEqual(set(inputs), set(spec["required"]) | set(spec["optional"]))
        self.assertNotIn("sidecar", inputs)
        self.assertIsInstance(inputs["take"], int)
        self.assertIsInstance(inputs["save_frames"], bool)

    # -- behaviour ---------------------------------------------------------

    @needs_ffmpeg
    def test_no_sidecar(self):
        shot_dir, status = self.save(clip(24))
        self.assertIn("mp4 written", status)
        tp = T.take_paths(self.root, "final", "sh020", 1)
        self.assertEqual(os.path.normcase(shot_dir), os.path.normcase(tp.dir))
        self.assertTrue(os.path.isfile(tp.mp4))
        self.assertFalse(os.path.exists(tp.sidecar))
        with Image.open(tp.thumb) as im:
            self.assertEqual(im.format, "JPEG")
            self.assertEqual(im.size, (480, 240))
            mid = np.asarray(im.convert("RGB"), dtype=np.float32)[..., 0].mean()
        self.assertAlmostEqual(float(mid), red_of(24, 12), delta=4)
        self.assert_strip(tp.strip, 24)
        self.assertEqual([f for f in os.listdir(tp.dir) if f.startswith(".tmp_")], [])

    @needs_ffmpeg
    def test_sidecar_finalised(self):
        t = self.reserve()
        self.assertEqual(t.status, "queued")
        before = T.read_json(t.paths.sidecar)
        _, status = self.save(clip(24), take=t.take, sidecar=t.paths.sidecar)

        sc = T.read_json(t.paths.sidecar)
        self.assertEqual(sc["status"], "ok")
        self.assertEqual(sc["frames"], 24)
        self.assertEqual(sc["mp4"], "sh020_t01.mp4")
        self.assertEqual(sc["thumb"], "sh020_t01.jpg")
        self.assertEqual(sc["strip"], "sh020_t01_strip.jpg")
        self.assertEqual(sc["save_notes"], status)
        self.assertRegex(sc["finished"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d$")
        self.assertEqual(len(sc["finished"]), len(T.now()))
        # every queuer field survives, unchanged
        saver = {"status", "finished", "frames", "fps", "mp4", "thumb", "strip", "save_notes"}
        for k, v in before.items():
            if k not in saver:
                self.assertEqual(sc[k], v, k)
        self.assertEqual(set(sc), set(before) | saver)
        # same bytes h3takes would write
        with open(t.paths.sidecar, "rb") as fh:
            raw = fh.read()
        ref = os.path.join(self.root, "ref.json")
        T.write_json(ref, sc)
        with open(ref, "rb") as fh:
            self.assertEqual(raw, fh.read())
        self.assertNotIn(b"\r\n", raw)
        self.assertIn("«quoted»".encode("utf-8"), raw)

        takes = T.list_takes(self.root, "final", "sh020")
        self.assertEqual([x.take for x in takes], [1])
        self.assertTrue(takes[0].usable)
        self.assertEqual(T.latest_usable(takes).take, 1)
        self.assertEqual(T.sweep_queued(takes, alive=set()), [])

    def test_mp4_failure(self):
        t = self.reserve()
        err = subprocess.CalledProcessError(1, ["ffmpeg"], stderr=b"boom: encoder exploded")
        with mock.patch.object(N.subprocess, "run", side_effect=err):
            _, status = self.save(clip(24), sidecar=t.paths.sidecar)
        self.assertIn("ffmpeg failed", status)
        sc = T.read_json(t.paths.sidecar)
        self.assertEqual(sc["status"], "failed")
        self.assertIsNone(sc["mp4"])
        self.assertEqual(sc["thumb"], "sh020_t01.jpg")
        self.assertEqual(sc["strip"], "sh020_t01_strip.jpg")
        self.assertIn("boom", sc["save_notes"])
        self.assertEqual(sc["seed"], 1743920155)
        self.assertTrue(os.path.isfile(t.paths.thumb))
        self.assert_strip(t.paths.strip, 24)

        takes = T.list_takes(self.root, "final", "sh020")
        self.assertEqual(takes[0].status, "failed")
        self.assertIsNone(T.latest_usable(takes))

    def test_missing_sidecar_is_created(self):
        with mock.patch.object(N.H3SaveShot, "_encode",
                               staticmethod(lambda *a: "ffmpeg not found - mp4 skipped")):
            path = T.take_paths(self.root, "final", "sh030", 2).sidecar
            _, status = self.save(clip(10), shot="sh030", take=2, sidecar=path)
        self.assertIn("missing", status)
        sc = T.read_json(path)
        self.assertEqual(set(sc), {"shot", "take", "status", "finished", "frames", "fps",
                                   "mp4", "thumb", "strip", "save_notes"})
        self.assertEqual((sc["shot"], sc["take"]), ("sh030", 2))
        self.assertEqual(sc["status"], "failed")
        self.assertEqual(sc["frames"], 10)
        self.assertIn("missing", sc["save_notes"])

    def test_unreadable_sidecar_is_rewritten(self):
        path = T.take_paths(self.root, "final", "sh030", 1).sidecar
        os.makedirs(os.path.dirname(path))
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        with mock.patch.object(N.H3SaveShot, "_encode",
                               staticmethod(lambda *a: "ffmpeg not found - mp4 skipped")):
            _, status = self.save(clip(4), shot="sh030", sidecar=path)
        self.assertIn("unreadable", status)
        sc = T.read_json(path)
        self.assertEqual((sc["shot"], sc["take"], sc["frames"]), ("sh030", 1, 4))

    def test_mismatched_sidecar_still_updated_with_warning(self):
        t = self.reserve(shot="sh010")
        with mock.patch.object(N.H3SaveShot, "_encode",
                               staticmethod(lambda *a: "ffmpeg not found - mp4 skipped")):
            _, status = self.save(clip(4), shot="sh020", take=5, sidecar=t.paths.sidecar)
        self.assertIn("warning", status)
        sc = T.read_json(t.paths.sidecar)
        self.assertEqual((sc["shot"], sc["take"]), ("sh010", 1))    # left alone
        self.assertEqual(sc["frames"], 4)
        self.assertIn("warning", sc["save_notes"])

    @needs_ffmpeg
    def test_relative_sidecar_path(self):
        t = self.reserve()
        rel = os.path.relpath(t.paths.sidecar, self.root)
        self.assertFalse(os.path.isabs(rel))
        self.save(clip(24), sidecar=rel)
        self.assertEqual(T.read_json(t.paths.sidecar)["status"], "ok")
        self.assertFalse(os.path.exists(os.path.join(os.getcwd(), rel)))

    @needs_ffmpeg
    def test_short_clip_uses_every_frame(self):
        t = self.reserve()
        self.save(clip(5), sidecar=t.paths.sidecar)
        sc = T.read_json(t.paths.sidecar)
        self.assertEqual((sc["status"], sc["frames"]), ("ok", 5))
        self.assert_strip(t.paths.strip, 5)            # 5 cells, frames 0..4 in order
        with Image.open(t.paths.thumb) as im:
            mid = np.asarray(im.convert("RGB"), dtype=np.float32)[..., 0].mean()
        self.assertAlmostEqual(float(mid), red_of(5, 2), delta=4)

    def test_thumbnail_failure_never_raises(self):
        t = self.reserve()
        boom = mock.patch.object(N.H3SaveShot, "_save_jpeg",
                                 staticmethod(mock.Mock(side_effect=OSError("disk full"))))
        with boom, mock.patch.object(N.H3SaveShot, "_encode",
                                     staticmethod(lambda *a: "mp4 written (mute)")):
            open(t.paths.mp4, "wb").close()            # stand-in for the render
            _, status = self.save(clip(6), sidecar=t.paths.sidecar)
        self.assertIn("thumbnail failed: disk full", status)
        self.assertIn("strip failed: disk full", status)
        sc = T.read_json(t.paths.sidecar)
        self.assertEqual(sc["status"], "ok")
        self.assertEqual(sc["mp4"], "sh020_t01.mp4")
        self.assertIsNone(sc["thumb"])
        self.assertIsNone(sc["strip"])

    def test_sidecar_write_failure_never_raises(self):
        t = self.reserve()
        with mock.patch.object(N, "_write_json_atomic", side_effect=PermissionError("locked")), \
                mock.patch.object(N.H3SaveShot, "_encode",
                                  staticmethod(lambda *a: "ffmpeg not found - mp4 skipped")):
            _, status = self.save(clip(3), sidecar=t.paths.sidecar)
        self.assertIn("sidecar update failed: locked", status)
        self.assertEqual(T.read_json(t.paths.sidecar)["status"], "queued")

    # -- the h3pipe.take event (docs/API.md) -------------------------------

    def fake_server(self, send_sync):
        server = type(sys)("server")
        server.PromptServer = type("PromptServer", (), {})
        server.PromptServer.instance = mock.Mock(send_sync=send_sync)
        return mock.patch.dict(sys.modules, {"server": server})

    def test_take_event_after_the_sidecar_closes(self):
        t = T.reserve_take(self.root, "proxy", "sh020", {"status": "queued"})
        sent = mock.Mock()
        with self.fake_server(sent), mock.patch.object(
                N.H3SaveShot, "_encode", staticmethod(lambda *a: "mp4 written (mute)")):
            open(t.paths.mp4, "wb").close()
            self.save(clip(6), subfolder="renders_proxy", sidecar=t.paths.sidecar)
        sent.assert_called_once_with("h3pipe.take", {
            "ep": os.path.abspath(self.root), "pass": "proxy", "shot": "sh020", "take": 1,
            "status": "ok", "thumb": "renders_proxy/sh020/sh020_t01.jpg"})

    def test_take_event_never_affects_saving(self):
        t = self.reserve()
        with self.fake_server(mock.Mock(side_effect=RuntimeError("socket gone"))), \
                mock.patch.object(N.H3SaveShot, "_encode",
                                  staticmethod(lambda *a: "ffmpeg not found - mp4 skipped")):
            _, status = self.save(clip(3), sidecar=t.paths.sidecar)
        self.assertEqual(T.read_json(t.paths.sidecar)["status"], "failed")
        self.assertNotIn("socket", status)
        # no sidecar, no event
        sent = mock.Mock()
        with self.fake_server(sent), mock.patch.object(
                N.H3SaveShot, "_encode", staticmethod(lambda *a: "mp4 written (mute)")):
            self.save(clip(3), take=2)
        sent.assert_not_called()


class SaveRefAudioTest(unittest.TestCase):
    """H3SaveRefAudio (docs/API.md "Phase 9c-B"): the wav a voice-ref take
    gets, and the sidecar it closes."""

    RATE = 48000

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.sidecar = os.path.join(self.root, "voice__ada_t01.json")

    def tearDown(self):
        self._tmp.cleanup()

    def audio(self, seconds=1.5, channels=1, batch=1):
        n = int(self.RATE * seconds)
        t = torch.arange(n, dtype=torch.float32) / self.RATE
        wave = torch.sin(2 * torch.pi * 220.0 * t) * 0.5
        return {"waveform": wave.expand(batch, channels, n).clone(),
                "sample_rate": self.RATE}

    def write_sidecar(self, **fields):
        data = {"version": 1, "ref": "voice:ada", "view": None, "take": 1,
                "ep": self.root, "status": "queued", "image": "voice__ada_t01.wav"}
        data.update(fields)
        with open(self.sidecar, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        return self.sidecar

    def save(self, audio=None, sidecar=None):
        return N.H3SaveRefAudio().save(audio if audio is not None else self.audio(),
                                       self.sidecar if sidecar is None else sidecar)

    def test_writes_a_wav_and_closes_the_sidecar(self):
        import wave
        self.write_sidecar()
        out = self.save()
        self.assertIn("ok", out["result"][0])
        wav = os.path.join(self.root, "voice__ada_t01.wav")
        self.assertTrue(os.path.isfile(wav))
        with wave.open(wav, "rb") as w:
            self.assertEqual((w.getnchannels(), w.getsampwidth(), w.getframerate()),
                             (1, 2, self.RATE))
            self.assertEqual(w.getnframes(), int(self.RATE * 1.5))
            frames = w.readframes(w.getnframes())
        self.assertGreater(max(abs(v) for v in
                               np.frombuffer(frames, dtype="<i2")[:2000]), 1000)
        data = json.loads(open(self.sidecar, encoding="utf-8").read())
        self.assertEqual((data["status"], data["image"]), ("ok", "voice__ada_t01.wav"))
        self.assertAlmostEqual(data["duration"], 1.5, places=3)
        self.assertEqual((data["sample_rate"], data["channels"]), (self.RATE, 1))
        self.assertIn("voice__ada_t01.wav 1.50s", data["save_notes"])
        # every other field is left alone
        self.assertEqual(data["ref"], "voice:ada")

    def test_stereo_and_a_batch(self):
        self.write_sidecar()
        self.save(self.audio(0.5, channels=2, batch=3))
        data = json.loads(open(self.sidecar, encoding="utf-8").read())
        self.assertEqual((data["status"], data["channels"]), ("ok", 2))
        self.assertIn("3 clips in the batch; kept the first", data["save_notes"])

    def test_extension_and_missing_sidecar(self):
        # the sidecar's `image` always becomes .wav
        self.write_sidecar(image="voice__ada_t01.png")
        self.save()
        self.assertTrue(os.path.isfile(os.path.join(self.root, "voice__ada_t01.wav")))
        self.assertEqual(json.loads(open(self.sidecar, encoding="utf-8").read())["image"],
                         "voice__ada_t01.wav")
        # no sidecar at all: nothing is written, nothing is raised
        out = N.H3SaveRefAudio().save(self.audio(), "")
        self.assertIn("no sidecar given", out["result"][0])
        # a sidecar that isn't there yet is created
        os.remove(self.sidecar)
        self.save()
        data = json.loads(open(self.sidecar, encoding="utf-8").read())
        self.assertEqual(data["status"], "ok")
        self.assertIn("sidecar was missing; created", data["save_notes"])

    def test_failure_is_recorded_not_raised(self):
        self.write_sidecar()
        with mock.patch.object(N, "save_audio", side_effect=RuntimeError("no disk")):
            out = self.save()
        self.assertIn("failed", out["result"][0])
        data = json.loads(open(self.sidecar, encoding="utf-8").read())
        self.assertEqual((data["status"], data["image"]), ("failed", None))
        self.assertIn("no disk", data["save_notes"])
        self.assertFalse([f for f in os.listdir(self.root) if f.endswith(".wav")])

    def test_ref_event(self):
        self.write_sidecar()
        sent = mock.Mock()
        server = type(sys)("server")
        server.PromptServer = type("PromptServer", (), {})
        server.PromptServer.instance = mock.Mock(send_sync=sent)
        with mock.patch.dict(sys.modules, {"server": server}):
            self.save()
        sent.assert_called_once_with("h3pipe.ref", {
            "ep": self.root, "ref": "voice:ada", "view": None, "take": 1, "status": "ok"})


if __name__ == "__main__":
    unittest.main()
