"""
Continuity keyframes (h3refs.keyframe_from_take, `h3.py keyframe`): a shot's
first keyframe cut out of the previous shot's take, in cut order, from the take
the cut uses; the frame is exact; auto-pick; and the LTX takes that used a
keyframe going `ref`-stale when it is re-extracted. On the mixed H3/LTX fixture
(test_ltx.build_mixed), with tiny mp4s made by ffmpeg's `testsrc`.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
import h3edit as E  # noqa: E402
import h3jobs as J  # noqa: E402
import h3refs as R  # noqa: E402
import h3takes as T  # noqa: E402
from test_ltx import build_mixed  # noqa: E402
from test_render import ENV  # noqa: E402

HAVE_FF = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
W, H = 64, 48


def make_mp4(path: str, frames: int = 17, source: str = "testsrc") -> None:
    """A tiny H.264 clip whose frames all differ (testsrc draws a counter)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                    "-i", f"{source}=size={W}x{H}:rate=24", "-frames:v", str(frames),
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", path],
                   check=True, capture_output=True)


def make_take(root: str, pass_: str, shot: str, frames: int = 17,
              source: str = "testsrc", status: str = "ok") -> T.Take:
    """A finished video take with a real mp4."""
    t = T.reserve_take(root, pass_, shot, {"status": status, "target": None})
    if status == "ok":
        make_mp4(t.paths.mp4, frames, source)
    return t


def rgb_frames(path: str) -> list[bytes]:
    """Every frame of a video (or the one of an image) as raw RGB24."""
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-f", "rawvideo",
                        "-pix_fmt", "rgb24", "-"], check=True, capture_output=True)
    n = W * H * 3
    return [r.stdout[i:i + n] for i in range(0, len(r.stdout), n)]


