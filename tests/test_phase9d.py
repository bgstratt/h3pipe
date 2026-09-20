"""
Phase 9d (docs/API.md "Phase 9d: a shot's audio from elsewhere"): a cut
entry's `audio` — its shape (h3takes.audio_spec), what PUT /h3pipe/cut
refuses, the round trip through cut.json, the episode status's `audio`,
`audio_file` and `audio_why`, reset/copy, `h3.py cut --audio`, and real
assembles where h3assemble lays the chosen sound under a clip.

The route tests run through h3pipe_api with test_api's built kitchen_sink
episode; the assemble tests build tiny episodes with ffmpeg and are skipped
when ffmpeg/ffprobe isn't on PATH.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "comfy_nodes"))
import h3edit as E  # noqa: E402
import h3peaks as PK  # noqa: E402
import h3pipe_api as A  # noqa: E402
import h3takes as T  # noqa: E402
import test_api  # noqa: E402
from test_api import ApiTest  # noqa: E402
from test_phase9b import steps_wav  # noqa: E402

ASSEMBLE = os.path.join(ROOT, "h3assemble.py")
HAVE_FF = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
FPS = 24


def tearDownModule():
    test_api.tearDownModule()


# ---------------------------------------------------------------------------
# the shape of `audio` (no disk)
# ---------------------------------------------------------------------------

class SpecTest(unittest.TestCase):
    def test_take(self):
        self.assertEqual(T.audio_spec({"source": "take", "shot": "sh020", "take": 1},
                                      "final"),
                         {"source": "take", "shot": "sh020", "take": 1, "pass": "final"})
        # the list's pass is the default; defaults are dropped
        self.assertEqual(
            T.audio_spec({"source": "take", "shot": "sh020", "take": 3, "pass": "final",
                          "start": 0.0, "offset": 0.0, "gain": 1.0}, "proxy"),
            {"source": "take", "shot": "sh020", "take": 3, "pass": "final"})
        self.assertEqual(
            T.audio_spec({"source": "take", "shot": "sh020", "take": 3,
                          "start": 1.5, "offset": -0.25, "gain": 4}, "proxy"),
            {"source": "take", "shot": "sh020", "take": 3, "pass": "proxy",
             "start": 1.5, "offset": -0.25, "gain": 4.0})

    def test_file_and_none(self):
        self.assertEqual(T.audio_spec({"source": "file", "path": "audio\\a.wav"}),
                         {"source": "file", "path": "audio/a.wav"})
        self.assertEqual(T.audio_spec({"source": "none"}), {"source": "none"})
        # silence keeps nothing else, not even the knobs
        self.assertEqual(T.audio_spec({"source": "none", "gain": 2}), {"source": "none"})
        self.assertIsNone(T.audio_spec(None))

    def test_bad(self):
        bad = [
            None,                                          # replaced below
            {"source": "voice"},
            {"source": "take", "take": 1},                 # no shot
            {"source": "take", "shot": "sh020"},           # no take
            {"source": "take", "shot": "sh020", "take": 0},
            {"source": "take", "shot": "sh020", "take": True},
            {"source": "take", "shot": "sh020", "take": "1"},
            {"source": "take", "shot": "sh020", "take": 1, "pass": "draft"},
            {"source": "take", "shot": "sh020", "take": 1, "path": "a.wav"},
            {"source": "file"},                            # no path
            {"source": "file", "path": "a.wav", "take": 1},
            {"source": "none", "path": "a.wav"},
            {"source": "take", "shot": "sh020", "take": 1, "start": -0.1},
            {"source": "take", "shot": "sh020", "take": 1,
             "start": T.SECONDS_MAX + 1},
            {"source": "take", "shot": "sh020", "take": 1,
             "offset": -T.SECONDS_MAX - 1},
            {"source": "take", "shot": "sh020", "take": 1, "gain": 4.5},
            {"source": "take", "shot": "sh020", "take": 1, "gain": -1},
            {"source": "take", "shot": "sh020", "take": 1, "gain": "loud"},
            {"source": "take", "shot": "sh020", "take": 1, "loud": True},
            {"source": "take", "shot": "sh020", "take": 1, "offset": float("nan")},
        ]
        bad[0] = {"source": "take", "shot": "sh020", "take": 1, "offset": float("inf")}
        for raw in bad:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                T.audio_spec(raw, "final")
        for raw in ("x", 3, []):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                T.audio_spec(raw, "final")

    def test_label(self):
        self.assertEqual(T.audio_label({"source": "take", "shot": "sh020", "take": 1,
                                        "pass": "final"}, "final"), "sh020 t01")
        self.assertEqual(T.audio_label({"source": "take", "shot": "sh020", "take": 1,
                                        "pass": "proxy"}, "final"), "sh020 t01 (proxy)")
        self.assertEqual(T.audio_label({"source": "file", "path": "a/line_sh030.wav"}),
                         "line_sh030.wav")
        self.assertEqual(T.audio_label({"source": "none"}), "silent")
        self.assertEqual(T.audio_label(None), "")

    def test_entry_round_trip(self):
        spec = {"source": "take", "shot": "sh020", "take": 2, "pass": "final", "gain": 0.5}
        raw = {"shot": "sh010", "take": 1, "audio": spec}
        e = T.resolve_cut({"final": [raw]}, "final", ["sh010"])[0]
        self.assertEqual(e.audio, spec)
        self.assertEqual(T.cut_entry_to_json(e, "final"), raw)
        self.assertEqual(e.extra, {})                       # `audio` is a known field
        # a hand-edited, unusable `audio` is ignored rather than kept
        e2 = T.resolve_cut({"final": [{"shot": "sh010", "audio": {"source": "nope"}}]},
                           "final", ["sh010"])[0]
        self.assertIsNone(e2.audio)
        self.assertEqual(T.cut_entry_to_json(e2, "final"), {"shot": "sh010"})


# ---------------------------------------------------------------------------
# PUT /h3pipe/cut, the status, reset/copy, the CLI
# ---------------------------------------------------------------------------

class Base(ApiTest):
    def setUp(self):
        super().setUp()
        self.order = E.script_order(self.ep, "proxy")

    def put_cut(self, entries, status=200, pass_="proxy"):
        res = A.put_cut(self.ctx, {"ep": self.ep, "pass": pass_, "entries": entries})
        return self.ok(res)["cut"] if status == 200 else self.err(res, status)

    def proxy(self):
        return T.load_cut(self.ep)["proxy"]

    def sounding_take(self, shot="sh020", pass_="proxy"):
        """Render a take and give it a `_h3.wav`, so it has sound."""
        self.render(shot, pass_=pass_)
        t = T.get_take(self.ep, pass_, shot, 1)
        steps_wav(t.paths.h3_wav, [4000])
        return t

    def audio_file(self, rel="audio/line.wav"):
        steps_wav(os.path.join(self.ep, *rel.split("/")), [6000])
        return rel


class ValidationTest(Base):
    def test_each_400(self):
        self.sounding_take("sh020")
        rel = self.audio_file()
        ok = {"source": "take", "shot": "sh020", "take": 1}
        # every 400 the contract lists
        cases = {
            "unknown audio field": dict(ok, loud=True),
            "audio source must be one of": {"source": "recording"},
            "there is no such take": dict(ok, take=9),
            "has no sound": {"source": "take", "shot": "sh030", "take": 1},
            "inside the episode": {"source": "file", "path": "../outside.wav"},
            "no audio stream": {"source": "file", "path": "ks01.md"},
            "audio start must be": dict(ok, start=-0.5),
            "audio gain must be": dict(ok, gain=4.5),
        }
        self.render("sh030")                               # a take with no sound at all
        for want, spec in cases.items():
            with self.subTest(want=want):
                msg = self.put_cut([{"shot": "sh010", "audio": spec}], 400)
                self.assertIn(want, msg)
        # nothing was written by any of them
        self.assertEqual(self.proxy(), [])
        # and the good one is
        cut = self.put_cut([{"shot": "sh010", "audio": ok},
                            {"shot": "sh030", "audio": {"source": "file", "path": rel}}])
        self.assertEqual(cut["proxy"][0]["audio"],
                         {"source": "take", "shot": "sh020", "take": 1, "pass": "proxy"})
        self.assertEqual(cut["proxy"][1]["audio"], {"source": "file", "path": rel})

    def test_missing_file_and_paths(self):
        for path in ("../outside.wav", "/etc/passwd", "C:\\windows\\x.wav", ""):
            with self.subTest(path=path):
                self.put_cut([{"shot": "sh010",
                               "audio": {"source": "file", "path": path}}], 400)
        msg = self.put_cut([{"shot": "sh010",
                             "audio": {"source": "file", "path": "audio/nope.wav"}}], 400)
        self.assertIn("no audio file", msg)

    def test_pipeline_checks_too(self):
        with self.assertRaises(E.CutError):
            E.replace_cut(self.ep, "proxy", [{"shot": "sh010",
                                              "audio": {"source": "take", "shot": "sh020"}}])
        with self.assertRaises(E.CutError):
            E.set_cut_entry(self.ep, "proxy", "sh010",
                            audio={"source": "file", "path": "nope.wav"})

    def test_round_trip_through_cut_json(self):
        self.sounding_take("sh020")
        spec = {"source": "take", "shot": "sh020", "take": 1, "pass": "proxy",
                "start": 0.5, "offset": -0.25, "gain": 2.0}
        self.put_cut([{"shot": "sh010", "trim_in": 2, "audio": dict(spec)}])
        with open(os.path.join(self.ep, "cut.json"), encoding="utf-8") as fh:
            on_disk = json.load(fh)
        self.assertEqual(on_disk["proxy"][0],
                         {"shot": "sh010", "trim_in": 2, "audio": spec})
        e = next(e for e in E.cut_entries(self.ep, "proxy") if e.shot == "sh010")
        self.assertEqual(e.audio, spec)
        # writing the resolved cut back keeps it byte for byte
        E.set_cut_entry(self.ep, "proxy", "sh020", note="x")
        self.assertEqual(self.proxy()[0], {"shot": "sh010", "trim_in": 2, "audio": spec})

    def test_locked(self):
        self.sounding_take("sh020")
        spec = {"source": "none"}
        self.put_cut([{"shot": "sh010", "locked": True}])
        with self.assertRaises(E.Locked):
            E.set_cut_entry(self.ep, "proxy", "sh010", audio=spec)
        E.set_cut_entry(self.ep, "proxy", "sh010", audio=spec, force=True)
        self.assertEqual(self.proxy()[0], {"shot": "sh010", "locked": True,
                                           "audio": spec})
        # the server itself doesn't refuse a locked entry in a PUT (the UI does)
        self.put_cut([{"shot": "sh010", "locked": True,
                       "audio": {"source": "take", "shot": "sh020", "take": 1}}])


class StatusTest(Base):
    def test_fields(self):
        self.sounding_take("sh020")
        rel = self.audio_file()
        shots = self.status()[1]
        self.assertEqual((shots["sh010"]["cut"]["audio"],
                          shots["sh010"]["cut"]["audio_file"],
                          shots["sh010"]["cut"]["audio_why"]), (None, None, None))
        self.put_cut([{"shot": "sh010", "audio": {"source": "take", "shot": "sh020",
                                                  "take": 1}},
                      {"shot": "sh030", "audio": {"source": "file", "path": rel}},
                      {"shot": "sh040", "audio": {"source": "none"}}])
        shots = self.status()[1]
        self.assertEqual(shots["sh010"]["cut"]["audio_file"],
                         "renders_proxy/sh020/sh020_t01_h3.wav")
        self.assertEqual(shots["sh010"]["cut"]["audio_why"], "sh020 t01")
        self.assertEqual(shots["sh010"]["cut"]["audio"],
                         {"source": "take", "shot": "sh020", "take": 1, "pass": "proxy"})
        self.assertEqual((shots["sh030"]["cut"]["audio_file"],
                          shots["sh030"]["cut"]["audio_why"]), (rel, "line.wav"))
        self.assertEqual((shots["sh040"]["cut"]["audio_file"],
                          shots["sh040"]["cut"]["audio_why"]), (None, "silent"))
        # the file it plays can go away; the badge says so
        os.remove(os.path.join(self.ep, *rel.split("/")))
        shots = self.status()[1]
        self.assertEqual((shots["sh030"]["cut"]["audio_file"],
                          shots["sh030"]["cut"]["audio_why"]),
                         (None, "line.wav (missing)"))
        # the peaks route can draw the source the status names
        pk = self.ok(A.get_peaks(self.ctx, {"ep": self.ep, "bins": "1",
                                            "path": shots["sh010"]["cut"]["audio_file"]}))
        self.assertEqual(pk["peaks"], [PK._scale(4000)])


class ResetCopyTest(Base):
    def setUp(self):
        super().setUp()
        self.sounding_take("sh020")
        self.spec = {"source": "take", "shot": "sh020", "take": 1, "pass": "proxy"}

    def test_reset(self):
        self.put_cut([{"shot": "sh010", "audio": dict(self.spec), "trim_in": 1},
                      {"shot": "sh030", "audio": {"source": "none"}, "locked": True}])
        self.ok(A.post_cut_reset(self.ctx, {"ep": self.ep, "pass": "proxy",
                                            "what": "trims"}))
        self.assertEqual(self.proxy()[0], {"shot": "sh010", "audio": self.spec})
        self.ok(A.post_cut_reset(self.ctx, {"ep": self.ep, "pass": "proxy",
                                            "what": "audio"}))
        got = {e["shot"]: e for e in self.proxy()}
        self.assertNotIn("audio", got["sh010"])
        # a locked entry keeps its audio, as it keeps its trims
        self.assertEqual(got["sh030"]["audio"], {"source": "none"})
        self.err(A.post_cut_reset(self.ctx, {"ep": self.ep, "pass": "proxy",
                                             "what": "sound"}), 400)

    def test_reset_all_clears_audio(self):
        self.put_cut([{"shot": "sh010", "audio": dict(self.spec)}])
        self.ok(A.post_cut_reset(self.ctx, {"ep": self.ep, "pass": "proxy", "what": "all"}))
        self.assertEqual(self.proxy(), [{"shot": s} for s in self.order])

    def test_copy(self):
        self.put_cut([{"shot": "sh010", "audio": dict(self.spec)},
                      {"shot": "sh030", "locked": True}], pass_="proxy")
        # audio only travels under "all" (and the added "audio")
        for what in ("order", "trims"):
            self.ok(A.post_cut_copy(self.ctx, {"ep": self.ep, "from": "proxy",
                                               "to": "final", "what": what}))
            self.assertNotIn("audio", T.load_cut(self.ep)["final"][0])
        self.ok(A.post_cut_copy(self.ctx, {"ep": self.ep, "from": "proxy", "to": "final",
                                           "what": "all"}))
        final = {e["shot"]: e for e in T.load_cut(self.ep)["final"]}
        # copied as it stands: the take source keeps the pass it names
        self.assertEqual(final["sh010"]["audio"], self.spec)
        # a locked entry in the target keeps its own audio (here: none at all)
        E.set_cut_entry(self.ep, "proxy", "sh040", audio={"source": "none"})
        E.set_cut_entry(self.ep, "final", "sh040", locked=True)
        E.copy_cut(self.ep, "proxy", "final", "audio")
        final = {e["shot"]: e for e in T.load_cut(self.ep)["final"]}
        self.assertNotIn("audio", final["sh040"])


class AudioImportTest(Base):
    """POST /h3pipe/audio/import: a media file into <ep>/audio/, whose `path`
    goes straight into a cut entry's audio source."""

    def outside(self, name="line.wav", amp=6000):
        p = os.path.join(self.tmp, "elsewhere", name)
        steps_wav(p, [amp])
        return p

    def imp(self, status=200, **body):
        res = A.post_audio_import(self.ctx, dict({"ep": self.ep}, **body))
        return self.ok(res) if status == 200 else self.err(res, status)

    def test_source_path(self):
        got = self.imp(source_path=self.outside())
        self.assertEqual(got, {"path": "audio/line.wav", "copied": True})
        self.assertTrue(os.path.isfile(os.path.join(self.ep, "audio", "line.wav")))
        # the same bytes again are reused, not copied twice
        self.assertEqual(self.imp(source_path=self.outside()),
                         {"path": "audio/line.wav", "copied": False})
        # a clash with other bytes gets -2
        self.assertEqual(self.imp(source_path=self.outside(amp=9000))["path"],
                         "audio/line-2.wav")
        # the name is sanitised, and the extension must be an audio one
        self.assertEqual(self.imp(source_path=self.outside("a b#c.wav"))["path"],
                         "audio/a b_c.wav")
        self.imp(400, source_path=self.outside("notes.txt"))
        self.imp(404, source_path=os.path.join(self.tmp, "nope.wav"))
        self.imp(400)
        self.imp(400, source_path="   ")
        self.imp(403, ep=os.path.join(self.tmp, "Elsewhere"), source_path=self.outside())

    def test_upload(self):
        src = self.outside("take_one.WAV")
        up = A.Upload(src, "take one.WAV", os.path.getsize(src))
        self.assertEqual(self.imp(file=up), {"path": "audio/take one.wav",
                                             "copied": True})
        # the upload's own name is sanitised, whatever the browser sent
        self.assertEqual(self.imp(file=A.Upload(src, "../we:ird*.wav", 8))["path"],
                         "audio/we_ird_.wav")
        self.imp(400, file=A.Upload(src, "notes.txt", 8))
        self.imp(413, file=A.Upload(src, "big.wav", A.MAX_UPLOAD + 1))
        self.imp(400, file="not-an-upload")

    def test_the_path_is_ready_for_a_cut_entry(self):
        rel = self.imp(source_path=self.outside())["path"]
        cut = self.put_cut([{"shot": "sh010",
                             "audio": {"source": "file", "path": rel}}])
        self.assertEqual(cut["proxy"][0]["audio"], {"source": "file", "path": rel})
        # it is not the episode's recording, and nothing was rebuilt
        data = self.status()[0]
        self.assertNotEqual((data["track"] or {}).get("path"), rel)
        self.assertEqual(self.events_of("h3pipe.episode"), [{"ep": self.ep}])

    def test_a_file_already_inside_the_episode_stays_put(self):
        steps_wav(os.path.join(self.ep, "audio", "here.wav"), [1000])
        got = self.imp(source_path=os.path.join(self.ep, "audio", "here.wav"))
        self.assertEqual(got, {"path": "audio/here.wav", "copied": False})

    def test_it_is_a_route(self):
        self.assertIn(("POST", "/h3pipe/audio/import", A.post_audio_import, "form"),
                      A.ROUTES)


