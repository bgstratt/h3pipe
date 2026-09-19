"""
Phase 8.6 (docs/API.md "Phase 8.6: look-back"): discarding takes and ref
candidates to _trash/ (and that nothing lists or numbers from the trash),
POST /h3pipe/refs/generate-missing, uploads on POST /h3pipe/refs/import, and
the keyframe polish (the framing wording, the composed reference for a
single-reference edit target). The routes run through h3pipe_api with
test_render's FakeComfy, as test_api does; the multipart adapter itself is in
test_routes (it needs aiohttp).
"""
from __future__ import annotations

import io
import os
import random
import sys
import unittest
from contextlib import redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "comfy_nodes"))
import h3_refsheet as RS  # noqa: E402
import h3edit as E  # noqa: E402
import h3jobs as J  # noqa: E402
import h3pipe_api as A  # noqa: E402
import h3refs as R  # noqa: E402
import h3takes as T  # noqa: E402
import kreagen  # noqa: E402
import targets as TG  # noqa: E402
import test_api  # noqa: E402
import test_phase85  # noqa: E402
from targets.image import common as IC  # noqa: E402
from test_api import ApiTest  # noqa: E402
from test_phase85 import OBJECT_INFO, Episode, write_png  # noqa: E402
from test_render import png_bytes  # noqa: E402

try:
    from PIL import Image
    HAVE_PIL = True
except ImportError:                                      # pragma: no cover
    HAVE_PIL = False


def tearDownModule():
    test_api.tearDownModule()
    test_phase85.tearDownModule()


class RefsHelpers(ApiTest):
    def refs(self):
        data = self.ok(A.get_refs(self.ctx, {"ep": self.ep}))
        return {r["id"]: r for r in data["refs"]}

    def png(self, name="in.png", rgb=(9, 9, 9)) -> str:
        p = os.path.join(self.tmp, name)
        with open(p, "wb") as fh:
            fh.write(png_bytes(32, 16, rgb))
        return p

    def imp(self, ref, view=None, **kw):
        body = dict({"ep": self.ep, "ref": ref, "source_path": self.png()}, **kw)
        if view:
            body["view"] = view
        return self.ok(A.post_refs_import(self.ctx, body))


# ---------------------------------------------------------------------------
# discarding video takes
# ---------------------------------------------------------------------------

