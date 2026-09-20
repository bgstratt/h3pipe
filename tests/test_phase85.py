"""
Phase 8.5 (docs/API.md "Phase 8.5"): the image targets (graphs against a trimmed
/object_info, model families from real headers), keyframes as needed refs
(listing, the prompt, generate with an edit target), Clear, negatives (the
precedence, and that the text reaches Wan's and LTX's graphs), refs_used, and
the small items (length_estimated, model_low).
"""
from __future__ import annotations

import json
import os
import random
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
import h3edit as E  # noqa: E402
import h3jobs as J  # noqa: E402
import h3refs as R  # noqa: E402
import h3takes as T  # noqa: E402
import targets as TG  # noqa: E402
from h3core import story as S  # noqa: E402
from targets.image import common as IC  # noqa: E402
from test_render import FIXTURE, FakeComfy, png_bytes  # noqa: E402

MIXED = os.path.join(HERE, "fixtures", "mixed")
OBJECT_INFO = json.load(open(os.path.join(HERE, "fixtures", "workflows",
                                          "object_info_image.json"), encoding="utf-8"))
OBJECT_INFO_WAN = json.load(open(os.path.join(HERE, "fixtures", "workflows",
                                              "object_info_wan.json"), encoding="utf-8"))
OBJECT_INFO_LTX = json.load(open(os.path.join(HERE, "fixtures", "workflows",
                                              "object_info_ltx.json"), encoding="utf-8"))
IMAGE_TARGETS = ("z_image_turbo", "flux2_klein", "flux2_klein_edit", "flux_kontext")
COMFY_MODELS = os.environ.get("COMFYUI_MODELS", r"C:\AI\ComfyUI\ComfyUI\models")

_BUILT: dict = {}


def built(fixture: str, name: str) -> str:
    """A built episode of a fixture (both passes), made once."""
    if name not in _BUILT:
        tmp = tempfile.mkdtemp(prefix="h3p85_")
        ep = os.path.join(tmp, name)
        os.makedirs(ep)
        shutil.copy(os.path.join(fixture, "series.json"), ep)
        shutil.copy(os.path.join(fixture, "script.md"), os.path.join(ep, f"{name}.md"))
        r = E.build_episode(ep)
        assert r["ok"], r
        _BUILT[name] = ep
    return _BUILT[name]


def tearDownModule():
    for ep in _BUILT.values():
        shutil.rmtree(os.path.dirname(ep), ignore_errors=True)
    _BUILT.clear()                           # another module (test_phase86) may build again


def write_png(path: str, w: int = 64, h: int = 64, rgb=(10, 20, 30)) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(png_bytes(w, h, rgb))
    return path


class Episode(unittest.TestCase):
    fixture, name = FIXTURE, "ks01"

    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.ep = os.path.join(self._t.name, self.name)
        shutil.copytree(built(self.fixture, self.name), self.ep)
        self.s = R.load_series(self.ep)
        self.comfy = None

    def tearDown(self):
        if self.comfy:
            self.comfy.close()
        self._t.cleanup()

    def fake(self) -> FakeComfy:
        if self.comfy is None:
            self.comfy = FakeComfy()
        return self.comfy

    def episode_target(self, tid):
        E.set_episode_target(self.ep, tid)

    def rewrite_script(self, old: str, new: str):
        md = os.path.join(self.ep, f"{self.name}.md")
        text = open(md, encoding="utf-8").read()
        self.assertIn(old, text)
        with open(md, "w", encoding="utf-8") as fh:
            fh.write(text.replace(old, new, 1))
        r = E.build_episode(self.ep)
        self.assertTrue(r["ok"], r)
        self.s = R.load_series(self.ep)

    def pick_views(self, subject: str, view: str = "01_threequarter"):
        ref = R.find_ref(self.s, f"subject:{subject}")
        t = R.import_take(self.s, ref, view, write_png(os.path.join(self._t.name, f"{subject}.png")))
        R.pick_take(self.s, ref, view, t.take)

    def live(self, rel: str):
        write_png(os.path.join(self.s.home, rel))


# ---------------------------------------------------------------------------
# the image targets
# ---------------------------------------------------------------------------

