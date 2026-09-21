"""
Wardrobe variants (docs/PLAN.md, Phase 10): a subject with `of: <subject>` is
the same character or object in a different state, with its own reference
sheet.

Four things, in the order the pipeline meets them: the loader resolves a
variant into an ordinary subject entry; the parser gives the base's lines to
the variant on screen; the refs listing gives it a sheet but no voice; and a
built shot keeps `fully_preserved` against the variant's own picture, with the
variant's words on the targets that have no pictures at all.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import random  # noqa: E402
import h3refs as R  # noqa: E402
from test_render import png_bytes  # noqa: E402
from h3core.series_config import (character_ids, load_series_config,  # noqa: E402
                                  series_config_from, series_info, subject_ids,
                                  variant_of)
from h3core.story import ScriptError, parse_story  # noqa: E402

FIXTURE = os.path.join(HERE, "fixtures", "kitchen_sink")

BASE = {"kind": "character", "name": "Gina", "pronoun": "her",
        "design": "a woman in a red coat",
        "sheet": "refs/gina/gina_sheet_4panel.png",
        "voice": "low and dry", "voice_sample": "audio/voices/gina.wav"}
TOWEL = {"of": "gina", "design": "Gina in a white bath towel, hair wet"}


def cfg_with(subjects: dict) -> dict:
    return series_config_from({"series": {"id": "t", "title": "T", "fps": 24},
                               "style": {"look": "a flat cartoon"},
                               "subjects": subjects,
                               "locations": {"bath": {"description": "a steamy bathroom",
                                                      "plate": "refs/_bg/bath.png"}}})


def parse(text: str, cfg: dict):
    return parse_story(text, subject_ids(cfg), character_ids(cfg), series_info(cfg),
                       variant_of(cfg))


def script(*body: str) -> str:
    return "= ep01  T\n\n# sq01  bath\n\n## sh010\n" + "\n".join(body) + "\n"


class LoaderTest(unittest.TestCase):
    """series_config: `of:` resolved into a complete subject entry."""

    def test_inherits_everything_but_the_sheet(self):
        cfg = cfg_with({"gina": dict(BASE), "gina_towel": dict(TOWEL)})
        v = cfg["subjects"]["gina_towel"]
        self.assertEqual(v["name"], "Gina")
        self.assertEqual(v["kind"], "character")
        self.assertEqual(v["pronoun"], "her")
        self.assertEqual(v["voice"], "low and dry")
        self.assertEqual(v["voice_sample"], "audio/voices/gina.wav")
        # its own, never the base's: the wardrobe is the whole point
        self.assertEqual(v["design"], TOWEL["design"])
        self.assertEqual(v["sheet"], "refs/gina/gina_towel_sheet_4panel.png")
        self.assertEqual(v["of"], "gina")
        self.assertEqual(variant_of(cfg), {"gina_towel": "gina"})
        # the base is untouched
        self.assertEqual(cfg["subjects"]["gina"], BASE)

    def test_the_variant_may_state_its_own_sheet(self):
        cfg = cfg_with({"gina": dict(BASE),
                        "gina_towel": dict(TOWEL, sheet="refs/towel.png")})
        self.assertEqual(cfg["subjects"]["gina_towel"]["sheet"], "refs/towel.png")

    def test_a_variant_may_override_what_it_inherits(self):
        cfg = cfg_with({"gina": dict(BASE),
                        "gina_towel": dict(TOWEL, voice="muffled, from behind a door")})
        self.assertEqual(cfg["subjects"]["gina_towel"]["voice"], "muffled, from behind a door")

    def test_a_prop_variant_works_the_same(self):
        pot = {"kind": "prop", "name": "the pot", "design": "a clean copper pot",
               "sheet": "refs/props/pot.png"}
        cfg = cfg_with({"gina": dict(BASE), "pot": pot,
                        "pot_burnt": {"of": "pot", "design": "the copper pot, scorched black"}})
        v = cfg["subjects"]["pot_burnt"]
        self.assertEqual(v["kind"], "prop")
        self.assertEqual(v["sheet"], "refs/props/pot_burnt.png")

    def test_variants_are_subjects_and_characters(self):
        cfg = cfg_with({"gina": dict(BASE), "gina_towel": dict(TOWEL)})
        self.assertIn("gina_towel", subject_ids(cfg))
        self.assertIn("gina_towel", character_ids(cfg))

    def test_a_series_with_no_variants_is_untouched(self):
        raw = {"gina": dict(BASE)}
        cfg = cfg_with(raw)
        self.assertEqual(cfg["subjects"], raw)
        self.assertEqual(variant_of(cfg), {})

    def test_variant_of_tolerates_an_unloaded_config(self):
        """h3align reads series.json raw, `_note` strings and all."""
        self.assertEqual(variant_of({"subjects": {"_note": "a comment",
                                                  "gina_towel": dict(TOWEL)}}),
                         {"gina_towel": "gina"})

    def _bad(self, subjects: dict) -> str:
        with self.assertRaises(ValueError) as cm:
            cfg_with(subjects)
        return str(cm.exception)

    def test_unknown_base(self):
        msg = self._bad({"gina": dict(BASE), "x": {"of": "nope", "design": "d"}})
        self.assertIn("not a subject", msg)

    def test_no_variant_of_a_variant(self):
        msg = self._bad({"gina": dict(BASE), "a": dict(TOWEL),
                         "b": {"of": "a", "design": "d"}})
        self.assertIn("itself a variant", msg)

    def test_no_variant_of_itself(self):
        msg = self._bad({"gina": dict(BASE), "x": {"of": "x", "design": "d"}})
        self.assertIn("not itself", msg)

    def test_a_variant_needs_its_own_design(self):
        msg = self._bad({"gina": dict(BASE), "x": {"of": "gina"}})
        self.assertIn("needs its own `design`", msg)

    def test_kind_must_match_the_base(self):
        msg = self._bad({"gina": dict(BASE),
                         "x": {"of": "gina", "design": "d", "kind": "prop"}})
        self.assertIn("variant of, is a character", msg)

    def test_a_sheet_that_cannot_be_derived_is_reported(self):
        msg = self._bad({"gina": dict(BASE, sheet="refs/sheets/lady.png"),
                         "x": {"of": "gina", "design": "d"}})
        self.assertIn("give 'x' a `sheet`", msg)

    def test_a_base_with_no_sheet_is_reported(self):
        base = {k: v for k, v in BASE.items() if k != "sheet"}
        msg = self._bad({"gina": base, "x": {"of": "gina", "design": "d"}})
        self.assertIn("no `sheet` to derive one from", msg)


class ParserTest(unittest.TestCase):
    """story: a shot's lines belong to the variant that is in it."""

    def setUp(self):
        self.cfg = cfg_with({"gina": dict(BASE), "gina_towel": dict(TOWEL),
                             "gina_coat": {"of": "gina", "design": "Gina in a navy coat"},
                             "sam": {"kind": "character", "name": "Sam", "design": "a man",
                                     "sheet": "refs/sam/sam_sheet_4panel.png"}})

    def shot(self, *body: str):
        return parse(script(*body), self.cfg).sequences[0].shots[0]

    def test_the_base_name_speaks_for_the_variant(self):
        sh = self.shot("who: gina_towel", "dur: 3.0", "She leans on the doorframe.",
                       "GINA (low): Don't come in.")
        self.assertEqual(sh.cast, ["gina_towel"])
        self.assertEqual(sh.dialogue[0].speaker, "gina_towel")
        self.assertEqual(sh.dialogue[0].delivery, "low")

    def test_a_line_before_the_who_line_binds_too(self):
        """`who:` may come after the lines it re-owns."""
        sh = self.shot("GINA: Don't come in.", "who: gina_towel", "dur: 3.0")
        self.assertEqual(sh.cast, ["gina_towel"])
        self.assertEqual(sh.dialogue[0].speaker, "gina_towel")

    def test_a_voiceover_by_the_variant_on_screen_binds(self):
        sh = self.shot("who: gina_towel", "dur: 3.0", "She stares at the mirror.",
                       "GINA (V.O.): I should have said no.")
        self.assertEqual(sh.cast, ["gina_towel"])
        self.assertEqual(sh.dialogue[0].speaker, "gina_towel")
        self.assertEqual(sh.dialogue[0].mode, "vo")

    def test_other_speakers_are_untouched(self):
        sh = self.shot("who: gina_towel, sam", "dur: 3.0", "They argue.",
                       "SAM: Put something on.", "GINA: Get out.")
        self.assertEqual(sh.cast, ["gina_towel", "sam"])
        self.assertEqual([d.speaker for d in sh.dialogue], ["sam", "gina_towel"])

    def test_the_base_elsewhere_in_the_episode_is_still_the_base(self):
        ep = parse("= ep01  T\n\n# sq01  bath\n\n"
                   "## sh010\nwho: gina_towel\ndur: 3.0\nShe leans out.\nGINA: Don't.\n\n"
                   "## sh020\nwho: gina\ndur: 3.0\nDressed now, she comes out.\nGINA: Better.\n",
                   self.cfg)
        a, b = ep.sequences[0].shots
        self.assertEqual((a.cast, a.dialogue[0].speaker), (["gina_towel"], "gina_towel"))
        self.assertEqual((b.cast, b.dialogue[0].speaker), (["gina"], "gina"))

    def test_a_subject_and_its_variant_in_one_shot_is_an_error(self):
        with self.assertRaises(ScriptError) as cm:
            self.shot("who: gina, gina_towel", "dur: 3.0", "Two of her.")
        self.assertIn("names both 'gina' and its variant 'gina_towel'", cm.exception.msg)

    def test_two_variants_of_one_subject_is_an_error(self):
        with self.assertRaises(ScriptError) as cm:
            self.shot("who: gina_towel, gina_coat", "dur: 3.0", "Two of her.")
        self.assertIn("both variants of 'gina'", cm.exception.msg)

    def test_without_the_variant_map_the_script_parses_as_it_always_did(self):
        """Every caller passes it; a caller that doesn't gets the old parse."""
        sh = parse_story(script("who: gina_towel", "dur: 3.0", "She leans out.",
                                "GINA: Don't come in."),
                         subject_ids(self.cfg), character_ids(self.cfg),
                         series_info(self.cfg)).sequences[0].shots[0]
        self.assertEqual(sh.cast, ["gina_towel", "gina"])
        self.assertEqual(sh.dialogue[0].speaker, "gina")