class DiscardTakeTest(ApiTest):
    def discard(self, shot, take, status=200, pass_="proxy"):
        res = A.post_discard(self.ctx, {"ep": self.ep, "shot": shot, "take": take,
                                        "pass": pass_})
        return self.ok(res) if status == 200 else self.err(res, status)

    def test_discard_moves_files_and_unpicks(self):
        self.render("sh010")
        self.render("sh010", redo=True)
        self.ok(A.put_pick(self.ctx, {"ep": self.ep, "pass": "proxy", "shot": "sh010",
                                      "take": 1}))
        d = os.path.join(self.ep, "renders_proxy", "sh010")
        before = sorted(n for n in os.listdir(d) if n.startswith("sh010_t01"))
        self.assertIn("sh010_t01.json", before)
        self.assertIn("sh010_t01.mp4", before)
        self.events.clear()
        res = self.discard("sh010", 1)
        self.assertEqual((res["shot"], res["take"], res["cut_changed"]), ("sh010", 1, True))
        self.assertEqual(sorted(os.path.basename(p) for p in res["moved"]), before)
        self.assertTrue(all(p.startswith("renders_proxy/_trash/sh010/") for p in res["moved"]))
        for p in res["moved"]:
            self.assertTrue(os.path.isfile(os.path.join(self.ep, p)), p)
        self.assertFalse([n for n in os.listdir(d) if n.startswith("sh010_t01")])
        self.assertEqual(self.events_of("h3pipe.episode"), [{"ep": self.ep}])
        # the listing: take 2 only, the cut back on the latest usable take
        _, shots = self.status()
        self.assertEqual([t["take"] for t in shots["sh010"]["takes"]], [2])
        cut = shots["sh010"]["cut"]
        self.assertEqual((cut["take"], cut["picked"]), (2, False))
        self.assertNotIn("_trash", {s["shot"] for s in self.status()[0]["shots"]})
        # the number isn't given out again, and a second discard of it is 404
        (q,) = self.render("sh010", redo=True)["queued"]
        self.assertEqual(q["take"], 3)
        self.discard("sh010", 1, 404)
        # a take number used again (an explicit --take) goes to a dated folder
        self.assertEqual(self.discard("sh010", 3)["cut_changed"], False)
        self.assertEqual(T.take_numbers(self.ep, "proxy", "sh010"), [2])

    def test_placeholder_pick_in_the_other_pass(self):
        self.render("sh010")
        self.ok(A.put_pick(self.ctx, {"ep": self.ep, "pass": "final", "shot": "sh010",
                                      "take": 1, "from_pass": "proxy"}))
        self.assertTrue(self.discard("sh010", 1)["cut_changed"])
        e = next(x for x in T.load_cut(self.ep)["final"] if x["shot"] == "sh010")
        self.assertNotIn("take", e)

    def test_queued_and_bad_input(self):
        self.comfy.mode = "hold"
        self.render("sh010")
        self.assertIn("cancel", self.discard("sh010", 1, 409))
        self.discard("sh999", 1, 404)
        self.discard("sh010", "x", 400)
        self.err(A.post_discard(self.ctx, {"ep": self.ep, "take": 1}), 400)

    def test_dated_folder_on_a_name_clash(self):
        self.render("sh010")
        trash = T.trash_dir(self.ep, "proxy", "sh010")
        os.makedirs(trash)
        open(os.path.join(trash, "sh010_t01.json"), "w").close()
        res = self.discard("sh010", 1)
        self.assertTrue(all(p.count("/") == 4 for p in res["moved"]), res["moved"])

    def test_stem_files_is_exact(self):
        d = os.path.join(self.tmp, "x")
        os.makedirs(d)
        for n in ("sh010_t01.json", "sh010_t01_strip.jpg", "sh010_t010.json", "sh010_t011.mp4"):
            open(os.path.join(d, n), "w").close()
        self.assertEqual([os.path.basename(p) for p in T.stem_files(d, "sh010_t01")],
                         ["sh010_t01.json", "sh010_t01_strip.jpg"])

    def test_cli(self):
        self.render("sh010")
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(E.COMMANDS["discard"](self.ep, ["sh010", "t01", "--proxy"]), 0)
            self.assertEqual(E.COMMANDS["discard"](self.ep, ["sh010", "1", "--proxy"]), 1)
        self.assertIn("moved", out.getvalue())
        self.assertIn("discard", __import__("h3").EDIT)


# ---------------------------------------------------------------------------
# discarding ref candidates
# ---------------------------------------------------------------------------

