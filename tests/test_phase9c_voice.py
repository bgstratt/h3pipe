"""
Phase 9c-B (docs/API.md "Phase 9c", part B): voice refs, generated.

The `audio` target kind and its readiness, the ltx2_voice target (its graph
against a trimmed /object_info, its length grid, the reference-audio chain),
the brief a voice generate uses, generating and picking a voice candidate
(including writing `voice_sample` into the series config), the no-model path
(`voice-from-take`), the defaults and the CLI flags.

The graph is validated here against tests/fixtures/workflows/object_info_audio.json,
trimmed from the live ComfyUI; it was also queued live (see the "Phase 9c-B as
built" section of docs/API.md).
"""
from __future__ import annotations

import json
import os
import random
import shutil
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "comfy_nodes"))
import h3edit as E  # noqa: E402
import h3jobs as J  # noqa: E402
import h3peaks as PK  # noqa: E402
import h3pipe_api as A  # noqa: E402
import h3refs as R  # noqa: E402
import h3takes as T  # noqa: E402
import targets as TG  # noqa: E402
import test_api  # noqa: E402
from targets.audio import common as AC  # noqa: E402
from test_api import ApiTest  # noqa: E402
from test_phase85 import Episode  # noqa: E402
from test_render import wav_bytes  # noqa: E402

OBJECT_INFO = json.load(open(os.path.join(HERE, "fixtures", "workflows",
                                          "object_info_audio.json"), encoding="utf-8"))
VOICE = "ltx2_voice"
HAVE_FFMPEG = shutil.which("ffmpeg") is not None
needs_ffmpeg = unittest.skipUnless(HAVE_FFMPEG, "ffmpeg not on PATH")


def wav(path: str, seconds: float = 1.0) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(wav_bytes(seconds))
    return path


# ---------------------------------------------------------------------------
# the audio kind and the ltx2_voice target
# ---------------------------------------------------------------------------

