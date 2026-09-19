"""
Model families (targets/modelid.py, targets.check_model, h3jobs.check_models,
GET /h3pipe/models): a model file is checked by name, then by the header of
its safetensors file. The headers here are synthetic (a header and no
weights: every tensor is zero bytes long), written per test; the real-file
check at the end runs only on a machine with the files installed.
"""
from __future__ import annotations

import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "comfy_nodes"))
import h3pipe_api as A  # noqa: E402
import h3edit as E  # noqa: E402
import h3jobs as J  # noqa: E402
import h3takes as T  # noqa: E402
import targets as TG  # noqa: E402
from targets import modelid as M  # noqa: E402
from test_api import ApiTest  # noqa: E402
from test_render import ENV, WORKFLOW, FakeComfy, build_episode  # noqa: E402

REAL = os.environ.get("H3PIPE_REAL_MODELS", r"C:\AI\ComfyUI\ComfyUI\models")


def write_st(path: str, tensors: dict, metadata: dict | None = None) -> str:
    """A .safetensors file with this header and no weights: {name: shape} or
    {name: (dtype, shape)}; every tensor is zero bytes long."""
    head = {}
    if metadata:
        head["__metadata__"] = {k: str(v) for k, v in metadata.items()}
    for k, v in tensors.items():
        dtype, shape = v if isinstance(v, tuple) else ("BF16", v)
        head[k] = {"dtype": dtype, "shape": list(shape), "data_offsets": [0, 0]}
    raw = json.dumps(head).encode()
    raw += b" " * (-len(raw) % 8)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(struct.pack("<Q", len(raw)) + raw)
    return path


# minimal headers, shaped like the installed files (docs/PLAN.md "Model families")
def h3_tensors() -> dict:
    return {"adaln_t_table": [1025, 8], "video_patch_proj.weight": [5376, 64],
            "audio_patch_proj.weight": [5376, 32], "condition_proj.weight": [5376, 5120],
            "token_refiner.final_norm.weight": [5120], "final_layer.video_out.weight": [64, 5376],
            "blocks.0.attn.qkv.weight": ("I8", [16128, 5376])}


def ltx_tensors(version: str, prefix: str = "model.diffusion_model.") -> dict:
    t = {"patchify_proj.weight": [4096, 128], "audio_patchify_proj.weight": [2048, 128],
         "adaln_single.linear.weight": [24576, 4096],
         "av_ca_a2v_gate_adaln_single.linear.weight": [4096, 4096],
         "transformer_blocks.0.audio_ff.net.0.proj.weight": ("I8", [8192, 2048])}
    if version == "2.5":
        t["keyframes_abs_pos_embedding"] = [1, 4096]
    else:
        t["transformer_blocks.0.ff.net.0.proj.bias"] = [16384]
    return {prefix + k: v for k, v in t.items()}


def wan14_tensors(in_ch: int = 36, vace: bool = False) -> dict:
    t = {"patch_embedding.weight": [5120, in_ch, 1, 2, 2], "head.modulation": [1, 2, 5120],
         "text_embedding.0.weight": ("F8_E4M3", [5120, 4096]),
         "blocks.39.ffn.0.weight": ("F8_E4M3", [13824, 5120])}
    if vace:
        t.update({"vace_patch_embedding.weight": [5120, 96, 1, 2, 2],
                  "vace_blocks.0.before_proj.weight": [5120, 5120]})
    return t


def krea_tensors() -> dict:
    return {"txtfusion.projector.weight": [1, 12], "txtfusion.refiner_blocks.0.attn.wq.weight":
            [6144, 6144], "first.weight": [6144, 64], "last.linear.weight": [64, 6144],
            "tmlp.0.weight": [6144, 256], "tproj.1.weight": [36864, 6144],
            "blocks.0.mod.lin": [36864]}


class HeaderTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.d = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def p(self, *names):
        return os.path.join(self.d, *names)

    def test_read_header(self):
        write_st(self.p("a.safetensors"), {"x.weight": ("F16", [2, 3])}, {"model_version": "2.5.0"})
        h = M.read_header(self.p("a.safetensors"))
        self.assertEqual(h["metadata"], {"model_version": "2.5.0"})
        self.assertEqual(h["tensors"], {"x.weight": {"dtype": "F16", "shape": [2, 3]}})
        with open(self.p("b.safetensors"), "wb") as fh:
            fh.write(b"GGUF\x03\x00\x00\x00" + b"\x00" * 64)
        with self.assertRaises(ValueError):
            M.read_header(self.p("b.safetensors"))
        with open(self.p("c.safetensors"), "wb") as fh:
            fh.write(struct.pack("<Q", 1000) + b"{}")
        with self.assertRaises(ValueError):                  # truncated
            M.read_header(self.p("c.safetensors"))
        r = M.identify(self.p("b.safetensors"))
        self.assertEqual((r["family"], r["confidence"]), (None, "unknown"))

    def test_h3_variants_need_the_name(self):
        for name, fam in (("minimax_h3_ref2va_int8.safetensors", "minimax-h3-ref2va"),
                          ("minimax_h3_fl2va_int8.safetensors", "minimax-h3-fl2va"),
                          ("my_h3_merge.safetensors", "minimax-h3")):
            r = M.identify(write_st(self.p(name), h3_tensors()))
            self.assertEqual(r["family"], fam, r)
            if fam == "minimax-h3":
                self.assertEqual(r["confidence"], "tensors")
                self.assertIn("same tensors", r["detail"])
            else:
                self.assertEqual((r["confidence"], r["base"], r["base_confidence"]),
                                 ("name", "minimax-h3", "tensors"))

    def test_ltx_versions_by_tensors_and_by_metadata(self):
        for v in ("2.3", "2.5"):
            r = M.identify(write_st(self.p(f"x{v}.safetensors"), ltx_tensors(v)))
            self.assertEqual((r["family"], r["confidence"]), (f"ltx{v}", "tensors"), r)
            # model_version says so too: metadata
            r = M.identify(write_st(self.p(f"y{v}.safetensors"), ltx_tensors(v),
                                    {"model_version": f"{v}.0"}))
            self.assertEqual((r["family"], r["confidence"]), (f"ltx{v}", "metadata"), r)
        # no prefix (a bare transformer) reads the same
        r = M.identify(write_st(self.p("bare.safetensors"), ltx_tensors("2.5", prefix="")))
        self.assertEqual(r["family"], "ltx2.5")
        # an rc version string
        r = M.identify(write_st(self.p("rc.safetensors"), ltx_tensors("2.3"),
                                {"model_version": "2.3.rc1"}))
        self.assertEqual((r["family"], r["confidence"]), ("ltx2.3", "metadata"))

    def test_wan(self):
        cases = {"wan2.2_i2v_high_noise_14B.safetensors": ("wan2.2-i2v-14b-high", "name"),
                 "wan2.2_i2v_low_noise_14B.safetensors": ("wan2.2-i2v-14b-low", "name"),
                 "renamed_expert.safetensors": ("wan2.2-i2v-14b", "tensors")}
        for name, want in cases.items():
            r = M.identify(write_st(self.p(name), wan14_tensors()))
            self.assertEqual((r["family"], r["confidence"]), want, r)
        r = M.identify(write_st(self.p("vace_low.safetensors"), wan14_tensors(16, vace=True)))
        self.assertEqual((r["family"], r["base"]), ("wan2.2-vace-14b-low", "wan2.2-vace-14b"))
        r = M.identify(write_st(self.p("ti2v.safetensors"), {
            "patch_embedding.weight": [3072, 48, 1, 2, 2], "head.modulation": [1, 2, 3072],
            "blocks.29.x.weight": [1]}))
        self.assertEqual(r["family"], "wan2.2-ti2v-5b")
        # Wan 2.1 I2V (img_emb) is not 2.2's: unknown rather than a wrong guess
        t = wan14_tensors()
        t["img_emb.proj.0.weight"] = [1280]
        self.assertIsNone(M.identify(write_st(self.p("w21.safetensors"), t))["family"])

    def test_krea2_and_quantized_repacks(self):
        r = M.identify(write_st(self.p("some_finetune.safetensors"), krea_tensors()))
        self.assertEqual((r["family"], r["confidence"]), ("krea2", "tensors"))
        # a ComfyUI checkpoint-style save: model.diffusion_model. prefix, int8 + scales
        t = {"model.diffusion_model." + k: v for k, v in krea_tensors().items()}
        t["model.diffusion_model.first.weight_scale"] = ("F32", [])
        r = M.identify(write_st(self.p("comfy_repack.safetensors"), t))
        self.assertEqual(r["family"], "krea2")

    def test_unknown_and_modelspec(self):
        r = M.identify(write_st(self.p("sdxl.safetensors"), {"x": [1]},
                                {"modelspec.architecture": "stable-diffusion-xl-v1-base"}))
        self.assertEqual((r["family"], r["confidence"]), (None, "unknown"))
        self.assertIn("stable-diffusion-xl", r["detail"])
        r = M.identify(write_st(self.p("k.safetensors"), {"x": [1]},
                                {"modelspec.architecture": "krea2"}))
        self.assertEqual((r["family"], r["confidence"]), ("krea2", "metadata"))
        # no signature, but the name matches exactly one family's patterns
        r = M.identify(self.p("sdxl.safetensors"), names={"sdxl": ["sdxl*"]})
        self.assertEqual((r["family"], r["confidence"]), ("sdxl", "name"))

    def test_relation_and_names(self):
        self.assertEqual(M.relation("ltx2.5", "ltx2.5"), "same")
        self.assertEqual(M.relation("ltx2.5", "ltx2"), "variant")
        self.assertEqual(M.relation("minimax-h3", "minimax-h3-ref2va"), "ambiguous")
        self.assertEqual(M.relation("minimax-h3-fl2va", "minimax-h3-ref2va"), "different")
        self.assertEqual(M.relation("ltx2.3", "ltx2.5"), "different")
        self.assertEqual(M.relation("ltx2.5", "somebody-elses-family"), "unregistered")
        self.assertEqual(M.relation(None, "ltx2.5"), "unregistered")
        self.assertTrue(M.name_matches("LTX-2.5-22B.safetensors", ["ltx-2.5*"]))
        self.assertTrue(M.name_matches("sub\\ltx-2.5-x.safetensors", ["ltx-2.5*"]))
        self.assertTrue(M.name_matches("sub/ltx-2.5-x.safetensors", ["sub/*"]))
        self.assertFalse(M.name_matches("ltx-2.3.safetensors", ["ltx-2.5*"]))
        self.assertFalse(M.name_matches("", ["*"]))

    def test_cache_by_path_size_mtime(self):
        path = write_st(self.p("m.safetensors"), krea_tensors())
        cache = M.ModelIdCache(self.p("cache", "modelid_cache.json"))
        self.assertEqual(M.identify(path, cache=cache)["family"], "krea2")
        self.assertTrue(os.path.isfile(cache.path))
        with mock.patch.object(M, "read_header", side_effect=AssertionError("read again")):
            self.assertEqual(M.identify(path, cache=cache)["family"], "krea2")
            # a fresh cache object reads the same file back
            again = M.ModelIdCache(cache.path)
            self.assertEqual(M.identify(path, cache=again)["family"], "krea2")
        # the file changes: its key changes, so it is read again
        write_st(path, h3_tensors())
        st = os.stat(path)
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))
        self.assertEqual(M.identify(path, cache=cache)["family"], "minimax-h3")
        # an unwritable cache path is no cache, not an error
        bad = M.ModelIdCache(os.path.join(path, "not", "a", "dir.json"))
        self.assertEqual(M.identify(path, cache=bad)["family"], "minimax-h3")


class CheckModelTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.models = os.path.join(self._tmp.name, "models")
        self.resolve = lambda folder, name: (
            p if os.path.isfile(p := os.path.join(self.models, folder, name)) else None)

    def tearDown(self):
        self._tmp.cleanup()

    def put(self, folder, name, tensors, meta=None):
        write_st(os.path.join(self.models, folder, name), tensors, meta)

    def check(self, tid, param, name, **kw):
        return TG.check_model(TG.load_target(tid), param, name, self.resolve, **kw)

    def test_targets_declare_their_families(self):
        # (the turbo / IC LoRA families and the files a workflow loads on its
        # own, `default`, were added with the requirement tiers)
        want = {"minimax_h3_ref2va": {"model": "minimax-h3-ref2va",
                                      "loras": "minimax-h3-ref2v-turbo-lora",
                                      "text_encoder": "qwen3vl-32b",
                                      "video_vae": "minimax-h3-video-vae",
                                      "audio_vae": "minimax-h3-audio-vae"},
                "minimax_h3_fl2va": {"model": "minimax-h3-fl2va",
                                     "loras": "minimax-h3-fl2v-turbo-lora",
                                     "text_encoder": "qwen3vl-32b",
                                     "video_vae": "minimax-h3-video-vae",
                                     "audio_vae": "minimax-h3-audio-vae"},
                "ltx2": {"model": "ltx2.5", "text_encoder": "ltx2.5-text-encoder",
                         "video_vae": "ltx2.5-video-vae", "audio_vae": "ltx2.5-audio-vae",
                         "upscaler": "ltx2.5-latent-upscaler",
                         "duration_head": "ltx2.5-duration-head"},
                "ltx2_ingredients": {"model": "ltx2.3", "loras": "ltx2.3-ic-lora-ingredients",
                                     "text_encoder": "gemma3-12b"},
                "krea2": {"model": "krea2", "text_encoder": "qwen3vl-4b", "vae": "wan2.1-vae"}}
        for tid, params in want.items():
            t = TG.load_target(tid)
            self.assertEqual({k: v["family"] for k, v in t.models.items()}, params, tid)
            for param, m in t.models.items():
                self.assertIn(m["family"], M.FAMILIES)
                self.assertTrue(m["folder"], (tid, param))
                # the preset's own files are named like their family
                for p in t.presets.values():
                    f = p.model if param == "model" else p.extra.get(param)
                    if f:
                        self.assertTrue(M.name_matches(f, m["patterns"]), (tid, param, f))
            d = t.describe()["models"]
            self.assertEqual(set(d), set(params))
            self.assertEqual(d["model"]["label"], M.family_label(params["model"]))
        self.assertEqual(TG.load_target("ltx2").models["upscaler"]["folder"],
                         "latent_upscale_models")
        self.assertEqual(TG.load_target("ltx2_ingredients").models["model"]["folder"],
                         "checkpoints")

    def test_name_fingerprint_mismatch_unknown(self):
        self.assertEqual(self.check("ltx2", "model", "LTX-2.5-anything.safetensors")["match"],
                         "name")
        self.put("diffusion_models", "renamed.safetensors", ltx_tensors("2.5"))
        c = self.check("ltx2", "model", "renamed.safetensors")
        self.assertEqual((c["match"], c["block"]), ("fingerprint", False))
        self.assertIn("header says LTX 2.5", c["message"])
        self.put("diffusion_models", "wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors",
                 wan14_tensors())
        c = self.check("ltx2", "model", "wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors")
        self.assertEqual((c["match"], c["block"]), ("mismatch", True))
        self.assertEqual(c["message"], "wan2.2_i2v_low_noise_14B_fp8_scaled is Wan 2.2 I2V 14B "
                                       "low-noise (tensors + name), but this LTX-2 target's "
                                       "model must be LTX 2.5")
        self.put("diffusion_models", "old.safetensors", ltx_tensors("2.3"),
                 {"model_version": "2.3.0"})
        c = self.check("ltx2", "model", "old.safetensors")
        self.assertEqual((c["match"], c["found"]["family"]), ("mismatch", "ltx2.3"))
        # H3: an unnamed merge passes (the tensors can't say which), FL2VA doesn't
        self.put("diffusion_models", "merge.safetensors", h3_tensors())
        self.put("diffusion_models", "x_fl2va.safetensors", h3_tensors())
        c = self.check("minimax_h3_ref2va", "model", "merge.safetensors")
        self.assertEqual((c["match"], c["block"]), ("fingerprint", False))
        self.assertIn("can't be told", c["message"])
        c = self.check("minimax_h3_ref2va", "model", "x_fl2va.safetensors")
        self.assertEqual((c["match"], c["found"]["family"]), ("mismatch", "minimax-h3-fl2va"))
        # unknown header, and a file that isn't there
        self.put("diffusion_models", "flux.safetensors", {"img_in.weight": [3072, 64]})
        for name in ("flux.safetensors", "absent.safetensors"):
            c = self.check("ltx2", "model", name)
            self.assertEqual((c["match"], c["block"]), ("unknown", False), name)
        # no models folder: names only
        c = TG.check_model(TG.load_target("ltx2"), "model", "renamed.safetensors", None)
        self.assertEqual(c["match"], "unchecked")
        self.assertIn("COMFYUI_PATH", c["message"])
        # a param with no family, or no file
        self.assertIsNone(self.check("ltx2", "loras", "x.safetensors"))
        self.assertIsNone(self.check("ltx2", "model", ""))

    def test_other_params_use_their_folders(self):
        self.put("latent_upscale_models", "up.safetensors", {
            "initial_conv.weight": [1024, 128, 3, 3, 3], "initial_norm.weight": [1024],
            "res_blocks.0.conv1.weight": [1], "post_upsample_res_blocks.0.conv1.weight": [1],
            "upsampler.0.weight": [1], "final_conv.weight": [128, 1024, 3, 3, 3]})
        c = self.check("ltx2", "upscaler", "up.safetensors")
        # the 2.3 and 2.5 upscalers share every tensor: the header can't say which
        self.assertEqual((c["match"], c["found"]["family"]), ("fingerprint", "ltx2-latent-upscaler"))
        shutil.copy(os.path.join(self.models, "latent_upscale_models", "up.safetensors"),
                    os.path.join(self.models, "latent_upscale_models", "ltx-2.3-up.safetensors"))
        c = self.check("ltx2", "upscaler", "ltx-2.3-up.safetensors")
        self.assertEqual((c["match"], c["found"]["family"]), ("mismatch", "ltx2.3-latent-upscaler"))

    def test_series_config_extends_the_patterns(self):
        self.assertEqual(TG.series_model_families({}), {})
        cfg = {"model_families": {"_note": "x", "ltx2.5": ["my_ltx25_merge*"], "krea2": "k*"}}
        extra = TG.series_model_families(cfg)
        self.assertEqual(extra, {"ltx2.5": ["my_ltx25_merge*"], "krea2": ["k*"]})
        c = self.check("ltx2", "model", "My_LTX25_Merge_v2.safetensors", extra=extra)
        self.assertEqual(c["match"], "name")
        self.assertIn("my_ltx25_merge*", c["patterns"])
        self.assertEqual(self.check("ltx2", "model", "My_LTX25_Merge_v2.safetensors")["match"],
                         "unknown")
        # patterns for a variant family help identify() pick the variant
        self.put("diffusion_models", "custom_expert_B.safetensors", wan14_tensors())
        r = M.identify(os.path.join(self.models, "diffusion_models", "custom_expert_B.safetensors"),
                       TG.family_names({"wan2.2-i2v-14b-low": ["*expert_b*"]}))
        self.assertEqual(r["family"], "wan2.2-i2v-14b-low")
        for bad in ({"model_families": ["x"]}, {"model_families": {"ltx2.5": [3]}},
                    {"model_families": {"ltx2.5": [""]}}):
            with self.assertRaises(ValueError):
                TG.series_model_families(bad)

    def test_other_agents_families_are_not_blocked(self):
        """A target family modelid doesn't know (e.g. another target's own
        naming) is never a mismatch: there is nothing to compare with."""
        t = TG.load_target("ltx2")
        self.put("diffusion_models", "w.safetensors", wan14_tensors())
        with mock.patch.dict(t.spec, {"models": {"model_low": {"family": "wan22-low-custom",
                                                                "patterns": ["*low*"]}}}):
            self.assertEqual(t.models["model_low"]["folder"], None)   # not a binding widget
            c = TG.check_model(t, "model_low", "w.safetensors", self.resolve)
            self.assertEqual((c["match"], c["block"]), ("unknown", False))
        with mock.patch.dict(t.spec, {"models": {"model": {"family": "wan22-low-custom"}}}):
            c = TG.check_model(t, "model", "w.safetensors", self.resolve)
            self.assertEqual((c["match"], c["block"]), ("unknown", False))
            self.assertIn("can't verify", c["message"])


