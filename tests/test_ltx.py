"""
Phase 8: LTX-2 as a second video target, and retargeting shots.

  - h3jobs.ui_to_api flattens ComfyUI subgraphs: the user's three saved LTX
    workflows (tests/fixtures/workflows, copied from ComfyUI's user folder)
    convert to graphs whose every link resolves and every required input is
    set, checked against a trimmed /object_info of the same install.
  - The ltx2 target: template, presets, audio capability, prose prompt, and
    the patched graph (text-to-video, first / last keyframes).
  - Episodes that mix targets (tests/fixtures/mixed): per-target shotlists,
    finding a shot in whichever holds it, status and detail merged.
  - Retargeting without a rebuild: overrides.json `target`, a request's
    `target`, the routes and `h3.py override --target`.
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
sys.path.insert(0, os.path.join(ROOT, "comfy_nodes"))
import h3edit as E  # noqa: E402
import h3jobs as J  # noqa: E402
import h3takes as T  # noqa: E402
import targets as TG  # noqa: E402
from h3core import ir  # noqa: E402
from test_render import ENV, FakeComfy, stub_refs  # noqa: E402

WF = os.path.join(HERE, "fixtures", "workflows")
MIXED = os.path.join(HERE, "fixtures", "mixed")
H3, LTX = "minimax_h3_ref2va", "ltx2"
OBJECT_INFO = json.load(open(os.path.join(WF, "object_info_ltx.json"), encoding="utf-8"))
SAVED = ["video_ltx2_5_i2v.json", "video_ltx2_3_i2v.json",
         "template_ltx2_3_ic_lora_ingredients.json"]
PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02"
       b"\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe"
       b"\xa7\x35\x81\x84\x00\x00\x00\x00IEND\xaeB`\x82")


def build_mixed(root: str) -> None:
    shutil.copy(os.path.join(MIXED, "series.json"), root)
    for flags in ([], ["--proxy"]):
        subprocess.run([sys.executable, os.path.join(ROOT, "h3build.py"),
                        os.path.join(root, "series.json"), os.path.join(MIXED, "script.md"),
                        "-o", root, *flags], check=True, capture_output=True, env=ENV)
    stub_refs(root)


def of(g: dict, ctype: str) -> list[str]:
    return [k for k, v in g.items() if v["class_type"] == ctype]


# ---------------------------------------------------------------------------
# subgraphs
# ---------------------------------------------------------------------------

class SubgraphTest(unittest.TestCase):
    def test_saved_ltx_workflows_flatten_well_formed(self):
        for name in SAVED:
            with self.subTest(workflow=name):
                g = J.load_graph(os.path.join(WF, name))
                self.assertEqual(J.check_graph(g, OBJECT_INFO), [])
                self.assertFalse(any(v["class_type"] in ("MarkdownNote", "Reroute",
                                                        "PrimitiveNode") for v in g.values()))
                # nothing is left of the subgraph instance itself
                self.assertFalse(any(len(v["class_type"]) == 36 and v["class_type"].count("-") == 4
                                     for v in g.values()))

    def test_ltx25_promoted_widgets_and_links(self):
        g = J.load_graph(os.path.join(WF, "video_ltx2_5_i2v.json"))
        # the instance's own widget values win over the inner nodes' (format 1)
        self.assertTrue(g["398:376"]["inputs"]["value"].startswith("Use the provided start image"))
        self.assertEqual(g["398:339"]["inputs"]["noise_seed"], 689064419517969)
        self.assertEqual(g["398:384"]["inputs"]["unet_name"],
                         "ltx-2.5-22b-distilled-transformer-comfy-int8-convrot.safetensors")
        # an instance input fed from outside: the inner primitive reads the outer node
        self.assertEqual(g["398:372"]["inputs"]["value"], ["403", 0])
        # the subgraph's output is the inner CreateVideo
        self.assertEqual(g["75"]["inputs"]["video"], ["398:370", 0])
        # a primitive feeding a socket is kept; one feeding only widgets is inlined
        self.assertEqual(g["398:353"]["inputs"]["values.a"], ["398:372", 0])
        self.assertEqual(g["398:382"]["inputs"]["switch"], True)
        self.assertNotIn("398:383", g)

    def test_ltx23_proxy_widgets_and_reroute(self):
        g = J.load_graph(os.path.join(WF, "video_ltx2_3_i2v.json"))
        # proxyWidgets: the instance keeps no values, the inner widgets hold them
        self.assertEqual(g["320:312"]["inputs"]["value"], 1280)
        self.assertEqual(g["320:285"]["inputs"]["strength_model"], 0.75)
        # the Reroute between the checkpoint's VAE and three nodes is resolved
        self.assertEqual(g["320:288"]["inputs"]["vae"], ["320:316", 2])
        self.assertEqual(g["320:287"]["inputs"]["vae"], ["320:316", 2])

    def test_ingredients_unfed_input_reaches_sockets(self):
        g = J.load_graph(os.path.join(WF, "template_ltx2_3_ic_lora_ingredients.json"))
        # the prompt input feeds a widget AND a switch's socket: both get the value
        sw = g["129:211"]["inputs"]
        self.assertTrue(sw["on_false"].startswith("2D hand-drawn cartoon animation"))
        self.assertEqual(sw["on_false"], g["129:209"]["inputs"]["prompt"])
        # top-level primitives feeding the subgraph
        self.assertEqual(g["129:114"]["inputs"]["value"], 25)
        self.assertEqual(g["129:108"]["inputs"]["width"], ["721", 0])
        # a bypassed output node (PreviewImage) drops out
        self.assertFalse(of(g, "PreviewImage"))

    def _tiny(self):
        """A subgraph (x2 math on its input), nested inside another."""
        inner = {"id": "inner-sg", "name": "inner", "inputs": [{"name": "v", "linkIds": [2]}],
                 "outputs": [{"name": "out", "linkIds": [3]}],
                 "nodes": [{"id": 1, "type": "Mul", "inputs": [
                     {"name": "a", "link": 2}, {"name": "k", "widget": {"name": "k"}, "link": None}],
                     "outputs": [{"name": "o", "type": "INT"}], "widgets_values": [2]}],
                 "links": [{"id": 2, "origin_id": -10, "origin_slot": 0, "target_id": 1,
                            "target_slot": 0, "type": "INT"},
                           {"id": 3, "origin_id": 1, "origin_slot": 0, "target_id": -20,
                            "target_slot": 0, "type": "INT"}]}
        outer = {"id": "outer-sg", "name": "outer",
                 "inputs": [{"name": "v", "linkIds": [5]}, {"name": "same", "linkIds": [7]}],
                 "outputs": [{"name": "out", "linkIds": [6]}, {"name": "thru", "linkIds": [7]}],
                 "nodes": [{"id": 4, "type": "inner-sg", "inputs": [{"name": "v", "link": 5}],
                            "outputs": [{"name": "out", "type": "INT"}]}],
                 "links": [{"id": 5, "origin_id": -10, "origin_slot": 0, "target_id": 4,
                            "target_slot": 0, "type": "INT"},
                           {"id": 6, "origin_id": 4, "origin_slot": 0, "target_id": -20,
                            "target_slot": 0, "type": "INT"},
                           {"id": 7, "origin_id": -10, "origin_slot": 1, "target_id": -20,
                            "target_slot": 1, "type": "INT"}]}
        return {"nodes": [
            {"id": 10, "type": "Src", "inputs": [], "outputs": [{"name": "o", "type": "INT"}]},
            {"id": 11, "type": "outer-sg", "inputs": [{"name": "v", "link": 20},
                                                      {"name": "same", "link": 23}],
             "outputs": [{"name": "out"}, {"name": "thru"}]},
            {"id": 12, "type": "Sink", "inputs": [{"name": "x", "link": 21},
                                                  {"name": "y", "link": 22}], "outputs": []}],
            "links": [[20, 10, 0, 11, 0, "INT"], [21, 11, 0, 12, 0, "INT"],
                      [22, 11, 1, 12, 1, "INT"], [23, 10, 0, 11, 1, "INT"]],
            "definitions": {"subgraphs": [inner, outer]}}

    def test_nested_and_pass_through(self):
        g = J.ui_to_api(self._tiny())
        self.assertEqual(g["11:4:1"], {"class_type": "Mul", "inputs": {"a": ["10", 0], "k": 2},
                                       "_meta": {"title": "Mul"}})
        self.assertEqual(g["12"]["inputs"], {"x": ["11:4:1", 0], "y": ["10", 0]})
        self.assertEqual(J.check_graph(g), [])

    def test_bypassed_nodes(self):
        ui = self._tiny()
        ui["nodes"][1]["mode"] = 4
        with self.assertRaises(J.WorkflowError) as cm:
            J.ui_to_api(ui)
        self.assertIn("bypassed", str(cm.exception))
        # a bypassed plain node passes its matching input through
        ui = {"nodes": [{"id": 1, "type": "Src", "outputs": [{"name": "o", "type": "IMAGE"}]},
                        {"id": 2, "type": "Blur", "mode": 4,
                         "inputs": [{"name": "image", "type": "IMAGE", "link": 1}],
                         "outputs": [{"name": "o", "type": "IMAGE"}]},
                        {"id": 3, "type": "Save", "inputs": [{"name": "images", "link": 2}]},
                        {"id": 4, "type": "Mask", "mode": 4,
                         "inputs": [{"name": "image", "type": "IMAGE", "link": 3}],
                         "outputs": [{"name": "m", "type": "MASK"}]},
                        {"id": 5, "type": "Save", "inputs": [{"name": "m", "link": 4}]}],
              "links": [[1, 1, 0, 2, 0, "IMAGE"], [2, 2, 0, 3, 0, "IMAGE"],
                        [3, 1, 0, 4, 0, "IMAGE"], [4, 4, 0, 5, 0, "MASK"]]}
        with self.assertRaises(J.WorkflowError) as cm:     # no MASK input to pass through
            J.ui_to_api(ui)
        self.assertIn("node 4 (Mask) is bypassed and has no MASK input", str(cm.exception))
        del ui["nodes"][3:]
        ui["links"] = ui["links"][:2]
        g = J.ui_to_api(ui)
        self.assertEqual(g["3"]["inputs"], {"images": ["1", 0]})
        self.assertNotIn("2", g)

    def test_h3_workflow_unchanged_by_the_converter(self):
        # H3's workflow has no subgraphs; the snapshot test pins its graph,
        # this pins the converter's handling of its PrimitiveInt (a widget feed)
        g = J.load_graph(os.path.join(ROOT, "targets", "video", H3, "workflow.json"))
        self.assertFalse(of(g, "PrimitiveInt"))
        J.node_of(g, "H3ShotListLoader")                      # exactly one


# ---------------------------------------------------------------------------
# the ltx2 target
# ---------------------------------------------------------------------------

class LtxTargetTest(unittest.TestCase):
    def setUp(self):
        self.t = TG.load_target(LTX, "video")

    def test_template(self):
        tp = self.t.template
        self.assertEqual([tp.snap(n) for n in (1, 2, 9, 10, 97, 98)], [1, 9, 9, 17, 97, 105])
        self.assertEqual(tp.fps_for({"series": {"fps": 25}}), 25.0)
        self.assertEqual(tp.fps_for({"series": {}}), 24.0)
        self.assertEqual(tp.fit_size(768, 512), (768, 512))
        self.assertEqual(tp.fit_size(480, 272), (448, 256))          # H3's proxy size
        self.assertEqual(tp.fit_size(1344, 768), (1344, 768))
        self.assertEqual(tp.fit_size(1920, 1080), (1344, 768))       # under 1 MP, /64
        self.assertEqual(tp.fit_size(2048, 2048), (1024, 1024))
        with self.assertRaises(ValueError):
            tp.snap(500)

    def test_presets_ignore_another_targets_series_block(self):
        cfg = {"series": {"model": "h3.safetensors", "lora": "turbo.safetensors", "steps": 4,
                          "width": 1344, "height": 768},
               "proxy": {"width": 480, "height": 272, "steps": 4, "lora": "x"}}
        p = self.t.preset("final", cfg)
        self.assertEqual((p.model, p.lora, p.steps, p.width, p.height),
                         (self.t.presets["final"].model, None, 8, 1344, 768))
        p = self.t.preset("proxy", cfg)
        self.assertEqual((p.model, p.lora, p.steps, p.width, p.height),
                         (self.t.presets["final"].model, None, 8, 480, 272))
        self.assertIn("negative", p.extra)
        # ...but on a series that renders on ltx2, the block is its own
        cfg["series"]["target"] = LTX
        self.assertEqual(self.t.preset("final", cfg).model, "h3.safetensors")
        # and H3's own presets are what they always were
        h3 = TG.load_target(H3, "video")
        self.assertEqual(h3.preset("final", cfg).model, h3.presets["final"].model)

    def test_audio_is_a_declared_capability(self):
        self.assertEqual(self.t.audio_policy("generate"), ("generate", ""))
        pol, note = self.t.audio_policy("dub_keep_foley")
        self.assertEqual(pol, "generate")
        self.assertIn("no voice sample or recording", note)
        h3 = TG.load_target(H3, "video")
        self.assertEqual(h3.audio_policy("clone"), ("clone", ""))
        spec = dict(h3.recipe)
        try:
            h3.recipe = dict(spec, policies=["generate"])
            with self.assertRaises(ValueError):
                h3.audio_policy("clone")                      # no fallback declared
        finally:
            h3.recipe = spec

    def test_prose_prompt(self):
        from targets.video.ltx2.prompt import build_prompt
        cfg = json.load(open(os.path.join(MIXED, "series.json"), encoding="utf-8"))
        sq = ir.Sequence("sq01", "kitchen")
        shot = ir.Shot("sh1", cast=["ada"], props=["kettle"], size="close",
                       camera="slowly pushes in", action="Ada lifts the kettle.",
                       dialogue=[ir.Line("ada", "on", "warmly", "Tea?"),
                                 ir.Line("narrator", "vo", "", "She always asks."),
                                 ir.Line("ada", "vo", "", "Always.")],
                       sound="a kettle whistle", music="a soft piano")
        p = build_prompt(shot, sq, cfg)
        self.assertTrue(p.startswith("Style: a flat vector cartoon"))
        self.assertIn("A close-up of a cramped diner kitchen", p)
        self.assertIn("the camera slowly pushes in.", p)
        self.assertIn("Ada is a tall woman", p)
        self.assertIn('Ada, in a voice that is dry, quick and precise, says, warmly: "Tea?"', p)
        self.assertIn("says in an off-screen voiceover: \"She always asks.\"", p)
        self.assertIn("Ada says in an off-screen voiceover, her lips staying closed: \"Always.\"", p)
        self.assertIn("The sound is a kettle whistle.", p)
        self.assertIn("A soft piano plays underneath.", p)
        self.assertNotIn("<", p)
        self.assertNotIn("\n", p)                                 # one paragraph
        # camera first, then subjects and action, then audio
        self.assertLess(p.index("the camera"), p.index("Ada is"))
        self.assertLess(p.index("Ada lifts"), p.index("The sound"))
        static = build_prompt(ir.Shot("sh2", action="Rain."), sq, cfg)
        self.assertIn("the camera remains static", static)
        self.assertIn("There is no music.", static)


# ---------------------------------------------------------------------------
# a mixed episode
# ---------------------------------------------------------------------------

class MixedEpisodeTest(unittest.TestCase):
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
        self.base = J.load_graph(os.path.join(ROOT, "targets", "video", LTX, "workflow.json"))

    def tearDown(self):
        self._t.cleanup()

    def plan(self, sid, pass_="proxy", ov=None, **req):
        doc, i = J.find_shot(self.root, pass_, sid)
        return J.plan_job(self.root, pass_, doc, i, J.RenderRequest(sid, **req),
                          T.load_overrides(self.root) if ov is None else ov)

    def graph(self, job, inputs=None):
        take = T.Take(job.id, job.take, job.pass_,
                      T.take_paths(self.root, job.pass_, job.id, job.take))
        return J.graph_for(self.base, job, take, inputs=inputs)

    def test_files_and_lookup(self):
        self.assertEqual(J.extra_shotlist_targets(self.root, "final"), [LTX])
        self.assertEqual(J.extra_shotlist_targets(self.root, "proxy"), [LTX])
        docs = J.load_shotlists(self.root, "proxy")
        self.assertEqual([d.get("target") for d in docs], [None, LTX])
        self.assertEqual([s["id"] for s in docs[0]["shots"]], ["sh010", "sh030", "sh060"])
        self.assertEqual([s["id"] for s in docs[1]["shots"]], ["sh020", "sh040", "sh050"])
        order = [d["shots"][i]["id"] for d, i in J.episode_shots(self.root, "proxy")]
        self.assertEqual(order, ["sh010", "sh020", "sh030", "sh040", "sh050", "sh060"])
        doc, i = J.find_shot(self.root, "final", "sh040")
        self.assertEqual((doc["target"], doc["shots"][i]["id"]), (LTX, "sh040"))
        with self.assertRaises(KeyError):
            J.find_shot(self.root, "final", "sh999")
        # H3 compiles its shots exactly as a whole-episode build would: a shot
        # after a retargeted one keeps its place (and seed)
        e = next(s for s in docs[0]["shots"] if s["id"] == "sh030")
        self.assertEqual(e["seed"], ir.stable_seed("mx01", "sq01", "sh030"))

    def test_ltx_entries_carry_the_neutral_keys(self):
        doc, i = J.find_shot(self.root, "proxy", "sh040")
        s = doc["shots"][i]
        for k in ("length", "subjects", "audio_policy", "background", "seed", "steps", "prompt"):
            self.assertIn(k, s)
        self.assertEqual((s["length"] - 1) % 8, 0)
        self.assertEqual((s["audio_policy"], s["audio_intent"]), ("generate", "dub_keep_foley"))
        self.assertIsInstance(s["prompt"], str)
        self.assertEqual(s["keyframes"], {"first": "refs/shots/sh040/first.png",
                                          "last": "refs/shots/sh040/last.png"})
        self.assertEqual(doc["defaults"]["width"], 448)

    def test_text_only_graph(self):
        job = self.plan("sh040")
        self.assertEqual((job.target, job.built_target, job.retargeted), (LTX, LTX, False))
        self.assertEqual(job.missing, [])                      # keyframes are optional
        g = self.graph(job)
        self.assertEqual(J.check_graph(g, OBJECT_INFO), [])
        for gone in ("LTXVImgToVideoInplace", "LoadImage", "TextGenerateLTX2Prompt",
                     "ComfySwitchNode", "PrimitiveInt", "CreateVideo", "SaveVideo",
                     "PreviewAny", "ResolutionSelector"):
            self.assertFalse(of(g, gone), gone)
        lat = g[J.node_of(g, "EmptyLTXVLatentVideo")]["inputs"]
        self.assertEqual((lat["width"], lat["height"], lat["length"]), (224, 128, job.frames))
        self.assertEqual(g[J.node_of(g, "LTXVEmptyLatentAudio")]["inputs"]["frames_number"],
                         job.frames)
        self.assertEqual({g[k]["inputs"]["noise_seed"] for k in of(g, "RandomNoise")}, {job.seed})
        texts = {g[k]["inputs"]["text"] for k in of(g, "CLIPTextEncode")}
        self.assertEqual(texts, {job.prompt, job.shot["negative"]})
        sv = g[J.node_of(g, "H3SaveShot")]["inputs"]
        self.assertEqual((sv["shot_id"], sv["audio_policy"], sv["fps"], sv["save_frames"]),
                         ("sh040", "generate", 24.0, False))
        self.assertEqual(sv["images"][0], J.node_of(g, "VAEDecodeTiled"))
        self.assertEqual(sv["audio"][0], J.node_of(g, "LTXVAudioVAEDecode"))

    def test_keyframes(self):
        job = self.plan("sh050")
        g = self.graph(job, {"first": "h3pipe/a.png"})
        self.assertEqual(J.check_graph(g, OBJECT_INFO), [])
        self.assertEqual(g[J.node_of(g, "LoadImage")]["inputs"]["image"], "h3pipe/a.png")
        self.assertEqual(sorted(g[k]["inputs"]["strength"] for k in of(g, "LTXVImgToVideoInplace")),
                         [0.7, 1.0])
        g = self.graph(job, {"last": "h3pipe/b.png"})
        self.assertEqual(J.check_graph(g, OBJECT_INFO), [])
        self.assertFalse(of(g, "LTXVImgToVideoInplace"))
        self.assertEqual(len(of(g, "LTXVAddGuide")), 2)
        self.assertEqual(len(of(g, "LTXVCropGuides")), 2)
        self.assertEqual({g[k]["inputs"]["frame_idx"] for k in of(g, "LTXVAddGuide")}, {-1})
        # the decoder reads the stage-2 latent without its guide frames
        dec = g[J.node_of(g, "VAEDecodeTiled")]["inputs"]["samples"]
        self.assertEqual(g[dec[0]]["class_type"], "LTXVCropGuides")
        g = self.graph(job, {"first": "h3pipe/a.png", "last": "h3pipe/b.png"})
        self.assertEqual(J.check_graph(g, OBJECT_INFO), [])
        self.assertEqual(len(of(g, "LoadImage")), 2)

    def test_stage_inputs_uploads_keyframes(self):
        job = self.plan("sh050")
        self.assertEqual(J.stage_inputs(job), {})
        kf = os.path.join(self.root, "refs", "shots", "sh050")
        os.makedirs(kf)
        with open(os.path.join(kf, "first.png"), "wb") as fh:
            fh.write(PNG)
        comfy = FakeComfy()
        try:
            got = J.stage_inputs(job, J.Comfy(comfy.url))
            self.assertEqual(list(got), ["first"])
            self.assertTrue(got["first"].startswith("h3pipe/") and got["first"].endswith(".png"))
            self.assertEqual(comfy.uploads, {got["first"]: PNG})
        finally:
            comfy.close()
        take = J.start_job(job)
        sc = T.read_sidecar(take.paths.sidecar)
        self.assertEqual(sc["inputs"], got)
        self.assertEqual({r["role"]: bool(r["sha1"]) for r in sc["refs"]},
                         {"first": True, "last": False})
        self.assertEqual(sc["target"], LTX)
        self.assertNotIn("notes", sc)                   # the proxy generates anyway
        # the final clones (the series' audio mode): the sidecar says it can't
        job = self.plan("sh050", "final")
        self.assertIn("audio clone renders as generate on LTX-2", " ".join(job.notes))
        job = self.plan("sh040")                        # dub_keep_foley, even on the proxy
        self.assertIn("audio dub_keep_foley renders as generate", " ".join(job.notes))
        self.assertIn("audio dub_keep_foley renders as generate",
                      " ".join(T.read_sidecar(J.start_job(job).paths.sidecar)["notes"]))

    def test_retarget_by_override_and_request(self):
        ov = T.load_overrides(self.root)
        T.set_shot_target(ov, "sh010", LTX)
        ov["shots"]["sh010"][H3] = {"proxy": {"prompt": "an H3 prompt"}}
        ov["shots"]["sh010"][LTX] = {"seed": 7, "proxy": {"prompt": "for ltx", "steps": 3}}
        job = self.plan("sh010", ov=ov)
        self.assertEqual((job.target, job.built_target, job.retargeted), (LTX, H3, True))
        self.assertEqual(job.frames, 65)                           # 2.5 s on 8k+1
        self.assertIsInstance(job.prompt, str)
        self.assertTrue(job.prompt.startswith("Style:"))           # both prompt overrides ignored
        self.assertIn("prompt override was ignored", " ".join(job.notes))
        self.assertEqual((job.seed, job.seed_source, job.steps), (7, "override", 3))
        self.assertIn("steps 3 is recorded but not used", " ".join(job.notes))
        take = J.start_job(job)
        frozen = T.read_json(take.paths.shotlist)
        self.assertEqual(frozen["target"], LTX)
        self.assertEqual(frozen["defaults"]["width"], 448)
        sc = T.read_sidecar(take.paths.sidecar)
        self.assertEqual((sc["target"], sc["built_target"], sc["length"]), (LTX, H3, 65))
        g = self.graph(job)
        self.assertEqual(J.check_graph(g, OBJECT_INFO), [])
        # the request beats the override: back to H3 for this run
        job = self.plan("sh010", ov=ov, target=H3)
        self.assertEqual((job.target, job.retargeted), (H3, False))
        self.assertEqual(job.prompt, "an H3 prompt")
        # an LTX-built shot retargeted to H3
        job = self.plan("sh020", ov={}, target=H3)
        self.assertEqual((job.target, job.built_target), (H3, LTX))
        self.assertIsInstance(job.prompt, list)
        self.assertEqual((job.frames - 5) % 17, 0)

    def test_retarget_needs_a_current_build(self):
        p = os.path.join(self.root, "series.json")
        cfg = json.load(open(p, encoding="utf-8"))
        cfg["subjects"]["ada"]["design"] = "someone else entirely"
        json.dump(cfg, open(p, "w", encoding="utf-8"))
        job = self.plan("sh010", ov={}, target=LTX)
        self.assertEqual(job.action, "error")
        self.assertIn("out of date", job.error)
        self.assertFalse(job.runs)

    def test_status_merges_targets_and_reports_them(self):
        ov = T.load_overrides(self.root)
        T.set_shot_target(ov, "sh030", LTX)
        T.save_overrides(self.root, ov)
        st = E.episode_status(self.root, "proxy")
        self.assertEqual(st["target"], H3)
        by = {s["shot"]: s for s in st["shots"]}
        self.assertEqual(list(by), ["sh010", "sh020", "sh030", "sh040", "sh050", "sh060"])
        self.assertEqual((by["sh020"]["target"], by["sh020"]["built_target"]), (LTX, LTX))
        self.assertEqual((by["sh030"]["target"], by["sh030"]["built_target"]), (LTX, H3))
        self.assertEqual(by["sh030"]["override"]["fields"], ["target"])
        self.assertEqual(by["sh030"]["missing_refs"], [])          # ltx needs no refs
        # an H3 take of a shot now on ltx2 is stale "target"; a new render isn't skipped
        job = self.plan("sh030", target=H3)
        J.start_job(job)
        take = T.list_takes(self.root, "proxy", "sh030")[0]
        T.update_sidecar(take.paths.sidecar, status="ok")
        open(take.paths.mp4, "wb").close()
        by = {s["shot"]: s for s in E.episode_status(self.root, "proxy")["shots"]}
        self.assertEqual(by["sh030"]["takes"][0]["stale"], ["target"])
        self.assertEqual(self.plan("sh030").action, "render")
        d = E.shot_detail(self.root, "proxy", "sh030")
        self.assertEqual((d["target"], d["built_target"]), (LTX, H3))
        self.assertEqual((d["effective"]["target"], d["effective"]["width"],
                          d["effective"]["height"], d["effective"]["length"]),
                         (LTX, 448, 256, 41))
        self.assertEqual(d["override"], {"target": LTX})
        d = E.shot_detail(self.root, "final", "sh040")               # built on ltx2
        self.assertEqual((d["target"], d["built_target"], d["effective"]["width"]),
                         (LTX, LTX, 1344))

    def test_queue_shots_uses_each_targets_workflow(self):
        comfy = FakeComfy()
        try:
            asked = []

            def base_for(tid):
                asked.append(tid)
                return (self.base if tid == LTX else
                        J.load_graph(os.path.join(ROOT, "targets", "video", H3, "workflow.json")))

            out = E.queue_shots(self.root, "proxy", ["sh010", "sh020"],
                                J.RenderRequest(""), J.Comfy(comfy.url), base_for)
            self.assertEqual([(q["shot"], q["target"]) for q in out["queued"]],
                             [("sh010", H3), ("sh020", LTX)])
            self.assertEqual(asked, [H3, LTX])
            self.assertEqual([t.status for t in T.list_takes(self.root, "proxy", "sh020")], ["ok"])
            # a request target retargets the H3 shot; it gets a new take
            out = E.queue_shots(self.root, "proxy", ["sh010"],
                                J.RenderRequest("", target=LTX), J.Comfy(comfy.url), base_for)
            self.assertEqual(out["queued"][0]["take"], 2)
            self.assertEqual(T.read_sidecar(T.list_takes(self.root, "proxy", "sh010")[1]
                                            .paths.sidecar)["target"], LTX)
        finally:
            comfy.close()

    def test_override_cli_writes_the_shots_own_target(self):
        env = dict(ENV)
        run = lambda *a: subprocess.run(  # noqa: E731
            [sys.executable, os.path.join(ROOT, "h3.py"), "override", self.root, *a],
            capture_output=True, env=env, text=True, encoding="utf-8")
        r = run("sh020", "--seed", "11", "--steps", "3", "--proxy")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        ov = T.load_overrides(self.root)
        self.assertEqual(ov["shots"]["sh020"][LTX]["seed"], 11)
        self.assertEqual(ov["shots"]["sh020"][LTX]["proxy"]["steps"], 3)
        self.assertNotIn(H3, ov["shots"]["sh020"])
        r = run("sh010", "--target", LTX)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("renders on ltx2", r.stdout)
        self.assertEqual(T.shot_target(T.load_overrides(self.root), "sh010"), LTX)
        run("sh010", "--seed", "5")
        self.assertEqual(T.load_overrides(self.root)["shots"]["sh010"][LTX]["seed"], 5)
        r = run("sh010", "--target", "built")
        self.assertIsNone(T.shot_target(T.load_overrides(self.root), "sh010"))
        r = run("sh010", "--target", "nope")
        self.assertEqual(r.returncode, 2)

    def test_h3align_snaps_to_each_shots_grid(self):
        import h3align
        with open(os.path.join(MIXED, "script.md"), encoding="utf-8") as fh:
            snap = h3align.shot_grids(os.path.join(MIXED, "series.json"), fh.read())
        self.assertEqual(snap("sh010")(50), 56)                    # H3: 17k+5
        self.assertEqual(snap("sh020")(50), 57)                    # ltx2: 8k+1


# ---------------------------------------------------------------------------
# the routes
# ---------------------------------------------------------------------------

class RetargetRouteTest(unittest.TestCase):
    def setUp(self):
        import h3pipe_api as A
        self.A = A
        self._tmp = tempfile.TemporaryDirectory()
        self.shows = os.path.join(self._tmp.name, "Shows")
        self.ep = os.path.join(self.shows, "mx01")
        os.makedirs(self.ep)
        build_mixed(self.ep)
        self.comfy = FakeComfy()
        self.ctx = A.Context(os.path.join(self._tmp.name, "user"), self.comfy.url,
                             comfy=J.Comfy(self.comfy.url, client_id="h3pipe"), env={})
        self.ok(A.put_config(self.ctx, {"roots": [self.shows]}))

    def tearDown(self):
        self.comfy.close()
        self._tmp.cleanup()

    def ok(self, res):
        self.assertEqual(res[0], 200, res[1])
        return res[1]

    def test_override_target_render_and_delete(self):
        A = self.A
        body = {"ep": self.ep, "pass": "proxy", "shot": "sh010", "fields": {"target": LTX}}
        data = self.ok(A.put_override(self.ctx, body))
        self.assertEqual((data["target"], data["built_target"]), (LTX, H3))
        self.assertEqual(data["override"]["proxy"]["target"], LTX)
        self.assertEqual(data["override"]["final"]["target"], LTX)      # shared
        # pass fields now go to the new target's block, stamped against its entry
        data = self.ok(A.put_override(self.ctx, dict(body, fields={"steps": 3, "seed": "9"})))
        self.assertFalse(data["override"]["proxy"]["stale"])
        blk = T.load_overrides(self.ep)["shots"]["sh010"][LTX]
        self.assertEqual((blk["seed"], blk["proxy"]["steps"]), (9, 3))
        st = self.ok(A.get_episode(self.ctx, {"ep": self.ep, "pass": "proxy"}))
        sh = next(s for s in st["shots"] if s["shot"] == "sh010")
        self.assertEqual((sh["target"], sh["built_target"]), (LTX, H3))
        d = self.ok(A.get_shot(self.ctx, {"ep": self.ep, "pass": "proxy", "shot": "sh010"}))
        self.assertEqual((d["effective"]["target"], d["effective"]["seed"]), (LTX, "9"))
        self.comfy.nodes |= {"EmptyLTXVLatentVideo"}
        r = self.ok(A.post_render(self.ctx, {"ep": self.ep, "pass": "proxy",
                                             "shots": ["sh010", "sh060"]}))
        self.assertEqual([(q["shot"], q["target"]) for q in r["queued"]],
                         [("sh010", LTX), ("sh060", H3)], r)
        g = self.comfy.graphs[0]
        self.assertTrue(any(v["class_type"] == "EmptyLTXVLatentVideo" for v in g.values()))
        # the request's target beats the override for one run
        r = self.ok(A.post_render(self.ctx, {"ep": self.ep, "pass": "proxy", "shots": ["sh010"],
                                             "redo": True, "target": H3}))
        self.assertEqual(r["queued"][0]["target"], H3)
        code, err = A.post_render(self.ctx, {"ep": self.ep, "shots": ["sh010"], "target": "nope"})
        self.assertEqual(code, 400)
        code, err = A.put_override(self.ctx, dict(body, fields={"target": "nope"}))
        self.assertEqual(code, 400)
        # null clears it; DELETE without a pass clears it too
        data = self.ok(A.put_override(self.ctx, dict(body, fields={"target": None})))
        self.assertEqual(data["target"], H3)
        self.ok(A.put_override(self.ctx, dict(body, fields={"target": LTX})))
        data = self.ok(A.delete_override(self.ctx, {"ep": self.ep, "shot": "sh010"}))
        self.assertEqual(data["target"], H3)
        self.assertIsNone(T.shot_target(T.load_overrides(self.ep), "sh010"))


if __name__ == "__main__":
    unittest.main()
