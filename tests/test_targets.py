"""
Phase 7: the targets package (targets/), render profiles, and the H3 target's
per-shot interface. The byte-for-byte proof is elsewhere (test_golden,
test_graph_snapshot); this covers what those can't see.
"""
from __future__ import annotations

import json
import os
import random
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "comfy_nodes"))
import h3build  # noqa: E402
import h3jobs as J  # noqa: E402
import h3pipe_api as A  # noqa: E402
import h3refs as R  # noqa: E402
import h3takes as T  # noqa: E402
import targets as TG  # noqa: E402
from h3core.series_config import (character_ids, load_series_config,  # noqa: E402
                                  series_info, subject_ids, variant_of)
from h3core.story import parse_story  # noqa: E402
from test_api import ApiTest  # noqa: E402
from test_render import FIXTURE, build_episode  # noqa: E402

H3 = "minimax_h3_ref2va"


def kitchen_sink():
    series_cfg = load_series_config(os.path.join(FIXTURE, "series.json"))
    with open(os.path.join(FIXTURE, "script.md"), encoding="utf-8") as fh:
        story = parse_story(fh.read(), subject_ids(series_cfg), character_ids(series_cfg),
                            series_info(series_cfg), variant_of(series_cfg))
    return series_cfg, story


class LoadingTest(unittest.TestCase):
    def test_list_and_load(self):
        ids = {(t.kind, t.id) for t in TG.list_targets()}
        self.assertEqual(ids, {("video", H3), ("video", "ltx2"), ("video", "ltx2_ingredients"),
                               ("video", "minimax_h3_fl2va"), ("video", "wan22_i2v"),
                               ("video", "wan22_ti2v"), ("video", "wan22_vace"), ("image", "krea2"),
                               ("image", "z_image_turbo"), ("image", "flux2_klein"),
                               ("image", "flux2_klein_edit"), ("image", "flux_kontext"),
                               ("image", "minimax_h3_still"), ("audio", "ltx2_voice")})
        self.assertEqual([t.id for t in TG.list_targets("video")],
                         ["ltx2", "ltx2_ingredients", "minimax_h3_fl2va", H3, "wan22_i2v",
                          "wan22_ti2v", "wan22_vace"])
        self.assertIs(TG.load_target(H3), TG.load_target(H3))
        with self.assertRaises(TG.TargetError) as cm:
            TG.load_target("ltx_2_3", "video")
        self.assertIn(H3, str(cm.exception))
        with self.assertRaises(TG.TargetError):
            TG.load_target(H3, "image")
        self.assertEqual(T.DEFAULT_TARGET, TG.DEFAULT_VIDEO_TARGET)

    def test_describe(self):
        d = TG.load_target(H3).describe()
        self.assertEqual(d["kind"], "video")
        self.assertTrue(d["default"])
        self.assertEqual(set(d["presets"]), {"final", "proxy"})
        self.assertEqual(d["presets"]["final"]["steps"], 8)
        self.assertEqual(d["widgets"]["model"], {"class_type": "UNETLoader", "field": "unet_name"})
        self.assertEqual(d["widgets"]["loras"]["class_type"], "LoraLoaderModelOnly")
        self.assertEqual(d["widgets"]["seed"], {"via": "loader"})
        self.assertEqual(d["workflow"], "H3_Ref2VA_Shotlist_v1.json")
        self.assertEqual(d["template"]["frames"], {"step": 17, "base": 5, "max": 3592})
        k = TG.load_target("krea2").describe()
        self.assertEqual(k["widgets"]["steps"], {"class_type": "KSampler", "field": "steps"})
        self.assertEqual(k["presets"]["final"]["steps"], 12)
        json.dumps(d), json.dumps(k)

    def test_workflows_live_with_their_targets(self):
        h3 = TG.load_target(H3).binding
        self.assertTrue(os.path.isfile(h3.workflow))
        self.assertEqual(TG.repo_workflow("H3_Ref2VA_Shotlist_v1.json"), h3.workflow)
        self.assertEqual(TG.repo_workflow("krea2_refs_t2i.json"),
                         TG.load_target("krea2").binding.workflow)
        self.assertIsNone(TG.repo_workflow("nope.json"))
        g, where = J.target_workflow(TG.load_target(H3), None, None, prefer_repo=True)
        self.assertEqual(os.path.normcase(where), os.path.normcase(h3.workflow))
        J.node_of(g, h3.loader_class), J.node_of(g, h3.saver_class)
        self.assertEqual(os.path.normcase(J.find_workflow(None)), os.path.normcase(h3.workflow)
                         if not os.environ.get("H3_WORKFLOW") else os.path.normcase(
                             os.environ["H3_WORKFLOW"]))
        g, where = R.resolve_workflow(None)
        self.assertIsNotNone(g)