class AudioTargetTest(unittest.TestCase):
    def setUp(self):
        self.t = TG.load_target(VOICE, "audio")

    def test_kind_and_defaults(self):
        self.assertIn("audio", TG.KINDS)
        self.assertEqual(TG.DEFAULT_TARGETS["audio"], VOICE)
        self.assertEqual([t.id for t in TG.list_targets("audio")], [VOICE])
        self.assertEqual(TG.audio_target(None).id, VOICE)
        self.assertEqual(TG.audio_target({"refs": {"voice_target": VOICE}}).id, VOICE)
        with self.assertRaises(ValueError):
            TG.refs_block({"refs": {"voice_target": "krea2"}})      # an image target
        with self.assertRaises(ValueError):
            TG.refs_block({"refs": {"target": VOICE}})              # an audio one

    def test_described(self):
        d = self.t.describe()
        self.assertEqual(d["kind"], "audio")
        self.assertTrue(d["default"])
        self.assertEqual(d["capabilities"]["mode"], "t2a")
        self.assertEqual(d["capabilities"]["max_seconds"], 20.0)
        # the whole range is published: a picker must not guess the floor
        caps, rng = d["capabilities"], TG.load_target("ltx2_voice", "audio").seconds_range()
        self.assertEqual((caps["default_seconds"], caps["max_seconds"], caps["min_seconds"]),
                         rng)
        self.assertTrue(d["capabilities"]["negative_prompt"])
        # reference_audio is off: the ID-LoRA weights aren't installed (target.json)
        self.assertFalse(d["capabilities"]["reference_audio"])
        self.assertEqual(d["saver"], "H3SaveRefAudio")
        self.assertTrue(d["workflow"].startswith("h3pipe_"))
        self.assertEqual(set(d["models"]), {"model", "text_encoder", "audio_vae"})
        for param, m in d["models"].items():
            self.assertTrue(m["family"] and m["tier"] and m["folder"], param)
        # every file it names has a download record
        self.assertEqual(set(self.t.named_files()) - set(self.t.downloads), set())
        self.assertEqual(self.t.models["model"]["tier"], "accelerator")
        self.assertIsNotNone(self.t.presets["final"].base)

    def test_length_grid(self):
        default, hi, lo = self.t.seconds_range()
        self.assertEqual((default, hi, lo), (8.0, 20.0, 2.0))
        secs, frames, fps = self.t.snap_seconds(6.0)
        self.assertEqual((frames, fps), (145, 24.0))
        self.assertAlmostEqual(secs, 145 / 24.0)
        self.assertEqual((frames - 1) % 8, 0)               # LTX's 8k+1 grid
        # clamped into the target's range, both ways
        self.assertEqual(self.t.snap_seconds(0.1)[1], self.t.snap_seconds(lo)[1])
        self.assertEqual(self.t.snap_seconds(1000)[1], self.t.snap_seconds(hi)[1])
        self.assertEqual(self.t.snap_seconds(None)[1], self.t.snap_seconds(default)[1])

    def test_graph_passes_object_info(self):
        base = J.load_graph(self.t.binding.workflow)
        self.assertEqual(J.check_graph(base, OBJECT_INFO), [])
        # titled nodes, so the binding's `title` selectors work
        titles = {(v.get("_meta") or {}).get("title") for v in base.values()}
        self.assertIn("Positive prompt", titles)
        self.assertIn("Negative prompt", titles)
        self.assertNotIn(None, titles)

    def test_readiness_on_this_object_info(self):
        rd = E.readiness([self.t], OBJECT_INFO)[VOICE]
        self.assertEqual((rd["status"], rd["missing"], rd["nodes_missing"]),
                         ("ready", [], []))
        self.assertEqual(rd["resolved"]["model"]["how"], "exact")
        # the accelerator missing: the dev model and 30 steps take over
        info = json.loads(json.dumps(OBJECT_INFO))
        files = info["UNETLoader"]["input"]["required"]["unet_name"][0]
        info["UNETLoader"]["input"]["required"]["unet_name"][0] = [
            f for f in files if "2.5" not in f]
        rd = E.readiness([self.t], info)[VOICE]
        self.assertEqual(rd["status"], "not_ready")         # no 2.5 dev installed either
        self.assertEqual([m["param"] for m in rd["missing"]], ["model", "model"])
        # a ComfyUI without the reference-audio nodes is only degraded
        info = json.loads(json.dumps(OBJECT_INFO))
        info.pop("LTXVReferenceAudio")
        rd = E.readiness([self.t], info)[VOICE]
        self.assertEqual((rd["status"], rd["nodes_missing"]),
                         ("degraded", ["LTXVReferenceAudio"]))
        self.assertEqual(rd["features_off"], ["voice cloning (LTXVReferenceAudio)"])


class BriefTest(unittest.TestCase):
    def test_wording(self):
        p = AC.voice_prompt("Ada", "dry, quick and precise",
                            "a tall woman in her thirties. She wears red glasses.",
                            "You know that one whistles.", 8.0)
        self.assertIn("one voice only", p)
        self.assertIn("Ada is a tall woman in her thirties.", p)   # one sentence of the design
        self.assertNotIn("red glasses", p)
        self.assertIn("Voice: dry, quick and precise.", p)
        self.assertIn("About 8 seconds of speech.", p)
        self.assertTrue(p.endswith('Ada says: "You know that one whistles."'))
        # whole seconds, however the grid snapped it
        self.assertIn("About 8 seconds", AC.voice_prompt("Ada", line="hi", seconds=8.0416))
        # no line: the neutral sentence
        self.assertIn(AC.NEUTRAL_LINE, AC.voice_prompt("Ada"))


# ---------------------------------------------------------------------------
# generating a voice ref
# ---------------------------------------------------------------------------

