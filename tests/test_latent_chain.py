"""
`continuous: latent` (docs/CONTINUOUS.md, phase b): the build's `hold` (the
overlap rounded up to H3's grid, the take keeping the end of the render), the
queued graph (H3ChainLatent before the sampler, H3ChainTrim before the saver),
the plan before a render (chains in cut order, waiting on the take ahead), and
the nodes' arithmetic on stand-in tensors.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "comfy_nodes"))

import h3jobs as J  # noqa: E402
import h3refs as R  # noqa: E402
import h3takes as T  # noqa: E402
import targets as TG  # noqa: E402
from targets.video.minimax_h3_ref2va import compile as C  # noqa: E402
from test_render import ENV, stub_refs  # noqa: E402

FIXTURE = os.path.join(HERE, "fixtures", "continuous")


def build(root: str) -> None:
    for flags in ([], ["--proxy"]):
        subprocess.run([sys.executable, os.path.join(ROOT, "h3build.py"),
                        os.path.join(FIXTURE, "series.json"),
                        os.path.join(FIXTURE, "script.md"), "-o", root, *flags],
                       check=True, capture_output=True, env=ENV)
    stub_refs(root)
    shutil.copy(os.path.join(FIXTURE, "series.json"), root)


class HoldTest(unittest.TestCase):
    def setUp(self):
        self.t = TG.load_target("minimax_h3_ref2va", "video")

    def test_overlap_rounds_up_onto_the_grid(self):
        for asked, held in ((None, 39), (39, 39), (30, 39), (22, 22), (23, 39), (1, 5),
                            (40, 56)):
            self.assertEqual(C.hold_frames(self.t, {}, asked), held, asked)

    def test_the_series_default_beats_the_targets(self):
        self.assertEqual(C.hold_frames(self.t, {"continuous": {"overlap": 56}}, None), 56)
        self.assertEqual(C.hold_frames(self.t, {"continuous": {"overlap": 56}}, 22), 22)


class ChainBuildTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = cls._tmp.name
        build(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def entry(self, sid: str, pass_: str = "final") -> tuple[dict, int]:
        return J.find_shot(self.root, pass_, sid)

    def test_a_latent_shot_holds_and_keeps_the_end(self):
        doc, i = self.entry("sh080")
        sh = doc["shots"][i]
        # 4.04 s asks 97 frames; with 39 held the render is 141 (17k+5), the take 102
        self.assertEqual((sh["hold"], sh["length"]), (39, 102))
        doc, i = self.entry("sh070")
        self.assertNotIn("hold", doc["shots"][i])      # the sequence's first shot
        self.assertEqual(J.shotlist_target(doc).id, "minimax_h3_ref2va")

    def test_the_graph_holds_then_trims(self):
        doc, i = self.entry("sh080")
        job = J.plan_job(self.root, "final", doc, i, J.RenderRequest("sh080"), {})
        base, _ = J.resolve_workflow(None, J.WORKFLOW_NAME, None, prefer_repo=True)
        take = J.start_job(job)
        g = J.graph_for(base, job, take)
        chain = [k for k, v in g.items() if v["class_type"] == "H3ChainLatent"]
        trim = [k for k, v in g.items() if v["class_type"] == "H3ChainTrim"]
        self.assertEqual((len(chain), len(trim)), (1, 1))
        c, t = g[chain[0]]["inputs"], g[trim[0]]["inputs"]
        sampler = J.node_of(g, "SamplerCustomAdvanced")
        self.assertEqual(g[sampler]["inputs"]["latent_image"], [chain[0], 0])
        self.assertEqual(g[c["latent"][0]]["class_type"], "MiniMaxH3ReferenceToVideo")
        self.assertEqual((c["shot"], c["pass_"], c["overlap"], c["hold_audio"]),
                         ("sh080", "final", 39, True))
        for vae in ("video_vae", "audio_vae"):
            self.assertEqual(g[c[vae][0]]["class_type"], "VAELoader", vae)
        self.assertNotEqual(c["video_vae"], c["audio_vae"])
        saver = g[J.node_of(g, "H3SaveShot")]["inputs"]
        self.assertEqual(saver["images"], [trim[0], 0])
        self.assertEqual(saver["audio"], [trim[0], 1])
        self.assertEqual(g[t["images"][0]]["class_type"], "VAEDecode")
        self.assertEqual(t["frames"], 39)
        # the latent the take keeps is the whole render: its tail is the take's
        self.assertEqual(saver["latent"], [sampler, 0])
        # what was held reaches the record through the saver, at the end
        self.assertEqual(saver["chain"], [chain[0], 1])
        self.assertNotIn("sidecar", c)
        sc = json.load(open(take.paths.sidecar, encoding="utf-8"))
        self.assertEqual((sc["hold"], sc["length"]), (39, 102))

    def test_a_plain_shot_is_untouched(self):
        doc, i = self.entry("sh070")
        job = J.plan_job(self.root, "final", doc, i, J.RenderRequest("sh070"), {})
        base, _ = J.resolve_workflow(None, J.WORKFLOW_NAME, None, prefer_repo=True)
        g = J.graph_for(base, job, J.start_job(job))
        self.assertFalse([v for v in g.values()
                          if v["class_type"] in ("H3ChainLatent", "H3ChainTrim")])

    def finished(self, sid: str, latent: bool = True) -> T.Take:
        doc, i = self.entry(sid)
        job = J.plan_job(self.root, "final", doc, i,
                         J.RenderRequest(sid, seed_mode="new"), {})
        take = J.start_job(job)
        open(take.paths.mp4, "wb").close()
        if latent:
            open(take.paths.latent, "wb").close()
        T.update_sidecar(take.paths.sidecar, status="ok", finished=T.now(),
                         **({"latent": os.path.basename(take.paths.latent)} if latent else {}))
        return T.get_take(self.root, "final", sid, take.take)

    def test_an_upscale_re_samples_the_whole_render_and_trims_it(self):
        import h3upscale as U
        up = U.plan_upscale(self.root, self.finished("sh080"))
        self.assertEqual((up.action, up.route), ("upscale", "latent"), up.why)
        base, _ = J.resolve_workflow(None, J.WORKFLOW_NAME, None, prefer_repo=True)
        g = U.upscale_graph(base, up)
        self.assertFalse([v for v in g.values() if v["class_type"] == "H3ChainLatent"])
        trim = [k for k, v in g.items() if v["class_type"] == "H3ChainTrim"]
        self.assertEqual(len(trim), 1)
        self.assertEqual(g["up_save"]["inputs"]["images"], [trim[0], 0])
        self.assertEqual(g[trim[0]]["inputs"]["frames"], 39)
        self.assertNotIn("audio", g[trim[0]]["inputs"])
        # its frames alone can't rebuild the render
        up = U.plan_upscale(self.root, self.finished("sh090", latent=False))
        self.assertEqual(up.action, "error")
        self.assertIn("kept latent", up.why)

    def test_before_a_render_a_chain_waits_or_says_why_not(self):
        s = R.load_series(self.root)
        # sh070 has no take: sh080 has nothing to continue
        out = R.refresh_continuity(s, "proxy", {"sh080"}, dry_run=True)
        self.assertNotIn("sh080", out["live"])
        self.assertTrue(any(e["shot"] == "sh080" and "continuous: latent" in e["error"]
                            for e in out["errors"]), out["errors"])
        # queued with sh070: it waits on sh070's new take, and queues behind it
        out = R.refresh_continuity(s, "proxy", {"sh070", "sh080", "sh090"}, dry_run=True)
        self.assertEqual(out["live"]["sh080"], {"after": "sh070", "pass": "proxy"})
        self.assertEqual(out["live"]["sh090"], {"after": "sh080", "pass": "proxy"})
        waits = {w["shot"]: w for w in out["wait"] if w.get("chain")}
        self.assertEqual(waits["sh080"]["after"], "sh070")
        self.assertFalse([e for e in out["errors"] if e["shot"] in ("sh080", "sh090")])


class _Episode(unittest.TestCase):
    """A fresh build of the fixture, and finished takes made by hand."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        build(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def take(self, sid: str, **sidecar) -> T.Take:
        doc, i = J.find_shot(self.root, "final", sid)
        job = J.plan_job(self.root, "final", doc, i, J.RenderRequest(sid, seed_mode="new"), {})
        t = J.start_job(job)
        with open(t.paths.mp4, "wb") as fh:
            fh.write(f"{sid} {t.take}".encode())
        T.update_sidecar(t.paths.sidecar, status="ok", finished=T.now(), **sidecar)
        return T.get_take(self.root, "final", sid, t.take)

    def stale(self, t: T.Take) -> list[str]:
        import h3edit as E
        doc, i = J.find_shot(self.root, "final", t.shot)
        return E.take_stale(self.root, "final", doc, doc["shots"][i], t.sidecar,
                            "minimax_h3_ref2va", {})



