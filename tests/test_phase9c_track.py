"""
Phase 9c A (docs/API.md "A. Attaching and aligning a recording"): the
readiness check (GET /h3pipe/align/ready), attaching a recording (POST
/h3pipe/track, by path and as an upload, and clearing it), running h3align
(POST /h3pipe/align: progress events, dry runs, a missing dependency), and
h3align's own writing through h3source (_history instead of .bak).

No Whisper is installed on this machine, so nothing here transcribes:
h3track.probe is faked for the readiness answers, the align route runs a stub
script that speaks h3align's --progress / --json protocol, and the real
h3align is exercised with a hand-made <recording>.words.json cache, which is
the path it takes whenever a transcript is already there (it needs ffmpeg and
numpy; those tests skip without them).
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import struct
import sys
import unittest
import wave
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "comfy_nodes"))
import h3align  # noqa: E402
import h3build  # noqa: E402
import h3edit as E  # noqa: E402
import h3pipe_api as A  # noqa: E402
import h3source as H  # noqa: E402
import h3takes as T  # noqa: E402
import h3track as K  # noqa: E402
import test_api  # noqa: E402
from test_api import ApiTest  # noqa: E402

HAVE_FF = bool(shutil.which("ffmpeg"))
HAVE_NUMPY = importlib.util.find_spec("numpy") is not None


def tearDownModule():
    test_api.tearDownModule()


def write_wav(path: str, seconds: float, rate: int = 16000) -> None:
    """A quiet PCM wav with a little buzz, long enough to hold the words."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    n = int(seconds * rate)
    frames = b"".join(struct.pack("<h", 300 if i % 97 < 40 else 0) for i in range(n))
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(frames)


def script_lines(ep: str) -> list[dict]:
    """The script's dialogue lines, in order (as h3align reads them)."""
    cfg = json.load(open(os.path.join(ep, "series.json"), encoding="utf-8"))
    subjects = {k for k in cfg.get("subjects", {}) if not k.startswith("_")}
    chars = {k for k, v in cfg["subjects"].items()
             if not k.startswith("_") and isinstance(v, dict)
             and v.get("kind", "character") == "character"}
    text = open(E.episode_script(ep), encoding="utf-8").read()
    epi = h3build.parse_script(text, subjects, chars)
    return [{"shot": sh["id"], "text": d["line"]}
            for seq in epi["sequences"] for sh in seq["shots"] for d in sh["dialogue"]]


def fake_words(lines: list[dict], gap: float = 1.2, rate: float = 3.0,
               start: float = 1.5) -> tuple[list[dict], float]:
    """A transcript that says exactly what the script says, line by line:
    (the words with timings, how long the recording must be). This is the
    cache h3align writes after a real transcription."""
    words, t = [], start
    for ln in lines:
        for tok in h3align.tokens(ln["text"]):
            words.append({"word": tok, "start": round(t, 3), "end": round(t + 1 / rate, 3)})
            t += 1 / rate
        t += gap
    return words, t + 1.0