class ImageTargetsTest(Episode):
    def job(self, tid, ref="location:kitchen"):
        (job,) = R.plan_generate(self.s, R.GenRequest(ref, target=tid), rng=random.Random(1))
        return job

    def test_described(self):
        for tid in IMAGE_TARGETS:
            t = TG.load_target(tid, "image")
            d = t.describe()
            self.assertIn(d["capabilities"]["mode"], ("t2i", "edit"), tid)
            self.assertEqual(set(d["models"]), set(t.models), tid)
            for param, m in d["models"].items():
                self.assertTrue(m["family"] and m["tier"], (tid, param))
            # every file it names has a download record (url or null + source)
            self.assertEqual(set(t.named_files()) - set(t.downloads), set(), tid)
            self.assertTrue(t.binding.workflow_name.startswith("h3pipe_"), tid)
        self.assertEqual(TG.load_target("flux2_klein_edit", "image").capabilities()["max_refs"], 4)
        self.assertEqual(TG.load_target("flux_kontext", "image").capabilities()["max_refs"], 1)

    def test_every_graph_passes_object_info(self):
        for tid in IMAGE_TARGETS:
            t = TG.load_target(tid, "image")
            base = J.load_graph(t.binding.workflow)
            job = self.job(tid)
            g = R.image_graph(base, job, os.path.join(self._t.name, "x_t01.json"))
            self.assertEqual(J.check_graph(g, OBJECT_INFO), [], tid)
            classes = {v["class_type"] for v in g.values()}
            self.assertIn("H3SaveRefTake", classes, tid)
            self.assertNotIn("SaveImage", classes, tid)
            self.assertNotIn("LoadImage", classes, tid)      # no references: t2i
            prompt = next(v for v in g.values() if v["class_type"] == "CLIPTextEncode"
                          and v["_meta"]["title"] == "Positive prompt")
            self.assertEqual(prompt["inputs"]["text"], job.prompt)
            sizes = [v["inputs"] for v in g.values()
                     if v["class_type"] in ("EmptySD3LatentImage", "EmptyFlux2LatentImage")]
            self.assertEqual([(x["width"], x["height"]) for x in sizes], [(1344, 768)], tid)
            # a LoRA splices in after the UNETLoader
            job.loras = [{"name": "style.safetensors", "strength": 0.5}]
            g = R.image_graph(base, job, None)
            lo = [v for v in g.values() if v["class_type"] == "LoraLoaderModelOnly"]
            self.assertEqual([x["inputs"]["lora_name"] for x in lo], ["style.safetensors"], tid)
            self.assertEqual(J.check_graph(g, OBJECT_INFO), [], tid)

    def test_edit_reference_chains(self):
        for tid, names, want in (("flux2_klein_edit", ["a.png", "b.png", "c.png"], 3),
                                 ("flux2_klein_edit", ["a.png"], 1),
                                 ("flux_kontext", ["a.png", "b.png"], 1)):
            t = TG.load_target(tid, "image")
            job = self.job(tid)
            job.inputs = {"references": [f"h3pipe/{n}" for n in names]}
            g = R.image_graph(J.load_graph(t.binding.workflow), job, None)
            self.assertEqual(J.check_graph(g, OBJECT_INFO), [], (tid, names))
            loads = [v["inputs"]["image"] for v in g.values() if v["class_type"] == "LoadImage"]
            self.assertEqual(loads, [f"h3pipe/{n}" for n in names[:want]], (tid, names))
            refl = [k for k, v in g.items() if v["class_type"] == "ReferenceLatent"]
            self.assertEqual(len(refl), want * (2 if tid == "flux2_klein_edit" else 1))
        # Klein: the chain runs positive -> ref 1 -> ref 2 -> ... -> the guider
        job = self.job("flux2_klein_edit")
        job.inputs = {"references": ["h3pipe/a.png", "h3pipe/b.png"]}
        g = R.image_graph(J.load_graph(TG.load_target("flux2_klein_edit", "image")
                                       .binding.workflow), job, None)
        guider = next(v for v in g.values() if v["class_type"] == "CFGGuider")["inputs"]
        last_pos = g[guider["positive"][0]]
        self.assertEqual(last_pos["_meta"]["title"], "Reference 2 positive")
        first_pos = g[last_pos["inputs"]["conditioning"][0]]
        self.assertEqual(first_pos["_meta"]["title"], "Reference 1 positive")
        self.assertEqual(g[first_pos["inputs"]["conditioning"][0]]["_meta"]["title"],
                         "Positive prompt")

    def test_resolution_by_family(self):
        t = TG.load_target("flux2_klein_edit", "image")
        installed = {"diffusion_models": ["flux-2-klein-9b-kv-fp8.safetensors"],
                     "text_encoders": ["qwen_3_8b_fp8mixed.safetensors"],
                     "vae": ["flux2-vae.safetensors"]}
        job = self.job("flux2_klein_edit")
        missing = R.resolve_job_models(job, lambda spec: installed.get(spec["folder"]))
        self.assertEqual(missing, [])
        self.assertEqual(job.model, "flux-2-klein-9b-kv-fp8.safetensors")
        self.assertEqual(job.resolved["model"]["how"], "family")
        # only the base model: the accelerator falls back to `base` (20 steps, cfg 5)
        installed["diffusion_models"] = ["flux-2-klein-base-9b-fp8.safetensors"]
        job = self.job("flux2_klein_edit")
        self.assertEqual(R.resolve_job_models(job, lambda spec: installed.get(spec["folder"])),
                         [])
        self.assertEqual((job.model, job.steps, job.cfg, job.base),
                         ("flux-2-klein-base-9b-fp8.safetensors", 20, 5.0, True))
        # no text encoder: blocked, naming the download
        installed["text_encoders"] = []
        job = self.job("flux2_klein_edit")
        (m,) = R.resolve_job_models(job, lambda spec: installed.get(spec["folder"]))
        self.assertEqual((m["param"], m["want"]), ("text_encoder", "qwen_3_8b_fp8mixed.safetensors"))
        self.assertTrue(m["url"].startswith("https://huggingface.co/"))
        self.assertTrue(t.downloads)

    def test_series_config_and_override_choose_the_target(self):
        self.assertEqual(self.job(None).target.id, "krea2")
        cfg = json.load(open(os.path.join(self.ep, "series.json"), encoding="utf-8"))
        cfg["refs"] = {"target": "z_image_turbo", "keyframe_target": "flux_kontext"}
        with open(os.path.join(self.ep, "series.json"), "w", encoding="utf-8") as fh:
            json.dump(cfg, fh)
        self.s = R.load_series(self.ep)
        self.assertEqual(self.job(None).target.id, "z_image_turbo")
        d = R.image_defaults(self.s)
        self.assertEqual((d["target"], d["target_source"], d["keyframe_target"],
                          d["keyframe_target_source"]),
                         ("z_image_turbo", "series", "flux_kontext", "series"))
        # the episode's choice beats the series config's
        R.set_image_defaults(self.ep, {"target": "flux2_klein"})
        self.assertEqual(self.job(None).target.id, "flux2_klein")
        # a per-ref override beats both, a request beats that
        ov = R.load_overrides(self.s.home)
        R.set_ref_override(ov, R.find_ref(self.s, "location:kitchen"), None,
                           {"target": "z_image_turbo"}, None)
        R.save_overrides(self.s.home, ov)
        self.assertEqual(self.job(None).target.id, "z_image_turbo")
        self.assertEqual(self.job("flux_kontext").target.id, "flux_kontext")
        with self.assertRaises(R.RefError):
            self.job("no_such_target")
        R.set_image_defaults(self.ep, {"target": None})
        self.assertEqual(T.episode_field(T.load_overrides(self.ep), "refs_target"), None)