class VoiceGenerateTest(Episode):
    def test_listed_and_can_generate(self):
        refs = {r["id"]: r for r in R.list_refs(self.ep)}
        self.assertIn("voice:ada", refs)
        # a character with no `voice_sample` gets a ref of his own
        rex = refs["voice:rex"]
        self.assertEqual((rex["kind"], rex["path"], rex["exists"], rex["can_generate"]),
                         ("voice", None, False, True))
        # props and vehicles never speak
        self.assertNotIn("voice:kettle", refs)
        self.assertNotIn("voice:van", refs)
        ada = refs["voice:ada"]
        self.assertTrue(ada["can_generate"])
        self.assertIsNone(ada["why_not"])
        self.assertEqual(ada["effective"]["target"], VOICE)
        self.assertEqual(ada["effective"]["line_source"], "script")
        self.assertIn(ada["effective"]["line"], ada["prompt"])

    def test_plan(self):
        (job,) = R.plan_generate(self.s, R.GenRequest("voice:ada", seconds=6.0),
                                 rng=random.Random(1))
        self.assertTrue(job.is_audio)
        self.assertEqual((job.frames, job.fps), (145, 24.0))
        self.assertAlmostEqual(job.seconds, 145 / 24.0)
        self.assertEqual((job.width, job.height), (0, 0))
        self.assertEqual(job.line_source, "script")
        self.assertIn('Ada says: "', job.prompt)
        self.assertEqual(job.seed, R.stable_seed(R.find_ref(self.s, "voice:ada")))
        # rex barks once: the longest line he has is still his own
        (rex,) = R.plan_generate(self.s, R.GenRequest("voice:rex"), rng=random.Random(1))
        self.assertEqual((rex.line_source, rex.line), ("script", "Woof."))
        # a character with nothing to say in this episode gets the neutral
        # sentence (everyone in the fixture speaks, so ask the writer directly)
        story = R.episode_story(self.ep)
        self.assertEqual(AC.voice_line(story, "ada")[1], "script")
        self.assertEqual(AC.voice_line(story, "nobody"), (AC.NEUTRAL_LINE, "neutral"))
        self.assertEqual(AC.voice_line(None, "ada"), (AC.NEUTRAL_LINE, "neutral"))
        # a typed prompt is the caller's, and says so
        (own,) = R.plan_generate(self.s, R.GenRequest("voice:ada", prompt="say something"),
                                 rng=random.Random(1))
        self.assertEqual((own.prompt, own.line_source), ("say something", "request"))
        # out-of-range lengths and an image target are refused
        for bad in (dict(ref="voice:ada", seconds=0.5), dict(ref="voice:ada", seconds=90),
                    dict(ref="voice:ada", target="krea2")):
            with self.assertRaises(R.RefError, msg=bad):
                R.plan_generate(self.s, R.GenRequest(**bad))

    def test_graph(self):
        (job,) = R.plan_generate(self.s, R.GenRequest("voice:ada", seconds=6.0, seed=7),
                                 rng=random.Random(1))
        take = R.start_gen(self.s, job)
        self.assertTrue(take.paths.image.endswith(".wav"))
        base = J.load_graph(job.target.binding.workflow)
        g = R.graph_for(base, job, take)
        self.assertEqual(J.check_graph(g, OBJECT_INFO), [])
        saver = next(v for v in g.values() if v["class_type"] == "H3SaveRefAudio")
        self.assertEqual(saver["inputs"]["sidecar"], os.path.abspath(take.paths.sidecar))
        self.assertFalse([v for v in g.values() if v["class_type"] == "SaveAudio"])
        lat = next(v["inputs"] for v in g.values()
                   if v["class_type"] == "LTXVEmptyLatentAudio")
        self.assertEqual((lat["frames_number"], lat["frame_rate"]), (145, 24.0))
        self.assertEqual(next(v["inputs"]["frame_rate"] for v in g.values()
                              if v["class_type"] == "LTXVConditioning"), 24.0)
        self.assertEqual(next(v["inputs"]["noise_seed"] for v in g.values()
                              if v["class_type"] == "RandomNoise"), 7)
        self.assertEqual(next(v["inputs"]["steps"] for v in g.values()
                              if v["class_type"] == "LTXVScheduler"), 8)
        self.assertEqual(next(v["inputs"]["audio_cfg"] for v in g.values()
                              if v["class_type"] == "LTXVDualCFGGuider"), 1.0)
        pos = next(v["inputs"]["text"] for k, v in g.items()
                   if (v.get("_meta") or {}).get("title") == "Positive prompt")
        self.assertEqual(pos, job.prompt)
        neg = next(v["inputs"]["text"] for k, v in g.items()
                   if (v.get("_meta") or {}).get("title") == "Negative prompt")
        self.assertIn("music", neg)
        # no reference-audio chain: the target's capabilities say so
        self.assertFalse([v for v in g.values() if v["class_type"] == "LTXVReferenceAudio"])
        # the sidecar records the length and the line
        sc = T.read_sidecar(take.paths.sidecar)
        self.assertEqual((sc["frames"], sc["fps"], sc["line_source"], sc["target"]),
                         (145, 24.0, "script", VOICE))
        self.assertIsNone(sc["width"])

    def test_reference_audio_chain(self):
        """The voice-clone path, which ltx2_voice ships with off."""
        (job,) = R.plan_generate(self.s, R.GenRequest("voice:ada"), rng=random.Random(1))
        wav(os.path.join(self.s.home, "audio", "voices", "ada_sample.wav"))
        self.assertEqual(R.stage_voice_reference(self.s, job, None), {})   # off
        caps = job.target.spec["capabilities"]
        caps["reference_audio"] = True
        try:
            inputs = R.stage_voice_reference(self.s, job, None)
            self.assertTrue(inputs["reference_audio"].startswith(J.INPUT_SUBFOLDER + "/"))
            self.assertTrue(inputs["reference_audio"].endswith(".wav"))
            take = R.start_gen(self.s, job)
            g = R.graph_for(J.load_graph(job.target.binding.workflow), job, take)
        finally:
            caps["reference_audio"] = False
        self.assertEqual(J.check_graph(g, OBJECT_INFO), [])
        load = next(v for v in g.values() if v["class_type"] == "LoadAudio")
        self.assertEqual(load["inputs"]["audio"], inputs["reference_audio"])
        patch = next(v for v in g.values() if v["class_type"] == "LTXVReferenceAudio")
        self.assertEqual(patch["inputs"]["identity_guidance_scale"], 3.0)
        guider = next(v for v in g.values() if v["class_type"] == "LTXVDualCFGGuider")
        self.assertEqual(guider["inputs"]["model"][1], 0)
        self.assertEqual(guider["inputs"]["positive"][1], 1)
        self.assertEqual(guider["inputs"]["negative"][1], 2)
        self.assertEqual({guider["inputs"][k][0] for k in ("model", "positive", "negative")},
                         {next(k for k, v in g.items()
                               if v["class_type"] == "LTXVReferenceAudio")})
        self.assertEqual(T.read_sidecar(take.paths.sidecar)["references"][0]["role"], "voice")

    def test_queue_and_close(self):
        comfy = J.Comfy(self.fake().url)
        out = R.queue_generate(self.s, R.GenRequest("voice:ada", seconds=6.0), comfy,
                               listing=J.model_lister(comfy))
        self.assertEqual(out["errors"], [])
        (q,) = out["queued"]
        self.assertEqual((q["ref"], q["target"], q["take"]), ("voice:ada", VOICE, 1))
        ref = R.find_ref(self.s, "voice:ada")
        t = R.get_take(ref, None, 1)
        self.assertEqual(t.status, "ok")
        self.assertTrue(t.paths.image.endswith("_t01.wav"))
        self.assertAlmostEqual(PK.media_info(t.paths.image)["duration"], 145 / 24.0, places=2)
        j = R.take_json(self.ep, ref, t)
        self.assertIsNone(j["image"])
        self.assertTrue(j["audio"].endswith(".wav"))
        self.assertEqual(j["line"], t.sidecar["line"])
        # a ComfyUI whose SaveAudio wrote a flac: the take keeps that extension
        t2 = R.reserve_take(ref, None, {"status": "queued", "queued": T.now()}, ext=".wav")
        entry = {"status": {"status_str": "success", "completed": True},
                 "outputs": {"9": {"audio": [{"filename": "x.flac", "subfolder": "",
                                              "type": "output"}]}}}

        class Fetch:
            def view(self, a):
                return wav_bytes(1.0)
        self.assertEqual(R.finish_from_history(t2, entry, Fetch()), "ok")
        self.assertTrue(R.get_take(ref, None, t2.take).paths.image.endswith(".flac"))


