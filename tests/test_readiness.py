"""
Requirement tiers, family resolution, readiness, downloads and the episode
target (docs/API.md "Readiness, requirement tiers, and the episode target").

  - targets.resolve_models against synthetic model lists (and, for a
    fingerprint substitute, synthetic safetensors headers): an exact file, a
    family substitute chosen by precision, an accelerator falling back to the
    pass's `base` preset, an optional file turning its feature off, a required
    file blocking with its download record. Never a file of another family.
  - h3jobs.resolve_models on a planned job: the base preset's steps and
    sampler reach the take, the LoRA loader leaves the graph, the sidecar
    records `resolved`; a missing required file skips the shot before a take
    is reserved (POST /h3pipe/render: `missing_files`).
  - h3edit.readiness over a fake /object_info; `GET /h3pipe/targets?ready=1`;
    `h3.py targets` output.
  - The downloads data: every file a target names has an entry, every URL a
    source; every accelerator has a base preset.
  - The episode target: precedence, PUT /h3pipe/episode-target, the CLI, and
    a shot pinned to its built target.
  - The user's machine, read-only, when ComfyUI answers at 127.0.0.1:8188.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.request
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
from test_api import ApiTest  # noqa: E402
from test_modelid import krea_tensors, ltx_tensors, wan14_tensors, write_st  # noqa: E402
from test_render import WORKFLOW  # noqa: E402

COMFY = "http://127.0.0.1:8188"


def lister(files: dict[str, list[str]]):
    """listing(spec) over {folder: [files]} (a folder not given: unknown)."""
    return lambda spec: files.get(spec.get("folder"))


def everything(tid: str, pass_: str = "final") -> dict[str, list[str]]:
    """{folder: [files]} with every file a target names installed."""
    t = TG.load_target(tid)
    out: dict[str, list[str]] = {}
    for name, param in t.named_files().items():
        spec = t.models.get(param) or TG.lora_spec(t, name)
        out.setdefault(spec["folder"], []).append(name)
    return out


def without(files: dict, *names: str) -> dict:
    return {k: [n for n in v if n not in names] for k, v in files.items()}


def resolve(tid: str, pass_: str, files: dict, **kw) -> dict:
    t = TG.load_target(tid)
    return TG.resolve_models(t, pass_, TG.wanted_files(t, pass_), lister(files),
                             slot=TG.lora_slot(t, pass_), **kw)


# ---------------------------------------------------------------------------
# resolution against synthetic model lists
# ---------------------------------------------------------------------------

class ResolveTest(unittest.TestCase):
    def test_everything_installed_resolves_exact(self):
        for t in TG.list_targets():
            for pass_ in t.presets:
                r = resolve(t.id, pass_, everything(t.id))
                self.assertEqual((r["missing"], r["blocked"], r["base"]), ([], [], None),
                                 (t.id, pass_))
                self.assertTrue(all(v["how"] == "exact" for v in r["resolved"].values()),
                                (t.id, pass_, r["resolved"]))

    def test_exact_in_a_subfolder(self):
        # ComfyUI-Manager saves the LTX 2.3 checkpoints in checkpoints/LTX-2.3
        want = "ltx-2.3-22b-distilled-fp8.safetensors"
        files = without(everything("ltx2_ingredients"), want)
        files["checkpoints"].append("LTX-2.3\\" + want)
        r = resolve("ltx2_ingredients", "final", files)
        self.assertEqual(r["resolved"]["model"],
                         {"want": want, "using": "LTX-2.3\\" + want, "how": "exact",
                          "tier": "accelerator"})
        self.assertEqual(r["files"], {"model": "LTX-2.3\\" + want})

    def test_family_substitute_by_precision(self):
        want = "ltx-2.3-22b-distilled-fp8.safetensors"
        files = without(everything("ltx2_ingredients"), want)
        # the shorter name has no precision; the fp8 one is the same precision
        # as the preset's; the dev checkpoint is the wrong kind (keep: distilled)
        files["checkpoints"] += ["ltx-2.3-22b-distilled.safetensors",
                                 "ltx-2.3-22b-distilled-1.1-fp8.safetensors",
                                 "ltx-2.3-22b-dev-fp8.safetensors"]
        r = resolve("ltx2_ingredients", "final", files)
        self.assertEqual(r["resolved"]["model"]["how"], "family")
        self.assertEqual(r["resolved"]["model"]["using"],
                         "ltx-2.3-22b-distilled-1.1-fp8.safetensors")
        self.assertIsNone(r["base"])
        self.assertIn("isn't installed: rendered with ltx-2.3-22b-distilled-1.1-fp8", r["notes"][0])
        # without the fp8 one: the shortest name of the family
        files["checkpoints"].remove("ltx-2.3-22b-distilled-1.1-fp8.safetensors")
        r = resolve("ltx2_ingredients", "final", files)
        self.assertEqual(r["resolved"]["model"]["using"], "ltx-2.3-22b-distilled.safetensors")

    def test_never_another_family(self):
        files = without(everything("minimax_h3_ref2va"),
                        "minimax_h3_ref2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors")
        # FL2V turbo LoRAs, a 4-step Ref2V LoRA (the wrong step count) and the
        # SLA one: none of them may stand in for the 8-step Ref2V LoRA
        files["loras"] += ["minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors",
                           "minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors",
                           "minimax_h3_ref2v_turbo_8step_v0.1_768p_sla_comfyui_bf16.safetensors"]
        r = resolve("minimax_h3_ref2va", "final", files)
        self.assertEqual(r["resolved"]["loras"]["how"], "base")
        # a Ref2VA model named for FL2VA is never picked for the model
        files = without(everything("minimax_h3_ref2va"),
                        "minimax_h3_ref2va_pruned_int8_convrot.safetensors")
        files["diffusion_models"].append("minimax_h3_fl2va_pruned_int8_convrot.safetensors")
        r = resolve("minimax_h3_ref2va", "final", files)
        self.assertEqual([m["param"] for m in r["blocked"]], ["model"])
        # a same-step Ref2V LoRA of the family is picked
        files = without(everything("minimax_h3_ref2va"),
                        "minimax_h3_ref2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors")
        files["loras"].append("Minimax_H3_Ref2V_Turbo_8Step_v2.safetensors")
        r = resolve("minimax_h3_ref2va", "final", files)
        self.assertEqual((r["resolved"]["loras"]["how"], r["loras"][0]["name"]),
                         ("family", "Minimax_H3_Ref2V_Turbo_8Step_v2.safetensors"))

    def test_accelerator_falls_back_to_base(self):
        # H3 Ref2VA: no turbo LoRA -> no LoRA, 20 steps, res_multistep
        for pass_ in ("final", "proxy"):
            t = TG.load_target("minimax_h3_ref2va")
            lora = t.presets[pass_].lora
            r = resolve(t.id, pass_, without(everything(t.id), lora))
            self.assertEqual(r["base"], {"loras": [], "steps": 20, "sampler": "res_multistep"})
            self.assertEqual(r["loras"], [])
            self.assertEqual(r["values"], {"steps": 20, "sampler": "res_multistep"})
            self.assertEqual(r["resolved"]["loras"],
                             {"want": [lora], "using": [], "how": "base", "tier": "accelerator"})
            self.assertEqual([m["tier"] for m in r["missing"]], ["accelerator"])
            self.assertEqual(r["blocked"], [])
            self.assertIn("base preset (steps 20, sampler res_multistep, no LoRA)", r["notes"][-1])
        # Wan 2.2 14B I2V: one of the pair missing is enough; cfg 3.5, 20 steps
        r = resolve("wan22_i2v", "final",
                    without(everything("wan22_i2v"),
                            "wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors"))
        self.assertEqual((r["loras"], r["values"]["steps"], r["values"]["cfg"],
                          r["values"]["split"]), ([], 20, 3.5, 0.5))
        # FL2VA
        r = resolve("minimax_h3_fl2va", "proxy",
                    without(everything("minimax_h3_fl2va"),
                            "minimax_h3_fl2v_lightx2v_turbo_4step_v0.1_comfy.safetensors"))
        self.assertEqual((r["loras"], r["values"]), ([], {"steps": 20, "sampler": "res_multistep"}))
        # LTX-2.3 ingredients: no distilled checkpoint -> the dev one, the
        # IC-LoRA at the model card's 1.4, 30 steps, cfg 4
        r = resolve("ltx2_ingredients", "final",
                    without(everything("ltx2_ingredients"), "ltx-2.3-22b-distilled-fp8.safetensors"))
        self.assertEqual(r["files"], {"model": "ltx-2.3-22b-dev-fp8.safetensors"})
        self.assertEqual(r["loras"], [{"name": "ltx-2.3-22b-ic-lora-ingredients-0.9.safetensors",
                                       "strength": 1.4}])
        self.assertEqual((r["values"]["steps"], r["values"]["cfg"]), (30, 4.0))
        self.assertEqual(r["resolved"]["model"]["how"], "base")
        self.assertEqual(r["blocked"], [])
        # ... and without the dev one either, the base's own file is required
        r = resolve("ltx2_ingredients", "final",
                    without(everything("ltx2_ingredients"), "ltx-2.3-22b-distilled-fp8.safetensors",
                            "ltx-2.3-22b-dev-fp8.safetensors"))
        self.assertEqual([(m["tier"], m["want"]) for m in r["blocked"]],
                         [("required", "ltx-2.3-22b-dev-fp8.safetensors")])
        self.assertTrue(r["blocked"][0]["url"].startswith("https://huggingface.co/"))

    def test_a_series_turbo_lora_is_the_accelerator(self):
        # a series config's own turbo LoRA name, not named like the family:
        # in the pass's lora slot it is still the accelerator (base, not blocked)
        t = TG.load_target("minimax_h3_ref2va")
        wanted = dict(TG.wanted_files(t, "proxy"),
                      loras=[{"name": "proxy_turbo_4step.safetensors", "strength": 1.0}])
        r = TG.resolve_models(t, "proxy", wanted, lister(everything(t.id)),
                              slot={"proxy_turbo_4step.safetensors"})
        self.assertEqual((r["resolved"]["loras"]["how"], r["blocked"]), ("base", []))
        # a style LoRA from a profile is required: blocked, with no URL known
        wanted["loras"] = [{"name": "style.safetensors", "strength": 0.6}]
        r = TG.resolve_models(t, "proxy", wanted, lister(everything(t.id)))
        self.assertEqual([(m["tier"], m["url"]) for m in r["blocked"]], [("required", None)])
        self.assertIn("search for style.safetensors", r["blocked"][0]["source"])

    def test_optional_turns_its_feature_off(self):
        r = resolve("ltx2", "final",
                    without(everything("ltx2"), "ltx-2.5-duration-head-bf16.safetensors"))
        self.assertEqual(r["resolved"]["duration_head"]["how"], "off")
        self.assertEqual(r["features_off"], ["dur: model (duration head)"])
        self.assertEqual(r["missing"][0]["feature"], "dur: model (duration head)")
        self.assertEqual(r["missing"][0]["tier"], "optional")
        self.assertEqual(r["blocked"], [])
        self.assertTrue(r["missing"][0]["url"].endswith("ltx-2.5-duration-head-bf16.safetensors"))

    def test_required_blocks_with_a_download_message(self):
        want = "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
        r = resolve("minimax_h3_ref2va", "final", without(everything("minimax_h3_ref2va"), want))
        self.assertEqual(len(r["blocked"]), 1)
        m = r["blocked"][0]
        self.assertEqual((m["param"], m["tier"], m["want"], m["folder"]),
                         ("text_encoder", "required", want, "text_encoders"))
        self.assertEqual(m["url"], "https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/"
                                   "text_encoders/" + want)
        msg = TG.missing_message(m)
        self.assertIn(want, msg)
        self.assertIn("models/text_encoders", msg)
        self.assertIn(m["url"], msg)

    def test_unknown_folder_is_left_alone(self):
        files = everything("ltx2")
        del files["diffusion_models"]                 # ComfyUI can't list it
        r = resolve("ltx2", "final", files)
        self.assertNotIn("model", r["resolved"])
        self.assertEqual(r["blocked"], [])


class FingerprintResolveTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.models = self._tmp.name
        self.cache = TG.modelid.ModelIdCache(None)

    def tearDown(self):
        self._tmp.cleanup()

    def put(self, folder, name, tensors, meta=None):
        write_st(os.path.join(self.models, folder, name), tensors, meta)

    def resolve_path(self, folder, name):
        p = os.path.join(self.models, folder, name)
        return p if os.path.isfile(p) else None

    def test_a_renamed_file_passes_by_its_header(self):
        want = "krea2_turbo_fp8_scaled.safetensors"
        self.put("diffusion_models", "my_style_v3.safetensors", krea_tensors())
        self.put("diffusion_models", "wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors",
                 wan14_tensors())
        files = without(everything("krea2"), want)
        files["diffusion_models"] = ["wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors",
                                     "my_style_v3.safetensors"]
        r = resolve("krea2", "final", files, resolve=self.resolve_path, cache=self.cache)
        self.assertEqual((r["resolved"]["model"]["how"], r["resolved"]["model"]["using"]),
                         ("family", "my_style_v3.safetensors"))
        self.assertIn("header says Krea 2", r["notes"][0])
        # without it: the Wan file is never picked, the model is missing
        files["diffusion_models"] = ["wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors"]
        r = resolve("krea2", "final", files, resolve=self.resolve_path, cache=self.cache)
        self.assertEqual([m["param"] for m in r["blocked"]], ["model"])

    def test_keep_words_hold_for_headers_too(self):
        # an LTX 2.5 merge whose name doesn't say distilled or dev: its header
        # can't say which either, so it doesn't stand in for the distilled model
        want = "ltx-2.5-22b-distilled-transformer-comfy-int8-convrot.safetensors"
        self.put("diffusion_models", "my_merge_v3.safetensors", ltx_tensors("2.5"),
                 {"model_version": "2.5.0"})
        files = without(everything("ltx2"), want)
        files["diffusion_models"] = ["my_merge_v3.safetensors"]
        r = resolve("ltx2", "final", files, resolve=self.resolve_path, cache=self.cache)
        self.assertEqual([m["param"] for m in r["blocked"]], ["model"])
        # named distilled, it does
        self.put("diffusion_models", "my_merge_distilled_v3.safetensors", ltx_tensors("2.5"),
                 {"model_version": "2.5.0"})
        files["diffusion_models"].append("my_merge_distilled_v3.safetensors")
        r = resolve("ltx2", "final", files, resolve=self.resolve_path, cache=self.cache)
        self.assertEqual(r["resolved"]["model"]["using"], "my_merge_distilled_v3.safetensors")


# ---------------------------------------------------------------------------
# a job: the base preset in the graph, the sidecar, a blocked render
# ---------------------------------------------------------------------------

def ref2va_files(ep_files: dict | None = None) -> dict:
    """Every H3 Ref2VA file, plus the kitchen_sink series config's own model."""
    f = everything("minimax_h3_ref2va")
    f["diffusion_models"] = f["diffusion_models"] + ["series_model.safetensors"]
    f["loras"] = f["loras"] + ["series_turbo_8step.safetensors", "proxy_turbo_4step.safetensors",
                               "profile_turbo_8step.safetensors", "profile_style.safetensors",
                               "profile_model.safetensors"]
    return f