class ModelIdRealFilesTest(unittest.TestCase):
    """The signatures against the dev machine's installed files (skipped
    where they aren't)."""
    FILES = {
        "diffusion_models/flux-2-klein-9b-kv.safetensors": "flux2-klein-9b",
        "diffusion_models/flux1-dev-kontext_fp8_scaled.safetensors": "flux1-kontext-dev",
        "diffusion_models/flux1-kontext-dev.safetensors": "flux1-kontext-dev",
        "diffusion_models/z_image_turbo_bf16.safetensors": "z-image-turbo",
        "text_encoders/qwen_3_8b_fp8mixed.safetensors": "qwen3-8b",
        "clip/qwen_3_4b.safetensors": "qwen3-4b",
        "clip/qwen3vl_4b_fp8_scaled.safetensors": "qwen3vl-4b",
        "text_encoders/clip_l.safetensors": "clip-l",
        "text_encoders/t5xxl_fp16.safetensors": "t5-xxl",
        "vae/ae.safetensors": "flux1-vae",
        "vae/flux2-vae.safetensors": "flux2-vae",
        "vae/full_encoder_small_decoder.safetensors": "flux2-vae",
        "checkpoints/flux1-schnell-fp8.safetensors": "flux1-schnell",
    }

    def test_headers(self):
        names = TG.family_names()
        seen = 0
        for rel, fam in self.FILES.items():
            p = os.path.join(COMFY_MODELS, *rel.split("/"))
            if not os.path.isfile(p):
                continue
            seen += 1
            with self.subTest(rel):
                self.assertEqual(TG.modelid.identify(p, names)["family"], fam)
        if not seen:
            self.skipTest(f"no model files under {COMFY_MODELS}")

    def test_header_only(self):
        """Only the header is read: a tiny fake with the right header passes."""
        import struct
        head = {"img_in.weight": {"dtype": "BF16", "shape": [4096, 128], "data_offsets": [0, 0]},
                "txt_in.weight": {"dtype": "BF16", "shape": [4096, 12288], "data_offsets": [0, 0]},
                "double_stream_modulation_img.lin.weight": {"dtype": "BF16", "shape": [1],
                                                            "data_offsets": [0, 0]},
                "single_stream_modulation.lin.weight": {"dtype": "BF16", "shape": [1],
                                                        "data_offsets": [0, 0]},
                "double_blocks.7.x": {"dtype": "BF16", "shape": [1], "data_offsets": [0, 0]},
                "single_blocks.23.x": {"dtype": "BF16", "shape": [1], "data_offsets": [0, 0]}}
        raw = json.dumps(head).encode()
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "renamed.safetensors")
            with open(p, "wb") as fh:
                fh.write(struct.pack("<Q", len(raw)) + raw)
            self.assertEqual(TG.modelid.identify(p)["family"], "flux2-klein-9b")


# ---------------------------------------------------------------------------
# keyframes as needed refs
# ---------------------------------------------------------------------------