class QueueTimeTest(ApiTest):
    """h3jobs.check_models through POST /h3pipe/render (h3edit.queue_shots)."""

    def setUp(self):
        super().setUp()
        self.models = os.path.join(self.tmp, "ComfyUI", "models")
        self.ctx.model_resolve = lambda folder, name: (
            p if os.path.isfile(p := os.path.join(self.models, folder, name)) else None)
        write_st(os.path.join(self.models, "diffusion_models", "my_merge.safetensors"),
                 h3_tensors())
        write_st(os.path.join(self.models, "diffusion_models", "wan_low_noise.safetensors"),
                 wan14_tensors())

    def sidecar(self, q):
        return T.get_take(self.ep, "proxy", q["shot"], q["take"]).sidecar

    def test_name_match_says_nothing(self):
        r = self.render("sh010", model="minimax_h3_ref2va_pruned_int8_convrot.safetensors")
        (q,) = r["queued"]
        self.assertFalse([n for n in self.sidecar(q).get("notes", []) if "named like" in n])

    def test_fingerprint_match_is_a_note(self):
        r = self.render("sh010", model="my_merge.safetensors")
        (q,) = r["queued"]
        notes = " ".join(self.sidecar(q)["notes"])
        self.assertIn("isn't named like MiniMax H3 Ref2VA", notes)
        self.assertIn("header says MiniMax H3", notes)
        # cached in the user folder
        self.assertTrue(os.path.isfile(os.path.join(self.user, "default", "h3pipe",
                                                    "modelid_cache.json")))

    def test_mismatch_blocks_unless_allowed(self):
        r = self.render("sh010", model="wan_low_noise.safetensors")
        self.assertEqual(r["queued"], [])
        (s,) = r["skipped"]
        self.assertIn("model mismatch: wan_low_noise is Wan 2.2 I2V 14B low-noise", s["reason"])
        self.assertIn("allow_model_mismatch", s["reason"])
        self.assertEqual(s["model_mismatch"][0]["param"], "model")
        self.assertEqual(self.comfy.graphs, [])
        self.assertEqual(T.list_takes(self.ep, "proxy", "sh010"), [])
        r = self.render("sh010", model="wan_low_noise.safetensors", allow_model_mismatch=True)
        (q,) = r["queued"]
        self.assertIn("model mismatch, rendered anyway", " ".join(self.sidecar(q)["notes"]))
        self.err(A.post_render(self.ctx, {"ep": self.ep, "allow_model_mismatch": "yes"}), 400)

    def test_unknown_and_unchecked_warn(self):
        r = self.render("sh010", model="not_installed.safetensors")
        (q,) = r["queued"]
        self.assertIn("wasn't fingerprinted", " ".join(self.sidecar(q)["notes"]))
        self.ctx.model_resolve = None
        r = self.render("sh020", model="not_installed.safetensors")
        (q,) = r["queued"]
        self.assertIn("set COMFYUI_PATH", " ".join(self.sidecar(q)["notes"]))

    def test_series_config_patterns_apply(self):
        p = os.path.join(self.ep, "series.json")
        cfg = json.load(open(p, encoding="utf-8"))
        cfg["model_families"] = {"minimax-h3-ref2va": ["my_merge*"]}
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh)
        r = self.render("sh010", model="my_merge.safetensors")
        (q,) = r["queued"]
        self.assertFalse([n for n in self.sidecar(q).get("notes", []) if "named like" in n])

    def test_check_models_directly(self):
        doc, i = J.find_shot(self.ep, "proxy", "sh010")
        job = J.plan_job(self.ep, "proxy", doc, i,
                         J.RenderRequest("sh010", model="wan_low_noise.safetensors"), {})
        before = job.action
        J.check_models(job, self.ctx.model_resolve, None)
        self.assertEqual((job.action, job.mismatch_from, job.runs), ("mismatch", before, False))
        self.assertIn("wan_low_noise", job.mismatch_note())
        # a re-check with the mismatch allowed renders, with one note (not two)
        job.allow_model_mismatch = True
        J.check_models(job, self.ctx.model_resolve, None)
        J.check_models(job, self.ctx.model_resolve, None)
        self.assertEqual(job.action, before)
        self.assertEqual(len([n for n in job.notes if "rendered anyway" in n]), 1)