class JobResolveTest(ApiTest):
    def job(self, shot="sh010", pass_="proxy", **req):
        doc, i = J.find_shot(self.ep, pass_, shot)
        return J.plan_job(self.ep, pass_, doc, i, J.RenderRequest(shot, **req),
                          T.load_overrides(self.ep))

    def test_base_preset_reaches_the_graph_and_sidecar(self):
        job = self.job()
        self.assertEqual((job.steps, [lo["name"] for lo in job.loras]),
                         (4, ["proxy_turbo_4step.safetensors"]))
        files = without(ref2va_files(), "proxy_turbo_4step.safetensors")
        J.resolve_models(job, lister(files), None, None, {})
        self.assertTrue(job.runs)
        self.assertTrue(job.based)
        self.assertEqual((job.steps, job.loras), (20, []))
        self.assertEqual(job.values, {"sampler": "res_multistep"})
        take = T.Take(job.id, 1, "proxy", T.take_paths(self.ep, "proxy", job.id, 1))
        g = J.graph_for(J.load_graph(WORKFLOW), job, take)
        self.assertEqual([k for k, v in g.items() if v["class_type"] == "LoraLoaderModelOnly"], [])
        unet = next(k for k, v in g.items() if v["class_type"] == "UNETLoader")
        # whatever read the LoRA's MODEL now reads the UNETLoader's
        self.assertTrue(any(v["inputs"].get("model") == [unet, 0] for v in g.values()))
        self.assertEqual([v["inputs"]["sampler_name"] for v in g.values()
                          if v["class_type"] == "KSamplerSelect"], ["res_multistep"])
        self.assertEqual(J.check_graph(g), [])
        frozen = J.frozen_shotlist(job)["shots"][0]
        self.assertEqual((frozen["steps"], frozen["loras"], frozen["sampler"]),
                         (20, [], "res_multistep"))
        sc = J.sidecar_for(job)
        self.assertTrue(sc["base"])
        self.assertEqual(sc["resolved"]["loras"],
                         {"want": ["proxy_turbo_4step.safetensors"], "using": [], "how": "base",
                          "tier": "accelerator"})
        self.assertEqual(sc["resolved"]["model"]["how"], "exact")
        self.assertTrue(any("base preset" in n for n in sc["notes"]))

    def test_steps_set_for_the_shot_are_kept(self):
        job = self.job(steps=6)
        J.resolve_models(job, lister(without(ref2va_files(), "proxy_turbo_4step.safetensors")),
                         None, None, {})
        self.assertEqual((job.steps, job.based), (6, True))
        self.assertTrue(any("steps 6 kept" in n for n in job.notes))

    def test_family_substitute_is_patched_in(self):
        job = self.job()
        files = without(ref2va_files(), "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors")
        files["text_encoders"].append("qwen3vl_32b_minimax_h3_fp8.safetensors")
        J.resolve_models(job, lister(files), None, None, {})
        self.assertEqual(job.values, {"text_encoder": "qwen3vl_32b_minimax_h3_fp8.safetensors"})
        take = T.Take(job.id, 1, "proxy", T.take_paths(self.ep, "proxy", job.id, 1))
        g = J.graph_for(J.load_graph(WORKFLOW), job, take)
        self.assertEqual([v["inputs"]["clip_name"] for v in g.values()
                          if v["class_type"] == "CLIPLoader"],
                         ["qwen3vl_32b_minimax_h3_fp8.safetensors"])
        self.assertEqual(J.sidecar_for(job)["resolved"]["text_encoder"]["how"], "family")

    def test_nothing_known_changes_nothing(self):
        job = self.job()
        before = J.sidecar_for(job)
        self.assertEqual(J.resolve_models(job, None), {})
        J.resolve_models(job, lister({}), None, None, {})      # no folder listed
        self.assertEqual(J.sidecar_for(job), before)

    def test_render_skips_a_missing_required_file_before_a_take(self):
        files = without(ref2va_files(), "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors")
        self.ctx._model_list = lambda folder, ct, f: files.get(folder)
        out = self.render("sh010")
        self.assertEqual(out["queued"], [])
        s = out["skipped"][0]
        self.assertEqual((s["shot"], s["target"]), ("sh010", "minimax_h3_ref2va"))
        self.assertIn("qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors is not installed", s["reason"])
        self.assertIn("models/text_encoders", s["reason"])
        m = s["missing_files"][0]
        self.assertEqual(set(m), {"param", "tier", "want", "family", "folder", "url", "source"})
        self.assertEqual((m["param"], m["tier"], m["folder"]),
                         ("text_encoder", "required", "text_encoders"))
        self.assertTrue(m["url"].startswith("https://huggingface.co/Comfy-Org/MiniMax-H3/"))
        self.assertEqual(T.list_takes(self.ep, "proxy", "sh010"), [])     # nothing reserved
        self.assertEqual(self.comfy.graphs, [])

    def test_render_with_the_base_preset(self):
        files = without(ref2va_files(), "proxy_turbo_4step.safetensors")
        self.ctx._model_list = lambda folder, ct, f: files.get(folder)
        out = self.render("sh010")
        self.assertEqual(len(out["queued"]), 1, out)
        g = self.comfy.graphs[-1]
        self.assertFalse(any(v["class_type"] == "LoraLoaderModelOnly" for v in g.values()))
        sc = T.list_takes(self.ep, "proxy", "sh010")[0].sidecar
        self.assertEqual((sc["steps"], sc["loras"], sc["base"]), (20, [], True))
        self.assertEqual(sc["resolved"]["loras"]["how"], "base")


