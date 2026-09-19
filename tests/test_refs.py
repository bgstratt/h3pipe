"""
h3refs: refs listed from the bible, ref takes, picks (with the character sheet
stitch), import, overrides, generating against test_render's FakeComfy, and the
kreagen CLI rebuilt on top of it.
"""
from __future__ import annotations

import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
import h3edit as E  # noqa: E402
import h3jobs as J  # noqa: E402
import h3refs as R  # noqa: E402
import h3takes as T  # noqa: E402
from test_render import ENV, FIXTURE, FakeComfy, png_bytes, stub_refs  # noqa: E402

REFS_WF = os.path.join(ROOT, "workflows", R.REFS_WORKFLOW)
H3_WF = os.path.join(ROOT, "workflows", J.WORKFLOW_NAME)

try:
    import PIL  # noqa: F401
    HAVE_PIL = True
except ImportError:                                      # pragma: no cover
    HAVE_PIL = False

_BUILT = None


def built_episode() -> str:
    """A built kitchen_sink episode (both passes), made once and copied per test."""
    global _BUILT
    if _BUILT is None:
        tmp = tempfile.mkdtemp(prefix="h3refs_")
        ep = os.path.join(tmp, "ks01")
        os.makedirs(ep)
        shutil.copy(os.path.join(FIXTURE, "series.json"), ep)
        shutil.copy(os.path.join(FIXTURE, "script.md"), os.path.join(ep, "ks01.md"))
        r = E.build_episode(ep)
        assert r["ok"], r
        _BUILT = ep
    return _BUILT


def tearDownModule():
    if _BUILT:
        shutil.rmtree(os.path.dirname(_BUILT), ignore_errors=True)


class RefsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.ep = os.path.join(self.tmp, "ks01")
        shutil.copytree(built_episode(), self.ep)
        self.s = R.load_series(self.ep)
        self.comfy = None

    def tearDown(self):
        if self.comfy:
            self.comfy.close()
        self._tmp.cleanup()

    def fake(self) -> FakeComfy:
        if self.comfy is None:
            self.comfy = FakeComfy()
        return self.comfy

    def gen(self, ref, view=None, rng=None, **kw):
        c = self.fake()
        base = J.load_graph(REFS_WF)
        return R.queue_generate(self.s, R.GenRequest(ref, view, **kw),
                                J.Comfy(c.url), base, rng=rng or random.Random(3))

    def png(self, name="in.png", w=64, h=32, rgb=(1, 2, 3)) -> str:
        p = os.path.join(self.tmp, name)
        with open(p, "wb") as fh:
            fh.write(png_bytes(w, h, rgb))
        return p

    def ref(self, rid):
        return R.find_ref(self.s, rid)

    def listing(self):
        return {r["id"]: r for r in R.list_refs(self.ep)}