class CliTest(Base):
    def cut(self, *argv, code=0):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            got = E.cmd_cut(self.ep, ["--proxy", *argv])
        self.assertEqual(got, code, buf.getvalue())
        return buf.getvalue()

    def test_audio_flags(self):
        self.sounding_take("sh020")
        rel = self.audio_file()
        out = self.cut("--audio", "sh010", "take", "sh020:1", "--from", "0.5",
                       "--at", "0.25", "--gain", "1.5")
        self.assertIn("audio: sh020 t01", out)
        self.assertEqual(self.proxy()[0]["audio"],
                         {"source": "take", "shot": "sh020", "take": 1, "pass": "proxy",
                          "start": 0.5, "offset": 0.25, "gain": 1.5})
        self.cut("--audio", "sh010", "take", "sh020:t1:final", code=1)   # no final take
        self.cut("--audio", "sh010", "file", rel)
        self.assertEqual(self.proxy()[0]["audio"], {"source": "file", "path": rel})
        self.cut("--audio", "sh010", "none")
        self.assertEqual(self.proxy()[0]["audio"], {"source": "none"})
        self.cut("--audio", "sh010", "own")
        self.assertNotIn("audio", self.proxy()[0])
        self.assertIn("no audio file", self.cut("--audio", "sh010", "file", "nope.wav",
                                                code=1))
        with contextlib.redirect_stderr(io.StringIO()):
            for bad in (["sh010"], ["sh010", "voice", "x"], ["sh010", "take"],
                        ["sh010", "take", "sh020"], ["sh010", "take", "sh020:x"],
                        ["sh010", "take", "sh020:1:draft"], ["sh010", "none", "x"]):
                with self.subTest(bad=bad), self.assertRaises(SystemExit):
                    self.cut("--audio", *bad)
            with self.assertRaises(SystemExit):                # own takes no knobs
                self.cut("--audio", "sh010", "own", "--gain", "2")

    def test_through_h3(self):
        self.sounding_take("sh020")
        r = subprocess.run([sys.executable, os.path.join(ROOT, "h3.py"), "cut", self.ep,
                            "--proxy", "--audio", "sh010", "take", "sh020:1"],
                           capture_output=True, text=True, timeout=180,
                           env=dict(os.environ, PYTHONIOENCODING="utf-8"))
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("audio: sh020 t01", r.stdout)
        self.assertEqual(self.proxy()[0]["audio"],
                         {"source": "take", "shot": "sh020", "take": 1, "pass": "proxy"})