# ---------------------------------------------------------------------------
# readiness
# ---------------------------------------------------------------------------

LOADERS = {("UNETLoader", "unet_name"): ["diffusion_models"],
           ("CLIPLoader", "clip_name"): ["text_encoders"],
           ("VAELoader", "vae_name"): ["vae"],
           ("LoraLoaderModelOnly", "lora_name"): ["loras"],
           ("CheckpointLoaderSimple", "ckpt_name"): ["checkpoints"],
           ("LTXVAudioVAELoader", "ckpt_name"): ["checkpoints"],
           ("LTXAVTextEncoderLoader", "ckpt_name"): ["checkpoints"],
           ("LTXAVTextEncoderLoader", "text_encoder"): ["text_encoders"],
           ("LatentUpscaleModelLoader", "model_name"): ["latent_upscale_models"],
           ("ModelPatchLoader", "name"): ["model_patches"]}


def fake_object_info(drop_files=(), drop_nodes=()) -> dict:
    """A /object_info with every node class the targets need and every file
    they name, less `drop_files` and `drop_nodes`."""
    files: dict[str, set] = {}
    classes = set()
    for t in TG.list_targets():
        classes |= set(E.target_nodes(t))
        for folder, names in everything(t.id).items():
            files.setdefault(folder, set()).update(names)
    classes |= {ct for ct, _ in LOADERS}
    info = {c: {"input": {"required": {}}} for c in classes if c not in drop_nodes}
    for (ct, field), folders in LOADERS.items():
        if ct in info:
            info[ct]["input"]["required"][field] = [sorted(
                n for f in folders for n in files.get(f, ()) if n not in drop_files)]
    return info


