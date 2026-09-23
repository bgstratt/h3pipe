"""
P5 (docs/polish_Plan.md): a new episode from a template.

  - `examples/starter/` is a real, correct pair: it passes h3build --check with
    no warnings, and it is honest about references (it lists them as missing).
  - h3source.new_episode makes `<parent>/<name>/` with a series config and a
    script that build. The template is the newest episode already there -- its
    cast and look carry over -- else the starter.
  - POST /h3pipe/episode/new does the same inside a configured root, and
    `python h3.py new <folder>/<name>` without the editor.
  - docs/AUTHORING.md's opening example IS the starter, so the pair the guide
    teaches is the pair the button writes (tools/sync_starter_doc.py).
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
sys.path.insert(0, os.path.join(ROOT, "tools"))

import h3edit as E  # noqa: E402
import h3source as S  # noqa: E402
import sync_starter_doc as SYNC  # noqa: E402

STARTER = os.path.join(ROOT, "examples", "starter")


def read(path: str) -> str:
    with io.open(path, encoding="utf-8") as fh:
        return fh.read()


class TestStarter(unittest.TestCase):
    """The shipped pair, which is what a first episode starts from."""

    def test_it_is_two_files(self):
        self.assertEqual(sorted(os.listdir(STARTER)), ["ep01.md", "series.json"])

    def test_check_passes_with_no_warnings(self):
        out = subprocess.run(
            [sys.executable, os.path.join(ROOT, "h3build.py"),
             os.path.join(STARTER, "series.json"), os.path.join(STARTER, "ep01.md"), "--check"],
            capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        for bad in ("!!", "warning", "WARNING"):
            self.assertNotIn(bad, out.stdout, out.stdout)

    def test_it_is_small_and_covers_the_parts(self):
        cfg = json.loads(read(os.path.join(STARTER, "series.json")))
        kinds = sorted(s["kind"] for s in cfg["subjects"].values())
        self.assertEqual(kinds, ["character", "prop"])       # one of each, no more
        self.assertEqual(len(cfg["locations"]), 1)
        script = read(os.path.join(STARTER, "ep01.md"))
        self.assertEqual(script.count("\n## "), 3)          # three shots
        self.assertIn("ADA:", script)                        # one of them speaks
        self.assertEqual(cfg["audio"]["mode"], "generate")   # so no recording is needed

    def test_pictures_are_named_one_level_up(self):
        """The shared layout: `../refs/...`, so every episode of the show reads
        the same folder while keeping a series config of its own."""
        cfg = json.loads(read(os.path.join(STARTER, "series.json")))
        paths = [s["sheet"] for s in cfg["subjects"].values()]
        paths += [loc["plate"] for loc in cfg["locations"].values()]
        for p in paths:
            self.assertTrue(p.startswith("../refs/"), p)

    def test_the_docs_teach_this_pair(self):
        """docs/AUTHORING.md's first example is the starter, byte for byte."""
        self.assertEqual(SYNC.main(["--check"]), 0,
                         "run python tools/sync_starter_doc.py, then make_prompts.py")


class NewEpisodeCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.show = os.path.join(self.dir, "Show")
        os.makedirs(self.show)

    def cfg(self, ep: str) -> dict:
        return json.loads(read(os.path.join(ep, "series.json")))

    def script(self, ep: str) -> str:
        return read(os.path.join(ep, f"{os.path.basename(ep)}.md"))


class TestFirstEpisode(NewEpisodeCase):
    """An empty show folder: the starter, with the caller's names on it."""

    def test_writes_the_two_files_and_they_build(self):
        r = S.new_episode(self.show, "ep01")
        self.assertEqual(r["template"], "starter")
        self.assertEqual(sorted(os.listdir(r["ep"])), ["ep01.md", "series.json"])
        self.assertEqual(r["files"], ["series.json", "ep01.md"])
        self.assertTrue(r["check"]["ok"], r["check"]["errors"])
        self.assertEqual(r["check"]["warnings"], [])
        self.assertEqual(len(r["check"]["shots"]), 3)

    def test_the_refs_folder_is_beside_the_episode_and_made(self):
        r = S.new_episode(self.show, "ep01")
        self.assertEqual(r["refs"], os.path.join(self.show, "refs"))
        self.assertTrue(os.path.isdir(r["refs"]))

    def test_names_the_series_when_it_is_being_started(self):
        r = S.new_episode(self.show, "ep01", series_id="porch", title="Porchlights")
        self.assertEqual(self.cfg(r["ep"])["series"]["id"], "porch")
        self.assertEqual(self.cfg(r["ep"])["series"]["title"], "Porchlights")
        self.assertTrue(self.script(r["ep"]).startswith("= ep01  Porchlights\n"))

    def test_the_script_header_is_the_new_name(self):
        r = S.new_episode(self.show, "pilot")
        self.assertTrue(self.script(r["ep"]).startswith("= pilot  First Light\n"))

    def test_the_editor_finds_it(self):
        r = S.new_episode(self.show, "ep01")
        found = [e["ep"] for e in E.find_episodes([self.dir])]
        self.assertIn(r["ep"], found)

    def test_a_config_beside_it_is_not_a_sibling_episode(self):
        """A series folder with a config but no episodes still starts from the
        starter (that config isn't an episode's)."""
        with io.open(os.path.join(self.show, "series.json"), "w", encoding="utf-8") as fh:
            fh.write('{"series": {"id": "x", "title": "X"}}')
        r = S.new_episode(self.show, "ep01")
        self.assertEqual(r["template"], "starter")
        self.assertEqual(self.cfg(r["ep"])["series"]["id"], "first_light")