class ModelsRouteTest(ApiTest):
    build = False

    def setUp(self):
        super().setUp()
        self.models = os.path.join(self.tmp, "ComfyUI", "models")
        self.ctx.model_resolve = lambda folder, name: (
            p if os.path.isfile(p := os.path.join(self.models, folder, name)) else None)
        dm = os.path.join(self.models, "diffusion_models")
        write_st(os.path.join(dm, "wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors"),
                 wan14_tensors())
        write_st(os.path.join(dm, "renamed_ltx.safetensors"), ltx_tensors("2.5"),
                 {"model_version": "2.5.0"})
        write_st(os.path.join(dm, "flux.safetensors"), {"img_in.weight": [3072, 64]})
        self.names = ["flux.safetensors", "ltx-2.5-22b-distilled.safetensors",
                      "renamed_ltx.safetensors", "wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors",
                      "missing.safetensors"]
        # ComfyUI's list comes from /object_info (no folder_paths outside ComfyUI)
        self.comfy.info["UNETLoader"] = {"input": {"required": {"unet_name": [self.names]}}}

    def test_models_route(self):
        data = self.ok(A.get_models(self.ctx, {"target": "ltx2", "param": "model"}))
        self.assertEqual((data["family"], data["label"], data["folder"], data["class_type"],
                          data["field"], data["fingerprint"]),
                         ("ltx2.5", "LTX 2.5", "diffusion_models", "UNETLoader", "unet_name", True))
        files = {f["name"]: f for f in data["files"]}
        self.assertEqual([f["name"] for f in data["files"]],
                         ["ltx-2.5-22b-distilled.safetensors", "renamed_ltx.safetensors",
                          "flux.safetensors", "wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors",
                          "missing.safetensors"])
        self.assertEqual((files["ltx-2.5-22b-distilled.safetensors"]["match"],
                          files["ltx-2.5-22b-distilled.safetensors"]["confidence"]),
                         ("name", "name"))
        f = files["renamed_ltx.safetensors"]
        self.assertEqual((f["match"], f["family"], f["confidence"], f["mismatch"]),
                         ("fingerprint", "ltx2.5", "metadata", False))
        f = files["wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors"]
        self.assertEqual((f["match"], f["family"], f["mismatch"]),
                         ("other", "wan2.2-i2v-14b-low", True))
        self.assertIn("must be LTX 2.5", f["detail"])
        self.assertEqual((files["flux.safetensors"]["match"],
                          files["flux.safetensors"]["confidence"],
                          files["flux.safetensors"]["mismatch"]), ("other", "unknown", False))
        self.assertEqual(files["missing.safetensors"]["match"], "other")
        json.dumps(data)

    def test_models_route_errors_and_series_patterns(self):
        self.err(A.get_models(self.ctx, {}), 400)
        self.err(A.get_models(self.ctx, {"target": "nope"}), 400)
        self.err(A.get_models(self.ctx, {"target": "ltx2", "param": "loras"}), 400)
        self.err(A.get_models(self.ctx, {"target": "ltx2", "ep": self.tmp}), 403)
        p = os.path.join(self.ep, "series.json")
        cfg = json.load(open(p, encoding="utf-8"))
        cfg["model_families"] = {"ltx2.5": ["flux*"]}
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh)
        data = self.ok(A.get_models(self.ctx, {"target": "ltx2", "ep": self.ep}))
        self.assertIn("flux*", data["patterns"])
        self.assertEqual(data["files"][0]["name"], "flux.safetensors")   # ComfyUI's order
        self.assertEqual(data["files"][0]["match"], "name")
        # no models folder: names only, the rest "other" with confidence unknown
        self.ctx.model_resolve = None
        data = self.ok(A.get_models(self.ctx, {"target": "ltx2"}))
        self.assertFalse(data["fingerprint"])
        self.assertEqual([f["match"] for f in data["files"]].count("fingerprint"), 0)
        self.assertIn(("GET", "/h3pipe/models"), {(m, p) for m, p, _f, _t in A.ROUTES})

    def test_targets_route_has_models(self):
        data = self.ok(A.get_targets(self.ctx, {}))
        ltx = next(t for t in data["targets"] if t["id"] == "ltx2")
        self.assertEqual(ltx["models"]["model"]["family"], "ltx2.5")
        self.assertIn("ltx-2.5*", ltx["models"]["model"]["patterns"])