# ---------------------------------------------------------------------------
# picking: the series config's voice_sample
# ---------------------------------------------------------------------------

class VoicePickTest(Episode):
    def sample(self, ref_id: str) -> R.RefTake:
        ref = R.find_ref(self.s, ref_id)
        return R.import_take(self.s, ref, None,
                             wav(os.path.join(self._t.name, f"{ref.key}.wav")))

    def test_pick_existing_sample(self):
        ref = R.find_ref(self.s, "voice:ada")
        t = self.sample("voice:ada")
        res = R.pick_take(self.s, ref, None, t.take)
        self.assertFalse(res.series_changed)
        self.assertTrue(os.path.isfile(os.path.join(self.ep, "audio", "voices",
                                                    "ada_sample.wav")))
        self.assertEqual(R.picked_take(R.load_picks(ref.home), ref.id), t.take)

    def test_pick_writes_voice_sample(self):
        ref = R.find_ref(self.s, "voice:rex")
        self.assertEqual(ref.path, "")
        before = open(self.s.config_file, encoding="utf-8").read()
        t = self.sample("voice:rex")
        res = R.pick_take(self.s, ref, None, t.take)
        self.assertTrue(res.series_changed)
        self.assertEqual(R.ep_rel(self.ep, res.live), "refs/voices/rex.wav")
        self.assertTrue(os.path.isfile(os.path.join(self.ep, "refs", "voices", "rex.wav")))
        cfg = json.load(open(self.s.config_file, encoding="utf-8"))
        self.assertEqual(cfg["subjects"]["rex"]["voice_sample"], "refs/voices/rex.wav")
        # written through the Phase 9a save path: a history copy is kept
        hist = os.path.join(self.ep, "_history")
        self.assertTrue(any(f.startswith("series") for f in os.listdir(hist)))
        self.assertNotEqual(open(self.s.config_file, encoding="utf-8").read(), before)
        # and the ref now has a live file
        s2 = R.load_series(self.ep)
        rex = R.find_ref(s2, "voice:rex")
        self.assertEqual(rex.path, "refs/voices/rex.wav")
        self.assertTrue(os.path.isfile(rex.file))
        # picking again changes nothing in the series config
        t2 = self.sample("voice:rex")
        self.assertFalse(R.pick_take(s2, rex, None, t2.take).series_changed)

    def test_clear_and_discard(self):
        ref = R.find_ref(self.s, "voice:ada")
        t = self.sample("voice:ada")
        R.pick_take(self.s, ref, None, t.take)
        res = R.clear_pick(self.s, ref, None)
        self.assertEqual(res.was, t.take)
        self.assertFalse(os.path.isfile(ref.file))
        # the series config still names the sample: the ref stays listed
        self.assertEqual(json.load(open(self.s.config_file, encoding="utf-8"))
                         ["subjects"]["ada"]["voice_sample"], "audio/voices/ada_sample.wav")
        self.assertTrue(R.is_cleared(R.load_picks(ref.home), ref.id))
        # a voice with no sample named has no file to clear
        with self.assertRaises(R.RefError):
            R.clear_pick(self.s, R.find_ref(self.s, "voice:rex"))
        # discarding the pick clears it too
        t2 = self.sample("voice:ada")
        R.pick_take(self.s, ref, None, t2.take)
        d = R.discard_take(self.s, ref, None, t2.take)
        self.assertIsNotNone(d.cleared)
        self.assertTrue(d.moved)

    def test_auto_pick_leaves_voices_alone(self):
        ref = R.find_ref(self.s, "voice:ada")
        self.sample("voice:ada")
        # picking a voice can write the series config, so it is never automatic
        self.assertEqual(R.auto_pick(self.s, ref), [])