class ScriptLinesTest(unittest.TestCase):
    def parse(self, body: str):
        text = "= ep01 T\n\n# sq01 kitchen\n" + body
        return S.parse_story(text, {"ada"}, {"ada"})

    def test_first_last_lines(self):
        ep = self.parse("first: generate\n## sh010\nwho: ada\nlast: refs/x.png\nAda waits.\n"
                        "## sh020\nwho: ada\nfirst: NONE\nAda goes.\n")
        sq = ep.sequences[0]
        a, b = sq.shots
        self.assertEqual((sq.first, a.first, a.last, b.first), ("generate", None, "refs/x.png",
                                                               "none"))
        self.assertEqual((a.keyframe("first", sq), b.keyframe("first", sq),
                          b.keyframe("last", sq)), ("generate", "none", None))
        d = ep.to_json()
        self.assertEqual(d["sequences"][0]["first"], "generate")
        self.assertNotIn("first", d["sequences"][0]["shots"][0])
        self.assertEqual(type(ep).from_json(d).to_json(), d)
        with self.assertRaises(S.ScriptError):
            self.parse("## sh010\nwho: ada\nfirst: sometimes\nAda waits.\n")

    def test_unset_leaves_the_ir_alone(self):
        d = self.parse("## sh010\nwho: ada\nAda waits.\n").to_json()
        self.assertNotIn("first", d["sequences"][0])
        self.assertNotIn("last", d["sequences"][0]["shots"][0])


class KeyframeListingTest(Episode):
    fixture, name = MIXED, "mx01"

    def needs(self):
        return R.keyframe_needs(self.ep)

    def test_ltx_shots_list_optional_keyframes(self):
        needs = self.needs()
        ltx = sorted({s for (s, _w), n in needs.items() if n["target"] == "ltx2"})
        self.assertTrue(ltx)
        for (shot, which), n in needs.items():
            self.assertEqual(n["need"], "optional", (shot, which))
            self.assertTrue(n["reads"])
            self.assertIn(n["target"], ("ltx2",))
        # the first shot of a sequence can't continue from anything
        for sq in R.episode_story(self.ep).sequences:
            first = sq.shots[0].id
            if (first, "first") in needs:
                self.assertEqual(needs[(first, "first")]["method"], "generate")
        # the listing carries them before any take exists
        refs = {r["id"]: r for r in R.list_refs(self.ep)}
        some = next(iter(needs))
        r = refs[f"shot:{some[0]}:{some[1]}"]
        self.assertEqual((r["shot"], r["which"], r["need"], r["target"], r["exists"],
                          r["requested"]), (some[0], some[1], "optional", "ltx2", False, False))
        self.assertTrue(r["can_generate"])

    def test_wan_first_is_required_and_script_lines(self):
        self.episode_target("wan22_i2v")
        self.rewrite_script("## sh010\n", "## sh010\nlast: generate\n")
        needs = self.needs()
        wan = {k: n for k, n in needs.items() if n["target"] == "wan22_i2v"}
        firsts = [n for (s, w), n in wan.items() if w == "first"]
        self.assertTrue(firsts)
        self.assertTrue(all(n["need"] == "required" for n in firsts))
        self.assertEqual(needs[("sh010", "last")]["method"], "generate")
        self.assertEqual(needs[("sh010", "last")]["script"], "generate")
        refs = {r["id"]: r for r in R.list_refs(self.ep)}
        self.assertTrue(refs["shot:sh010:last"]["requested"])
        # the second shot of a sequence continues from the first
        sq = R.episode_story(self.ep).sequences[0]
        if len(sq.shots) > 1 and sq.shots[1].id in {s for s, _ in wan}:
            self.assertEqual(needs[(sq.shots[1].id, "first")]["method"], "continuity")
        # `first: none` turns an optional one off, never a required one
        self.rewrite_script("## sh010\nlast: generate\n", "## sh010\nlast: none\nfirst: none\n")
        needs = self.needs()
        self.assertNotIn(("sh010", "last"), needs)
        self.assertEqual(needs[("sh010", "first")]["need"], "required")

    def test_missing_keyframes_filter(self):
        self.episode_target("wan22_i2v")
        miss = R.missing_keyframes(self.s)
        self.assertTrue(miss)
        self.assertTrue(all(n["need"] == "required" for _s, _w, n in miss))
        shot, which, _ = miss[0]
        self.live(f"refs/shots/{shot}/{which}.png")
        self.assertNotIn((shot, which), [(s, w) for s, w, _ in R.missing_keyframes(self.s)])

    def test_script_none_drops_the_keyframe_from_the_entry(self):
        # an ltx2 shot with `first: none` compiles without its first keyframe
        needs = self.needs()
        shot = next(s for (s, w), n in needs.items() if w == "first" and n["target"] == "ltx2")
        self.rewrite_script(f"## {shot}\n", f"## {shot}\nfirst: none\n")
        doc, i = J.find_shot(self.ep, "proxy", shot)
        self.assertEqual(list(doc["shots"][i]["keyframes"]), ["last"])
        self.assertNotIn((shot, "first"), self.needs())


