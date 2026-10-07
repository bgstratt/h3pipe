"""
The post pass (h3post.py, docs/POST_PROCESSING.md): an upscale finished by an
enhance step (pixel, SeedVR2, SUPIR; the tiers name them) and motion blur.

What it refuses, when a post is fresh, the graph for each step, readiness,
the recipe; then its nodes (comfy_nodes/h3_post.py), which need torch and are
skipped without it.
"""
from __future__ import annotations

import json
import os
import sys
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "comfy_nodes"))
import h3edit as E  # noqa: E402
import h3pipe_api as A  # noqa: E402
import h3post as P  # noqa: E402
import h3takes as T  # noqa: E402
import h3upscale as U  # noqa: E402
from test_api import ApiTest  # noqa: E402
from test_phase13 import UpscaleTest  # noqa: E402


class PostTest(ApiTest):
    final_take = UpscaleTest.final_take

    def upscaled(self, shot="sh010") -> T.Take:
        """A final take with a fresh 1920x1088 upscale."""
        t = self.final_take(shot)
        U.start(U.plan_upscale(self.ep, t))
        with open(t.paths.up_mp4, "wb") as fh:
            fh.write(b"up")
        T.update_sidecar(t.paths.up_sidecar, status="ok", width=1920, height=1088,
                         quality="master")
        return T.get_take(self.ep, "final", shot, 1)

    def by_class(self, g, ctype):
        return [k for k, v in g.items() if v["class_type"] == ctype]

    def finish(self, t: T.Take, job: P.PostJob) -> None:
        """What H3SaveUpscale does when it's done: the upscale stamped as it saves."""
        P.start(job)
        self.assertNotIn("source_sha1", T.read_sidecar(t.paths.post_sidecar))
        self.assertFalse(T.post_of(t)["fresh"])
        with open(t.paths.post_mp4, "wb") as fh:
            fh.write(b"post")
        T.update_sidecar(t.paths.post_sidecar, status="ok", **T.source_stamp(t.paths.up_mp4))

    # -- refusals and freshness -----------------------------------------------

    def test_refusals(self):
        t = self.final_take()
        self.assertIn("no upscale", P.plan_post(self.ep, t, enhance="draft").why)
        t = self.upscaled()
        self.assertIn("nothing to do", P.plan_post(self.ep, t).why)
        self.assertIn("tier", P.plan_post(self.ep, t, enhance={"tier": "ultra"}).why)
        self.assertIn("method", P.plan_post(self.ep, t, enhance="ccsr").why)
        self.assertIn("clean-up", P.plan_post(self.ep, t, enhance={"method": "supir",
                                                                   "strength": 0.8}).why)
        self.assertIn("motion_blur", P.plan_post(self.ep, t, blur=2).why)
        # a stale upscale: the take changed under it
        with open(t.paths.mp4, "ab") as fh:
            fh.write(b"more")
        self.assertIn("stale", P.plan_post(self.ep, t, enhance="draft").why)

    def test_fresh_until_the_upscale_changes(self):
        t = self.upscaled()
        job = P.plan_post(self.ep, t, enhance="production")
        self.assertEqual(job.action, "post")
        self.assertEqual((job.width, job.height), (1920, 1088))
        self.assertEqual(job.quality, "master")              # as its upscale was encoded
        self.assertEqual(P.plan_post(self.ep, t, enhance="production",
                                     quality="review").quality, "review")
        self.assertIsNone(T.post_of(t))
        self.finish(t, job)
        self.assertTrue(T.post_of(t)["fresh"])
        self.assertEqual(P.plan_post(self.ep, t, enhance="production").action, "skip")
        # other settings, or --redo: post again
        self.assertEqual(P.plan_post(self.ep, t, enhance="cinematic").action, "post")
        self.assertEqual(P.plan_post(self.ep, t, enhance="production", redo=True).action, "post")
        # a new upscale: the post is stale
        with open(t.paths.up_mp4, "ab") as fh:
            fh.write(b"again")
        self.assertFalse(T.post_of(t)["fresh"])
        # and it goes with the take
        self.assertIn(t.paths.post_mp4, T.stem_files(t.paths.dir, t.paths.stem))

    # -- the graph ------------------------------------------------------------

    def test_after_an_upscale_not_made_yet(self):
        t = self.final_take()                      # no upscale at all
        job = P.plan_post(self.ep, t, enhance="production", blur=0.3,
                          after_upscale=(2560, 1440), quality="master")
        self.assertEqual(job.action, "post")
        self.assertEqual((job.width, job.height, job.enhance["chunk"]), (2560, 1440, 33))
        self.assertEqual(P.describe_recipe({"enhance": "production", "blur": 0.3}),
                         "SeedVR2 7b, then motion blur 0.3")

    def test_tiers(self):
        self.assertEqual(P.resolve_enhance("draft")["method"], "pixel")
        e = P.resolve_enhance("production")
        self.assertEqual((e["method"], e["seedvr2_model"], e["chunk"], e["overlap"]),
                         ("seedvr2", U.SEEDVR2_MODELS["7b"], "fit", 6))
        # "fit": 61 frames at 1080p, fewer as the frame grows
        self.assertEqual(P.fit_chunk(1920, 1080), 61)
        self.assertEqual(P.fit_chunk(1920, 1088), 61)
        self.assertEqual(P.fit_chunk(2560, 1440), 33)
        self.assertEqual(P.fit_chunk(3840, 2160), 13)
        e = P.resolve_enhance({"tier": "production", "seedvr2_model": "3b", "chunk": 0})
        self.assertEqual(e["seedvr2_model"], U.SEEDVR2_MODELS["3b"])
        self.assertNotIn("chunk", e)                          # 0: auto
        e = P.resolve_enhance("cinematic")
        self.assertEqual((e["method"], e["strength"]), ("supir", 0.2))
        self.assertIsNone(P.resolve_enhance("none"))

    def test_pixel_graph(self):
        t = self.upscaled()
        g = P.post_graph(P.plan_post(self.ep, t, enhance="draft"))
        load = g[self.by_class(g, "H3LoadTakeVideo")[0]]["inputs"]
        self.assertTrue(load["video_file"].endswith(".up.mp4"))
        px = g[self.by_class(g, "H3PixelUpscale")[0]]["inputs"]
        self.assertEqual((px["width"], px["height"]), (1920, 1088))      # 1x: a sharpen
        self.assertTrue(px["frequency_split"])
        save = g["pp_save"]["inputs"]
        self.assertTrue(save["stamp_source"])
        self.assertEqual(save["event"], "h3pipe.post")
        self.assertTrue(save["out_mp4"].endswith(".post.mp4"))
        self.assertTrue(save["sidecar"].endswith(".post.json"))
        self.assertTrue(save["source_mp4"].endswith(".up.mp4"))         # the upscale's audio

    def test_seedvr2_graph_is_the_upscales_chain(self):
        t = self.upscaled()
        g = P.post_graph(P.plan_post(self.ep, t, enhance="production"))
        unet = g[self.by_class(g, "UNETLoader")[0]]["inputs"]
        self.assertEqual(unet["unet_name"], U.SEEDVR2_MODELS["7b"])
        scale = g[self.by_class(g, "ImageScale")[0]]["inputs"]
        self.assertEqual((scale["width"], scale["height"]), (1920, 1088))
        self.assertEqual(g["pp_save"]["inputs"]["images"], ["pp_sv_finish", 0])
        self.assertEqual(g["pp_sv_chunk"]["inputs"]["chunking_mode.frames_per_chunk"], 61)
        g = P.post_graph(P.plan_post(self.ep, t, enhance={"tier": "production", "chunk": 0}))
        self.assertEqual(g["pp_sv_chunk"]["inputs"]["chunking_mode"], "auto")
        # fixed chunks, so a redo splits the shot at the same frames
        g = P.post_graph(P.plan_post(self.ep, t, enhance={"tier": "production", "chunk": 49,
                                                          "overlap": 4}))
        ch = g["pp_sv_chunk"]["inputs"]
        self.assertEqual((ch["chunking_mode"], ch["chunking_mode.frames_per_chunk"],
                          ch["temporal_overlap"]), ("manual", 49, 4))
        self.assertIn("4n+1", P.plan_post(self.ep, t, enhance={"tier": "production",
                                                               "chunk": 48}).why)

    def test_supir_graph(self):
        t = self.upscaled()
        g = P.post_graph(P.plan_post(self.ep, t, enhance={"method": "supir", "strength": 0.15}))
        ks = g["pp_ks"]["inputs"]
        self.assertEqual(ks["denoise"], 0.15)
        self.assertEqual(ks["model"], ["pp_supir", 0])
        # one batch at a time, then joined and given the source's colour back
        self.assertEqual(g["pp_split"]["class_type"], "RebatchImages")
        self.assertEqual(g["pp_join"]["inputs"]["images"], ["pp_dec", 0])
        self.assertEqual(g["pp_finish"]["inputs"]["source"], ["pp_video", 0])
        self.assertEqual(g["pp_patch"]["inputs"]["name"], P.SUPIR_MODEL)

    def test_blur_after_enhance(self):
        t = self.upscaled()
        g = P.post_graph(P.plan_post(self.ep, t, enhance="draft", blur=0.3))
        self.assertEqual(g["pp_blur"]["inputs"]["images"], ["pp_pixels", 0])
        self.assertEqual(g["pp_blur"]["inputs"]["amount"], 0.3)
        self.assertEqual(g["pp_save"]["inputs"]["images"], ["pp_blur", 0])
        g = P.post_graph(P.plan_post(self.ep, t, blur=0.3))               # blur alone
        self.assertEqual(g["pp_blur"]["inputs"]["images"], ["pp_video", 0])

    def test_not_ready(self):
        t = self.upscaled()
        jobs = [P.plan_post(self.ep, t, enhance="cinematic", blur=0.25)]
        info = {c: {"input": {"required": {}}} for c in
                P.BASE_NODES + P.SUPIR_NODES + P.BLUR_NODES}
        info["ModelPatchLoader"] = {"input": {"required": {"name": [["other.safetensors"]]}}}
        info["OpticalFlowLoader"] = {"input": {"required": {"model_name": [[]]}}}
        missing = P.not_ready(jobs, info)
        self.assertTrue(any(P.SUPIR_MODEL in m for m in missing))
        self.assertTrue(any(P.RAFT_FILE in m and P.RAFT_URL in m for m in missing))
        del info["H3MotionBlur"]
        self.assertTrue(any("H3MotionBlur" in m for m in P.not_ready(jobs, info)))

    # -- the routes -----------------------------------------------------------

    def ready_comfy(self):
        self.comfy.nodes |= set(P.BASE_NODES) | set(U.PIXEL_NODES) | set(P.BLUR_NODES)
        self.comfy.info["UpscaleModelLoader"] = {"input": {"required": {
            "model_name": [["RealESRGAN_x2.pth"], {}]}}}
        self.comfy.info["OpticalFlowLoader"] = {"input": {"required": {
            "model_name": [[P.RAFT_FILE], {}]}}}

    def set_post(self, recipe):
        p = os.path.join(self.ep, "series.json")
        cfg = json.load(open(p, encoding="utf-8"))
        cfg["post"] = {"master": recipe}
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh)

    def test_post_route(self):
        self.ready_comfy()
        t = self.upscaled()
        # no recipe and nothing asked: 409
        self.err(A.queue_post(self.ctx, {"ep": self.ep, "takes": [{"shot": "sh010", "take": 1}]}), 409)
        out = self.ok(A.queue_post(self.ctx, {"ep": self.ep, "takes": [{"shot": "sh010", "take": 1}],
                                              "enhance": "draft", "blur": 0.3}))
        self.assertEqual([q["shot"] for q in out["queued"]], ["sh010"])
        self.assertIn(("h3pipe.post", {"ep": self.ep, "shot": "sh010", "take": 1,
                                       "status": "queued"}), self.events)
        self.assertTrue(T.post_of(t)["fresh"])               # the fake saver stamped it
        # the take's view says so
        view = E.post_summary(self.ep, T.get_take(self.ep, "final", "sh010", 1))
        self.assertEqual((view["status"], view["fresh"]), ("ok", True))
        self.assertEqual(view["recipe"]["motion_blur"], 0.3)
        # the same again is skipped; the recipe is used when nothing is asked
        out = self.ok(A.queue_post(self.ctx, {"ep": self.ep, "takes": [{"shot": "sh010", "take": 1}],
                                              "enhance": "draft", "blur": 0.3}))
        self.assertEqual(out["queued"], [])
        self.set_post({"enhance": "draft"})
        out = self.ok(A.queue_post(self.ctx, {"ep": self.ep, "takes": [{"shot": "sh010", "take": 1}]}))
        self.assertEqual(len(out["queued"]), 1)
        # bad input
        self.err(A.queue_post(self.ctx, {"ep": self.ep, "shots": "sh010"}), 400)
        self.err(A.queue_post(self.ctx, {"ep": self.ep, "shots": ["sh010"], "blur": "x"}), 400)
        # options: the tiers and the recipe
        opts = self.ok(A.get_post_options(self.ctx, {"ep": self.ep}))
        self.assertEqual([x["id"] for x in opts["tiers"]], list(P.TIERS))
        self.assertEqual(opts["tiers"][0]["readiness"]["status"], "ready")    # draft: pixel
        self.assertEqual(opts["blur"]["readiness"]["status"], "ready")
        self.assertEqual(opts["recipe"]["text"], "pixel")
        # delete
        self.ok(A.delete_post(self.ctx, {"ep": self.ep, "shot": "sh010", "take": "1"}))
        self.assertIsNone(T.post_of(t))
        self.err(A.delete_post(self.ctx, {"ep": self.ep, "shot": "sh010", "take": "1"}), 404)

    # -- the recipe -----------------------------------------------------------

    def test_recipe_and_shot_override(self):
        recipe = {"enhance": "production", "motion_blur": 0}
        shots = {"sh010": {"motion_blur": 0.3, "enhance": "none"}}
        self.assertEqual(P.recipe_for(self.ep, "sh020", recipe, shots),
                         {"enhance": "production", "blur": 0})
        self.assertEqual(P.recipe_for(self.ep, "sh010", recipe, shots),
                         {"enhance": "none", "blur": 0.3})
        self.assertEqual(P.check_recipe(recipe), [])
        bad = P.check_recipe({"enhance": "ultra", "motion_blur": 3, "faces": True})
        self.assertEqual(len(bad), 3, bad)


