"""
Phase 9b (docs/API.md "Phase 9b: timeline editing"): cut edits (PUT /h3pipe/cut's
trim checks and _history/ copies, POST /h3pipe/cut/reset and /cut/copy), the
episode status's new fields (cut.order, script_index, out_of_order, track,
audio_in/audio_out, take audio), locked picks, GET /h3pipe/peaks (h3peaks:
stdlib wav, ffmpeg for the rest, the cache, resampling, path safety) and
`h3.py cut`. The routes run through h3pipe_api with test_api's built
kitchen_sink episode; the aiohttp adapter is in test_routes.
"""
from __future__ import annotations

import contextlib
import io
import os
import shutil
import struct
import subprocess
import sys
import unittest
import wave
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "comfy_nodes"))
import h3edit as E  # noqa: E402
import h3jobs as J  # noqa: E402
import h3peaks as PK  # noqa: E402
import h3pipe_api as A  # noqa: E402
import h3source as H  # noqa: E402
import h3takes as T  # noqa: E402
import test_api  # noqa: E402
from test_api import ApiTest  # noqa: E402

HAVE_FF = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def tearDownModule():
    test_api.tearDownModule()


def write_wav(path: str, samples: list[int], rate: int = 8000, channels: int = 1,
              width: int = 2) -> None:
    """A PCM wav of `samples` (interleaved when stereo), each in the 16-bit range
    and stored at `width` bytes."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    out = bytearray()
    for v in samples:
        if width == 1:
            out += bytes([(v >> 8) + 128])
        elif width == 2:
            out += struct.pack("<h", v)
        elif width == 3:
            out += struct.pack("<i", v << 8)[:3]
        else:
            out += struct.pack("<i", v << 16)
    with wave.open(path, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(bytes(out))


def steps_wav(path: str, levels: list[int], seconds_each: float = 0.5, rate: int = 8000,
              **kw) -> None:
    """A square wave whose amplitude is each of `levels` in turn."""
    n = int(seconds_each * rate)
    samples = []
    for lv in levels:
        samples += [lv if i % 2 else -lv for i in range(n)]
    write_wav(path, samples, rate, **kw)


def ff(*args: str) -> None:
    r = subprocess.run(["ffmpeg", "-y", "-v", "error", *args], capture_output=True, text=True,
                       timeout=120)
    if r.returncode != 0:
        raise RuntimeError(r.stderr)


def make_mp4(path: str, seconds: float, audio: bool) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    args = ["-f", "lavfi", "-i", f"testsrc=size=64x64:rate=24:duration={seconds}"]
    if audio:
        args += ["-f", "lavfi", "-i",
                 f"sine=frequency=440:sample_rate=8000:duration={seconds}", "-c:a", "aac"]
    ff(*args, "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", path)


class Base(ApiTest):
    def setUp(self):
        super().setUp()
        self.order = E.script_order(self.ep, "proxy")

    def put_cut(self, entries, status=200, pass_="proxy"):
        res = A.put_cut(self.ctx, {"ep": self.ep, "pass": pass_, "entries": entries})
        return self.ok(res)["cut"] if status == 200 else self.err(res, status)

    def length(self, shot, pass_="proxy"):
        return next(s for d, i in J.episode_shots(self.ep, pass_)
                    for s in [d["shots"][i]] if s["id"] == shot)["length"]

    def history(self):
        return H.history(self.ep, "cut.json")


class CutEditTest(Base):
    def test_trim_validation(self):
        self.render("sh010")
        n = self.length("sh010")
        self.assertEqual(T.get_take(self.ep, "proxy", "sh010", 1).sidecar["frames"], n)
        for bad in (1.5, "3", True, -1):
            self.put_cut([{"shot": "sh010", "trim_in": bad}], 400)
        msg = self.put_cut([{"shot": "sh010", "trim_in": n - 1, "trim_out": 1}], 400)
        self.assertIn("at least one frame", msg)
        cut = self.put_cut([{"shot": "sh010", "trim_in": n - 2, "trim_out": 1}])
        self.assertEqual(cut["proxy"][0], {"shot": "sh010", "trim_in": n - 2, "trim_out": 1})
        # no take yet (or a take whose length isn't known): trims are taken as given
        self.put_cut([{"shot": "sh020", "trim_in": 10_000}])
        # a picked take that isn't usable has no known length either
        self.put_cut([{"shot": "sh010", "take": 7, "trim_in": 10_000}])
        # the pipeline checks too (the CLI and anything else calling it)
        with self.assertRaises(E.CutError):
            E.replace_cut(self.ep, "proxy", [{"shot": "sh010", "trim_out": n}])
        with self.assertRaises(E.CutError):
            E.replace_cut(self.ep, "proxy", [{"shot": "sh010", "trim_out": 2.0}])

    def test_dialogue_window_counts(self):
        """assemble cuts a windowed shot to its window before the trims, so the
        check counts from the window (sh110: 1.0-3.5 s), even with no take yet."""
        fps = E.pass_fps(self.ep, "proxy")
        keep = E.dialogue_windows(self.ep, "proxy", fps)["sh110"]
        self.assertEqual(keep, round(2.5 * fps))
        self.put_cut([{"shot": "sh110", "trim_in": keep - 2, "trim_out": 1}])
        msg = self.put_cut([{"shot": "sh110", "trim_in": keep - 1, "trim_out": 1}], 400)
        self.assertIn(f"({keep} frames)", msg)

    def test_other_rate_take_counts_by_duration(self):
        self.render("sh010")
        n = self.length("sh010")
        t = T.get_take(self.ep, "proxy", "sh010", 1)
        T.update_sidecar(t.paths.sidecar, fps=16.0)            # a Wan 14B take
        span = round(n * 24 / 16)
        self.assertEqual(E.take_span(self.ep, T.CutEntry("sh010", "proxy"), 24.0), span)
        self.put_cut([{"shot": "sh010", "trim_in": span - 1}])
        self.put_cut([{"shot": "sh010", "trim_in": span}], 400)

    def test_history_copies(self):
        self.assertEqual(self.history(), [])
        self.put_cut([{"shot": "sh020"}])                  # no cut.json before: no copy
        self.assertEqual(self.history(), [])
        with open(os.path.join(self.ep, "cut.json"), "rb") as fh:
            first = fh.read()
        self.put_cut([{"shot": "sh030"}])
        (h,) = self.history()
        with open(h, "rb") as fh:
            self.assertEqual(fh.read(), first)
        self.assertTrue(os.path.basename(h).startswith("cut.json."))
        self.put_cut([{"shot": "sh030"}])                  # the same cut: nothing written
        self.assertEqual(len(self.history()), 1)
        for i in range(H.HISTORY_KEEP + 3):
            self.put_cut([{"shot": "sh030", "note": f"n{i}"}])
        self.assertEqual(len(self.history()), H.HISTORY_KEEP)
        self.assertEqual(self.events_of("h3pipe.episode")[-1], {"ep": self.ep})

    def test_reset(self):
        self.render("sh010")
        self.render("sh020")
        a, b, c = self.order[:3]
        self.put_cut([{"shot": c, "trim_in": 2, "note": "keep"},
                      {"shot": b, "trim_out": 3, "locked": True},
                      {"shot": a, "take": 1, "trim_in": 1}, {"shot": "sh999"}])
        res = self.ok(A.post_cut_reset(self.ctx, {"ep": self.ep, "pass": "proxy",
                                                  "what": "order"}))["cut"]["proxy"]
        self.assertEqual([e["shot"] for e in res], self.order + ["sh999"])
        self.assertEqual(res[:3], [{"shot": a, "take": 1, "trim_in": 1},
                                   {"shot": b, "trim_out": 3, "locked": True},
                                   {"shot": c, "trim_in": 2, "note": "keep"}])
        res = self.ok(A.post_cut_reset(self.ctx, {"ep": self.ep, "pass": "proxy",
                                                  "what": "trims"}))["cut"]["proxy"]
        # picks, notes and locks kept; a locked entry keeps its trims
        self.assertEqual(res[:3], [{"shot": a, "take": 1},
                                   {"shot": b, "trim_out": 3, "locked": True},
                                   {"shot": c, "note": "keep"}])
        self.put_cut([{"shot": c, "trim_in": 2}, {"shot": a}])
        res = self.ok(A.post_cut_reset(self.ctx, {"ep": self.ep, "pass": "proxy",
                                                  "what": "all"}))["cut"]["proxy"]
        self.assertEqual(res, [{"shot": s} for s in self.order])
        self.assertEqual(self.events_of("h3pipe.episode")[-1], {"ep": self.ep})
        self.err(A.post_cut_reset(self.ctx, {"ep": self.ep, "pass": "proxy", "what": "x"}), 400)
        self.err(A.post_cut_reset(self.ctx, {"ep": self.ep, "pass": "draft",
                                             "what": "all"}), 400)

    def test_copy_same_rate(self):
        a, b, c = self.order[:3]
        E.reorder_cut(self.ep, "final", [c, a])
        E.set_cut_entry(self.ep, "final", c, trim_in=4, trim_out=2)
        self.put_cut([{"shot": a, "take": 3, "note": "mine"}, {"shot": b, "locked": True,
                                                              "trim_in": 9}])
        res = self.ok(A.post_cut_copy(self.ctx, {"ep": self.ep, "from": "final", "to": "proxy",
                                                 "what": "all"}))["cut"]
        self.assertEqual(res["proxy"][:3], [{"shot": c, "trim_in": 4, "trim_out": 2},
                                            {"shot": a, "take": 3, "note": "mine"},
                                            {"shot": b, "trim_in": 9, "locked": True}])
        self.assertEqual([e["shot"] for e in res["proxy"]],
                         [e.shot for e in E.cut_entries(self.ep, "final")])
        for bad in ({"from": "final", "to": "final", "what": "all"},
                    {"from": "final", "to": "proxy", "what": "picks"},
                    {"from": "draft", "to": "proxy", "what": "all"}):
            self.err(A.post_cut_copy(self.ctx, dict(bad, ep=self.ep)), 400)

    def test_copy_converts_trims_between_rates(self):
        # no series.fps: each pass counts in its shotlist's rate
        p = os.path.join(self.ep, "series.json")
        cfg = T.read_json(p)
        cfg["series"].pop("fps", None)
        T.write_json(p, cfg)
        sl = os.path.join(self.ep, J.shotlist_rel("proxy"))
        doc = T.read_json(sl)
        doc["defaults"]["fps"] = 12
        T.write_json(sl, doc)
        self.assertEqual((E.pass_fps(self.ep, "final"), E.pass_fps(self.ep, "proxy")),
                         (24.0, 12.0))
        a, b = self.order[:2]
        E.reorder_cut(self.ep, "final", [b, a])
        E.set_cut_entry(self.ep, "final", b, trim_in=8, trim_out=3)
        res = self.ok(A.post_cut_copy(self.ctx, {"ep": self.ep, "from": "final", "to": "proxy",
                                                 "what": "trims"}))["cut"]["proxy"]
        # trims only: proxy keeps script order
        self.assertEqual(res[:2], [{"shot": a}, {"shot": b, "trim_in": 4, "trim_out": 2}])
        # and back: 4/2 at 12 fps are 8/4 at 24
        E.copy_cut(self.ep, "proxy", "final", "trims")
        final = T.load_cut(self.ep)["final"]
        self.assertEqual(final[:2], [{"shot": b, "trim_in": 8, "trim_out": 4}, {"shot": a}])

    def test_copy_keeps_one_frame(self):
        self.render("sh010")
        n = self.length("sh010")
        T.save_cut(self.ep, {"final": [{"shot": "sh010", "trim_in": n, "trim_out": 5}],
                             "proxy": []})
        res = E.copy_cut(self.ep, "final", "proxy", "trims")["proxy"]
        self.assertEqual(res[0], {"shot": "sh010", "trim_in": n - 1})

    def test_locked_pick(self):
        self.render("sh010")
        self.render("sh010", redo=True)
        self.put_cut([{"shot": "sh010", "locked": True}])
        res = A.put_pick(self.ctx, {"ep": self.ep, "pass": "proxy", "shot": "sh010", "take": 1})
        self.assertIn("locked", self.err(res, 409))
        self.assertTrue(res[1]["locked"])
        cut = self.ok(A.put_pick(self.ctx, {"ep": self.ep, "pass": "proxy", "shot": "sh010",
                                            "take": 1, "force": True}))["cut"]
        self.assertEqual(cut["proxy"][0], {"shot": "sh010", "take": 1, "locked": True})
        # the other pass isn't locked
        self.ok(A.put_pick(self.ctx, {"ep": self.ep, "pass": "final", "shot": "sh010",
                                      "take": 1, "from_pass": "proxy"}))
        with self.assertRaises(E.Locked):
            E.pick_take(self.ep, "proxy", "sh010", None)


class StatusTest(Base):
    def test_order_fields(self):
        data, shots = self.status()
        for i, s in enumerate(data["shots"]):
            self.assertEqual((s["cut"]["order"], s["cut"]["script_index"],
                              s["cut"]["out_of_order"]), (i, i, False))
        moved = self.order[4]
        E.move_shot(self.ep, "proxy", moved, self.order[1], after=False)
        self.put_cut([{"shot": e["shot"]} for e in T.load_cut(self.ep)["proxy"]]
                     + [{"shot": "sh999"}])
        data, shots = self.status()
        self.assertEqual([s["shot"] for s in data["shots"][:3]],
                         [self.order[0], moved, self.order[1]])
        self.assertEqual([s["shot"] for s in data["shots"] if s["cut"]["out_of_order"]], [moved])
        self.assertEqual((shots[moved]["cut"]["order"], shots[moved]["cut"]["script_index"]),
                         (1, 4))
        self.assertEqual((shots["sh999"]["cut"]["script_index"],
                          shots["sh999"]["cut"]["out_of_order"]), (None, False))

    def test_out_of_order(self):
        self.assertEqual(E.out_of_order([0, 1, 2]), [False] * 3)
        self.assertEqual(E.out_of_order([4, 0, 1, 2, 3, 5]), [True] + [False] * 5)
        self.assertEqual(E.out_of_order([1, 2, 0, 3]), [False, False, True, False])
        self.assertEqual(E.out_of_order([0, None, 2, 1]).count(True), 1)
        self.assertEqual(E.out_of_order([]), [])

    def test_track_and_windows(self):
        track = os.path.join(self.ep, "audio", "ks01_mix.wav")
        if os.path.exists(track):
            os.remove(track)
        data, shots = self.status()
        self.assertEqual(data["track"], {"path": "audio/ks01_mix.wav", "duration": None,
                                         "rate": None, "exists": False})
        steps_wav(os.path.join(self.ep, "audio", "ks01_mix.wav"), [1000, 2000], rate=16000)
        data, shots = self.status()
        self.assertEqual(data["track"], {"path": "audio/ks01_mix.wav", "duration": 1.0,
                                         "rate": 16000, "exists": True})
        self.assertEqual((shots["sh110"]["audio_in"], shots["sh110"]["audio_out"]), (1.0, 3.5))
        self.assertNotIn("audio_in", shots["sh010"])
        # the peaks route takes the track's path as it comes
        pk = self.ok(A.get_peaks(self.ctx, {"ep": self.ep, "path": data["track"]["path"],
                                            "bins": "2"}))
        self.assertEqual(pk["peaks"], [PK._scale(1000), PK._scale(2000)])
        cfg = T.read_json(os.path.join(self.ep, "series.json"))
        del cfg["audio"]["track"]
        T.write_json(os.path.join(self.ep, "series.json"), cfg)
        self.assertIsNone(self.status()[0]["track"])

    def test_take_audio(self):
        self.render("sh010")
        take = lambda: self.status()[1]["sh010"]["takes"][0]["audio"]  # noqa: E731
        self.assertIsNone(take())                           # an empty mp4 and no wav
        t = T.get_take(self.ep, "proxy", "sh010", 1)
        steps_wav(t.paths.h3_wav, [500])
        self.assertEqual(take(), "renders_proxy/sh010/sh010_t01_h3.wav")
        if HAVE_FF:
            make_mp4(t.paths.mp4, 0.5, audio=True)
            self.assertEqual(take(), "renders_proxy/sh010/sh010_t01.mp4")
            make_mp4(t.paths.mp4, 0.5, audio=False)
            self.assertEqual(take(), "renders_proxy/sh010/sh010_t01_h3.wav")


class PeaksTest(Base):
    def peaks(self, path, status=200, **q):
        res = A.get_peaks(self.ctx, dict({"ep": self.ep, "path": path},
                                         **{k: str(v) for k, v in q.items()}))
        return self.ok(res) if status == 200 else self.err(res, status)

    def test_wav(self):
        steps_wav(os.path.join(self.ep, "a.wav"), [1000, 8000, 32767, 0])
        got = self.peaks("a.wav", bins=4)
        self.assertEqual(got["duration"], 2.0)
        self.assertEqual(got["bins"], 4)
        self.assertEqual(got["peaks"], [8, 62, 255, 0])
        self.assertNotIn("silent", got)
        # the default is the cached resolution: 200 a second
        self.assertEqual(len(self.peaks("a.wav")["peaks"]), 400)
        # a window, and more bins than the source has there
        got = self.peaks("a.wav", bins=10, start=0.5, end=1.0)
        self.assertEqual((got["start"], got["end"], got["peaks"]), (0.5, 1.0, [62] * 10))
        got = self.peaks("a.wav", bins=2000, start=0.99, end=1.01)
        self.assertEqual(set(got["peaks"]), {62, 255})
        self.assertEqual(len(got["peaks"]), 2000)
        # max over the bins each covers
        self.assertEqual(self.peaks("a.wav", bins=1)["peaks"], [255])
        self.assertEqual(self.peaks("a.wav", bins=3, start=0.25, end=1.75)["peaks"],
                         [62, 255, 255])
        for bad in ({"bins": 0}, {"bins": "x"}, {"bins": PK.MAX_BINS + 1}, {"start": -1},
                    {"start": 1.5, "end": 1.0}, {"start": 5}):
            self.peaks("a.wav", 400, **bad)

    def test_wav_widths_and_channels(self):
        for width in (1, 2, 3, 4):
            p = os.path.join(self.ep, f"w{width}.wav")
            steps_wav(p, [16384, 32767], width=width)
            got = self.peaks(f"w{width}.wav", bins=2)["peaks"]
            self.assertTrue(abs(got[0] - 127) <= 2 and got[1] >= 253, (width, got))
        # stereo: the louder channel counts
        write_wav(os.path.join(self.ep, "st.wav"), [100, -3000] * 400 + [0, 0] * 400,
                  channels=2)
        self.assertEqual(self.peaks("st.wav", bins=2)["peaks"], [PK._scale(3000), 0])

    def test_cache(self):
        p = os.path.join(self.ep, "a.wav")
        steps_wav(p, [1000, 2000])
        first = self.peaks("a.wav", bins=2)
        cache = os.listdir(os.path.join(self.ep, "_cache", "peaks"))
        self.assertEqual(len(cache), 1)
        with mock.patch.object(PK, "_wav_peaks", side_effect=AssertionError("recomputed")):
            self.assertEqual(self.peaks("a.wav", bins=2), first)
            self.assertEqual(PK.file_peaks(self.ep, p, "a.wav")["cached"], True)
        # a changed file is a new key
        steps_wav(p, [3000, 3000, 3000])
        os.utime(p, ns=(1, 1))
        got = self.peaks("a.wav", bins=3)
        self.assertEqual(got["duration"], 1.5)
        self.assertEqual(len(os.listdir(os.path.join(self.ep, "_cache", "peaks"))), 2)

    def test_path_safety(self):
        outside = os.path.join(self.shows, "secret.wav")
        steps_wav(outside, [1000])
        self.peaks("../secret.wav", 400)
        self.peaks(outside, 400)
        self.peaks("C:/Windows/win.ini", 400)
        self.peaks("", 400)
        self.peaks("nope.wav", 404)
        self.err(A.get_peaks(self.ctx, {"ep": os.path.join(self.tmp, "x"), "path": "a.wav"}),
                 403)

    def test_not_audio(self):
        with open(os.path.join(self.ep, "notes.txt"), "w") as fh:
            fh.write("hello")
        with mock.patch.object(PK.shutil, "which", return_value=None):
            PK._info_cache.clear()
            got = self.peaks("notes.txt")
            self.assertEqual((got["peaks"], got["silent"]), ([], True))

    def test_ffmpeg_missing_for_mp4_with_sound(self):
        p = os.path.join(self.ep, "x.m4a")
        with open(p, "wb") as fh:
            fh.write(b"not really")
        with mock.patch.object(PK, "media_info", return_value={"audio": True, "duration": 1.0,
                                                                "rate": 8000}), \
                mock.patch.object(PK.shutil, "which", return_value=None):
            self.assertIn("ffmpeg", self.err(A.get_peaks(self.ctx, {"ep": self.ep,
                                                                    "path": "x.m4a"}), 500))

    @unittest.skipUnless(HAVE_FF, "needs ffmpeg")
    def test_mp4(self):
        make_mp4(os.path.join(self.ep, "with.mp4"), 1.0, audio=True)
        make_mp4(os.path.join(self.ep, "mute.mp4"), 1.0, audio=False)
        info = PK.mp4_info(os.path.join(self.ep, "with.mp4"))
        self.assertTrue(info["audio"])
        self.assertEqual(info["rate"], 8000)
        self.assertAlmostEqual(info["duration"], 1.0, delta=0.1)
        self.assertFalse(PK.has_audio(os.path.join(self.ep, "mute.mp4")))
        got = self.peaks("with.mp4", bins=10)
        self.assertEqual(len(got["peaks"]), 10)
        self.assertTrue(all(28 <= v <= 36 for v in got["peaks"][1:-1]), got)  # sine at 1/8
        self.assertAlmostEqual(got["duration"], 1.0, delta=0.1)
        got = self.peaks("mute.mp4", bins=10)
        self.assertEqual((got["peaks"], got["silent"], got["bins"]), ([], True, 0))
        self.assertAlmostEqual(got["duration"], 1.0, delta=0.1)
        # a flac goes through ffmpeg too (and ffprobe for its facts)
        ff("-f", "lavfi", "-i", "sine=frequency=200:sample_rate=8000:duration=0.5",
           os.path.join(self.ep, "s.flac"))
        got = self.peaks("s.flac", bins=4)
        self.assertEqual(len(got["peaks"]), 4)
        self.assertAlmostEqual(got["duration"], 0.5, delta=0.05)

    def test_clip_audio(self):
        mp4 = os.path.join(self.ep, "c.mp4")
        wav = os.path.join(self.ep, "c_h3.wav")
        self.assertIsNone(PK.clip_audio(mp4, wav))
        open(mp4, "wb").close()
        self.assertIsNone(PK.clip_audio(mp4, wav))
        steps_wav(wav, [10])
        self.assertEqual(PK.clip_audio(mp4, wav), wav)
        self.assertEqual(PK.clip_audio(mp4, None), None)
        if HAVE_FF:
            make_mp4(mp4, 0.3, audio=True)
            self.assertEqual(PK.clip_audio(mp4, wav), mp4)


class CliTest(Base):
    def cut(self, *argv, code=0):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            got = E.COMMANDS["cut"](self.ep, ["--proxy", *argv])
        self.assertEqual(got, code, buf.getvalue())
        return buf.getvalue()

    def proxy(self):
        return T.load_cut(self.ep)["proxy"]

    def test_edits(self):
        self.render("sh010")
        n = self.length("sh010")
        a, b, c, d = self.order[:4]
        out = self.cut()
        self.assertIn(f"{len(self.order)} shots", out)
        out = self.cut("--move", d, "--before", b)
        self.assertIn("OUT OF ORDER", out)
        self.assertEqual([e["shot"] for e in self.proxy()][:4], [a, d, b, c])
        self.cut("--move", d, "--after", c)
        self.assertEqual([e["shot"] for e in self.proxy()][:4], [a, b, c, d])
        self.cut("--order", f"{c},{a}")
        self.assertEqual([e["shot"] for e in self.proxy()][:4], [c, a, b, d])
        self.assertIn("!! sh999 is not in the proxy cut", self.cut("--order", "sh999", code=1))
        self.cut("--trim", "sh010", "3", "2")
        self.assertIn({"shot": "sh010", "trim_in": 3, "trim_out": 2}, self.proxy())
        self.assertIn("at least one frame", self.cut("--trim", "sh010", str(n), "0", code=1))
        self.cut("--trim", "sh010", "x", "0", code=1)
        self.cut("--lock", "sh010")
        self.assertIn("unlock it first", self.cut("--trim", "sh010", "0", "0", code=1))
        self.assertIn("unlock it first", self.cut("--move", "sh010", "--after", d, code=1))
        self.cut("--trim", "sh010", "1", "0", "--force")
        self.cut("--unlock", "sh010")
        self.assertIn({"shot": "sh010", "trim_in": 1}, self.proxy())
        self.cut("--reset", "all")
        self.assertEqual(self.proxy(), [{"shot": s} for s in self.order])
        E.reorder_cut(self.ep, "final", [b])
        E.set_cut_entry(self.ep, "final", b, trim_in=2)
        self.cut("--copy-from", "final", "order")
        self.assertEqual(self.proxy()[0], {"shot": b})
        self.cut("--copy-from", "final")
        self.assertEqual(self.proxy()[0], {"shot": b, "trim_in": 2})
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                self.cut("--move", b)
            with self.assertRaises(SystemExit):
                self.cut("--copy-from", "draft")
        self.assertGreater(len(H.history(self.ep, "cut.json")), 5)

    def test_through_h3(self):
        r = subprocess.run([sys.executable, os.path.join(ROOT, "h3.py"), "cut", self.ep,
                            "--proxy", "--move", self.order[2], "--before", self.order[0]],
                           capture_output=True, text=True, timeout=120,
                           env=dict(os.environ, PYTHONIOENCODING="utf-8"))
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertEqual(self.proxy()[0], {"shot": self.order[2]})
        self.assertIn("OUT OF ORDER", r.stdout)


if __name__ == "__main__":
    unittest.main()