class TemplateTest(unittest.TestCase):
    def setUp(self):
        self.t = TG.load_target(H3).template

    def test_grid(self):
        self.assertEqual([self.t.snap(n) for n in (1, 5, 6, 22, 23, 73, 74)],
                         [5, 5, 22, 22, 39, 73, 90])
        self.assertEqual(self.t.frames(3.04, 24), 73)
        self.assertEqual(h3build.snap_up(100), self.t.snap(100))
        with self.assertRaises(ValueError) as cm:
            self.t.snap(3593)
        self.assertEqual(str(cm.exception), "3593 frames is longer than H3's maximum of 3592 "
                                             "(149.67s). Split this shot into two.")

    def test_size(self):
        self.t.validate_size(1344, 768)
        with self.assertRaises(ValueError) as cm:
            self.t.validate_size(1280, 720)
        self.assertEqual(str(cm.exception), "height=720 is not a multiple of 32 — H3 rejects "
                                             "it. Nearest legal: 704 or 736.")

    def test_continuous(self):
        self.assertIsNone(self.t.continuous_warning("sh1", 199))
        self.assertIn("costs 22 of 73 frames (30%)", self.t.continuous_warning("sh1", 73))


class PresetTest(unittest.TestCase):
    def test_pass_blocks_over_presets(self):
        t = TG.load_target(H3)
        p = t.preset("final", {"series": {}})
        self.assertEqual((p.model, p.steps, p.width, p.height),
                         (h3build.FINAL_MODEL, 8, 1344, 768))
        p = t.preset("proxy", {"series": {"model": "m.safetensors"}})
        self.assertEqual((p.model, p.lora, p.steps, p.width, p.height),
                         ("m.safetensors", h3build.PROXY_LORA, 4, 512, 288))
        p = t.preset("proxy", {"series": {}, "proxy": {"model": "p", "steps": 6, "width": 448}})
        self.assertEqual((p.model, p.steps, p.width), ("p", 6, 448))
        with self.assertRaises(KeyError):
            t.preset("final", {})                        # as the build always said: 'series'
        with self.assertRaises(TG.TargetError):
            t.preset("draft", {"series": {}})


