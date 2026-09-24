"""
P8 (docs/polish_Plan.md): supplying references you already have.

  - A ready-made 4-panel sheet is a take on the reserved `sheet` pseudo-view:
    imported, picked (copied, never stitched), compared, discarded. Nothing
    GENERATES a sheet, so the generate paths refuse that view.
  - `live_from` says which route wrote a character's live file, judged from the
    file's own sha1: the supplied sheet, the four views stitched, or neither --
    which is what a sheet copied into the folder by hand looks like, and that
    stays a perfectly good way to supply one.
  - match_files: which slot a file name means, exactly and explicably, with
    anything ambiguous left alone.
  - supply_files / `h3.py supply`: a folder of pictures into their slots.
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "comfy_nodes"))

import h3refs as R  # noqa: E402
import h3source as S  # noqa: E402

PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
                    "890000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082")


def starter_episode(tmp: str) -> str:
    """A starter episode: one character (ada), one prop, one location, one voice."""
    show = os.path.join(tmp, "Show")
    os.makedirs(show, exist_ok=True)
    return S.new_episode(show, "ep01")["ep"]


def png(path: str, rgb=(90, 120, 150), size=(64, 64)) -> str:
    """A real PNG when PIL is here (mksheet needs one to stitch), else a 1x1."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    try:
        from PIL import Image
        Image.new("RGB", size, rgb).save(path)
    except ImportError:
        with io.open(path, "wb") as fh:
            fh.write(PNG)
    return os.path.abspath(path)


def has_pil() -> bool:
    try:
        import PIL  # noqa: F401
        return True
    except ImportError:
        return False


class SupplyCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.ep = starter_episode(self.tmp)
        self.s = R.load_series(self.ep)
        self.ada = R.find_ref(self.s, "subject:ada")
        self.lantern = R.find_ref(self.s, "subject:lantern")
        self.porch = R.find_ref(self.s, "location:porch")

    def tearDown(self):
        self._tmp.cleanup()

    def file(self, name: str, **kw) -> str:
        return png(os.path.join(self.tmp, "supplied", name), **kw)

    def supply_sheet(self, name: str = "ada_sheet_4panel.png"):
        t = R.import_take(self.s, self.ada, R.SHEET_VIEW, self.file(name), original_name=name)
        return t, R.pick_take(self.s, self.ada, R.SHEET_VIEW, t.take)


class TestSheetView(SupplyCase):
    def test_a_sheet_is_a_take_and_goes_live_unstitched(self):
        t, res = self.supply_sheet()
        self.assertEqual(os.path.basename(t.paths.image), "subject__ada_sheet_t01.png")
        self.assertEqual(res.live, self.ada.file)
        self.assertFalse(res.stitched)
        self.assertTrue(os.path.isfile(self.ada.file))
        # the copy is byte for byte what was supplied: no stitch, no re-encode
        with io.open(t.paths.image, "rb") as a, io.open(self.ada.file, "rb") as b:
            self.assertEqual(a.read(), b.read())

    def test_the_sidecar_records_where_it_came_from(self):
        t, _ = self.supply_sheet()
        with io.open(t.paths.sidecar, encoding="utf-8") as fh:
            side = json.load(fh)
        self.assertEqual(side["source"], "imported")
        self.assertEqual(side["original_name"], "ada_sheet_4panel.png")
        self.assertEqual(side["view"], R.SHEET_VIEW)

    def test_a_second_sheet_is_a_second_candidate(self):
        self.supply_sheet()
        t2, _ = self.supply_sheet("ada_sheet_v2.png")
        self.assertEqual(t2.take, 2)
        self.assertEqual([x.take for x in R.list_takes(self.ada, R.SHEET_VIEW)], [1, 2])
        self.assertEqual(R.picked_take(R.load_picks(self.ada.home), "subject:ada",
                                       R.SHEET_VIEW), 2)

    def test_the_listing_shows_it_as_the_refs_own_takes(self):
        self.supply_sheet()
        j = R.ref_json(self.s, self.ada)
        self.assertEqual(len(j["takes"]), 1)
        self.assertEqual(j["picked"], 1)
        self.assertEqual(j["live_from"], R.SHEET_VIEW)
        # the four views are untouched by a supplied sheet
        self.assertEqual([len(v["takes"]) for v in j["views"]], [0, 0, 0, 0])

    def test_nothing_generates_a_sheet(self):
        with self.assertRaises(R.RefError) as cm:
            R.check_view(self.ada, R.SHEET_VIEW)
        self.assertIn("not a view to generate", str(cm.exception))
        with self.assertRaises(R.RefError) as cm:
            R.plan_generate(self.s, R.GenRequest(ref="subject:ada", view=R.SHEET_VIEW))
        self.assertIn("not a view to generate", str(cm.exception))

    def test_only_a_character_has_one(self):
        for ref in (self.lantern, self.porch):
            with self.subTest(ref=ref.id):
                with self.assertRaises(R.RefError) as cm:
                    R.check_view(ref, R.SHEET_VIEW, allow_sheet=True)
                self.assertIn("no views", str(cm.exception))

    def test_a_view_name_that_is_not_one(self):
        with self.assertRaises(R.RefError):
            R.check_view(self.ada, "05_sheet", allow_sheet=True)


