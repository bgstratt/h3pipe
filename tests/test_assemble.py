"""End-to-end tests for h3assemble following cut.json.

Each test builds a tiny fake episode in a temp dir (a minimal shotlist and a few
real mp4 takes made with ffmpeg's testsrc), runs h3assemble through its CLI and
checks the output's frame count and its _shots.txt. Skipped when ffmpeg or
ffprobe is not on PATH.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import h3takes as T  # noqa: E402

ASSEMBLE = os.path.join(ROOT, "h3assemble.py")
HAVE_FF = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def ff(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(list(args), capture_output=True, text=True, timeout=120)


def make_clip(path: str, frames: int, size: str) -> None:
    """A testsrc clip of exactly `frames` frames at 24 fps, with a sine track."""
    r = ff("ffmpeg", "-y", "-v", "error",
           "-f", "lavfi", "-i", f"testsrc=size={size}:rate=24",
           "-f", "lavfi", "-t", f"{frames / 24:.6f}", "-i", "sine=frequency=440:sample_rate=44100",
           "-frames:v", str(frames), "-c:v", "libx264", "-preset", "ultrafast",
           "-pix_fmt", "yuv420p", "-c:a", "aac", path)
    if r.returncode != 0:
        raise RuntimeError(r.stderr)


def probe_frames(path: str) -> int:
    r = ff("ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
           "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", path)
    return int(r.stdout.strip())


def probe_size(path: str) -> str:
    r = ff("ffprobe", "-v", "error", "-select_streams", "v:0",
           "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", path)
    return r.stdout.strip()


@unittest.skipUnless(HAVE_FF, "ffmpeg/ffprobe not on PATH")
class AssembleTest(unittest.TestCase):
    clips: dict = {}

    @classmethod
    def setUpClass(cls):
        cls._cache = tempfile.TemporaryDirectory()
        for size in ("64x64", "32x32"):
            for n in (22, 39, 56):
                p = os.path.join(cls._cache.name, f"{size}_{n}.mp4")
                make_clip(p, n, size)
                cls.clips[(size, n)] = p

    @classmethod
    def tearDownClass(cls):
        cls._cache.cleanup()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    # ---- fixture helpers -------------------------------------------------

    def shotlist(self, shots: list[tuple[str, int]], name: str = "shotlist.json",
                 size: tuple[int, int] = (64, 64)) -> None:
        doc = {"episode": "ep01",
               "defaults": {"width": size[0], "height": size[1]},
               "shots": [{"id": sid, "length": n, "audio_policy": "keep"}
                         for sid, n in shots]}
        T.write_json(os.path.join(self.root, "shotlist", name), doc)

    def take(self, shot: str, frames: int | None, status: str | None = "ok",
             pass_: str = "final", size: str = "64x64") -> int:
        """Add a take. status None: a legacy take (mp4, no sidecar).
        frames None: no mp4 (a queued or failed job)."""
        if status is None:
            n = (T.take_numbers(self.root, pass_, shot) or [0])[-1] + 1
            tp = T.take_paths(self.root, pass_, shot, n)
            os.makedirs(tp.dir, exist_ok=True)
            shutil.copy(self.clips[(size, frames)], tp.mp4)
            return n
        t = T.reserve_take(self.root, pass_, shot, {"status": "queued"})
        if frames is not None:
            shutil.copy(self.clips[(size, frames)], t.paths.mp4)
        if status != "queued":
            T.update_sidecar(t.paths.sidecar, status=status, frames=frames)
        return t.take

    def cut(self, final: list[dict], proxy: list[dict] | None = None) -> None:
        T.save_cut(self.root, {"episode": "ep01", "final": final, "proxy": proxy or []})

    def assemble(self, *args: str, ok: bool = True) -> subprocess.CompletedProcess:
        r = subprocess.run([sys.executable, ASSEMBLE, "-o", self.root, *args],
                           capture_output=True, text=True, encoding="utf-8", timeout=120)
        if ok:
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r

    def out(self, sub: str = "renders", name: str = "ep01") -> str:
        return os.path.join(self.root, sub, name + ".mp4")

    def shots_txt(self, sub: str = "renders", name: str = "ep01") -> list[list[str]]:
        with open(os.path.join(self.root, sub, name + "_shots.txt"), encoding="utf-8") as fh:
            return [ln.split() for ln in fh if ln.strip() and not ln.startswith("#")]

    def order(self, sub: str = "renders", name: str = "ep01") -> list[tuple[str, int]]:
        return [(row[1], int(row[2])) for row in self.shots_txt(sub, name)]

    # ---- tests -----------------------------------------------------------

    def test_no_cut_uses_latest_usable(self):
        self.shotlist([("sh010", 22), ("sh020", 39)])
        self.take("sh010", 22, status=None)          # t01 legacy: counts as ok
        self.take("sh010", 22)                       # t02 ok
        self.take("sh010", None, status="queued")    # t03 still queued
        self.take("sh020", 39, status=None)          # t01 legacy
        self.take("sh020", 39, status="failed")      # t02 failed, mp4 left behind
        self.assemble()
        self.assertEqual(self.order(), [("sh010", 2), ("sh020", 1)])
        self.assertEqual(probe_frames(self.out()), 61)

    def test_explicit_take_pick(self):
        self.shotlist([("sh010", 22), ("sh020", 39)])
        self.take("sh010", 22)
        self.take("sh010", 22)
        self.take("sh020", 39)
        self.cut([{"shot": "sh010", "take": 1}])
        self.assemble()
        self.assertEqual(self.order(), [("sh010", 1), ("sh020", 1)])
        self.assertEqual(probe_frames(self.out()), 61)

    def test_picked_take_not_usable_is_missing(self):
        self.shotlist([("sh010", 22), ("sh020", 39)])
        self.take("sh010", 22)
        self.take("sh010", None, status="queued")
        self.take("sh020", 39)
        self.take("sh020", None, status="failed")
        self.cut([{"shot": "sh010", "take": 2}, {"shot": "sh020", "take": 2}])
        r = self.assemble(ok=False)
        self.assertEqual(r.returncode, 1)
        self.assertIn("t02 is still queued", r.stdout)
        self.assertIn("t02 failed", r.stdout)
        self.assertFalse(os.path.exists(self.out()))

    def test_reordered_cut(self):
        self.shotlist([("sh010", 22), ("sh020", 39), ("sh030", 56)])
        for sid, n in (("sh010", 22), ("sh020", 39), ("sh030", 56)):
            self.take(sid, n)
        self.cut([{"shot": "sh030"}, {"shot": "sh010"}, {"shot": "sh020"}])
        self.assemble()
        self.assertEqual([s for s, _ in self.order()], ["sh030", "sh010", "sh020"])
        rows = self.shots_txt()
        self.assertEqual(rows[1][0], "00:00:02.333")     # sh010 starts after 56 frames
        self.assertEqual(probe_frames(self.out()), 117)

    def test_orphan_skipped(self):
        self.shotlist([("sh010", 22), ("sh020", 39)])
        self.take("sh010", 22)
        self.take("sh020", 39)
        self.take("sh015", 22)                       # rendered, then cut from the script
        self.cut([{"shot": "sh010"}, {"shot": "sh015"}, {"shot": "sh020"}])
        r = self.assemble()
        self.assertIn("orphaned", r.stdout)
        self.assertIn("sh015", r.stdout)
        self.assertEqual([s for s, _ in self.order()], ["sh010", "sh020"])
        self.assertEqual(probe_frames(self.out()), 61)
        r = self.assemble("--check")
        self.assertRegex(r.stdout, r"sh015 .*orphan")

    def test_new_script_shot_inserted(self):
        self.shotlist([("sh010", 22), ("sh020", 39), ("sh030", 56)])
        for sid, n in (("sh010", 22), ("sh020", 39), ("sh030", 56)):
            self.take(sid, n)
        self.cut([{"shot": "sh030"}, {"shot": "sh010"}])   # sh020 is new
        self.assemble()
        self.assertEqual([s for s, _ in self.order()], ["sh030", "sh010", "sh020"])
        r = self.assemble("--check")
        self.assertRegex(r.stdout, r"sh020 .*not in cut\.json")

    def test_placeholder_from_proxy(self):
        self.shotlist([("sh010", 22), ("sh020", 39)])
        self.take("sh010", 22)
        self.take("sh020", 39, pass_="proxy", size="32x32")
        self.cut([{"shot": "sh010"}, {"shot": "sh020", "pass": "proxy", "take": 1}])
        r = self.assemble("--check")
        self.assertRegex(r.stdout, r"sh020 .*placeholder\(proxy\)")
        self.assemble()
        self.assertEqual(probe_size(self.out()), "64x64")
        self.assertEqual(probe_frames(self.out()), 61)
        rows = self.shots_txt()
        self.assertEqual(rows[1][1], "sh020")
        self.assertIn("placeholder(proxy)", rows[1])

    def test_trims_exact_frames(self):
        self.shotlist([("sh010", 56), ("sh020", 22)])
        self.take("sh010", 56)
        self.take("sh020", 22)
        self.cut([{"shot": "sh010", "trim_in": 5, "trim_out": 7},
                  {"shot": "sh020", "trim_in": 3}])
        self.assemble()
        rows = self.shots_txt()
        self.assertEqual([int(r[3]) for r in rows], [44, 19])
        self.assertEqual(probe_frames(self.out()), 63)

    def test_trim_after_window_warns_with_master(self):
        doc = {"episode": "ep01", "defaults": {"width": 64, "height": 64},
               "shots": [{"id": "sh010", "length": 56, "audio_policy": "dub",
                          "audio_in": 10.0, "audio_out": 11.5}]}
        T.write_json(os.path.join(self.root, "shotlist", "shotlist.json"), doc)
        self.take("sh010", 56)
        self.cut([{"shot": "sh010", "trim_in": 4}])
        r = self.assemble("--check", "--audio", "master")
        self.assertIn("master track will drift", r.stdout)
        self.assertRegex(r.stdout, r"sh010 .* 32/56 ")     # 36-frame window, minus 4

    def test_take_flag_beats_cut(self):
        self.shotlist([("sh010", 22), ("sh020", 39)])
        for sid, n in (("sh010", 22), ("sh020", 39)):
            self.take(sid, n)
            self.take(sid, n)
            self.take(sid, n)
        self.cut([{"shot": "sh010", "take": 1}, {"shot": "sh020", "take": 3}])
        self.assemble("--take", "2")
        self.assertEqual(self.order(), [("sh010", 2), ("sh020", 2)])

    def test_pass_from_shotlist_name(self):
        self.shotlist([("sh010", 22)], name="shotlist_proxy.json", size=(32, 32))
        self.take("sh010", 22, pass_="proxy", size="32x32")
        self.cut([], proxy=[{"shot": "sh010", "take": 1}])
        self.assemble("--shotlist", os.path.join("shotlist", "shotlist_proxy.json"))
        self.assertEqual(self.order("renders_proxy", "ep01_proxy"), [("sh010", 1)])
        self.assertEqual(probe_frames(self.out("renders_proxy", "ep01_proxy")), 22)


if __name__ == "__main__":
    unittest.main()