class DiscardRefTest(RefsHelpers):
    def test_discard_the_pick_clears(self):
        for _ in range(2):
            self.imp("location:kitchen")
        self.ok(A.put_refs_pick(self.ctx, {"ep": self.ep, "ref": "location:kitchen",
                                           "take": 1}))
        self.events.clear()
        r = self.ok(A.post_refs_discard(self.ctx, {"ep": self.ep, "ref": "location:kitchen",
                                                   "take": 1}))
        self.assertEqual((r["picked"], r["cleared"], r["exists"]), (None, True, False))
        self.assertEqual([t["take"] for t in r["takes"]], [2])
        self.assertEqual([e["status"] for e in self.events_of("h3pipe.ref")],
                         ["discarded", "cleared"])
        trash = os.path.join(self.ep, "refs", "_takes", "_trash", "location__kitchen")
        self.assertEqual(sorted(os.listdir(trash)),
                         ["location__kitchen_t01.json", "location__kitchen_t01.png"])
        # nothing lists or sweeps it, and its number isn't reused
        self.assertEqual([t["take"] for t in self.refs()["location:kitchen"]["takes"]], [2])
        self.assertEqual({t.take for t in R.all_takes(R.load_series(self.ep))
                          if t.ref == "location:kitchen"}, {2})
        self.assertEqual(self.imp("location:kitchen")["take"], 3)
        # an unpicked one: nothing else changes
        r = self.ok(A.post_refs_discard(self.ctx, {"ep": self.ep, "ref": "location:kitchen",
                                                   "take": 2}))
        self.assertEqual([t["take"] for t in r["takes"]], [3])
        self.err(A.post_refs_discard(self.ctx, {"ep": self.ep, "ref": "location:kitchen",
                                                "take": 2}), 404)

    def test_view_and_errors(self):
        self.imp("subject:ada", "02_side")
        self.ok(A.put_refs_pick(self.ctx, {"ep": self.ep, "ref": "subject:ada",
                                           "view": "02_side", "take": 1}))
        r = self.ok(A.post_refs_discard(self.ctx, {"ep": self.ep, "ref": "subject:ada",
                                                   "view": "02_side", "take": 1}))
        side = next(v for v in r["views"] if v["view"] == "02_side")
        self.assertEqual((side["picked"], side["cleared"], side["takes"]), (None, True, []))
        self.err(A.post_refs_discard(self.ctx, {"ep": self.ep, "ref": "subject:ada",
                                                "take": 1}), 400)          # needs a view
        self.comfy.mode = "hold"
        self.ok(A.post_refs_generate(self.ctx, {"ep": self.ep, "ref": "location:street"}))
        self.err(A.post_refs_discard(self.ctx, {"ep": self.ep, "ref": "location:street",
                                                "take": 1}), 409)

    def test_kreagen(self):
        self.assertEqual(kreagen.parse_discard("subject:ada:02_side:t3"),
                         ("subject:ada", "02_side", 3))
        self.assertEqual(kreagen.parse_discard("shot:sh020:first:1"), ("shot:sh020:first", None, 1))
        with self.assertRaises(ValueError):
            kreagen.parse_discard("location:kitchen")
        self.imp("location:kitchen")
        out = io.StringIO()
        with redirect_stdout(out):
            s = R.load_series(self.ep)
            self.assertEqual(kreagen.discard(s, self.ep, ["location:kitchen:1"]), 0)
            self.assertEqual(kreagen.discard(s, self.ep, ["location:kitchen:1"]), 1)
        self.assertIn("moved 2 file(s)", out.getvalue())

    def test_routes_listed(self):
        got = {(m, p): takes for m, p, _, takes in A.ROUTES}
        self.assertEqual(got[("POST", "/h3pipe/discard")], "body")
        self.assertEqual(got[("POST", "/h3pipe/refs/discard")], "body")
        self.assertEqual(got[("POST", "/h3pipe/refs/generate-missing")], "body")
        self.assertEqual(got[("POST", "/h3pipe/refs/import")], "form")


# ---------------------------------------------------------------------------
# uploads
# ---------------------------------------------------------------------------

class UploadTest(RefsHelpers):
    def upload(self, name, data=None, **kw):
        p = os.path.join(self.tmp, "h3pipe_upload_x" + os.path.splitext(name)[1])
        with open(p, "wb") as fh:
            fh.write(data if data is not None else png_bytes(16, 16))
        return dict({"ep": self.ep, "file": A.Upload(p, name, os.path.getsize(p))}, **kw)

    def test_upload_and_pick(self):
        t = self.ok(A.post_refs_import(self.ctx, self.upload("My Kitchen.PNG",
                                                             ref="location:kitchen", pick="1")))
        self.assertEqual((t["take"], t["source"], t["original_name"]),
                         (1, "imported", "My Kitchen.PNG"))
        self.assertTrue(t["image"].endswith("location__kitchen_t01.png"))
        r = self.refs()["location:kitchen"]
        self.assertEqual(r["picked"], 1)
        sc = R.get_take(R.find_ref(R.load_series(self.ep), "location:kitchen"), None, 1).sidecar
        self.assertIsNone(sc["source_path"])                   # the temp file isn't recorded
        self.assertEqual([e["status"] for e in self.events_of("h3pipe.ref")], ["ok", "picked"])
        # a view, not picked ("0", or no field)
        t = self.ok(A.post_refs_import(self.ctx, self.upload("f.webp", ref="subject:ada",
                                                             view="04_face", pick="0")))
        self.assertEqual((t["view"], t["image"][-4:]), ("04_face", "webp"))
        self.assertIsNone(next(v for v in self.refs()["subject:ada"]["views"]
                               if v["view"] == "04_face")["picked"])

    def test_json_pick(self):
        t = self.imp("subject:kettle", pick=True)
        self.assertNotIn("original_name", t)
        self.assertEqual(self.refs()["subject:kettle"]["picked"], t["take"])

    def test_errors(self):
        self.err(A.post_refs_import(self.ctx, self.upload("notes.txt", b"hi",
                                                          ref="location:kitchen")), 400)
        self.err(A.post_refs_import(self.ctx, self.upload("a.png", ref="voice:ada")), 400)
        big = self.upload("a.png", ref="location:kitchen")
        big["file"].size = A.MAX_UPLOAD + 1
        self.err(A.post_refs_import(self.ctx, big), 413)
        self.err(A.post_refs_import(self.ctx, {"ep": self.ep, "ref": "location:kitchen",
                                               "file": "x.png"}), 400)
        self.err(A.post_refs_import(self.ctx, self.upload("a.png", ref="location:kitchen",
                                                          pick="maybe")), 400)
        ok = self.ok(A.post_refs_import(self.ctx, self.upload("take.wav", b"RIFF",
                                                              ref="voice:ada")))
        self.assertEqual((ok["audio"][-4:], ok["original_name"]), (".wav", "take.wav"))