class ProfileTest(unittest.TestCase):
    def test_validation(self):
        good = TG.series_profiles({"profiles": {"_c": 1, "a": {"steps": 5, "loras": "x:0.5"}}})
        self.assertEqual(good, {"a": {"steps": 5, "loras": [{"name": "x", "strength": 0.5}]}})
        for bad in ({"a": {"stpes": 5}}, {"a": {"steps": 0}}, {"a": {"steps": "5"}},
                    {"a": {"loras": [3]}}, {"a": "x"}, ["a"]):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                TG.series_profiles({"profiles": bad})
        self.assertEqual(TG.normalize_loras(["none"], "w"), [])
        self.assertEqual(TG.normalize_loras([{"name": "a", "strength": 1}], "w"),
                         [{"name": "a", "strength": 1.0}])

    def test_layers(self):
        cfg = {"profiles": {"p": {"model": "pm", "steps": 7, "loras": ["pl"]},
                            "q": {"steps": 5, "model": "qm"}}}
        seq, shot = {"id": "sq", "profile": "q", "model": "sm"}, {"id": "sh", "profile": "p"}
        layers = TG.render_layers(cfg, seq, shot)
        self.assertEqual(TG.layered(layers, "model"), "pm")        # shot profile > seq line
        self.assertEqual(TG.layered(layers, "steps", 4, present=True), 7)
        self.assertEqual(TG.layered_lora(layers), ("loras", [{"name": "pl", "strength": 1.0}]))
        layers = TG.render_layers(cfg, seq, {"id": "sh", "lora": "x", "steps": 9})
        self.assertEqual(TG.layered(layers, "model"), "sm")        # seq line > seq profile
        self.assertEqual(TG.layered(layers, "steps", 4, present=True), 9)
        self.assertEqual(TG.layered_lora(layers), ("lora", "x"))
        with self.assertRaises(ValueError):
            TG.render_layers(cfg, seq, {"id": "sh", "profile": "nope"})

    def test_episode_target(self):
        series_cfg, story = kitchen_sink()
        self.assertEqual(TG.episode_target(story, series_cfg).id, H3)
        series_cfg["series"]["target"] = "ltx_2_3"
        with self.assertRaises(TG.TargetError):
            TG.episode_target(story, series_cfg)
        series_cfg["series"].pop("target")
        series_cfg["profiles"]["dialogue_close"]["target"] = "ltx_2_3"
        with self.assertRaises(ValueError) as cm:
            TG.episode_target(story, series_cfg)
        self.assertIn("not a video target (known: ltx2, ltx2_ingredients, minimax_h3_fl2va, "
                      "minimax_h3_ref2va, wan22_i2v, wan22_ti2v, wan22_vace)",
                      str(cm.exception))
        # sh320 names the series target itself, which beats its profile's; sh330
        # takes the profile's
        self.assertIn("shot sh330:", str(cm.exception))
        # a known target: the episode is split, the series target first
        series_cfg["profiles"]["dialogue_close"]["target"] = "ltx2"
        self.assertEqual(TG.episode_target(story, series_cfg).id, H3)
        by_shot = TG.shot_targets(story, series_cfg)
        self.assertEqual((by_shot["sh320"], by_shot["sh330"], by_shot["sh010"]), (H3, "ltx2", H3))
        groups = TG.episode_targets(story, series_cfg)
        self.assertEqual([(t.id, sorted(ids)) for t, ids in groups][1], ("ltx2", ["sh330"]))
        self.assertEqual(groups[0][0].id, H3)
        self.assertNotIn("sh330", groups[0][1])
        series_cfg["profiles"]["dialogue_close"].pop("target")
        self.assertEqual([(t.id, ids) for t, ids in TG.episode_targets(story, series_cfg)],
                         [(H3, None)])


class H3InterfaceTest(unittest.TestCase):
    """compile / required_refs / ref_slots agree with the episode build."""

    @classmethod
    def setUpClass(cls):
        cls.series_cfg, cls.story = kitchen_sink()
        cls.t = TG.load_target(H3)

    def test_compile_shot_matches_the_episode_build(self):
        for ps in ("final", "proxy"):
            doc, _ = self.t.compile_episode(self.story, self.series_cfg, ps)
            built = {s["id"]: s for s in doc["shots"]}
            for shot in self.story.shots():
                with self.subTest(pass_=ps, shot=shot.id):
                    got = self.t.compile(shot, self.series_cfg, ps, {"episode": self.story})
                    self.assertEqual(got, built[shot.id])

    def test_required_refs_are_the_loaders_slots(self):
        doc, _ = self.t.compile_episode(self.story, self.series_cfg, "final")
        built = {s["id"]: s for s in doc["shots"]}
        for shot in self.story.shots():
            if built[shot.id]["audio_policy"] in ("dub", "dub_keep_foley"):
                continue                                  # the recording isn't a series ref
            with self.subTest(shot=shot.id):
                reqs = self.t.required_refs(shot, self.series_cfg)
                slots = self.t.ref_slots(doc, built[shot.id])
                self.assertEqual([(r.slot, r.path) for r in reqs],
                                 [(s["slot"], s["path"]) for s in slots])
        reqs = self.t.required_refs(next(self.story.shots()), self.series_cfg)
        self.assertEqual([r.shape for r in reqs], ["sheet", "plate"])
        self.assertEqual(reqs[0].views, 4)
        self.assertEqual(reqs[0].size_hint, "4096x1024 or larger (horizontal 4-panel strip)")

    def test_ref_wording_is_the_image_targets(self):
        img = TG.image_target(self.series_cfg)
        shot = next(self.story.shots())
        sheet, plate = self.t.required_refs(shot, self.series_cfg)
        look = self.series_cfg["style"]["look"]
        self.assertEqual(img.ref_prompt(sheet, self.series_cfg),
                         R.sheet_prompt(sheet.entry["design"], look))
        self.assertEqual(img.ref_prompt(plate, self.series_cfg),
                         R.plate_prompt(look, plate.entry["description"]))


