"""
Phase 12a (docs/PLAN.md): a target made of data alone. The builtin prose
compile (`"code": "builtin:video_prose"`), a show's own targets in
`<show>/targets/<id>/target.json`, and keyframes wired by `binding.inputs`
instead of Python.

The exit check is CustomEqualsStockTest: `tests/fixtures/custom_target`'s
`ti2v_custom` is wan22_ti2v re-expressed as a custom target, and every shot
entry it compiles must equal the stock target's.
"""
from __future__ import annotations

import copy
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "comfy_nodes"))
import h3jobs as J  # noqa: E402
import h3pipe_api as A  # noqa: E402
import targets as TG  # noqa: E402
from h3core.series_config import (character_ids, load_series_config, series_info,  # noqa: E402
                                  subject_ids, variant_of)
from h3core.story import parse_story  # noqa: E402
from targets.generic import video_prose as G  # noqa: E402
from test_api import ApiTest  # noqa: E402

KITCHEN = os.path.join(HERE, "fixtures", "kitchen_sink")
CUSTOM = os.path.join(HERE, "fixtures", "custom_target")
STOCK = "wan22_ti2v"
MINE = "ti2v_custom"


def a_show(tmp: str, *targets: str, script: bool = True) -> str:
    """A show folder with the kitchen_sink series config, an episode, and a copy
    of each named fixture target under `<show>/targets/`."""
    ep = os.path.join(tmp, "show", "ep01")
    os.makedirs(ep, exist_ok=True)
    shutil.copy(os.path.join(KITCHEN, "series.json"), ep)
    if script:
        shutil.copy(os.path.join(KITCHEN, "script.md"), os.path.join(ep, "ep01.md"))
    for name in targets:
        shutil.copytree(os.path.join(CUSTOM, name), os.path.join(ep, "targets", name),
                        dirs_exist_ok=True)
    return ep


def story_of(ep: str):
    cfg = load_series_config(os.path.join(ep, "series.json"))
    with io.open(os.path.join(ep, "ep01.md"), encoding="utf-8") as fh:
        story = parse_story(fh.read(), subject_ids(cfg), character_ids(cfg), series_info(cfg),
                            variant_of(cfg))
    return story, cfg