class RefsTest(unittest.TestCase):
    """h3refs: a sheet of its own, and no voice of its own."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ep = os.path.join(self._tmp.name, "ks01")
        os.makedirs(self.ep)
        shutil.copy(os.path.join(FIXTURE, "series.json"), self.ep)
        shutil.copy(os.path.join(FIXTURE, "script.md"), os.path.join(self.ep, "ks01.md"))
        self.s = R.load_series(self.ep)

    def tearDown(self):
        self._tmp.cleanup()

    def test_a_variant_gets_a_subject_ref_and_no_voice_ref(self):
        refs = {r.id: r for r in R.series_refs(self.s)}
        self.assertIn("subject:ada_wet", refs)
        self.assertNotIn("voice:ada_wet", refs)
        self.assertIn("voice:ada", refs)                 # the base still has one

    def test_the_variant_ref_points_at_its_own_sheet(self):
        ref = {r.id: r for r in R.series_refs(self.s)}["subject:ada_wet"]
        self.assertTrue(ref.file.replace("\\", "/").endswith(
            "refs/ada/ada_wet_sheet_4panel.png"))
        self.assertEqual(ref.kind, "character")

    def test_the_variant_prompt_describes_the_variant(self):
        ref = {r.id: r for r in R.series_refs(self.s)}["subject:ada_wet"]
        prompt = R.built_prompt(self.s, ref, None)
        self.assertIn("soaked through", prompt)
        self.assertNotIn("a mustard apron over a striped shirt", prompt)

    def test_the_sheet_prompt_names_the_base_sheet_to_start_from(self):
        """refs_todo tells whoever makes it where to start (Phase 10b wires
        the edit target; until then the work order says it in words)."""
        ref = {r.id: r for r in R.series_refs(self.s)}["subject:ada_wet"]
        prompt = R.built_prompt(self.s, ref, None)
        self.assertIn("start from the existing sheet at refs/ada/ada_sheet_4panel.png",
                      prompt)
        self.assertIn("This is Ada in a different state", prompt)
        # not on the base, and not in what a text-to-image model is fed
        base = {r.id: r for r in R.series_refs(self.s)}["subject:ada"]
        self.assertNotIn("start from", R.built_prompt(self.s, base, None))
        for view in R.VIEW_TAGS:
            self.assertNotIn("start from", R.built_prompt(self.s, ref, view))

    def test_the_listing_says_whose_variant_it_is(self):
        """It keeps the character's name (that name goes into every prompt), so
        `of` is what tells the two rows apart."""
        j = R.ref_json(self.s, R.find_ref(self.s, "subject:ada_wet"))
        self.assertEqual((j["name"], j["of"]), ("Ada", "ada"))
        self.assertIsNone(R.ref_json(self.s, R.find_ref(self.s, "subject:ada"))["of"])

    def test_a_take_records_what_it_was_drawn_from(self):
        """On a target that does both, the references are the only way to tell
        an edit from a text-to-image generate."""
        ref = R.find_ref(self.s, "subject:ada")
        src = os.path.join(self._tmp.name, "v.png")
        with open(src, "wb") as fh:
            fh.write(png_bytes(64, 64))
        R.import_take(self.s, ref, "01_threequarter", src)
        j = R.ref_json(self.s, ref)
        take = j["views"][0]["takes"][0]
        self.assertIn("references", take)
        self.assertEqual(take["references"], [])        # imported: drawn from nothing

    def test_a_voice_cannot_be_written_onto_a_variant(self):
        with self.assertRaises(R.RefError) as cm:
            R.set_voice_sample(self.s, "ada_wet", "refs/voices/ada_wet.wav")
        self.assertIn("shares its voice", str(cm.exception))


class DerivedViewsTest(unittest.TestCase):
    """Phase 10b: a variant's views come from the base's, not from nothing."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ep = os.path.join(self._tmp.name, "ks01")
        os.makedirs(self.ep)
        shutil.copy(os.path.join(FIXTURE, "series.json"), self.ep)
        shutil.copy(os.path.join(FIXTURE, "script.md"), os.path.join(self.ep, "ks01.md"))
        self.s = R.load_series(self.ep)
        self.base = R.find_ref(self.s, "subject:ada")
        self.var = R.find_ref(self.s, "subject:ada_wet")

    def tearDown(self):
        self._tmp.cleanup()

    def target(self, tid: str):
        import targets as TG
        return TG.load_target(tid, "image")

    def pick_base_view(self, view: str) -> None:
        src = os.path.join(self._tmp.name, "v.png")
        with open(src, "wb") as fh:
            fh.write(png_bytes(64, 64))
        t = R.import_take(self.s, self.base, view, src)
        R.pick_take(self.s, self.base, view, t.take)

    # -- the seed (every target, including the ones that read no references) --

    def test_a_variant_draws_on_the_base_seed(self):
        """The only thing carrying identity on a text-to-image target."""
        self.assertEqual(R.stable_seed(self.var), R.stable_seed(self.base))
        self.assertEqual(R.stable_seed(self.var), R.seed_for("ada"))

    def test_a_plain_subject_keeps_its_own_seed(self):
        self.assertEqual(R.stable_seed(R.find_ref(self.s, "subject:bo")), R.seed_for("bo"))

    # -- the reference an edit target reads -----------------------------------

    def test_a_variant_view_edits_the_bases_same_view(self):
        self.pick_base_view("03_back")
        refs = R.variant_reference_images(self.s, self.var, "03_back",
                                          self.target("flux2_klein_edit"))
        self.assertEqual(len(refs), 1)
        self.assertEqual((refs[0]["subject"], refs[0]["name"], refs[0]["view"]),
                         ("ada", "Ada", "03_back"))
        self.assertTrue(os.path.isfile(refs[0]["path"]))
        self.assertNotIn("crop", refs[0])           # a picked view needs no cropping

    def test_without_a_picked_view_it_crops_the_bases_sheet(self):
        sheet = os.path.join(self.ep, "refs", "ada", "ada_sheet_4panel.png")
        os.makedirs(os.path.dirname(sheet), exist_ok=True)
        with open(sheet, "wb") as fh:
            fh.write(png_bytes(4096, 1024))
        refs = R.variant_reference_images(self.s, self.var, "02_side",
                                          self.target("flux2_klein_edit"))
        self.assertEqual(refs[0]["crop"], {"panels": 4, "index": 1})

    def test_nothing_on_disk_means_no_reference(self):
        self.assertEqual(R.variant_reference_images(self.s, self.var, "03_back",
                                                    self.target("flux2_klein_edit")), [])

    def test_a_subject_that_is_not_a_variant_is_generated_as_it_always_was(self):
        self.pick_base_view("03_back")
        self.assertEqual(R.variant_reference_images(self.s, self.base, "03_back",
                                                    self.target("flux2_klein_edit")), [])

    def test_a_text_to_image_target_reads_none(self):
        self.pick_base_view("03_back")
        self.assertEqual(R.variant_reference_images(self.s, self.var, "03_back",
                                                    self.target("krea2")), [])

    def test_one_reference_target_works_too(self):
        """Kontext takes exactly one, which is all this needs."""
        self.pick_base_view("03_back")
        self.assertEqual(len(R.variant_reference_images(self.s, self.var, "03_back",
                                                        self.target("flux_kontext"))), 1)

    # -- what the job ends up with --------------------------------------------

    def plan(self, ref_id: str, view: str, target: str):
        return R.plan_generate(self.s, R.GenRequest(ref_id, view, target=target),
                               rng=random.Random(0))[0]

    def test_the_job_carries_the_reference_and_asks_for_a_change(self):
        for v in R.VIEW_TAGS:
            self.pick_base_view(v)
        job = self.plan("subject:ada_wet", "03_back", "flux2_klein_edit")
        self.assertEqual([r["name"] for r in job.references], ["Ada"])
        self.assertIn("The reference image is Ada:", job.prompt)
        self.assertIn("seen from directly behind", job.prompt)      # the same view
        self.assertIn("changing only what this description changes", job.prompt)
        self.assertIn("soaked through and clinging", job.prompt)
        self.assertEqual(job.seed, R.seed_for("ada"))

    def test_a_view_is_never_asked_to_keep_a_face_it_cannot_show(self):
        """One `design` sentence serves all four views, so it describes a face
        even for the views that have none. Told to keep "the face exactly as in
        the reference" on a back view, a model turns the character around."""
        for v in R.VIEW_TAGS:
            self.pick_base_view(v)
        back = self.plan("subject:ada_wet", "03_back", "flux2_klein_edit").prompt
        self.assertNotIn("keeping the face", back)
        self.assertIn("the face is NOT visible", back)
        self.assertIn("must not be turned toward the viewer", back)
        side = self.plan("subject:ada_wet", "02_side", "flux2_klein_edit").prompt
        self.assertIn("the profile of the face", side)
        self.assertIn("must stay in profile", side)
        for v in ("01_threequarter", "04_face"):
            front = self.plan("subject:ada_wet", v, "flux2_klein_edit").prompt
            self.assertIn("keeping the face", front)
            self.assertNotIn("NOT visible", front)

    def test_the_close_up_is_not_told_to_hold_the_whole_figure(self):
        """The framing clause was shared by every view, so the head-and-
        shoulders view was also told to fit the whole figure in frame. A model
        satisfies both by drawing the full figure, which is the un-zoomed
        close-up seen on every image target."""
        import targets.image.krea2.prompt as KP
        face = KP.view_prompt("04_face", "d", "l", 1024, 1024)
        self.assertIn("framed close on the head and shoulders", face)
        self.assertNotIn("the whole figure inside the frame", face)
        for v in ("01_threequarter", "02_side", "03_back"):
            self.assertIn("the whole figure inside the frame",
                          KP.view_prompt(v, "d", "l", 1024, 1024), v)
        edit = KP.view_edit_prompt("04_face", "d", "l", 1024, 1024, "Gina")
        self.assertIn("framed close on the head and shoulders", edit)
        self.assertNotIn("the whole figure inside the frame", edit)

    def test_the_cold_path_says_it_too(self):
        """The same contradiction sits in a plain character's back view, with
        no variant involved."""
        import targets.image.krea2.prompt as KP
        back = KP.view_prompt("03_back", "a woman with a pretty face", "a flat cartoon",
                              1024, 1024)
        self.assertIn("the face is NOT visible", back)
        self.assertNotIn("NOT visible",
                         KP.view_prompt("01_threequarter", "x", "y", 1024, 1024))

    def test_a_text_to_image_job_keeps_the_plain_brief(self):
        for v in R.VIEW_TAGS:
            self.pick_base_view(v)
        job = self.plan("subject:ada_wet", "03_back", "krea2")
        self.assertEqual(job.references, [])
        self.assertTrue(job.prompt.startswith("A single character reference view"))
        self.assertIn("soaked through and clinging", job.prompt)
        self.assertEqual(job.seed, R.seed_for("ada"))               # all identity it has

    def test_the_base_is_still_generated_from_words(self):
        job = self.plan("subject:ada", "03_back", "flux2_klein_edit")
        self.assertEqual(job.references, [])
        self.assertTrue(job.prompt.startswith("A single character reference view"))