# ---------------------------------------------------------------------------
# generate missing
# ---------------------------------------------------------------------------

class GenerateMissingTest(RefsHelpers):
    def setUp(self):
        super().setUp()
        s = R.load_series(self.ep)
        for rid in ("location:kitchen", "subject:ada", "subject:kettle"):
            os.remove(R.find_ref(s, rid).file)

    def gm(self, status=200, **kw):
        res = A.post_refs_generate_missing(self.ctx, dict({"ep": self.ep}, **kw))
        return self.ok(res) if status == 200 else self.err(res, status)

    def test_dry_run_then_queue(self):
        dry = self.gm(dry_run=True, kinds=["series"])
        got = sorted((q["ref"], q["view"]) for q in dry["queued"])
        self.assertEqual(got, sorted([("location:kitchen", None), ("subject:kettle", None)]
                                     + [("subject:ada", v) for v in R.VIEW_TAGS]))
        self.assertTrue(all(q["take"] is None and q["prompt_id"] is None
                            and q["method"] == "generate" for q in dry["queued"]))
        self.assertEqual(self.comfy.graphs, [])
        self.assertEqual(self.events, [])
        self.assertFalse(os.path.isdir(os.path.join(self.ep, "refs", "_takes", "subject__ada")))
        # ada's four views share one seed, as a character generate does
        self.assertEqual(len({q["seed"] for q in dry["queued"] if q["ref"] == "subject:ada"}), 1)

        out = self.gm(kinds=["series"])
        self.assertEqual(out["errors"], [])
        self.assertEqual(sorted((q["ref"], q["view"]) for q in out["queued"]), got)
        self.assertTrue(all(q["take"] == 1 and q["prompt_id"] for q in out["queued"]))
        self.assertEqual(len(self.events_of("h3pipe.ref")), 6)
        # the candidates finished (FakeComfy) and wait to be picked: skipped now
        again = self.gm(kinds=["series"], dry_run=True)
        self.assertEqual(again["queued"], [])
        self.assertIn("location:kitchen", {x["ref"] for x in again["skipped"]})
        # the listing auto-picks them: nothing is missing any more
        self.refs()
        self.assertEqual(self.gm(kinds=["series"], dry_run=True)["skipped"],
                         [x for x in again["skipped"] if x["ref"] not in
                          ("location:kitchen", "subject:ada", "subject:kettle")])

    def test_cleared_and_one_view(self):
        s = R.load_series(self.ep)
        R.clear_pick(s, R.find_ref(s, "location:kitchen"))
        self.imp("subject:ada", "01_threequarter")          # waits to be picked
        dry = self.gm(dry_run=True, kinds=["series"])
        self.assertEqual(sorted((q["ref"], q["view"]) for q in dry["queued"]),
                         sorted([("subject:kettle", None)]
                                + [("subject:ada", v) for v in R.VIEW_TAGS[1:]]))
        why = {(x["ref"], x.get("view")): x["reason"] for x in dry["skipped"]}
        self.assertIn("cleared", why[("location:kitchen", None)])
        self.assertIn("waiting", why[("subject:ada", "01_threequarter")])

    def test_keyframes(self):
        E.set_episode_target(self.ep, "wan22_i2v")         # every first frame is required
        dry = self.gm(dry_run=True, kinds=["keyframe"], keyframe_target="z_image_turbo")
        kf = [q for q in dry["queued"] if q["ref"].startswith("shot:")]
        self.assertIn("shot:sh010:first", {q["ref"] for q in kf})
        self.assertTrue(all(q["target"] == "z_image_turbo" for q in kf))
        # sh020 has a previous shot but no take to cut a frame from: a still
        self.assertIn("shot:sh020:first", {q["ref"] for q in kf})
        self.assertEqual([q for q in dry["queued"] if not q["ref"].startswith("shot:")], [])
        out = self.gm(kinds=["keyframe"], keyframe_target="z_image_turbo")
        self.assertEqual({q["ref"] for q in out["queued"]}, {q["ref"] for q in kf})
        self.assertEqual(out["errors"], [])
        again = self.gm(dry_run=True, kinds=["keyframe"])
        self.assertEqual(again["queued"], [])                # all waiting to be picked

    def test_script_path_is_imported(self):
        E.set_episode_target(self.ep, "wan22_i2v")
        s = R.load_series(self.ep)
        need = R.keyframe_needs(self.ep)[("sh010", "first")]
        write_png(os.path.join(self.ep, "stills", "open.png"))
        need = dict(need, method="import", import_path="stills/open.png")
        with mock.patch.object(R, "keyframe_needs", return_value={("sh010", "first"): need}):
            out = R.generate_missing(s, None, "proxy", ["keyframe"])
        self.assertEqual(out["picked"], [{"ref": "shot:sh010:first", "take": 1,
                                          "method": "import"}])
        self.assertTrue(os.path.isfile(R.find_ref(s, "shot:sh010:first").file))

    def test_errors(self):
        self.gm(400, kinds=["voices"])
        self.gm(400, kinds=[])
        self.gm(400, target="wan22_i2v")                    # a video target
        self.gm(400, dry_run="yes")
        self.gm(400, **{"pass": "draft"})


