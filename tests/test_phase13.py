"""
Phase 13 (docs/PLAN.md): an optional 2x upscale of a picked final take.

13a, a take's latent: whether a render keeps it (the request, else the series
config's `upscale.save_latents`, else final-only), only on a target whose
binding says where its latent comes from (`saver.latent`), and the build's
warning for a value it doesn't know. H3SaveShot's side (writing and loading
the file) is in test_save_node.py; the route's in test_api.py.

13b, the upscale of a take (h3upscale.py): what it refuses, when an upscale
is fresh, and the graph it builds from the target's render graph on both
routes. The nodes it adds are in test_upscale_nodes.py.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "comfy_nodes"))
import h3build  # noqa: E402
import h3upscale as U  # noqa: E402
import h3jobs as J  # noqa: E402
import h3pipe_api as A  # noqa: E402
import h3takes as T  # noqa: E402
import targets as TG  # noqa: E402
from h3core.series_config import (character_ids, load_series_config, series_info,  # noqa: E402
                                  subject_ids, variant_of)
from h3core.story import parse_story  # noqa: E402
from test_api import ApiTest  # noqa: E402

KITCHEN = os.path.join(HERE, "fixtures", "kitchen_sink")


class LatentDefaultTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.show = self._tmp.name
        self.ep = os.path.join(self.show, "ep01")
        os.makedirs(self.ep)

    def tearDown(self):
        self._tmp.cleanup()

    def config(self, upscale):
        with open(os.path.join(self.show, "series.json"), "w", encoding="utf-8") as fh:
            json.dump({"subjects": {}, **({"upscale": upscale} if upscale is not None else {})}, fh)

    def modes(self):
        return J.latent_default(self.ep, "final"), J.latent_default(self.ep, "proxy")

    def test_final_only_by_default(self):
        self.assertEqual(self.modes(), (True, False))      # no series config at all
        self.config(None)
        self.assertEqual(self.modes(), (True, False))
        self.config({})
        self.assertEqual(self.modes(), (True, False))

    def test_series_config_modes(self):
        for mode, want in (("final", (True, False)), ("always", (True, True)),
                           ("never", (False, False)), ("sometimes", (True, False))):
            self.config({"save_latents": mode})
            self.assertEqual(self.modes(), want, mode)

    def test_build_warns_about_a_mode_it_doesnt_know(self):
        cfg = load_series_config(os.path.join(KITCHEN, "series.json"))
        with open(os.path.join(KITCHEN, "script.md"), encoding="utf-8") as fh:
            story = parse_story(fh.read(), subject_ids(cfg), character_ids(cfg),
                                series_info(cfg), variant_of(cfg))
        quiet = h3build.story_warnings(story, cfg)
        self.assertFalse([w for w in quiet if "save_latents" in w])
        cfg["upscale"] = {"save_latents": "always"}
        self.assertEqual(h3build.story_warnings(story, cfg), quiet)
        cfg["upscale"] = {"save_latents": "yes"}
        extra = [w for w in h3build.story_warnings(story, cfg) if "save_latents" in w]
        self.assertEqual(len(extra), 1)
        self.assertIn("'yes'", extra[0])


class LatentBindingTest(unittest.TestCase):
    def test_h3_ref2va_names_its_sampler(self):
        b = TG.load_target("minimax_h3_ref2va", "video").binding
        self.assertEqual(b.saver["latent"], {"class_type": "SamplerCustomAdvanced", "output": 0})
        # the repo workflow has exactly one, so graph_for can link it
        g = J.graph_from(json.load(open(b.workflow, encoding="utf-8")))
        self.assertEqual(sum(v["class_type"] == "SamplerCustomAdvanced" for v in g.values()), 1)


class LatentRouteTest(ApiTest):
    def test_a_target_without_a_latent_source_keeps_none(self):
        """Asked for on a target whose binding names no latent: nothing is
        linked and the sidecar says why."""
        target = "wan22_ti2v"                               # silent, keeps no latent
        self.assertFalse(TG.load_target(target, "video").binding.saver.get("latent"))
        self.ok(A.post_render(self.ctx, {"ep": self.ep, "pass": "final", "shots": ["sh010"],
                                         "target": target, "save_latent": True,
                                         "allow_missing_refs": True,
                                         "allow_model_mismatch": True}))
        g = self.comfy.graphs[-1]
        saver = next(v["inputs"] for v in g.values() if v["class_type"] == J.SAVER)
        self.assertNotIn("latent", saver)
        sc = T.get_take(self.ep, "final", "sh010", 1).sidecar
        self.assertNotIn("save_latent", sc)
        self.assertTrue(any("no latent is kept" in n for n in sc.get("notes", [])), sc)


WORKFLOW = os.path.join(ROOT, "targets", "video", "minimax_h3_ref2va", "workflow.json")


UPSCALER = {"input": {"required": {
    "latent": ["*", {}],
    "model_name": [["minimax_h3_latent_upscaler_3d_fp16.safetensors"], {}],
    "enable_temporal_chunking": ["BOOLEAN", {"default": True}]}}}


class UpscaleTest(ApiTest):
    def final_take(self, shot="sh010", latent=True, wav=True) -> T.Take:
        self.render(shot, pass_="final")
        t = T.get_take(self.ep, "final", shot, 1)
        if latent:
            with open(t.paths.latent, "wb") as fh:
                fh.write(b"latent")
            T.update_sidecar(t.paths.sidecar, latent=os.path.basename(t.paths.latent))
        if wav:
            with open(t.paths.h3_wav, "wb") as fh:
                fh.write(b"RIFF")
        return T.get_take(self.ep, "final", shot, 1)

    def base(self) -> dict:
        return J.graph_from(json.load(open(WORKFLOW, encoding="utf-8")))

    def by_class(self, g, ctype):
        return [k for k, v in g.items() if v["class_type"] == ctype]

    def test_latent_route_graph(self):
        t = self.final_take()
        sc = t.sidecar
        up = U.plan_upscale(self.ep, t)
        # the start is 7/8 of the take's own schedule: kitchen_sink's final pass has 6 steps
        self.assertEqual(sc["steps"], 6)
        self.assertEqual((up.action, up.route, up.scale, up.start_step), ("upscale", "latent", 2.0, 5))
        self.assertEqual((up.width, up.height), (sc["width"] * 2, sc["height"] * 2))
        g = U.upscale_graph(self.base(), up)
        loader = g[self.by_class(g, "H3ShotListLoader")[0]]["inputs"]
        self.assertEqual(loader["resolution_override"], f"{up.width}x{up.height}")
        self.assertEqual(os.path.normpath(os.path.join(self.ep, loader["shotlist_file"])),
                         os.path.normpath(t.paths.shotlist))          # the take's frozen shotlist
        sampler = g[self.by_class(g, "SamplerCustomAdvanced")[0]]["inputs"]
        self.assertEqual(sampler["latent_image"], ["up_hold", 0])
        self.assertEqual(sampler["sigmas"], ["up_sigmas", 1])
        self.assertEqual(g["up_sigmas"]["inputs"]["step"], 5)
        self.assertEqual(g["up_hold"]["inputs"]["latent"], ["up_join", 0])
        self.assertEqual(g["up_join"]["inputs"]["audio_latent"], ["up_split_av", 1])  # audio untouched
        self.assertEqual(g["up_scale"]["inputs"]["latent"], ["up_split_av", 0])
        self.assertEqual(g["up_scale"]["inputs"]["mode.scale"], 2.0)
        self.assertTrue(g["up_scale"]["inputs"]["enable_temporal_chunking"])
        self.assertEqual(g["up_source"]["class_type"], "H3LoadTakeLatent")
        save = g["up_save"]["inputs"]
        self.assertTrue(save["out_mp4"].endswith(".up.mp4"))
        self.assertTrue(save["sidecar"].endswith(".up.json"))
        self.assertEqual(os.path.normpath(os.path.join(self.ep, save["source_mp4"])),
                         os.path.normpath(t.paths.mp4))
        # the take's own saver and audio decode are gone: the audio is copied
        self.assertFalse(self.by_class(g, J.SAVER))
        self.assertFalse(self.by_class(g, "VAEDecodeAudio"))

    def test_vae_route_encodes_the_take_and_its_h3_mix(self):
        t = self.final_take(latent=False)
        up = U.plan_upscale(self.ep, t)
        self.assertEqual(up.route, "vae")
        g = U.upscale_graph(self.base(), up)
        self.assertEqual(g["up_video"]["class_type"], "H3LoadTakeVideo")
        self.assertTrue(g["up_video"]["inputs"]["audio_file"].endswith("_h3.wav"))
        decode_vae = g[self.by_class(g, "VAEDecode")[0]]["inputs"]["vae"]
        self.assertEqual(g["up_venc"]["inputs"]["vae"], decode_vae)
        self.assertEqual(g["up_source"]["class_type"], "LTXVConcatAVLatent")
        self.assertEqual(g["up_split_av"]["inputs"]["av_latent"], ["up_source", 0])
        # a take with no H3 mix: the mp4's audio
        os.remove(t.paths.h3_wav)
        g = U.upscale_graph(self.base(), U.plan_upscale(self.ep, t))
        self.assertEqual(g["up_video"]["inputs"]["audio_file"], "")

    def test_refusals(self):
        t = self.final_take(latent=False)
        self.assertEqual(U.plan_upscale(self.ep, t, route="latent").action, "error")
        self.assertEqual([U.start_of(n, {}) for n in (8, 6, 20, 4, 1)], [7, 5, 18, 3, 0])
        bad = U.plan_upscale(self.ep, t, start_step=6)
        self.assertEqual(bad.action, "error")
        self.assertIn("start step", bad.why)
        self.render("sh010")
        # a proxy take upscales too: it kept no latent, so through the VAE
        pt = T.get_take(self.ep, "proxy", "sh010", 1)
        proxy = U.plan_upscale(self.ep, pt)
        self.assertEqual((proxy.action, proxy.route), ("upscale", "vae"))
        self.assertEqual((proxy.width, proxy.height), (pt.sidecar["width"] * 2, pt.sidecar["height"] * 2))
        self.assertEqual(U.plan_upscale(self.ep, pt, method="pixel").action, "upscale")
        with self.assertRaises(U.UpscaleError):
            U.scaled(960, 544, 1.5)                      # 816 isn't a multiple of 32
        self.assertEqual(U.scaled(1344, 768, 1.5), (2016, 1152))

    def test_fresh_until_the_take_changes(self):
        t = self.final_take()
        self.assertIsNone(T.upscale_of(t))
        up = U.plan_upscale(self.ep, t)
        U.start(up)
        self.assertEqual(T.upscale_of(t)["status"], "queued")
        self.assertFalse(T.upscale_of(t)["fresh"])
        with open(t.paths.up_mp4, "wb") as fh:
            fh.write(b"up")
        T.update_sidecar(t.paths.up_sidecar, status="ok")
        self.assertTrue(T.upscale_of(t)["fresh"])
        self.assertEqual(U.plan_upscale(self.ep, t).action, "skip")
        self.assertEqual(U.plan_upscale(self.ep, t, redo=True).action, "upscale")
        # the take re-rendered into the same number: the upscale is stale
        with open(t.paths.mp4, "ab") as fh:
            fh.write(b"more")
        self.assertFalse(T.upscale_of(t)["fresh"])
        self.assertEqual(U.plan_upscale(self.ep, t).action, "upscale")
        # an upscale is never listed as a take, and discarding the take takes it along
        self.assertEqual(T.take_numbers(self.ep, "final", "sh010"), [1])
        stems = T.stem_files(t.paths.dir, t.paths.stem)
        self.assertIn(t.paths.up_mp4, stems)
        self.assertIn(t.paths.up_sidecar, stems)

    def test_cut_takes_follow_the_final_cut(self):
        self.final_take("sh010")
        got = {shot: (take.take if take else None) for shot, take, _ in U.cut_takes(self.ep)}
        self.assertEqual(got.get("sh010"), 1)
        only = U.cut_takes(self.ep, {"sh010"})
        self.assertEqual([s for s, _, _ in only], ["sh010"])


class UpscaleRouteTest(UpscaleTest):
    def setUp(self):
        super().setUp()
        self.comfy.nodes |= set(U.UPSCALE_NODES)
        self.comfy.info["MinimaxH3LatentUpscaler3D"] = UPSCALER

    def status_take(self, shot="sh010", n=1):
        data = self.ok(A.get_episode(self.ctx, {"ep": self.ep, "pass": "final"}))
        s = next(x for x in data["shots"] if x["shot"] == shot)
        return next(t for t in s["takes"] if t["take"] == n)

    def test_upscale_the_cut_take(self):
        self.final_take()
        self.assertIsNone(self.status_take()["upscale"])
        res = self.ok(A.post_upscale(self.ctx, {"ep": self.ep, "shots": ["sh010"]}))
        self.assertEqual([(q["shot"], q["take"], q["route"]) for q in res["queued"]],
                         [("sh010", 1, "latent")])
        self.assertEqual(self.events_of("h3pipe.upscale")[-1]["status"], "queued")
        g = self.comfy.graphs[-1]
        self.assertTrue(any(v["class_type"] == "H3SaveUpscale" for v in g.values()))
        up = self.status_take()["upscale"]
        self.assertEqual((up["status"], up["fresh"], up["route"]), ("ok", True, "latent"))
        self.assertTrue(up["mp4"].endswith("sh010_t01.up.mp4"))
        # fresh: asked again, it is skipped; redo queues it again
        res = self.ok(A.post_upscale(self.ctx, {"ep": self.ep, "shots": ["sh010"]}))
        self.assertEqual((res["queued"], res["skipped"][0]["reason"]), ([], "already upscaled"))
        res = self.ok(A.post_upscale(self.ctx, {"ep": self.ep, "takes": [{"shot": "sh010", "take": 1}],
                                                "redo": True, "vae": True, "start_step": 4}))
        self.assertEqual((res["queued"][0]["route"], res["queued"][0]["start_step"]), ("vae", 4))

    def test_errors_and_not_ready(self):
        self.final_take()
        res = self.ok(A.post_upscale(self.ctx, {"ep": self.ep, "shots": ["sh020"]}))
        self.assertEqual(res["queued"], [])
        self.assertEqual(res["skipped"][0]["shot"], "sh020")      # never rendered
        res = self.ok(A.post_upscale(self.ctx, {"ep": self.ep, "shots": ["sh010"], "scale": 1.3}))
        self.assertEqual(res["queued"], [])
        self.assertIn("multiple of 32", res["errors"][0]["error"])
        self.err(A.post_upscale(self.ctx, {"ep": self.ep, "shots": "sh010"}), 400)
        res = self.ok(A.post_upscale(self.ctx, {"ep": self.ep, "takes": [{"shot": "sh010", "take": 9}]}))
        self.assertIn("doesn't exist", res["skipped"][0]["reason"])
        # the Plus fork: same node, no temporal chunking
        self.comfy.info["MinimaxH3LatentUpscaler3D"] = {"input": {"required": {
            "latent": ["*", {}], "model_name": [["minimax_h3_latent_upscaler_3d_fp16.safetensors"], {}]}}}
        msg = self.err(A.post_upscale(self.ctx, {"ep": self.ep, "shots": ["sh010"]}), 409)
        self.assertIn("Plus fork", msg)
        self.assertFalse(os.path.exists(T.get_take(self.ep, "final", "sh010", 1).paths.up_sidecar))

    def test_delete(self):
        t = self.final_take()
        self.ok(A.post_upscale(self.ctx, {"ep": self.ep, "shots": ["sh010"]}))
        self.assertTrue(os.path.isfile(t.paths.up_mp4))
        self.ok(A.delete_upscale(self.ctx, {"ep": self.ep, "shot": "sh010", "take": "1"}))
        self.assertFalse(os.path.exists(t.paths.up_mp4))
        self.assertFalse(os.path.exists(t.paths.up_sidecar))
        self.assertIsNone(self.status_take()["upscale"])
        self.err(A.delete_upscale(self.ctx, {"ep": self.ep, "shot": "sh010", "take": "1"}), 404)

    def test_readiness_says_whether_it_can_upscale(self):
        import h3edit as E
        t = TG.load_target("minimax_h3_ref2va", "video")
        info = {c: {"input": {}} for c in U.UPSCALE_NODES}
        info["MinimaxH3LatentUpscaler3D"] = UPSCALER
        self.assertEqual(U.upscale_readiness(t, info), {"status": "ready", "missing": []})
        del info["H3HoldAudio"]
        r = U.upscale_readiness(t, info)
        self.assertEqual(r["status"], "not_ready")
        self.assertIn("H3HoldAudio", r["missing"][0])
        self.assertEqual(U.upscale_readiness(t, None)["status"], "unknown")
        wan = TG.load_target("wan22_vace", "video")          # no re-sample of its own
        self.assertIsNone(U.upscale_readiness(wan, info))
        self.assertNotIn("upscale", E.readiness([wan], info)[wan.id])
        self.assertIn("upscale", E.readiness([t], info)[t.id])


class LtxUpscaleTest(UpscaleTest):
    """13d: LTX-2.5's upscale is its own second stage run again on the take."""
    LTX_WORKFLOW = os.path.join(ROOT, "targets", "video", "ltx2", "workflow.json")

    def ltx_take(self, latent=True) -> T.Take:
        self.ok(A.post_render(self.ctx, {"ep": self.ep, "pass": "final", "shots": ["sh010"],
                                         "target": "ltx2", "allow_missing_refs": True,
                                         "allow_model_mismatch": True}))
        g = self.comfy.graphs[-1]
        saver = next(k for k, v in g.items() if v["class_type"] == J.SAVER)
        final = g[saver]["inputs"]["latent"][0]
        # the saver keeps the FINAL sampler's latent, not the first stage's
        self.assertEqual(g[final]["class_type"], "SamplerCustomAdvanced")
        self.assertEqual(g[g[final]["inputs"]["sigmas"][0]]["inputs"]["sigmas"], "0.85, 0.7250, 0.4219, 0.0")
        t = T.get_take(self.ep, "final", "sh010", 1)
        self.assertEqual(t.sidecar["target"], "ltx2")
        if latent:
            with open(t.paths.latent, "wb") as fh:
                fh.write(b"latent")
            T.update_sidecar(t.paths.sidecar, latent=os.path.basename(t.paths.latent))
        return T.get_take(self.ep, "final", "sh010", 1)

    def ltx_base(self) -> dict:
        return J.graph_from(json.load(open(self.LTX_WORKFLOW, encoding="utf-8")))

    def test_second_stage_graph(self):
        t = self.ltx_take()
        up = U.plan_upscale(self.ep, t)
        self.assertEqual((up.action, up.route, up.start_step), ("upscale", "latent", 2))
        self.assertEqual((up.width, up.height), (t.sidecar["width"] * 2, t.sidecar["height"] * 2))
        g = U.upscale_graph(self.ltx_base(), up)
        samplers = self.by_class(g, "SamplerCustomAdvanced")
        self.assertEqual(len(samplers), 1)                             # stage 1 is gone
        self.assertFalse(self.by_class(g, "EmptyLTXVLatentVideo"))
        si = g[samplers[0]]["inputs"]
        self.assertEqual(si["latent_image"], ["up_hold", 0])
        join = g["up_hold"]["inputs"]["latent"][0]
        self.assertEqual(g[join]["class_type"], "LTXVConcatAVLatent")
        self.assertEqual(g[join]["inputs"]["audio_latent"], ["up_split_av", 1])   # the take's audio
        ups = self.by_class(g, "LTXVLatentUpsampler")
        self.assertEqual(len(ups), 1)
        self.assertEqual(g[ups[0]]["inputs"]["samples"], ["up_split_av", 0])
        self.assertEqual(g[si["sigmas"][0]]["inputs"]["sigmas"], "0.4219, 0.0")
        self.assertEqual(g["up_source"]["class_type"], "H3LoadTakeLatent")
        self.assertTrue(self.by_class(g, "H3SaveUpscale"))
        self.assertFalse(self.by_class(g, J.SAVER))
        # a longer tail by hand; past the end is refused
        g = U.upscale_graph(self.ltx_base(), U.plan_upscale(self.ep, t, start_step=0))
        s = self.by_class(g, "SamplerCustomAdvanced")[0]
        self.assertEqual(g[g[s]["inputs"]["sigmas"][0]]["inputs"]["sigmas"], "0.85, 0.7250, 0.4219, 0.0")
        self.assertEqual(U.plan_upscale(self.ep, t, start_step=3).action, "error")
        self.assertEqual(U.plan_upscale(self.ep, t, scale=1.5).action, "error")   # fixed 2x

    def test_vae_route_uses_ltx_audio_encoder(self):
        t = self.ltx_take(latent=False)
        up = U.plan_upscale(self.ep, t)
        self.assertEqual(up.route, "vae")
        g = U.upscale_graph(self.ltx_base(), up)
        self.assertEqual(g["up_aenc"]["class_type"], "LTXVAudioVAEEncode")
        self.assertIn("audio_vae", g["up_aenc"]["inputs"])
        self.assertEqual(g["up_venc"]["class_type"], "VAEEncode")

    def test_readiness_needs_no_extra_pack(self):
        t = TG.load_target("ltx2", "video")
        info = {c: {"input": {}} for c in U.UPSCALE_NODES + ("LTXVLatentUpsampler", "LTXVAudioVAEEncode")}
        self.assertEqual(U.upscale_readiness(t, info), {"status": "ready", "missing": []})
        del info["LTXVAudioVAEEncode"]
        self.assertEqual(U.upscale_readiness(t, info)["status"], "not_ready")