try:
    import torch
except Exception:                                         # noqa: BLE001
    torch = None


@unittest.skipIf(torch is None, "the post nodes need torch")
class PostNodesTest(unittest.TestCase):
    def setUp(self):
        # H3MotionBlur asks comfy.model_management for the device and to load RAFT
        mm = types.ModuleType("comfy.model_management")
        mm.get_torch_device = lambda: torch.device("cpu")
        mm.load_model_gpu = lambda m: None
        comfy = types.ModuleType("comfy")
        comfy.model_management = mm
        self._mods = {k: sys.modules.get(k) for k in ("comfy", "comfy.model_management")}
        sys.modules.update({"comfy": comfy, "comfy.model_management": mm})
        import h3_post
        self.N = h3_post

    def tearDown(self):
        for k, v in self._mods.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v

    def test_frames_to_batch(self):
        a, b = torch.zeros(2, 4, 6, 3), torch.ones(1, 4, 6, 3)
        out, = self.N.H3FramesToBatch().join([a, b])
        self.assertEqual(tuple(out.shape), (3, 4, 6, 3))
        self.assertEqual(float(out[2].mean()), 1.0)

    def frames(self, n=4, h=64, w=128):
        x = torch.zeros(n, h, w, 3)
        for i in range(n):
            x[i, :, 40 + 4 * i:44 + 4 * i] = 1.0          # a bar moving right
        return x

    def test_blur_zero_passes_through(self):
        x = self.frames()
        out, = self.N.H3MotionBlur().blur(x, None, amount=0.0)
        self.assertIs(out, x)

    def test_blur_spreads_along_the_motion(self):
        x = self.frames()

        # forward flow +4 px, backward -4 px: a steady move right
        def model(a, b):
            n, _, h, w = a.shape
            f = torch.zeros(n, 2, h, w)
            f[:, 0] = 4.0 if model.forward else -4.0
            model.forward = not model.forward
            return [f]
        model.forward = True
        out, = self.N.H3MotionBlur().blur(x, types.SimpleNamespace(model=model), amount=1.0,
                                          samples=9, flow_width=128, max_motion=0.5)
        row = out[1, 32, :, 0]
        sharp = x[1, 32, :, 0]
        self.assertGreater(int((row > 0.01).sum()), int((sharp > 0.01).sum()))   # wider
        self.assertAlmostEqual(float(row.sum()), float(sharp.sum()), delta=0.5)   # same light
        self.assertEqual(tuple(out.shape), tuple(x.shape))

    def test_tracks_round_trip(self):
        doc = {"width": 8, "height": 8, "num_frames": 2, "tracks": [
            {"track_id": 0, "frames": {"0": {"bbox": [1, 1, 2, 2]}}},
            {"track_id": 3, "frames": {"1": {"bbox": [1, 1, 2, 2]}}}]}
        ft = self.N.tracks_from_json(json.loads(json.dumps(doc)), {3})
        self.assertEqual([t["track_id"] for t in ft["tracks"]], [3])
        self.assertEqual(list(ft["tracks"][0]["frames"]), [1])
        self.assertEqual(self.N._ids(" 1, 3 "), {1, 3})
        self.assertIsNone(self.N._ids(""))


if __name__ == "__main__":
    unittest.main()