# ---------------------------------------------------------------------------
# a voice sample out of a take (no model)
# ---------------------------------------------------------------------------

class VoiceFromTakeTest(Episode):
    def video_take(self, shot: str = "sh020", pass_: str = "proxy", seconds: float = 3.0):
        """A take of `shot` whose _h3.wav carries the sound."""
        t = T.reserve_take(self.ep, pass_, shot, {"status": "queued", "queued": T.now()})
        open(t.paths.mp4, "wb").close()
        wav(t.paths.h3_wav, seconds)
        T.update_sidecar(t.paths.sidecar, status="ok", finished=T.now(),
                         mp4=os.path.basename(t.paths.mp4))
        return T.get_take(self.ep, pass_, shot, t.take)

    @needs_ffmpeg
    def test_cut_and_pick(self):
        src = self.video_take()
        ref = R.find_ref(self.s, "voice:ada")
        res = R.voice_from_take(self.s, ref, "sh020", src.take, "proxy", 0.5, 2.0)
        self.assertEqual(res.take.status, "ok")
        self.assertTrue(res.take.paths.image.endswith(".wav"))
        self.assertAlmostEqual(PK.media_info(res.take.paths.image)["duration"], 1.5,
                               places=1)
        sc = res.take.sidecar
        self.assertEqual((sc["source"], sc["source_shot"], sc["source_take"],
                          sc["source_pass"], sc["source_start"], sc["source_end"]),
                         ("from_take", "sh020", src.take, "proxy", 0.5, 2.0))
        # picked: the voice had no live file
        self.assertTrue(res.picked)
        self.assertTrue(os.path.isfile(ref.file))
        j = R.take_json(self.ep, ref, res.take)
        self.assertEqual(j["from"], {"shot": "sh020", "take": src.take, "pass": "proxy",
                                     "start": 0.5, "end": 2.0})

    @needs_ffmpeg
    def test_errors(self):
        src = self.video_take()
        ref = R.find_ref(self.s, "voice:ada")
        for kw in (dict(start=0.0, end=0.05),             # too short
                   dict(start=0.0, end=60.0),             # too long
                   dict(start=9.0, end=10.0)):            # after the end
            with self.assertRaises(R.RefError, msg=kw):
                R.voice_from_take(self.s, ref, "sh020", src.take, "proxy", **kw)
        with self.assertRaises(R.RefError):               # not a voice ref
            R.voice_from_take(self.s, R.find_ref(self.s, "subject:ada"), "sh020",
                              src.take, "proxy", 0.0, 1.0)
        with self.assertRaises(R.UnknownRef):
            R.voice_from_take(self.s, ref, "sh020", 99, "proxy", 0.0, 1.0)
        # a take with no sound at all
        silent = T.reserve_take(self.ep, "proxy", "sh030", {"status": "queued",
                                                            "queued": T.now()})
        open(silent.paths.mp4, "wb").close()
        T.update_sidecar(silent.paths.sidecar, status="ok", finished=T.now(),
                         mp4=os.path.basename(silent.paths.mp4))
        with self.assertRaises(R.NotUsable):
            R.voice_from_take(self.s, ref, "sh030", silent.take, "proxy", 0.0, 1.0)


