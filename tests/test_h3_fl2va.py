"""
The minimax_h3_fl2va target: MiniMax H3 first/last-frame to video + audio
(targets/video/minimax_h3_fl2va).

  - golden-style: kitchen_sink compiled for minimax_h3_fl2va (what retargeting
    each shot gives), both passes: the shotlist doc and report, and the prompts
    (tests/golden/minimax_h3_fl2va/; `python tests/test_h3_fl2va.py --update`
    rewrites them, intended changes only)
  - the prompt: H3's three base-mode fields, no <Picture N> in a build, the
    keyframe alignment line added at queue time for the frames a render has
  - audio: clone falls back to generate with a note; dub / dub_keep_foley cut
    the shot's slice of the recording, upload it and anchor it at frame 0
  - the flattened, patched graph passes check_graph against the trimmed
    /object_info (tests/fixtures/workflows/object_info_h3_fl2va.json, from a
    running ComfyUI) with no keyframes, the first, the last, both, and a dub
"""
from __future__ import annotations

import json
import os
import shutil
import struct
import sys
import tempfile
import unittest
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
import h3jobs as J  # noqa: E402
import h3takes as T  # noqa: E402
import targets as TG  # noqa: E402
from h3core import ir  # noqa: E402
from test_ltx_ingredients import kitchen_sink  # noqa: E402
from test_render import FIXTURE, FakeComfy, build_episode  # noqa: E402

FL, H3 = "minimax_h3_fl2va", "minimax_h3_ref2va"
GOLDEN = os.path.join(HERE, "golden", FL)
WF = os.path.join(HERE, "fixtures", "workflows")
OBJECT_INFO = json.load(open(os.path.join(WF, "object_info_h3_fl2va.json"), encoding="utf-8"))
MODEL = "minimax_h3_fl2va_pruned_int8_convrot.safetensors"
LORA8 = "minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors"
LORA4 = "minimax_h3_fl2v_lightx2v_turbo_4step_v0.1_comfy.safetensors"


def capture() -> dict[str, str]:
    """kitchen_sink compiled for minimax_h3_fl2va: golden file name -> text."""
    series_cfg, story = kitchen_sink()
    t = TG.load_target(FL, "video")
    out = {}
    for pass_ in ("final", "proxy"):
        doc, report = t.compile_episode(story, series_cfg, pass_)
        sfx = "_proxy" if pass_ == "proxy" else ""
        out[f"kitchen_sink{sfx}.json"] = json.dumps({"doc": doc, "report": report},
                                                    ensure_ascii=False, indent=2) + "\n"
        out[f"kitchen_sink_prompts{sfx}.txt"] = "".join(
            f"## {s['id']}\n{s['prompt']}\n\n" for s in doc["shots"])
    return out


