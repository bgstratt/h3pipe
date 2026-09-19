"""
The Wan 2.2 video targets (targets/video/wan22_i2v, wan22_ti2v, wan22_vace;
shared code in targets/video/wan).

  - golden-style: kitchen_sink compiled for each Wan target (what retargeting
    each shot gives), both passes: the shotlist doc and report, and the prompts
    (tests/golden/<target>/; `python tests/test_wan.py --update` rewrites
    them, intended changes only)
  - the prompt: plain prose, no sound, the lines acted silently
  - audio: every policy renders `silent` with a note; dialogue shots warn
  - wan22_i2v: blocked without a first frame, even rendering anyway, with the
    reason; the two stages (models, LoRA chains by stage, the step split);
    first + last switches to WanFirstLastFrameToVideo
  - wan22_ti2v: text only, or from a first frame
  - wan22_vace: the reference image composed on white from the picked refs,
    kept in the take; missing refs block, rendering anyway drops them; the
    keyframes as VACE control frames and masks
  - every flattened, patched graph passes check_graph against the trimmed
    /object_info (tests/fixtures/workflows/object_info_wan.json, from the
    running ComfyUI)
  - the take records its fps (16 for 14B); h3assemble converts a 16 fps clip
    in a 24 fps cut by duration, and scales a clip of another size
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
sys.path.insert(0, HERE)
import h3edit as E  # noqa: E402
import h3jobs as J  # noqa: E402
import h3takes as T  # noqa: E402
import targets as TG  # noqa: E402
from targets.video.wan import prompt as WP  # noqa: E402
from test_ltx_ingredients import kitchen_sink  # noqa: E402
from test_render import FIXTURE, FakeComfy, build_episode  # noqa: E402

try:
    from PIL import Image
except ImportError:                                       # the reference image needs PIL
    Image = None

I2V, TI2V, VACE, H3 = "wan22_i2v", "wan22_ti2v", "wan22_vace", "minimax_h3_ref2va"
WAN = (I2V, TI2V, VACE)
WF = os.path.join(HERE, "fixtures", "workflows")
OBJECT_INFO = json.load(open(os.path.join(WF, "object_info_wan.json"), encoding="utf-8"))
HIGH = "wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors"
LOW = "wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors"
needs_pil = unittest.skipIf(Image is None, "composing a reference image needs PIL")
HAVE_FF = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def capture(tid: str) -> dict[str, str]:
    """kitchen_sink compiled for one Wan target: golden file name -> text."""
    series_cfg, story = kitchen_sink()
    t = TG.load_target(tid, "video")
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
    for tid in WAN:
        d = os.path.join(HERE, "golden", tid)
        os.makedirs(d, exist_ok=True)
        for name, text in capture(tid).items():
            with open(os.path.join(d, name), "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
            print(f"  {name} -> {os.path.relpath(d, ROOT)}")


def of(g: dict, ctype: str, title: str | None = None) -> list[str]:
    return [k for k, v in g.items() if v["class_type"] == ctype
            and (title is None or v["_meta"]["title"] == title)]


def titled(g: dict, ctype: str, title: str) -> dict:
    ids = of(g, ctype, title)
    assert len(ids) == 1, (ctype, title, ids)
    return g[ids[0]]["inputs"]


def png(path: str, size=(64, 64), colors=((200, 30, 30),)) -> None:
    """A PNG split into len(colors) vertical bands (a 4-panel strip)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img = Image.new("RGB", size)
    n = len(colors)
    for i, c in enumerate(colors):
        img.paste(c, (i * size[0] // n, 0, (i + 1) * size[0] // n, size[1]))
    img.save(path)


# ---------------------------------------------------------------------------
# golden-style
# ---------------------------------------------------------------------------

class GoldenTest(unittest.TestCase):
    maxDiff = None

    def test_kitchen_sink(self):
        for tid in WAN:
            for name, text in capture(tid).items():
                with self.subTest(target=tid, file=name):
                    path = os.path.join(HERE, "golden", tid, name)
                    if not os.path.isfile(path):
                        self.fail(f"no golden at {os.path.relpath(path, ROOT)} — run "
                                  f"`python tests/test_wan.py --update`")
                    with open(path, encoding="utf-8") as fh:
                        self.assertEqual(text, fh.read().replace("\r\n", "\n"), name)


# ---------------------------------------------------------------------------
# the targets
# ---------------------------------------------------------------------------

class TargetTest(unittest.TestCase):
    def setUp(self):
        self.cfg, self.story = kitchen_sink()

    def test_templates(self):
        for tid, fps, m, top in ((I2V, 16.0, 16, 161), (TI2V, 24.0, 32, 241), (VACE, 16.0, 16, 161)):
            tp = TG.load_target(tid).template
            self.assertEqual((tp.fps, tp.size_multiple, tp.max), (fps, m, top), tid)
            self.assertEqual({(tp.snap(n) - 1) % 4 for n in range(1, top + 1)}, {0}, tid)
            self.assertEqual([tp.snap(n) for n in (1, 5, 6, 80, 81)], [5, 5, 9, 81, 81])
            with self.assertRaises(ValueError):
                tp.snap(top + 1)
            # Wan's rate, whatever the series says
            self.assertEqual(tp.fps_for({"series": {"fps": 25}}), fps)
        self.assertEqual(TG.load_target(I2V).template.frames(5.0, 16.0), 81)
        self.assertEqual(TG.load_target(TI2V).template.frames(5.0, 24.0), 121)

    def test_describe(self):
        for tid, label, short, keyframes in ((I2V, "Wan 2.2 14B I2V", "Wan I2V", ["first", "last"]),
                                             (TI2V, "Wan 2.2 5B TI2V", "Wan 5B", ["first"]),
                                             (VACE, "Wan 2.2 14B VACE (refs)", "Wan+refs",
                                              ["first", "last"])):
            t = TG.load_target(tid)
            d = t.describe()
            self.assertEqual((d["label"], d["short"]), (label, short))
            caps = d["capabilities"]
            self.assertEqual((caps["audio"], caps["policies"], caps["policy_fallback"]),
                             ("none", ["silent"], "silent"))
            self.assertEqual(caps["keyframes"], keyframes)
            self.assertEqual((caps["prompt"], caps["negative_prompt"]), ("prose", True))
            self.assertEqual(caps["subject_refs"], tid == VACE)
            # model families for the model identification (glob patterns)
            models = t.spec["models"]
            for param, spec in models.items():
                if param.startswith("_"):
                    continue
                self.assertIn(param, t.binding.params, (tid, param))
                self.assertTrue(spec["patterns"])
            self.assertIn("model", models)
            self.assertEqual("model_low" in models, tid != TI2V)

    def test_presets_and_sizes(self):
        # not the series target: the H3 series' 448x256 proxy lends no size
        for tid, final, proxy in ((I2V, "832x480", "640x352"), (TI2V, "1280x704", "640x352"),
                                  (VACE, "832x480", "640x352")):
            t = TG.load_target(tid)
            for ps, want in (("final", final), ("proxy", proxy)):
                doc, rep = t.compile_episode(self.story, self.cfg, ps)
                self.assertEqual(rep["resolution"], want, (tid, ps))
                self.assertEqual(doc["defaults"]["fps"], t.template.fps)
        d, _ = TG.load_target(I2V).compile_episode(self.story, self.cfg, "proxy")
        dd = d["defaults"]
        self.assertEqual((dd["model"], dd["model_low"], dd["lora"]),
                         ("wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors",
                          "wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors", None))
        self.assertEqual([lo["name"] for lo in dd["loras"]], [HIGH, LOW])
        self.assertEqual((dd["steps"], dd["cfg"], dd["split"]), (4, 1.0, 0.5))

    def test_every_policy_renders_silent(self):
        for tid in WAN:
            doc, rep = TG.load_target(tid).compile_episode(self.story, self.cfg, "final")
            self.assertEqual(rep["policies"], {"silent": len(doc["shots"])})
            self.assertEqual(doc["defaults"]["audio_policy"], "silent")
            for s in doc["shots"]:
                self.assertEqual(s["audio_policy"], "silent")
                self.assertIn("renders as silent", s["audio_note"])
            w = "\n".join(rep["warnings"])
            self.assertIn("makes no sound", w)
            self.assertIn("dialogue shot(s): no audio or lip-sync on Wan", w)

    def test_first_frame_warning(self):
        _, rep = TG.load_target(I2V).compile_episode(self.story, self.cfg, "final")
        self.assertTrue(any("refs/shots/<shot>/first.png" in w and "wan22_ti2v" in w
                            for w in rep["warnings"]))
        _, rep = TG.load_target(TI2V).compile_episode(self.story, self.cfg, "final")
        self.assertFalse(any("first.png" in w for w in rep["warnings"]))

    def test_vace_needs_the_subject_refs(self):
        t = TG.load_target(VACE)
        doc, rep = t.compile_episode(self.story, self.cfg, "final")
        s = next(x for x in doc["shots"] if x["id"] == "sh020")
        self.assertEqual([(p.get("subject"), p.get("view")) for p in s["panels"]],
                         [("ada", "body"), ("bo", "body"), ("kettle", None)])
        # no plate panel (recipe `plate: false`): the place is in words
        self.assertFalse(any(p.get("location") for x in doc["shots"] for p in x["panels"]))
        self.assertIn("sh020", rep["blocked_shots"][s["panels"][0]["path"]])
        self.assertEqual(rep["needed"][s["panels"][0]["path"]]["kind"], "character sheet")
        story = self.story
        reqs = t.required_refs(next(x for x in story.shots() if x.id == "sh020"), self.cfg)
        self.assertEqual([r.subject for r in reqs], ["ada", "bo", "kettle"])


class PromptTest(unittest.TestCase):
    def setUp(self):
        self.cfg, self.story = kitchen_sink()
        doc, _ = TG.load_target(I2V).compile_episode(self.story, self.cfg, "final")
        self.shots = {s["id"]: s for s in doc["shots"]}

    def test_prose_without_sound(self):
        for s in self.shots.values():
            p = s["prompt"]
            self.assertTrue(p.startswith("Style: "), s["id"])
            for word in ("sound", "music", "says", "voice", '"'):
                if word == '"' and "on-screen text" in p:
                    continue
                self.assertNotIn(word, p.lower() if word != '"' else p, (s["id"], word))

    def test_lines_are_acted(self):
        p = self.shots["sh020"]["prompt"]
        self.assertIn("Ada talks, warmly, mouth moving with the words.", p)
        self.assertIn("Bo talks, mouth moving with the words.", p)
        self.assertIn("the camera remains static", p)
        # the same prose on every Wan target
        for tid in (TI2V, VACE):
            doc, _ = TG.load_target(tid).compile_episode(self.story, self.cfg, "final")
            self.assertEqual({s["id"]: s["prompt"] for s in doc["shots"]},
                             {k: v["prompt"] for k, v in self.shots.items()})

    def test_voiceover_keeps_lips_closed(self):
        from h3core.ir import Line, Shot
        shot = Shot(id="x", cast=["ada"], dialogue=[
            Line(speaker="ada", line="I knew.", mode="vo"),
            Line(speaker="bo", line="Hey!", mode="os"),
            Line(speaker="ada", line="One.", delivery="quietly"),
            Line(speaker="ada", line="Two."),
        ])
        book = {"ada": {"name": "Ada"}, "bo": {"name": "Bo"}}
        self.assertEqual(WP.acting(shot, book),
                         ["Ada stays silent, lips closed.",
                          "Ada talks, quietly, mouth moving with the words."])


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
        cls.base = {tid: J.load_graph(os.path.join(ROOT, "targets", "video", tid, "workflow.json"))
                    for tid in WAN}

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.root = os.path.join(self._t.name, "ks01")
        shutil.copytree(self.src, self.root)

    def tearDown(self):
        self._t.cleanup()

    def plan(self, sid, tid, pass_="proxy", **req):
        doc, i = J.find_shot(self.root, pass_, sid)
        return J.plan_job(self.root, pass_, doc, i, J.RenderRequest(sid, target=tid, **req), {})

    def keyframe(self, sid, end, color=(0, 0, 200)):
        p = os.path.join(self.root, "refs", "shots", sid, f"{end}.png")
        if Image is not None:
            png(p, (96, 64), (color,))
        else:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "wb") as fh:
                fh.write(f"{sid} {end}".encode())

    def real_refs(self):
        cfg = json.load(open(os.path.join(self.root, "series.json"), encoding="utf-8"))
        for e in cfg["subjects"].values():
            if isinstance(e, dict) and e.get("sheet"):
                if e.get("kind", "character") == "character":
                    png(os.path.join(self.root, e["sheet"]), (256, 64),
                        ((255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)))
                else:
                    png(os.path.join(self.root, e["sheet"]), (48, 48), ((20, 20, 20),))

    def stage_and_start(self, job):
        comfy = FakeComfy()
        try:
            got = J.stage_inputs(job, J.Comfy(comfy.url))
            take = J.start_job(job)
        finally:
            comfy.close()
        return got, take, comfy

    def graph(self, job, take):
        g = J.graph_for(self.base[job.target], job, take)
        self.assertEqual(J.check_graph(g, OBJECT_INFO), [])
        return g

    def saver(self, g, fps, policy="silent"):
        sv = g[J.node_of(g, "H3SaveShot")]["inputs"]
        self.assertEqual((sv["fps"], sv["audio_policy"], sv["save_frames"]), (fps, policy, False))
        self.assertNotIn("audio", sv)                        # Wan has none: a mute mp4
        for gone in ("CreateVideo", "SaveVideo"):
            self.assertFalse(of(g, gone))
        return sv

    # -- wan22_i2v -------------------------------------------------------------

    def test_i2v_blocked_without_a_first_frame(self):
        why = "Wan 14B I2V needs a first frame: use continuity or import one, or retarget to wan22_ti2v"
        job = self.plan("sh010", I2V)
        self.assertEqual(job.action, "blocked")
        self.assertEqual([(r["slot"], r["anyway"], r["why"]) for r in job.missing],
                         [("first frame", False, why)])
        self.assertEqual(job.blocked_reason(), why)
        # rendering anyway isn't offered: still blocked, nothing recompiled
        job = self.plan("sh010", I2V, allow_missing_refs=True)
        self.assertEqual((job.action, job.recompiled, job.missing_mode), ("blocked", None, ""))
        # the routes' queue skips it with the reason (no ComfyUI is touched)
        out = E.queue_shots(self.root, "proxy", ["sh010"],
                            J.RenderRequest("", target=I2V, allow_missing_refs=True), None, {})
        self.assertEqual(out["queued"], [])
        self.assertEqual(out["skipped"][0]["reason"], why)
        # the episode status tells the editor it can't render anyway
        ov = T.load_overrides(self.root)
        T.set_shot_target(ov, "sh010", I2V)
        T.save_overrides(self.root, ov)
        st = {s["shot"]: s for s in E.episode_status(self.root, "proxy")["shots"]}
        self.assertEqual(st["sh010"]["missing_refs"],
                         [{"slot": "first frame", "kind": "image",
                           "path": "refs/shots/sh010/first.png", "anyway": False, "why": why}])
        # the pipeline stays text-free elsewhere: a last frame alone doesn't help
        self.keyframe("sh010", "last")
        self.assertEqual(self.plan("sh010", I2V).action, "blocked")

    def test_i2v_first_frame(self):
        self.keyframe("sh010", "first")
        job = self.plan("sh010", I2V)
        self.assertEqual((job.action, job.target, job.built_target), ("render", I2V, H3))
        self.assertEqual([lo["name"] for lo in job.loras], [HIGH, LOW])
        self.assertTrue(any("renders as silent" in n for n in job.notes))
        got, take, comfy = self.stage_and_start(job)
        self.assertEqual(list(got), ["first"])
        g = self.graph(job, take)
        i2v = g[J.node_of(g, "WanImageToVideo")]["inputs"]
        self.assertEqual((i2v["width"], i2v["height"], i2v["length"]), (640, 352, 41))
        self.assertEqual(g[i2v["start_image"][0]]["inputs"]["image"], got["first"])
        self.assertNotIn("end_image", i2v)
        # two stages: each model with its own LoRA and shift, samplers split 2/2
        hi = titled(g, "UNETLoader", "Wan high noise model")
        lo = titled(g, "UNETLoader", "Wan low noise model")
        self.assertEqual((hi["unet_name"], lo["unet_name"]),
                         ("wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors",
                          "wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors"))
        self.assertEqual(titled(g, "LoraLoaderModelOnly", "Wan high noise LoRA")["lora_name"], HIGH)
        self.assertEqual(titled(g, "LoraLoaderModelOnly", "Wan low noise LoRA")["lora_name"], LOW)
        s1 = titled(g, "KSamplerAdvanced", "Wan high noise sampler")
        s2 = titled(g, "KSamplerAdvanced", "Wan low noise sampler")
        self.assertEqual((s1["steps"], s1["start_at_step"], s1["end_at_step"], s1["add_noise"],
                          s1["return_with_leftover_noise"], s1["cfg"]),
                         (4, 0, 2, "enable", "enable", 1.0))
        self.assertEqual((s2["start_at_step"], s2["end_at_step"], s2["add_noise"]),
                         (2, 10000, "disable"))
        self.assertEqual(s1["noise_seed"], job.seed)
        self.assertEqual(s2["latent_image"], [of(g, "KSamplerAdvanced", "Wan high noise sampler")[0], 0])
        for s, stage in ((s1, "high"), (s2, "low")):
            shift = g[s["model"][0]]
            self.assertEqual(shift["class_type"], "ModelSamplingSD3")
            lora = g[shift["inputs"]["model"][0]]
            self.assertEqual(lora["_meta"]["title"], f"Wan {stage} noise LoRA")
        self.assertEqual(titled(g, "CLIPTextEncode", "Positive prompt")["text"], job.prompt)
        self.assertTrue(titled(g, "CLIPTextEncode", "Negative prompt")["text"].startswith("色调艳丽"))
        self.saver(g, 16.0)
        sc = T.read_sidecar(take.paths.sidecar)
        self.assertEqual((sc["fps"], sc["length"], sc["target"]), (16.0, 41, I2V))
        self.assertTrue(any("renders as silent" in n for n in sc["notes"]))
        first = next(r for r in sc["refs"] if r["slot"] == "first frame")
        self.assertEqual(first["sha1"], T.file_sha1(os.path.join(self.root, first["path"])))

    def test_i2v_first_and_last(self):
        self.keyframe("sh010", "first")
        self.keyframe("sh010", "last", (200, 0, 0))
        job = self.plan("sh010", I2V, pass_="final")
        got, take, _ = self.stage_and_start(job)
        self.assertEqual(sorted(got), ["first", "last"])
        g = self.graph(job, take)
        self.assertFalse(of(g, "WanImageToVideo"))
        fl = g[J.node_of(g, "WanFirstLastFrameToVideo")]["inputs"]
        self.assertEqual(g[fl["start_image"][0]]["inputs"]["image"], got["first"])
        self.assertEqual(g[fl["end_image"][0]]["inputs"]["image"], got["last"])
        self.assertEqual((fl["width"], fl["height"]), (832, 480))

    def test_i2v_lora_chains_by_stage(self):
        self.keyframe("sh010", "first")
        loras = [{"name": "style.safetensors", "strength": 0.6},
                 {"name": "fast_high_noise.safetensors", "strength": 1.0},
                 {"name": "fast_low_noise.safetensors", "strength": 1.0},
                 {"name": "x.safetensors", "strength": 0.5, "stage": "low"}]
        job = self.plan("sh010", I2V, loras=loras, steps=8)
        J.stage_inputs(job)
        take = T.Take(job.id, job.take, job.pass_, T.take_paths(self.root, job.pass_, job.id, job.take))
        g = self.graph(job, take)

        def chain(stage):
            s = titled(g, "KSamplerAdvanced", f"Wan {stage} noise sampler")
            node, names = g[g[s["model"][0]]["inputs"]["model"][0]], []
            while node["class_type"] == "LoraLoaderModelOnly":
                names.append(node["inputs"]["lora_name"])
                node = g[node["inputs"]["model"][0]]
            self.assertEqual(node["_meta"]["title"], f"Wan {stage} noise model")
            return names[::-1]

        self.assertEqual(chain("high"), ["style.safetensors", "fast_high_noise.safetensors"])
        self.assertEqual(chain("low"), ["style.safetensors", "fast_low_noise.safetensors",
                                        "x.safetensors"])
        s1 = titled(g, "KSamplerAdvanced", "Wan high noise sampler")
        self.assertEqual((s1["steps"], s1["end_at_step"]), (8, 4))
        # no LoRAs at all: both loaders stay wired at strength 0
        job = self.plan("sh010", I2V, loras=[])
        J.stage_inputs(job)
        g = self.graph(job, take)
        for stage in ("high", "low"):
            self.assertEqual(titled(g, "LoraLoaderModelOnly",
                                    f"Wan {stage} noise LoRA")["strength_model"], 0.0)
        self.assertEqual(J.lora_stage({"name": "A-High-Noise.safetensors"}), "high")
        self.assertEqual(J.lora_stage({"name": "plain.safetensors"}), None)

    # -- wan22_ti2v ------------------------------------------------------------

    def test_ti2v_text_only(self):
        job = self.plan("sh020", TI2V)
        self.assertEqual((job.action, job.missing), ("render", []))
        got, take, comfy = self.stage_and_start(job)
        self.assertEqual((got, comfy.uploads), ({}, {}))
        g = self.graph(job, take)
        self.assertFalse(of(g, "LoadImage"))
        lat = g[J.node_of(g, "Wan22ImageToVideoLatent")]["inputs"]
        self.assertNotIn("start_image", lat)
        self.assertEqual((lat["width"], lat["height"], lat["length"]), (640, 352, 89))
        ks = g[J.node_of(g, "KSampler")]["inputs"]
        self.assertEqual((ks["steps"], ks["cfg"], ks["sampler_name"], ks["seed"]),
                         (12, 5.0, "uni_pc", job.seed))
        self.assertEqual(g[J.node_of(g, "UNETLoader")]["inputs"]["unet_name"],
                         "wan2.2_ti2v_5B_fp16.safetensors")
        self.assertFalse(of(g, "LoraLoaderModelOnly"))
        self.saver(g, 24.0)
        self.assertEqual(T.read_sidecar(take.paths.sidecar)["fps"], 24.0)

    def test_ti2v_first_frame(self):
        self.keyframe("sh020", "first")
        job = self.plan("sh020", TI2V, pass_="final")
        got, take, _ = self.stage_and_start(job)
        g = self.graph(job, take)
        lat = g[J.node_of(g, "Wan22ImageToVideoLatent")]["inputs"]
        self.assertEqual(g[lat["start_image"][0]]["inputs"]["image"], got["first"])
        self.assertEqual((lat["width"], lat["height"]), (1280, 704))
        # a profile's LoRA goes in after the UNETLoader
        job = self.plan("sh020", TI2V, loras=[{"name": "s.safetensors", "strength": 0.7}])
        J.stage_inputs(job)
        g = self.graph(job, take)
        lo = g[J.node_of(g, "LoraLoaderModelOnly")]["inputs"]
        self.assertEqual((lo["lora_name"], g[lo["model"][0]]["class_type"]),
                         ("s.safetensors", "UNETLoader"))

    # -- wan22_vace ------------------------------------------------------------

    def test_vace_blocked_on_missing_refs(self):
        job = self.plan("sh020", VACE)
        self.assertEqual(job.action, "blocked")
        self.assertEqual([r["subject"] for r in job.missing], ["ada", "bo", "kettle"])
        self.assertIn("render anyway", job.blocked_reason())
        job = self.plan("sh020", VACE, allow_missing_refs=True)
        self.assertEqual((job.action, job.missing_mode), ("render", "recompiled"))
        self.assertEqual(job.recompiled["panels"], [])
        J.stage_inputs(job)
        self.assertEqual(job.inputs, {})
        self.assertTrue(any("from the prompt alone" in n for n in job.notes))
        take = T.Take(job.id, job.take, job.pass_, T.take_paths(self.root, job.pass_, job.id, job.take))
        g = self.graph(job, take)
        self.assertNotIn("reference_image", g[J.node_of(g, "WanVaceToVideo")]["inputs"])
        self.assertFalse(of(g, "LoadImage"))

    @needs_pil
    def test_vace_reference_image(self):
        self.real_refs()
        job = self.plan("sh020", VACE)
        self.assertEqual(job.action, "render")
        # a dry run composes nothing
        self.assertTrue(J.stage_inputs(job)["reference"].endswith("_reference.png"))
        self.assertEqual(job.staged, [])
        got, take, comfy = self.stage_and_start(job)
        self.assertEqual(list(got), ["reference"])
        kept = os.path.join(take.paths.dir, take.paths.stem + "_reference.png")
        self.assertEqual(comfy.uploads[got["reference"]], open(kept, "rb").read())
        img = Image.open(kept).convert("RGB")
        self.assertEqual(img.size, (640, 352))
        # on white: two body panels (red, the strip's first), then the kettle
        self.assertEqual(img.getpixel((0, 0)), (255, 255, 255))
        colors = {c for _, c in img.getcolors(640 * 352)}
        self.assertIn((255, 0, 0), colors)
        self.assertIn((20, 20, 20), colors)
        self.assertNotIn((0, 255, 0), colors)                    # one view per character
        sc = T.read_sidecar(take.paths.sidecar)
        refs = {r["slot"]: r for r in sc["refs"]}
        self.assertEqual(refs["reference image"]["sha1"], T.file_sha1(kept))
        self.assertEqual(refs["reference panel 1"]["subject"], "ada")
        self.assertEqual(sc["inputs"], got)
        g = self.graph(job, take)
        vace = g[J.node_of(g, "WanVaceToVideo")]["inputs"]
        self.assertEqual(g[vace["reference_image"][0]]["inputs"]["image"], got["reference"])
        self.assertEqual((vace["width"], vace["height"], vace["length"], vace["strength"]),
                         (640, 352, 61, 1.0))
        self.assertNotIn("control_video", vace)
        trim = g[J.node_of(g, "TrimVideoLatent")]["inputs"]
        self.assertEqual(trim["trim_amount"], [J.node_of(g, "WanVaceToVideo"), 3])
        s1 = titled(g, "KSamplerAdvanced", "Wan high noise sampler")
        self.assertEqual((s1["steps"], s1["end_at_step"], s1["cfg"]), (10, 5, 3.5))
        self.assertFalse(of(g, "LoraLoaderModelOnly"))
        self.saver(g, 16.0)
        # re-picking a view makes the take ref-stale
        png(os.path.join(self.root, "refs", "ada", "ada_sheet_4panel.png"), (256, 64), ((1, 2, 3),))
        doc, i = J.find_shot(self.root, "proxy", "sh020")
        cur = J.current_entry(self.root, "proxy", doc, doc["shots"][i], VACE)
        self.assertIn("ref", J.stale_reasons(self.root, cur[0], cur[1], sc))

    @needs_pil
    def test_vace_keyframes_are_control_frames(self):
        self.real_refs()
        self.keyframe("sh020", "first")
        self.keyframe("sh020", "last", (200, 0, 0))
        job = self.plan("sh020", VACE)
        got, take, _ = self.stage_and_start(job)
        self.assertEqual(sorted(got), ["first", "last", "reference"])
        g = self.graph(job, take)
        vace = g[J.node_of(g, "WanVaceToVideo")]["inputs"]

        def flatten(nid):
            n = g[nid]
            if n["class_type"] == "ImageBatch":
                return flatten(n["inputs"]["image1"][0]) + flatten(n["inputs"]["image2"][0])
            return [n]

        video = flatten(vace["control_video"][0])
        self.assertEqual([n["class_type"] for n in video], ["ImageScale", "EmptyImage", "ImageScale"])
        self.assertEqual(g[video[0]["inputs"]["image"][0]]["inputs"]["image"], got["first"])
        self.assertEqual(g[video[2]["inputs"]["image"][0]]["inputs"]["image"], got["last"])
        self.assertEqual((video[1]["inputs"]["batch_size"], video[1]["inputs"]["color"]),
                         (59, 0x7F7F7F))
        mask = g[vace["control_masks"][0]]
        self.assertEqual((mask["class_type"], mask["inputs"]["channel"]), ("ImageToMask", "red"))
        frames = flatten(mask["inputs"]["image"][0])
        self.assertEqual([(n["inputs"]["batch_size"], n["inputs"]["color"]) for n in frames],
                         [(1, 0), (59, 0xFFFFFF), (1, 0)])
        # first only: the keyframe, then grey to the end
        os.remove(os.path.join(self.root, "refs", "shots", "sh020", "last.png"))
        job = self.plan("sh020", VACE, redo=True)
        J.stage_inputs(job)
        g = self.graph(job, take)
        vace = g[J.node_of(g, "WanVaceToVideo")]["inputs"]
        self.assertEqual([n["class_type"] for n in flatten(vace["control_video"][0])],
                         ["ImageScale", "EmptyImage"])
        self.assertEqual(flatten(vace["control_video"][0])[1]["inputs"]["batch_size"], 60)


# ---------------------------------------------------------------------------
# assemble: a 16 fps take in a 24 fps cut
# ---------------------------------------------------------------------------

def ff(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(list(args), capture_output=True, text=True, timeout=120)


def make_clip(path: str, frames: int, fps: int, size: str = "64x64", sound: bool = True) -> None:
    """testsrc at `fps`, exactly `frames` frames, with a sine track or mute."""
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc=size={size}:rate={fps}"]
    if sound:
        cmd += ["-f", "lavfi", "-t", f"{frames / fps:.6f}", "-i",
                "sine=frequency=440:sample_rate=44100"]
    cmd += ["-frames:v", str(frames), "-c:v", "libx264", "-preset", "ultrafast",
            "-pix_fmt", "yuv420p", "-r", str(fps)]
    cmd += ["-c:a", "aac"] if sound else []
    r = ff(*cmd, path)
    if r.returncode != 0:
        raise RuntimeError(r.stderr)


def probe(path: str, stream: str, entry: str) -> str:
    return ff("ffprobe", "-v", "error", "-select_streams", stream, "-count_frames",
              "-show_entries", f"stream={entry}", "-of", "csv=p=0", path).stdout.strip()


@unittest.skipUnless(HAVE_FF, "ffmpeg/ffprobe not on PATH")
class AssembleFpsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def take(self, shot: str, frames: int, fps: int, size="64x64", sound=True, record_fps=True):
        t = T.reserve_take(self.root, "final", shot, {"status": "queued", "length": frames,
                                                      **({"fps": float(fps)} if record_fps else {})})
        make_clip(t.paths.mp4, frames, fps, size, sound)
        T.update_sidecar(t.paths.sidecar, status="ok", frames=frames)

    def assemble(self):
        r = subprocess.run([sys.executable, os.path.join(ROOT, "h3assemble.py"), "-o", self.root],
                           capture_output=True, text=True, encoding="utf-8", timeout=300)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r.stdout

    def test_mixed_rates_join_by_duration(self):
        shots = [("a", 48), ("w", 81), ("b", 24), ("v", 33)]
        T.write_json(os.path.join(self.root, "shotlist", "shotlist.json"),
                     {"episode": "ep01", "defaults": {"width": 64, "height": 64, "fps": 24},
                      "shots": [{"id": s, "length": n, "audio_policy": "generate"}
                                for s, n in shots]})
        self.take("a", 48, 24)
        self.take("w", 81, 16, size="32x32", sound=False)       # Wan 14B: 16 fps, mute, smaller
        self.take("b", 24, 24)
        self.take("v", 33, 16, sound=False, record_fps=False)   # no fps in the sidecar: probed
        out = self.assemble()
        self.assertIn("converting 2 clip(s) from 16 fps to 24 fps", out)
        self.assertIn("scaled to 64x64: w 32x32", out)
        self.assertNotIn("mismatch", out)
        # 2 s + 5.0625 s + 1 s + 2.0625 s = 10.125 s = 243 frames at 24 fps, each
        # clip rounded on the running clock (170 - 48 = 122 for w; 243 - 194 = 49 for v)
        mp4 = os.path.join(self.root, "renders", "ep01.mp4")
        self.assertEqual(int(probe(mp4, "v:0", "nb_read_frames")), 243)
        self.assertEqual(probe(mp4, "v:0", "r_frame_rate"), "24/1")
        self.assertEqual(probe(mp4, "v:0", "width,height"), "64,64")
        with open(os.path.join(self.root, "renders", "ep01_shots.txt"), encoding="utf-8") as fh:
            rows = [ln.split() for ln in fh if ln.strip() and not ln.startswith("#")]
        self.assertEqual([(r[1], r[3]) for r in rows],
                         [("a", "48"), ("w", "122"), ("b", "24"), ("v", "49")])
        self.assertEqual([r[0] for r in rows],
                         ["00:00:00.000", "00:00:02.000", "00:00:07.083", "00:00:08.083"])
        self.assertIn("from 16fps", " ".join(rows[1]))
        # sync: the sound runs the whole cut (silence under the mute Wan clips)
        dur = float(ff("ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
                       "stream=duration", "-of", "csv=p=0", mp4).stdout.strip())
        self.assertAlmostEqual(dur, 243 / 24, delta=0.05)


if __name__ == "__main__":
    if "--update" in sys.argv:
        update()
    else:
        unittest.main()