class ProfileJobTest(unittest.TestCase):
    """A profile's LoRA list reaches the graph; target is recorded."""

    def test_profile_shot_graph(self):
        base = J.load_graph(TG.load_target(H3).binding.workflow)
        with tempfile.TemporaryDirectory() as root:
            build_episode(root)
            doc = J.load_shotlist(root, "final")
            i = next(i for i, s in enumerate(doc["shots"]) if s["id"] == "sh320")
            job = J.plan_job(root, "final", doc, i, J.RenderRequest("sh320"), {},
                             rng=random.Random(1))
            self.assertEqual((job.model, job.steps, job.target),
                             ("profile_model.safetensors", 7, H3))
            self.assertEqual([lo["name"] for lo in job.loras],
                             ["profile_turbo_8step.safetensors", "profile_style.safetensors"])
            take = J.start_job(job)
            g = J.graph_for(base, job, take)
            loras = sorted((v["inputs"]["lora_name"], v["inputs"]["strength_model"])
                           for v in g.values() if v["class_type"] == "LoraLoaderModelOnly")
            self.assertEqual(loras, [("profile_style.safetensors", 0.6),
                                     ("profile_turbo_8step.safetensors", 1.0)])
            self.assertEqual(g["1"]["inputs"]["unet_name"], "profile_model.safetensors")
            frozen = json.load(open(take.paths.shotlist, encoding="utf-8"))
            self.assertEqual(frozen["target"], H3)
            self.assertEqual(frozen["shots"][0]["loras"], job.loras)
            self.assertEqual(T.read_sidecar(take.paths.sidecar)["target"], H3)
            # an override replaces the profile's list, as a redo would
            job2 = J.plan_job(root, "final", doc, i,
                              J.RenderRequest("sh320", loras=[{"name": "x", "strength": 1.0}]),
                              {})
            self.assertEqual(job2.loras, [{"name": "x", "strength": 1.0}])

    def test_preset_hash_ignores_absent_extras(self):
        shot = {"id": "a", "model": "m", "steps": 4}
        doc = {"defaults": {"model": "d"}}
        h = J.preset_hash(doc, shot)
        self.assertEqual(h, J.preset_hash(doc, dict(shot)))
        self.assertNotEqual(h, J.preset_hash(doc, dict(shot, loras=[])))
        self.assertEqual(J.story_hash(shot), J.story_hash(dict(shot, loras=[], profile="p")))

    def test_apply_loras_by_spec(self):
        g = {"1": {"class_type": "Loader", "inputs": {}},
             "2": {"class_type": "MyLora", "inputs": {"m": ["1", 0], "n": "", "s": 1.0}},
             "3": {"class_type": "Sampler", "inputs": {"model": ["2", 0]}}}
        spec = {"class_type": "MyLora", "name": "n", "strength": "s", "input": "m",
                "chain": True}
        J.apply_loras(g, [{"name": "a", "strength": 1}, {"name": "b", "strength": 0.5}], spec)
        new = [k for k, v in g.items() if v["class_type"] == "MyLora" and k != "2"]
        self.assertEqual(len(new), 1)
        self.assertEqual(g[new[0]]["inputs"], {"m": ["2", 0], "n": "b", "s": 0.5})
        self.assertEqual(g["3"]["inputs"]["model"], [new[0], 0])
        with self.assertRaises(ValueError):
            J.apply_loras(g, [{"name": "a"}, {"name": "b"}], dict(spec, chain=False))

    def test_set_widget(self):
        g = {"7": {"class_type": "KSampler", "inputs": {"steps": 1}}}
        J._set_widget(g, {"class_type": "KSampler", "field": "steps"}, 9)
        J._set_widget(g, {"via": "loader"}, 3)
        J._set_widget(g, None, 3)
        self.assertEqual(g["7"]["inputs"], {"steps": 9})