class PromoteTest(unittest.TestCase):
    """A variant's design still promotes out of a prompt override, although the
    prompt around it is the edit wording rather than the plain brief."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ep = os.path.join(self._tmp.name, "ks01")
        os.makedirs(self.ep)
        shutil.copy(os.path.join(FIXTURE, "series.json"), self.ep)
        shutil.copy(os.path.join(FIXTURE, "script.md"), os.path.join(self.ep, "ks01.md"))
        import h3edit as E
        import h3takes as T
        self.assertTrue(E.build_episode(self.ep)["ok"])
        s = R.load_series(self.ep)
        base = R.find_ref(s, "subject:ada")
        src = os.path.join(self._tmp.name, "v.png")
        with open(src, "wb") as fh:
            fh.write(png_bytes(64, 64))
        for v in R.VIEW_TAGS:
            t = R.import_take(s, base, v, src)
            R.pick_take(s, base, v, t.take)
        ov = T.load_overrides(self.ep)
        ov.setdefault("episode", {})["refs_target"] = "flux2_klein_edit"
        T.save_overrides(self.ep, ov)
        self.s = R.load_series(self.ep)

    def tearDown(self):
        self._tmp.cleanup()

    def test_the_design_comes_back_out_of_the_edit_prompt(self):
        import h3promote as P
        var = R.find_ref(self.s, "subject:ada_wet")
        old = var.entry["design"]
        new = old.replace("soaked through and clinging", "soaked through and torn at the hem")
        data = R.load_overrides(var.home)
        for v in R.VIEW_TAGS:
            eff = R.effective(self.s, var, v, data)
            self.assertIn("The reference image is Ada:", eff["prompt"])   # the edit wording
            self.assertEqual(eff["prompt"].count(old), 1)
            data = R.set_ref_override(data, var, v, {"prompt": eff["prompt"].replace(old, new)},
                                      R.prompt_hash(eff["prompt"]))
        R.save_overrides(var.home, data)
        item = [i for i in P.plan(self.ep)["items"]
                if i["id"] == "ref:subject:ada_wet:prompt"]
        self.assertEqual(len(item), 1, "the design override did not reach the plan")
        self.assertEqual(item[0]["value"], new)
        self.assertEqual(item[0]["dest"], "series")


class BuiltShotTest(unittest.TestCase):
    """The kitchen_sink fixture's sh340, through every kind of target."""

    @classmethod
    def setUpClass(cls):
        cls.cfg = load_series_config(os.path.join(FIXTURE, "series.json"))
        with open(os.path.join(FIXTURE, "script.md"), encoding="utf-8") as fh:
            cls.story = parse(fh.read(), cls.cfg)
        cls.seq = [sq for sq in cls.story.sequences if sq.id == "sq04"][0]
        cls.shot = [sh for sh in cls.seq.shots if sh.id == "sh340"][0]
        with open(os.path.join(HERE, "golden", "kitchen_sink", "shotlist.json"),
                  encoding="utf-8") as fh:
            built = json.load(fh)
        cls.built = [sh for sh in built["shots"] if sh["id"] == "sh340"][0]

    def test_the_story_ir_names_the_variant(self):
        self.assertEqual(self.shot.cast, ["ada_wet"])
        self.assertEqual(self.shot.dialogue[0].speaker, "ada_wet")
        # the base is not also in the shot: one subject, one reference slot
        self.assertNotIn("ada", self.shot.cast)

    def test_h3_keeps_fully_preserved_against_the_variants_own_picture(self):
        defs, _summary, ret, desc, _sound, _music = self.built["prompt"]
        self.assertEqual(self.built["subjects"], ["ada_wet"])
        self.assertIn("<Subject 1> is Ada, defined by <Picture 1>", defs)
        self.assertIn("soaked through and clinging", defs)
        self.assertIn("<Subject 1> (appears in [Shot 1]): fully_preserved", ret)
        # the line is the variant's own, not an invented off-screen voice
        self.assertIn("<Subject 1> (S1) says,", desc)
        self.assertNotIn("a mustard apron over a striped shirt", defs)

    def test_the_variant_shares_the_base_voice(self):
        self.assertEqual(self.built["voice_subject"], "ada_wet")
        self.assertEqual([v["sample"] for v in self.built["voice_refs"]
                          if v["subject"] == "ada_wet"], ["audio/voices/ada_sample.wav"])

    def test_the_word_only_targets_carry_the_variants_design(self):
        from targets.video.ltx2.prompt import build_prompt as ltx
        from targets.video.wan.prompt import build_prompt as wan
        for build in (ltx, wan):
            text = build(self.shot, self.seq, self.cfg)
            self.assertIn("soaked through and clinging", text)
            self.assertNotIn("a mustard apron over a striped shirt", text)
            self.assertIn("Ada", text)


if __name__ == "__main__":
    unittest.main()