class KeyframePromptTest(Episode):
    def ir(self, shot):
        return R.shot_ir(self.ep, shot)

    def test_first_and_last(self):
        sh, sq = self.ir("sh040")
        first = IC.keyframe_prompt(sh, sq, self.s.series_cfg, "first")
        last = IC.keyframe_prompt(sh, sq, self.s.series_cfg, "last")
        look = self.s.series_cfg["style"]["look"]
        for p in (first, last):
            self.assertIn(look, p)
            self.assertIn(self.s.series_cfg["locations"]["kitchen"]["description"], p)
            self.assertIn(self.s.series_cfg["subjects"]["bo"]["design"], p)
            self.assertIn("close-up", p)
            self.assertNotIn("Left! Left!", p)               # no dialogue text in a still
        self.assertIn("first frame", first)
        self.assertIn("The moment the shot opens", first)
        self.assertIn("Bo leans out of the back door", first)
        self.assertIn("last frame", last)
        self.assertIn("after the action is over", last)
        self.assertIn("completed", last)

    def test_opening_and_closing_sentences(self):
        text = "Ada opens the door. She steps in. She sits down by the fire."
        f = IC.moment(text, "first")
        self.assertIn("Ada opens the door", f)
        self.assertIn("not yet shown: She steps in. She sits down by the fire", f)
        self.assertIn("She sits down by the fire, completed", IC.moment(text, "last"))

    def test_references_are_named(self):
        sh, sq = self.ir("sh020")
        refs = [{"role": "subject", "subject": "ada", "name": "Ada", "kind": "character"},
                {"role": "plate", "location": "kitchen", "name": "kitchen", "kind": "plate"}]
        p = IC.keyframe_prompt(sh, sq, self.s.series_cfg, "first", refs)
        self.assertTrue(p.startswith("Image 1 is Ada: draw this character exactly as in image 1"))
        self.assertIn("Image 2 is the background plate", p)


class KeyframeGenerateTest(Episode):
    def setUp(self):
        super().setUp()
        self.episode_target("ltx2")                       # every shot reads keyframes
        for sid in ("ada", "bo"):
            self.pick_views(sid)
        self.live("refs/props/kettle.png")
        self.live("refs/_bg/kitchen.png")

    def gen(self, ref, **kw):
        c = self.fake()
        return R.queue_generate(self.s, R.GenRequest(ref, **kw), J.Comfy(c.url), None,
                                rng=random.Random(3))

    def test_edit_target_gets_the_references(self):
        out = self.gen("shot:sh020:first", target="flux2_klein_edit")
        self.assertEqual(out["errors"], [])
        (q,) = out["queued"]
        self.assertEqual(q["target"], "flux2_klein_edit")
        g = self.comfy.graphs[-1]
        self.assertEqual(J.check_graph(g, OBJECT_INFO), [])
        loads = [v["inputs"]["image"] for v in g.values() if v["class_type"] == "LoadImage"]
        self.assertEqual(len(loads), 4)                    # ada, bo, the kettle, the plate
        self.assertTrue(all(n.startswith("h3pipe/") for n in loads))
        self.assertEqual(sorted(self.comfy.uploads), sorted(set(loads)))
        prompt = next(v for v in g.values() if v["_meta"].get("title") == "Positive prompt")
        text = prompt["inputs"]["text"]
        self.assertTrue(text.startswith("Image 1 is Ada"))
        self.assertIn("Image 2 is Bo", text)
        self.assertIn("Image 3 is the diner kettle", text)
        self.assertIn("Image 4 is the background plate", text)
        # the shot's render size (ltx2 proxy? no: the final pass), scaled up
        ref = R.find_ref(self.s, "shot:sh020:first")
        sc = R.get_take(ref, None, q["take"]).sidecar
        self.assertEqual(sc["target"], "flux2_klein_edit")
        self.assertEqual([r["role"] for r in sc["references"]],
                         ["subject", "subject", "subject", "plate"])
        self.assertEqual(sc["video_target"], "ltx2")
        rw, rh = sc["render_width"], sc["render_height"]
        self.assertAlmostEqual(sc["width"] / sc["height"], rw / rh, delta=0.05)
        self.assertGreaterEqual(sc["width"] * sc["height"], 786432 * 0.95)

    def test_max_refs(self):
        out = self.gen("shot:sh020:first", target="flux_kontext")
        self.assertEqual(out["errors"], [])
        g = self.comfy.graphs[-1]
        loads = [v["inputs"]["image"] for v in g.values() if v["class_type"] == "LoadImage"]
        # Kontext: one reference (Phase 8.6: composed of Ada over the plate;
        # test_phase86 has the rest)
        self.assertEqual(len(loads), 1)
        self.assertEqual(J.check_graph(g, OBJECT_INFO), [])
        refs = {r["id"]: r for r in R.list_refs(self.ep)}
        ov = R.load_overrides(self.s.home)
        R.set_ref_override(ov, R.find_ref(self.s, "shot:sh020:first"), None,
                           {"target": "flux_kontext"}, None)
        R.save_overrides(self.s.home, ov)
        r = R.ref_json(self.s, R.find_ref(self.s, "shot:sh020:first"))
        self.assertEqual(r["effective"]["target"], "flux_kontext")
        (comp,) = r["edit_refs"]
        self.assertEqual(comp["role"], "composite")
        self.assertEqual([(e["id"], e["role"]) for e in comp["parts"]][0],
                         ("subject:ada", "subject"))
        self.assertTrue(refs["shot:sh020:first"]["can_generate"])

    def test_default_keyframe_target_and_t2i(self):
        # nothing to say it isn't ready: the default keyframe target is Klein edit
        r = R.ref_json(self.s, R.find_ref(self.s, "shot:sh030:first"))
        self.assertEqual(r["effective"]["target"], "flux2_klein_edit")
        # not ready: the refs target (krea2), text to image
        out = R.queue_generate(self.s, R.GenRequest("shot:sh030:first"),
                               J.Comfy(self.fake().url), None, rng=random.Random(1),
                               listing=lambda spec: ["krea2_turbo_fp8_scaled.safetensors",
                                                     "qwen3vl_4b_fp8_scaled.safetensors",
                                                     "wan_2.1_vae.safetensors"])
        self.assertEqual(out["errors"], [])
        self.assertEqual(out["queued"][0]["target"], "krea2")
        # a sheet with no picked view: the panel is cut out of it (needs PIL)
        sh, sq = R.shot_ir(self.ep, "sh050")
        self.live("refs/cy/cy_sheet_4panel.png")
        refs = R.reference_images(self.s, sh, sq, TG.load_target("flux2_klein_edit", "image"))
        cy = next(r for r in refs if r.get("subject") == "cy")
        self.assertEqual(cy["crop"], {"panels": 4, "index": 0})


