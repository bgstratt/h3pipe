"""
An optional 2x upscale of a picked final take.

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

# every built-in video target re-samples now; the tests of one that doesn't use
# wan22_vace with its `upscale` block taken away (targets are cached by folder)
PLAIN = "wan22_vace"


def without_resample(case: unittest.TestCase, target_id: str = PLAIN) -> None:
    spec = TG.load_target(target_id, "video").spec
    block = spec.pop("upscale")
    case.addCleanup(spec.__setitem__, "upscale", block)


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
        without_resample(self)
        wan = TG.load_target(PLAIN, "video")                  # no re-sample of its own
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
        without_resample(self)
        wan = self.as_target(t, PLAIN)                     # no re-sample of its own
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
        without_resample(self)
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
        self.assertIsNone(opts["latent"][PLAIN])
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

    def test_deliver(self):
        """A delivery size: the last step that can make any size is sized to cover
        (crop) or fit inside (pad) it, aspect kept; the saver crops or pads."""
        self.assertEqual(U.parse_deliver("4K"), (3840, 2160))
        self.assertEqual(U.parse_deliver("2048x1080"), (2048, 1080))
        self.assertIsNone(U.parse_deliver(None))
        for bad in ("huge", "1921x1080", "10x10"):
            with self.assertRaises(U.UpscaleError):
                U.parse_deliver(bad)
        import h3_upscale as UN                      # the node's copy agrees
        for case in ((1344, 768, 3840, 2160, "crop"), (960, 544, 1920, 1080, "pad"),
                     (1024, 576, 3840, 2160, "crop"), (832, 480, 2560, 1440, "pad")):
            self.assertEqual(U.fit_size(*case), UN.fit_size(*case))
        t = self.final_take()
        T.update_sidecar(t.paths.sidecar, width=1344, height=768)
        t = T.get_take(self.ep, "final", "sh010", 1)
        # re-sample 2x, then the model to 4K: cover, then crop
        up = U.plan_upscale(self.ep, t, then_model="RealESRGAN_x2.pth", deliver="4k")
        self.assertEqual((up.action, (up.width, up.height), up.made_size, up.out_size),
                         ("upscale", (2688, 1536), (3840, 2196), (3840, 2160)))
        self.assertEqual(up.then_scale, round(3840 / 2688, 4))
        g = U.upscale_graph(self.base(), up)
        self.assertEqual((g["up_then"]["inputs"]["width"], g["up_then"]["inputs"]["height"]), (3840, 2196))
        si = g["up_save"]["inputs"]
        self.assertEqual((si["width"], si["height"], si["fit"]), (3840, 2160, "crop"))
        rec = U.queued_record(up)
        self.assertEqual((rec["width"], rec["height"]), (3840, 2160))
        self.assertEqual(rec["deliver"], {"width": 3840, "height": 2160, "fit": "crop", "made": [3840, 2196]})
        self.assertIn("-> 3840x2160 (cropped)", U.describe(up))
        # padded instead: fits inside
        up = U.plan_upscale(self.ep, t, then_model="RealESRGAN_x2.pth", deliver="4k", fit="pad")
        self.assertEqual(up.made_size, (3780, 2160))
        # the pixel method: its scale worked out from the take
        up = U.plan_upscale(self.ep, t, method="pixel", deliver="1080p", scale=4)
        self.assertEqual((up.scale, up.width, up.height), (round(1920 / 1344, 4), 1920, 1098))
        g = U.graph_of(up, {}, "")
        self.assertEqual((g["up_pixels"]["inputs"]["width"], g["up_save"]["inputs"]["height"]), (1920, 1080))
        # a re-sample alone keeps its scale; the saver resizes, with a note
        up = U.plan_upscale(self.ep, t, deliver="4k")
        self.assertEqual((up.width, up.out_size), (2688, (3840, 2160)))
        self.assertTrue(any("stretched" in n for n in up.notes))
        self.assertEqual(U.plan_upscale(self.ep, t, deliver="1080p").notes, [])
        # what can't be: a pixel step that would shrink, a bad fit
        self.assertIn("needs no upscale model", U.plan_upscale(
            self.ep, t, then_model="RealESRGAN_x2.pth", deliver="1080p").why)
        self.assertEqual(U.plan_upscale(self.ep, t, method="pixel", deliver="1280x720").action, "error")
        self.assertEqual(U.plan_upscale(self.ep, t, deliver="4k", fit="stretch").action, "error")
        # the route and the options
        self.err(A.post_upscale(self.ctx, {"ep": self.ep, "shots": ["sh010"], "deliver": "big"}), 400)
        self.err(A.post_upscale(self.ctx, {"ep": self.ep, "shots": ["sh010"], "fit": "zoom"}), 400)
        res = self.ok(A.post_upscale(self.ctx, {"ep": self.ep, "shots": ["sh010"], "redo": True,
                                                "method": "pixel", "deliver": "1440p", "fit": "pad"}))
        self.assertEqual((res["queued"][0]["width"], res["queued"][0]["height"]), (2560, 1440))
        opts = self.ok(A.get_upscale_options(self.ctx, {}))
        self.assertEqual(opts["fits"], ["crop", "pad"])
        self.assertIn({"id": "4k", "width": 3840, "height": 2160}, opts["delivers"])

    def test_then_seedvr2(self):
        """Re-sample, then SeedVR2 in the same job (to an output size here)."""
        t = self.final_take()
        T.update_sidecar(t.paths.sidecar, width=1344, height=768)
        t = T.get_take(self.ep, "final", "sh010", 1)
        up = U.plan_upscale(self.ep, t, then_method="seedvr2", seedvr2_model="3b", deliver="4k")
        self.assertEqual((up.action, up.then_method, up.then_model, up.made_size),
                         ("upscale", "seedvr2", "seedvr2_3b_int8_convrot.safetensors", (3840, 2196)))
        g = U.upscale_graph(self.base(), up)
        decode = self.by_class(g, "VAEDecode")[0]
        self.assertEqual(g["up_resize"]["inputs"]["image"], [decode, 0])
        self.assertEqual((g["up_resize"]["inputs"]["width"], g["up_resize"]["inputs"]["height"]), (3840, 2196))
        self.assertEqual(g["up_unet"]["inputs"]["unet_name"], "seedvr2_3b_int8_convrot.safetensors")
        self.assertEqual(g["up_finish"]["inputs"]["source"], [decode, 0])
        self.assertEqual(g["up_save"]["inputs"]["images"], ["up_finish", 0])
        self.assertFalse(self.by_class(g, "H3PixelUpscale"))
        self.assertTrue(self.by_class(g, "H3HoldAudio"))                  # still a re-sample first
        rec = U.queued_record(up)
        self.assertEqual((rec["then_pixel"]["method"], rec["width"], rec["height"]), ("seedvr2", 3840, 2160))
        self.assertIn("then SeedVR2 (seedvr2_3b_int8_convrot.safetensors)", U.describe(up))
        # readiness asks for SeedVR2's files, not an upscale model
        missing = U.not_ready([up], {c: {"input": {}} for c in U.UPSCALE_NODES})
        self.assertTrue(any(m.startswith("SeedVR2:") for m in missing))
        self.assertFalse(any(m.startswith("pixel:") for m in missing))
        self.assertEqual(U.plan_upscale(self.ep, t, then_method="sharp").action, "error")
        self.err(A.post_upscale(self.ctx, {"ep": self.ep, "shots": ["sh010"], "then_method": "x"}), 400)

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

    def test_fl2va_resample(self):
        """H3 FL2V re-samples as Ref2VA does; its size goes to the I2V node, which
        stretches the keyframes to it (they and the anchor are conditioning)."""
        t = self.as_target(self.final_take(), "minimax_h3_fl2va")
        T.update_sidecar(t.paths.sidecar, width=1344, height=768, steps=8,
                         inputs={"first": "h3pipe/sh010_first.png", "audio": "h3pipe/sh010_line.wav"})
        t = T.get_take(self.ep, "final", "sh010", 1)
        up = U.plan_upscale(self.ep, t)
        self.assertEqual((up.action, up.method, up.route, up.start_step), ("upscale", "latent", "latent", 7))
        self.assertEqual((up.width, up.height), (2688, 1536))
        g = U.upscale_graph(self.wan_base("minimax_h3_fl2va"), up)
        samplers = self.by_class(g, "SamplerCustomAdvanced")
        self.assertEqual(len(samplers), 1)
        si = g[samplers[0]]["inputs"]
        self.assertEqual(si["latent_image"], ["up_hold", 0])
        self.assertEqual(g[si["sigmas"][0]]["class_type"], "SplitSigmas")
        self.assertEqual(g[si["sigmas"][0]]["inputs"]["step"], 7)
        i2v = g[self.by_class(g, "MiniMaxH3ImageToVideo")[0]]["inputs"]
        self.assertEqual((i2v["width"], i2v["height"]), (2688, 1536))
        self.assertEqual(g[i2v["first_frame"][0]]["inputs"]["image"], "h3pipe/sh010_first.png")
        self.assertTrue(self.by_class(g, "MiniMaxH3AddGuide"))              # the anchor, kept
        self.assertEqual(g["up_scale"]["class_type"], "MinimaxH3LatentUpscaler3D")
        self.assertEqual(g["up_scale"]["inputs"]["mode.scale"], 2.0)
        self.assertEqual(g["up_source"]["class_type"], "H3LoadTakeLatent")
        info = {c: {"input": {}} for c in U.UPSCALE_NODES}
        info["MinimaxH3LatentUpscaler3D"] = UPSCALER
        self.assertEqual(U.upscale_readiness(up.target, info)["status"], "ready")

    def test_vace_refine_keeps_its_reference_in_front(self):
        """VACE refines as Wan I2V does, its latent led by the reference picture's
        frame (trim_latent cuts it off after sampling), sized as the node sizes it."""
        t = self.as_target(self.final_take(latent=False), "wan22_vace")
        T.update_sidecar(t.paths.sidecar, width=832, height=480, steps=4,
                         inputs={"reference": "h3pipe/sh010_ref.png"})
        t = T.get_take(self.ep, "final", "sh010", 1)
        up = U.plan_upscale(self.ep, t)
        self.assertEqual((up.action, up.route, up.start_step, up.width, up.height),
                         ("upscale", "vae", 3, 1664, 960))
        g = U.upscale_graph(self.wan_base("wan22_vace"), up)
        samplers = self.by_class(g, "KSamplerAdvanced")
        self.assertEqual(len(samplers), 1)                                   # the high expert is gone
        si = g[samplers[0]]["inputs"]
        self.assertEqual((si["start_at_step"], si["latent_image"]), (3, ["up_with_ref", 0]))
        cat = g["up_with_ref"]["inputs"]
        self.assertEqual((cat["samples1"], cat["samples2"], cat["dim"]), (["up_ref_enc", 0], ["up_venc", 0], "t"))
        ref = g["up_ref"]["inputs"]
        self.assertEqual((ref["width"], ref["height"], ref["upscale_method"], ref["crop"]), (1664, 960, "bilinear", "center"))
        vace = self.by_class(g, "WanVaceToVideo")[0]
        self.assertEqual(ref["image"], g[vace]["inputs"]["reference_image"])
        self.assertEqual(g[ref["image"][0]]["inputs"]["image"], "h3pipe/sh010_ref.png")
        self.assertEqual((g[vace]["inputs"]["width"], g[vace]["inputs"]["height"]), (1664, 960))
        self.assertTrue(self.by_class(g, "TrimVideoLatent"))
        info = {c: {"input": {}} for c in ("H3LoadTakeVideo", "H3PixelUpscale", "UpscaleModelLoader",
                                           "VAEEncode", "H3SaveUpscale", "ImageScale")}
        r = U.upscale_readiness(up.target, info)
        self.assertEqual(r["status"], "not_ready")
        self.assertIn("LatentConcat", r["missing"][0])

    def test_ltx_ingredients_resample(self):
        """LTX-2.3 ingredients: the take's video (its guide frames cut off) through
        LTX's 2x latent upsampler into the graph's own guide node, which appends
        the sheet again at the new size; the KSampler started late."""
        t = self.as_target(self.final_take(), "ltx2_ingredients")
        T.update_sidecar(t.paths.sidecar, width=768, height=448, steps=8, length=121, seed=41,
                         inputs={"sheet": "h3pipe/sh010_sheet.png"})
        t = T.get_take(self.ep, "final", "sh010", 1)
        up = U.plan_upscale(self.ep, t)
        self.assertEqual((up.action, up.route, up.start_step, up.width, up.height),
                         ("upscale", "latent", 7, 1536, 896))
        self.assertEqual(U.plan_upscale(self.ep, t, scale=1.5).action, "error")   # fixed 2x
        g = U.upscale_graph(self.wan_base("ltx2_ingredients"), up)
        self.assertFalse(self.by_class(g, "KSampler"))
        self.assertFalse(self.by_class(g, "EmptyLTXVLatentVideo"))
        ks = self.by_class(g, "KSamplerAdvanced")
        self.assertEqual(len(ks), 1)
        si = g[ks[0]]["inputs"]
        self.assertEqual((si["start_at_step"], si["add_noise"], si["latent_image"]), (7, "enable", ["up_hold", 0]))
        self.assertNotIn("denoise", si)
        self.assertIn("noise_seed", si)
        join = g["up_hold"]["inputs"]["latent"][0]
        self.assertEqual(g[join]["class_type"], "LTXVConcatAVLatent")
        self.assertEqual(g[join]["inputs"]["audio_latent"], ["up_split_av", 1])     # the take's audio
        guide = g[join]["inputs"]["video_latent"][0]
        self.assertEqual(g[guide]["class_type"], "LTXVAddGuide")
        self.assertEqual(g[guide]["inputs"]["latent"], ["up_scale", 0])
        self.assertEqual(g[g[guide]["inputs"]["image"][0]]["inputs"]["target_width"], 1536)
        ckpt = self.by_class(g, "CheckpointLoaderSimple")[0]
        ups = g["up_scale"]["inputs"]
        self.assertEqual(g["up_scale"]["class_type"], "LTXVLatentUpsampler")
        self.assertEqual((ups["samples"], ups["upscale_model"], ups["vae"]),
                         (["up_trim", 0], ["up_scale_model", 0], [ckpt, 2]))
        self.assertEqual(g["up_scale_model"]["inputs"]["model_name"], "ltx-2.3-spatial-upscaler-x2-1.1.safetensors")
        self.assertEqual((g["up_trim"]["inputs"]["dim"], g["up_trim"]["inputs"]["amount"]), ("t", 16))
        self.assertTrue(self.by_class(g, "LTXVCropGuides"))
        loads = [g[n]["inputs"]["image"] for n in self.by_class(g, "LoadImage")]
        self.assertEqual(loads, ["h3pipe/sh010_sheet.png"])
        # through the VAE: the checkpoint's VAE and LTX's audio encoder, nothing to trim
        os.remove(t.paths.latent)
        g = U.upscale_graph(self.wan_base("ltx2_ingredients"), U.plan_upscale(self.ep, t))
        self.assertNotIn("up_trim", g)
        self.assertEqual(g["up_venc"]["inputs"]["vae"], [ckpt, 2])
        self.assertEqual(g["up_aenc"]["class_type"], "LTXVAudioVAEEncode")
        self.assertEqual(g[g["up_aenc"]["inputs"]["audio_vae"][0]]["class_type"], "LTXVAudioVAELoader")
        # the options say it's fixed at 2x; readiness asks for the upsampler's file
        opts = self.ok(A.get_upscale_options(self.ctx, {}))
        self.assertEqual(opts["latent"]["ltx2_ingredients"]["fixed_scale"], 2.0)
        info = {c: {"input": {}} for c in U.UPSCALE_NODES + ("LTXVAudioVAEEncode", "LTXVLatentUpsampler",
                                                             "LatentCut", "KSamplerAdvanced")}
        info["LatentUpscaleModelLoader"] = {"input": {"required": {"model_name": [["other.safetensors"], {}]}}}
        r = U.upscale_readiness(up.target, info)
        self.assertEqual(r["status"], "not_ready")
        self.assertIn("ltx-2.3-spatial-upscaler-x2-1.1.safetensors", r["missing"][0])

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

    def sv2_info(self, models=("seedvr2_7b_int8_convrot.safetensors", "seedvr2_3b_int8_convrot.safetensors")):
        info = {c: {"input": {}} for c in U.SEEDVR2_NODES}
        info["UNETLoader"] = {"input": {"required": {"unet_name": [list(models), {}]}}}
        info["VAELoader"] = {"input": {"required": {"vae_name": [[U.SEEDVR2_VAE], {}]}}}
        return info

    def test_seedvr2_any_target(self):
        t = self.as_target(self.final_take(latent=False), "wan22_vace")      # no re-sample of its own
        up = U.plan_upscale(self.ep, t, method="seedvr2", grain=0.02)
        self.assertEqual((up.action, up.method, up.route, up.seedvr2_model),
                         ("upscale", "seedvr2", "seedvr2", "seedvr2_7b_int8_convrot.safetensors"))
        self.assertEqual(U.plan_upscale(self.ep, t, method="seedvr2", seedvr2_model="3b").seedvr2_model,
                         "seedvr2_3b_int8_convrot.safetensors")
        g = U.seedvr2_graph(up)
        self.assertEqual((g["up_resize"]["inputs"]["width"], g["up_resize"]["inputs"]["height"]), (up.width, up.height))
        self.assertEqual((g["up_ks"]["inputs"]["steps"], g["up_ks"]["inputs"]["latent_image"]), (1, ["up_chunk", 0]))
        self.assertEqual(g["up_chunk"]["inputs"]["chunking_mode"], "auto")
        self.assertEqual(g["up_merge"]["inputs"]["temporal_overlap"], ["up_chunk", 1])
        self.assertEqual(g["up_post"]["inputs"]["color_correction_method"], "lab")
        fin = g["up_finish"]["inputs"]
        self.assertEqual((fin["images"], fin["source"], fin["frequency_split"], fin["grain"]),
                         (["up_post", 0], ["up_video", 0], True, 0.02))
        self.assertEqual(g["up_save"]["inputs"]["images"], ["up_finish", 0])
        rec = U.queued_record(up)
        self.assertEqual((rec["method"], rec["seedvr2_model"], rec["color_correction"]),
                         ("seedvr2", "seedvr2_7b_int8_convrot.safetensors", "lab"))
        self.assertIn("SeedVR2", U.describe(up))
        # on top of an upscale, like the pixel method
        self.assertEqual(U.plan_upscale(self.ep, t, method="seedvr2", from_upscale=True).action, "error")

    def test_seedvr2_readiness_route_and_options(self):
        self.final_take()                                    # rendered before the lists change
        self.comfy.nodes |= set(U.SEEDVR2_NODES)
        self.comfy.info.update({k: v for k, v in self.sv2_info().items() if k in ("UNETLoader", "VAELoader")})
        self.assertEqual(U.seedvr2_readiness(self.sv2_info())["status"], "ready")
        r = U.seedvr2_readiness(self.sv2_info(models=("seedvr2_3b_int8_convrot.safetensors",)))
        self.assertEqual(r["status"], "not_ready")                          # the default 7b missing
        self.assertEqual(r["default"], "seedvr2_3b_int8_convrot.safetensors")
        self.assertEqual(U.seedvr2_readiness(self.sv2_info(), "3b")["status"], "ready")
        res = self.ok(A.post_upscale(self.ctx, {"ep": self.ep, "shots": ["sh010"], "method": "seedvr2",
                                                "seedvr2_model": "3b"}))
        q = res["queued"][0]
        self.assertEqual((q["method"], q["seedvr2_model"]), ("seedvr2", "seedvr2_3b_int8_convrot.safetensors"))
        self.assertEqual(self.status_take()["upscale"]["seedvr2_model"], "seedvr2_3b_int8_convrot.safetensors")
        opts = self.ok(A.get_upscale_options(self.ctx, {}))
        self.assertEqual(opts["seedvr2"]["status"], "ready")
        self.assertIn("seedvr2_7b_int8_convrot.safetensors", opts["seedvr2"]["models"])
        msg = self.err(A.post_upscale(self.ctx, {"ep": self.ep, "shots": ["sh010"], "method": "seedvr2",
                                                 "seedvr2_model": "gone.safetensors", "redo": True}), 409)
        self.assertIn("gone.safetensors", msg)

    def test_default_pixel_model(self):
        self.assertEqual(U.default_pixel_model(["4x-UltraSharp.pth", "RealESRGAN_x2.pth"]), "RealESRGAN_x2.pth")
        self.assertEqual(U.default_pixel_model(["4x-UltraSharp.pth", "2x-Other.pth"]), "2x-Other.pth")
        self.assertEqual(U.default_pixel_model(["4x-UltraSharp.pth"]), "4x-UltraSharp.pth")
        self.assertEqual(U.pixel_readiness({})["status"], "not_ready")      # no nodes, no models


RECIPE = {"deliver": "4k", "fit": "crop", "quality": "master",
          "finish": {"frequency_split": True, "grain": 0.02},
          "targets": {"minimax_h3_*": {"method": "latent", "then": "RealESRGAN_x2.pth"},
                      "minimax_h3_fl2va": {"method": "pixel"},
                      "wan22_*": {"method": "seedvr2", "seedvr2_model": "3b"},
                      "*": {"method": "pixel", "pixel_model": "4x-UltraSharp.pth"}}}


class RecipeTest(ApiTest):
    """13e1: the master recipe (series.json upscale.master) and per-shot overrides."""

    final_take = UpscaleTest.final_take
    as_target = PixelUpscaleTest.as_target

    def setUp(self):
        super().setUp()
        self.comfy.nodes |= set(U.UPSCALE_NODES) | set(U.PIXEL_NODES)
        self.comfy.info["MinimaxH3LatentUpscaler3D"] = UPSCALER
        self.comfy.info["UpscaleModelLoader"] = {"input": {"required": {
            "model_name": [["4x-UltraSharp.pth", "RealESRGAN_x2.pth", "RealESRGAN_x4.pth"], {}]}}}

    def set_recipe(self, recipe):
        p = os.path.join(self.ep, "series.json")
        cfg = json.load(open(p, encoding="utf-8"))
        cfg.setdefault("upscale", {})["master"] = recipe
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh)

    def test_sections_and_resolution(self):
        self.assertEqual(U.recipe_section(RECIPE, "minimax_h3_fl2va")[0], "minimax_h3_fl2va")   # exact
        self.assertEqual(U.recipe_section(RECIPE, "minimax_h3_ref2va")[0], "minimax_h3_*")     # glob
        self.assertEqual(U.recipe_section(RECIPE, "ltx2")[0], "*")                             # the rest
        self.assertEqual(U.recipe_section({"targets": {"wan*": {}}}, "ltx2"), (None, {}))
        t = self.final_take()
        with self.assertRaises(U.UpscaleError):
            U.recipe_for(self.ep, t)                                        # no recipe yet
        self.set_recipe(RECIPE)
        kw = U.recipe_for(self.ep, t)
        self.assertEqual(kw, {"method": "latent", "then_model": "RealESRGAN_x2.pth",
                              "frequency_split": True, "grain": 0.02,
                              "deliver": "4k", "fit": "crop", "quality": "master"})
        # a shot's override merges over its target's section
        U.set_shot_recipe(self.ep, "sh010", {"detail": 1, "then": "seedvr2"})
        kw = U.recipe_for(self.ep, t)
        self.assertEqual((kw["detail"], kw["then_method"], "then_model" in kw), (1, "seedvr2", False))
        self.assertEqual(U.shot_recipes(self.ep), {"sh010": {"detail": 1, "then": "seedvr2"}})
        U.set_shot_recipe(self.ep, "sh010", None)
        self.assertNotIn("upscale", T.load_overrides(self.ep))
        with self.assertRaises(U.UpscaleError):
            U.set_shot_recipe(self.ep, "sh010", {"colour": "warm"})
        # the Wan section
        wan = self.as_target(t, "wan22_vace")
        up = U.plan_upscale(self.ep, wan, **U.recipe_for(self.ep, wan))
        self.assertEqual((up.method, up.seedvr2_model, up.out_size),
                         ("seedvr2", "seedvr2_3b_int8_convrot.safetensors", (3840, 2160)))
        self.assertEqual(U.describe_recipe(RECIPE["targets"]["minimax_h3_*"], RECIPE),
                         "re-sample 2x, then RealESRGAN_x2.pth → 4k (crop)")

    def test_check(self):
        self.assertEqual(U.check_recipe(RECIPE), [])
        bad = U.check_recipe({"deliver": "huge", "fit": "zoom", "size": 1,
                              "targets": {"*": {"method": "magic", "detail": 5, "scale": 9,
                                                "then": "x.pth"}}})
        text = "\n".join(bad)
        for want in ("size: not a recipe field", "deliver 'huge'", "fit 'zoom'", "method 'magic'",
                     "detail 5", "scale 9", "then: only after a re-sample"):
            self.assertIn(want, text)
        self.assertTrue(U.check_recipe({"deliver": "4k"}))                   # no targets
        # the build says so too
        cfg = load_series_config(os.path.join(KITCHEN, "series.json"))
        with open(os.path.join(KITCHEN, "script.md"), encoding="utf-8") as fh:
            story = parse_story(fh.read(), subject_ids(cfg), character_ids(cfg),
                                series_info(cfg), variant_of(cfg))
        cfg["upscale"] = {"master": RECIPE}
        self.assertFalse([w for w in h3build.story_warnings(story, cfg) if "upscale.master" in w])
        cfg["upscale"] = {"master": {"targets": {"*": {"method": "magic"}}}}
        self.assertTrue([w for w in h3build.story_warnings(story, cfg) if "method 'magic'" in w])

    def test_routes(self):
        t = self.final_take()
        opts = self.ok(A.get_upscale_options(self.ctx, {"ep": self.ep}))
        self.assertIsNone(opts["recipe"])
        self.err(A.post_upscale(self.ctx, {"ep": self.ep, "shots": ["sh010"], "recipe": True}), 409)
        self.set_recipe(RECIPE)
        opts = self.ok(A.get_upscale_options(self.ctx, {"ep": self.ep}))
        r = opts["recipe"]
        self.assertEqual((r["deliver"], r["fit"], r["quality"], r["problems"]), ("4k", "crop", "master", []))
        self.assertEqual(r["targets"]["minimax_h3_ref2va"]["key"], "minimax_h3_*")
        self.assertIn("SeedVR2 3b", r["targets"]["wan22_i2v"]["text"])
        # a shot's recipe from the dialog's fields
        res = self.ok(A.put_upscale_recipe(self.ctx, {"ep": self.ep, "shot": "sh010", "recipe": {
            "method": "latent", "detail": 1, "then_pixel_model": "4x-UltraSharp.pth", "grain": 0}}))
        self.assertEqual(res["recipe"], {"method": "latent", "detail": 1, "grain": 0, "then": "4x-UltraSharp.pth"})
        self.assertEqual(self.ok(A.get_upscale_options(self.ctx, {"ep": self.ep}))["recipe"]["shots"]["sh010"]["fields"]["detail"], 1)
        self.err(A.put_upscale_recipe(self.ctx, {"ep": self.ep, "shot": "sh010", "recipe": {"detail": 7}}), 400)
        # queued by the recipe: the shot's override over its target's section, the master's size
        res = self.ok(A.post_upscale(self.ctx, {"ep": self.ep, "shots": ["sh010"], "recipe": True,
                                                "method": "pixel", "scale": 3}))   # ignored
        q = res["queued"][0]
        self.assertEqual((q["method"], q["then_pixel_model"], q["width"], q["height"]),
                         ("latent", "4x-UltraSharp.pth", 3840, 2160))
        rec = T.upscale_of(T.get_take(self.ep, "final", "sh010", 1))
        self.assertEqual(rec["deliver"]["width"], 3840)
        self.ok(A.put_upscale_recipe(self.ctx, {"ep": self.ep, "shot": "sh010", "recipe": None}))
        self.assertEqual(U.shot_recipes(self.ep), {})
        # a target the recipe doesn't cover: that take's error, the rest go on
        self.set_recipe({"targets": {"wan22_*": {"method": "pixel"}}})
        res = self.ok(A.post_upscale(self.ctx, {"ep": self.ep, "shots": ["sh010"], "recipe": True, "redo": True}))
        self.assertIn("no section for minimax_h3_ref2va", res["errors"][0]["error"])

    def summary(self, shot="sh010", n=1):
        data = self.ok(A.get_episode(self.ctx, {"ep": self.ep, "pass": "final"}))
        s = next(x for x in data["shots"] if x["shot"] == shot)
        return next(t for t in s["takes"] if t["take"] == n)["upscale"]

    def test_upscales_remember_their_recipe(self):
        """13e2: an upscale records its settings; against the recipe it is same,
        different (the recipe changed) or unknown (from before); never stale for it."""
        t = self.final_take()
        self.ok(A.post_upscale(self.ctx, {"ep": self.ep, "shots": ["sh010"], "method": "pixel"}))
        t = T.get_take(self.ep, "final", "sh010", 1)
        rec = T.upscale_of(t)
        self.assertEqual(rec["recipe"]["method"], "pixel")
        self.assertEqual(len(rec["recipe_hash"]), 12)
        self.assertIsNone(self.summary()["recipe_match"])                    # no recipe
        self.set_recipe(RECIPE)
        self.assertEqual(self.summary()["recipe_match"], "different")        # the recipe re-samples
        self.ok(A.post_upscale(self.ctx, {"ep": self.ep, "shots": ["sh010"], "recipe": True, "redo": True}))
        up = self.summary()
        self.assertEqual((up["recipe_match"], up["fresh"]), ("same", True))
        # the recipe changes: different, still fresh
        self.set_recipe({**RECIPE, "fit": "pad"})
        up = self.summary()
        self.assertEqual((up["recipe_match"], up["fresh"]), ("different", True))
        # so does a shot's own recipe
        self.set_recipe(RECIPE)
        U.set_shot_recipe(self.ep, "sh010", {"detail": 1})
        self.assertEqual(self.summary()["recipe_match"], "different")
        U.set_shot_recipe(self.ep, "sh010", None)
        # an upscale from before 13e2
        T.write_json(t.paths.up_sidecar, {k: v for k, v in T.read_json(t.paths.up_sidecar).items()
                                          if k not in ("recipe", "recipe_hash")})
        self.assertEqual(self.summary()["recipe_match"], "unknown")

    def test_master_quality(self):
        """13e3: the recipe's quality reaches the saver and the record; a review-quality
        upscale isn't the master recipe's."""
        t = self.final_take()
        self.set_recipe(RECIPE)
        up = U.plan_upscale(self.ep, t, **U.recipe_for(self.ep, t))
        self.assertEqual(up.quality, "master")
        g = U.graph_of(up, {}, "")
        self.assertEqual(g["up_save"]["inputs"]["quality"], "master")
        self.assertEqual(U.queued_record(up)["recipe"]["quality"], "master")
        review = U.plan_upscale(self.ep, t, method="pixel")
        self.assertNotIn("quality", U.graph_of(review, {}, "")["up_save"]["inputs"])
        self.assertEqual(U.plan_upscale(self.ep, t, quality="best").action, "error")
        self.err(A.post_upscale(self.ctx, {"ep": self.ep, "shots": ["sh010"], "quality": "best"}), 400)

    def test_keep(self):
        t = self.final_take()
        self.err(A.put_upscale_keep(self.ctx, {"ep": self.ep, "shot": "sh010", "take": 1, "keep": True}), 409)
        self.ok(A.post_upscale(self.ctx, {"ep": self.ep, "shots": ["sh010"], "method": "pixel"}))
        self.ok(A.put_upscale_keep(self.ctx, {"ep": self.ep, "shot": "sh010", "take": 1, "keep": True}))
        self.assertTrue(self.summary()["keep"])
        t = T.get_take(self.ep, "final", "sh010", 1)
        # the whole cut redone: a Keep is left alone; naming the shot redoes it
        res = self.ok(A.post_upscale(self.ctx, {"ep": self.ep, "shots": None, "redo": True, "method": "pixel"}))
        self.assertIn("kept", next(s["reason"] for s in res["skipped"] if s["shot"] == "sh010"))
        self.assertEqual(U.plan_upscale(self.ep, t, redo=True, respect_keep=True).action, "skip")
        res = self.ok(A.post_upscale(self.ctx, {"ep": self.ep, "shots": ["sh010"], "redo": True, "method": "pixel"}))
        self.assertEqual(len(res["queued"]), 1)
        self.assertFalse(self.summary()["keep"])                              # a new upscale, unkept
        self.err(A.put_upscale_keep(self.ctx, {"ep": self.ep, "shot": "sh010", "take": 9, "keep": True}), 404)
        self.err(A.put_upscale_keep(self.ctx, {"ep": self.ep, "shot": "sh010", "take": 1, "keep": "yes"}), 400)


if __name__ == "__main__":
    unittest.main()