class PixelUpscaleTest(UpscaleRouteTest):
    """The pixel method: an upscale model over the frames, for any target."""

    def setUp(self):
        super().setUp()
        self.comfy.nodes |= set(U.PIXEL_NODES)
        self.comfy.info["UpscaleModelLoader"] = {"input": {"required": {
            "model_name": [["4x-UltraSharp.pth", "RealESRGAN_x2.pth", "RealESRGAN_x4.pth"], {}]}}}

    def as_target(self, t: T.Take, target: str) -> T.Take:
        T.update_sidecar(t.paths.sidecar, target=target)
        return T.get_take(self.ep, "final", t.shot, t.take)

    def test_default_method_follows_the_target(self):
        t = self.final_take()
        self.assertEqual(U.plan_upscale(self.ep, t).method, "latent")         # H3 has one
        wan = self.as_target(t, "wan22_vace")              # no re-sample of its own
        up = U.plan_upscale(self.ep, wan)
        self.assertEqual((up.method, up.route, up.pixel_model, up.start_step),
                         ("pixel", "pixel", "RealESRGAN_x2.pth", None))
        self.assertEqual((up.width, up.height), (wan.sidecar["width"] * 2, wan.sidecar["height"] * 2))
        # asked for a latent upscale it doesn't have: said plainly
        bad = U.plan_upscale(self.ep, wan, method="latent")
        self.assertEqual(bad.action, "error")
        self.assertIn("use the pixel method", bad.why)
        # any scale that lands on even sides
        self.assertEqual(U.plan_upscale(self.ep, wan, scale=1.5).action, "upscale")

    def test_pixel_graph(self):
        t = self.final_take()
        up = U.plan_upscale(self.ep, t, method="pixel", pixel_model="RealESRGAN_x4.pth")
        g = U.pixel_graph(up)
        self.assertEqual(g["up_model"]["inputs"]["model_name"], "RealESRGAN_x4.pth")
        px = g["up_pixels"]["inputs"]
        self.assertEqual((px["width"], px["height"]), (up.width, up.height))
        self.assertEqual(px["images"], ["up_video", 0])
        self.assertEqual(g["up_save"]["inputs"]["images"], ["up_pixels", 0])
        self.assertTrue(g["up_save"]["inputs"]["out_mp4"].endswith(".up.mp4"))
        self.assertEqual({v["class_type"] for v in g.values()}, set(U.PIXEL_NODES))

    def test_detail_starts_earlier(self):
        t = self.final_take()
        steps = t.sidecar["steps"]                                  # kitchen_sink final: 6
        base = U.start_of(steps, U.upscale_spec(TG.load_target("minimax_h3_ref2va", "video")))
        self.assertEqual([U.plan_upscale(self.ep, t, detail=d).start_step for d in U.DETAILS],
                         [base, base - 1, base - 2])

    def test_route_and_options(self):
        t = self.final_take()
        res = self.ok(A.post_upscale(self.ctx, {"ep": self.ep, "takes": [{"shot": "sh010", "take": 1}],
                                                "method": "pixel", "pixel_model": "4x-UltraSharp.pth"}))
        q = res["queued"][0]
        self.assertEqual((q["method"], q["pixel_model"], q["route"]), ("pixel", "4x-UltraSharp.pth", "pixel"))
        self.assertTrue(any(v["class_type"] == "H3PixelUpscale" for v in self.comfy.graphs[-1].values()))
        up = self.status_take()["upscale"]
        self.assertEqual((up["method"], up["pixel_model"], up["fresh"]), ("pixel", "4x-UltraSharp.pth", True))
        # a model this ComfyUI doesn't have
        msg = self.err(A.post_upscale(self.ctx, {"ep": self.ep, "shots": ["sh010"], "redo": True,
                                                 "method": "pixel", "pixel_model": "nope.pth"}), 409)
        self.assertIn("nope.pth", msg)
        self.err(A.post_upscale(self.ctx, {"ep": self.ep, "shots": ["sh010"], "method": "sharp"}), 400)
        self.err(A.post_upscale(self.ctx, {"ep": self.ep, "shots": ["sh010"], "detail": 5}), 400)
        opts = self.ok(A.get_upscale_options(self.ctx, {}))
        self.assertEqual(opts["pixel"]["status"], "ready")
        self.assertEqual(opts["pixel"]["default"], "RealESRGAN_x2.pth")
        self.assertIn("RealESRGAN_x4.pth", opts["pixel"]["models"])
        self.assertEqual(opts["latent"]["minimax_h3_ref2va"]["status"], "ready")
        self.assertIsNone(opts["latent"]["wan22_vace"])
        self.assertEqual(opts["details"], [0, 1, 2])

    def test_the_proxy_pass(self):
        self.render("sh010")                                       # a proxy take
        res = self.ok(A.post_upscale(self.ctx, {"ep": self.ep, "pass": "proxy", "shots": ["sh010"],
                                                "method": "pixel"}))
        self.assertEqual([(q["shot"], q["take"], q["method"]) for q in res["queued"]], [("sh010", 1, "pixel")])
        t = T.get_take(self.ep, "proxy", "sh010", 1)
        self.assertTrue(os.path.isfile(t.paths.up_mp4))
        self.assertTrue("renders_proxy" in t.paths.up_mp4)
        data = self.ok(A.get_episode(self.ctx, {"ep": self.ep, "pass": "proxy"}))
        tk = next(x for s in data["shots"] if s["shot"] == "sh010" for x in s["takes"] if x["take"] == 1)
        self.assertEqual((tk["upscale"]["method"], tk["upscale"]["fresh"]), ("pixel", True))
        # the final pass's sh010 is untouched, and the proxy one is deleted by pass
        self.assertIsNone(T.get_take(self.ep, "final", "sh010", 1))
        self.err(A.delete_upscale(self.ctx, {"ep": self.ep, "shot": "sh010", "take": "1"}), 404)
        self.ok(A.delete_upscale(self.ctx, {"ep": self.ep, "pass": "proxy", "shot": "sh010", "take": "1"}))
        self.assertFalse(os.path.exists(t.paths.up_mp4))

    def test_then_pixel(self):
        """Re-sample 2x, then an upscale model 2x more, in one job: 4x."""
        t = self.final_take()
        w, h = t.sidecar["width"], t.sidecar["height"]
        up = U.plan_upscale(self.ep, t, then_model="RealESRGAN_x2.pth")
        self.assertEqual(up.action, "upscale")
        self.assertEqual(((up.width, up.height), up.out_size), ((w * 2, h * 2), (w * 4, h * 4)))
        g = U.upscale_graph(self.base(), up)
        decode = self.by_class(g, "VAEDecode")[0]
        self.assertEqual(g["up_then"]["inputs"]["images"], [decode, 0])
        self.assertEqual((g["up_then"]["inputs"]["width"], g["up_then"]["inputs"]["height"]), (w * 4, h * 4))
        self.assertEqual(g["up_then_model"]["inputs"]["model_name"], "RealESRGAN_x2.pth")
        self.assertEqual(g["up_save"]["inputs"]["images"], ["up_then", 0])
        loader = g[self.by_class(g, "H3ShotListLoader")[0]]["inputs"]
        self.assertEqual(loader["resolution_override"], f"{w * 2}x{h * 2}")   # the re-sample's size
        self.assertIn("then RealESRGAN_x2.pth 2x", U.describe(up))
        rec = U.queued_record(up)
        self.assertEqual((rec["width"], rec["height"]), (w * 4, h * 4))
        self.assertEqual(rec["then_pixel"], {"model": "RealESRGAN_x2.pth", "scale": 2.0, "from": [w * 2, h * 2]})
        # through the route: the answer is the final size; a model it lacks is a 409
        res = self.ok(A.post_upscale(self.ctx, {"ep": self.ep, "shots": ["sh010"], "then_pixel_model": "RealESRGAN_x4.pth",
                                                "then_scale": 1.5}))
        q = res["queued"][0]
        self.assertEqual((q["width"], q["height"], q["then_pixel_model"]), (w * 3, h * 3, "RealESRGAN_x4.pth"))
        self.assertEqual(self.status_take()["upscale"]["then_pixel"]["model"], "RealESRGAN_x4.pth")
        msg = self.err(A.post_upscale(self.ctx, {"ep": self.ep, "shots": ["sh010"], "redo": True,
                                                 "then_pixel_model": "gone.pth"}), 409)
        self.assertIn("gone.pth", msg)

    def test_scale_limits(self):
        t = self.final_take()
        self.assertEqual(U.plan_upscale(self.ep, t, method="pixel", scale=4).action, "upscale")
        self.assertEqual(U.plan_upscale(self.ep, t, method="pixel", scale=5).action, "error")
        self.assertEqual(U.plan_upscale(self.ep, t, scale=1).action, "error")
        self.assertEqual(U.plan_upscale(self.ep, t, then_model="RealESRGAN_x2.pth", then_scale=6).action, "error")
        opts = self.ok(A.get_upscale_options(self.ctx, {}))
        self.assertEqual(opts["max_scale"], 4.0)
        h3 = opts["latent"]["minimax_h3_ref2va"]
        self.assertEqual((h3["mode"], h3["align"], h3["fixed_scale"]), ("resample", 32, None))
        # ltx2 is second_stage, fixed 2x (its readiness depends on nodes this fake lacks)
        self.assertEqual((opts["latent"]["ltx2"]["mode"], opts["latent"]["ltx2"]["fixed_scale"]), ("second_stage", 2.0))
        take = self.status_take()
        self.assertEqual((take["width"], take["height"]), (t.sidecar["width"], t.sidecar["height"]))

    def test_pixel_on_top_of_an_upscale(self):
        """A re-sample first, then later the pixel method on its result."""
        t = self.final_take()
        w, h = t.sidecar["width"], t.sidecar["height"]
        no = U.plan_upscale(self.ep, t, from_upscale=True)
        self.assertEqual(no.action, "error")
        self.assertIn("no fresh upscale", no.why)
        self.ok(A.post_upscale(self.ctx, {"ep": self.ep, "shots": ["sh010"]}))       # the re-sample
        t = T.get_take(self.ep, "final", "sh010", 1)
        up = U.plan_upscale(self.ep, t, from_upscale=True)
        self.assertEqual((up.action, up.method), ("upscale", "pixel"))              # never "already upscaled"
        self.assertEqual((up.width, up.height), (w * 4, h * 4))                     # 2x of the 2x
        g = U.pixel_graph(up)
        self.assertTrue(g["up_video"]["inputs"]["video_file"].endswith(".up.mp4"))
        self.assertTrue(g["up_save"]["inputs"]["source_mp4"].endswith("sh010_t01.mp4"))  # the take's audio
        self.assertEqual(U.queued_record(up)["on_upscale"]["method"], "latent")
        self.assertIn("on its upscale", U.describe(up))
        self.assertEqual(U.plan_upscale(self.ep, t, method="latent", from_upscale=True).action, "error")
        # through the route; the chain reads back, and a second step nests
        res = self.ok(A.post_upscale(self.ctx, {"ep": self.ep, "shots": ["sh010"], "from_upscale": True,
                                                "scale": 1.5}))
        self.assertEqual((res["queued"][0]["width"], res["queued"][0]["height"]), (w * 3, h * 3))
        st = self.status_take()["upscale"]
        self.assertEqual((st["method"], st["on_upscale"]["method"], st["fresh"]), ("pixel", "latent", True))
        t = T.get_take(self.ep, "final", "sh010", 1)
        again = U.queued_record(U.plan_upscale(self.ep, t, from_upscale=True))
        self.assertEqual((again["on_upscale"]["method"], again["on_upscale"]["on_upscale"]["method"]),
                         ("pixel", "latent"))
        self.err(A.post_upscale(self.ctx, {"ep": self.ep, "shots": ["sh010"], "from_upscale": "yes"}), 400)

    def test_encoder_and_precision(self):
        t = self.final_take()
        up = U.plan_upscale(self.ep, t, method="pixel", encoder="nvenc", precision="fp32")
        g = U.pixel_graph(up)
        self.assertEqual((g["up_save"]["inputs"]["encoder"], g["up_pixels"]["inputs"]["precision"]),
                         ("nvenc", "fp32"))
        rec = U.queued_record(up)
        self.assertEqual((rec["encoder_asked"], rec["precision"]), ("nvenc", "fp32"))
        lat = U.plan_upscale(self.ep, t, then_model="RealESRGAN_x2.pth")
        g = U.upscale_graph(self.base(), lat)
        self.assertEqual((g["up_save"]["inputs"]["encoder"], g["up_then"]["inputs"]["precision"]), ("auto", "fp16"))
        self.assertEqual(U.plan_upscale(self.ep, t, encoder="gpu").action, "error")
        self.assertEqual(U.plan_upscale(self.ep, t, precision="fp8").action, "error")
        self.err(A.post_upscale(self.ctx, {"ep": self.ep, "shots": ["sh010"], "encoder": "gpu"}), 400)
        self.err(A.post_upscale(self.ctx, {"ep": self.ep, "shots": ["sh010"], "precision": "fp8"}), 400)
        opts = self.ok(A.get_upscale_options(self.ctx, {}))
        self.assertEqual((opts["encoders"], opts["precisions"]), (["auto", "nvenc", "x264"], ["fp16", "fp32"]))

    def wan_base(self, target: str) -> dict:
        t = TG.load_target(target, "video")
        return J.graph_from(json.load(open(t.binding.workflow, encoding="utf-8")))

    def test_wan_i2v_refine(self):
        """13d: Wan re-samples from the take's frames, upscaled by a pixel model:
        the low-noise expert alone from the start step, the first frame at the new size."""
        t = self.as_target(self.final_take(latent=False), "wan22_i2v")
        # an i2v take records the first frame it was staged with; the upscale reuses it
        T.update_sidecar(t.paths.sidecar, width=832, height=480, steps=4,
                         inputs={"first": "h3pipe/sh010_t01_first.png"})
        t = T.get_take(self.ep, "final", "sh010", 1)
        up = U.plan_upscale(self.ep, t)
        self.assertEqual((up.action, up.method, up.route, up.pixel_model), ("upscale", "latent", "vae", "RealESRGAN_x2.pth"))
        self.assertEqual((up.width, up.height, up.start_step), (1664, 960, 3))
        g = U.upscale_graph(self.wan_base("wan22_i2v"), up)
        samplers = self.by_class(g, "KSamplerAdvanced")
        self.assertEqual(len(samplers), 1)                                   # the high expert is gone
        si = g[samplers[0]]["inputs"]
        self.assertEqual((si["add_noise"], si["start_at_step"], si["latent_image"]), ("enable", 3, ["up_venc", 0]))
        self.assertEqual((g["up_pixels"]["inputs"]["width"], g["up_pixels"]["inputs"]["height"]), (1664, 960))
        wiv = g[self.by_class(g, "WanImageToVideo")[0]]["inputs"]
        self.assertEqual((wiv["width"], wiv["height"]), (1664, 960))        # the first frame, rebuilt at 2x
        first = g[wiv["start_image"][0]]["inputs"]["image"]
        self.assertEqual(first, "h3pipe/sh010_t01_first.png")
        self.assertEqual(g["up_venc"]["inputs"]["vae"][0], self.by_class(g, "VAELoader")[0])
        self.assertFalse(self.by_class(g, "H3HoldAudio"))                    # silent
        self.assertTrue(self.by_class(g, "H3SaveUpscale"))
        rec = U.queued_record(up)
        self.assertEqual((rec["mode"], rec["upscaler"]), ("pixel_refine", "RealESRGAN_x2.pth, then the target's own sampler"))
        self.assertEqual(U.plan_upscale(self.ep, t, route="latent").action, "error")
        # its readiness asks for the pixel pieces, and the model
        info = {c: {"input": {}} for c in ("H3LoadTakeVideo", "H3PixelUpscale", "VAEEncode", "H3SaveUpscale")}
        info["UpscaleModelLoader"] = {"input": {"required": {"model_name": [["RealESRGAN_x2.pth"], {}]}}}
        self.assertEqual(U.not_ready([up], info), [])
        missing = U.not_ready([U.plan_upscale(self.ep, t, pixel_model="RealESRGAN_x4.pth")], info)
        self.assertEqual(len(missing), 1)
        self.assertIn("RealESRGAN_x4.pth", missing[0])

    def test_wan_ti2v_refine(self):
        t = self.as_target(self.final_take(latent=False), "wan22_ti2v")
        T.update_sidecar(t.paths.sidecar, width=1280, height=704, steps=20)
        t = T.get_take(self.ep, "final", "sh010", 1)
        up = U.plan_upscale(self.ep, t)
        self.assertEqual((up.width, up.height, up.start_step), (1920, 1056, 15))   # 1.5x by default
        g = U.upscale_graph(self.wan_base("wan22_ti2v"), up)
        ks = g[self.by_class(g, "KSampler")[0]]["inputs"]
        self.assertEqual((ks["denoise"], ks["latent_image"]), (0.25, ["up_venc", 0]))
        self.assertEqual(U.plan_upscale(self.ep, t, scale=2).height, 1408)

    def test_finish_reaches_every_pixel_node(self):
        t = self.final_take()
        kw = dict(frequency_split=False, keep_soft=0.5, grain=0.03)
        seed = int(t.sidecar["seed"]) % (1 << 32)
        g = U.pixel_graph(U.plan_upscale(self.ep, t, method="pixel", **kw))
        px = g["up_pixels"]["inputs"]
        self.assertEqual((px["frequency_split"], px["keep_soft"], px["grain"], px["grain_seed"]),
                         (False, 0.5, 0.03, seed))
        g = U.upscale_graph(self.base(), U.plan_upscale(self.ep, t, then_model="RealESRGAN_x2.pth", **kw))
        self.assertEqual(g["up_then"]["inputs"]["grain"], 0.03)
        # defaults: the split on, the rest off
        px = U.pixel_graph(U.plan_upscale(self.ep, t, method="pixel"))["up_pixels"]["inputs"]
        self.assertEqual((px["frequency_split"], px["keep_soft"], px["grain"]), (True, 0.0, 0.0))
        rec = U.queued_record(U.plan_upscale(self.ep, t, method="pixel", **kw))
        self.assertEqual(rec["finish"], {"frequency_split": False, "keep_soft": 0.5, "grain": 0.03})
        self.assertEqual(U.plan_upscale(self.ep, t, method="pixel", grain=0.5).action, "error")
        self.assertEqual(U.plan_upscale(self.ep, t, method="pixel", keep_soft=2).action, "error")
        self.err(A.post_upscale(self.ctx, {"ep": self.ep, "shots": ["sh010"], "grain": 1}), 400)
        self.err(A.post_upscale(self.ctx, {"ep": self.ep, "shots": ["sh010"], "frequency_split": "no"}), 400)
        # Wan's re-sample: the finish before the sampler, but never grain
        wan = self.as_target(t, "wan22_ti2v")
        T.update_sidecar(wan.paths.sidecar, width=1280, height=704, steps=20)
        wan = T.get_take(self.ep, "final", "sh010", 1)
        g = U.upscale_graph(self.wan_base("wan22_ti2v"), U.plan_upscale(self.ep, wan, **kw))
        self.assertEqual((g["up_pixels"]["inputs"]["keep_soft"], g["up_pixels"]["inputs"]["grain"]), (0.5, 0.0))

    def test_default_pixel_model(self):
        self.assertEqual(U.default_pixel_model(["4x-UltraSharp.pth", "RealESRGAN_x2.pth"]), "RealESRGAN_x2.pth")
        self.assertEqual(U.default_pixel_model(["4x-UltraSharp.pth", "2x-Other.pth"]), "2x-Other.pth")
        self.assertEqual(U.default_pixel_model(["4x-UltraSharp.pth"]), "4x-UltraSharp.pth")
        self.assertEqual(U.pixel_readiness({})["status"], "not_ready")      # no nodes, no models


if __name__ == "__main__":
    unittest.main()