# ---------------------------------------------------------------------------
# defaults
# ---------------------------------------------------------------------------

class VoiceDefaultsTest(Episode):
    def test_layers(self):
        d = R.image_defaults(self.s)
        self.assertEqual((d["voice_target"], d["voice_target_source"]), (VOICE, "default"))
        # the series config's refs block
        cfg = json.load(open(self.s.config_file, encoding="utf-8"))
        cfg["refs"] = {"voice_target": VOICE}
        with open(self.s.config_file, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh)
        s = R.load_series(self.ep)
        self.assertEqual(R.image_defaults(s)["voice_target_source"], "series")
        # the editor's episode choice beats it
        R.set_image_defaults(self.ep, {"voice_target": VOICE})
        self.assertEqual(R.image_defaults(s)["voice_target_source"], "editor")
        self.assertEqual(T.episode_field(T.load_overrides(self.ep), "voice_target"), VOICE)
        R.set_image_defaults(self.ep, {"voice_target": None})
        self.assertEqual(R.image_defaults(s)["voice_target_source"], "series")
        with self.assertRaises(R.RefError):
            R.set_image_defaults(self.ep, {"voice_target": "krea2"})

    def test_per_ref_override(self):
        ref = R.find_ref(self.s, "voice:ada")
        ov = R.load_overrides(ref.home)
        R.set_ref_override(ov, ref, None, {"target": VOICE}, None)
        R.save_overrides(ref.home, ov)
        self.assertEqual(R.image_target_for(self.s, ref).id, VOICE)
        # a voice ref's target override must be an audio target
        with self.assertRaises(R.RefError):
            R.set_ref_override(R.load_overrides(ref.home), ref, None,
                               {"target": "krea2"}, None)
        # and an image ref's must not be one
        with self.assertRaises(R.RefError):
            R.set_ref_override(R.load_overrides(ref.home),
                               R.find_ref(self.s, "location:kitchen"), None,
                               {"target": VOICE}, None)
        # the request still beats the override
        self.assertEqual(R.image_target_for(self.s, ref, VOICE).id, VOICE)


