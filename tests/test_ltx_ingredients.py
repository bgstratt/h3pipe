"""
The ltx2_ingredients target: LTX-2.3 with the IC-LoRA "ingredients" reference
sheet (targets/video/ltx2_ingredients).

  - golden-style: kitchen_sink compiled for ltx2_ingredients (what retargeting
    each shot gives), both passes: the shotlist doc and report, and the prompts
    (tests/golden/ltx2_ingredients/; `python tests/test_ltx_ingredients.py
    --update` rewrites them, intended changes only)
  - the two-part prompt's form
  - the sheet: its layout, and composing it from small PNGs (skipped without PIL)
  - missing refs: blocked; rendering anyway leaves them off the sheet and the
    prompt; none at all renders text-only
  - the flattened, patched graph passes check_graph against the trimmed
    /object_info; the sheet is uploaded, kept in the take, and in the sidecar
"""
from __future__ import annotations

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
import h3edit as E  # noqa: E402
import h3jobs as J  # noqa: E402
import h3takes as T  # noqa: E402
import h3_refsheet as RS  # noqa: E402
import targets as TG  # noqa: E402
from h3core import ir  # noqa: E402
from h3core.series_config import (character_ids, load_series_config,  # noqa: E402
                                  series_info, subject_ids, variant_of)
from h3core.story import parse_story  # noqa: E402
from test_render import FIXTURE, FakeComfy, build_episode  # noqa: E402

try:
    from PIL import Image
except ImportError:                                       # the sheet needs PIL
    Image = None

ING, H3 = "ltx2_ingredients", "minimax_h3_ref2va"
GOLDEN = os.path.join(HERE, "golden", ING)
WF = os.path.join(HERE, "fixtures", "workflows")
OBJECT_INFO = json.load(open(os.path.join(WF, "object_info_ltx.json"), encoding="utf-8"))
IC = "ltx-2.3-22b-ic-lora-ingredients-0.9.safetensors"
needs_pil = unittest.skipIf(Image is None, "composing a reference sheet needs PIL")


def kitchen_sink():
    series_cfg = load_series_config(os.path.join(FIXTURE, "series.json"))
    with open(os.path.join(FIXTURE, "script.md"), encoding="utf-8") as fh:
        story = parse_story(fh.read(), subject_ids(series_cfg), character_ids(series_cfg),
                            series_info(series_cfg), variant_of(series_cfg))
    return series_cfg, story


def capture() -> dict[str, str]:
    """kitchen_sink compiled for ltx2_ingredients: golden file name -> text."""
    series_cfg, story = kitchen_sink()
    t = TG.load_target(ING, "video")
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


