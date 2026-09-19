"""
Shot lengths that follow the story, and optionally the model.

  - `dur: model [min-max]` in the script is `timing: {"model": true, ...}` in
    the IR; every other `dur:` form is what it was.
  - The build writes an estimate (`length_estimated`): the dialogue's
    `dur: auto` length, else the preset's default seconds. A target with
    `capabilities.duration: "predict"` (ltx2) also gets the predictor's range;
    any other (H3, ltx2_ingredients) renders the estimate and says so in
    --check and in the take's notes.
  - Queue time: the duration head is looked up in ComfyUI's ModelPatchLoader
    choices. Installed: graph_for adds ModelPatchLoader + LTXVDurationPredictor
    and links num_frames into every length widget; the sidecar says
    `length_source: predicted` and the saver's `frames` is the truth. Missing:
    the estimate renders, with a note. Never a failure.
  - The episode status carries the cut take's real frames (`cut.frames`,
    `takes[].frames`) for the timeline and Play all.
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
from h3core import ir  # noqa: E402
from h3core.story import ScriptError, parse_script, parse_story  # noqa: E402
from test_render import ENV, FakeComfy, stub_refs  # noqa: E402

WF = os.path.join(HERE, "fixtures", "workflows")
MIXED = os.path.join(HERE, "fixtures", "mixed")
OBJECT_INFO = json.load(open(os.path.join(WF, "object_info_ltx.json"), encoding="utf-8"))
LTX, ING, H3 = "ltx2", "ltx2_ingredients", "minimax_h3_ref2va"
HEAD = "ltx-2.5-duration-head-bf16.safetensors"

SCRIPT = """= dm01  Model Durations

# sq01  kitchen
target: ltx2

// silent: the preset's default seconds
## sh010
who: ada
size: ws
dur: model
Ada wipes the counter down in long slow strokes.

// dialogue: the `dur: auto` length, inside the clamp
## sh020
who: ada, bo
size: ms
dur: model 3-8
ADA: You know that one whistles.
BO: I know.

// every other form, unchanged
## sh025
who: bo
size: ms
dur: 2.5
Bo shrugs.

# sq02  street

// H3 can't predict: the estimate, and a warning
## sh030
who: bo
size: wide
dur: model
Bo waves the van back toward the kitchen door.

// nor can ltx2_ingredients (LTX 2.3)
## sh040
target: ltx2_ingredients
who: bo
size: wide
dur: model 2-4
Bo waves.
"""


def build(root: str) -> list[str]:
    """The script above against the mixed series config; --check's output per pass."""
    shutil.copy(os.path.join(MIXED, "series.json"), root)
    with open(os.path.join(root, "dm01.md"), "w", encoding="utf-8") as fh:
        fh.write(SCRIPT)
    out = []
    for flags in ([], ["--proxy"]):
        args = [sys.executable, os.path.join(ROOT, "h3build.py"),
                os.path.join(root, "series.json"), os.path.join(root, "dm01.md"), "-o", root]
        r = subprocess.run(args + flags, capture_output=True, env=ENV, text=True,
                           encoding="utf-8")
        if r.returncode:
            raise AssertionError(r.stdout + r.stderr)
        out.append(subprocess.run(args + flags + ["--check"], capture_output=True, env=ENV,
                                  text=True, encoding="utf-8").stdout)
    stub_refs(root)
    return out


def of(g: dict, ctype: str) -> list[str]:
    return [k for k, v in g.items() if v["class_type"] == ctype]


def head_info(files: list[str]) -> dict:
    """/object_info for ModelPatchLoader with these model_patches files, and the predictor."""
    return {"ModelPatchLoader": {"input": {"required": {"name": [files]}},
                                 "output": ["MODEL_PATCH"]},
            "LTXVDurationPredictor": {"input": {"required": {}}, "output": ["INT", "FLOAT"]}}


# ---------------------------------------------------------------------------
# the script and the IR
# ---------------------------------------------------------------------------