class TestNextEpisode(NewEpisodeCase):
    """A show with an episode already: that episode's config carries over."""

    def setUp(self):
        super().setUp()
        self.first = S.new_episode(self.show, "ep01", series_id="porch", title="Porchlights")["ep"]

    def test_copies_the_series_config(self):
        r = S.new_episode(self.show, "ep02")
        self.assertEqual(r["template"], "episode")
        self.assertEqual(r["from"], self.first)
        self.assertEqual(self.cfg(r["ep"]), self.cfg(self.first))

    def test_the_series_keeps_its_own_id_and_title(self):
        """`title` titles the episode; the series is the neighbour's."""
        r = S.new_episode(self.show, "ep02", title="The Glance", series_id="ignored")
        self.assertEqual(self.cfg(r["ep"])["series"]["id"], "porch")
        self.assertEqual(self.cfg(r["ep"])["series"]["title"], "Porchlights")
        self.assertTrue(self.script(r["ep"]).startswith("= ep02  The Glance\n"))

    def test_the_skeleton_names_this_show_and_builds(self):
        r = S.new_episode(self.show, "ep02")
        text = self.script(r["ep"])
        self.assertIn("# sq01  porch", text)          # the config's first location
        self.assertIn("who: ada", text)               # its first character
        self.assertIn("Ada stands still", text)       # opening a sentence, capitalised
        self.assertTrue(r["check"]["ok"], r["check"]["errors"])
        self.assertEqual(r["check"]["warnings"], [])
        self.assertEqual(len(r["check"]["shots"]), 1)

    def test_it_is_the_newest_neighbour_that_is_copied(self):
        S.new_episode(self.show, "ep02")
        with io.open(os.path.join(self.show, "ep02", "series.json"), encoding="utf-8") as fh:
            raw = json.load(fh)
        raw["subjects"]["bram"] = {"kind": "character", "name": "Bram",
                                   "design": "a tall man in a long coat",
                                   "sheet": "../refs/bram/bram_sheet_4panel.png",
                                   "voice": "low and slow"}
        with io.open(os.path.join(self.show, "ep02", "series.json"), "w", encoding="utf-8") as fh:
            json.dump(raw, fh)
        r = S.new_episode(self.show, "ep03")
        self.assertEqual(r["from"], os.path.join(self.show, "ep02"))
        self.assertIn("bram", self.cfg(r["ep"])["subjects"])

    def test_a_recording_follows_the_episode_name(self):
        raw = self.cfg(self.first)
        raw["audio"] = {"mode": "source_track", "track": "audio/ep01_dialogue_mix.wav"}
        with io.open(os.path.join(self.first, "series.json"), "w", encoding="utf-8") as fh:
            json.dump(raw, fh)
        r = S.new_episode(self.show, "ep02")
        self.assertEqual(self.cfg(r["ep"])["audio"]["track"], "audio/ep02_dialogue_mix.wav")

    def test_a_config_with_nobody_to_write_about_falls_back(self):
        raw = self.cfg(self.first)
        raw["subjects"] = {}
        with io.open(os.path.join(self.first, "series.json"), "w", encoding="utf-8") as fh:
            json.dump(raw, fh)
        r = S.new_episode(self.show, "ep02")
        self.assertEqual(r["template"], "starter")
        self.assertIn("ada", self.cfg(r["ep"])["subjects"])