class TestLiveFrom(SupplyCase):
    def picks(self):
        return R.load_picks(self.ada.home)

    def test_nothing_live(self):
        self.assertIsNone(R.live_from(self.picks(), self.ada))
        self.assertIsNone(R.ref_json(self.s, self.ada)["live_from"])

    def test_a_hand_placed_sheet_is_used_and_owned_by_nobody(self):
        """The Explorer route: a file at the path series.json names, no take."""
        png(self.ada.file)
        j = R.ref_json(self.s, self.ada)
        self.assertTrue(j["exists"])
        self.assertIsNone(j["live_from"])
        self.assertEqual(j["takes"], [])
        self.assertIsNone(j["picked"])

    def test_supplied_then_hand_edited_is_reported_honestly(self):
        self.supply_sheet()
        self.assertEqual(R.live_from(self.picks(), self.ada), R.SHEET_VIEW)
        png(self.ada.file, rgb=(1, 2, 3))          # replaced outside the editor
        self.assertIsNone(R.live_from(self.picks(), self.ada))

    @unittest.skipUnless(has_pil(), "stitching a sheet needs PIL")
    def test_four_view_picks_stitch_over_a_supplied_sheet(self):
        self.supply_sheet()
        for i, v in enumerate(R.VIEW_TAGS):
            t = R.import_take(self.s, self.ada, v, self.file(f"{v}.png", rgb=(0, 0, 40 + i * 40)),
                              original_name=f"{v}.png")
            res = R.pick_take(self.s, self.ada, v, t.take)
        self.assertTrue(res.stitched)
        self.assertEqual(R.live_from(self.picks(), self.ada), "views")

    @unittest.skipUnless(has_pil(), "stitching a sheet needs PIL")
    def test_discarding_the_supplied_sheet_leaves_a_stitch_alone(self):
        """The guard that stops a clear from deleting someone else's file."""
        sheet, _ = self.supply_sheet()
        for i, v in enumerate(R.VIEW_TAGS):
            t = R.import_take(self.s, self.ada, v, self.file(f"{v}.png", rgb=(0, 0, 40 + i * 40)),
                              original_name=f"{v}.png")
            R.pick_take(self.s, self.ada, v, t.take)
        before = R.T.file_sha1(self.ada.file)
        d = R.discard_take(self.s, self.ada, R.SHEET_VIEW, sheet.take)
        self.assertTrue(os.path.isfile(self.ada.file))
        self.assertEqual(R.T.file_sha1(self.ada.file), before)
        self.assertEqual(d.cleared.removed, [])
        self.assertEqual(R.live_from(self.picks(), self.ada), "views")

    def test_discarding_the_live_supplied_sheet_does_remove_it(self):
        sheet, _ = self.supply_sheet()
        d = R.discard_take(self.s, self.ada, R.SHEET_VIEW, sheet.take)
        self.assertFalse(os.path.isfile(self.ada.file))
        self.assertEqual(d.cleared.removed, [self.ada.file])

    def test_clearing_the_ref_clears_the_supplied_sheet_too(self):
        self.supply_sheet()
        R.clear_pick(self.s, self.ada)
        picks = self.picks()["refs"]["subject:ada"]["views"]
        self.assertIn(R.SHEET_VIEW, picks)
        self.assertIn("cleared", picks[R.SHEET_VIEW])
        self.assertTrue(R.is_cleared(self.picks(), "subject:ada", R.SHEET_VIEW))
        self.assertFalse(os.path.isfile(self.ada.file))