class LoadingTest(unittest.TestCase):
    """A show's own targets: found, not shadowing, cached per folder."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.ep = a_show(self.tmp, MINE)
        TG.clear_thread_roots()
        TG.forget_targets()

    def tearDown(self):
        TG.clear_thread_roots()
        TG.forget_targets()
        self._tmp.cleanup()

    def test_not_found_without_a_root(self):
        with self.assertRaises(TG.TargetError):
            TG.load_target(MINE, "video")
        self.assertNotIn(MINE, [t.id for t in TG.list_targets("video")])

    def test_found_with_a_root(self):
        t = TG.load_target(MINE, "video", root=self.ep)
        self.assertTrue(t.custom)
        self.assertEqual(t.kind, "video")
        self.assertIn(MINE, [x.id for x in TG.list_targets("video", root=self.ep)])
        # the repo's targets are still there, and first
        ids = [x.id for x in TG.list_targets("video", root=self.ep)]
        self.assertLess(ids.index(STOCK), ids.index(MINE))

    def test_use_roots_is_scoped(self):
        with TG.use_roots(self.ep):
            self.assertTrue(TG.load_target(MINE, "video").custom)
        with self.assertRaises(TG.TargetError):
            TG.load_target(MINE, "video")

    def test_thread_root_until_cleared(self):
        TG.add_thread_root(self.ep)
        self.assertTrue(TG.load_target(MINE, "video").custom)
        TG.clear_thread_roots()
        with self.assertRaises(TG.TargetError):
            TG.load_target(MINE, "video")

    def test_series_folder_targets_count_for_its_episodes(self):
        """The targets folder may sit beside series.json, one level up."""
        show = os.path.dirname(self.ep)
        shutil.move(os.path.join(self.ep, "series.json"), show)
        shutil.move(os.path.join(self.ep, "targets"), show)
        self.assertEqual(TG.show_folder(self.ep), show)
        self.assertTrue(TG.load_target(MINE, "video", root=self.ep).custom)

    def test_a_custom_target_with_a_builtin_name_is_ignored(self):
        """The built-in keeps rendering; the stray folder is findable, for a
        warning. (Saving one is refused by the route instead.)"""
        shutil.copytree(os.path.join(self.ep, "targets", MINE),
                        os.path.join(self.ep, "targets", STOCK))
        p = os.path.join(self.ep, "targets", STOCK, "target.json")
        spec = json.load(io.open(p, encoding="utf-8"))
        spec["id"] = STOCK
        json.dump(spec, io.open(p, "w", encoding="utf-8"))
        TG.forget_targets()
        self.assertFalse(TG.load_target(STOCK, "video", root=self.ep).custom)
        listed = [x for x in TG.list_targets("video", root=self.ep) if x.id == STOCK]
        self.assertEqual([x.custom for x in listed], [False])
        self.assertEqual([os.path.basename(f) for f in TG.shadowed_custom(self.ep)], [STOCK])

    def test_id_must_match_the_folder(self):
        p = os.path.join(self.ep, "targets", MINE, "target.json")
        spec = json.load(io.open(p, encoding="utf-8"))
        spec["id"] = "something_else"
        json.dump(spec, io.open(p, "w", encoding="utf-8"))
        TG.forget_targets()
        with self.assertRaises(TG.TargetError):
            TG.load_target(MINE, "video", root=self.ep)

    def test_two_shows_may_use_one_id(self):
        other = a_show(os.path.join(self.tmp, "other"), MINE)
        p = os.path.join(other, "targets", MINE, "target.json")
        spec = json.load(io.open(p, encoding="utf-8"))
        spec["label"] = "The other show's"
        json.dump(spec, io.open(p, "w", encoding="utf-8"))
        self.assertEqual(TG.load_target(MINE, "video", root=self.ep).label,
                         "Wan 2.2 5B (custom)")
        self.assertEqual(TG.load_target(MINE, "video", root=other).label,
                         "The other show's")


class BuiltinCodeTest(unittest.TestCase):
    """`code: "builtin:<name>"`, and the default for a custom target."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ep = a_show(self._tmp.name, MINE)
        TG.forget_targets()

    def tearDown(self):
        TG.forget_targets()
        self._tmp.cleanup()

    def test_default_code_is_the_builtin_prose_compile(self):
        t = TG.load_target(MINE, "video", root=self.ep)
        self.assertEqual(t.default_code, "builtin:video_prose")
        self.assertEqual(t.module.__name__, "targets.generic.video_prose")

    def test_explicit_builtin(self):
        p = os.path.join(self.ep, "targets", MINE, "target.json")
        spec = json.load(io.open(p, encoding="utf-8"))
        spec["code"] = "builtin:video_prose"
        json.dump(spec, io.open(p, "w", encoding="utf-8"))
        TG.forget_targets()
        self.assertEqual(TG.load_target(MINE, "video", root=self.ep).module.__name__,
                         "targets.generic.video_prose")

    def test_unknown_builtin_is_a_target_error(self):
        p = os.path.join(self.ep, "targets", MINE, "target.json")
        spec = json.load(io.open(p, encoding="utf-8"))
        spec["code"] = "builtin:nope"
        json.dump(spec, io.open(p, "w", encoding="utf-8"))
        TG.forget_targets()
        t = TG.load_target(MINE, "video", root=self.ep)
        with self.assertRaises(TG.TargetError):
            t.module

    def test_repo_targets_keep_their_own_code(self):
        t = TG.load_target(STOCK, "video")
        self.assertFalse(t.custom)
        self.assertEqual(t.default_code, "compile.py")
        self.assertEqual(t.module.__name__, "targets.video.wan22_ti2v.compile")