class ListingTest(RefsTest):
    def test_every_bible_ref_with_usage(self):
        refs = self.listing()
        self.assertEqual(
            [i for i in refs],
            ["subject:ada", "subject:bo", "subject:cy", "subject:rex", "subject:narrator",
             "subject:kettle", "subject:van", "location:kitchen", "location:kitchen_window",
             "location:street", "voice:ada", "voice:bo", "voice:cy", "voice:narrator"])
        ada = refs["subject:ada"]
        self.assertEqual((ada["scope"], ada["kind"], ada["name"], ada["path"]),
                         ("series", "character", "Ada", "refs/ada/ada_sheet_4panel.png"))
        self.assertEqual([v["view"] for v in ada["views"]], R.VIEW_TAGS)
        self.assertEqual((ada["takes"], ada["picked"], ada["exists"], ada["sha1"]),
                         ([], None, False, None))
        todo = {i["path"]: i for i in json.load(open(os.path.join(self.ep, "refs_todo.json"),
                                                     encoding="utf-8"))}
        self.assertEqual(ada["used_by"]["final"], todo[ada["path"]]["blocks_shots"])
        self.assertEqual(refs["subject:kettle"]["kind"], "prop")
        self.assertEqual(refs["subject:van"]["kind"], "vehicle")
        self.assertEqual(refs["subject:rex"]["kind"], "character")   # no kind: a character
        self.assertEqual(refs["location:street"]["used_by"]["proxy"],
                         todo["refs/_bg/street.png"]["blocks_shots"])
        # the proxy generates its voices, so nothing reads the samples there
        self.assertEqual(refs["voice:ada"]["used_by"]["proxy"], [])
        self.assertEqual(refs["voice:ada"]["used_by"]["final"],
                         todo["audio/voices/ada_sample.wav"]["blocks_shots"])
        self.assertFalse(refs["voice:ada"]["can_generate"])
        # the narrator is never on screen: a sheet nobody reads
        self.assertEqual(refs["subject:narrator"]["used_by"], {"final": [], "proxy": []})
        # one source of wording: the listing's prompts are refs_todo's
        for rid, path in (("subject:ada", ada["path"]), ("subject:kettle", "refs/props/kettle.png"),
                          ("location:kitchen", "refs/_bg/kitchen.png"),
                          ("voice:bo", "audio/voices/bo_sample.wav")):
            self.assertEqual(refs[rid]["prompt"], todo[path]["prompt"], rid)
        v = ada["views"][1]
        self.assertEqual(v["prompt"], R.VIEW_TMPL.format(
            view=R.VIEW_DESC["02_side"], design=self.s.bible["subjects"]["ada"]["design"],
            look=self.s.look, w=1024, h=1024))
        self.assertEqual((v["effective"]["seed"], v["effective"]["seed_source"]),
                         (R.seed_for("ada"), "stable"))
        self.assertEqual(refs["location:kitchen"]["effective"]["seed"],
                         R.seed_for("refs/_bg/kitchen.png"))
        self.assertEqual((refs["location:kitchen"]["effective"]["width"],
                          refs["location:kitchen"]["effective"]["height"]), (1344, 768))

    def test_unused_subject_is_listed_and_generates(self):
        bible = json.load(open(os.path.join(self.ep, "series.json"), encoding="utf-8"))
        bible["subjects"]["zed"] = {"name": "Zed", "design": "a lanky man in a tan trench coat",
                                    "sheet": "refs/zed/zed_sheet_4panel.png"}
        with open(os.path.join(self.ep, "series.json"), "w", encoding="utf-8") as fh:
            json.dump(bible, fh)
        self.s = R.load_series(self.ep)
        zed = self.listing()["subject:zed"]
        self.assertEqual(zed["used_by"], {"final": [], "proxy": []})
        self.assertTrue(zed["can_generate"])
        out = self.gen("subject:zed")
        self.assertEqual(len(out["queued"]), 4)
        self.assertEqual({q["seed"] for q in out["queued"]}, {R.seed_for("zed")})

    def test_parent_folder_bible(self):
        shows = os.path.join(self.tmp, "Show")
        ep = os.path.join(shows, "ep01")
        os.makedirs(ep)
        shutil.copy(os.path.join(FIXTURE, "series.json"), shows)
        shutil.copy(os.path.join(FIXTURE, "script.md"), os.path.join(ep, "ep01.md"))
        self.assertTrue(E.build_episode(ep)["ok"])
        s = R.load_series(ep)
        self.assertEqual(os.path.normcase(s.home), os.path.normcase(os.path.abspath(shows)))
        refs = {r["id"]: r for r in R.list_refs(ep)}
        kitchen = refs["location:kitchen"]
        self.assertEqual(kitchen["path"], "../refs/_bg/kitchen.png")
        self.assertTrue(kitchen["used_by"]["final"])
        # takes, picks and the live file all live in the series folder
        t = R.import_take(s, R.find_ref(s, "location:kitchen"), None, self.png())
        self.assertTrue(t.paths.sidecar.startswith(os.path.join(shows, "refs", "_takes",
                                                                "location__kitchen")))
        R.pick_take(s, R.find_ref(s, "location:kitchen"), None, t.take)
        self.assertTrue(os.path.isfile(os.path.join(shows, "refs", "_bg", "kitchen.png")))
        self.assertTrue(os.path.isfile(os.path.join(shows, "refs", "_picks.json")))
        kitchen = {r["id"]: r for r in R.list_refs(ep)}["location:kitchen"]
        self.assertTrue(kitchen["exists"])
        self.assertEqual(kitchen["picked"], 1)
        self.assertEqual(kitchen["takes"][0]["image"],
                         "../refs/_takes/location__kitchen/location__kitchen_t01.png")

    def test_unknown_refs(self):
        with self.assertRaises(R.UnknownRef):
            R.find_ref(self.s, "subject:nobody")
        with self.assertRaises(R.UnknownRef):
            R.find_ref(self.s, "shot:sh999:first")
        with self.assertRaises(R.RefError):
            R.find_ref(self.s, "thing:x")
        with self.assertRaises(R.RefError):
            R.find_ref(self.s, "shot:sh010:middle")
        with self.assertRaises(R.RefError):
            R.check_view(self.ref("location:kitchen"), "02_side")
        with self.assertRaises(R.RefError):
            R.check_view(self.ref("subject:ada"), "05_top")