class TestMatchFiles(SupplyCase):
    def match(self, *names):
        return R.match_files(self.s, list(names))

    def one(self, name):
        """(ref, view) of a name that matches exactly one slot."""
        r = self.match(name)
        self.assertEqual(len(r["matched"]), 1, r)
        m = r["matched"][0]
        return m["ref"], m["view"]

    def why_not(self, name):
        r = self.match(name)
        self.assertEqual(r["matched"], [], r)
        return r["unmatched"][0]["why"]

    def test_the_file_the_series_config_names(self):
        self.assertEqual(self.one("ada_sheet_4panel.png"), ("subject:ada", R.SHEET_VIEW))
        self.assertEqual(self.one("lantern.png"), ("subject:lantern", None))
        self.assertEqual(self.one("porch.png"), ("location:porch", None))

    def test_a_path_is_taken_by_its_last_part(self):
        self.assertEqual(self.one("C:/from_krea/2026/ada_sheet_4panel.png"),
                         ("subject:ada", R.SHEET_VIEW))

    def test_a_character_by_name_means_the_sheet(self):
        self.assertEqual(self.one("ada_sheet.png"), ("subject:ada", R.SHEET_VIEW))
        self.assertEqual(self.one("ada_4panel.png"), ("subject:ada", R.SHEET_VIEW))

    def test_views_by_tag_and_by_word(self):
        self.assertEqual(self.one("ada_02_side.png"), ("subject:ada", "02_side"))
        self.assertEqual(self.one("ada_side.png"), ("subject:ada", "02_side"))
        self.assertEqual(self.one("ada_threequarter.png"), ("subject:ada", "01_threequarter"))
        self.assertEqual(self.one("ada_face.png"), ("subject:ada", "04_face"))
        self.assertEqual(self.one("04_face_ada.png"), ("subject:ada", "04_face"))

    def test_a_location_by_plate_wording(self):
        self.assertEqual(self.one("porch_plate.png"), ("location:porch", None))
        self.assertEqual(self.one("bg_porch.png"), ("location:porch", None))

    def test_the_extension_decides_which_of_two_slots_a_name_means(self):
        """`ada` is both the character's sheet and the voice: the file says which."""
        self.assertEqual(self.one("ada.wav"), ("voice:ada", None))
        self.assertEqual(self.one("ada.mp3"), ("voice:ada", None))
        self.assertEqual(self.one("ada.png"), ("subject:ada", R.SHEET_VIEW))

    def test_a_slot_that_cannot_take_this_kind_of_file(self):
        self.assertIn("takes an image", self.why_not("porch.wav"))
        self.assertIn("takes an image", self.why_not("lantern.mp3"))

    def test_case_and_separators_do_not_matter(self):
        for name in ("ADA_SHEET_4PANEL.PNG", "Ada Sheet 4Panel.png", "ada-sheet-4panel.png",
                     "  ada__sheet--4panel .png"):
            with self.subTest(name=name):
                self.assertEqual(self.one(name), ("subject:ada", R.SHEET_VIEW))

    def test_a_revision_marker_is_ignored_and_said_so(self):
        r = self.match("ada_sheet_4panel_v2.png")
        self.assertEqual(len(r["matched"]), 1, r)
        self.assertIn("ignoring the trailing 'v2'", r["matched"][0]["why"])
        self.assertEqual(self.one("lantern_final.png"), ("subject:lantern", None))
        self.assertEqual(self.one("porch_2.png"), ("location:porch", None))

    def test_a_name_nothing_is_called(self):
        self.assertIn("no ref or view is named 'highway_dawn'", self.why_not("highway_dawn.png"))

    def test_a_file_that_is_not_media(self):
        self.assertIn("not a picture", self.why_not("notes.txt"))
        self.assertIn("no extension", self.why_not("ada_sheet_4panel"))

    def test_two_files_for_one_slot_leave_both_alone(self):
        r = self.match("ada_sheet_4panel.png", "ada.png")
        self.assertEqual(r["matched"], [])
        self.assertEqual(len(r["unmatched"]), 2)
        for u in r["unmatched"]:
            self.assertIn("rename one", u["why"])

    def test_the_matched_entry_says_enough_to_act_on(self):
        m = self.match("ada_sheet_4panel.png")["matched"][0]
        self.assertEqual(m["ref"], "subject:ada")
        self.assertEqual(m["view"], R.SHEET_VIEW)
        self.assertEqual(m["name"], "Ada")
        self.assertEqual(m["kind"], "character")
        self.assertFalse(m["audio"])
        self.assertEqual(m["path"], "../refs/ada/ada_sheet_4panel.png")

    def test_nothing_is_written(self):
        self.match("ada_sheet_4panel.png", "lantern.png")
        self.assertFalse(os.path.exists(os.path.join(self.ep, "refs", "_takes")))
        self.assertFalse(os.path.isfile(self.ada.file))

    def test_an_empty_list(self):
        self.assertEqual(R.match_files(self.s, []), {"matched": [], "unmatched": []})