class TestRefusals(NewEpisodeCase):
    def test_a_name_that_is_not_a_folder_name(self):
        for bad in ("", "  ", "..", "ep 1", "a/b", "a\\b", "-ep", ".hidden", "ep01."):
            with self.subTest(name=bad):
                with self.assertRaises(S.SourceError) as cm:
                    S.new_episode(self.show, bad)
                self.assertEqual(cm.exception.status, 400)

    def test_a_name_the_editor_could_never_find_again(self):
        for bad in ("refs", "Refs", "audio", "shotlist", "renders", "views", "targets",
                    "nul", "COM1"):
            with self.subTest(name=bad):
                with self.assertRaises(S.SourceError) as cm:
                    S.new_episode(self.show, bad)
                self.assertEqual(cm.exception.status, 400)

    def test_a_folder_that_is_already_there(self):
        S.new_episode(self.show, "ep01")
        with self.assertRaises(S.SourceError) as cm:
            S.new_episode(self.show, "ep01")
        self.assertEqual(cm.exception.status, 409)

    def test_a_file_in_the_way(self):
        with io.open(os.path.join(self.show, "ep01"), "w", encoding="utf-8") as fh:
            fh.write("not a folder")
        with self.assertRaises(S.SourceError) as cm:
            S.new_episode(self.show, "ep01")
        self.assertEqual(cm.exception.status, 409)

    def test_a_parent_that_is_not_a_folder(self):
        with self.assertRaises(S.SourceError) as cm:
            S.new_episode(os.path.join(self.dir, "nope"), "ep01")
        self.assertEqual(cm.exception.status, 400)

    def test_nothing_is_left_behind_when_the_write_fails(self):
        real = S.atomic_write

        def boom(path, data):
            if path.endswith(".md"):
                raise OSError("disk full")
            return real(path, data)

        S.atomic_write = boom
        try:
            with self.assertRaises(OSError):
                S.new_episode(self.show, "ep01")
        finally:
            S.atomic_write = real
        self.assertFalse(os.path.exists(os.path.join(self.show, "ep01")))


class TestDiscoveryFallback(unittest.TestCase):
    """episode_series_config falls back to the parent folder, so find_episodes
    has to as well -- otherwise an episode written that way is invisible in the
    editor while the CLI renders it happily."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.show = os.path.join(self.dir, "Show")
        os.makedirs(os.path.join(self.show, "ep01"))
        with io.open(os.path.join(self.show, "series.json"), "w", encoding="utf-8") as fh:
            json.dump({"series": {"id": "s", "title": "Shared"}}, fh)
        with io.open(os.path.join(self.show, "ep01", "ep01.md"), "w", encoding="utf-8") as fh:
            fh.write("= ep01  Shared\n")

    def names(self) -> dict:
        return {e["name"]: e for e in E.find_episodes([self.dir])}

    def test_an_episode_using_the_parents_config_is_listed(self):
        self.assertIn("ep01", self.names())
        self.assertEqual(self.names()["ep01"]["series"], "Shared")

    def test_a_folder_of_loose_notes_is_not(self):
        os.makedirs(os.path.join(self.show, "notes"))
        with io.open(os.path.join(self.show, "notes", "ideas.md"), "w", encoding="utf-8") as fh:
            fh.write("what if\n")
        self.assertNotIn("notes", self.names())

    def test_a_folders_own_config_still_wins(self):
        os.makedirs(os.path.join(self.show, "ep02"))
        with io.open(os.path.join(self.show, "ep02", "series.json"), "w", encoding="utf-8") as fh:
            json.dump({"series": {"id": "s2", "title": "Own"}}, fh)
        with io.open(os.path.join(self.show, "ep02", "ep02.md"), "w", encoding="utf-8") as fh:
            fh.write("= ep02  Own\n")
        self.assertEqual(self.names()["ep02"]["series"], "Own")


class TestCli(unittest.TestCase):
    """`python h3.py new <folder>/<name>`."""

    def run_new(self, *args: str):
        return subprocess.run([sys.executable, os.path.join(ROOT, "h3.py"), "new", *args],
                              capture_output=True, text=True, cwd=ROOT)

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.show = os.path.join(self.dir, "Show")
        os.makedirs(self.show)

    def test_makes_an_episode_and_says_what_comes_next(self):
        out = self.run_new(os.path.join(self.show, "ep01"), "--title", "First Light")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("starter template", out.stdout)
        self.assertIn("reference pictures go in", out.stdout)
        self.assertIn("h3.py build", out.stdout)
        self.assertTrue(os.path.isfile(os.path.join(self.show, "ep01", "ep01.md")))

    def test_the_second_one_comes_from_the_first(self):
        self.run_new(os.path.join(self.show, "ep01"))
        out = self.run_new(os.path.join(self.show, "ep02"))
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("from the episode beside it", out.stdout)

    def test_it_refuses_rather_than_overwriting(self):
        self.run_new(os.path.join(self.show, "ep01"))
        out = self.run_new(os.path.join(self.show, "ep01"))
        self.assertEqual(out.returncode, 1)
        self.assertIn("already there", out.stdout)

    def test_usage_without_a_folder(self):
        out = self.run_new()
        self.assertEqual(out.returncode, 2)
        self.assertIn("usage", out.stdout)

    def test_an_unknown_flag_is_not_swallowed(self):
        out = self.run_new(os.path.join(self.show, "ep01"), "--bogus")
        self.assertEqual(out.returncode, 2)
        self.assertIn("--bogus", out.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.show, "ep01")))


if __name__ == "__main__":
    unittest.main()