class ChainStaleTest(_Episode):
    """A latent take is stale `chain` when what the cut puts before it changed."""

    def test_another_take_or_a_re_render_before_it(self):
        a = self.take("sh070")
        held = {"shot": "sh070", "take": a.take, "pass": "final",
                "sha1": T.file_sha1(a.paths.mp4), "via": "latent", "overlap": 39}
        b = self.take("sh080", continued_from=held)
        self.assertNotIn("chain", self.stale(b))
        with open(a.paths.mp4, "ab") as fh:              # sh070 t01 rendered again
            fh.write(b" again")
        self.assertIn("chain", self.stale(b))
        b = self.take("sh080", continued_from=dict(held, sha1=T.file_sha1(a.paths.mp4)))
        self.assertNotIn("chain", self.stale(b))
        self.take("sh070")                               # a newer take: the cut uses it
        self.assertIn("chain", self.stale(b))


class ChainUpscaleTest(_Episode):
    """Phase c: a latent chain's upscales meet on one picture."""

    def chained(self):
        a = self.take("sh070")
        open(a.paths.latent, "wb").close()
        T.update_sidecar(a.paths.sidecar, latent=os.path.basename(a.paths.latent))
        held = {"shot": "sh070", "take": a.take, "pass": "final", "via": "latent"}
        b = self.take("sh080", continued_from=held)
        open(b.paths.latent, "wb").close()
        T.update_sidecar(b.paths.sidecar, latent=os.path.basename(b.paths.latent))
        return (T.get_take(self.root, "final", "sh070", a.take),
                T.get_take(self.root, "final", "sh080", b.take))

    def graph(self, up):
        import h3upscale as U
        base, _ = J.resolve_workflow(None, J.WORKFLOW_NAME, None, prefer_repo=True)
        return U.upscale_graph(base, up)

    def test_the_source_keeps_its_latent_and_the_chain_holds_it(self):
        import h3upscale as U
        a, b = self.chained()
        up_a = U.plan_upscale(self.root, a)
        self.assertEqual(up_a.action, "upscale", up_a.why)
        self.assertTrue(up_a.keep_latent)
        self.assertIsNone(up_a.held_from)
        save = self.graph(up_a)["up_save"]["inputs"]
        self.assertTrue(save["latent_file"].endswith("sh070_t01.up.latent.safetensors"))
        up_b = U.plan_upscale(self.root, b)
        self.assertEqual(up_b.action, "upscale", up_b.why)
        self.assertEqual((up_b.held_from.shot, up_b.held_from.take), ("sh070", a.take))
        self.assertTrue(up_b.keep_latent)                # sh090 holds sh080 in turn
        self.assertEqual(U.settings_of(up_b)["held_from"], "sh070 t01")
        self.assertTrue(any("upscaled tail" in n for n in up_b.notes), up_b.notes)
        g = self.graph(up_b)
        sampler = J.node_of(g, "SamplerCustomAdvanced")
        self.assertEqual(g[sampler]["inputs"]["latent_image"], ["up_chain", 0])
        c = g["up_chain"]["inputs"]
        self.assertEqual((c["overlap"], c["hold_audio"], c["missing_ok"]), (39, False, True))
        self.assertTrue(c["previous_latent"].endswith("sh070_t01.up.latent.safetensors"))
        self.assertEqual(g[c["latent"][0]]["class_type"], "H3HoldAudio")
        self.assertNotIn("sidecar", c)              # nothing writes the record mid-job
        self.assertEqual(g["up_save"]["inputs"]["chain_hold"], ["up_chain", 1])

    def test_a_plain_shot_keeps_nothing_and_holds_nothing(self):
        import h3upscale as U
        t = self.take("sh040")
        open(t.paths.latent, "wb").close()
        T.update_sidecar(t.paths.sidecar, latent=os.path.basename(t.paths.latent))
        up = U.plan_upscale(self.root, T.get_take(self.root, "final", "sh040", t.take))
        self.assertEqual(up.action, "upscale", up.why)
        self.assertFalse(up.keep_latent)
        self.assertIsNone(up.held_from)
        g = self.graph(up)
        self.assertNotIn("latent_file", g["up_save"]["inputs"])
        self.assertNotIn("up_chain", g)

    def test_master_queues_the_source_first(self):
        from types import SimpleNamespace
        import h3master as M

        def row(shot, src=None):
            take = SimpleNamespace(shot=shot, take=1)
            job = SimpleNamespace(take=take, first_from=None, held_from=src, method="latent",
                                  target=SimpleNamespace(id="minimax_h3_ref2va"),
                                  then_method="pixel", then_model="", seedvr2_model="",
                                  pixel_model="")
            return SimpleNamespace(job=job, shot=shot)
        a = row("sh070")
        b = row("sh080", src=a.job.take)
        self.assertEqual([r.shot for r in M.in_order([b, a], "target")], ["sh070", "sh080"])