class TestSupplyFiles(SupplyCase):
    def drop_folder(self, *names) -> str:
        d = os.path.join(self.tmp, "drop")
        for n in names:
            png(os.path.join(d, n))
        return d

    def test_a_folder_of_pictures_goes_in_and_goes_live(self):
        d = self.drop_folder("ada_sheet_4panel.png", "lantern.png", "porch.png", "nope.png")
        r = R.supply_files(self.ep, [d])
        self.assertEqual(len(r["supplied"]), 3)
        self.assertEqual(len(r["unmatched"]), 1)
        self.assertEqual(r["failed"], [])
        for ref in (self.ada, self.lantern, self.porch):
            with self.subTest(ref=ref.id):
                self.assertTrue(os.path.isfile(ref.file), ref.file)
        self.assertEqual(R.live_from(R.load_picks(self.ada.home), self.ada), R.SHEET_VIEW)

    def test_a_dry_run_writes_nothing(self):
        d = self.drop_folder("ada_sheet_4panel.png", "lantern.png")
        r = R.supply_files(self.ep, [d], dry_run=True)
        self.assertEqual(len(r["matched"]), 2)
        self.assertEqual(r["supplied"], [])
        self.assertFalse(os.path.isfile(self.ada.file))
        self.assertFalse(os.path.exists(os.path.join(self.ep, "refs", "_takes")))

    def test_no_pick_leaves_the_live_file_alone(self):
        d = self.drop_folder("lantern.png")
        r = R.supply_files(self.ep, [d], pick=False)
        self.assertEqual(len(r["supplied"]), 1)
        self.assertIsNone(r["supplied"][0]["live"])
        self.assertFalse(os.path.isfile(self.lantern.file))
        self.assertEqual(len(R.list_takes(self.lantern)), 1)

    def test_one_file_into_a_named_slot(self):
        f = png(os.path.join(self.tmp, "whatever.png"))
        r = R.supply_files(self.ep, [f], ref_id="subject:ada", view=R.SHEET_VIEW)
        self.assertEqual(len(r["supplied"]), 1)
        self.assertEqual(R.live_from(R.load_picks(self.ada.home), self.ada), R.SHEET_VIEW)

    def test_a_named_slot_takes_exactly_one_file(self):
        d = self.drop_folder("a.png", "b.png")
        with self.assertRaises(R.RefError):
            R.supply_files(self.ep, [d], ref_id="subject:ada", view=R.SHEET_VIEW)

    def test_a_character_needs_to_be_told_which_slot(self):
        f = png(os.path.join(self.tmp, "whatever.png"))
        with self.assertRaises(R.RefError) as cm:
            R.supply_files(self.ep, [f], ref_id="subject:ada")
        self.assertIn("give a view", str(cm.exception))

    def test_a_folder_with_nothing_usable(self):
        d = os.path.join(self.tmp, "empty")
        os.makedirs(d)
        with io.open(os.path.join(d, "readme.txt"), "w", encoding="utf-8") as fh:
            fh.write("nothing here")
        with self.assertRaises(R.RefError):
            R.supply_files(self.ep, [d])

    def test_a_path_that_is_not_there(self):
        with self.assertRaises(FileNotFoundError):
            R.supply_files(self.ep, [os.path.join(self.tmp, "nope")])