# ---------------------------------------------------------------------------
# keyframe polish: the wording, the composed reference
# ---------------------------------------------------------------------------

class FramingTest(Episode):
    def test_close_up_fills_the_frame(self):
        sh, sq = R.shot_ir(self.ep, "sh030")                # cu, Ada
        p = IC.keyframe_prompt(sh, sq, self.s.series_cfg, "first")
        self.assertIn("Framing: a close-up. Ada's face fills the frame, large", p)
        self.assertIn(", framed as a close-up.", p)          # the Drawn as sentence
        self.assertTrue(p.endswith("Framing: a close-up."), p)
        sh, sq = R.shot_ir(self.ep, "sh010")                # ws
        p = IC.keyframe_prompt(sh, sq, self.s.series_cfg, "last")
        self.assertIn("Framing: a wide shot. The whole place in view, Ada small", p)
        self.assertTrue(p.endswith("Framing: a wide shot."))
        sh, sq = R.shot_ir(self.ep, "sh020")                # ms, Ada and Bo
        p = IC.keyframe_prompt(sh, sq, self.s.series_cfg, "first")
        self.assertIn("Ada and Bo seen from the waist up", p)
        self.assertEqual(IC.framing("close-up", ["Ada", "Bo"], []).split(". ")[1],
                         "Ada and Bo's faces fill the frame, large: from the chin to the top "
                         "of the head, cut off at the shoulders")