class CustomEqualsStockTest(unittest.TestCase):
    """Phase 12a's exit check: the same shots, compiled by data alone."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.ep = a_show(cls._tmp.name, MINE)
        cls.story, cls.cfg = story_of(cls.ep)

    @classmethod
    def tearDownClass(cls):
        TG.forget_targets()
        cls._tmp.cleanup()

    def test_entries_and_defaults_match(self):
        stock = TG.load_target(STOCK, "video")
        mine = TG.load_target(MINE, "video", root=self.ep)
        for pass_ in ("final", "proxy"):
            with self.subTest(pass_=pass_):
                a, _ = stock.compile_episode(self.story, self.cfg, pass_)
                b, _ = mine.compile_episode(self.story, self.cfg, pass_)
                self.assertTrue(a["shots"])
                self.assertEqual(a["shots"], b["shots"])
                self.assertEqual(a["defaults"], b["defaults"])
                self.assertEqual(a["subjects"], b["subjects"])
                # only the target's own name differs
                self.assertEqual({k for k in set(a) | set(b) if a.get(k) != b.get(k)},
                                 {"target"})

    def test_ref_slots_and_required_refs_match(self):
        stock = TG.load_target(STOCK, "video")
        mine = TG.load_target(MINE, "video", root=self.ep)
        a, _ = stock.compile_episode(self.story, self.cfg, "proxy")
        b, _ = mine.compile_episode(self.story, self.cfg, "proxy")
        for x, y in zip(a["shots"], b["shots"]):
            self.assertEqual(stock.ref_slots(a, x), mine.ref_slots(b, y))
        shot = next(s for s in self.story.shots())
        self.assertEqual(stock.required_refs(shot, self.cfg), mine.required_refs(shot, self.cfg))

    def test_the_dialogue_warning_names_the_target(self):
        """The one report difference: a custom target is not called Wan."""
        mine = TG.load_target(MINE, "video", root=self.ep)
        _, rep = mine.compile_episode(self.story, self.cfg, "proxy")
        line = next(w for w in rep["warnings"] if "no audio or lip-sync" in w)
        self.assertIn(mine.short, line)


class ProseStyleTest(unittest.TestCase):
    """Which prose writer a target gets, and the silent-target wording."""

    def test_silent_target_gets_the_acting_writer(self):
        t = TG.load_target(STOCK, "video")
        self.assertFalse(G.makes_sound(t))
        self.assertEqual(G.style_for(t).build_prompt.__module__, "targets.video.wan.prompt")

    def test_sound_target_gets_the_full_writer(self):
        t = TG.load_target("ltx2", "video")
        self.assertTrue(G.makes_sound(t))
        self.assertEqual(G.style_for(t).build_prompt.__module__, "targets.video.ltx2.prompt")

    def test_prose_silent_asks_for_the_acting_writer(self):
        t = copy.copy(TG.load_target("ltx2", "video"))
        t.recipe = dict(t.recipe, prompt="prose_silent")
        self.assertEqual(G.style_for(t).build_prompt.__module__, "targets.video.wan.prompt")

    def test_default_policy(self):
        self.assertEqual(G.default_policy(TG.load_target(STOCK, "video")), "silent")
        self.assertEqual(G.default_policy(TG.load_target("ltx2", "video")), "generate")


class KeyframeInputsTest(unittest.TestCase):
    """`binding.inputs`: the keyframe wiring wan22_ti2v writes in Python."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ep = a_show(self._tmp.name, MINE)
        TG.forget_targets()
        self.mine = TG.load_target(MINE, "video", root=self.ep)
        self.stock = TG.load_target(STOCK, "video")
        self.graph = J.graph_from(json.load(io.open(self.stock.binding.workflow,
                                                    encoding="utf-8")))

    def tearDown(self):
        TG.forget_targets()
        self._tmp.cleanup()

    def declared(self):
        return G.keyframe_inputs(self.mine)

    def test_the_spec_is_read(self):
        self.assertEqual(self.declared()["first"]["class_type"], "LoadImage")

    def test_a_keyframe_lands_in_the_loader(self):
        g = copy.deepcopy(self.graph)
        self.mine.patch_graph(g, None, {"first": "h3pipe/abc.png"})
        loader = next(v for v in g.values() if v["class_type"] == "LoadImage")
        self.assertEqual(loader["inputs"]["image"], "h3pipe/abc.png")

    def test_no_keyframe_unwires_the_latent(self):
        g = copy.deepcopy(self.graph)
        self.mine.patch_graph(g, None, {})
        lat = next(v for v in g.values() if v["class_type"] == "Wan22ImageToVideoLatent")
        self.assertNotIn("start_image", lat["inputs"])

    def test_it_does_what_the_python_did(self):
        """The declarative wiring and wan22_ti2v's patch_graph agree."""
        for inputs in ({"first": "h3pipe/abc.png"}, {}):
            mine, stock = copy.deepcopy(self.graph), copy.deepcopy(self.graph)
            self.mine.patch_graph(mine, None, dict(inputs))
            self.stock.patch_graph(stock, None, dict(inputs))
            self.assertEqual(mine, stock, inputs)

    def test_a_missing_node_is_an_error(self):
        g = {k: v for k, v in copy.deepcopy(self.graph).items()
             if v["class_type"] != "LoadImage"}
        with self.assertRaises(ValueError):
            self.mine.patch_graph(g, None, {"first": "x.png"})