@unittest.skipUnless(HAVE_FF, "needs ffmpeg and ffprobe on PATH")
class KeyframeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.src = os.path.join(cls._tmp.name, "mx01")
        os.makedirs(cls.src)
        build_mixed(cls.src)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.root = os.path.join(self._t.name, "mx01")
        shutil.copytree(self.src, self.root)
        self.s = R.load_series(self.root)

    def tearDown(self):
        self._t.cleanup()

    def kf(self, shot="sh020", **kw):
        return R.keyframe_from_take(self.s, shot, **kw)

    def pixels(self, png: str) -> bytes:
        (px,) = rgb_frames(png)
        return px

    # -- extraction --------------------------------------------------------

    def test_last_frame_is_exact(self):
        # the order of shots: sh010 (H3), sh020 (LTX), sh030, sh040, sh050, sh060
        self.assertFalse(os.path.exists(os.path.join(self.root, "refs", "shots")))
        src = make_take(self.root, "proxy", "sh010")
        res = self.kf()
        self.assertEqual((res.ref.id, res.take.take, res.picked), ("shot:sh020:first", 1, True))
        frames = rgb_frames(src.paths.mp4)
        self.assertEqual(len(frames), 17)
        got = self.pixels(res.take.paths.image)
        self.assertEqual(got, frames[-1])                 # the very last frame, pixel for pixel
        self.assertNotEqual(got, frames[-2])
        sc = res.take.sidecar
        self.assertEqual((sc["source"], sc["source_shot"], sc["source_take"], sc["source_pass"],
                          sc["source_frame"], sc["source_frames"], sc["status"]),
                         ("frame", "sh010", 1, "proxy", 16, 17, "ok"))
        self.assertEqual((sc["width"], sc["height"]), (W, H))
        self.assertEqual(sc["source_mp4"], "renders_proxy/sh010/sh010_t01.mp4")
        # picked: the live keyframe is that image
        live = os.path.join(self.root, "refs", "shots", "sh020", "first.png")
        self.assertEqual(T.file_sha1(live), T.file_sha1(res.take.paths.image))
        self.assertEqual(R.load_picks(self.root)["refs"]["shot:sh020:first"]["take"], 1)
        # and it lists, with where it came from
        listed = {r["id"]: r for r in R.list_refs(self.root)}["shot:sh020:first"]
        self.assertEqual((listed["exists"], listed["picked"]), (True, 1))
        self.assertEqual(listed["takes"][0]["source"], "frame")
        self.assertEqual(listed["takes"][0]["from"], {"shot": "sh010", "take": 1,
                                                      "pass": "proxy", "frame": 16,
                                                      "frames": 17})

    def test_given_frames_and_the_last_keyframe(self):
        src = make_take(self.root, "proxy", "sh010")
        frames = rgb_frames(src.paths.mp4)
        for spec, idx in ((3, 3), (-2, 15), ("first", 0), ("last", 16)):
            with self.subTest(frame=spec):
                res = self.kf(frame=spec)
                self.assertEqual(res.source["frame"], idx)
                self.assertEqual(self.pixels(res.take.paths.image), frames[idx])
        # last keyframe: the NEXT shot's first frame by default
        nxt = make_take(self.root, "proxy", "sh030", source="testsrc2")
        res = self.kf("sh020", which="last")
        self.assertEqual((res.ref.id, res.source["shot"], res.source["frame"]),
                         ("shot:sh020:last", "sh030", 0))
        self.assertEqual(self.pixels(res.take.paths.image), rgb_frames(nxt.paths.mp4)[0])
        # an explicit source shot and take
        res = self.kf("sh050", source_shot="sh010", source_take=1, frame=0)
        self.assertEqual((res.source["shot"], res.source["take"], res.source["frame"]),
                         ("sh010", 1, 0))

    # -- which take: cut order and the cut's take --------------------------

    def test_source_follows_the_cut(self):
        make_take(self.root, "proxy", "sh010")
        a = make_take(self.root, "proxy", "sh030")
        b = make_take(self.root, "proxy", "sh030", source="testsrc2")
        # script order: sh040's previous shot is sh030, latest usable take (t02)
        res = self.kf("sh040")
        self.assertEqual((res.source["shot"], res.source["take"]), ("sh030", 2))
        self.assertEqual(self.pixels(res.take.paths.image), rgb_frames(b.paths.mp4)[-1])
        # the cut picks t01: that is the source now
        E.pick_take(self.root, "proxy", "sh030", 1)
        res = self.kf("sh040")
        self.assertEqual((res.source["shot"], res.source["take"]), ("sh030", 1))
        self.assertEqual(self.pixels(res.take.paths.image), rgb_frames(a.paths.mp4)[-1])
        # reordered: sh030 before sh020, so sh020's previous shot is sh030,
        # and sh030's is sh010 (not sh020, its script predecessor)
        E.replace_cut(self.root, "proxy", [{"shot": s} for s in
                                           ("sh010", "sh030", "sh020", "sh040", "sh050",
                                            "sh060")])
        self.assertEqual(self.kf("sh020").source["shot"], "sh030")
        self.assertEqual(self.kf("sh030").source["shot"], "sh010")
        self.assertEqual(E.cut_neighbour(self.root, "proxy", "sh020", 1).shot, "sh040")
        # a placeholder: the proxy cut uses sh030's FINAL t01
        fin = make_take(self.root, "final", "sh030", source="testsrc2")
        E.pick_take(self.root, "proxy", "sh030", 1, from_pass="final")
        res = self.kf("sh020")
        self.assertEqual((res.source["shot"], res.source["pass"], res.source["take"]),
                         ("sh030", "final", 1))
        self.assertEqual(res.take.sidecar["source_pass"], "final")
        self.assertEqual(self.pixels(res.take.paths.image), rgb_frames(fin.paths.mp4)[-1])

    def test_the_final_pass(self):
        make_take(self.root, "final", "sh010")
        res = self.kf(pass_="final")
        self.assertEqual((res.source["pass"], res.source["take"]), ("final", 1))
        with self.assertRaises(R.NotUsable):              # no proxy take of sh010
            self.kf(pass_="proxy")

    # -- errors -------------------------------------------------------------

    def test_errors(self):
        with self.assertRaisesRegex(R.RefError, "first shot of the proxy cut: there is no "
                                                "previous shot"):
            self.kf("sh010")
        with self.assertRaisesRegex(R.RefError, "no next shot"):
            self.kf("sh060", which="last")
        with self.assertRaisesRegex(R.NotUsable, "sh010 has no usable proxy take"):
            self.kf()
        failed = make_take(self.root, "proxy", "sh010", status="failed")
        with self.assertRaises(R.NotUsable):
            self.kf()
        E.pick_take(self.root, "proxy", "sh010", failed.take, force=True)
        make_take(self.root, "proxy", "sh010")             # t02 is fine, but the cut picks t01
        with self.assertRaisesRegex(R.NotUsable, "cut uses sh010 proxy t01, which is failed"):
            self.kf()
        with self.assertRaisesRegex(R.NotUsable, "t01 is failed"):
            self.kf(source_shot="sh010", source_take=1)
        with self.assertRaises(R.UnknownRef):
            self.kf(source_shot="sh010", source_take=9)
        with self.assertRaises(R.UnknownRef):
            self.kf("sh999")
        with self.assertRaises(R.UnknownRef):
            self.kf(source_shot="sh999")
        with self.assertRaises(R.RefError):
            self.kf(which="middle")
        with self.assertRaisesRegex(R.RefError, "outside the take"):
            self.kf(source_shot="sh010", source_take=2, frame=17)
        with self.assertRaises(R.RefError):
            self.kf(source_shot="sh010", source_take=2, frame=1.5)
        with mock.patch.object(R.shutil, "which", return_value=None):
            with self.assertRaisesRegex(R.FfmpegMissing, "needs ffprobe on PATH"):
                self.kf(source_shot="sh010", source_take=2)
        # nothing was left behind by the failures
        self.assertEqual(R.list_takes(R.find_ref(self.s, "shot:sh020:first")), [])
        self.assertFalse(os.path.isfile(os.path.join(self.root, "refs", "shots", "sh020",
                                                     "first.png")))

    # -- picking -------------------------------------------------------------

    def test_auto_pick_only_without_a_live_file(self):
        make_take(self.root, "proxy", "sh010")
        live = os.path.join(self.root, "refs", "shots", "sh020", "first.png")
        self.assertTrue(self.kf().picked)
        first = T.file_sha1(live)
        res = self.kf(frame=0)                             # a live file: added, not picked
        self.assertEqual((res.take.take, res.picked), (2, False))
        self.assertEqual(T.file_sha1(live), first)
        res = self.kf(frame=0, pick=True)
        self.assertEqual((res.take.take, res.picked), (3, True))
        self.assertNotEqual(T.file_sha1(live), first)
        self.assertFalse(self.kf("sh040", source_shot="sh010", pick=False).picked)  # asked not to

    # -- stale by ref ------------------------------------------------------------

    def render_take(self, shot: str) -> T.Take:
        """What a render would leave: a finished take with its sidecar's refs."""
        doc, i = J.find_shot(self.root, "proxy", shot)
        job = J.plan_job(self.root, "proxy", doc, i, J.RenderRequest(shot),
                         T.load_overrides(self.root))
        take = J.start_job(job)
        T.update_sidecar(take.paths.sidecar, status="ok")
        make_mp4(take.paths.mp4)
        return take

    def stale(self, shot: str) -> dict[int, list[str]]:
        st = E.episode_status(self.root, "proxy")
        return {t["take"]: t["stale"] for s in st["shots"] if s["shot"] == shot
                for t in s["takes"]}

    def test_reextracting_makes_the_takes_that_used_it_ref_stale(self):
        make_take(self.root, "proxy", "sh010")
        self.kf()
        used = self.render_take("sh020")                  # an LTX take on that keyframe
        (first,) = [r for r in used.sidecar["refs"] if r.get("role") == "first"]
        self.assertEqual(first["sha1"], T.file_sha1(os.path.join(
            self.root, "refs", "shots", "sh020", "first.png")))
        self.assertEqual(self.stale("sh020"), {used.take: []})
        # the previous shot is re-rendered; its new last frame is re-extracted
        make_take(self.root, "proxy", "sh010", source="testsrc2")
        res = self.kf(pick=True)
        self.assertEqual(res.source["take"], 2)
        self.assertEqual(self.stale("sh020"), {used.take: ["ref"]})
        # re-picking the old frame puts it back as it was
        R.pick_take(self.s, res.ref, None, 1)
        self.assertEqual(self.stale("sh020"), {used.take: []})

    def test_a_take_rendered_without_a_keyframe_is_stale_once_there_is_one(self):
        make_take(self.root, "proxy", "sh030")
        t = self.render_take("sh040")                     # LTX, text only
        # the keyframes; the sheet panels beside them carry no role
        self.assertEqual({r["role"]: r["sha1"] for r in t.sidecar["refs"] if r.get("role")},
                         {"first": None, "last": None})
        self.assertEqual(self.stale("sh040"), {t.take: []})
        self.kf("sh040")
        self.assertEqual(self.stale("sh040"), {t.take: ["ref"]})

    # -- the CLI -------------------------------------------------------------

    def cli(self, *args):
        return subprocess.run([sys.executable, os.path.join(ROOT, "h3.py"), "keyframe",
                               self.root, *args], capture_output=True, text=True, env=ENV)

    def test_cli(self):
        make_take(self.root, "proxy", "sh010")
        r = self.cli("sh020", "--from-prev", "--proxy")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("shot:sh020:first t01: frame 16 of 17 of sh010 proxy t01", r.stdout)
        self.assertIn("picked: refs/shots/sh020/first.png", r.stdout)
        r = self.cli("sh020", "--from", "sh010:1", "--frame", "-17", "--proxy")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("t02: frame 0 of 17", r.stdout)
        self.assertIn("not picked", r.stdout)
        r = self.cli("sh020", "--last", "--from", "sh010", "--frame", "first", "--proxy",
                     "--pick")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("shot:sh020:last t01: frame 0", r.stdout)
        r = self.cli("sh010", "--proxy")
        self.assertEqual(r.returncode, 1)
        self.assertIn("no previous shot", r.stdout)
        r = self.cli("sh020")                              # final: sh010 has no final take
        self.assertEqual(r.returncode, 1)
        self.assertIn("no usable final take", r.stdout)
        r = self.cli("sh020", "--from", "sh010:x", "--proxy")
        self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()