class TestSupplyCli(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.ep = starter_episode(self.tmp)
        self.drop = os.path.join(self.tmp, "drop")
        for n in ("ada_sheet_4panel.png", "lantern.png", "porch.png", "mystery.png"):
            png(os.path.join(self.drop, n))

    def tearDown(self):
        self._tmp.cleanup()

    def run_supply(self, *args: str):
        return subprocess.run([sys.executable, os.path.join(ROOT, "h3.py"), "supply",
                               self.ep, *args], capture_output=True, text=True, cwd=ROOT)

    def test_dry_run_prints_the_table_and_writes_nothing(self):
        out = self.run_supply(self.drop, "--dry-run")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("subject:ada sheet", out.stdout)
        self.assertIn("mystery.png", out.stdout)
        self.assertIn("dry run: 3 would be supplied", out.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.ep, "refs", "_takes")))

    def test_it_supplies_and_the_build_sees_them(self):
        out = self.run_supply(self.drop)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("3 supplied, 1 unmatched, 0 failed", out.stdout)
        build = subprocess.run(
            [sys.executable, os.path.join(ROOT, "h3build.py"),
             os.path.join(self.ep, "series.json"), os.path.join(self.ep, "ep01.md"),
             "-o", self.ep], capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
        self.assertIn("references   3/3 on disk", build.stdout)

    def test_one_file_into_a_named_slot(self):
        out = self.run_supply(os.path.join(self.drop, "mystery.png"), "--ref", "location:porch")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("named on the command line", out.stdout)

    def test_a_view_on_the_ref_spec(self):
        out = self.run_supply(os.path.join(self.drop, "mystery.png"),
                              "--ref", "subject:ada:02_side")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("subject:ada 02_side", out.stdout)

    def test_usage_and_unknown_flags(self):
        self.assertEqual(self.run_supply().returncode, 2)
        bad = self.run_supply(self.drop, "--bogus")
        self.assertEqual(bad.returncode, 2)
        self.assertIn("--bogus", bad.stdout)



class TestSharedLiveFiles(unittest.TestCase):
    """P9: a live file other episodes read. With a series config per episode and
    `../refs/...` paths, one file serves the whole show -- so the listing has to
    say who else reads it and whose pick wrote the copy that is there now."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.show = os.path.join(self.tmp, "Show")
        os.makedirs(self.show)
        self.ep1 = S.new_episode(self.show, "ep01")["ep"]
        self.ep2 = S.new_episode(self.show, "ep02")["ep"]
        R._PATHS.clear()
        R._SHA1S.clear()

    def tearDown(self):
        R._PATHS.clear()
        R._SHA1S.clear()
        self._tmp.cleanup()

    def ada(self, ep):
        s = R.load_series(ep)
        return s, R.find_ref(s, "subject:ada")

    def supply(self, ep, name="ada_sheet_4panel.png", rgb=None):
        """A supplied sheet, picked, in `ep` -- it writes the SHARED live file.
        Each episode's file differs, because two byte-identical files would
        record the same sha1 and ownership between them would be arbitrary (and
        moot: the content is the same either way)."""
        s, ada = self.ada(ep)
        rgb = rgb or ((200, 0, 0) if ep == self.ep1 else (0, 0, 200))
        src = png(os.path.join(self.tmp, f"{os.path.basename(ep)}_{name}"), rgb=rgb)
        t = R.import_take(s, ada, R.SHEET_VIEW, src, original_name=name)
        R.pick_take(s, ada, R.SHEET_VIEW, t.take)
        return t

    def test_siblings_are_found_and_the_episode_itself_is_not(self):
        self.assertEqual([os.path.basename(x) for x in R.sibling_episodes(self.ep1)], ["ep02"])
        self.assertEqual([os.path.basename(x) for x in R.sibling_episodes(self.ep2)], ["ep01"])

    def test_the_shared_map_is_every_file_both_episodes_name(self):
        m = R.shared_context(self.ep1)["shared"]
        _, ada = self.ada(self.ep1)
        self.assertEqual(m[R._real_path(ada.file)], ["ep02"])
        # the starter names three pictures and one voice sample path per episode
        self.assertGreaterEqual(len(m), 3)

    def test_nothing_is_shared_when_the_refs_are_episode_local(self):
        """The one-folder layout: `refs/...` inside the episode, nothing shared."""
        for ep in (self.ep1, self.ep2):
            cfg = os.path.join(ep, "series.json")
            raw = json.load(io.open(cfg, encoding="utf-8"))
            for sub in raw["subjects"].values():
                sub["sheet"] = sub["sheet"].replace("../refs/", "refs/")
            for loc in raw["locations"].values():
                loc["plate"] = loc["plate"].replace("../refs/", "refs/")
            with io.open(cfg, "w", encoding="utf-8") as fh:
                json.dump(raw, fh)
        R._PATHS.clear()
        self.assertEqual(R.shared_context(self.ep1)["shared"], {})

    def test_the_listing_says_who_else_reads_it_and_who_owns_it(self):
        self.supply(self.ep1)
        s, ada = self.ada(self.ep1)
        j = R.ref_json(s, ada)
        self.assertEqual(j["shared_with"], ["ep02"])
        self.assertEqual(j["live_owner"], "ep01")
        # and from ep02's side, the same file is ep01's doing
        s2, ada2 = self.ada(self.ep2)
        j2 = R.ref_json(s2, ada2)
        self.assertEqual(j2["shared_with"], ["ep01"])
        self.assertEqual(j2["live_owner"], "ep01")

    def test_a_pick_in_the_other_episode_moves_the_ownership(self):
        self.supply(self.ep1)
        R._SHA1S.clear()
        self.supply(self.ep2)                      # ep02 picks over the shared file
        R._SHA1S.clear()
        s, ada = self.ada(self.ep1)
        self.assertEqual(R.ref_json(s, ada)["live_owner"], "ep02")

    def test_a_hand_placed_file_is_nobodys(self):
        _, ada = self.ada(self.ep1)
        png(ada.file)                              # copied in with Explorer
        s, ada = self.ada(self.ep1)
        j = R.ref_json(s, ada)
        self.assertTrue(j["exists"])
        self.assertIsNone(j["live_owner"])
        self.assertEqual(j["shared_with"], ["ep02"])

    def test_a_file_replaced_outside_the_editor_stops_being_owned(self):
        self.supply(self.ep1)
        _, ada = self.ada(self.ep1)
        png(ada.file, rgb=(9, 9, 9))               # edited in Photoshop
        s, ada = self.ada(self.ep1)
        self.assertIsNone(R.ref_json(s, ada)["live_owner"])

    def test_no_live_file_has_no_owner_but_is_still_shared(self):
        s, ada = self.ada(self.ep1)
        j = R.ref_json(s, ada)
        self.assertFalse(j["exists"])
        self.assertIsNone(j["live_owner"])
        self.assertEqual(j["shared_with"], ["ep02"])

    def test_the_only_episode_of_a_show_shares_nothing(self):
        alone = os.path.join(self.tmp, "Solo")
        os.makedirs(alone)
        ep = S.new_episode(alone, "ep01")["ep"]
        s, ada = self.ada(ep)
        self.assertEqual(R.ref_json(s, ada)["shared_with"], [])

    def test_the_memo_follows_the_files_it_read(self):
        """A neighbour's pick has to show up on the next call, not after a restart."""
        self.supply(self.ep1)
        s, ada = self.ada(self.ep1)
        self.assertEqual(R.ref_json(s, ada)["live_owner"], "ep01")
        self.supply(self.ep2)                      # no cache clearing this time
        self.assertEqual(R.ref_json(s, ada)["live_owner"], "ep02")

    def test_a_new_sibling_appears_without_clearing_anything(self):
        self.assertEqual(R.shared_context(self.ep1)["shared"][
            R._real_path(self.ada(self.ep1)[1].file)], ["ep02"])
        S.new_episode(self.show, "ep03")
        self.assertEqual(R.shared_context(self.ep1)["shared"][
            R._real_path(self.ada(self.ep1)[1].file)], ["ep02", "ep03"])

    def test_an_unreadable_neighbour_is_ignored_rather_than_fatal(self):
        with io.open(os.path.join(self.ep2, "series.json"), "w", encoding="utf-8") as fh:
            fh.write("{ this is not json")
        R._PATHS.clear()
        self.assertEqual(R.shared_context(self.ep1)["shared"], {})
        s, ada = self.ada(self.ep1)
        self.assertEqual(R.ref_json(s, ada)["shared_with"], [])

if __name__ == "__main__":
    unittest.main()
