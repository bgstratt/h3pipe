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

    def test_progress_lines(self):
        # --progress, read as h3master reads it (h3edit.run_tool): every step in
        # order, none of it in the log
        import h3edit
        self.shotlist([("sh010", 22), ("sh020", 39)])
        self.take("sh010", 22)
        self.take("sh020", 39)
        heard = []
        rc, out, err = h3edit.run_tool("h3assemble.py", ["-o", self.root, "--progress"], self.root,
                                       120, progress=lambda tool, ev: heard.append((tool, ev)))
        self.assertEqual(rc, 0, out + err)
        self.assertNotIn("##h3assemble", out)
        self.assertIn("-> ", out)
        self.assertEqual({tool for tool, _ in heard}, {"h3assemble"})
        steps = [(ev["stage"], ev["done"], ev["total"], ev["text"]) for _, ev in heard]
        self.assertEqual(steps[:6], [("probe", 0, 2, "sh010"), ("probe", 1, 2, "sh020"),
                                     ("probe", None, None, "comparing the clips' encodings"),
                                     ("clips", 0, 2, ""), ("clips", 1, 2, "sh010"),
                                     ("clips", 2, 2, "sh020")])
        self.assertEqual([s[0] for s in steps[6:]], ["join", "verify"])
        # without --progress, nothing of the sort
        self.assertNotIn("##h3assemble", self.assemble().stdout)

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

    def test_master_quality_and_prores(self):
        """13e3: --quality master re-encodes what must be at CRF 12; clips that
        needn't be are copied as they are; --intermediate prores adds a .mov."""
        self.shotlist([("sh010", 22), ("sh020", 39)])
        self.take("sh010", 22)
        self.take("sh020", 39)
        self.assemble("--quality", "master", "--intermediate", "prores")
        with open(self.out(), "rb") as fh:
            data = fh.read()
        # nothing to re-encode: the clips' own x264 streams (ultrafast, CRF 23) copied
        self.assertIn(b"crf=23.0", data)
        self.assertNotIn(b"crf=12.0", data)
        mov = os.path.splitext(self.out())[0] + ".mov"
        r = ff("ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
               "stream=codec_name,profile", "-of", "csv=p=0", mov)
        self.assertEqual(r.stdout.strip(), "prores,HQ")
        self.assertEqual(probe_frames(mov), 61)
        # a trim re-encodes, at master quality
        self.cut([{"shot": "sh010", "trim_in": 2}, {"shot": "sh020"}])
        self.assemble("--quality", "master")
        with open(self.out(), "rb") as fh:
            self.assertIn(b"crf=12.0", fh.read())

    def test_left_out_shot_is_skipped(self):
        self.shotlist([("sh010", 22), ("sh020", 39), ("sh030", 22)])
        for sid, n in (("sh010", 22), ("sh020", 39), ("sh030", 22)):
            self.take(sid, n)
        self.cut([{"shot": "sh010"}, {"shot": "sh020", "out": True}, {"shot": "sh030"}])
        r = self.assemble()
        self.assertIn("left out of the cut (1): sh020", r.stdout)
        self.assertEqual(self.order(), [("sh010", 1), ("sh030", 1)])
        self.assertEqual(probe_frames(self.out()), 44)

    def test_mixed_profiles_are_reencoded(self):
        """13e4's live check: an NVENC Main upscale beside x264 High ones concat-copied
        into one track with the first clip's headers. Clips whose codec, profile or
        pixel format differ from how the cut is encoded are re-encoded: here neither
        clip is, so both are."""
        self.shotlist([("sh010", 22), ("sh020", 39)])
        self.take("sh010", 22)
        n = self.take("sh020", 39)
        high = T.take_paths(self.root, "final", "sh020", n).mp4
        r = ff("ffmpeg", "-y", "-v", "error", "-i", self.clips[("64x64", 39)], "-c:v", "libx264",
               "-preset", "medium", "-profile:v", "high", "-c:a", "copy", high + ".tmp.mp4")
        self.assertEqual(r.returncode, 0, r.stderr)
        os.replace(high + ".tmp.mp4", high)
        out = self.assemble().stdout
        self.assertIn("none is encoded the way this cut is", out)
        with open(self.out(), "rb") as fh:
            self.assertIn(b"crf=16.0", fh.read())               # re-encoded, review quality
        self.assertEqual(probe_frames(self.out()), 61)

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