try:
    import PIL  # noqa: F401
    HAVE_PIL = True
except ImportError:                                      # pragma: no cover
    HAVE_PIL = False


@unittest.skipUnless(HAVE_PIL, "cutting a view out of a sheet needs PIL (ComfyUI's python)")
class SheetCropTest(Episode):
    def test_view_cut_from_a_live_sheet(self):
        self.episode_target("ltx2")
        write_png(os.path.join(self.s.home, "refs", "ada", "ada_sheet_4panel.png"), 256, 64)
        c = self.fake()
        out = R.queue_generate(self.s, R.GenRequest("shot:sh010:first",
                                                    target="flux2_klein_edit"),
                               J.Comfy(c.url), None, rng=random.Random(3))
        self.assertEqual(out["errors"], [])
        (name,) = [v["inputs"]["image"] for v in c.graphs[-1].values()
                   if v["class_type"] == "LoadImage"]
        self.assertIn(name, c.uploads)
        from PIL import Image
        import io
        self.assertEqual(Image.open(io.BytesIO(c.uploads[name])).size, (1024, 1024))


class ContinuityFillTest(Episode):
    def test_continuity_falls_back_to_generate(self):
        self.episode_target("wan22_i2v")
        dry = R.fill_keyframe(self.s, "sh020", "first", pass_="proxy", dry_run=True,
                              target="z_image_turbo")
        # no usable take of sh010 to cut a frame from: a still instead
        self.assertEqual(dry.method, "generate")
        self.assertIn("would generate with z_image_turbo", dry.detail)


# ---------------------------------------------------------------------------
# Clear
# ---------------------------------------------------------------------------

class ClearTest(Episode):
    def test_clear_keyframe_sticks(self):
        ref = R.find_ref(self.s, "shot:sh010:first")
        t = R.import_take(self.s, ref, None, write_png(os.path.join(self._t.name, "k.png")))
        self.assertEqual([p.take.take for p in R.auto_pick(self.s, ref)], [t.take])
        self.assertTrue(os.path.isfile(ref.file))
        res = R.clear_pick(self.s, ref)
        self.assertEqual((res.was, res.removed), (t.take, [ref.file]))
        self.assertFalse(os.path.isfile(ref.file))
        self.assertTrue(os.path.isfile(t.paths.image))     # the take stays
        picks = R.load_picks(ref.home)
        self.assertTrue(R.is_cleared(picks, ref.id))
        self.assertIsNone(R.picked_take(picks, ref.id))
        # auto-pick (GET /refs) doesn't put it back, nor does a new continuity-style take
        self.assertEqual(R.auto_pick(self.s, ref), [])
        R.import_take(self.s, ref, None, write_png(os.path.join(self._t.name, "k2.png")))
        self.assertEqual(R.auto_pick(self.s, ref), [])
        self.assertFalse(os.path.isfile(ref.file))
        # a pick ends the clear
        R.pick_take(self.s, ref, None, t.take)
        self.assertFalse(R.is_cleared(R.load_picks(ref.home), ref.id))
        self.assertTrue(os.path.isfile(ref.file))

    def test_clear_series_ref_and_view(self):
        ref = R.find_ref(self.s, "subject:ada")
        for v in ("01_threequarter", "02_side"):             # two views: no stitch (PIL)
            t = R.import_take(self.s, ref, v, write_png(os.path.join(self._t.name, v + ".png")))
            R.pick_take(self.s, ref, v, t.take)
        res = R.clear_pick(self.s, ref, "02_side")
        self.assertEqual(res.was, 1)
        picks = R.load_picks(ref.home)
        self.assertTrue(R.is_cleared(picks, ref.id, "02_side"))
        self.assertIsNone(R.picked_take(picks, ref.id, "02_side"))
        self.assertFalse(R.is_cleared(picks, ref.id, "01_threequarter"))
        self.assertEqual(R.picked_take(picks, ref.id, "01_threequarter"), 1)
        # auto-pick leaves the cleared view alone
        self.assertEqual(R.auto_pick(self.s, ref), [])
        # Phase 9c-B: a voice can be cleared too, but only one with a file named
        with self.assertRaises(R.RefError):
            R.clear_pick(self.s, R.find_ref(self.s, "voice:rex"))

    def test_cli_clear(self):
        ref = R.find_ref(self.s, "shot:sh010:first")
        t = R.import_take(self.s, ref, None, write_png(os.path.join(self._t.name, "k.png")))
        R.pick_take(self.s, ref, None, t.take)
        self.assertEqual(E.cmd_keyframe(self.ep, ["sh010", "--clear"]), 0)
        self.assertFalse(os.path.isfile(ref.file))
        self.assertTrue(R.is_cleared(R.load_picks(ref.home), ref.id))