class ParseTest(unittest.TestCase):
    def shot(self, dur: str, extra: str = "") -> ir.Shot:
        text = f"= ep01  T\n\n# sq01  kitchen\n\n## sh010\nwho: ada\nsize: ws\ndur: {dur}\n{extra}Ada.\n"
        return parse_story(text, {"ada"}, {"ada"}).sequences[0].shots[0]

    def test_model_and_clamp(self):
        self.assertEqual(self.shot("model").timing, {"model": True})
        self.assertEqual(self.shot("Model").timing, {"model": True})
        self.assertEqual(self.shot("model 3-8").timing, {"model": True, "min": 3.0, "max": 8.0})
        self.assertEqual(self.shot("model 1.5 - 12").timing,
                         {"model": True, "min": 1.5, "max": 12.0})
        # the IR round-trips through shots.json
        s = self.shot("model 3-8")
        self.assertEqual(ir.Shot.from_json(json.loads(json.dumps(s.to_json()))).timing,
                         s.timing)

    def test_bad_clamps(self):
        for bad in ("model 8-3", "model 0-4", "model 3", "model x-y", "model 3-8s"):
            with self.subTest(dur=bad), self.assertRaises(ScriptError):
                self.shot(bad)
        with self.assertRaises(ScriptError) as cm:
            self.shot("fast")
        self.assertIn("`auto` or `model`", str(cm.exception))

    def test_other_forms_unchanged(self):
        self.assertEqual(self.shot("3.04").timing, {"seconds": 3.04})
        self.assertEqual(self.shot("auto").timing, {"auto": True})
        self.assertEqual(self.shot("model", "audio: 1.0-3.5\n").timing,
                         {"audio_in": 1.0, "audio_out": 3.5})     # a window still wins
        legacy = parse_script(f"= ep01  T\n\n# sq01  kitchen\n\n## sh010\nwho: ada\nsize: ws\n"
                              f"dur: model 3-8\nAda.\n", {"ada"}, {"ada"})
        self.assertEqual(legacy["sequences"][0]["shots"][0]["duration_model"],
                         {"min": 3.0, "max": 8.0})


# ---------------------------------------------------------------------------
# the build: an estimate in the shotlist
# ---------------------------------------------------------------------------

class BuildTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.src = os.path.join(cls._tmp.name, "dm01")
        os.makedirs(cls.src)
        cls.check = build(cls.src)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def entry(self, sid: str, pass_: str = "proxy") -> tuple[dict, dict]:
        doc, i = J.find_shot(self.src, pass_, sid)
        return doc, doc["shots"][i]

    def test_capability(self):
        self.assertEqual(TG.load_target(LTX).duration, "predict")
        self.assertEqual(TG.load_target(LTX).describe()["capabilities"]["duration"], "predict")
        for t in (ING, H3):
            self.assertEqual(TG.load_target(t).describe()["capabilities"]["duration"], "script")
        p = TG.load_target(LTX).presets["proxy"]
        self.assertEqual(p.extra["duration_head"], HEAD)

    def test_ltx2_silent_estimate_is_the_preset_default(self):
        doc, e = self.entry("sh010")
        self.assertEqual(doc["target"], LTX)
        self.assertEqual((e["duration"], e["length"], e["length_estimated"]), (5.0, 121, True))
        self.assertEqual(e["duration_predict"], {"min_seconds": 1.0, "max_seconds": 20.0})
        # the dur: model preset keys stay out of the shotlist's defaults
        for k in TG.DURATION_PRESET_KEYS:
            self.assertNotIn(k, doc["defaults"])

    def test_ltx2_dialogue_estimate_and_clamp(self):
        _, e = self.entry("sh020")
        self.assertEqual(e["duration_predict"], {"min_seconds": 3.0, "max_seconds": 8.0})
        # the dialogue's `dur: auto` length, kept inside the clamp
        self.assertGreaterEqual(e["duration"], 3.0)
        self.assertLessEqual(e["duration"], 8.0)
        self.assertEqual(e["length"], TG.load_target(LTX).template.frames(e["duration"], 24))

    def test_other_forms_carry_no_estimate(self):
        _, e = self.entry("sh025")
        self.assertEqual(e["duration"], 2.5)
        self.assertNotIn("length_estimated", e)
        self.assertNotIn("duration_predict", e)

    def test_h3_falls_back_to_the_estimate_and_warns(self):
        doc, e = self.entry("sh030", "final")
        self.assertEqual(doc.get("target", H3), H3)
        self.assertEqual((e["duration"], e["length_estimated"]), (5.0, True))
        self.assertNotIn("duration_predict", e)
        for out in self.check:
            self.assertIn("sh030: `dur: model` renders the estimate, 5.00s: H3 can't predict",
                          out.replace("MiniMax H3", "H3"))

    def test_ingredients_falls_back_inside_the_clamp(self):
        doc, e = self.entry("sh040")
        self.assertEqual(doc["target"], ING)
        # silent: the 5 s default, clamped to the script's 2-4
        self.assertEqual((e["duration"], e["length"], e["length_estimated"]), (4.0, 97, True))
        self.assertNotIn("duration_predict", e)
        self.assertIn("sh040: `dur: model` renders the estimate, 4.00s: LTX+refs can't predict",
                      self.check[1])
        job = J.plan_job(self.src, "proxy", doc, doc["shots"].index(e), J.RenderRequest("sh040"),
                         {})
        self.assertEqual(job.length_source, "estimate")
        self.assertIn("`dur: model` rendered at the estimate, 4.04 s: LTX+refs can't predict",
                      " ".join(job.notes))

    def test_pacing_measures_the_estimate(self):
        r = subprocess.run([sys.executable, os.path.join(ROOT, "h3build.py"),
                            os.path.join(self.src, "series.json"),
                            os.path.join(self.src, "dm01.md"), "--pace"],
                           capture_output=True, env=ENV, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("sh020", r.stdout)


# ---------------------------------------------------------------------------
# queue time: the predictor, or the estimate
# ---------------------------------------------------------------------------

class QueueTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.src = os.path.join(cls._tmp.name, "dm01")
        os.makedirs(cls.src)
        build(cls.src)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.root = os.path.join(self._t.name, "dm01")
        shutil.copytree(self.src, self.root)
        self.base = J.load_graph(os.path.join(ROOT, "targets", "video", LTX, "workflow.json"))
        self.comfy = FakeComfy()

    def tearDown(self):
        self.comfy.close()
        self._t.cleanup()

    def plan(self, sid: str) -> J.Job:
        doc, i = J.find_shot(self.root, "proxy", sid)
        return J.plan_job(self.root, "proxy", doc, i, J.RenderRequest(sid), {})

    def graph(self, job: J.Job, take=None) -> dict:
        take = take or T.Take(job.id, job.take, job.pass_,
                              T.take_paths(self.root, job.pass_, job.id, job.take))
        return J.graph_for(self.base, job, take)

    def test_installed_head_drives_every_length(self):
        self.comfy.info = head_info(["other.safetensors", HEAD])
        job = self.plan("sh020")
        J.stage_inputs(job, J.Comfy(self.comfy.url))
        self.assertEqual((job.length_source, job.duration_head), ("predicted", HEAD))
        self.assertIn(f"length predicted by {HEAD} (3-8 s)", job.duration_note)
        g = self.graph(job)
        self.assertEqual(J.check_graph(g, OBJECT_INFO), [])
        (loader,) = of(g, "ModelPatchLoader")
        (pred,) = of(g, "LTXVDurationPredictor")
        self.assertEqual(g[loader]["inputs"], {"name": HEAD})
        pi = g[pred]["inputs"]
        self.assertEqual(pi["duration_head"], [loader, 0])
        self.assertEqual((pi["frame_rate"], pi["min_seconds"], pi["max_seconds"]),
                         (24.0, 3.0, 8.0))
        # the model and the positive conditioning the workflow already has
        guider = g[of(g, "LTXVDualCFGGuider")[0]]["inputs"]
        self.assertEqual(pi["model"], guider["model"])
        self.assertEqual(g[pi["model"][0]]["class_type"], "UNETLoader")
        cond = g[J.node_of(g, "LTXVConditioning")]["inputs"]
        self.assertEqual(pi["positive"], cond["positive"])
        self.assertEqual(g[pi["positive"][0]]["inputs"]["text"], job.prompt)
        # num_frames replaces the widget in every length input the binding patches
        self.assertEqual(g[J.node_of(g, "EmptyLTXVLatentVideo")]["inputs"]["length"], [pred, 0])
        self.assertEqual(g[J.node_of(g, "LTXVEmptyLatentAudio")]["inputs"]["frames_number"],
                         [pred, 0])

    def test_predicted_with_a_keyframe_and_a_lora(self):
        self.comfy.info = head_info([HEAD])
        job = self.plan("sh010")
        job.loras = [{"name": "x.safetensors", "strength": 0.5}]
        J.stage_inputs(job, J.Comfy(self.comfy.url))
        take = T.Take(job.id, job.take, job.pass_,
                      T.take_paths(self.root, job.pass_, job.id, job.take))
        g = J.graph_for(self.base, job, take, inputs={"last": "h3pipe/b.png"})
        self.assertEqual(J.check_graph(g, OBJECT_INFO), [])
        pi = g[J.node_of(g, "LTXVDurationPredictor")]["inputs"]
        self.assertEqual(g[pi["model"][0]]["class_type"], "LoraLoaderModelOnly")
        self.assertEqual((pi["min_seconds"], pi["max_seconds"]), (1.0, 20.0))

    def test_missing_head_renders_the_estimate(self):
        self.comfy.info = head_info([])                  # models/model_patches is empty
        job = self.plan("sh010")
        J.stage_inputs(job, J.Comfy(self.comfy.url))
        self.assertEqual((job.length_source, job.duration_head), ("estimate", ""))
        self.assertEqual(job.duration_note,
                         f"LTX-2 duration head not installed (models/model_patches: {HEAD}); "
                         f"used the estimate 5.04 s")
        g = self.graph(job)
        self.assertFalse(of(g, "LTXVDurationPredictor") + of(g, "ModelPatchLoader"))
        self.assertEqual(g[J.node_of(g, "EmptyLTXVLatentVideo")]["inputs"]["length"], 121)
        take = J.start_job(job)
        sc = T.read_sidecar(take.paths.sidecar)
        self.assertEqual((sc["length"], sc["length_source"]), (121, "estimate"))
        self.assertIn(job.duration_note, sc["notes"])

    def test_old_comfy_or_none_to_ask(self):
        job = self.plan("sh010")                         # this ComfyUI has neither node
        J.stage_inputs(job, J.Comfy(self.comfy.url))
        self.assertEqual(job.length_source, "estimate")
        self.assertIn("this ComfyUI has no ModelPatchLoader node", job.duration_note)
        J.stage_inputs(job)                              # a dry run: nothing asked
        self.assertEqual(job.length_source, "estimate")
        self.assertIn("wasn't checked", job.duration_note)
        # re-planning replaces the note rather than piling them up
        self.assertEqual(sum("duration head" in n for n in job.notes), 1)
        J.stage_inputs(job, J.Comfy("http://127.0.0.1:9"))   # nothing listening
        self.assertEqual(job.length_source, "estimate")
        self.assertIn("couldn't ask ComfyUI", job.duration_note)

    def test_render_records_the_real_length_and_status_shows_it(self):
        self.comfy.info = head_info([HEAD])
        self.comfy.predicted_frames = 199                # the model chose 8.29 s
        out = E.queue_shots(self.root, "proxy", ["sh010", "sh025"], J.RenderRequest(""),
                            J.Comfy(self.comfy.url), lambda tid: self.base)
        self.assertEqual(out["errors"], [])
        self.assertEqual(len(out["queued"]), 2)
        st = E.episode_status(self.root, "proxy")
        by = {s["shot"]: s for s in st["shots"]}
        s = by["sh010"]
        self.assertEqual((s["length"], s["seconds"]), (121, round(121 / 24, 3)))   # the estimate
        self.assertEqual(s["cut"]["frames"], 199)                                 # the take
        self.assertEqual(s["takes"][0]["frames"], 199)
        sc = T.read_sidecar(T.list_takes(self.root, "proxy", "sh010")[0].paths.sidecar)
        self.assertEqual((sc["length"], sc["length_source"], sc["frames"]),
                         (121, "predicted", 199))
        self.assertEqual(by["sh025"]["cut"]["frames"], 65)
        self.assertEqual(by["sh030"]["cut"]["frames"], None)                      # no take
        self.assertEqual(by["sh030"]["takes"], [])


if __name__ == "__main__":
    unittest.main()