def frame_md5s(path: str, first: int, count: int) -> list[str]:
    r = ff("ffmpeg", "-v", "error", "-i", path, "-map", "0:v:0",
           "-vf", f"select=between(n\\,{first}\\,{first + count - 1})", "-fps_mode", "passthrough",
           "-f", "framemd5", "-")
    return [ln.rsplit(",", 1)[1].strip() for ln in r.stdout.splitlines()
            if ln and not ln.startswith("#")]


@unittest.skipUnless(HAVE_FF, "ffmpeg/ffprobe not on PATH")
class SelectiveTest(unittest.TestCase):
    """A clip that must be re-encoded (a trim, a placeholder) no longer re-encodes
    the whole cut: when the other clips are encoded alike, and the way conform
    encodes (a master's upscales are), only it is; the rest are copied."""
    clips: dict = {}

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, ROOT)
        import h3assemble
        cls.A = h3assemble
        cls._cache = tempfile.TemporaryDirectory()
        cls.clips = {}
        for n in (22, 39, 56):
            raw = os.path.join(cls._cache.name, f"raw_{n}.mp4")
            make_clip(raw, n, "64x64")
            cls.clips[("raw", n)] = raw
            # saved the way conform writes a clip: review quality, 32 kHz stereo
            p = os.path.join(cls._cache.name, f"64x64_{n}.mp4")
            h3assemble.conform(raw, p, raw, 24.0, n, layout=(32000, 2))
            cls.clips[("64x64", n)] = p

    @classmethod
    def tearDownClass(cls):
        cls._cache.cleanup()

    setUp = AssembleTest.setUp
    tearDown = AssembleTest.tearDown
    shotlist = AssembleTest.shotlist
    take = AssembleTest.take
    cut = AssembleTest.cut
    assemble = AssembleTest.assemble
    out = AssembleTest.out
    shots_txt = AssembleTest.shots_txt

    def three(self):
        self.shotlist([("sh010", 56), ("sh020", 22), ("sh030", 39)])
        for sid, n in (("sh010", 56), ("sh020", 22), ("sh030", 39)):
            self.take(sid, n)

    def test_only_the_trimmed_clip_is_re_encoded(self):
        self.three()
        self.cut([{"shot": "sh010"}, {"shot": "sh020", "trim_in": 3, "trim_out": 2},
                  {"shot": "sh030"}])
        r = self.assemble()
        self.assertIn("re-encoded 1 clip(s), copied 2", r.stdout)
        self.assertEqual([int(row[3]) for row in self.shots_txt()], [56, 17, 39])
        out = self.out()
        self.assertEqual(probe_frames(out), 112)
        # the copied clips are their own frames, untouched, on both sides of the trim
        self.assertEqual(frame_md5s(out, 0, 56), frame_md5s(self.clips[("64x64", 56)], 0, 56))
        self.assertEqual(frame_md5s(out, 73, 39), frame_md5s(self.clips[("64x64", 39)], 0, 39))
        # the trimmed clip's frames are the source's 3..19, re-encoded
        self.assertEqual(len(frame_md5s(out, 56, 17)), 17)
        # one sound for the whole cut, in the copies' rate and layout
        self.assertEqual(self.A.stream_sig(out)[1], (32000, 2))

    def test_a_clip_encoded_differently_is_conformed(self):
        self.shotlist([("sh010", 56), ("sh020", 22), ("sh030", 39)])
        self.take("sh010", 56)
        self.take("sh020", 22)
        t = T.reserve_take(self.root, "final", "sh030", {"status": "queued"})
        # H.264 High like the others, but slow-preset headers, not medium's
        raw = self.clips[("raw", 39)]
        self.A.conform(raw, t.paths.mp4, raw, 24.0, 39, quality="master", layout=(32000, 2))
        T.update_sidecar(t.paths.sidecar, status="ok", frames=39)
        self.cut([{"shot": "sh010"}, {"shot": "sh020", "trim_in": 3}, {"shot": "sh030"}])
        r = self.assemble()
        # sh030 isn't encoded the way the cut is: conformed beside the trimmed clip
        self.assertIn("1 clip(s) not encoded the way this cut is, conformed: sh030", r.stdout)
        self.assertIn("re-encoded 2 clip(s), copied 1", r.stdout)
        self.assertEqual(probe_frames(self.out()), 114)
        self.assertEqual(frame_md5s(self.out(), 0, 56), frame_md5s(self.clips[("64x64", 56)], 0, 56))

    def test_headers_that_dont_match_re_encode_all(self):
        # copies saved at review quality; a master-quality re-encode can't match them
        self.three()
        self.cut([{"shot": "sh010"}, {"shot": "sh020", "trim_out": 4}, {"shot": "sh030"}])
        r = self.assemble("--quality", "master")
        self.assertIn("none is encoded the way this cut is", r.stdout)
        self.assertIn("re-encoded 3 clip(s)", r.stdout)
        self.assertEqual(probe_frames(self.out()), 113)

    def test_a_derived_takes_pixel_tag_is_conformed_alone(self):
        """ep01's derived takes (cropped from another take) carry a 1:1 pixel
        tag in their headers; nothing else needs re-encoding, so only that clip
        is conformed and the rest still copied."""
        self.three()
        mp4 = T.take_paths(self.root, "final", "sh020", 1).mp4
        r = ff("ffmpeg", "-y", "-v", "error", "-i", mp4, "-c", "copy",
               "-bsf:v", "h264_metadata=sample_aspect_ratio=1/1", mp4 + ".tag.mp4")
        self.assertEqual(r.returncode, 0, r.stderr)
        os.replace(mp4 + ".tag.mp4", mp4)
        self.assertNotEqual(self.A.stream_sig(mp4)[0],
                            self.A.stream_sig(self.clips[("64x64", 56)])[0])
        r = self.assemble()
        self.assertIn("conformed: sh020", r.stdout)
        self.assertIn("re-encoded 1 clip(s), copied 2", r.stdout)
        self.assertEqual(probe_frames(self.out()), 117)

    def test_a_scaled_clip_matches_the_copies(self):
        """A clip at another size is scaled; it comes out untagged, as the takes
        are, so the takes beside it are still copied."""
        self.shotlist([("sh010", 56), ("sh020", 22), ("sh030", 39)])
        self.take("sh010", 56)
        t = T.reserve_take(self.root, "final", "sh020", {"status": "queued"})
        raw = os.path.join(self.root, "raw32.mp4")
        make_clip(raw, 22, "32x32")
        self.A.conform(raw, t.paths.mp4, raw, 24.0, 22, layout=(32000, 2))
        T.update_sidecar(t.paths.sidecar, status="ok", frames=22)
        self.take("sh030", 39)
        r = self.assemble()
        self.assertIn("1 clip(s) at another size", r.stdout)
        self.assertIn("re-encoded 1 clip(s), copied 2", r.stdout)
        self.assertEqual(probe_frames(self.out()), 117)

    def test_reencode_all_asks_for_the_old_way(self):
        self.three()
        self.cut([{"shot": "sh010"}, {"shot": "sh020", "trim_in": 1}, {"shot": "sh030"}])
        r = self.assemble("--reencode-all")
        self.assertIn("re-encoded 3 clip(s)", r.stdout)
        self.assertNotIn("copied", r.stdout.split("re-encoded")[-1].splitlines()[0])
        self.assertEqual(probe_frames(self.out()), 116)

    def test_nothing_to_re_encode_copies_everything(self):
        self.three()
        r = self.assemble()
        self.assertNotIn("re-encoded", r.stdout)
        self.assertEqual(probe_frames(self.out()), 117)

    def overrun_take(self, shot: str, frames: int) -> None:
        """A take whose sound runs 60 ms past its picture (an upscale like
        sh1360's): its picture is the conformed clip's, copied."""
        t = T.reserve_take(self.root, "final", shot, {"status": "queued"})
        r = ff("ffmpeg", "-y", "-v", "error", "-i", self.clips[("64x64", frames)],
               "-f", "lavfi", "-t", f"{frames / 24 + 0.06:.6f}", "-i", "sine=frequency=330",
               "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
               "-c:a", "aac", "-ar", "32000", "-ac", "2", t.paths.mp4)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertGreater(self.A.stream_sig(t.paths.mp4)[2], 0.05)
        T.update_sidecar(t.paths.sidecar, status="ok", frames=frames)

    def assert_even_timing(self, out: str, frames: int) -> None:
        self.assertIsNone(self.A.timing_gap(out, frames, 24.0))
        self.assertAlmostEqual(self.A.video_duration(out), frames / 24, delta=0.5 / 24)

    def test_sound_past_the_picture_leaves_no_gap_when_copying(self):
        self.shotlist([("sh010", 56), ("sh020", 22), ("sh030", 39)])
        self.take("sh010", 56)
        self.overrun_take("sh020", 22)
        self.take("sh030", 39)
        r = self.assemble()
        self.assertNotIn("gap in its timing", r.stdout)
        out = self.out()
        self.assert_even_timing(out, 117)
        # the picture after it is still exactly its own frames
        self.assertEqual(frame_md5s(out, 78, 39), frame_md5s(self.clips[("64x64", 39)], 0, 39))

    def test_sound_past_the_picture_beside_a_re_encoded_clip(self):
        self.shotlist([("sh010", 56), ("sh020", 22), ("sh030", 39)])
        self.take("sh010", 56)
        self.overrun_take("sh020", 22)
        self.take("sh030", 39)
        self.cut([{"shot": "sh010"}, {"shot": "sh020"}, {"shot": "sh030", "trim_in": 4}])
        r = self.assemble()
        self.assertIn("re-encoded 1 clip(s), copied 2", r.stdout)
        self.assert_even_timing(self.out(), 113)

    def test_seeking_a_frame_lands_on_it(self):
        """frame_md5s (and h3publish.frame_hashes) seek by time: on a concat with
        sound, whose picture starts after its sound, and on that picture alone,
        both must land on the frame asked for, as a full decode counts it."""
        self.three()
        self.assemble()
        out = self.out()
        pic = os.path.join(self.root, "picture.mp4")
        ff("ffmpeg", "-y", "-v", "error", "-i", out, "-map", "0:v:0", "-c", "copy", pic)
        import h3publish
        for path in (out, pic):
            for first in (0, 1, 55, 56, 57, 110):
                want = frame_md5s(path, first, 4)
                self.assertEqual(self.A.frame_md5s(path, first, 4, 24.0), want, (path, first))
                self.assertEqual(h3publish.frame_hashes(path, first, 4, {"fps": 24.0}), want)

    def test_timing_gap(self):
        p = os.path.join(self.root, "gap.mp4")
        lst = os.path.join(self.root, "l.txt")
        os.makedirs(self.root, exist_ok=True)
        long = os.path.join(self.root, "long.mp4")
        ff("ffmpeg", "-y", "-v", "error", "-i", self.clips[("64x64", 22)], "-f", "lavfi",
           "-t", "1.2", "-i", "sine", "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
           "-c:a", "aac", long)
        with open(lst, "w", encoding="utf-8") as fh:
            for f in (long, self.clips[("64x64", 22)]):
                fh.write(f"file '{f.replace(os.sep, '/')}'\n")
        ff("ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", p)
        self.assertIn("gap in its timing", self.A.timing_gap(p, 44, 24.0))