def png(path: str, size=(64, 64), colors=((200, 30, 30),)) -> None:
    """A PNG split into len(colors) vertical bands (a 4-panel strip)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img = Image.new("RGB", size)
    n = len(colors)
    for i, c in enumerate(colors):
        img.paste(c, (i * size[0] // n, 0, (i + 1) * size[0] // n, size[1]))
    img.save(path)


STRIP = ((255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0))   # body, side, back, face


def real_refs(root: str) -> None:
    """Every panel file of the kitchen_sink series config, as small PNGs."""
    cfg = json.load(open(os.path.join(root, "series.json"), encoding="utf-8"))
    for e in cfg["subjects"].values():
        if isinstance(e, dict) and e.get("sheet"):
            if e.get("kind", "character") == "character":
                png(os.path.join(root, e["sheet"]), (256, 64), STRIP)
            else:
                png(os.path.join(root, e["sheet"]), (48, 48), ((250, 250, 250),))
    for e in cfg["locations"].values():
        if isinstance(e, dict) and e.get("plate"):
            png(os.path.join(root, e["plate"]), (160, 90), ((10, 120, 200),))


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
                              f"`python tests/test_ltx_ingredients.py --update`")
                with open(path, encoding="utf-8") as fh:
                    self.assertEqual(text, fh.read().replace("\r\n", "\n"), name)


# ---------------------------------------------------------------------------
# the target
# ---------------------------------------------------------------------------

class TargetTest(unittest.TestCase):
    def setUp(self):
        self.t = TG.load_target(ING, "video")
        self.cfg, self.story = kitchen_sink()

    def test_template_is_the_shot_length(self):
        # the shot's own length on the 8k+1 grid, 2 s to 20 s at 24 fps
        tp = self.t.template
        self.assertEqual([tp.snap(n) for n in (1, 49, 60, 72, 73, 120, 121, 481)],
                         [49, 49, 65, 73, 73, 121, 121, 481])
        self.assertEqual({(tp.snap(n) - 1) % 8 for n in range(1, 482)}, {0})
        with self.assertRaises(ValueError):
            tp.snap(482)
        self.assertEqual(tp.fps_for({"series": {"fps": 25}}), 24.0)
        self.assertEqual(tp.fit_size(768, 448), (768, 448))

    def test_presets(self):
        f, p = self.t.presets["final"], self.t.presets["proxy"]
        self.assertEqual((f.model, f.lora, f.width, f.height, f.steps),
                         ("ltx-2.3-22b-distilled-fp8.safetensors", IC, 768, 448, 8))
        self.assertEqual((p.width, p.height, p.lora), (512, 288, IC))
        self.assertEqual((p.width % 32, p.height % 32), (0, 0))
        self.assertEqual(f.extra["text_encoder"], "gemma_3_12B_it_fp4_mixed.safetensors")
        # another target's series block lends no size: the IC-LoRA has one bucket
        doc, _ = self.t.compile_episode(self.story, self.cfg, "final")
        self.assertEqual((doc["defaults"]["width"], doc["defaults"]["height"]), (768, 448))
        self.assertEqual(doc["defaults"]["model"], f.model)
        doc, _ = self.t.compile_episode(self.story, self.cfg, "proxy")
        self.assertEqual((doc["defaults"]["width"], doc["defaults"]["height"]), (512, 288))

    def test_describe(self):
        d = self.t.describe()
        self.assertEqual((d["label"], d["short"]),
                         ("LTX-2.3 ingredients (character/plate refs)", "LTX+refs"))
        self.assertTrue(d["capabilities"]["subject_refs"])
        self.assertTrue(d["capabilities"]["reference_sheet"])
        self.assertEqual(d["capabilities"]["policies"], ["generate"])
        self.assertEqual(d["capabilities"]["policy_fallback"], "generate")
        # ltx2 draws a sheet too now (its own 2.5 IC-LoRA), but only when the
        # refs and the LoRA are there; here it is the target's whole point
        self.assertTrue(TG.load_target("ltx2").describe()["capabilities"]["reference_sheet"])
        self.assertEqual(TG.load_target("ltx2").describe()["capabilities"]["prompt"], "prose")
        self.assertEqual(self.t.audio_policy("clone")[0], "generate")

    def test_length_warning_and_too_long(self):
        sq = self.story.sequences[0]
        shot = sq.shots[0]
        old = shot.timing
        try:
            shot.timing = {"seconds": 6.0}                      # renders, softly warned
            doc, report = self.t.compile_episode(self.story, self.cfg, "final", only={shot.id})
            self.assertEqual(doc["shots"][0]["length"], 145)
            self.assertIn(f"trained at 121 frames; identity may weaken at other lengths "
                          f"(1 shot, frames): {shot.id} 145", " ".join(report["warnings"]))
            shot.timing = {"seconds": 5.0}                      # the bucket: no warning
            _, report = self.t.compile_episode(self.story, self.cfg, "final", only={shot.id})
            self.assertNotIn("trained at", " ".join(report["warnings"]))
            shot.timing = {"seconds": 20.5}                     # past 481 frames
            with self.assertRaises(ValueError) as cm:
                self.t.compile_episode(self.story, self.cfg, "final", only={shot.id})
            self.assertIn("maximum of 481 frames", str(cm.exception))
            self.assertIn("Split the shot", str(cm.exception))
        finally:
            shot.timing = old

    def test_panels_order_and_views(self):
        doc, report = self.t.compile_episode(self.story, self.cfg, "final")
        by = {s["id"]: s for s in doc["shots"]}
        # characters, then props and vehicles, then the plate
        self.assertEqual([p.get("subject") or p["location"] for p in by["sh040"]["panels"]],
                         ["bo", "van", "kettle", "kitchen"])
        self.assertEqual(by["sh040"]["panels"][0]["view"], "body")
        self.assertEqual(by["sh030"]["panels"][0]["view"], "face")   # one character, cu
        self.assertEqual(by["sh140"]["panels"], [{"location": "street", "kind": "plate",
                                                  "path": "refs/_bg/street.png"}])
        # the refs every sheet is made of are needed, and block their shots
        self.assertIn("refs/ada/ada_sheet_4panel.png", report["needed"])
        self.assertIn("sh010", report["blocked_shots"]["refs/_bg/kitchen.png"])
        reqs = self.t.required_refs(self.story.sequences[0].shots[1], self.cfg,
                                    {"sequence": self.story.sequences[0]})
        self.assertEqual([(r.subject or r.location, r.shape) for r in reqs],
                         [("ada", "sheet"), ("bo", "sheet"), ("kettle", "object"),
                          ("kitchen", "plate")])


class PromptTest(unittest.TestCase):
    def setUp(self):
        from targets.video.ltx2_ingredients.prompt import build_prompt
        self.build = build_prompt
        self.cfg, _ = kitchen_sink()
        self.sq = ir.Sequence("sq01", "kitchen")
        self.shot = ir.Shot("sh1", cast=["ada"], props=["kettle"], size="medium",
                            action="Ada lifts the kettle.",
                            dialogue=[ir.Line("ada", "on", "warmly", "Tea?")])
        self.panels = [{"subject": "ada", "kind": "character", "path": "a", "view": "body"},
                       {"subject": "kettle", "kind": "prop", "path": "k"},
                       {"location": "kitchen", "kind": "plate", "path": "p"}]

    def test_two_part_form(self):
        p = self.build(self.shot, self.sq, self.cfg, self.panels)
        first, second = p.split("\n\n")                  # exactly two paragraphs
        self.assertTrue(first.startswith("Reference sheet: "))
        self.assertTrue(second.startswith("Generated video: Style: a flat vector cartoon"))
        self.assertEqual(
            first,
            "Reference sheet: Ada, a tall woman in her thirties with a short grey bob, a "
            "mustard apron over a striped shirt, and round red glasses, shown full-body in a "
            "three-quarter view. The diner kettle, a dented chrome kettle with a black bakelite "
            "handle and a whistle cap on a chain. The location, a cramped diner kitchen with "
            "steel counters, a hanging ticket rail and cold fluorescent light from overhead.")
        # on the sheet: named in the prose, not described again
        self.assertIn("Ada and the diner kettle are in frame.", second)
        self.assertNotIn("grey bob", second)
        self.assertIn('Ada, in a voice that is dry, quick and precise, says, warmly: "Tea?"',
                      second)
        self.assertNotIn("<", p)

    def test_face_view_and_left_off(self):
        panels = [dict(self.panels[0], view="face"), self.panels[2]]
        p = self.build(self.shot, self.sq, self.cfg, panels)
        self.assertIn("round red glasses, shown in a face close-up.", p)
        # the kettle isn't on the sheet: not in the first half, described in the prose
        first, second = p.split("\n\n")
        self.assertNotIn("kettle", first)
        self.assertIn("The diner kettle is a dented chrome kettle", second)

    def test_no_panels_is_ltx2_prose(self):
        from targets.video.ltx2.prompt import build_prompt as prose
        p = self.build(self.shot, self.sq, self.cfg, [])
        self.assertEqual(p, prose(self.shot, self.sq, self.cfg))
        self.assertNotIn("Reference sheet", p)


# ---------------------------------------------------------------------------
# the sheet
# ---------------------------------------------------------------------------

class LayoutTest(unittest.TestCase):
    CASES = [([1.0, 16 / 9], (768, 448)), ([1.0, 1.0, 16 / 9], (512, 288)),
             ([1.0] * 3 + [1.78], (768, 448)), ([1.0] * 5 + [1.78], (512, 288)),
             ([0.5], (512, 288)), ([1.0], (768, 448)), ([16 / 9], (768, 448))]

    def test_boxes(self):
        for aspects, (w, h) in self.CASES:
            fits = ["figure"] * (len(aspects) - 1) + ["cover" if aspects[-1] > 1.5 else "figure"]
            with self.subTest(aspects=aspects):
                places = RS.layout(aspects, fits, w, h, 8)
                self.assertEqual(len(places), len(aspects))
                boxes = [p["box"] for p in places]
                for x, y, bw, bh in boxes:
                    self.assertTrue(0 <= x and 0 <= y and x + bw <= w and y + bh <= h)
                for i, a in enumerate(boxes):
                    for b in boxes[i + 1:]:
                        self.assertFalse(a[0] < b[0] + b[2] and b[0] < a[0] + a[2]
                                         and a[1] < b[1] + b[3] and b[1] < a[1] + a[3])
                # the cells tile the sheet: no border, only the gaps between them
                cells = [p["cell"] for p in places]
                self.assertAlmostEqual(min(c[0] for c in cells), 0, delta=0.01)
                self.assertAlmostEqual(min(c[1] for c in cells), 0, delta=0.01)
                self.assertAlmostEqual(max(c[0] + c[2] for c in cells), w, delta=0.6)
                self.assertAlmostEqual(max(c[1] + c[3] for c in cells), h, delta=0.6)
                for a, f, p in zip(aspects, fits, places):
                    l, t, r, b = p["crop"]
                    bw, bh = p["box"][2], p["box"][3]
                    # the picture keeps its proportions (only cropped, never squashed)
                    self.assertAlmostEqual(bw / bh, a * (r - l) / (b - t), delta=0.05 * bw / bh)
                    if f == "figure":
                        self.assertEqual((t, b), (0.0, 1.0))        # never loses head or feet
                        self.assertGreaterEqual(r - l, RS.MIN_KEEP - 1e-9)
        # one character and the plate: side by side, full height, no black bands
        places = RS.layout([1.0, 16 / 9], ["figure", "cover"], 512, 288, 6)
        self.assertEqual([p["box"][3] for p in places], [288, 288])
        # two characters and the plate: nothing is a sliver
        places = RS.layout([1.0, 1.0, 16 / 9], ["figure", "figure", "cover"], 512, 288, 6)
        self.assertTrue(all(p["box"][2] >= 100 and p["box"][3] >= 100 for p in places))

    def test_fit(self):
        # a figure in a narrow cell: sides cropped, at most to MIN_KEEP, then black above/below
        crop, box = RS.fit(1.0, 50, 100, "figure")
        self.assertEqual((crop[1], crop[3]), (0.0, 1.0))
        self.assertAlmostEqual(crop[2] - crop[0], 0.6)
        self.assertAlmostEqual(box[2], 50)
        self.assertLess(box[3], 100)
        # in a wide cell: whole, as tall as the cell, black at the sides
        crop, box = RS.fit(1.0, 200, 100, "figure")
        self.assertEqual(crop, (0.0, 0.0, 1.0, 1.0))
        self.assertEqual((box[2], box[3]), (100, 100))
        # the plate covers its cell either way
        for cw, ch in ((50, 100), (400, 100)):
            crop, box = RS.fit(16 / 9, cw, ch, "cover")
            self.assertEqual(box, (0.0, 0.0, cw, ch))

    @needs_pil
    def test_compose(self):
        with tempfile.TemporaryDirectory() as tmp:
            strip, plate = os.path.join(tmp, "s.png"), os.path.join(tmp, "p.png")
            png(strip, (400, 100), STRIP)
            png(plate, (160, 90), ((10, 120, 200),))
            out = os.path.join(tmp, "sheet.png")
            spec = {"width": 256, "height": 144, "background": "black", "gap": 0.02,
                    "panels": [{"path": strip, "crop": {"panels": 4, "index": 3},
                                "fit": "figure"},
                               {"path": strip, "crop": {"panels": 4, "index": 0},
                                "fit": "figure"},
                               {"path": plate, "fit": "cover"}]}
            rep = RS.compose(spec, out)
            img = Image.open(out).convert("RGB")
            self.assertEqual(img.size, (256, 144))
            px = img.load()
            # black between the panels, but no black band across the sheet
            self.assertTrue(any(px[x, y] == (0, 0, 0) for x in range(256) for y in range(144)))
            def longest_band(lines):
                run = best = 0
                for black in lines:
                    run = run + 1 if black else 0
                    best = max(best, run)
                return best
            self.assertEqual(longest_band(all(px[x, y] == (0, 0, 0) for x in range(256))
                                          for y in range(144)), 0)
            self.assertLess(longest_band(all(px[x, y] == (0, 0, 0) for y in range(144))
                                         for x in range(256)), 256 * 0.1)
            centre = [img.getpixel((x + w // 2, y + h // 2)) for x, y, w, h in rep["boxes"]]
            self.assertEqual(centre[0], (255, 255, 0))                  # the face panel
            self.assertEqual(centre[1], (255, 0, 0))                    # the body panel
            self.assertEqual(centre[2], (10, 120, 200))                 # the plate
            # the pipeline's way: a subprocess of the running Python
            from targets.video.ltx2_ingredients import sheet as S
            out2 = os.path.join(tmp, "sheet2.png")
            self.assertEqual(S.compose(spec, out2)["boxes"], rep["boxes"])
            self.assertEqual(T.file_sha1(out), T.file_sha1(out2))
            with self.assertRaises(S.SheetError):
                S.compose(dict(spec, panels=[{"path": os.path.join(tmp, "gone.png")}]), out2)


# ---------------------------------------------------------------------------
# rendering: missing refs, the graph, the take
# ---------------------------------------------------------------------------

class RenderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.src = os.path.join(cls._tmp.name, "ks01")
        os.makedirs(cls.src)
        build_episode(cls.src, refs=False)
        shutil.copy(os.path.join(FIXTURE, "series.json"), cls.src)
        cls.base = J.load_graph(os.path.join(ROOT, "targets", "video", ING, "workflow.json"))

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
        return J.plan_job(self.root, pass_, doc, i, J.RenderRequest(sid, target=ING, **req), {})

    def graph(self, job, take=None, inputs=None):
        take = take or T.Take(job.id, job.take, job.pass_,
                              T.take_paths(self.root, job.pass_, job.id, job.take))
        return J.graph_for(self.base, job, take, inputs=inputs)

    def test_missing_refs_block(self):
        job = self.plan("sh020")
        self.assertEqual((job.target, job.built_target), (ING, H3))
        self.assertEqual(job.action, "blocked")
        self.assertEqual([r["slot"] for r in job.missing],
                         ["sheet panel 1", "sheet panel 2", "sheet panel 3", "sheet panel 4"])
        self.assertEqual(job.missing[0]["subject"], "ada")
        self.assertEqual(job.missing[3]["location"], "kitchen")

    @needs_pil
    def test_render_anyway_leaves_them_out(self):
        real_refs(self.root)
        os.remove(os.path.join(self.root, "refs", "bo", "bo_sheet_4panel.png"))
        os.remove(os.path.join(self.root, "refs", "_bg", "kitchen.png"))
        job = self.plan("sh020", allow_missing_refs=True)
        self.assertEqual((job.action, job.missing_mode), ("render", "recompiled"))
        self.assertEqual([r["subject"] if "subject" in r else r["location"] for r in job.missing],
                         ["bo", "kitchen"])
        sheet = [p.get("subject") or p.get("location") for p in job.recompiled["panels"]]
        self.assertEqual(sheet, ["ada", "kettle"])
        first, second = job.prompt.split("\n\n")
        self.assertNotIn("Bo,", first)
        self.assertNotIn("location", first)
        self.assertIn("Bo is a stocky teenage boy", second)       # still in words
        comfy = FakeComfy()
        try:
            got = J.stage_inputs(job, J.Comfy(comfy.url))
            take = J.start_job(job)
        finally:
            comfy.close()
        self.assertEqual(list(got), ["sheet"])
        self.assertEqual(T.read_json(take.paths.shotlist)["shots"][0]["panels"],
                         job.recompiled["panels"])
        g = self.graph(job, take)
        self.assertEqual(J.check_graph(g, OBJECT_INFO), [])

    def test_no_refs_at_all_is_text_only(self):
        job = self.plan("sh140", allow_missing_refs=True)        # the plate only
        self.assertEqual(job.missing_mode, "recompiled")
        self.assertEqual(job.recompiled["panels"], [])
        self.assertFalse(job.prompt.startswith("Reference sheet"))
        self.assertTrue(job.prompt.startswith("Style:"))
        self.assertEqual(J.stage_inputs(job), {})
        self.assertIn("text-only", " ".join(job.notes))
        g = self.graph(job)
        self.assertEqual(J.check_graph(g, OBJECT_INFO), [])
        for gone in ("LTXVAddGuide", "LTXVCropGuides", "LoadImage", "RepeatImageBatch",
                     "GetICLoRAParameters", "LoraLoaderModelOnly"):
            self.assertFalse(of(g, gone), gone)
        sampler = g[J.node_of(g, "KSampler")]["inputs"]
        self.assertEqual(g[sampler["model"][0]]["class_type"], "CheckpointLoaderSimple")
        self.assertEqual(g[sampler["positive"][0]]["class_type"], "LTXVConditioning")

    @needs_pil
    def test_graph_take_and_sidecar(self):
        real_refs(self.root)
        job = self.plan("sh020")
        self.assertEqual((job.action, job.missing), ("render", []))
        self.assertEqual(job.loras, [{"name": IC, "strength": 1.0}])
        self.assertTrue(job.prompt.startswith("Reference sheet: Ada"))
        self.assertEqual(job.frames, 89)                      # its own length, not the 121 bucket
        with self.assertRaises(ValueError):                  # never silently text-only
            self.graph(job)
        # a dry run composes nothing and writes nothing
        self.assertTrue(J.stage_inputs(job)["sheet"].endswith("_refsheet.png"))
        self.assertEqual(job.staged, [])
        comfy = FakeComfy()
        try:
            got = J.stage_inputs(job, J.Comfy(comfy.url))
            self.assertEqual(list(comfy.uploads), [got["sheet"]])
            take = J.start_job(job)
        finally:
            comfy.close()
        sheet = os.path.join(take.paths.dir, take.paths.stem + "_refsheet.png")
        self.assertTrue(os.path.isfile(sheet))
        self.assertEqual(comfy.uploads[got["sheet"]], open(sheet, "rb").read())
        self.assertEqual(Image.open(sheet).size, (512, 288))
        self.assertFalse([f for f in os.listdir(take.paths.dir) if f.startswith(".tmp")])
        sc = T.read_sidecar(take.paths.sidecar)
        self.assertEqual(sc["target"], ING)
        self.assertEqual(sc["inputs"], got)
        refs = {r["slot"]: r for r in sc["refs"]}
        self.assertEqual(refs["reference sheet"]["path"], "renders_proxy/sh020/sh020_t01_refsheet.png")
        self.assertEqual(refs["reference sheet"]["sha1"], T.file_sha1(sheet))
        self.assertTrue(all(r["sha1"] for r in sc["refs"]))
        self.assertEqual(len(sc["refs"]), 5)                     # 4 panels + the sheet
        self.assertEqual((sc["length"], sc["length_source"]), (89, "script"))

        g = self.graph(job, take)
        self.assertEqual(J.check_graph(g, OBJECT_INFO), [])
        for gone in ("TextGenerateLTX2Prompt", "ComfySwitchNode", "LoraLoader", "PreviewAny",
                     "CreateVideo", "SaveVideo", "PrimitiveInt", "ComfyMathExpression",
                     "GetImageSize"):
            self.assertFalse(of(g, gone), gone)
        self.assertEqual(g[J.node_of(g, "LoadImage")]["inputs"]["image"], got["sheet"])
        self.assertEqual(g[J.node_of(g, "RepeatImageBatch")]["inputs"]["amount"], 89)
        pad = g[J.node_of(g, "ResizeAndPadImage")]["inputs"]
        self.assertEqual((pad["target_width"], pad["target_height"], pad["padding_color"]),
                         (512, 288, "black"))
        lat = g[J.node_of(g, "EmptyLTXVLatentVideo")]["inputs"]
        self.assertEqual((lat["width"], lat["height"], lat["length"]), (512, 288, 89))
        au = g[J.node_of(g, "LTXVEmptyLatentAudio")]["inputs"]
        self.assertEqual((au["frames_number"], au["frame_rate"]), (89, 24.0))
        ks = g[J.node_of(g, "KSampler")]["inputs"]
        self.assertEqual((ks["seed"], ks["steps"]), (job.seed, 8))
        lo = g[J.node_of(g, "LoraLoaderModelOnly")]["inputs"]
        self.assertEqual((lo["lora_name"], lo["strength_model"]), (IC, 1.0))
        self.assertEqual({g[k]["inputs"]["ckpt_name"] for k in
                          of(g, "CheckpointLoaderSimple") + of(g, "LTXVAudioVAELoader")
                          + of(g, "LTXAVTextEncoderLoader")},
                         {"ltx-2.3-22b-distilled-fp8.safetensors"})
        self.assertEqual({g[k]["inputs"]["text"] for k in of(g, "CLIPTextEncode")},
                         {job.prompt, job.shot["negative"]})
        guide = g[J.node_of(g, "LTXVAddGuide")]["inputs"]
        self.assertEqual(g[guide["image"][0]]["class_type"], "ResizeAndPadImage")
        sv = g[J.node_of(g, "H3SaveShot")]["inputs"]
        self.assertEqual((sv["shot_id"], sv["fps"], sv["save_frames"]), ("sh020", 24.0, False))
        self.assertEqual(g[sv["images"][0]]["class_type"], "VAEDecodeTiled")
        self.assertEqual(g[sv["audio"][0]]["class_type"], "LTXVAudioVAEDecode")

        # re-picking a view makes the take ref-stale
        doc, i = J.find_shot(self.root, "proxy", "sh020")
        rdoc, rshot = J.retarget(self.root, "proxy", doc, doc["shots"][i], ING)
        self.assertEqual(J.stale_reasons(self.root, rdoc, rshot, sc), [])
        png(os.path.join(self.root, "refs", "bo", "bo_sheet_4panel.png"), (256, 64),
            tuple(reversed(STRIP)))
        self.assertEqual(J.stale_reasons(self.root, rdoc, rshot, sc), ["ref"])

    @needs_pil
    def test_lora_override_keeps_the_ic_lora(self):
        real_refs(self.root)
        job = self.plan("sh020", loras=[])                       # `lora: none`
        J.stage_inputs(job)
        self.assertEqual(job.loras, [{"name": IC, "strength": 1.0}])
        self.assertIn("put first in the LoRA list", " ".join(job.notes))
        job = self.plan("sh020", loras=[{"name": "style.safetensors", "strength": 0.5}])
        J.stage_inputs(job)
        self.assertEqual([lo["name"] for lo in job.loras], [IC, "style.safetensors"])

    @needs_pil
    def test_queue_shots(self):
        real_refs(self.root)
        comfy = FakeComfy()
        try:
            out = E.queue_shots(self.root, "proxy", ["sh010"], J.RenderRequest("", target=ING),
                                J.Comfy(comfy.url), lambda tid: self.base)
        finally:
            comfy.close()
        self.assertEqual([(q["shot"], q["target"]) for q in out["queued"]], [("sh010", ING)])
        take = T.list_takes(self.root, "proxy", "sh010")[0]
        self.assertEqual(take.status, "ok")
        self.assertTrue(os.path.isfile(os.path.join(take.paths.dir,
                                                    take.paths.stem + "_refsheet.png")))
        self.assertEqual(len(comfy.uploads), 1)


if __name__ == "__main__":
    if "--update" in sys.argv:
        update()
    else:
        unittest.main()