class RenderAnywayTest(unittest.TestCase):
    """Render anyway, target-aware: H3 recompiles a shot without its missing
    refs; anything it can't do that for falls back to grey stand-ins."""

    def setUp(self):
        import shutil
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        build_episode(self.root)                          # every ref stubbed
        shutil.copy(os.path.join(FIXTURE, "series.json"), self.root)
        self.doc = J.load_shotlist(self.root, "final")
        os.remove(os.path.join(self.root, "refs", "ada", "ada_sheet_4panel.png"))
        os.remove(os.path.join(self.root, "refs", "_bg", "kitchen.png"))

    def tearDown(self):
        self._tmp.cleanup()

    def plan(self, sid, ov=None, **req):
        i = next(i for i, s in enumerate(self.doc["shots"]) if s["id"] == sid)
        return J.plan_job(self.root, "final", self.doc, i,
                          J.RenderRequest(sid, allow_missing_refs=True, **req), ov or {})

    def test_recompiled_without_the_missing_refs(self):
        job = self.plan("sh020")                          # ada, bo + kettle; kitchen plate
        self.assertEqual([m["slot"] for m in job.missing], ["Picture 1", "Picture 4"])
        self.assertEqual(job.missing_mode, "recompiled")
        shot = job.recompiled
        self.assertEqual(shot["subjects"], ["bo", "kettle"])
        self.assertEqual(shot["background"], "")
        self.assertEqual(shot["audio_policy"], "clone")          # the samples are there
        text = "\n\n".join(job.prompt)
        self.assertIn("<Picture 1>", text)                         # bo
        self.assertIn("<Picture 2>", text)                         # the kettle
        self.assertNotIn("<Picture 3>", text)
        self.assertNotIn("<Picture 4>", text)
        self.assertIn("Ada has no reference image and is drawn from this description: "
                      "a tall woman", text)
        self.assertIn("Ada, with a voice that is dry, quick and precise, (S1)", text)
        self.assertIn("The scene takes place in a cramped diner kitchen", text)
        # everything the refs don't touch is the build's
        for k in ("id", "length", "seed", "steps", "model", "lora", "voices", "duration"):
            self.assertEqual(shot.get(k), job.shot.get(k), k)
        take = J.start_job(job)
        frozen = T.read_json(take.paths.shotlist)["shots"][0]
        self.assertEqual(frozen["subjects"], ["bo", "kettle"])
        self.assertEqual(frozen["prompt"], job.prompt)
        self.assertEqual(frozen["missing_refs"], "blank")      # the loader greys the plate
        sc = T.read_sidecar(take.paths.sidecar)
        self.assertEqual((sc["missing_mode"], sc["missing_refs"]),
                         ("recompiled", ["Picture 1", "Picture 4"]))
        self.assertNotIn("missing_note", sc)

    def test_missing_voice_makes_it_generate(self):
        os.remove(os.path.join(self.root, "audio", "voices", "ada_sample.wav"))
        job = self.plan("sh020")
        self.assertIn("Audio 1", [m["slot"] for m in job.missing])
        self.assertEqual((job.recompiled["audio_policy"], job.recompiled["voice_refs"]),
                         ("generate", []))
        self.assertNotIn("<Audio", "\n".join(job.prompt))

    def test_prompt_override_is_kept(self):
        ov = {"shots": {"sh020": {H3: {"final": {"prompt": "my text"}}}}}
        self.assertEqual(self.plan("sh020", ov).prompt, "my text")

    def test_nothing_missing_is_untouched(self):
        job = self.plan("sh130")                          # bo at the window: all there
        self.assertEqual((job.missing, job.recompiled, job.missing_mode), ([], None, ""))

    def test_out_of_date_build_falls_back_to_grey(self):
        p = os.path.join(self.root, "series.json")
        cfg = json.load(open(p, encoding="utf-8"))
        cfg["subjects"]["bo"]["design"] = "someone else entirely"
        json.dump(cfg, open(p, "w", encoding="utf-8"))
        job = self.plan("sh020")
        self.assertEqual((job.missing_mode, job.recompiled), ("blank", None))
        self.assertIn("out of date", job.missing_why)
        self.assertEqual(job.prompt, job.shot["prompt"])
        take = J.start_job(job)
        self.assertEqual(T.read_json(take.paths.shotlist)["shots"][0]["subjects"],
                         ["ada", "bo", "kettle"])
        self.assertEqual(T.read_sidecar(take.paths.sidecar)["missing_mode"], "blank")

    def test_target_without_support_falls_back_to_grey(self):
        real = TG.Target.supports
        TG.Target.supports = lambda self, name: False if name == "compile_without" \
            else real(self, name)
        try:
            job = self.plan("sh020")
        finally:
            TG.Target.supports = real
        self.assertEqual(job.missing_mode, "blank")
        self.assertIn("can't write a shot without", job.missing_why)