class ReadyTest(unittest.TestCase):
    """GET /h3pipe/align/ready: what h3align needs, in the Python that runs it."""

    def ready(self, numpy="1.26", faster=None, openai=None, ffmpeg="C:/ff/ffmpeg.exe"):
        pkgs = {"numpy": numpy, "faster-whisper": faster, "openai-whisper": openai}
        with mock.patch.object(K, "probe", lambda py: dict(pkgs)), \
                mock.patch.object(K.shutil, "which", lambda n: ffmpeg):
            return K.align_ready("C:/py/python.exe")

    def test_everything_there(self):
        r = self.ready(faster="1.0.3")
        self.assertTrue(r["ready"])
        self.assertEqual(r["missing"], [])
        self.assertEqual(r["install"], "")
        self.assertEqual(r["ffmpeg"], "C:/ff/ffmpeg.exe")
        self.assertEqual(r["python"], "C:/py/python.exe")
        self.assertEqual(r["models"]["default"], "medium.en")
        self.assertIn("medium.en", r["models"]["choices"])
        self.assertEqual(r["packages"]["faster-whisper"], "1.0.3")

    def test_openai_whisper_is_enough(self):
        self.assertTrue(self.ready(openai="20231117")["ready"])

    def test_missing_names_the_pip_line_and_the_interpreter(self):
        r = self.ready(numpy=None, ffmpeg=None)
        self.assertFalse(r["ready"])
        self.assertEqual(r["missing"], ["ffmpeg", "numpy", "faster-whisper"])
        self.assertEqual(r["install"],
                         '"C:/py/python.exe" -m pip install numpy faster-whisper')
        self.assertIsNone(r["ffmpeg"])
        self.assertIn("ffmpeg", r["ffmpeg_hint"])

    def test_ffmpeg_alone_has_no_pip_line(self):
        r = self.ready(faster="1.0", ffmpeg=None)
        self.assertEqual((r["ready"], r["missing"], r["install"]), (False, ["ffmpeg"], ""))

    def test_probe_asks_the_real_interpreter(self):
        K._probe_cache.clear()
        got = K.probe(sys.executable)
        self.assertEqual(sorted(got), ["faster-whisper", "numpy", "openai-whisper"])
        for dist, mod in K.PACKAGES:
            self.assertEqual(got[dist] is not None,
                             importlib.util.find_spec(mod) is not None, dist)
        with mock.patch.object(K.subprocess, "run",
                               side_effect=AssertionError("cached")):
            self.assertEqual(K.probe(sys.executable), got)      # inside PROBE_TTL

    def test_probe_falls_back_to_this_interpreter(self):
        K._probe_cache.clear()
        with mock.patch.object(K.subprocess, "run", side_effect=OSError("no such exe")):
            got = K.probe("C:/nope/python.exe")
        self.assertEqual(got["numpy"] is not None, HAVE_NUMPY)

    def test_route(self):
        with mock.patch.object(K, "align_ready", lambda: {"ready": True, "missing": []}):
            self.assertEqual(A.get_align_ready(None, {}), (200, {"ready": True,
                                                                 "missing": []}))