class RoutesTest(ApiTest):
    """The routes see a show's own targets, and only that show's."""

    def setUp(self):
        super().setUp()
        shutil.copytree(os.path.join(CUSTOM, MINE),
                        os.path.join(self.ep, "targets", MINE))
        TG.clear_thread_roots()
        TG.forget_targets()
        A._GRAPHS.clear()
        A._READY.clear()

    def tearDown(self):
        TG.clear_thread_roots()
        TG.forget_targets()
        A._GRAPHS.clear()
        A._READY.clear()
        super().tearDown()

    def ids(self, **query):
        return [t["id"] for t in self.ok(A.get_targets(self.ctx, query))["targets"]]

    def test_targets_with_ep_lists_the_custom_one(self):
        self.assertIn(MINE, self.ids(kind="video", ep=self.ep))
        self.assertNotIn(MINE, self.ids(kind="video"))

    def test_a_custom_target_can_be_a_shot_override(self):
        r = self.ok(A.put_override(self.ctx, {"ep": self.ep, "shot": "sh010",
                                              "fields": {"target": MINE}}))
        self.assertEqual(r["target"], MINE)
        st = self.ok(A.get_episode(self.ctx, {"ep": self.ep, "pass": "proxy"}))
        sh = next(s for s in st["shots"] if s["shot"] == "sh010")
        self.assertEqual(sh["target"], MINE)
        self.assertEqual(sh.get("retarget_error"), None)

    def test_episode_target_may_be_a_custom_one(self):
        r = self.ok(A.put_episode_target(self.ctx, {"ep": self.ep, "target": MINE}))
        self.assertEqual(r["target"], MINE)
        self.assertEqual(r["target_source"], "editor")


# ---------------------------------------------------------------------------
# Phase 12b: proposing a target from a workflow
# ---------------------------------------------------------------------------

OBJECT_INFO_INSPECT = json.load(io.open(os.path.join(HERE, "fixtures", "workflows",
                                                     "object_info_inspect.json"),
                                        encoding="utf-8"))
FOLDER_FILES = {
    "diffusion_models": ["wan2.2_ti2v_5B_fp16.safetensors"],
    "text_encoders": ["umt5_xxl_fp8_e4m3fn_scaled.safetensors"],
    "vae": ["wan2.2_vae.safetensors"],
    "loras": ["some_turbo_4step.safetensors"],
}


def folder_list(folder, class_type=None, field=None):
    return FOLDER_FILES.get(folder, [])


def ti2v_graph() -> dict:
    t = TG.load_target(STOCK, "video")
    return json.load(io.open(t.binding.workflow, encoding="utf-8"))