def update() -> None:
    os.makedirs(GOLDEN, exist_ok=True)
    for name, text in capture().items():
        with open(os.path.join(GOLDEN, name), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        print(f"  {name} -> {os.path.relpath(GOLDEN, ROOT)}")


def of(g: dict, ctype: str) -> list[str]:
    return [k for k, v in g.items() if v["class_type"] == ctype]


def write_wav(path: str, seconds: float, sr: int = 16000, channels: int = 1) -> None:
    """A wav whose sample n is n % 30000 (so a slice's first sample says where
    it was cut)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    n = int(seconds * sr)
    with wave.open(path, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(b"".join(struct.pack("<h", i % 30000) * channels for i in range(n)))


# ---------------------------------------------------------------------------
# golden-style
# ---------------------------------------------------------------------------

class GoldenTest(unittest.TestCase):
    maxDiff = None

    def test_kitchen_sink(self):
        for name, text in capture().items():
            with self.subTest(file=name):
                path = os.path.join(GOLDEN, name)
                if not os.path.isfile(path):
                    self.fail(f"no golden at {os.path.relpath(path, ROOT)} — run "
                              f"`python tests/test_h3_fl2va.py --update`")
                with open(path, encoding="utf-8") as fh:
                    self.assertEqual(text, fh.read().replace("\r\n", "\n"), name)


# ---------------------------------------------------------------------------
# the target
# ---------------------------------------------------------------------------

class TargetTest(unittest.TestCase):
    def setUp(self):
        self.t = TG.load_target(FL, "video")
        self.cfg, self.story = kitchen_sink()

    def test_template_is_h3s(self):
        tp, h3 = self.t.template, TG.load_target(H3).template
        for n in (1, 5, 6, 100, 124, 362, 363):
            self.assertEqual(tp.snap(n), h3.snap(n))
            self.assertEqual((tp.snap(n) - 5) % 17, 0)
        self.assertEqual((tp.fps, tp.size_multiple), (24.0, 32))
        with self.assertRaises(ValueError):
            tp.validate_size(480, 272)

    def test_presets(self):
        f, p = self.t.presets["final"], self.t.presets["proxy"]
        self.assertEqual((f.model, f.lora, f.steps, f.width, f.height),
                         (MODEL, LORA8, 8, 1344, 768))
        self.assertEqual((p.model, p.lora, p.steps, p.width, p.height),
                         (None, LORA4, 4, 448, 256))
        self.assertEqual((f.extra["sampler"], p.extra["sampler"]), ("res_multistep", "euler"))
        # the series target is H3 Ref2VA: its pass blocks lend only their size
        doc, _ = self.t.compile_episode(self.story, self.cfg, "proxy")
        d = doc["defaults"]
        self.assertEqual((d["model"], d["lora"], d["steps"], d["width"], d["height"]),
                         (MODEL, LORA4, 4, 448, 256))
        self.assertEqual(d["text_encoder"], "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors")

    def test_describe_and_audio(self):
        d = self.t.describe()
        self.assertEqual((d["label"], d["short"]), ("MiniMax H3 FL2VA (first/last frames)",
                                                   "H3 FL2V"))
        c = d["capabilities"]
        self.assertEqual(c["keyframes"], ["first", "last"])
        self.assertEqual(c["policies"], ["generate", "dub", "dub_keep_foley"])
        self.assertEqual(c["policy_fallback"], "generate")
        self.assertFalse(c["subject_refs"] or c["voice_reference"] or c["negative_prompt"])
        self.assertEqual(self.t.audio_policy("dub"), ("dub", ""))
        pol, note = self.t.audio_policy("clone")
        self.assertEqual(pol, "generate")
        self.assertIn("no voice sample", note)

    def test_policies_in_the_build(self):
        doc, report = self.t.compile_episode(self.story, self.cfg, "final")
        by = {s["id"]: s for s in doc["shots"]}
        # the series config clones: clone shots render with generate, and say so
        self.assertEqual(by["sh020"]["audio_policy"], "generate")
        self.assertEqual(by["sh020"]["audio_intent"], "clone")
        self.assertIn("renders as generate", by["sh020"]["audio_note"])
        self.assertTrue(any("ask for audio clone" in w for w in report["warnings"]))
        # recorded dialogue is anchored, both ways
        self.assertEqual(by["sh110"]["audio_policy"], "dub")
        self.assertEqual(by["sh120"]["audio_policy"], "dub_keep_foley")
        self.assertNotIn("audio_note", by["sh110"])
        self.assertEqual((by["sh110"]["audio_in"], by["sh110"]["audio_out"]), (1.0, 3.5))
        # an explicit generate stays generate; nothing is required
        self.assertEqual(by["sh040"]["audio_policy"], "generate")
        self.assertEqual(report["needed"], {})
        self.assertEqual(by["sh010"]["keyframes"], {"first": "refs/shots/sh010/first.png",
                                                    "last": "refs/shots/sh010/last.png"})
        slots = self.t.ref_slots(doc, by["sh110"])
        self.assertEqual([(s["slot"], s.get("role"), s.get("optional", False)) for s in slots],
                         [("first frame", "first", True), ("last frame", "last", True),
                          ("dialogue recording", None, False)])
        self.assertEqual(slots[2]["path"], "audio/ks01_mix.wav")
        self.assertEqual(len(self.t.ref_slots(doc, by["sh010"])), 2)

    def test_dub_needs_a_window(self):
        shot = self.story.sequences[1].shots[0]                 # sh110
        old = shot.timing
        try:
            shot.timing = {"seconds": 2.0}
            with self.assertRaises(ValueError) as cm:
                self.t.compile_episode(self.story, self.cfg, "final", only={shot.id})
            self.assertIn("needs an `audio: in-out` window", str(cm.exception))
        finally:
            shot.timing = old


class PromptTest(unittest.TestCase):
    def setUp(self):
        from targets.video.minimax_h3_fl2va import prompt as P
        self.P = P
        self.cfg, self.story = kitchen_sink()

    def test_three_fields_and_no_pictures(self):
        doc, _ = TG.load_target(FL).compile_episode(self.story, self.cfg, "final")
        for s in doc["shots"]:
            p = s["prompt"]
            fields = p.split("\n\n")
            self.assertEqual([f.split(":", 1)[0] for f in fields],
                             ["integrated_multimodal_description", "overall_soundscape",
                              "non_diegetic_music"], s["id"])
            self.assertTrue(fields[0].startswith(
                "integrated_multimodal_description: [Shot 1] A flat vector cartoon"))
            for label in ("<Picture", "<Subject", "<Audio", "subject_definitions",
                          "retention_analysis"):
                self.assertNotIn(label, p)
        by = {s["id"]: s["prompt"] for s in doc["shots"]}
        # subjects in words, dialogue in H3's tags, voiceover's clause
        self.assertIn("Ada is a tall woman in her thirties", by["sh010"])
        self.assertIn("Ada, with a voice that is dry, quick and precise, (S1) says, warmly: "
                      "<d>[English] You know that one whistles.</d>", by["sh020"])
        self.assertIn("Bo (S2) says, whispering: <d>[English] That's the point.</d>", by["sh020"])
        self.assertIn("says in an off-screen voiceover: <d>[English] Not today.</d> while her "
                      "lips remain completely closed.", by["sh030"])
        self.assertIn('Visible on-screen text reads "OPEN 24 HOURS".', by["sh010"])
        self.assertIn("non_diegetic_music: A plucky ukulele sting.", by["sh010"])
        self.assertIn("close-up frames Ada against a shallow, out-of-focus slice", by["sh030"])
        # recorded dialogue
        self.assertIn("says, in the recorded voice: <d>[English] Van.</d>", by["sh110"])
        self.assertIn("overall_soundscape: The only audio is the recorded dialogue", by["sh110"])
        self.assertIn("overall_soundscape: Running footsteps splashing through puddles, laid "
                      "under the recorded dialogue.", by["sh120"])

    def test_keyframe_lines(self):
        P = self.P
        base = ("integrated_multimodal_description: [Shot 1] A shot.\n\n"
                "overall_soundscape: Rain.\n\nnon_diegetic_music: N/A")
        self.assertEqual(P.with_keyframes(base, (), 124, 24.0), base)
        first = P.with_keyframes(base, ["first"], 124, 24.0)
        self.assertTrue(first.startswith(
            "For the target video, at 0.00 seconds into the target video, <Picture 1> "
            "(from [Shot 1]) is fully referenced.\n\nintegrated_multimodal_description:"))
        self.assertIn("A shot. The shot develops forward from <Picture 1>, its first frame",
                      first)
        both = P.with_keyframes(base, ["last", "first"], 124, 24.0)
        self.assertTrue(both.startswith(
            "How the reference pictures align with the target video — Picture 1 (from Shot 1) "
            "aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) "
            "aligns with the 5.17-second mark of the target video.\n\n"))
        self.assertIn("established by Picture 2.\n\noverall_soundscape: Rain.", both)
        last = P.with_keyframes(base, ["last"], 73, 24.0)
        self.assertTrue(last.startswith(
            "How the reference pictures align with the target video — <Picture 1> (from "
            "[Shot 1]) aligns with the 3.04-second mark of the target video.\n\n"))
        # idempotent, and reversible: a second stage replaces the first's lines
        self.assertEqual(P.with_keyframes(both, ["first"], 124, 24.0), first)
        self.assertEqual(P.without_keyframes(both), base)
        self.assertEqual(P.with_keyframes(first, [], 124, 24.0), base)
        # an overridden prompt without the fields still gets them
        self.assertEqual(P.with_keyframes("Just prose.", ["first"], 124, 24.0).split("\n\n")[1],
                         "Just prose." + P._LANDINGS[("first",)])


# ---------------------------------------------------------------------------
# rendering: the graph, the take
# ---------------------------------------------------------------------------

class RenderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.src = os.path.join(cls._tmp.name, "ks01")
        os.makedirs(cls.src)
        build_episode(cls.src, refs=False)
        shutil.copy(os.path.join(FIXTURE, "series.json"), cls.src)
        cls.base = J.load_graph(os.path.join(ROOT, "targets", "video", FL, "workflow.json"))

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.root = os.path.join(self._t.name, "ks01")
        shutil.copytree(self.src, self.root)

    def tearDown(self):
        self._t.cleanup()

    def plan(self, sid, pass_="proxy", **req):
        doc, i = J.find_shot(self.root, pass_, sid)
        return J.plan_job(self.root, pass_, doc, i, J.RenderRequest(sid, target=FL, **req), {})

    def keyframe(self, sid, end):
        p = os.path.join(self.root, "refs", "shots", sid, f"{end}.png")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as fh:
            fh.write(f"{sid} {end}".encode())

    def stage_and_start(self, job):
        comfy = FakeComfy()
        try:
            got = J.stage_inputs(job, J.Comfy(comfy.url))
            take = J.start_job(job)
        finally:
            comfy.close()
        return got, take, comfy

    def graph(self, job, take):
        g = J.graph_for(self.base, job, take)
        self.assertEqual(J.check_graph(g, OBJECT_INFO), [])
        return g

    def test_text_only(self):
        job = self.plan("sh010")
        self.assertEqual((job.target, job.built_target, job.action), (FL, H3, "render"))
        self.assertEqual(job.missing, [])
        got, take, comfy = self.stage_and_start(job)
        self.assertEqual((got, comfy.uploads), ({}, {}))
        self.assertTrue(job.prompt.startswith("integrated_multimodal_description: [Shot 1]"))
        g = self.graph(job, take)
        for gone in ("LoadImage", "ImageScaleToTotalPixels", "GetImageSize", "ResolutionSelector",
                     "ComfyMathExpression", "PrimitiveFloat", "CreateVideo", "SaveVideo",
                     "MiniMaxH3AddGuide", "LoadAudio"):
            self.assertFalse(of(g, gone), gone)
        i2v = g[J.node_of(g, "MiniMaxH3ImageToVideo")]["inputs"]
        self.assertNotIn("first_frame", i2v)
        self.assertNotIn("last_frame", i2v)
        self.assertEqual((i2v["width"], i2v["height"], i2v["length"]), (448, 256, 73))
        self.assertEqual(i2v["prompt"], job.prompt)
        self.assertEqual(g[J.node_of(g, "UNETLoader")]["inputs"]["unet_name"], MODEL)
        lo = g[J.node_of(g, "LoraLoaderModelOnly")]["inputs"]
        self.assertEqual((lo["lora_name"], lo["strength_model"]), (LORA4, 1.0))
        # the LoRA sits between the model and everything that read it
        for ctype in ("BasicScheduler", "BasicGuider"):
            src = g[J.node_of(g, ctype)]["inputs"]["model"][0]
            self.assertEqual(g[src]["class_type"], "LoraLoaderModelOnly")
        self.assertEqual(g[J.node_of(g, "BasicScheduler")]["inputs"]["steps"], 4)
        self.assertEqual(g[J.node_of(g, "KSamplerSelect")]["inputs"]["sampler_name"], "euler")
        self.assertEqual(g[J.node_of(g, "RandomNoise")]["inputs"]["noise_seed"], job.seed)
        sv = g[J.node_of(g, "H3SaveShot")]["inputs"]
        self.assertEqual((sv["shot_id"], sv["fps"], sv["save_frames"], sv["audio_policy"]),
                         ("sh010", 24.0, False, "generate"))
        self.assertEqual(g[sv["images"][0]]["class_type"], "VAEDecode")
        self.assertEqual(g[sv["audio"][0]]["class_type"], "VAEDecodeAudio")
        guider = g[J.node_of(g, "BasicGuider")]["inputs"]
        self.assertEqual(g[guider["conditioning"][0]]["class_type"], "MiniMaxH3ImageToVideo")
        self.assertEqual(T.read_json(take.paths.shotlist)["target"], FL)

    def test_first_frame(self):
        self.keyframe("sh010", "first")
        job = self.plan("sh010")
        got, take, comfy = self.stage_and_start(job)
        self.assertEqual(list(got), ["first"])
        self.assertEqual(list(comfy.uploads), [got["first"]])
        self.assertTrue(job.prompt.startswith("For the target video, at 0.00 seconds"))
        # the frozen shotlist carries the prompt the graph renders
        self.assertEqual(T.read_json(take.paths.shotlist)["shots"][0]["prompt"], job.prompt)
        self.assertEqual(T.read_sidecar(take.paths.sidecar)["inputs"], got)
        g = self.graph(job, take)
        i2v = g[J.node_of(g, "MiniMaxH3ImageToVideo")]["inputs"]
        self.assertNotIn("last_frame", i2v)
        load = g[i2v["first_frame"][0]]
        self.assertEqual((load["class_type"], load["inputs"]["image"]), ("LoadImage", got["first"]))
        self.assertEqual(len(of(g, "LoadImage")), 1)
        self.assertFalse(of(g, "ImageScaleToTotalPixels"))
        self.assertEqual(i2v["prompt"], job.prompt)

    def test_first_and_last(self):
        self.keyframe("sh010", "first")
        self.keyframe("sh010", "last")
        job = self.plan("sh010", pass_="final")
        got, take, _ = self.stage_and_start(job)
        self.assertEqual(sorted(got), ["first", "last"])
        self.assertTrue(job.prompt.startswith("How the reference pictures align"))
        self.assertIn("Picture 2 (from Shot 1) aligns with the 3.04-second mark", job.prompt)
        g = self.graph(job, take)
        i2v = g[J.node_of(g, "MiniMaxH3ImageToVideo")]["inputs"]
        self.assertEqual(g[i2v["first_frame"][0]]["inputs"]["image"], got["first"])
        self.assertEqual(g[i2v["last_frame"][0]]["inputs"]["image"], got["last"])
        self.assertEqual((i2v["width"], i2v["height"]), (1344, 768))
        self.assertEqual(g[J.node_of(g, "KSamplerSelect")]["inputs"]["sampler_name"],
                         "res_multistep")
        self.assertEqual(g[J.node_of(g, "LoraLoaderModelOnly")]["inputs"]["lora_name"], LORA8)

    def test_last_only(self):
        self.keyframe("sh010", "last")
        job = self.plan("sh010")
        J.stage_inputs(job)                                   # a dry run
        self.assertEqual(list(job.inputs), ["last"])
        self.assertIn("<Picture 1> (from [Shot 1]) aligns with the 3.04-second", job.prompt)
        take = T.Take(job.id, job.take, job.pass_,
                      T.take_paths(self.root, job.pass_, job.id, job.take))
        g = self.graph(job, take)
        i2v = g[J.node_of(g, "MiniMaxH3ImageToVideo")]["inputs"]
        self.assertNotIn("first_frame", i2v)
        self.assertEqual(g[i2v["last_frame"][0]]["inputs"]["image"], job.inputs["last"])

    def test_dub_anchors_the_slice(self):
        write_wav(os.path.join(self.root, "audio", "ks01_mix.wav"), 10.0)
        job = self.plan("sh110")
        self.assertEqual((job.action, job.shot["audio_policy"]), ("render", "dub"))
        # a dry run cuts nothing
        dry = J.stage_inputs(job)
        self.assertTrue(dry["audio"].endswith("_dub.wav"))
        self.assertEqual(job.staged, [])
        got, take, comfy = self.stage_and_start(job)
        self.assertEqual(list(got), ["audio"])
        kept = os.path.join(take.paths.dir, take.paths.stem + "_dub.wav")
        self.assertEqual(comfy.uploads[got["audio"]], open(kept, "rb").read())
        with wave.open(kept, "rb") as w:
            self.assertEqual((w.getnchannels(), w.getframerate(), w.getnframes()),
                             (2, 16000, 40000))                   # 1.00-3.50, stereo
            first = struct.unpack("<hh", w.readframes(1))
        self.assertEqual(first, (16000, 16000))                   # cut at 1.00 s
        refs = {r["slot"]: r for r in T.read_sidecar(take.paths.sidecar)["refs"]}
        self.assertEqual(refs["dialogue recording"]["path"], "audio/ks01_mix.wav")
        self.assertEqual(refs["dialogue slice"]["sha1"], T.file_sha1(kept))
        g = self.graph(job, take)
        guide = g[J.node_of(g, "MiniMaxH3AddGuide")]["inputs"]
        i2v = J.node_of(g, "MiniMaxH3ImageToVideo")
        self.assertEqual((guide["positive"], guide["latent"], guide["frame_idx"]),
                         ([i2v, 0], [i2v, 1], 0))
        self.assertEqual(g[guide["audio"][0]]["inputs"]["audio"], got["audio"])
        self.assertEqual(g[guide["audio_vae"][0]]["inputs"]["vae_name"],
                         "minimax_h3_audio_vae_fp32.safetensors")
        guider = g[J.node_of(g, "BasicGuider")]["inputs"]
        self.assertEqual(guider["conditioning"][0], J.node_of(g, "MiniMaxH3AddGuide"))
        # the sampler still reads the node's own latent
        self.assertEqual(g[J.node_of(g, "SamplerCustomAdvanced")]["inputs"]["latent_image"],
                         [i2v, 1])
        self.assertEqual(g[J.node_of(g, "H3SaveShot")]["inputs"]["audio_policy"], "dub")

    def test_dub_without_the_recording(self):
        self.assertFalse(os.path.exists(os.path.join(self.root, "audio", "ks01_mix.wav")))
        job = self.plan("sh120")
        self.assertEqual(job.action, "blocked")
        self.assertEqual([r["slot"] for r in job.missing], ["dialogue recording"])
        job = self.plan("sh120", allow_missing_refs=True)
        self.assertEqual(job.missing_mode, "recompiled")
        self.assertEqual(job.recompiled["audio_policy"], "generate")
        self.assertNotIn("recorded", job.prompt)
        J.stage_inputs(job)
        self.assertEqual(job.inputs, {})

    def test_clone_says_it_falls_back(self):
        job = self.plan("sh020", pass_="final")
        self.assertEqual(job.shot["audio_policy"], "generate")
        self.assertTrue(any("audio clone renders as generate" in n for n in job.notes))


if __name__ == "__main__":
    if "--update" in sys.argv:
        update()
    else:
        unittest.main()