# ---------------------------------------------------------------------------
# the routes
# ---------------------------------------------------------------------------

def tearDownModule():
    test_api.tearDownModule()


class VoiceApiTest(ApiTest):
    def refs(self):
        return {r["id"]: r for r in self.ok(A.get_refs(self.ctx, {"ep": self.ep}))["refs"]}

    def test_targets_kind_audio(self):
        data = self.ok(A.get_targets(self.ctx, {"kind": "audio"}))
        self.assertEqual([t["id"] for t in data["targets"]], [VOICE])
        self.assertEqual(data["default"]["audio"], VOICE)
        self.assertEqual(data["targets"][0]["capabilities"]["mode"], "t2a")
        self.err(A.get_targets(self.ctx, {"kind": "voice"}), 400)

    def test_defaults_route(self):
        d = self.ok(A.get_refs(self.ctx, {"ep": self.ep}))["defaults"]
        self.assertEqual(d["voice_target"], VOICE)
        out = self.ok(A.put_refs_defaults(self.ctx, {"ep": self.ep, "voice_target": VOICE}))
        self.assertEqual(out["defaults"]["voice_target_source"], "editor")
        self.err(A.put_refs_defaults(self.ctx, {"ep": self.ep, "voice_target": "krea2"}), 400)

    def test_generate_and_pick(self):
        data = self.ok(A.post_refs_generate(self.ctx, {"ep": self.ep, "ref": "voice:rex",
                                                       "seconds": 6}))
        self.assertEqual(data["errors"], [])
        (q,) = data["queued"]
        self.assertEqual((q["ref"], q["target"], q["take"]), ("voice:rex", VOICE, 1))
        self.assertEqual(self.events_of("h3pipe.ref"),
                         [{"ep": self.ep, "ref": "voice:rex", "view": None, "take": 1,
                           "status": "queued"}])
        rex = self.refs()["voice:rex"]
        t = rex["takes"][0]
        self.assertEqual((t["status"], t["source"]), ("ok", "generated"))
        self.assertTrue(t["audio"].endswith(".wav"))
        self.assertIsNone(t["image"])
        # picking writes the series config
        out = self.ok(A.put_refs_pick(self.ctx, {"ep": self.ep, "ref": "voice:rex",
                                                 "take": 1}))
        self.assertTrue(out["series_changed"])
        self.assertEqual(out["path"], "refs/voices/rex.wav")
        self.assertTrue(out["exists"])
        cfg = json.load(open(os.path.join(self.ep, "series.json"), encoding="utf-8"))
        self.assertEqual(cfg["subjects"]["rex"]["voice_sample"], "refs/voices/rex.wav")
        # seconds is only a voice's
        self.err(A.post_refs_generate(self.ctx, {"ep": self.ep, "ref": "location:kitchen",
                                                 "seconds": 6}), 400)
        self.err(A.post_refs_generate(self.ctx, {"ep": self.ep, "ref": "voice:rex",
                                                 "target": "krea2"}), 400)

    @needs_ffmpeg
    def test_voice_from_take_route(self):
        self.render("sh020")                      # a take to cut the voice out of
        t = T.list_takes(self.ep, "proxy", "sh020")[-1]
        wav(t.paths.h3_wav, 3.0)
        out = self.ok(A.post_refs_voice_from_take(self.ctx, {
            "ep": self.ep, "ref": "voice:rex", "shot": "sh020", "take": t.take,
            "pass": "proxy", "start": 0.5, "end": 2.0}))
        self.assertEqual(out["source"]["shot"], "sh020")
        # rex had no sample: the pick writes refs/voices/rex.wav and series.json
        self.assertTrue(out["picked"])
        self.assertEqual(out["path"], "refs/voices/rex.wav")
        take = next(x for x in out["takes"] if x["take"] == out["take"])
        self.assertEqual(take["source"], "from_take")
        self.assertEqual(take["from"]["start"], 0.5)
        self.assertIn(("POST", "/h3pipe/refs/voice-from-take"),
                      {(m, p) for m, p, _f, _t in A.ROUTES})
        self.err(A.post_refs_voice_from_take(self.ctx, {
            "ep": self.ep, "ref": "subject:ada", "shot": "sh020", "take": t.take,
            "start": 0.0, "end": 1.0}), 400)
        self.err(A.post_refs_voice_from_take(self.ctx, {
            "ep": self.ep, "ref": "voice:rex", "shot": "sh020", "take": 99,
            "start": 0.0, "end": 1.0}), 404)