class InspectTest(unittest.TestCase):
    """h3inspect against a workflow whose real target.json we have: the
    proposal must recover the binding the shipped target hand-wrote."""

    @classmethod
    def setUpClass(cls):
        import h3inspect as IN
        cls.IN = IN
        cls.stock = TG.load_target(STOCK, "video")
        cls.r = IN.inspect_graph(ti2v_graph(), OBJECT_INFO_INSPECT, folder_list,
                                 workflow_name=cls.stock.binding.workflow_name)
        cls.prop = cls.r["proposal"]

    def spec_of(self, params, name):
        v = params.get(name)
        v = v[0] if isinstance(v, list) else v
        return None if not v else f"{v['class_type']}.{v.get('field') or v.get('name')}"

    def test_the_core_params_match_the_shipped_binding(self):
        real = self.stock.binding.params
        got = self.prop["binding"]["params"]
        for name in ("model", "prompt", "negative", "width", "height", "length", "seed",
                     "steps", "cfg", "sampler", "shift", "text_encoder", "vae", "fps",
                     "shot_id", "audio_policy"):
            with self.subTest(param=name):
                self.assertEqual(self.spec_of(got, name), self.spec_of(real, name))

    def test_no_ambiguity_or_problem_on_a_clean_graph(self):
        self.assertEqual(self.r["ambiguous"], [])
        self.assertEqual(self.r["problems"], [])
        self.assertTrue(self.r["matched"]["prompt"].endswith(".text"))

    def test_the_saver_is_the_one_a_take_needs(self):
        saver = self.prop["binding"]["saver"]
        self.assertEqual(saver["class_type"], "H3SaveShot")
        self.assertEqual((saver.get("replace") or {}).get("class_type"), "CreateVideo")

    def test_template_from_the_widgets(self):
        tpl = self.prop["template"]
        real = self.stock.spec["template"]
        self.assertEqual(tpl["size_multiple"], real["size_multiple"])      # 32: width.step
        self.assertEqual(tpl["frames"]["step"], real["frames"]["step"])    # 4: length.step
        self.assertEqual(tpl["size_fit"], "snap")
        self.assertTrue(any("frames" in w for w in self.r["warnings"]))    # max is a guess

    def test_models_and_families(self):
        by = {m["param"]: m for m in self.r["models"]}
        self.assertEqual(by["model"]["file"], "wan2.2_ti2v_5B_fp16.safetensors")
        self.assertEqual(by["model"]["folder"], "diffusion_models")
        self.assertEqual(by["model"]["family"], self.stock.models["model"]["family"])
        self.assertEqual(self.prop["models"]["model"]["tier"], "required")

    def test_silent_graph_is_detected(self):
        self.assertEqual(self.prop["capabilities"]["audio"], "none")
        self.assertEqual(self.prop["recipe"]["policies"], ["silent"])

    def test_keyframe_is_wired_declaratively(self):
        self.assertEqual(self.prop["recipe"]["keyframes"], ["first"])
        first = self.prop["binding"]["inputs"]["first"]
        self.assertEqual(first["class_type"], "LoadImage")
        self.assertEqual(first["disconnect"]["class_type"], "Wan22ImageToVideoLatent")
        self.assertEqual(first["disconnect"]["input"], "start_image")

    def test_presets_come_from_the_graph(self):
        final = self.prop["presets"]["final"]
        self.assertEqual(final["model"], "wan2.2_ti2v_5B_fp16.safetensors")
        self.assertEqual(final["width"], 1280)
        self.assertEqual(self.prop["presets"]["proxy"]["width"], 640)

    def test_it_is_a_draft(self):
        self.assertTrue(self.prop["draft"])

    def test_a_graph_with_no_saver_is_refused(self):
        g = {k: v for k, v in J.graph_from(ti2v_graph()).items()
             if v["class_type"] not in ("CreateVideo", "SaveVideo")}
        with self.assertRaises(self.IN.InspectError):
            self.IN.inspect_graph(g, OBJECT_INFO_INSPECT, folder_list)

    def test_unbound_widgets_are_listed(self):
        fields = {u["field"] for u in self.r["unbound"]}
        self.assertIn("weight_dtype", fields)             # UNETLoader's, never a shot's


class SaveCustomTest(unittest.TestCase):
    """validate_spec / save_spec / delete_spec."""

    def setUp(self):
        import h3inspect as IN
        self.IN = IN
        self._tmp = tempfile.TemporaryDirectory()
        self.ep = a_show(self._tmp.name)
        self.graph = J.graph_from(ti2v_graph())
        self.spec = IN.inspect_graph(ti2v_graph(), OBJECT_INFO_INSPECT, folder_list,
                                     target_id="my_wan",
                                     workflow_name="mine.json")["proposal"]
        TG.forget_targets()

    def tearDown(self):
        TG.forget_targets()
        self._tmp.cleanup()

    def test_a_good_spec_validates_and_saves(self):
        self.assertEqual(self.IN.validate_spec(self.spec, self.graph, OBJECT_INFO_INSPECT), [])
        path = self.IN.save_spec(self.ep, self.spec)
        self.assertTrue(os.path.isfile(path))
        t = TG.load_target("my_wan", "video", root=self.ep)
        self.assertTrue(t.custom)
        self.assertEqual(t.module.__name__, "targets.generic.video_prose")
        self.assertEqual([x["id"] for x in self.IN.custom_targets(self.ep)], ["my_wan"])
        self.assertTrue(self.IN.delete_spec(self.ep, "my_wan"))
        self.assertFalse(self.IN.delete_spec(self.ep, "my_wan"))

    def test_a_builtin_name_is_refused(self):
        bad = dict(self.spec, id=STOCK)
        self.assertTrue(any("built-in" in p for p in self.IN.validate_spec(bad)))

    def test_a_bad_id_is_refused(self):
        for tid in ("Wan Custom", "1wan", "", "x" * 60):
            self.assertTrue(self.IN.validate_spec(dict(self.spec, id=tid)), tid)

    def test_a_param_the_graph_hasnt_got_is_refused(self):
        bad = json.loads(json.dumps(self.spec))
        bad["binding"]["params"]["width"] = {"class_type": "NoSuchNode", "field": "width"}
        problems = self.IN.validate_spec(bad, self.graph, OBJECT_INFO_INSPECT)
        self.assertTrue(any("binding.params.width" in p for p in problems), problems)

    def test_a_missing_kind_or_binding_is_refused(self):
        self.assertTrue(self.IN.validate_spec({"id": "x"}))
        self.assertTrue(self.IN.validate_spec(dict(self.spec, kind="image")))