# ---------------------------------------------------------------------------
# negatives
# ---------------------------------------------------------------------------

class NegativeTest(Episode):
    fixture, name = MIXED, "mx01"

    def plan(self, sid, tid, **req):
        doc, i = J.find_shot(self.ep, "proxy", sid)
        return J.plan_job(self.ep, "proxy", doc, i, J.RenderRequest(sid, target=tid, **req),
                          T.load_overrides(self.ep))

    def set_negative_override(self, sid, tid, text):
        ov = T.load_overrides(self.ep)
        T.set_shot_target(ov, sid, tid)
        E.set_shot_override(ov, sid, E.pass_entries(self.ep, sid, tid), ["proxy"], None,
                            {"negative": text}, tid)
        T.save_overrides(self.ep, ov)

    def test_precedence(self):
        sid = "sh010"
        job = self.plan(sid, "wan22_i2v")
        preset = TG.load_target("wan22_i2v", "video").presets["proxy"].extra["negative"]
        self.assertEqual((job.negative_source, J.job_values(job)["negative"]),
                         ("preset", preset))
        cfg = json.load(open(os.path.join(self.ep, "series.json"), encoding="utf-8"))
        cfg["negative"] = "series words"
        with open(os.path.join(self.ep, "series.json"), "w", encoding="utf-8") as fh:
            json.dump(cfg, fh)
        job = self.plan(sid, "wan22_i2v")
        self.assertEqual((job.negative_source, J.job_values(job)["negative"]),
                         ("series", "series words"))
        with open(os.path.join(self.ep, "negative.txt"), "w", encoding="utf-8") as fh:
            fh.write("episode words\n")
        job = self.plan(sid, "wan22_i2v")
        self.assertEqual((job.negative_source, J.job_values(job)["negative"]),
                         ("negative.txt", "episode words"))
        self.set_negative_override(sid, "wan22_i2v", "shot words")
        job = self.plan(sid, None)
        self.assertEqual((job.negative_source, J.job_values(job)["negative"]),
                         ("override", "shot words"))
        self.assertIn("negative", job.overridden)
        job = self.plan(sid, None, negative="request words")
        self.assertEqual((job.negative_source, J.job_values(job)["negative"]),
                         ("request", "request words"))
        # a target without a negative (H3) is unaffected
        job = self.plan(sid, "minimax_h3_ref2va")
        self.assertEqual(job.negative_source, "none")
        # the sidecar says where it came from, and that cfg 1 ignores it
        sc = J.sidecar_for(self.plan(sid, None, negative="request words"))
        self.assertEqual(sc["negative_source"], "request")
        self.assertTrue(any("no effect at cfg 1" in n for n in sc["notes"]))
        detail = E.shot_detail(self.ep, "proxy", sid)
        self.assertEqual((detail["effective"]["negative"], detail["effective"]["negative_source"]),
                         ("shot words", "override"))

    def test_reaches_wan_and_ltx_graphs(self):
        with open(os.path.join(self.ep, "negative.txt"), "w", encoding="utf-8") as fh:
            fh.write("no blur")
        for tid, oi, title in (("wan22_i2v", OBJECT_INFO_WAN, "Negative prompt"),
                               ("ltx2", OBJECT_INFO_LTX, None)):
            job = self.plan("sh020", tid)
            t = TG.load_target(tid, "video")
            take = T.Take("sh020", 1, "proxy", T.take_paths(self.ep, "proxy", "sh020", 1))
            g = J.graph_for(J.load_graph(t.binding.workflow), job, take,
                            inputs={"first": "h3pipe/f.png"})
            self.assertEqual(J.check_graph(g, oi), [], tid)
            if title:
                neg = next(v for v in g.values() if v["_meta"].get("title") == title)
                # and it is what the samplers' conditioning reads
                i2v = next(v for v in g.values() if v["class_type"].startswith("Wan"))
                self.assertEqual(g[i2v["inputs"]["negative"][0]], neg)
            else:
                (nid,) = J.select_nodes(g, t.binding.specs("negative")[0])
                neg = g[nid]
            self.assertEqual(neg["inputs"]["text"], "no blur", tid)
            self.assertEqual(J.frozen_shotlist(job)["shots"][0]["negative"], "no blur")

    def test_image_negative(self):
        with open(os.path.join(self.ep, "negative.txt"), "w", encoding="utf-8") as fh:
            fh.write("no text")
        (job,) = R.plan_generate(self.s, R.GenRequest("location:kitchen", target="z_image_turbo"))
        self.assertEqual((job.negative, job.negative_source), ("no text", "negative.txt"))
        self.assertTrue(any("no effect at cfg 1" in n for n in job.notes))
        (job,) = R.plan_generate(self.s, R.GenRequest("location:kitchen", target="z_image_turbo",
                                                      negative="kreagen file"))
        self.assertEqual((job.negative, job.negative_source), ("kreagen file", "request"))
        g = R.image_graph(J.load_graph(job.target.binding.workflow), job, None)
        neg = next(v for v in g.values() if v["_meta"].get("title") == "Negative prompt")
        self.assertEqual(neg["inputs"]["text"], "kreagen file")
        (job,) = R.plan_generate(self.s, R.GenRequest("location:kitchen",
                                                      target="flux2_klein_edit"))
        self.assertEqual(job.negative_source, "none")