class RenderCliModelTest(unittest.TestCase):
    """h3render finds model files through $COMFYUI_PATH."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self._tmp.name, "ep")
        os.makedirs(self.root)
        build_episode(self.root)
        self.comfy_root = os.path.join(self._tmp.name, "ComfyUI")
        write_st(os.path.join(self.comfy_root, "models", "unet", "wan_low_noise.safetensors"),
                 wan14_tensors())
        self.comfy = FakeComfy()
        self.env = dict(ENV, COMFYUI_PATH=self.comfy_root, TMP=self._tmp.name,
                        TEMP=self._tmp.name)

    def tearDown(self):
        self.comfy.close()
        self._tmp.cleanup()

    def render(self, *args):
        return subprocess.run([sys.executable, os.path.join(ROOT, "h3render.py"), self.root,
                               "--workflow", WORKFLOW, "--comfy", self.comfy.url, "--proxy",
                               "--only", "sh010", "--model", "wan_low_noise.safetensors", *args],
                              capture_output=True, env=self.env, cwd=self.root)

    def test_cli_blocks_then_renders_anyway(self):
        r = self.render()
        out = r.stdout.decode("utf-8", "replace")
        self.assertEqual(r.returncode, 0, out + r.stderr.decode())
        self.assertIn("1 blocked (model mismatch)", out)
        self.assertIn("wan_low_noise is Wan 2.2 I2V 14B low-noise", out)
        self.assertEqual(self.comfy.graphs, [])
        # the dry run with --check-nodes lists every check
        self.comfy.nodes |= {"UNETLoader", "LoraLoaderModelOnly"}
        r = self.render("--allow-model-mismatch", "--dry-run", "--check-nodes")
        out = r.stdout.decode("utf-8", "replace")
        self.assertIn("model files, checked by name, then by header", out)
        self.assertRegex(out, r"! model +wan_low_noise.safetensors: mismatch -> Wan 2.2 I2V 14B "
                              r"low-noise \(tensors \+ name\) — wants MiniMax H3 Ref2VA")
        r = self.render("--allow-model-mismatch")
        self.assertEqual(r.returncode, 0, r.stdout.decode() + r.stderr.decode())
        (t,) = T.list_takes(self.root, "proxy", "sh010")
        self.assertIn("model mismatch, rendered anyway", " ".join(t.sidecar["notes"]))


# ---------------------------------------------------------------------------
# the installed files (only on a machine that has them)
# ---------------------------------------------------------------------------

# Verified by reading these files' headers on the dev machine (2026-09-19).
REAL_FILES = {
    "diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors": "minimax-h3-ref2va",
    "diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors": "minimax-h3-fl2va",
    "diffusion_models/10Eros_Max_h3_TURBO-hybrid_beta4_int8_convrot.safetensors": "minimax-h3",
    "diffusion_models/ltx-2.3-22b-dev_transformer_only_int8_convrot.safetensors": "ltx2.3",
    "diffusion_models/ltx-2.5-22b-distilled-transformer-comfy-int8-convrot.safetensors": "ltx2.5",
    "checkpoints/ltx-2.3-22b-distilled-fp8.safetensors": "ltx2.3",
    "checkpoints/ltx-2.3-22b-distilled.safetensors": "ltx2.3",
    "diffusion_models/wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors": "wan2.2-i2v-14b-high",
    "diffusion_models/wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors": "wan2.2-i2v-14b-low",
    "diffusion_models/wan2.2_fun_vace_high_noise_14B_fp8_scaled.safetensors": "wan2.2-vace-14b-high",
    "diffusion_models/wan2.2_ti2v_5B_fp16.safetensors": "wan2.2-ti2v-5b",
    "unet/krea2_turbo_fp8_scaled.safetensors": "krea2",
    "text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors": "qwen3vl-32b",
    "text_encoders/gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors": "ltx2.5-text-encoder",
    "text_encoders/gemma_3_12B_it_fp4_mixed.safetensors": "gemma3-12b",
    "vae/minimax_h3_video_vae_fp16.safetensors": "minimax-h3-video-vae",
    "vae/minimax_h3_audio_vae_fp32.safetensors": "minimax-h3-audio-vae",
    "vae/ltx-2.5-video-vae-bf16.safetensors": "ltx2.5-video-vae",
    "vae/LTX23_video_vae_bf16.safetensors": "ltx2.3-video-vae",
    "vae/ltx-2.5-audio-vae-bf16.safetensors": "ltx2.5-audio-vae",
    "vae/LTX23_audio_vae_bf16.safetensors": "ltx2.3-audio-vae",
    "vae/wan2.2_vae.safetensors": "wan2.2-vae",
    "vae/wan_2.1_vae.safetensors": "wan2.1-vae",
    "latent_upscale_models/ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0.safetensors":
        "ltx2.5-latent-upscaler",
    "clip/umt5_xxl_fp8_e4m3fn_scaled.safetensors": "umt5-xxl",
}


class RealFilesTest(unittest.TestCase):
    def test_installed_files(self):
        present = {k: v for k, v in REAL_FILES.items()
                   if os.path.isfile(os.path.join(REAL, *k.split("/")))}
        if not present:
            self.skipTest(f"no installed model files under {REAL}")
        for rel, fam in present.items():
            with self.subTest(rel):
                r = M.identify(os.path.join(REAL, *rel.split("/")), TG.family_names())
                self.assertEqual(r["family"], fam, r)
                self.assertNotEqual(r["confidence"], "unknown")


if __name__ == "__main__":
    unittest.main()