class InspectRoutesTest(ApiTest):
    """GET /h3pipe/workflows, POST /h3pipe/targets/inspect, PUT and DELETE
    /h3pipe/targets/custom."""

    def setUp(self):
        super().setUp()
        TG.clear_thread_roots()
        TG.forget_targets()
        A._GRAPHS.clear()
        A._READY.clear()
        self.comfy.info = dict(OBJECT_INFO_INSPECT)
        self.comfy.nodes = set(OBJECT_INFO_INSPECT)
        self.comfy.userdata["workflows/mine.json"] = ti2v_graph()
        self.ctx = A.Context(self.user, self.comfy.url,
                             emit=lambda e, d: self.events.append((e, d)),
                             comfy=J.Comfy(self.comfy.url, client_id="h3pipe"), env={},
                             model_list=folder_list)
        self.ok(A.put_config(self.ctx, {"roots": [self.shows]}))

    def tearDown(self):
        TG.clear_thread_roots()
        TG.forget_targets()
        super().tearDown()

    def test_workflows_are_listed(self):
        d = self.ok(A.get_workflows(self.ctx, {}))
        self.assertIn("mine.json", [w["name"] for w in d["workflows"]])

    def test_inspect_a_saved_workflow(self):
        d = self.ok(A.post_targets_inspect(self.ctx, {"ep": self.ep, "workflow": "mine.json",
                                                      "id": "my_wan", "label": "My Wan"}))
        self.assertTrue(d["can_save"])
        self.assertEqual(d["proposal"]["id"], "my_wan")
        self.assertEqual(d["proposal"]["label"], "My Wan")
        self.assertTrue(d["warnings"])
        self.assertEqual(d["proposal"]["binding"]["workflow_name"], "mine.json")

    def test_inspect_a_graph_in_the_body(self):
        d = self.ok(A.post_targets_inspect(self.ctx, {"graph": ti2v_graph(),
                                                      "workflow": "mine.json"}))
        self.assertTrue(d["proposal"]["binding"]["params"])

    def test_inspect_needs_a_workflow(self):
        self.err(A.post_targets_inspect(self.ctx, {"ep": self.ep}), 400)
        self.err(A.post_targets_inspect(self.ctx, {"workflow": "nope.json"}), 404)

    def test_save_then_list_then_delete(self):
        d = self.ok(A.post_targets_inspect(self.ctx, {"workflow": "mine.json", "id": "my_wan"}))
        saved = self.ok(A.put_targets_custom(self.ctx, {"ep": self.ep,
                                                        "target": d["proposal"]}))
        self.assertEqual(saved["id"], "my_wan")
        self.assertTrue(saved["draft"])
        ids = [t["id"] for t in self.ok(A.get_targets(self.ctx, {"kind": "video",
                                                                "ep": self.ep}))["targets"]]
        self.assertIn("my_wan", ids)
        gone = self.ok(A.delete_targets_custom(self.ctx, {"ep": self.ep, "id": "my_wan"}))
        self.assertTrue(gone["deleted"])
        self.assertEqual(gone["targets"], [])

    def test_saving_a_bad_target_is_400_with_every_problem(self):
        d = self.ok(A.post_targets_inspect(self.ctx, {"workflow": "mine.json", "id": "my_wan"}))
        bad = json.loads(json.dumps(d["proposal"]))
        bad["id"] = STOCK
        code, data = A.put_targets_custom(self.ctx, {"ep": self.ep, "target": bad})
        self.assertEqual(code, 400, data)
        self.assertTrue(data["problems"])

    def test_saving_needs_the_workflow_to_be_in_comfyui(self):
        d = self.ok(A.post_targets_inspect(self.ctx, {"workflow": "mine.json", "id": "my_wan"}))
        spec = json.loads(json.dumps(d["proposal"]))
        spec["binding"]["workflow_name"] = "not_saved_anywhere.json"
        code, data = A.put_targets_custom(self.ctx, {"ep": self.ep, "target": spec})
        self.assertEqual(code, 400, data)
        self.assertTrue(any("save the graph there first" in p for p in data["problems"]))


if __name__ == "__main__":
    unittest.main()