class CompositeTest(Episode):
    def setUp(self):
        super().setUp()
        self.episode_target("ltx2")
        for sid in ("ada", "bo"):
            self.pick_views(sid)
        self.live("refs/props/kettle.png")
        self.live("refs/_bg/kitchen.png")
        self.kontext = TG.load_target("flux_kontext", "image")

    def test_reference_images(self):
        sh, sq = R.shot_ir(self.ep, "sh020")
        (c,) = R.reference_images(self.s, sh, sq, self.kontext)
        self.assertEqual(c["role"], "composite")
        self.assertEqual([p.get("subject") or p.get("location") for p in c["parts"]],
                         ["ada", "bo", "kettle", "kitchen"])
        self.assertEqual(c["name"], "Ada, Bo and the diner kettle over kitchen")
        # a multi-reference target still gets them one by one; limit 1 without
        # compose is the first alone
        self.assertEqual(len(R.reference_images(
            self.s, sh, sq, TG.load_target("flux2_klein_edit", "image"))), 4)
        self.assertEqual([r["role"] for r in R.reference_images(self.s, sh, sq, None, 1)],
                         ["subject"])
        p = IC.keyframe_prompt(sh, sq, self.s.series_cfg, "first", [c])
        self.assertTrue(p.startswith("The image shows Ada, Bo, and the diner kettle in front of "
                                     "the background (kitchen): draw each of them exactly"), p)
        self.assertIn("reference collage", p)

    def test_layout(self):
        boxes = RS.composite_layout([0.5, 0.5], 1200, 600)
        self.assertEqual([b[3] for b in boxes], [400, 400])          # two thirds tall
        self.assertEqual({b[1] + b[3] for b in boxes}, {600})         # on the bottom edge
        left, right = boxes
        self.assertAlmostEqual((left[0] + right[0] + right[2]) / 2, 600, delta=2)  # centred
        wide = RS.composite_layout([3.0, 3.0], 1000, 600)            # too wide: scaled down
        self.assertLessEqual(wide[-1][0] + wide[-1][2], 1000)
        self.assertLess(wide[0][3], 400)

    @unittest.skipUnless(HAVE_PIL, "composing needs PIL")
    def test_generate_uploads_one_composed_reference(self):
        write_png(os.path.join(self.s.home, "refs", "_bg", "kitchen.png"), 64, 36, (0, 0, 255))
        c = self.fake()
        out = R.queue_generate(self.s, R.GenRequest("shot:sh020:first", target="flux_kontext"),
                               J.Comfy(c.url), None, rng=random.Random(3))
        self.assertEqual(out["errors"], [])
        g = c.graphs[-1]
        self.assertEqual(J.check_graph(g, OBJECT_INFO), [])
        (name,) = [v["inputs"]["image"] for v in g.values() if v["class_type"] == "LoadImage"]
        im = Image.open(io.BytesIO(c.uploads[name])).convert("RGB")
        ref = R.find_ref(self.s, "shot:sh020:first")
        sc = R.get_take(ref, None, out["queued"][0]["take"]).sidecar
        self.assertEqual(im.size, (sc["width"], sc["height"]))
        self.assertEqual(im.getpixel((2, 2)), (0, 0, 255))           # the plate, top left
        (rec,) = sc["references"]
        self.assertEqual(rec["role"], "composite")
        self.assertEqual([p["role"] for p in rec["parts"]],
                         ["subject", "subject", "subject", "plate"])
        self.assertTrue(rec["path"].startswith("refs/_takes/shot__sh020__first/_composites/"))
        self.assertTrue(os.path.isfile(os.path.join(self.ep, rec["path"])))
        self.assertEqual(rec["sha1"], T.file_sha1(os.path.join(self.ep, rec["path"])))
        self.assertTrue(sc["prompt"].startswith("The image shows Ada, Bo"))
        # the composites folder isn't a take
        self.assertEqual([t.take for t in R.list_takes(ref)], [1])

    def test_without_pil_falls_back_to_one_part(self):
        from targets.video.ltx2_ingredients import sheet as SH
        c = self.fake()
        with mock.patch.object(SH, "compose", side_effect=SH.SheetError("needs PIL")):
            out = R.queue_generate(self.s, R.GenRequest("shot:sh020:first",
                                                        target="flux_kontext"),
                                   J.Comfy(c.url), None, rng=random.Random(3))
        self.assertEqual(out["errors"], [])
        ref = R.find_ref(self.s, "shot:sh020:first")
        sc = R.get_take(ref, None, out["queued"][0]["take"]).sidecar
        self.assertEqual([r.get("subject") for r in sc["references"]], ["ada"])
        self.assertTrue(any("sent Ada alone" in n for n in sc["notes"]), sc["notes"])
        self.assertTrue(sc["prompt"].startswith("The image is Ada: draw this character"))

    def test_dry_run_text(self):
        (job,) = R.plan_generate(self.s, R.GenRequest("shot:sh020:first", target="flux_kontext"),
                                 rng=random.Random(0))
        self.assertEqual(R.references_text(job.references),
                         "one image composed of ada 01_threequarter, bo 01_threequarter, "
                         "kettle, kitchen")
        self.assertEqual(R.stage_references(self.s, job, None),
                         {"references": ["h3pipe/dry_run_composite.png"]})


if __name__ == "__main__":
    unittest.main()