# ---------------------------------------------------------------------------
# h3assemble: the sound really under the clip
# ---------------------------------------------------------------------------

def ff(*args: str) -> subprocess.CompletedProcess:
    r = subprocess.run(["ffmpeg", "-y", "-v", "error", *args], capture_output=True,
                       text=True, timeout=180)
    if r.returncode != 0:
        raise RuntimeError(r.stderr)
    return r


def make_clip(path: str, frames: int, wav: str | None = None, fps: int = FPS,
              size: str = "64x64") -> None:
    """A testsrc clip of exactly `frames` frames, with `wav` as its sound (a
    mute clip without one: a `dub` shot's mp4 looks like that)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    args = ["-f", "lavfi", "-i", f"testsrc=size={size}:rate={fps}"]
    args += ["-i", wav, "-c:a", "aac"] if wav else ["-an"]
    ff(*args, "-frames:v", str(frames), "-c:v", "libx264", "-preset", "ultrafast",
       "-pix_fmt", "yuv420p", path)


def tone_wav(path: str, seconds: float, amp: int = 12000, rate: int = 44100,
             silent_first: float = 0.0) -> None:
    """A square wave of `amp`, with `silent_first` seconds of silence first."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    n0, n = int(silent_first * rate), int(seconds * rate)
    frames = bytearray()
    for i in range(n0 + n):
        v = 0 if i < n0 else (amp if (i // 20) % 2 else -amp)
        frames += int(v).to_bytes(2, "little", signed=True)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))