class TargetsRouteTest(ApiTest):
    def test_targets(self):
        data = self.ok(A.get_targets(self.ctx, {}))
        by = {t["id"]: t for t in data["targets"]}
        self.assertEqual(set(by), {H3, "ltx2", "ltx2_ingredients", "minimax_h3_fl2va", "krea2",
                                   "wan22_i2v", "wan22_ti2v", "wan22_vace", "z_image_turbo",
                                   "flux2_klein", "flux2_klein_edit", "flux_kontext",
                                   "minimax_h3_still", "ltx2_voice"})
        for tid, label, short in (("wan22_i2v", "Wan 2.2 14B I2V", "Wan I2V"),
                                  ("wan22_ti2v", "Wan 2.2 5B TI2V", "Wan 5B"),
                                  ("wan22_vace", "Wan 2.2 14B VACE (refs)", "Wan+refs")):
            self.assertEqual((by[tid]["label"], by[tid]["short"]), (label, short))
            self.assertEqual(by[tid]["capabilities"]["audio"], "none")
            self.assertEqual(by[tid]["capabilities"]["policies"], ["silent"])
        self.assertEqual(by["wan22_i2v"]["template"]["fps"], 16.0)
        self.assertEqual(by["wan22_ti2v"]["template"]["fps"], 24.0)
        self.assertTrue(by["wan22_vace"]["capabilities"]["subject_refs"])
        self.assertEqual(by[H3]["capabilities"]["audio"], "generate")
        self.assertEqual((by["ltx2_ingredients"]["label"], by["ltx2_ingredients"]["short"]),
                         ("LTX-2.3 ingredients (character/plate refs)", "LTX+refs"))
        fl = by["minimax_h3_fl2va"]
        self.assertEqual((fl["label"], fl["short"]),
                         ("MiniMax H3 FL2VA (first/last frames)", "H3 FL2V"))
        self.assertEqual(fl["capabilities"]["keyframes"], ["first", "last"])
        self.assertEqual(fl["capabilities"]["policies"], ["generate", "dub", "dub_keep_foley"])
        self.assertEqual(fl["capabilities"]["policy_fallback"], "generate")
        self.assertFalse(fl["capabilities"]["subject_refs"])
        self.assertEqual(by[H3]["kind"], "video")
        self.assertIn("loras", by[H3]["widgets"])
        # what a target picker needs
        lt = by["ltx2"]
        self.assertFalse(lt["default"])
        self.assertEqual(lt["capabilities"]["policies"], ["generate"])
        self.assertEqual(lt["capabilities"]["keyframes"], ["first", "last"])
        self.assertFalse(lt["capabilities"]["voice_reference"])
        self.assertTrue(by[H3]["capabilities"]["voice_reference"])
        self.assertEqual(lt["widgets"]["model"], {"class_type": "UNETLoader", "field": "unet_name"})
        self.assertEqual(lt["widgets"]["seed"]["class_type"], "RandomNoise")
        self.assertEqual(lt["template"]["frames"], {"step": 8, "base": 1, "max": 481})
        self.assertEqual(lt["template"]["fps"], "series")
        self.assertEqual(lt["presets"]["proxy"]["width"], 448)
        # the two optional extras a picker should see: the dev transformer
        # (the quality profile) and the 2.5 ingredients IC-LoRA (sheets).
        # Neither is a preset value, so the distilled presets are unchanged.
        self.assertTrue(lt["capabilities"]["reference_sheet"])
        self.assertTrue(lt["capabilities"]["subject_refs"])
        self.assertEqual(lt["capabilities"]["prompt"], "prose")
        self.assertEqual({p: m["tier"] for p, m in lt["models"].items()
                          if m["tier"] == "optional"},
                         {"duration_head": "optional", "quality_model": "optional",
                          "reference_lora": "optional"})
        self.assertEqual(lt["models"]["quality_model"]["feature"],
                         "the quality profile (LTX-2.5 dev transformer)")
        self.assertEqual(lt["models"]["reference_lora"]["label"], "LTX 2.5 ingredients IC-LoRA")
        self.assertEqual(lt["presets"]["final"]["model"],
                         "ltx-2.5-22b-distilled-transformer-comfy-int8-convrot.safetensors")
        self.assertEqual(lt["presets"]["final"]["steps"], 8)
        self.assertNotIn("base", lt["presets"]["final"])          # no accelerator to lose
        self.assertIn("ltx-2.5-22b-dev-transformer-comfy-int8-convrot.safetensors",
                      lt["downloads"])
        self.assertEqual(data["default"],
                         {"video": H3, "image": "krea2", "audio": "ltx2_voice"})
        images = self.ok(A.get_targets(self.ctx, {"kind": "image"}))["targets"]
        self.assertEqual([t["id"] for t in images],
                         ["flux2_klein", "flux2_klein_edit", "flux_kontext", "krea2",
                          "minimax_h3_still", "z_image_turbo"])
        caps = {t["id"]: t["capabilities"] for t in images}
        self.assertEqual(caps["krea2"], {"mode": "t2i", "max_refs": 0, "negative_prompt": False})
        self.assertEqual(caps["flux2_klein_edit"],
                         {"mode": "edit", "max_refs": 4, "negative_prompt": False})
        self.assertEqual(caps["flux_kontext"],
                         {"mode": "edit", "max_refs": 1, "negative_prompt": True})
        self.assertEqual(caps["z_image_turbo"]["mode"], "t2i")
        # the video model as an image target: nine reference slots (Phase 10b)
        self.assertEqual(caps["minimax_h3_still"],
                         {"mode": "edit", "max_refs": 9, "negative_prompt": False})
        self.assertTrue(by["wan22_i2v"]["capabilities"]["requires_first"])
        self.assertFalse(lt["capabilities"]["requires_first"])
        # Phase 9c-B: audio is a kind of its own (test_phase9c_voice)
        audio = self.ok(A.get_targets(self.ctx, {"kind": "audio"}))["targets"]
        self.assertEqual([t["id"] for t in audio], ["ltx2_voice"])
        self.err(A.get_targets(self.ctx, {"kind": "sound"}), 400)
        self.assertIn(("GET", "/h3pipe/targets"), {(m, p) for m, p, _f, _t in A.ROUTES})

    def test_target_in_status_detail_and_sidecar(self):
        data, shots = self.status("proxy")
        self.assertEqual(data["target"], H3)
        self.assertEqual(shots["sh010"]["target"], H3)
        self.assertIsNone(shots["sh010"]["profile"])
        self.assertEqual(shots["sh320"]["profile"], "dialogue_close")
        self.render("sh320")
        _, shots = self.status("proxy")
        self.assertEqual(shots["sh320"]["takes"][0]["target"], H3)
        d = self.ok(A.get_shot(self.ctx, {"ep": self.ep, "pass": "proxy", "shot": "sh320"}))
        self.assertEqual((d["target"], d["profile"]), (H3, "dialogue_close"))
        self.assertEqual(d["takes"][0]["sidecar"]["target"], H3)
        self.assertEqual(d["effective"]["steps"], 7)


if __name__ == "__main__":
    unittest.main()
