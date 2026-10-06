"""
Colour: every clip h3assemble re-encodes comes out BT.709, tagged so.

Takes saved before 2026-10-05 are BT.601 and untagged (ffmpeg's default RGB
conversion), which YouTube, browsers and HD players decode as BT.709: reds go
orange. Pure red is the test: BT.601 writes it as Y 81, BT.709 as Y 63
(limited range), with U 90 / 102 and V 240.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import h3assemble as A  # noqa: E402

HAVE_FF = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
W, H = 256, 144
BT709_RED = (63, 102, 240)
BT601_RED = (81, 90, 240)


def red_clip(path: str, tagged_709: bool) -> None:
    """24 frames of pure red with silence: BT.709 and tagged, or BT.601 untagged
    (ffmpeg's default conversion, as takes were written before the fix)."""
    vf = (["-vf", "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p," + A.BT709_TAGS]
          if tagged_709 else [])
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    f"color=c=red:s={W}x{H}:r=24:d=1", "-f", "lavfi", "-t", "1", "-i",
                    "anullsrc=r=44100:cl=mono", *vf, "-c:v", "libx264", "-crf", "0",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", path],
                   check=True, capture_output=True, timeout=120)


def yuv(path: str) -> tuple[int, int, int]:
    """Y, U, V of the first frame's top-left pixel."""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-frames:v", "1", "-f", "rawvideo",
                          "-pix_fmt", "yuv420p", "-"], capture_output=True, check=True).stdout
    return raw[0], raw[W * H], raw[W * H + (W // 2) * (H // 2)]


def tags(path: str) -> list[str]:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=color_range,color_space,color_primaries,color_transfer",
                          "-of", "default=nw=1:nk=1", path], capture_output=True, text=True).stdout
    return out.split()


def near(got, want, tol=2) -> bool:
    return all(abs(a - b) <= tol for a, b in zip(got, want))


@unittest.skipUnless(HAVE_FF, "needs ffmpeg and ffprobe")
class ConformTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.d = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def conform(self, tagged_709: bool) -> str:
        src, dst = os.path.join(self.d, "src.mp4"), os.path.join(self.d, "out.mp4")
        red_clip(src, tagged_709)
        A.conform(src, dst, src, 24, 24)
        return dst

    def test_the_red_clips_are_what_they_say(self):
        src = os.path.join(self.d, "a.mp4")
        red_clip(src, False)
        self.assertTrue(near(yuv(src), BT601_RED), yuv(src))
        self.assertEqual(A.color_matrix(src), "unknown")
        red_clip(src, True)
        self.assertTrue(near(yuv(src), BT709_RED), yuv(src))
        self.assertEqual(A.color_matrix(src), "bt709")

    def test_an_untagged_take_is_converted_to_bt709(self):
        out = self.conform(tagged_709=False)
        self.assertTrue(near(yuv(out), BT709_RED), yuv(out))
        self.assertEqual(tags(out), ["tv", "bt709", "bt709", "bt709"])

    def test_a_bt709_take_is_only_retagged(self):
        out = self.conform(tagged_709=True)
        self.assertTrue(near(yuv(out), BT709_RED), yuv(out))    # not converted twice
        self.assertEqual(tags(out), ["tv", "bt709", "bt709", "bt709"])


if __name__ == "__main__":
    unittest.main()