@unittest.skipUnless(HAVE_FF, "ffmpeg/ffprobe not on PATH")
class FrameCountTest(unittest.TestCase):
    """frame_count reads an mp4's header count; decoding is the fallback."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name
        sys.path.insert(0, ROOT)
        import h3assemble
        self.A = h3assemble

    def tearDown(self):
        self._tmp.cleanup()

    def test_mp4_from_its_header(self):
        p = os.path.join(self.dir, "c.mp4")
        make_clip(p, 39, "32x32")
        seen = []
        real = self.A.run

        def spy(cmd, *a, **k):
            seen.append(cmd)
            return real(cmd, *a, **k)

        self.A.run = spy
        try:
            self.assertEqual(self.A.frame_count(p), 39)
        finally:
            self.A.run = real
        self.assertEqual(len(seen), 1)
        self.assertNotIn("-count_frames", seen[0])
        self.assertEqual(probe_frames(p), 39)

    def test_no_header_count_is_decoded(self):
        p = os.path.join(self.dir, "c.h264")             # a raw stream: no container count
        r = ff("ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=32x32:rate=24",
               "-frames:v", "22", "-c:v", "libx264", "-preset", "ultrafast", "-f", "h264", p)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.A.frame_count(p), 22)

    def test_missing_file(self):
        self.assertEqual(self.A.frame_count(os.path.join(self.dir, "nope.mp4")), -1)


if __name__ == "__main__":
    unittest.main()