class ReadinessTest(unittest.TestCase):
    def ready(self, info, **kw):
        return E.readiness(TG.list_targets(), info, **kw)

    def test_all_ready(self):
        r = self.ready(fake_object_info())
        self.assertEqual({k: v["status"] for k, v in r.items()},
                         {t.id: "ready" for t in TG.list_targets()})
        self.assertEqual(r["ltx2"]["resolved"]["model"]["how"], "exact")
        self.assertEqual(set(r["minimax_h3_ref2va"]["by_pass"]), {"final", "proxy"})

    def test_degraded_not_ready_unknown(self):
        info = fake_object_info(drop_files={
            "ltx-2.5-duration-head-bf16.safetensors",                    # optional
            "minimax_h3_fl2v_lightx2v_turbo_4step_v0.1_comfy.safetensors",   # accelerator
            "wan2.2_vae.safetensors"})                                   # required
        r = self.ready(info)
        self.assertEqual(r["ltx2"]["status"], "degraded")
        self.assertEqual(r["ltx2"]["features_off"], ["dur: model (duration head)"])
        self.assertEqual({m.get("feature") for m in r["ltx2"]["missing"]},
                         set(r["ltx2"]["features_off"]))
        self.assertEqual(r["ltx2"]["missing"][0]["passes"], ["final", "proxy"])
        fl = r["minimax_h3_fl2va"]
        self.assertEqual(fl["status"], "degraded")
        self.assertEqual([(m["tier"], m["passes"]) for m in fl["missing"]],
                         [("accelerator", ["proxy"])])
        self.assertEqual(fl["by_pass"]["proxy"]["loras"]["how"], "base")
        self.assertEqual(fl["resolved"]["loras"]["how"], "exact")        # the final pass
        ti = r["wan22_ti2v"]
        self.assertEqual(ti["status"], "not_ready")
        self.assertEqual(ti["missing"][0]["url"], "https://huggingface.co/Comfy-Org/"
                         "Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/vae/"
                         "wan2.2_vae.safetensors")
        self.assertEqual(r["wan22_i2v"]["status"], "ready")                # other VAE
        self.assertEqual(self.ready(None, error="down")["krea2"],
                         {"status": "unknown", "missing": [], "resolved": {}, "by_pass": {},
                          "features_off": [], "nodes_missing": [], "error": "down"})

    def test_nodes(self):
        r = self.ready(fake_object_info(drop_nodes={"LTXVDurationPredictor", "H3SaveShot"}))
        self.assertEqual(r["ltx2"]["nodes_missing"], ["H3SaveShot", "LTXVDurationPredictor"])
        self.assertEqual(r["ltx2"]["status"], "not_ready")                   # H3SaveShot
        r = self.ready(fake_object_info(drop_nodes={"LTXVDurationPredictor"}))
        self.assertEqual((r["ltx2"]["status"], r["ltx2"]["features_off"]),
                         ("degraded", ["dur: model (duration head)"]))
        self.assertEqual(r["wan22_vace"]["status"], "ready")
        r = self.ready(fake_object_info(drop_nodes={"WanVaceToVideo"}))
        self.assertEqual(r["wan22_vace"]["nodes_missing"], ["WanVaceToVideo"])
        # the workflow's own classes, as a job keeps them (pruned, saver in place)
        need = E.target_nodes(TG.load_target("ltx2_ingredients"))
        self.assertIn("H3SaveShot", need)
        self.assertNotIn("SaveVideo", need)
        self.assertNotIn("TextGenerateLTX2Prompt", need)                    # pruned away

    def test_route(self):
        info = fake_object_info(drop_files={"ltx-2.5-duration-head-bf16.safetensors"})

        class C:
            def object_info(self):
                return info
        with tempfile.TemporaryDirectory() as user:
            A._READY.clear()
            ctx = A.Context(user, "http://127.0.0.1:9", comfy=C(), env={}, model_resolve=None)
            code, data = A.get_targets(ctx, {"ready": "1", "kind": "video"})
            self.assertEqual(code, 200, data)
            by = {t["id"]: t for t in data["targets"]}
            self.assertEqual(set(by), {t.id for t in TG.list_targets("video")})
            self.assertEqual(by["ltx2"]["readiness"]["status"], "degraded")
            self.assertEqual(by["ltx2"]["models"]["duration_head"]["tier"], "optional")
            self.assertEqual(by["minimax_h3_ref2va"]["presets"]["final"]["base"]["steps"], 20)
            # cached: a second call doesn't ask again
            info.clear()
            code, data = A.get_targets(ctx, {"ready": "1"})
            self.assertEqual({t["readiness"]["status"] for t in data["targets"]
                              if t["id"] == "ltx2"}, {"degraded"})
            # without ready=1: no readiness, nothing asked
            code, data = A.get_targets(ctx, {})
            self.assertNotIn("readiness", data["targets"][0])
            self.assertEqual(A.get_targets(ctx, {"ready": "maybe"})[0], 400)
            A._READY.clear()

    def test_cli_output(self):
        info = fake_object_info(drop_files={"ltx-2.5-duration-head-bf16.safetensors",
                                            "wan2.2_fun_vace_low_noise_14B_fp8_scaled.safetensors"})
        buf = io.StringIO()
        with mock.patch.object(J.Comfy, "object_info", return_value=info), \
                contextlib.redirect_stdout(buf):
            rc = E.cmd_targets(None, [])
        out = buf.getvalue()
        self.assertEqual(rc, 0)
        lines = out.splitlines()
        self.assertIn("  ltx2                 degraded   missing 1 optional", lines)
        self.assertIn("  wan22_vace           not_ready  missing 1 required", lines)
        self.assertIn("  krea2                ready     ", lines)
        self.assertIn("      optional    duration_head ltx-2.5-duration-head-bf16.safetensors"
                      "  [feature off: dur: model (duration head)]", lines)
        self.assertIn("                  -> models/model_patches/   https://huggingface.co/"
                      "Lightricks/LTX-2.5/resolve/main/model_patches/"
                      "ltx-2.5-duration-head-bf16.safetensors", lines)
        # no record: no URL, what to search for instead
        self.assertTrue(any(line.startswith("                  -> models/diffusion_models/   "
                                            "no URL: ") for line in lines), out)
        buf = io.StringIO()
        with mock.patch.object(J.Comfy, "object_info", return_value=info), \
                contextlib.redirect_stdout(buf):
            E.cmd_targets(None, ["--json", "--kind", "video"])
        data = json.loads(buf.getvalue())
        self.assertEqual(data["targets"]["ltx2"]["status"], "degraded")
        self.assertNotIn("krea2", data["targets"])
        buf = io.StringIO()
        with mock.patch.object(J.Comfy, "object_info", side_effect=OSError("refused")), \
                contextlib.redirect_stdout(buf):
            rc = E.cmd_targets(None, [])
        self.assertEqual(rc, 1)
        self.assertIn("didn't answer", buf.getvalue())
        self.assertIn("  ltx2                 unknown", buf.getvalue())