try:
    import torch
    HAVE_TORCH = True
except Exception:                                        # pragma: no cover
    HAVE_TORCH = False


class _Nested:
    """Stands in for comfy.nested_tensor.NestedTensor (video, audio)."""
    is_nested = True

    def __init__(self, parts):
        self.parts = tuple(parts)

    def unbind(self):
        return self.parts


@unittest.skipUnless(HAVE_TORCH, "needs torch")
class ChainNodeTest(unittest.TestCase):
    def setUp(self):
        comfy = types.ModuleType("comfy")
        nt = types.ModuleType("comfy.nested_tensor")
        nt.NestedTensor = _Nested
        comfy.nested_tensor = nt
        self._saved = {k: sys.modules.get(k) for k in ("comfy", "comfy.nested_tensor")}
        sys.modules["comfy"], sys.modules["comfy.nested_tensor"] = comfy, nt
        import h3_chain
        self.N = h3_chain

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v

    def test_the_head_is_the_previous_tail_and_masked(self):
        # this shot: 141 frames = 42 video steps; the previous take: 124 = 37
        v_new, a_new = torch.zeros(1, 4, 42, 3, 5), torch.zeros(1, 8, 235)
        v_old = torch.arange(37, dtype=torch.float32).view(1, 1, 37, 1, 1).expand(1, 4, 37, 3, 5)
        a_old = torch.arange(207, dtype=torch.float32).view(1, 1, 207).expand(1, 8, 207)
        out, rec = self.N.chain({"samples": _Nested((v_new, a_new))}, v_old, a_old, 39, True)
        v, a = out["samples"].unbind()
        vm, am = out["noise_mask"].unbind()
        self.assertEqual(rec, {"overlap": 39, "video_steps": 12, "audio_steps": 65})
        self.assertTrue(torch.equal(v[0, 0, :12, 0, 0], torch.arange(25, 37, dtype=torch.float32)))
        self.assertEqual(float(v[:, :, 12:].abs().sum()), 0.0)
        self.assertEqual((float(vm[:, :, :12].sum()), float(vm[:, :, 12:].min())), (0.0, 1.0))
        self.assertTrue(torch.equal(a[0, 0, :65], torch.arange(142, 207, dtype=torch.float32)))
        self.assertEqual((float(am[..., :65].sum()), float(am[..., 65:].min())), (0.0, 1.0))
        self.assertEqual(float(v_new.abs().sum()), 0.0)    # the input isn't changed

    def test_video_only_and_a_size_mismatch(self):
        v_new, a_new = torch.zeros(1, 4, 42, 3, 5), torch.zeros(1, 8, 235)
        out, rec = self.N.chain({"samples": _Nested((v_new, a_new))},
                                torch.ones(1, 4, 37, 3, 5), None, 39, True)
        self.assertEqual(rec["audio_steps"], 0)
        self.assertEqual(float(out["noise_mask"].unbind()[1].min()), 1.0)
        with self.assertRaises(ValueError):
            self.N.chain({"samples": _Nested((v_new, a_new))}, torch.ones(1, 4, 37, 6, 10),
                         None, 39, True)

    def test_an_upscale_holds_or_says_why_not(self):
        v_new, a_new = torch.zeros(1, 4, 42, 6, 10), torch.zeros(1, 8, 235)
        with tempfile.TemporaryDirectory() as d:
            lat = {"samples": _Nested((v_new, a_new))}
            out, rec = self.N.upscale_hold(lat, d, "sh080", "a.up.latent.safetensors", 39,
                                           False, 24.0)
            self.assertIs(out, lat)
            self.assertEqual((rec["held"], rec["file"]), (False, "a.up.latent.safetensors"))
            open(os.path.join(d, "a.up.latent.safetensors"), "wb").close()
            from unittest import mock
            with mock.patch.object(self.N, "kept_latent",
                                   return_value=(torch.ones(1, 4, 37, 6, 10), None)):
                out, rec = self.N.upscale_hold(lat, d, "sh080", "a.up.latent.safetensors", 39,
                                               False, 24.0)
                node_out = self.N.H3ChainLatent().chain(lat, d, "sh080", "final", 39, False,
                                                        previous_latent="a.up.latent.safetensors",
                                                        missing_ok=True)
            self.assertEqual(float(out["samples"].unbind()[0][:, :, :12].mean()), 1.0)
            self.assertEqual((rec["held"], rec["video_steps"]), (True, 12))
            self.assertTrue(json.loads(node_out[1])["held"])
            self.assertEqual(os.listdir(d), ["a.up.latent.safetensors"])   # nothing written

    def test_the_trim_cuts_the_held_frames_and_their_sound(self):
        images = torch.arange(141, dtype=torch.float32).view(141, 1, 1, 1).expand(141, 2, 2, 3)
        audio = {"waveform": torch.zeros(1, 2, 141 * 2000), "sample_rate": 48000}
        imgs, aud = self.N.H3ChainTrim().trim(images, 39, 24.0, audio)
        self.assertEqual((imgs.shape[0], float(imgs[0, 0, 0, 0])), (102, 39.0))
        self.assertEqual(aud["waveform"].shape[-1], 141 * 2000 - 39 * 2000)
        self.assertIsNone(self.N.H3ChainTrim().trim(images, 39, 24.0)[1])


if __name__ == "__main__":
    unittest.main()