def probe_frames(path: str) -> int:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                        "-count_frames", "-show_entries", "stream=nb_read_frames",
                        "-of", "csv=p=0", path], capture_output=True, text=True,
                       timeout=180)
    return int(r.stdout.strip())


def probe_audio(path: str) -> list[str]:
    """"<rate>,<channels>" of every audio stream in the file."""
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a",
                        "-show_entries", "stream=sample_rate,channels",
                        "-of", "csv=p=0", path], capture_output=True, text=True,
                       timeout=180)
    return [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]


def probe_duration(path: str) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", path], capture_output=True, text=True,
                       timeout=180)
    return float(r.stdout.strip())


@unittest.skipUnless(HAVE_FF, "ffmpeg/ffprobe not on PATH")
class AssembleAudioTest(unittest.TestCase):
    """Two one-second clips: sh010 mute, sh020 a loud tone. Each test changes
    where sh010's (or sh020's) sound comes from and reads the peaks back out
    of the assembled mp4."""

    @classmethod
    def setUpClass(cls):
        cls._cache = tempfile.TemporaryDirectory()
        tone = os.path.join(cls._cache.name, "tone.wav")
        tone_wav(tone, 1.0, amp=20000)
        cls.mute = os.path.join(cls._cache.name, "mute.mp4")
        cls.loud = os.path.join(cls._cache.name, "loud.mp4")
        cls.loud16 = os.path.join(cls._cache.name, "loud16.mp4")
        make_clip(cls.mute, 24)
        make_clip(cls.loud, 24, wav=tone)
        make_clip(cls.loud16, 16, wav=tone, fps=16)        # one second at 16 fps

    @classmethod
    def tearDownClass(cls):
        cls._cache.cleanup()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        T.write_json(os.path.join(self.root, "shotlist", "shotlist.json"),
                     {"episode": "ep01", "defaults": {"width": 64, "height": 64},
                      "shots": [{"id": "sh010", "length": 24, "audio_policy": "keep"},
                                {"id": "sh020", "length": 24, "audio_policy": "keep"}]})
        self.add_take("sh010", self.mute)
        self.add_take("sh020", self.loud)

    def tearDown(self):
        self._tmp.cleanup()

    # -- fixtures ----------------------------------------------------------

    def add_take(self, shot: str, clip: str, pass_: str = "final", **sc) -> int:
        t = T.reserve_take(self.root, pass_, shot, {"status": "queued"})
        shutil.copy(clip, t.paths.mp4)
        T.update_sidecar(t.paths.sidecar, status="ok",
                         frames=probe_frames(clip), **sc)
        return t.take

    def cut(self, entries: list[dict], pass_: str = "final") -> None:
        T.save_cut(self.root, {"episode": "ep01", pass_: entries,
                               "proxy" if pass_ == "final" else "final": []})

    def assemble(self, *args: str, ok: bool = True) -> subprocess.CompletedProcess:
        r = subprocess.run([sys.executable, ASSEMBLE, "-o", self.root, *args],
                           capture_output=True, text=True, encoding="utf-8", timeout=600)
        if ok:
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r

    @property
    def out(self) -> str:
        return os.path.join(self.root, "renders", "ep01.mp4")

    # -- reading the result ------------------------------------------------

    def peak(self, a: float, b: float) -> int:
        """The loudest sample of the assembled cut between a and b seconds."""
        got = PK.peaks(self.root, self.out, f"out{os.path.getmtime(self.out)}.mp4",
                       bins=1, start=a, end=b)
        return got["peaks"][0] if got["peaks"] else 0

    def assertLoud(self, a, b, least=80):
        self.assertGreaterEqual(self.peak(a, b), least, f"{a}..{b} should have sound")

    def assertQuiet(self, a, b, most=20):
        self.assertLessEqual(self.peak(a, b), most, f"{a}..{b} should be silent")

    def assertWhole(self, frames: int = 48):
        self.assertEqual(probe_frames(self.out), frames)
        self.assertAlmostEqual(probe_duration(self.out), frames / FPS, delta=0.05)

    # -- the tests ---------------------------------------------------------

    def test_baseline(self):
        self.cut([{"shot": "sh010"}, {"shot": "sh020"}])
        self.assemble()
        self.assertWhole()
        self.assertQuiet(0.1, 0.9)                          # the mute clip
        self.assertLoud(1.1, 1.9)                           # the loud one

    def test_audio_from_another_shots_take(self):
        self.cut([{"shot": "sh010", "audio": {"source": "take", "shot": "sh020",
                                              "take": 1, "pass": "final"}},
                  {"shot": "sh020"}])
        out = self.assemble().stdout
        self.assertIn("sh010 <- sh020 t01", out)
        self.assertWhole()
        self.assertLoud(0.1, 0.9)                           # sh020's sound, under sh010
        self.assertLoud(1.1, 1.9)
        # sh020 is still stream-copied (nothing forces a re-encode), so the
        # track made up for sh010 has to match its sample rate and layout or
        # the concat demuxer would not have copied them into one file
        self.assertNotIn("trimming", out)
        self.assertEqual(probe_audio(self.out), probe_audio(self.loud))

    def test_audio_from_a_file(self):
        tone_wav(os.path.join(self.root, "audio", "line.wav"), 1.0)
        self.cut([{"shot": "sh010", "audio": {"source": "file",
                                              "path": "audio/line.wav"}},
                  {"shot": "sh020"}])
        out = self.assemble().stdout
        self.assertIn("sh010 <- line.wav", out)
        self.assertWhole()
        self.assertLoud(0.1, 0.9)
        # the _shots.txt says where it came from
        with open(os.path.join(self.root, "renders", "ep01_shots.txt"),
                  encoding="utf-8") as fh:
            self.assertIn("audio line.wav", fh.read())

    def test_silence(self):
        self.cut([{"shot": "sh010"}, {"shot": "sh020", "audio": {"source": "none"}}])
        self.assemble()
        self.assertWhole()
        self.assertQuiet(0.1, 0.9)
        self.assertQuiet(1.1, 1.9)                          # silenced

    def test_start_offset_and_gain(self):
        # "late": silent for half a second, then a tone; "line": a plain tone
        tone_wav(os.path.join(self.root, "audio", "late.wav"), 1.0, silent_first=0.5)
        tone_wav(os.path.join(self.root, "audio", "line.wav"), 3.0)
        self.cut([{"shot": "sh010", "audio": {"source": "file", "path": "audio/late.wav",
                                              "start": 0.5}},
                  {"shot": "sh020", "audio": {"source": "file", "path": "audio/line.wav",
                                              "offset": 0.5}}])
        self.assemble()
        self.assertWhole()
        self.assertLoud(0.05, 0.95)                         # start skipped the silence
        self.assertQuiet(1.05, 1.4)                         # offset pushed it later
        self.assertLoud(1.6, 1.95)
        loud_full = self.peak(0.05, 0.95)
        self.cut([{"shot": "sh010", "audio": {"source": "file", "path": "audio/late.wav",
                                              "start": 0.5, "gain": 0.25}},
                  {"shot": "sh020"}])
        self.assemble()
        self.assertWhole()
        quarter = self.peak(0.1, 0.9)
        self.assertLess(quarter, loud_full * 0.5)
        self.assertGreater(quarter, 8)
        # a negative offset pulls the sound earlier, eating into the source
        self.cut([{"shot": "sh010", "audio": {"source": "file", "path": "audio/late.wav",
                                              "offset": -0.5}},
                  {"shot": "sh020"}])
        self.assemble()
        self.assertWhole()
        self.assertLoud(0.05, 0.45)

    def test_source_shorter_and_longer_than_the_clip(self):
        tone_wav(os.path.join(self.root, "audio", "short.wav"), 0.25)
        tone_wav(os.path.join(self.root, "audio", "long.wav"), 3.0)
        self.cut([{"shot": "sh010", "audio": {"source": "file", "path": "audio/short.wav"}},
                  {"shot": "sh020", "audio": {"source": "file", "path": "audio/long.wav"}}])
        self.assemble()
        self.assertWhole()                                  # the clips keep their length
        self.assertLoud(0.02, 0.2)
        self.assertQuiet(0.45, 0.95)                        # padded with silence
        self.assertLoud(1.1, 1.9)                           # the long one is cut

    def test_trimmed_clip_keeps_its_length(self):
        tone_wav(os.path.join(self.root, "audio", "line.wav"), 3.0)
        self.cut([{"shot": "sh010", "trim_in": 6, "trim_out": 6,
                   "audio": {"source": "file", "path": "audio/line.wav"}},
                  {"shot": "sh020"}])
        self.assemble()
        self.assertWhole(12 + 24)
        self.assertLoud(0.05, 0.45)

    def test_placeholder_and_other_fps_line_up(self):
        """A clip standing in from the other pass, and one at 16 fps in a 24 fps
        cut: the sound still fills exactly the clip, and only the clip."""
        self.add_take("sh010", self.loud16, pass_="proxy", fps=16)
        tone_wav(os.path.join(self.root, "audio", "line.wav"), 3.0)
        self.cut([{"shot": "sh010", "pass": "proxy", "take": 1,
                   "audio": {"source": "file", "path": "audio/line.wav"}},
                  {"shot": "sh020", "audio": {"source": "none"}}])
        out = self.assemble().stdout
        self.assertIn("16 fps to 24 fps", out)
        self.assertWhole()                                  # 24 + 24 frames
        self.assertLoud(0.05, 0.95)
        self.assertQuiet(1.1, 1.9)                          # the source stops at the cut

    def test_master_overrides_and_says_so(self):
        tone_wav(os.path.join(self.root, "audio", "line.wav"), 3.0)
        tone_wav(os.path.join(self.root, "audio", "master.wav"), 3.0, amp=1500)
        doc = T.read_json(os.path.join(self.root, "shotlist", "shotlist.json"))
        doc["defaults"]["master_track"] = "audio/master.wav"
        T.write_json(os.path.join(self.root, "shotlist", "shotlist.json"), doc)
        self.cut([{"shot": "sh010", "audio": {"source": "file", "path": "audio/line.wav"}},
                  {"shot": "sh020"}])
        out = self.assemble("--audio", "master").stdout
        self.assertIn("--audio master", out)
        self.assertIn("overrides the audio source on sh010", out)
        self.assertWhole()
        # the master track, not the loud source and not the loud take
        self.assertLess(self.peak(0.1, 0.9), 40)
        self.assertGreater(self.peak(0.1, 0.9), 2)
        self.assertLess(self.peak(1.1, 1.9), 40)

    def test_audio_none_flag_ignores_sources(self):
        tone_wav(os.path.join(self.root, "audio", "line.wav"), 3.0)
        self.cut([{"shot": "sh010", "audio": {"source": "file", "path": "audio/line.wav"}},
                  {"shot": "sh020"}])
        self.assemble("--audio", "none")
        self.assertWhole()
        self.assertQuiet(0.1, 0.9)
        self.assertQuiet(1.1, 1.9)

    def test_missing_source_is_silence_not_a_failure(self):
        self.cut([{"shot": "sh010", "audio": {"source": "file", "path": "audio/gone.wav"}},
                  {"shot": "sh020"}])
        out = self.assemble().stdout
        self.assertIn("is not there or has no sound", out)
        self.assertWhole()
        self.assertQuiet(0.1, 0.9)

    def test_check_lists_the_source(self):
        tone_wav(os.path.join(self.root, "audio", "line.wav"), 1.0)
        self.cut([{"shot": "sh010", "audio": {"source": "file", "path": "audio/line.wav"}},
                  {"shot": "sh020", "audio": {"source": "none"}}])
        out = self.assemble("--check").stdout
        self.assertIn("audio line.wav", out)
        self.assertIn("audio silent", out)
        self.assertFalse(os.path.exists(self.out))


if __name__ == "__main__":
    unittest.main()