# ---------------------------------------------------------------------------
# the data
# ---------------------------------------------------------------------------

class DownloadsDataTest(unittest.TestCase):
    def test_every_named_file_has_a_download_entry(self):
        for t in TG.list_targets():
            named = t.named_files()
            self.assertTrue(named, t.id)
            self.assertEqual(set(named) - set(t.downloads), set(), t.id)
            for name, d in t.downloads.items():
                self.assertEqual(set(d), {"folder", "url", "source"}, (t.id, name))
                self.assertTrue(d["folder"], (t.id, name))
                self.assertTrue(d["source"], (t.id, name))
                if d["url"] is not None:
                    self.assertTrue(d["url"].startswith("https://huggingface.co/"), (t.id, name))
                    self.assertTrue(d["url"].endswith("/" + name), (t.id, name))
                    self.assertRegex(d["source"], r"^(ComfyUI template |saved workflow |"
                                                  r"ComfyUI-Manager model list )")
                else:
                    self.assertIn("Search", d["source"], (t.id, name))
        # the LTX duration head's URL comes from ComfyUI-Manager's list
        d = TG.load_target("ltx2").downloads["ltx-2.5-duration-head-bf16.safetensors"]
        self.assertEqual(d["folder"], "model_patches")
        self.assertIn("ComfyUI-Manager model list", d["source"])

    def test_tiers_and_bases(self):
        for t in TG.list_targets():
            for param, m in t.models.items():
                self.assertIn(m["tier"], TG.TIERS, (t.id, param))
                self.assertTrue(m["folder"], (t.id, param))
                if m["tier"] == "optional":
                    self.assertTrue(m["feature"], (t.id, param))
            accel = [p for p, m in t.models.items() if m["tier"] == "accelerator"]
            for pass_, p in t.presets.items():
                if accel:
                    self.assertIsNotNone(p.base, (t.id, pass_))
                    # the base never reaches a shotlist
                    self.assertNotIn("base", p.extra)
        self.assertEqual({t.id for t in TG.list_targets()
                          if any(m["tier"] == "accelerator" for m in t.models.values())},
                         {"minimax_h3_ref2va", "minimax_h3_fl2va", "wan22_i2v", "ltx2_ingredients",
                          "flux2_klein", "flux2_klein_edit", "ltx2_voice"})
        self.assertEqual(TG.load_target("ltx2").models["duration_head"]["tier"], "optional")