class TrackTest(ApiTest):
    """POST /h3pipe/track."""

    def setUp(self):
        super().setUp()
        self.src = os.path.join(self.tmp, "recordings", "Take 1 (final).WAV")
        write_wav(self.src, 0.5)

    def series(self) -> dict:
        return json.load(open(os.path.join(self.ep, "series.json"), encoding="utf-8"))

    def post(self, body, status=200):
        res = A.post_track(self.ctx, dict({"ep": self.ep}, **body))
        return self.ok(res) if status == 200 else self.err(res, status)

    def history(self) -> list[str]:
        return H.history(self.ep, "series.json")

    def test_attach_by_path(self):
        data = self.post({"source_path": self.src})
        self.assertEqual(data["path"], "audio/Take 1 (final).wav")
        self.assertTrue(data["copied"])
        self.assertTrue(os.path.isfile(os.path.join(self.ep, "audio", "Take 1 (final).wav")))
        self.assertEqual(self.series()["audio"], {"mode": "source_track",
                                                  "track": "audio/Take 1 (final).wav"})
        self.assertEqual(data["track"]["path"], "audio/Take 1 (final).wav")
        self.assertEqual((data["track"]["exists"], data["track"]["words"]), (True, False))
        self.assertEqual(data["track"]["aligned"], 4)      # the fixture's `audio:` windows
        self.assertEqual(data["hash"],
                         self.ok(A.get_source(self.ctx, {"ep": self.ep, "file": "series",
                                                         "hash_only": "1"}))["hash"])
        # the build runs; its final pass now wants the windows h3align writes
        # (source_track turns the dialogue shots into dubs), which is the next step
        self.assertTrue(data["build"]["passes"]["proxy"]["ok"], data["build"])
        self.assertIn("`audio: in-out` window", data["build"]["passes"]["final"]["error"])
        self.assertEqual(len(self.history()), 1)           # the old series config is kept
        self.assertEqual(self.events_of("h3pipe.episode")[-1], {"ep": self.ep})

    def test_a_file_already_in_the_episode_is_used_in_place(self):
        inside = os.path.join(self.ep, "audio", "mine.wav")
        write_wav(inside, 0.2)
        data = self.post({"source_path": inside})
        self.assertEqual((data["path"], data["copied"]), ("audio/mine.wav", False))
        self.assertEqual([f for f in os.listdir(os.path.join(self.ep, "audio"))
                          if f.startswith("mine")], ["mine.wav"])

    def test_the_same_file_twice_is_not_copied_twice(self):
        self.post({"source_path": self.src})
        again = self.post({"source_path": self.src})
        self.assertEqual((again["path"], again["copied"]),
                         ("audio/Take 1 (final).wav", False))
        other = os.path.join(self.tmp, "elsewhere", "Take 1 (final).WAV")
        write_wav(other, 0.75)                             # same name, other sound
        third = self.post({"source_path": other})
        self.assertEqual((third["path"], third["copied"]),
                         ("audio/Take 1 (final)-2.wav", True))
        self.assertEqual(self.series()["audio"]["track"], "audio/Take 1 (final)-2.wav")

    def test_upload(self):
        up = A.Upload(self.src, "dialogue mix.wav", os.path.getsize(self.src))
        data = self.post({"file": up})
        self.assertEqual((data["path"], data["copied"]), ("audio/dialogue mix.wav", True))
        self.assertTrue(os.path.isfile(self.src))          # the adapter owns the temp file
        big = A.Upload(self.src, "x.wav", A.MAX_UPLOAD + 1)
        self.post({"file": big}, 413)
        self.post({"file": "x.wav"}, 400)

    def test_refusals(self):
        self.post({}, 400)
        self.post({"source_path": 3}, 400)
        self.post({"source_path": os.path.join(self.tmp, "nope.wav")}, 404)
        notes = os.path.join(self.tmp, "notes.txt")
        open(notes, "w").close()
        self.assertIn("not a recording", self.post({"source_path": notes}, 400))
        self.assertEqual(self.series()["audio"]["track"], "audio/ks01_mix.wav")
        self.assertEqual(self.history(), [])               # nothing written

    def test_clear_puts_the_mode_back(self):
        self.assertEqual(self.series()["audio"]["mode"], "clone")
        self.post({"source_path": self.src})
        self.assertEqual(T.load_overrides(self.ep)["audio"], {"mode_before_track": "clone"})
        data = self.post({"track": None})
        self.assertIsNone(data["track"])
        self.assertIsNone(data["path"])
        self.assertEqual(self.series()["audio"], {"mode": "clone"})
        self.assertNotIn("audio", T.load_overrides(self.ep))
        self.assertTrue(os.path.isfile(os.path.join(self.ep, "audio", "Take 1 (final).wav")))

    def test_clear_without_a_memory_generates(self):
        self.post({"track": None})
        self.assertEqual(self.series()["audio"], {"mode": "generate"})
        # nothing to clear a second time: the write changes nothing
        before = self.ok(A.get_source(self.ctx, {"ep": self.ep, "file": "series",
                                                 "hash_only": "1"}))["hash"]
        self.assertEqual(self.post({"track": None})["hash"], before)

    def test_a_series_config_that_is_not_json_is_refused(self):
        with open(os.path.join(self.ep, "series.json"), "w", encoding="utf-8") as fh:
            fh.write("{oops")
        self.assertIn("not valid JSON", self.post({"source_path": self.src}, 400))

    def test_the_rewrite_says_when_it_reformats(self):
        p = os.path.join(self.ep, "series.json")
        raw = json.load(open(p, encoding="utf-8"))
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(raw, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        self.assertFalse(self.post({"source_path": self.src})["reformatted"])


def stub_align(path: str, body: str) -> None:
    """A fake h3align that speaks the --progress / --json protocol."""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("import json, sys\n"
                 "a = sys.argv[1:]\n"
                 "out = a[a.index('--json') + 1] if '--json' in a else None\n"
                 "prog = '--progress' in a\n"
                 "dry = '--dry-run' in a\n"
                 + body)


OK_BODY = """
def ev(stage, pct, text):
    if prog:
        print('##h3align ' + json.dumps({'stage': stage, 'pct': pct, 'text': text}),
              flush=True)

ev('transcribe', 10, 'transcribing')
print('  some chatter')
ev('match', 65, 'matching')
ev('write', 100, 'written')
res = {'ok': True, 'dry_run': dry, 'recording': 'audio/x.wav', 'duration': 9.0,
       'words': 12, 'report': '# Alignment\\n', 'report_path': None if dry else
       'align_report.md',
       'changes': [{'shot': 'sh010', 'audio_in': 0.0, 'audio_out': 2.5, 'note': ''}],
       'notes': ['!  sh010: something'], 'script_hash': None if dry else 'aaa',
       'series_hash': None if dry else 'bbb', 'args': a}
if out:
    json.dump(res, open(out, 'w', encoding='utf-8'))
"""

FAIL_BODY = """
print('  transcribing')
print('  !! recording not found: nope.wav')
sys.exit(1)
"""

SLOW_BODY = """
import time
ev = None
print('  starting', flush=True)
time.sleep(30)
"""


class AlignRouteTest(ApiTest):
    """POST /h3pipe/align, against a stub h3align (nothing is transcribed)."""

    def setUp(self):
        super().setUp()
        self.stub = os.path.join(self.tmp, "stub_align.py")
        stub_align(self.stub, OK_BODY)
        self.patches = [mock.patch.object(K, "ALIGN_SCRIPT", self.stub),
                        mock.patch.object(K, "align_ready", self.fake_ready)]
        for p in self.patches:
            p.start()
        self.missing = []

    def tearDown(self):
        for p in self.patches:
            p.stop()
        super().tearDown()

    def fake_ready(self, python=None):
        return {"ready": not self.missing, "missing": list(self.missing),
                "install": "pip install faster-whisper", "python": "C:/py/python.exe",
                "ffmpeg": "ffmpeg"}

    def align(self, body=None, status=200):
        res = A.post_align(self.ctx, dict({"ep": self.ep}, **(body or {})))
        return self.ok(res) if status == 200 else self.err(res, status)

    def test_a_run(self):
        data = self.align()
        self.assertTrue(data["ok"])
        self.assertFalse(data["dry_run"])
        self.assertEqual(data["changes"], [{"shot": "sh010", "audio_in": 0.0,
                                            "audio_out": 2.5, "note": ""}])
        self.assertEqual((data["script_hash"], data["series_hash"]), ("aaa", "bbb"))
        self.assertEqual(data["report"], "# Alignment\n")
        self.assertEqual(data["report_path"], "align_report.md")
        self.assertIn("some chatter", data["log"])
        self.assertNotIn("##h3align", data["log"])
        self.assertTrue(data["build"]["ok"], data["build"])
        self.assertEqual(data["track"]["path"], "audio/ks01_mix.wav")
        self.assertEqual([(e["stage"], e["pct"]) for e in self.events_of("h3pipe.align")],
                         [("transcribe", 10), ("match", 65), ("write", 100)])
        self.assertEqual(self.events_of("h3pipe.align")[0]["ep"], self.ep)
        self.assertEqual(self.events_of("h3pipe.episode")[-1], {"ep": self.ep})

    def test_dry_run_changes_nothing(self):
        before = E.episode_status(self.ep, "proxy")
        data = self.align({"dry_run": True})
        self.assertTrue(data["dry_run"])
        self.assertIsNone(data["build"])
        self.assertIsNone(data["script_hash"])
        self.assertEqual(self.events_of("h3pipe.episode"), [])
        self.assertEqual(E.episode_status(self.ep, "proxy"), before)

    def test_missing_dependency_is_409(self):
        self.missing = ["faster-whisper"]
        code, data = A.post_align(self.ctx, {"ep": self.ep})
        self.assertEqual(code, 409)
        self.assertEqual(data["missing"], ["faster-whisper"])
        self.assertIn("faster-whisper", data["error"])
        self.assertFalse(data["words"])
        self.assertEqual(self.events_of("h3pipe.align"), [])

    def test_a_cached_transcript_needs_no_whisper(self):
        """h3align reads <recording>.words.json when it is newer than the
        recording, so re-aligning doesn't need Whisper at all."""
        rec = os.path.join(self.ep, "audio", "ks01_mix.wav")
        write_wav(rec, 0.2)
        self.missing = ["faster-whisper"]
        self.align(status=409)
        with open(E.words_cache(rec), "w", encoding="utf-8") as fh:
            json.dump([{"word": "hi", "start": 0.0, "end": 0.2}], fh)
        self.assertTrue(self.align()["ok"])                # it runs now
        self.missing = ["ffmpeg", "numpy", "faster-whisper"]
        self.assertEqual(self.err(A.post_align(self.ctx, {"ep": self.ep}), 409),
                         A.post_align(self.ctx, {"ep": self.ep})[1]["error"])
        self.assertEqual(A.post_align(self.ctx, {"ep": self.ep})[1]["missing"],
                         ["ffmpeg", "numpy"])             # those are still needed

    def test_a_failure_shows_the_reason(self):
        stub_align(self.stub, FAIL_BODY)
        msg = self.align(status=500)
        self.assertIn("recording not found", msg)

    def test_a_run_that_hangs_is_stopped(self):
        stub_align(self.stub, SLOW_BODY)
        with mock.patch.object(K, "ALIGN_TIMEOUT", 1.0):
            msg = self.align(status=500)
        self.assertIn("longer than 1s", msg)

    def test_bad_input(self):
        self.align({"track": "../../outside.wav"}, 400)
        self.align({"track": "audio/nope.wav"}, 404)
        self.align({"model": 7}, 400)
        self.align({"snap": "yes"}, 400)
        self.align({"dry_run": "1"}, 400)
        self.assertEqual(self.events_of("h3pipe.align"), [])


class AlignArgsTest(ApiTest):
    """What the route passes h3align (h3track.align builds the command line)."""

    def test_command_line(self):
        seen = {}

        def fake_stream(cmd, cwd, timeout, on_progress):
            seen["cmd"], seen["cwd"], seen["timeout"] = cmd, cwd, timeout
            out = cmd[cmd.index("--json") + 1]
            on_progress({"stage": "write", "pct": 100, "text": "ok"})
            with open(out, "w", encoding="utf-8") as fh:
                json.dump({"ok": True, "changes": []}, fh)
            return 0, "done"

        rec = os.path.join(self.ep, "audio", "ks01_mix.wav")
        write_wav(rec, 0.2)
        with mock.patch.object(K, "_stream", fake_stream), \
                mock.patch.object(K, "align_ready", lambda py=None: {"ready": True,
                                                                     "missing": []}):
            self.ok(A.post_align(self.ctx, {"ep": self.ep, "track": "audio/ks01_mix.wav",
                                            "model": "tiny.en", "snap": False,
                                            "dry_run": True}))
        cmd = seen["cmd"]
        self.assertEqual(cmd[0], sys.executable)
        self.assertEqual(cmd[3], K.ALIGN_SCRIPT)
        self.assertEqual(cmd[4:6], [self.ep, rec])
        self.assertIn("--progress", cmd)
        self.assertIn("--dry-run", cmd)
        self.assertIn("--no-snap", cmd)
        self.assertEqual(cmd[cmd.index("--model") + 1], "tiny.en")
        self.assertEqual(seen["cwd"], self.ep)
        self.assertEqual(seen["timeout"], K.ALIGN_TIMEOUT)
        self.assertEqual([e["stage"] for e in self.events_of("h3pipe.align")], ["write"])


@unittest.skipUnless(HAVE_FF and HAVE_NUMPY, "h3align needs ffmpeg and numpy")
class AlignWritesTest(ApiTest):
    """The real h3align, with a hand-made transcript cache instead of Whisper:
    what it writes, and that it goes through h3source (no .bak)."""

    def setUp(self):
        super().setUp()
        self.rec = os.path.join(self.ep, "audio", "ks01_dialogue.wav")
        words, seconds = fake_words(script_lines(self.ep))
        write_wav(self.rec, seconds)
        with open(E.words_cache(self.rec), "w", encoding="utf-8") as fh:
            json.dump(words, fh)
        self.words = words

    def run_align(self, **kw):
        events = []
        res = K.align(self.ep, track=self.rec, check_deps=False,
                      progress=events.append, **kw)
        return res, events

    def test_report_is_not_mistaken_for_the_script(self):
        """h3align leaves align_report.md in the episode folder. An episode
        whose script isn't <folder>.md then had two .md files, and every later
        build, source read and promote failed with "can't tell which .md"."""
        script = E.episode_script(self.ep)
        renamed = os.path.join(self.ep, "ep99_other_name.md")
        os.replace(script, renamed)
        self.assertEqual(E.episode_script(self.ep), renamed)
        res, _ = self.run_align()
        self.assertTrue(res["ok"])
        self.assertTrue(os.path.isfile(os.path.join(self.ep, "align_report.md")))
        self.assertEqual(E.episode_script(self.ep), renamed)
        self.assertTrue(self.ok(A.get_source(self.ctx, {"ep": self.ep, "file": "script"}))["text"])

    def test_writes_through_h3source(self):
        script = E.episode_script(self.ep)
        before = H.read_source(self.ep, "script")
        res, events = self.run_align()
        self.assertTrue(res["ok"])
        self.assertEqual([(e["stage"], e["pct"]) for e in events],
                         [("transcribe", 10), ("match", 65), ("write", 90), ("write", 100)])
        self.assertIn("cached transcript", events[0]["text"])
        # the script now carries the windows, and the old `dur:` lines are comments
        text = open(script, encoding="utf-8").read()
        self.assertIn("audio: ", text)
        self.assertIn("// dur: 2.5   (h3align)", text)
        # the series config points at the recording
        cfg = json.load(open(os.path.join(self.ep, "series.json"), encoding="utf-8"))
        self.assertEqual(cfg["audio"]["mode"], "source_track")
        self.assertEqual(cfg["audio"]["track"], "audio/ks01_dialogue.wav")
        # one history, no .bak
        self.assertEqual(len(H.history(self.ep, os.path.basename(script))), 1)
        self.assertEqual(len(H.history(self.ep, "series.json")), 1)
        self.assertEqual(open(H.history(self.ep, os.path.basename(script))[0],
                              encoding="utf-8").read(), before.text)
        self.assertEqual([f for f in os.listdir(self.ep) if f.endswith(".bak")], [])
        # the hashes it reports are the files as they are now
        self.assertEqual(res["script_hash"], H.read_source(self.ep, "script").hash)
        self.assertEqual(res["series_hash"], H.read_source(self.ep, "series").hash)
        # the report, and one change per shot
        self.assertTrue(os.path.isfile(os.path.join(self.ep, "align_report.md")))
        self.assertEqual(res["report_path"], "align_report.md")
        self.assertIn("| Shot | Window (s) |", res["report"])
        ids = [c["shot"] for c in res["changes"]]
        self.assertEqual(ids, E.script_order(self.ep, "final"))
        placed = [c for c in res["changes"] if c["audio_in"] is not None]
        self.assertTrue(len(placed) >= 10)
        # the windows run back to back (a shot that keeps its `dur:` breaks the run
        # and says so in its note)
        for a, b in zip(res["changes"], res["changes"][1:]):
            if a["audio_out"] is not None and b["audio_in"] is not None:
                self.assertAlmostEqual(a["audio_out"], b["audio_in"], places=3)
            elif b["audio_in"] is None:
                self.assertTrue(b["note"], b)
        # and the build reads them back as the shots' windows
        self.assertTrue(E.build_episode(self.ep)["ok"])
        status = E.episode_status(self.ep, "proxy")
        self.assertEqual(status["track"]["path"], "audio/ks01_dialogue.wav")
        self.assertTrue(status["track"]["words"])
        self.assertEqual(status["track"]["aligned"], len(placed))
        first = next(s for s in status["shots"] if s["shot"] == placed[0]["shot"])
        self.assertAlmostEqual(first["audio_in"], placed[0]["audio_in"], places=2)

    def test_dry_run_writes_nothing(self):
        script = E.episode_script(self.ep)
        before = (open(script, "rb").read(),
                  open(os.path.join(self.ep, "series.json"), "rb").read())
        res, events = self.run_align(dry_run=True)
        self.assertTrue(res["dry_run"])
        self.assertIsNone(res["script_hash"])
        self.assertIsNone(res["report_path"])
        self.assertIn("| Shot |", res["report"])           # the report still comes back
        self.assertFalse(os.path.isfile(os.path.join(self.ep, "align_report.md")))
        self.assertEqual(H.history(self.ep, os.path.basename(script)), [])
        self.assertEqual(before, (open(script, "rb").read(),
                                  open(os.path.join(self.ep, "series.json"), "rb").read()))
        self.assertEqual(events[-1]["pct"], 100)

    def test_line_endings_and_bom_survive(self):
        script = E.episode_script(self.ep)
        text = open(script, encoding="utf-8").read()
        with open(script, "wb") as fh:
            fh.write(b"\xef\xbb\xbf" + text.replace("\n", "\r\n").encode("utf-8"))
        self.run_align()
        data = open(script, "rb").read()
        self.assertTrue(data.startswith(b"\xef\xbb\xbf"))
        self.assertNotIn(b"\n", data.replace(b"\r\n", b""))
        self.assertIn(b"audio: ", data)

    def test_an_empty_transcript_fails_cleanly(self):
        with open(E.words_cache(self.rec), "w", encoding="utf-8") as fh:
            json.dump([], fh)
        with self.assertRaises(K.TrackError) as cm:
            self.run_align()
        self.assertEqual(cm.exception.status, 500)
        self.assertIn("transcript is empty", str(cm.exception))

    def test_the_route_runs_it_end_to_end(self):
        with mock.patch.object(K, "align_ready", lambda py=None: {"ready": True,
                                                                  "missing": []}):
            data = self.ok(A.post_align(self.ctx, {"ep": self.ep,
                                                   "track": "audio/ks01_dialogue.wav"}))
        self.assertTrue(data["ok"])
        self.assertTrue(data["build"]["ok"], data["build"])
        self.assertTrue(data["track"]["words"])
        self.assertEqual(data["track"]["aligned"],
                         len([c for c in data["changes"] if c["audio_in"] is not None]))
        self.assertTrue([e for e in self.events_of("h3pipe.align")])


if __name__ == "__main__":
    unittest.main(verbosity=2)
