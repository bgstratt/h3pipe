"""
The refs routes (docs/API.md, "References (Phase 5)") through
comfy_nodes/h3pipe_api.py, in test_api.py's style: the kitchen_sink episode
built in a temp dir, ComfyUI played by test_render's FakeComfy.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "comfy_nodes"))
import h3pipe_api as A  # noqa: E402
import h3edit as E  # noqa: E402
import h3refs as R  # noqa: E402
import h3takes as T  # noqa: E402
import test_api  # noqa: E402
from test_api import ApiTest  # noqa: E402
from test_render import FIXTURE, png_bytes  # noqa: E402

try:
    import PIL  # noqa: F401
    HAVE_PIL = True
except ImportError:                                      # pragma: no cover
    HAVE_PIL = False


def tearDownModule():
    test_api.tearDownModule()                            # the shared build


class RefsApiTest(ApiTest):
    def refs(self):
        data = self.ok(A.get_refs(self.ctx, {"ep": self.ep}))
        return {r["id"]: r for r in data["refs"]}

    def gen(self, ref, **kw):
        return self.ok(A.post_refs_generate(self.ctx, dict({"ep": self.ep, "ref": ref}, **kw)))

    def png(self, name="in.png", rgb=(9, 9, 9)) -> str:
        p = os.path.join(self.tmp, name)
        with open(p, "wb") as fh:
            fh.write(png_bytes(32, 16, rgb))
        return p

    def test_list(self):
        refs = self.refs()
        self.assertIn("subject:ada", refs)
        ada = refs["subject:ada"]
        self.assertEqual(ada["path"], "refs/ada/ada_sheet_4panel.png")
        eff = ada["views"][0]["effective"]
        self.assertEqual(eff["seed"], str(R.seed_for("ada")))   # seeds are strings
        self.assertEqual(ada["used_by"]["proxy"][:1], ["sh010"])
        self.err(A.get_refs(self.ctx, {}), 400)
        self.err(A.get_refs(self.ctx, {"ep": os.path.join(self.tmp, "x")}), 403)
        os.remove(os.path.join(self.ep, "series.json"))
        self.err(A.get_refs(self.ctx, {"ep": self.ep}), 404)

    def test_generate_character(self):
        data = self.gen("subject:ada", view=None)
        self.assertEqual(data["errors"], [])
        self.assertEqual([q["view"] for q in data["queued"]], R.VIEW_TAGS)
        self.assertEqual({q["seed"] for q in data["queued"]}, {str(R.seed_for("ada"))})
        self.assertEqual([e for e in self.events_of("h3pipe.ref")],
                         [{"ep": self.ep, "ref": "subject:ada", "view": v, "take": 1,
                           "status": "queued"} for v in R.VIEW_TAGS])
        self.assertEqual(self.events_of("h3pipe.episode"), [{"ep": self.ep}])
        views = self.refs()["subject:ada"]["views"]
        t = views[0]["takes"][0]
        self.assertEqual((t["status"], t["source"], t["seed"]),
                         ("ok", "generated", str(R.seed_for("ada"))))
        self.assertEqual(t["image"], "refs/_takes/subject__ada/subject__ada_01_threequarter_t01.png")
        f = self.ok(A.get_file(self.ctx, {"ep": self.ep, "path": t["image"]}))
        self.assertEqual(f["content_type"], "image/png")
        # typed seed as a string; count
        data = self.gen("location:kitchen", seed="123", count=2, note="try")
        self.assertEqual([(q["seed"], q["take"]) for q in data["queued"]][0], ("123", 1))
        self.assertEqual(len(data["queued"]), 2)

    def test_generate_errors(self):
        self.err(A.post_refs_generate(self.ctx, {"ep": self.ep, "ref": "voice:ada"}), 400)
        self.err(A.post_refs_generate(self.ctx, {"ep": self.ep, "ref": "subject:nobody"}), 404)
        self.err(A.post_refs_generate(self.ctx, {"ep": self.ep}), 400)
        self.err(A.post_refs_generate(self.ctx, {"ep": self.ep, "ref": "subject:ada",
                                                 "view": "05_top"}), 400)
        self.err(A.post_refs_generate(self.ctx, {"ep": self.ep, "ref": "location:kitchen",
                                                 "view": "02_side"}), 400)
        self.err(A.post_refs_generate(self.ctx, {"ep": self.ep, "ref": "location:kitchen",
                                                 "count": 0}), 400)
        self.err(A.post_refs_generate(self.ctx, {"ep": self.ep, "ref": "location:kitchen",
                                                 "seed": 1.5}), 400)
        self.err(A.post_refs_generate(self.ctx, {"ep": self.ep, "ref": "subject:ada",
                                                 "prompt": "one for all"}), 400)
        self.comfy.mode = "reject"
        data = self.gen("location:kitchen")
        (e,) = data["errors"]
        self.assertEqual(e["take"], 1)
        self.assertEqual(self.refs()["location:kitchen"]["takes"][0]["status"], "failed")

    def test_pick_and_conflicts(self):
        self.comfy.mode = "hold"
        self.gen("location:kitchen")
        body = {"ep": self.ep, "ref": "location:kitchen", "take": 1}
        self.err(A.put_refs_pick(self.ctx, body), 409)
        self.comfy.mode = "node"
        self.gen("location:kitchen")
        r = self.ok(A.put_refs_pick(self.ctx, dict(body, take=2)))
        self.assertEqual((r["id"], r["picked"], r["exists"]), ("location:kitchen", 2, True))
        self.assertIn({"ep": self.ep, "ref": "location:kitchen", "view": None, "take": 2,
                       "status": "picked"}, self.events_of("h3pipe.ref"))
        self.err(A.put_refs_pick(self.ctx, dict(body, take=7)), 404)
        self.err(A.put_refs_pick(self.ctx, dict(body, take="x")), 400)
        self.err(A.put_refs_pick(self.ctx, {"ep": self.ep, "ref": "subject:ada", "take": 1}), 400)

    @unittest.skipUnless(HAVE_PIL, "stitching needs PIL")
    def test_pick_views_stitches(self):
        self.gen("subject:bo")
        for v in R.VIEW_TAGS:
            r = self.ok(A.put_refs_pick(self.ctx, {"ep": self.ep, "ref": "subject:bo",
                                                   "view": v, "take": 1}))
        self.assertTrue(r["exists"])
        self.assertEqual([v["picked"] for v in r["views"]], [1, 1, 1, 1])

    def test_import(self):
        t = self.ok(A.post_refs_import(self.ctx, {"ep": self.ep, "ref": "subject:kettle",
                                                  "source_path": self.png()}))
        self.assertEqual((t["take"], t["status"], t["source"]), (1, "ok", "imported"))
        self.err(A.post_refs_import(self.ctx, {"ep": self.ep, "ref": "subject:kettle",
                                               "source_path": os.path.join(self.tmp, "no.png")}),
                 404)
        self.err(A.post_refs_import(self.ctx, {"ep": self.ep, "ref": "subject:kettle"}), 400)
        self.err(A.post_refs_import(self.ctx, {"ep": self.ep, "ref": "subject:kettle",
                                               "source_path": "rel/x.png"}), 400)
        self.err(A.post_refs_import(self.ctx, {"ep": self.ep, "ref": "subject:ada",
                                               "source_path": self.png()}), 400)
        t = self.ok(A.post_refs_import(self.ctx, {"ep": self.ep, "ref": "shot:sh010:last",
                                                  "source_path": self.png()}))
        self.assertEqual(t["image"], "refs/_takes/shot__sh010__last/shot__sh010__last_t01.png")
        self.assertIn("shot:sh010:last", self.refs())
        self.err(A.post_refs_import(self.ctx, {"ep": self.ep, "ref": "shot:sh999:last",
                                               "source_path": self.png()}), 404)

    def test_override(self):
        body = {"ep": self.ep, "ref": "subject:ada", "view": "04_face",
                "fields": {"prompt": "only the face", "seed": "18446744073709551615",
                           "steps": 5, "loras": ["x.safetensors:0.5"]}}
        o = self.ok(A.put_refs_override(self.ctx, body))["override"]
        self.assertEqual(o, {"prompt": "only the face", "seed": "18446744073709551615",
                             "steps": 5, "loras": [{"name": "x.safetensors", "strength": 0.5}],
                             "stale": False})
        ov = T.read_json(os.path.join(self.ep, "refs", "_overrides.json"))
        self.assertIn("base_hash", ov["refs"]["subject:ada"]["views"]["04_face"])
        face = self.refs()["subject:ada"]["views"][3]
        self.assertEqual(face["override"]["fields"], ["loras", "prompt", "seed", "steps"])
        self.assertEqual(face["prompt"], "only the face")
        self.assertEqual(self.events_of("h3pipe.episode"), [{"ep": self.ep}])
        self.err(A.put_refs_override(self.ctx, {"ep": self.ep, "ref": "subject:ada",
                                                "fields": {"prompt": "x"}}), 400)
        self.err(A.put_refs_override(self.ctx, dict(body, fields={"colour": 1})), 400)
        self.err(A.put_refs_override(self.ctx, dict(body, fields={"seed": "-1"})), 400)
        o = self.ok(A.put_refs_override(self.ctx, dict(body, fields={"prompt": None})))
        self.assertNotIn("prompt", o["override"])
        o = self.ok(A.delete_refs_override(self.ctx, {"ep": self.ep, "ref": "subject:ada",
                                                      "view": "04_face"}))["override"]
        self.assertEqual(o, {"stale": False})
        self.ok(A.put_refs_override(self.ctx, {"ep": self.ep, "ref": "location:street",
                                               "fields": {"note": "rainier"}}))
        self.ok(A.delete_refs_override(self.ctx, {"ep": self.ep, "ref": "location:street"}))
        self.assertEqual(T.read_json(os.path.join(self.ep, "refs", "_overrides.json"))["refs"], {})

    def test_sweep_on_list(self):
        self.comfy.mode = "hold"
        self.gen("location:street")
        self.assertEqual(self.refs()["location:street"]["takes"][0]["status"], "queued")
        self.comfy.pending.clear()                       # the job vanished
        # ...a while ago (a take queued in the snapshot's own second is left alone)
        (t,) = R.list_takes(R.find_ref(R.load_series(self.ep), "location:street"))
        T.update_sidecar(t.paths.sidecar, queued="2026-01-01T00:00:00+00:00")
        self.events.clear()
        self.assertEqual(self.refs()["location:street"]["takes"][0]["status"], "failed")
        self.assertEqual(self.events_of("h3pipe.ref"),
                         [{"ep": self.ep, "ref": "location:street", "view": None, "take": 1,
                           "status": "failed"}])

    def test_routes_listed(self):
        got = {(m, p) for m, p, _, _ in A.ROUTES}
        for want in (("GET", "/h3pipe/refs"), ("POST", "/h3pipe/refs/generate"),
                     ("PUT", "/h3pipe/refs/pick"), ("POST", "/h3pipe/refs/import"),
                     ("PUT", "/h3pipe/refs/override"), ("DELETE", "/h3pipe/refs/override")):
            self.assertIn(want, got)


@unittest.skipUnless(test_api.HAVE_FF, "needs ffmpeg and ffprobe on PATH")
class KeyframeApiTest(ApiTest):
    """POST /h3pipe/refs/keyframe: continuity keyframes (h3refs.keyframe_from_take)."""

    def kf(self, **body):
        return A.post_refs_keyframe(self.ctx, dict({"ep": self.ep, "pass": "proxy"}, **body))

    def refs(self):
        data = self.ok(A.get_refs(self.ctx, {"ep": self.ep}))
        return {r["id"]: r for r in data["refs"]}

    def test_from_the_previous_shot(self):
        from test_keyframes import make_take, rgb_frames
        src = make_take(self.ep, "proxy", "sh010")
        r = self.ok(self.kf(shot="sh020"))
        # the ref as GET /h3pipe/refs lists it
        self.assertEqual(r, self.refs()["shot:sh020:first"])
        self.assertEqual((r["id"], r["kind"], r["exists"], r["picked"], r["path"]),
                         ("shot:sh020:first", "keyframe", True, 1, "refs/shots/sh020/first.png"))
        (t,) = r["takes"]
        self.assertEqual((t["source"], t["from"]), ("frame", {"shot": "sh010", "take": 1,
                                                               "pass": "proxy", "frame": 16,
                                                               "frames": 17}))
        (px,) = rgb_frames(os.path.join(self.ep, t["image"]))
        self.assertEqual(px, rgb_frames(src.paths.mp4)[-1])
        self.assertEqual(self.events_of("h3pipe.ref"),
                         [{"ep": self.ep, "ref": "shot:sh020:first", "view": None, "take": 1,
                           "status": "ok"},
                          {"ep": self.ep, "ref": "shot:sh020:first", "view": None, "take": 1,
                           "status": "picked"}])
        self.assertEqual(self.events_of("h3pipe.episode"), [{"ep": self.ep}])
        # a given frame (a string from a form works too), not picked over the live one
        self.events.clear()
        r = self.ok(self.kf(shot="sh020", source_shot="sh010", source_take="1", frame="3"))
        self.assertEqual((r["picked"], r["takes"][1]["from"]["frame"]), (1, 3))
        self.assertEqual([e["status"] for e in self.events_of("h3pipe.ref")], ["ok"])
        r = self.ok(self.kf(shot="sh020", frame="first", pick=True))
        self.assertEqual((r["picked"], r["takes"][2]["from"]["frame"]), (3, 0))
        # the symmetric last keyframe, from the next shot's first frame
        make_take(self.ep, "proxy", "sh030")
        r = self.ok(self.kf(shot="sh020", which="last"))
        self.assertEqual((r["id"], r["takes"][0]["from"]["shot"], r["takes"][0]["from"]["frame"]),
                         ("shot:sh020:last", "sh030", 0))

    def test_errors(self):
        from test_keyframes import make_take
        self.err(self.kf(shot="sh010"), 400)                         # no previous shot
        self.assertIn("no usable proxy take", self.err(self.kf(shot="sh020"), 409))
        make_take(self.ep, "proxy", "sh010")
        self.err(self.kf(shot="sh999"), 404)                         # not in a build
        self.err(self.kf(shot="sh020", source_shot="sh010", source_take=4), 404)
        self.err(self.kf(shot="sh020", which="middle"), 400)
        self.err(self.kf(shot="sh020", frame=99), 400)
        self.err(self.kf(shot="sh020", frame=1.5), 400)
        self.err(self.kf(shot="sh020", pick="yes"), 400)
        self.err(self.kf(shot="sh020", source_take=0), 400)
        self.err(self.kf(shot="sh020", **{"pass": "rough"}), 400)
        self.err(self.kf(), 400)
        self.err(A.post_refs_keyframe(self.ctx, {"ep": os.path.join(self.tmp, "x"),
                                                 "shot": "sh020"}), 403)
        with mock.patch.object(R.shutil, "which", return_value=None):
            self.assertIn("ffprobe", self.err(self.kf(shot="sh020"), 500))
        self.assertEqual(self.events_of("h3pipe.ref"), [])
        self.assertNotIn("shot:sh020:first", self.refs())

    def test_route_listed(self):
        self.assertIn(("POST", "/h3pipe/refs/keyframe"), {(m, p) for m, p, _, _ in A.ROUTES})


class ParentSeriesConfigApiTest(ApiTest):
    build = False

    def setUp(self):
        super().setUp()
        # the series config moves up into the series folder (Shows/)
        shutil.move(os.path.join(self.ep, "series.json"), os.path.join(self.shows, "series.json"))
        self.assertTrue(E.build_episode(self.ep)["ok"])

    def test_refs_live_in_the_series_folder(self):
        p = os.path.join(self.tmp, "in.png")
        with open(p, "wb") as fh:
            fh.write(png_bytes(8, 8))
        t = self.ok(A.post_refs_import(self.ctx, {"ep": self.ep, "ref": "location:kitchen",
                                                  "source_path": p}))
        self.assertEqual(t["image"], "../refs/_takes/location__kitchen/location__kitchen_t01.png")
        f = self.ok(A.get_file(self.ctx, {"ep": self.ep, "path": t["image"]}))
        self.assertTrue(os.path.isfile(f["path"]))
        r = self.ok(A.put_refs_pick(self.ctx, {"ep": self.ep, "ref": "location:kitchen",
                                               "take": 1}))
        self.assertEqual((r["path"], r["exists"]), ("../refs/_bg/kitchen.png", True))
        self.assertTrue(os.path.isfile(os.path.join(self.shows, "refs", "_bg", "kitchen.png")))
        # .. still can't reach anything outside the series folder or the roots
        with open(os.path.join(self.tmp, "secret.txt"), "w") as fh:
            fh.write("x")
        self.err(A.get_file(self.ctx, {"ep": self.ep, "path": "../../secret.txt"}), 400)
        # the series folder itself is not a root here: once it isn't inside one, no
        self.ok(A.put_config(self.ctx, {"roots": [self.ep]}))
        self.err(A.get_file(self.ctx, {"ep": self.ep, "path": t["image"]}), 400)


if __name__ == "__main__":
    unittest.main()