# ---------------------------------------------------------------------------
# the episode target
# ---------------------------------------------------------------------------

class EpisodeTargetTest(ApiTest):
    def test_precedence_and_route(self):
        data, by = self.status()
        self.assertEqual((data["target"], data["target_source"], data["series_target"]),
                         ("minimax_h3_ref2va", "default", None))
        self.assertEqual(by["sh010"]["target_source"], "default")
        self.assertEqual(by["sh320"]["target_source"], "script")       # target: line
        got = self.ok(A.put_episode_target(self.ctx, {"ep": self.ep, "target": "ltx2"}))
        self.assertEqual(got, {"target": "ltx2", "target_source": "editor",
                               "series_target": None})
        self.assertEqual(self.events_of("h3pipe.episode")[-1], {"ep": self.ep})
        ov = T.load_overrides(self.ep)
        self.assertEqual(ov["episode"], {"id": "ks01", "target": "ltx2"})
        data, by = self.status()
        self.assertEqual((data["target"], data["target_source"]), ("ltx2", "editor"))
        self.assertEqual((by["sh010"]["target"], by["sh010"]["target_source"],
                          by["sh010"]["built_target"]), ("ltx2", "episode", "minimax_h3_ref2va"))
        self.assertNotIn("retarget_error", by["sh010"])
        # a script target (a line or a profile) beats the episode target
        self.assertEqual((by["sh320"]["target"], by["sh320"]["target_source"]),
                         ("minimax_h3_ref2va", "script"))
        self.assertEqual((by["sh330"]["target"], by["sh330"]["target_source"]),
                         ("minimax_h3_ref2va", "script"))
        # a shot override beats it; the built target pins while it is set
        self.ok(A.put_override(self.ctx, {"ep": self.ep, "pass": "proxy", "shot": "sh020",
                                          "fields": {"target": "minimax_h3_ref2va"}}))
        self.assertEqual(T.shot_target(T.load_overrides(self.ep), "sh020"), "minimax_h3_ref2va")
        data, by = self.status()
        self.assertEqual((by["sh020"]["target"], by["sh020"]["target_source"]),
                         ("minimax_h3_ref2va", "override"))
        detail = self.ok(A.get_shot(self.ctx, {"ep": self.ep, "pass": "proxy", "shot": "sh020"}))
        self.assertEqual(detail["target_source"], "override")
        detail = self.ok(A.get_shot(self.ctx, {"ep": self.ep, "pass": "proxy", "shot": "sh010"}))
        self.assertEqual((detail["target"], detail["target_source"]), ("ltx2", "episode"))
        # null clears the pin: back to the episode target
        self.ok(A.put_override(self.ctx, {"ep": self.ep, "pass": "proxy", "shot": "sh020",
                                          "fields": {"target": None}}))
        data, by = self.status()
        self.assertEqual(by["sh020"]["target_source"], "episode")
        # the request beats everything
        doc, i = J.find_shot(self.ep, "proxy", "sh010")
        job = J.plan_job(self.ep, "proxy", doc, i, J.RenderRequest("sh010", target="wan22_ti2v"),
                         T.load_overrides(self.ep))
        self.assertEqual((job.target, job.target_source), ("wan22_ti2v", "request"))
        job = J.plan_job(self.ep, "proxy", doc, i, J.RenderRequest("sh010"),
                         T.load_overrides(self.ep))
        self.assertEqual((job.target, job.target_source, job.retargeted), ("ltx2", "episode", True))
        # rendering compiles the shot for the episode target (the retarget path)
        out = self.render("sh010")
        self.assertEqual([q["target"] for q in out["queued"]], ["ltx2"], out)
        sc = T.list_takes(self.ep, "proxy", "sh010")[0].sidecar
        self.assertEqual((sc["target"], sc["built_target"]), ("ltx2", "minimax_h3_ref2va"))
        # null clears it; the episode's name comes back as it was
        got = self.ok(A.put_episode_target(self.ctx, {"ep": self.ep, "target": None}))
        self.assertEqual(got, {"target": "minimax_h3_ref2va", "target_source": "default",
                               "series_target": None})
        self.assertEqual(T.load_overrides(self.ep)["episode"], "ks01")
        data, by = self.status()
        self.assertEqual((by["sh010"]["target"], by["sh010"]["target_source"]),
                         ("minimax_h3_ref2va", "default"))
        # without an episode target the built target clears a shot override
        self.ok(A.put_override(self.ctx, {"ep": self.ep, "pass": "proxy", "shot": "sh020",
                                          "fields": {"target": "minimax_h3_ref2va"}}))
        self.assertIsNone(T.shot_target(T.load_overrides(self.ep), "sh020"))

    def test_series_target_and_errors(self):
        p = os.path.join(self.ep, "series.json")
        cfg = json.load(open(p, encoding="utf-8"))
        cfg["series"]["target"] = "minimax_h3_ref2va"
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh)
        before = open(p, encoding="utf-8").read()
        data, _ = self.status()
        self.assertEqual((data["target"], data["target_source"], data["series_target"]),
                         ("minimax_h3_ref2va", "series", "minimax_h3_ref2va"))
        got = self.ok(A.put_episode_target(self.ctx, {"ep": self.ep, "target": "wan22_ti2v"}))
        self.assertEqual(got["series_target"], "minimax_h3_ref2va")
        self.assertEqual(open(p, encoding="utf-8").read(), before)      # never written
        self.err(A.put_episode_target(self.ctx, {"ep": self.ep, "target": "krea2"}), 400)
        self.err(A.put_episode_target(self.ctx, {"ep": self.ep, "target": "nope"}), 400)
        self.err(A.put_episode_target(self.ctx, {"ep": self.ep}), 400)
        self.err(A.put_episode_target(self.ctx, {"ep": self.ep, "target": 3}), 400)

    def test_cli(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = E.cmd_override(self.ep, ["--episode-target", "ltx2"])
        self.assertEqual(rc, 0)
        self.assertIn("episode target: ltx2 (editor)", buf.getvalue())
        self.assertIn('"target": "ltx2"', buf.getvalue())
        self.assertEqual(T.episode_target(T.load_overrides(self.ep)), "ltx2")
        # --target built pins a shot to its build's target while it is set
        with contextlib.redirect_stdout(io.StringIO()):
            E.cmd_override(self.ep, ["sh020", "--target", "built"])
        self.assertEqual(T.shot_target(T.load_overrides(self.ep), "sh020"), "minimax_h3_ref2va")
        with contextlib.redirect_stdout(io.StringIO()):
            E.cmd_override(self.ep, ["--episode-target", "built"])
        self.assertIsNone(T.episode_target(T.load_overrides(self.ep)))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(E.cmd_override(self.ep, ["--episode-target", "nope"]), 2)


# ---------------------------------------------------------------------------
# the user's machine (read-only; skipped unless ComfyUI answers)
# ---------------------------------------------------------------------------

def comfy_up() -> bool:
    try:
        with urllib.request.urlopen(f"{COMFY}/system_stats", timeout=3):
            return True
    except Exception:
        return False


@unittest.skipUnless(comfy_up(), f"no ComfyUI at {COMFY}")
class RealMachineReadinessTest(unittest.TestCase):
    def test_what_this_install_is_missing(self):
        info = J.Comfy(COMFY).object_info()               # read-only
        r = E.readiness(TG.list_targets(), info, J.model_resolver(),
                        TG.modelid.temp_cache())
        print(f"\n  readiness of {COMFY}:")
        for tid, v in r.items():
            print(E.ready_line(tid, v))
            for m in v["missing"]:
                print(f"      {m['tier']:<11} {m['param']:<13} {m['want']} -> models/{m['folder']}/"
                      f"  {m['url'] or 'no URL: ' + m['source']}")
            for c in v["nodes_missing"]:
                print(f"      node        {c}")
            self.assertIn(v["status"], E.READY_STATUSES)
            self.assertNotEqual(v["status"], "unknown")


if __name__ == "__main__":
    unittest.main()