# ---------------------------------------------------------------------------
# the inspector and the small items
# ---------------------------------------------------------------------------

class InspectorTest(Episode):
    fixture, name = MIXED, "mx01"

    def test_refs_used(self):
        # an H3 shot: subjects and the plate
        d = E.shot_detail(self.ep, "proxy", "sh010")
        roles = [(r["role"], r["id"]) for r in d["refs_used"]]
        self.assertIn(("plate", "location:kitchen"), roles)
        self.assertTrue(any(role == "subject" for role, _ in roles))
        for r in d["refs_used"]:
            self.assertEqual(set(r) - {"slot"}, {"id", "kind", "role", "path", "exists",
                                                 "need", "thumb"})
            self.assertIsNone(r["thumb"])                   # nothing on disk yet
        # an LTX shot: its optional keyframes
        d = E.shot_detail(self.ep, "proxy", "sh020")
        kf = {r["role"]: r for r in d["refs_used"] if r["role"] in ("first", "last")}
        self.assertEqual(kf["first"]["id"], "shot:sh020:first")
        self.assertEqual(kf["first"]["need"], "optional")
        write_png(os.path.join(self.ep, "refs", "shots", "sh020", "first.png"))
        d = E.shot_detail(self.ep, "proxy", "sh020")
        kf = {r["role"]: r for r in d["refs_used"] if r["role"] == "first"}
        self.assertEqual(kf["first"]["thumb"], "refs/shots/sh020/first.png")
        self.assertTrue(kf["first"]["exists"])
        # Wan I2V: the first frame is required
        E.set_episode_target(self.ep, "wan22_i2v")
        d = E.shot_detail(self.ep, "proxy", "sh010")
        kf = {r["role"]: r for r in d["refs_used"] if r["role"] == "first"}
        self.assertEqual(kf["first"]["need"], "required")

    def test_reference_image_and_length_estimated(self):
        self.assertIsNone(E.reference_image(self.ep, {"refs": []}))
        p = write_png(os.path.join(self.ep, "renders_proxy", "sh020", "sh020_t01_refsheet.png"))
        self.assertEqual(E.reference_image(self.ep, {"refs": [
            {"role": "sheet", "path": "renders_proxy/sh020/sh020_t01_refsheet.png"}]}),
            "renders_proxy/sh020/sh020_t01_refsheet.png")
        self.assertTrue(os.path.isfile(p))
        st = E.episode_status(self.ep, "proxy")
        for s in st["shots"]:
            self.assertNotIn("length_estimated", s)        # no `dur: model` in mixed

    def test_model_low_override(self):
        ov = T.load_overrides(self.ep)
        T.set_shot_target(ov, "sh010", "wan22_i2v")
        E.set_shot_override(ov, "sh010", E.pass_entries(self.ep, "sh010", "wan22_i2v"),
                            ["proxy"], None, {"model_low": "my_low.safetensors"}, "wan22_i2v")
        T.save_overrides(self.ep, ov)
        doc, i = J.find_shot(self.ep, "proxy", "sh010")
        job = J.plan_job(self.ep, "proxy", doc, i, J.RenderRequest("sh010"), ov)
        self.assertEqual(J.job_values(job)["model_low"], "my_low.safetensors")
        self.assertIn("model_low", job.overridden)
        self.assertEqual(J.frozen_shotlist(job)["shots"][0]["model_low"], "my_low.safetensors")
        d = E.shot_detail(self.ep, "proxy", "sh010")
        self.assertEqual(d["effective"]["model_low"], "my_low.safetensors")


if __name__ == "__main__":
    unittest.main()