class TakesTest(RefsTest):
    def test_reservation_numbers_and_names(self):
        ada = self.ref("subject:ada")
        a = R.reserve_take(ada, "02_side", {"status": "queued"})
        b = R.reserve_take(ada, "02_side", {"status": "queued"})
        c = R.reserve_take(ada, "04_face", {"status": "queued"})
        self.assertEqual((a.take, b.take, c.take), (1, 2, 1))
        self.assertEqual(os.path.basename(b.paths.sidecar), "subject__ada_02_side_t02.json")
        self.assertEqual(os.path.basename(b.paths.image), "subject__ada_02_side_t02.png")
        self.assertEqual(b.sidecar["ref"], "subject:ada")
        self.assertEqual(b.sidecar["view"], "02_side")
        self.assertEqual([t.take for t in R.list_takes(ada, "02_side")], [1, 2])
        self.assertEqual(R.list_takes(ada, "01_threequarter"), [])
        k = R.reserve_take(self.ref("location:kitchen"), None, {"status": "queued"})
        self.assertEqual(os.path.basename(k.paths.sidecar), "location__kitchen_t01.json")

    def test_reservation_is_race_safe(self):
        kitchen = self.ref("location:kitchen")
        got, lock = [], threading.Lock()

        def worker():
            t = R.reserve_take(kitchen, None, {"status": "queued"})
            with lock:
                got.append(t.take)
        threads = [threading.Thread(target=worker) for _ in range(12)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        self.assertEqual(sorted(got), list(range(1, 13)))

    def test_sweep(self):
        kitchen = self.ref("location:kitchen")
        R.reserve_take(kitchen, None, {"status": "queued", "comfy_prompt_id": "gone",
                                       "queued": "2026-01-01T00:00:00+00:00"})
        alive = R.reserve_take(kitchen, None, {"status": "queued", "comfy_prompt_id": "alive",
                                               "queued": "2026-01-01T00:00:00+00:00"})
        c = self.fake()
        c.pending.append("alive")
        changed = R.sweep(self.s, J.Comfy(c.url))
        self.assertEqual([t.take for t in changed], [1])
        t1, t2 = R.list_takes(kitchen)
        self.assertEqual((t1.status, t2.status), ("failed", "queued"))
        self.assertEqual(alive.take, 2)


class GenerateTest(RefsTest):
    def test_character_four_views_share_a_seed(self):
        out = self.gen("subject:ada")
        self.assertEqual(out["errors"], [])
        self.assertEqual([q["view"] for q in out["queued"]], R.VIEW_TAGS)
        self.assertEqual({(q["seed"], q["seed_source"]) for q in out["queued"]},
                         {(R.seed_for("ada"), "stable")})
        ada = self.ref("subject:ada")
        for v in R.VIEW_TAGS:
            (t,) = R.list_takes(ada, v)
            self.assertTrue(t.usable, t.sidecar)
            self.assertEqual((t.sidecar["width"], t.sidecar["height"]), (1024, 1024))
            self.assertEqual(t.sidecar["source"], "generated")
            self.assertTrue(t.sidecar["prompt"].startswith("A single character reference view"))
            self.assertEqual(t.sidecar["ep"], self.ep)
        # the graph: the workflow's SaveImage became H3SaveRefTake on the take's sidecar
        g = self.comfy.graphs[0]
        self.assertFalse([v for v in g.values() if v["class_type"] == "SaveImage"])
        (saver,) = [v for v in g.values() if v["class_type"] == R.SAVER]
        self.assertEqual(os.path.normcase(saver["inputs"]["sidecar"]),
                         os.path.normcase(R.list_takes(ada, "01_threequarter")[0].paths.sidecar))
        self.assertEqual(saver["inputs"]["images"], ["22:9", 0])
        ks = next(v for v in g.values() if v["class_type"] == "KSampler")["inputs"]
        self.assertEqual((ks["seed"], ks["steps"]), (R.seed_for("ada"), R.STEPS))
        # a second round: a new seed, still shared
        out = self.gen("subject:ada")
        seeds = {q["seed"] for q in out["queued"]}
        self.assertEqual(len(seeds), 1)
        self.assertNotEqual(seeds, {R.seed_for("ada")})
        self.assertEqual({q["seed_source"] for q in out["queued"]}, {"new"})
        self.assertEqual({q["take"] for q in out["queued"]}, {2})

    def test_one_view_count_and_request_fields(self):
        out = self.gen("subject:ada", "04_face", count=2, prompt="just a face", steps=6,
                       model="other.safetensors",
                       loras=[{"name": "a.safetensors", "strength": 0.5},
                              {"name": "b.safetensors", "strength": 0.25}])
        self.assertEqual([(q["view"], q["take"]) for q in out["queued"]],
                         [("04_face", 1), ("04_face", 2)])
        self.assertEqual([q["seed_source"] for q in out["queued"]], ["stable", "new"])
        g = self.comfy.graphs[-1]
        self.assertEqual(next(v for v in g.values() if v["class_type"] == "CLIPTextEncode")
                         ["inputs"]["text"], "just a face")
        self.assertEqual(next(v for v in g.values() if v["class_type"] == "UNETLoader")
                         ["inputs"]["unet_name"], "other.safetensors")
        loras = {v["inputs"]["lora_name"]: v for v in g.values()
                 if v["class_type"] == "LoraLoader"}
        self.assertEqual(set(loras), {"a.safetensors", "b.safetensors"})
        self.assertEqual(loras["b.safetensors"]["inputs"]["strength_model"], 0.25)
        ks = next(v for v in g.values() if v["class_type"] == "KSampler")["inputs"]
        # the sampler reads the last LoRA, which reads the first
        b_id = next(k for k, v in g.items() if v["class_type"] == "LoraLoader"
                    and v["inputs"]["lora_name"] == "b.safetensors")
        self.assertEqual(ks["model"], [b_id, 0])
        self.assertEqual(ks["steps"], 6)

    def test_location_and_errors(self):
        out = self.gen("location:kitchen", seed=77)
        (q,) = out["queued"]
        self.assertEqual((q["seed"], q["seed_source"], q["view"]), (77, "typed", None))
        lat = next(v for v in self.comfy.graphs[-1].values()
                   if v["class_type"] == "EmptyLatentImage")["inputs"]
        self.assertEqual((lat["width"], lat["height"]), (1344, 768))
        for bad in (dict(ref="voice:ada"), dict(ref="shot:sh010:first"),
                    dict(ref="subject:ada", prompt="x"), dict(ref="location:kitchen", count=0),
                    dict(ref="location:kitchen", seed_mode="odd")):
            with self.assertRaises(R.RefError, msg=bad):
                R.plan_generate(self.s, R.GenRequest(**bad))

    def test_builtin_graph_and_save_image_fallback(self):
        c = self.fake()
        c.nodes.discard(R.SAVER)
        comfy = J.Comfy(c.url)
        self.assertFalse(comfy.has_node(R.SAVER))
        (job,) = R.plan_generate(self.s, R.GenRequest("subject:kettle"))
        take = R.start_gen(self.s, job)
        g = R.graph_for(None, job, take, save_node=False)
        self.assertEqual(g["10"]["class_type"], "SaveImage")
        pid = comfy.queue(g)
        R.mark_queued(take, pid)
        self.assertEqual(R.wait_take(comfy, take, pid, 30, poll=0.05), "ok")
        self.assertTrue(take.usable)
        self.assertEqual((take.sidecar["width"], take.sidecar["height"]), (1024, 1024))
        self.assertIn("fetched", take.sidecar["save_notes"])

    def test_execution_error_fails_the_take(self):
        c = self.fake()
        c.mode = "error"
        (q,) = self.gen("location:street")["queued"]
        t = R.list_takes(self.ref("location:street"))[0]
        self.assertEqual(t.status, "queued")
        R.sweep(self.s, J.Comfy(c.url))
        t = R.list_takes(self.ref("location:street"))[0]
        self.assertEqual((t.status, t.sidecar["save_notes"]), ("failed", "boom"))


class PickTest(RefsTest):
    def test_pick_copies_and_records(self):
        self.gen("location:kitchen")
        self.gen("location:kitchen")
        kitchen = self.ref("location:kitchen")
        t1, t2 = R.list_takes(kitchen)
        R.pick_take(self.s, kitchen, None, 2)
        live = os.path.join(self.ep, "refs", "_bg", "kitchen.png")
        self.assertEqual(T.file_sha1(live), T.file_sha1(t2.paths.image))
        picks = R.load_picks(self.ep)
        self.assertEqual(picks["refs"]["location:kitchen"]["take"], 2)
        self.assertEqual(picks["refs"]["location:kitchen"]["sha1"], T.file_sha1(live))
        r = self.listing()["location:kitchen"]
        self.assertEqual((r["picked"], r["exists"], r["sha1"]), (2, True, T.file_sha1(live)))
        R.pick_take(self.s, kitchen, None, 1)
        self.assertEqual(T.file_sha1(live), T.file_sha1(t1.paths.image))
        with self.assertRaises(R.UnknownRef):
            R.pick_take(self.s, kitchen, None, 9)

    def test_unusable_take_is_refused(self):
        c = self.fake()
        c.mode = "hold"
        self.gen("location:kitchen")
        with self.assertRaises(R.NotUsable):
            R.pick_take(self.s, self.ref("location:kitchen"), None, 1)
        with self.assertRaises(R.RefError):
            R.pick_take(self.s, self.ref("subject:ada"), None, 1)     # a character needs a view

    @unittest.skipUnless(HAVE_PIL, "stitching needs PIL")
    def test_four_view_picks_stitch_the_sheet(self):
        from PIL import Image
        self.gen("subject:bo")
        bo = self.ref("subject:bo")
        sheet = os.path.join(self.ep, "refs", "bo", "bo_sheet_4panel.png")
        for i, v in enumerate(R.VIEW_TAGS):
            res = R.pick_take(self.s, bo, v, 1)
            self.assertEqual(res.stitched, i == 3)
            self.assertEqual(os.path.isfile(sheet), i == 3)
        with Image.open(sheet) as im:
            self.assertEqual(im.size, (4096, 1024))
        picks = R.load_picks(self.ep)["refs"]["subject:bo"]
        self.assertEqual(sorted(picks["views"]), R.VIEW_TAGS)
        self.assertEqual(picks["sha1"], T.file_sha1(sheet))
        first = T.file_sha1(sheet)
        # re-picking one view restitches
        self.gen("subject:bo", "03_back", seed=12345)
        R.pick_take(self.s, bo, "03_back", 2)
        self.assertNotEqual(T.file_sha1(sheet), first)
        r = self.listing()["subject:bo"]
        self.assertEqual([v["picked"] for v in r["views"]], [1, 1, 2, 1])
        self.assertTrue(r["exists"])


class AutoPickTest(RefsTest):
    """A ref with no live file takes its first finished candidate; one with a
    live file is never replaced by new candidates."""

    def test_missing_plate_takes_its_first_candidate(self):
        self.gen("location:kitchen")
        self.gen("location:kitchen")
        kitchen = self.ref("location:kitchen")
        live = os.path.join(self.ep, "refs", "_bg", "kitchen.png")
        self.assertFalse(os.path.isfile(live))
        res = R.auto_pick(self.s, kitchen)
        self.assertEqual([r.take.take for r in res], [1])
        self.assertEqual(T.file_sha1(live), T.file_sha1(R.list_takes(kitchen)[0].paths.image))
        # the file exists now: later candidates wait for an explicit pick
        self.gen("location:kitchen")
        self.assertEqual(R.auto_pick(self.s, kitchen), [])
        self.assertEqual(R.load_picks(self.ep)["refs"]["location:kitchen"]["take"], 1)

    def test_queued_candidates_are_not_picked(self):
        self.fake().mode = "hold"
        self.gen("location:kitchen")
        self.assertEqual(R.auto_pick(self.s, self.ref("location:kitchen")), [])

    @unittest.skipUnless(HAVE_PIL, "stitching needs PIL")
    def test_character_views_fill_in_and_stitch(self):
        self.gen("subject:bo")                     # all four views
        bo = self.ref("subject:bo")
        R.pick_take(self.s, bo, "02_side", 1)      # one picked by hand already
        res = R.auto_pick(self.s, bo)
        self.assertEqual(sorted(r.take.view for r in res),
                         ["01_threequarter", "03_back", "04_face"])
        self.assertTrue(res[-1].stitched)
        self.assertTrue(os.path.isfile(os.path.join(self.ep, "refs", "bo", "bo_sheet_4panel.png")))

    def test_voice_and_nameless_refs_are_left_alone(self):
        self.assertEqual(R.auto_pick(self.s, self.ref("voice:ada")), [])


class ImportAndOverrideTest(RefsTest):
    def test_import(self):
        kettle = self.ref("subject:kettle")
        t = R.import_take(self.s, kettle, None, self.png(w=40, h=20), note="hand drawn")
        self.assertEqual((t.take, t.status, t.sidecar["source"]), (1, "ok", "imported"))
        self.assertEqual((t.sidecar["width"], t.sidecar["height"]), (40, 20))
        self.assertEqual(t.sidecar["note"], "hand drawn")
        j = R.take_json(self.ep, kettle, t)
        self.assertEqual(j["image"], "refs/_takes/subject__kettle/subject__kettle_t01.png")
        # a jpeg keeps its extension as a take
        jpg = os.path.join(self.tmp, "x.jpg")
        shutil.copy(self.png(), jpg)
        t2 = R.import_take(self.s, kettle, None, jpg)
        self.assertTrue(t2.paths.image.endswith("_t02.jpg"))
        # voices take audio
        wav = os.path.join(self.tmp, "v.wav")
        open(wav, "wb").write(b"RIFF")
        tv = R.import_take(self.s, self.ref("voice:ada"), None, wav)
        R.pick_take(self.s, self.ref("voice:ada"), None, tv.take)
        self.assertTrue(os.path.isfile(os.path.join(self.ep, "audio", "voices", "ada_sample.wav")))
        with self.assertRaises(R.RefError):
            R.import_take(self.s, self.ref("voice:ada"), None, self.png())
        with self.assertRaises(R.RefError):
            R.import_take(self.s, self.ref("subject:ada"), None, self.png())   # needs a view
        with self.assertRaises(FileNotFoundError):
            R.import_take(self.s, kettle, None, os.path.join(self.tmp, "nope.png"))
        # a keyframe: import and pick, then it lists
        kf = R.find_ref(self.s, "shot:sh010:first")
        tk = R.import_take(self.s, kf, None, self.png())
        R.pick_take(self.s, kf, None, tk.take)
        self.assertTrue(os.path.isfile(os.path.join(self.ep, "refs", "shots", "sh010",
                                                    "first.png")))
        r = self.listing()["shot:sh010:first"]
        self.assertEqual((r["scope"], r["kind"], r["picked"], r["exists"]),
                         ("shot", "keyframe", 1, True))
        self.assertNotIn("shot:sh010:last", self.listing())

    def test_overrides(self):
        kitchen = self.ref("location:kitchen")
        ov = R.load_overrides(self.ep)
        built = R.built_prompt(self.s, kitchen)
        R.set_ref_override(ov, kitchen, None, {"prompt": "my kitchen", "seed": 5, "steps": 3},
                           R.prompt_hash(built))
        R.save_overrides(self.ep, ov)
        r = self.listing()["location:kitchen"]
        self.assertEqual(r["override"]["fields"], ["prompt", "seed", "steps"])
        self.assertFalse(r["override"]["stale"])
        self.assertEqual((r["prompt"], r["effective"]["seed"], r["effective"]["seed_source"],
                          r["effective"]["steps"]), ("my kitchen", 5, "override", 3))
        (job,) = R.plan_generate(self.s, R.GenRequest("location:kitchen"))
        self.assertEqual(job.overridden, ["prompt", "seed", "steps"])
        (job,) = R.plan_generate(self.s, R.GenRequest("location:kitchen", seed_mode="new"))
        self.assertEqual(job.seed_source, "new")
        # the bible changes the description: the prompt override goes stale
        bible = json.load(open(os.path.join(self.ep, "series.json"), encoding="utf-8"))
        bible["locations"]["kitchen"]["description"] = "a bigger kitchen"
        with open(os.path.join(self.ep, "series.json"), "w", encoding="utf-8") as fh:
            json.dump(bible, fh)
        self.s = R.load_series(self.ep)
        self.assertTrue(self.listing()["location:kitchen"]["override"]["stale"])
        # a character's prompt is per view
        ada = self.ref("subject:ada")
        with self.assertRaises(R.RefError):
            R.set_ref_override(ov, ada, None, {"prompt": "x"}, None)
        R.set_ref_override(ov, ada, None, {"seed": 9}, None)
        R.set_ref_override(ov, ada, "04_face", {"prompt": "face only"},
                           R.prompt_hash(R.built_prompt(self.s, ada, "04_face")))
        R.save_overrides(self.ep, ov)
        jobs = R.plan_generate(self.s, R.GenRequest("subject:ada"))
        self.assertEqual({j.seed for j in jobs}, {9})
        self.assertEqual([j.prompt == "face only" for j in jobs], [False, False, False, True])
        R.clear_ref_override(ov, "subject:ada", "04_face")
        self.assertEqual(ov["refs"]["subject:ada"], {"seed": 9})
        R.clear_ref_override(ov, "subject:ada")
        self.assertNotIn("subject:ada", ov["refs"])
        with self.assertRaises(R.RefError):
            R.set_ref_override(ov, kitchen, None, {"colour": "red"}, None)


class StaleTest(RefsTest):
    def test_repick_makes_video_takes_ref_stale(self):
        stub_refs(self.ep)
        c = self.fake()
        comfy = J.Comfy(c.url)
        res = E.queue_shots(self.ep, "proxy", ["sh010"], J.RenderRequest(""), comfy,
                            J.load_graph(H3_WF))
        self.assertEqual(len(res["queued"]), 1, res)
        doc = J.load_shotlist(self.ep, "proxy")
        shot = doc["shots"][0]
        (t,) = T.list_takes(self.ep, "proxy", "sh010")
        self.assertEqual(J.stale_reasons(self.ep, doc, shot, t.sidecar), [])
        # sh010 is in the kitchen: a new plate picked makes the take ref-stale
        self.assertEqual(shot["background"], "refs/_bg/kitchen.png")
        kitchen = self.ref("location:kitchen")
        tk = R.import_take(self.s, kitchen, None, self.png(rgb=(200, 10, 10)))
        R.pick_take(self.s, kitchen, None, tk.take)
        self.assertEqual(J.stale_reasons(self.ep, doc, shot, t.sidecar), ["ref"])


class KreagenTest(RefsTest):
    def run_kreagen(self, *args, root=None):
        return subprocess.run([sys.executable, os.path.join(ROOT, "kreagen.py"),
                               "--project-root", root or self.ep, *args],
                              capture_output=True, env=ENV, cwd=root or self.ep)

    def test_list_and_dry_run(self):
        r = self.run_kreagen("--list", "--comfy", "http://127.0.0.1:9", "--no-workflow")
        out = r.stdout.decode("utf-8").replace("\r\n", "\n")
        self.assertEqual(r.returncode, 0, out + r.stderr.decode())
        self.assertIn("9 asset(s)", out)
        self.assertIn("built-in", out)
        self.assertIn("ada_sheet_4panel.png", out)
        self.assertIn("1024x1024 x4  blocks 7", out)
        self.assertNotIn("sample.wav", out)
        r = self.run_kreagen("--dry-run", "--only", "ada,kitchen", "--comfy",
                             "http://127.0.0.1:9")
        out = r.stdout.decode("utf-8").replace("\r\n", "\n")
        self.assertEqual(r.returncode, 0, out + r.stderr.decode())
        self.assertIn(f"--- ada:02_side  seed {R.seed_for('ada')}\nA single character", out)
        self.assertIn(f"--- kitchen  seed {R.seed_for('refs/_bg/kitchen.png')}\n"
                      f"A background plate", out)
        self.assertFalse(os.path.isdir(os.path.join(self.ep, "refs", "_takes")))
        # --all works from the bible alone, no refs_todo needed
        os.remove(os.path.join(self.ep, "refs_todo.json"))
        self.assertEqual(self.run_kreagen("--list").returncode, 1)
        r = self.run_kreagen("--list", "--all", "--comfy", "http://127.0.0.1:9")
        self.assertIn("10 asset(s)", r.stdout.decode("utf-8"))       # + the narrator

    @unittest.skipUnless(HAVE_PIL, "stitching needs PIL")
    def test_generate_picks_then_redo_keeps_live_files(self):
        c = self.fake()
        base = ["--comfy", c.url, "--workflow", REFS_WF, "--timeout", "60"]
        r = self.run_kreagen(*base, "--only", "ada,kitchen,kettle")
        out = r.stdout.decode("utf-8").replace("\r\n", "\n")
        self.assertEqual(r.returncode, 0, out + r.stderr.decode())
        sheet = os.path.join(self.ep, "refs", "ada", "ada_sheet_4panel.png")
        plate = os.path.join(self.ep, "refs", "_bg", "kitchen.png")
        for p in (sheet, plate, os.path.join(self.ep, "refs", "props", "kettle.png")):
            self.assertTrue(os.path.isfile(p), p)
        self.assertEqual(R.load_picks(self.ep)["refs"]["location:kitchen"]["take"], 1)
        # 4 views + kettle + kitchen + kitchen_window ("kitchen" is also a path
        # fragment of it, as --only always matched)
        self.assertEqual(len(c.graphs), 7)
        before = T.file_sha1(plate)
        # --redo: a new take, the live file untouched
        r = self.run_kreagen(*base, "--only", "kitchen", "--redo")
        self.assertEqual(r.returncode, 0, r.stdout.decode())
        kitchen = self.ref("location:kitchen")
        self.assertEqual([t.take for t in R.list_takes(kitchen)], [1, 2])
        self.assertEqual(R.list_takes(kitchen)[1].sidecar["seed_source"], "new")
        self.assertEqual(T.file_sha1(plate), before)
        # --redo --pick: the new take goes live
        r = self.run_kreagen(*base, "--only", "kitchen", "--redo", "--pick")
        self.assertEqual(r.returncode, 0, r.stdout.decode())
        self.assertEqual(T.file_sha1(plate), T.file_sha1(R.list_takes(kitchen)[2].paths.image))

    def test_old_node_pack_falls_back_to_save_image(self):
        c = self.fake()
        c.nodes.discard(R.SAVER)
        r = self.run_kreagen("--comfy", c.url, "--workflow", REFS_WF, "--only", "street")
        out = r.stdout.decode("utf-8").replace("\r\n", "\n")
        self.assertEqual(r.returncode, 0, out + r.stderr.decode())
        self.assertIn("no H3SaveRefTake node", out)
        self.assertTrue(os.path.isfile(os.path.join(self.ep, "refs", "_bg", "street.png")))
        g = c.graphs[-1]
        self.assertTrue([v for v in g.values() if v["class_type"] == "SaveImage"])
        (t,) = R.list_takes(self.ref("location:street"))
        self.assertEqual(t.status, "ok")


class SaveNodeTest(unittest.TestCase):
    def test_h3saverettake_writes_and_closes(self):
        try:
            import torch
            from PIL import Image
        except ImportError as exc:                       # pragma: no cover
            self.skipTest(f"needs torch and PIL: {exc}")
        sys.path.insert(0, os.path.join(ROOT, "comfy_nodes"))
        import h3_shotlist as N
        with tempfile.TemporaryDirectory() as tmp:
            ref = R.Ref("location:x", "series", "location", "x", "refs/x.png", tmp)
            t = R.reserve_take(ref, None, {"status": "queued", "ep": tmp, "seed": 5})
            img = torch.zeros((1, 24, 40, 3))
            img[..., 0] = 1.0
            (status,) = N.H3SaveRefTake().save(img, t.paths.sidecar)["result"]
            self.assertIn("ok", status)
            sc = T.read_json(t.paths.sidecar)
            self.assertEqual((sc["status"], sc["width"], sc["height"], sc["seed"]),
                             ("ok", 40, 24, 5))
            self.assertEqual(sc["image"], "location__x_t01.png")
            with Image.open(t.paths.image) as im:
                self.assertEqual(im.size, (40, 24))
                self.assertEqual(im.getpixel((3, 3))[:3], (255, 0, 0))
            self.assertTrue(R.list_takes(ref)[0].usable)


if __name__ == "__main__":
    unittest.main()