# ---------------------------------------------------------------------------
# the CLI
# ---------------------------------------------------------------------------

class VoiceCliTest(Episode):
    def kreagen(self, *args) -> tuple[int, str]:
        r = subprocess.run([sys.executable, os.path.join(ROOT, "kreagen.py"),
                            "--project-root", self.ep, *args],
                           capture_output=True, timeout=300)
        return r.returncode, (r.stdout + r.stderr).decode("utf-8", "replace")

    def test_list_with_voices(self):
        rc, out = self.kreagen("--all", "--voices", "--list")
        self.assertEqual(rc, 0, out)
        self.assertIn("voices on ltx2_voice", out)
        self.assertIn("ada_sample.wav", out)
        self.assertIn(os.path.join("voices", "rex.wav"), out)   # no sample yet
        rc, out = self.kreagen("--all", "--list")          # off by default
        self.assertEqual(rc, 0, out)
        self.assertNotIn("ada_sample.wav", out)

    def test_dry_run_prompt_and_target(self):
        rc, out = self.kreagen("--all", "--voices", "--only", "voice:ada",
                               "--voice-seconds", "6", "--dry-run")
        self.assertEqual(rc, 0, out)
        self.assertIn('Ada says: "', out)
        self.assertIn("About 6 seconds", out)
        rc, out = self.kreagen("--all", "--voices", "--voice-target", "krea2", "--list")
        self.assertEqual(rc, 1, out)

    def test_parse_from_take(self):
        sys.path.insert(0, ROOT)
        import kreagen
        self.assertEqual(kreagen.parse_from_take("sh020:3:1.2-6.4"),
                         ("sh020", 3, 1.2, 6.4))
        self.assertEqual(kreagen.parse_from_take("sh020:t03:0-5")[1], 3)
        with self.assertRaises(ValueError):
            kreagen.parse_from_take("sh020:3")

    @needs_ffmpeg
    def test_from_take(self):
        t = T.reserve_take(self.ep, "proxy", "sh020", {"status": "queued", "queued": T.now()})
        open(t.paths.mp4, "wb").close()
        wav(t.paths.h3_wav, 3.0)
        T.update_sidecar(t.paths.sidecar, status="ok", finished=T.now(),
                         mp4=os.path.basename(t.paths.mp4))
        rc, out = self.kreagen("--all", "--only", "voice:ada",
                               "--from-take", f"sh020:{t.take}:0.5-2.0")
        self.assertEqual(rc, 0, out)
        self.assertIn("voice:ada: t01", out)
        self.assertIn("picked", out)
        self.assertTrue(os.path.isfile(os.path.join(self.ep, "audio", "voices",
                                                    "ada_sample.wav")))
        # more than one voice selected: it must be narrowed
        rc, out = self.kreagen("--all", "--from-take", f"sh020:{t.take}:0.5-2.0")
        self.assertEqual(rc, 1, out)
        self.assertIn("narrow the run to one voice ref", out)


if __name__ == "__main__":
    unittest.main()
