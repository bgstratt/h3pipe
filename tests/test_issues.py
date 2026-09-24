"""
P10 (docs/polish_Plan.md): a pass's issues — the notepad you fill while watching
a proxy.

  - An issue snapshots what produced the take: the script's lines, the compiled
    prompt, the references, the take's file. The snapshot is the point — editing
    the script afterwards must not change what the note is about.
  - `addressed` is computed on read (the shot was rebuilt, or rendered again),
    never written, and nothing is deleted behind the user's back.
  - `<ep>/_issues.json` doesn't exist until the first note and is removed when
    the last one goes: an episode with no issues has no issue file.
  - The markdown export is generated one way. Nothing parses it back.
"""
from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import h3edit as E  # noqa: E402
import h3issues as I  # noqa: E402
import h3takes as T  # noqa: E402

FIXTURE = os.path.join(HERE, "fixtures", "kitchen_sink")
_BUILT = None


def built_episode() -> str:
    global _BUILT
    if _BUILT is None or not os.path.isdir(_BUILT):
        tmp = tempfile.mkdtemp(prefix="h3iss_")
        ep = os.path.join(tmp, "ks01")
        os.makedirs(ep)
        shutil.copy(os.path.join(FIXTURE, "series.json"), ep)
        shutil.copy(os.path.join(FIXTURE, "script.md"), os.path.join(ep, "ks01.md"))
        assert E.build_episode(ep)["ok"]
        _BUILT = ep
    return _BUILT


def tearDownModule():
    global _BUILT
    if _BUILT:
        shutil.rmtree(os.path.dirname(_BUILT), ignore_errors=True)
        _BUILT = None


class IssuesCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ep = os.path.join(self._tmp.name, "ks01")
        shutil.copytree(built_episode(), self.ep)

    def tearDown(self):
        self._tmp.cleanup()

    def script_text(self) -> str:
        with io.open(os.path.join(self.ep, "ks01.md"), encoding="utf-8") as fh:
            return fh.read()

    def write_script(self, text: str):
        with io.open(os.path.join(self.ep, "ks01.md"), "w", encoding="utf-8",
                     newline="\n") as fh:
            fh.write(text)

    def first_shot(self) -> str:
        import h3jobs as J
        return J.load_shotlists(self.ep, "proxy")[0]["shots"][0]["id"]

    def file(self) -> dict | None:
        p = I.issues_path(self.ep)
        if not os.path.isfile(p):
            return None
        with io.open(p, encoding="utf-8") as fh:
            return json.load(fh)


class TestTheFile(IssuesCase):
    def test_no_file_until_the_first_note(self):
        self.assertIsNone(self.file())
        self.assertEqual(I.list_issues(self.ep), [])
        I.add(self.ep, "proxy", self.first_shot(), "wrong side")
        self.assertIsNotNone(self.file())

    def test_it_goes_when_the_last_issue_goes(self):
        I.add(self.ep, "proxy", self.first_shot(), "wrong side")
        I.clear(self.ep)
        self.assertIsNone(self.file())
        self.assertFalse(os.path.exists(I.issues_path(self.ep)))

    def test_an_unreadable_file_is_reported_not_started_over(self):
        """Losing a list of notes silently is the one thing this must not do."""
        with io.open(I.issues_path(self.ep), "w", encoding="utf-8") as fh:
            fh.write("{ not json")
        with self.assertRaises(I.IssueError) as cm:
            I.list_issues(self.ep)
        self.assertEqual(cm.exception.status, 500)

    def test_a_file_from_a_newer_version(self):
        with io.open(I.issues_path(self.ep), "w", encoding="utf-8") as fh:
            json.dump({"version": I.VERSION + 1, "items": []}, fh)
        with self.assertRaises(I.IssueError) as cm:
            I.list_issues(self.ep)
        self.assertIn("newer h3pipe", str(cm.exception))

    def test_the_cut_json_and_sidecars_are_not_touched(self):
        before = T.load_cut(self.ep)
        I.add(self.ep, "proxy", self.first_shot(), "wrong side")
        I.clear(self.ep)
        self.assertEqual(T.load_cut(self.ep), before)


class TestSnapshot(IssuesCase):
    def test_it_records_what_produced_the_take(self):
        shot = self.first_shot()
        item = I.add(self.ep, "proxy", shot, "Ada enters from the wrong side")
        self.assertEqual(item["shot"], shot)
        self.assertEqual(item["pass"], "proxy")
        self.assertEqual(item["note"], "Ada enters from the wrong side")
        self.assertTrue(item["id"])
        self.assertTrue(item["when"])
        self.assertTrue(item["shot_hash"])
        self.assertTrue(item["target"])
        self.assertIn(f"## {shot}", item["script"])
        self.assertTrue(item["prompt"])
        self.assertTrue(item["refs"])
        self.assertTrue(all("role" in r and "path" in r for r in item["refs"]))

    def test_the_script_excerpt_is_that_shots_lines(self):
        shot = self.first_shot()
        item = I.add(self.ep, "proxy", shot, "x")
        lines = item["script"].splitlines()
        self.assertTrue(lines[0].startswith(f"## {shot}"))
        # it stops before the next shot
        self.assertEqual(sum(1 for line in lines if line.startswith("## ")), 1)

    def test_editing_the_script_afterwards_does_not_change_it(self):
        """The whole point: the note is about the shot as it was rendered."""
        shot = self.first_shot()
        item = I.add(self.ep, "proxy", shot, "x")
        kept = item["script"]
        self.write_script(self.script_text().replace(f"## {shot}", f"## {shot}\n// rewritten"))
        self.assertEqual(I.list_issues(self.ep)[0]["script"], kept)
        self.assertNotIn("rewritten", I.list_issues(self.ep)[0]["script"])

    def test_a_shot_that_is_not_in_the_pass(self):
        with self.assertRaises(I.IssueError) as cm:
            I.add(self.ep, "proxy", "sh999", "x")
        self.assertEqual(cm.exception.status, 404)
        self.assertIsNone(self.file())

    def test_a_note_that_says_nothing(self):
        for bad in ("", "   ", None, 7):
            with self.subTest(note=bad):
                with self.assertRaises(I.IssueError) as cm:
                    I.add(self.ep, "proxy", self.first_shot(), bad)
                self.assertEqual(cm.exception.status, 400)

    def test_a_note_longer_than_the_limit(self):
        with self.assertRaises(I.IssueError):
            I.add(self.ep, "proxy", self.first_shot(), "x" * (I.MAX_NOTE + 1))

    def test_the_note_is_trimmed(self):
        item = I.add(self.ep, "proxy", self.first_shot(), "  too dark  ")
        self.assertEqual(item["note"], "too dark")

    def test_a_take_can_be_named(self):
        item = I.add(self.ep, "proxy", self.first_shot(), "x", take=3)
        self.assertEqual(item["take"], 3)

    def test_no_takes_yet_is_still_worth_noting(self):
        """A structural note can come before anything has rendered."""
        item = I.add(self.ep, "proxy", self.first_shot(), "this shot shouldn't exist")
        self.assertIsNone(item["take"])
        self.assertTrue(item["script"])


class TestAddressed(IssuesCase):
    def test_an_untouched_shot_is_not_addressed(self):
        I.add(self.ep, "proxy", self.first_shot(), "x")
        self.assertFalse(I.list_issues(self.ep)[0]["addressed"])

    def action_line(self, item: dict) -> str:
        """An action line of the shot the issue is about — taken from its own
        snapshot, so the edit lands on that shot and no other."""
        return next(x for x in item["script"].splitlines()
                    if x and not x.startswith("#") and ":" not in x.split(" ")[0])

    def test_a_rebuilt_shot_is(self):
        shot = self.first_shot()
        item = I.add(self.ep, "proxy", shot, "Ada enters from the wrong side")
        line = self.action_line(item)
        self.write_script(self.script_text().replace(line, line.rstrip(".") + ", from the left.", 1))
        self.assertTrue(E.build_episode(self.ep)["ok"])
        self.assertTrue(I.list_issues(self.ep)[0]["addressed"])

    def test_rebuilding_a_different_shot_leaves_it_alone(self):
        """The hash is the shot's, not the episode's: a rebuild on its own
        addresses nothing."""
        import h3jobs as J
        shots = [x["id"] for x in J.load_shotlists(self.ep, "proxy")[0]["shots"][:2]]
        item = I.add(self.ep, "proxy", shots[0], "x")
        other = I.add(self.ep, "proxy", shots[1], "y")
        line = self.action_line(other)
        self.write_script(self.script_text().replace(line, line.rstrip(".") + ", slowly.", 1))
        self.assertTrue(E.build_episode(self.ep)["ok"])
        by_id = {x["id"]: x for x in I.list_issues(self.ep)}
        self.assertFalse(by_id[item["id"]]["addressed"])
        self.assertTrue(by_id[other["id"]]["addressed"])

    def test_a_rebuild_that_changes_nothing_addresses_nothing(self):
        I.add(self.ep, "proxy", self.first_shot(), "x")
        self.assertTrue(E.build_episode(self.ep)["ok"])
        self.assertFalse(I.list_issues(self.ep)[0]["addressed"])

    def test_a_newer_usable_take_is(self):
        shot = self.first_shot()
        I.add(self.ep, "proxy", shot, "x", take=1)
        self.assertFalse(I.list_issues(self.ep)[0]["addressed"])
        # take 2 finishes: the note was about take 1
        t = T.reserve_take(self.ep, "proxy", shot, {"status": "ok", "ep": self.ep})
        self.assertEqual(t.take, 1)
        t2 = T.reserve_take(self.ep, "proxy", shot, {"status": "ok", "ep": self.ep})
        self.assertEqual(t2.take, 2)
        self.assertTrue(I.list_issues(self.ep)[0]["addressed"])

    def test_it_is_never_written_to_the_file(self):
        I.add(self.ep, "proxy", self.first_shot(), "x")
        I.list_issues(self.ep)
        self.assertNotIn("addressed", self.file()["items"][0])

    def test_a_shot_that_left_the_pass_counts_as_addressed(self):
        shot = self.first_shot()
        I.add(self.ep, "proxy", shot, "x")
        text = self.script_text()
        start = text.index(f"## {shot}")
        end = text.index("## ", start + 3)
        self.write_script(text[:start] + text[end:])          # the shot is gone
        self.assertTrue(E.build_episode(self.ep)["ok"])
        self.assertTrue(I.list_issues(self.ep)[0]["addressed"])


class TestResolveAndClear(IssuesCase):
    def three(self):
        import h3jobs as J
        shots = [s["id"] for s in J.load_shotlists(self.ep, "proxy")[0]["shots"][:3]]
        return [I.add(self.ep, "proxy", s, f"note for {s}") for s in shots]

    def test_resolve_drops_only_the_named(self):
        items = self.three()
        self.assertEqual(I.resolve(self.ep, [items[1]["id"]]), 1)
        left = [x["shot"] for x in I.list_issues(self.ep)]
        self.assertEqual(left, [items[0]["shot"], items[2]["shot"]])

    def test_resolve_an_id_that_is_not_there(self):
        self.three()
        self.assertEqual(I.resolve(self.ep, ["nope"]), 0)
        self.assertEqual(len(I.list_issues(self.ep)), 3)

    def test_clear_takes_everything(self):
        self.three()
        self.assertEqual(I.clear(self.ep), 3)
        self.assertEqual(I.list_issues(self.ep), [])

    def test_clear_one_pass_leaves_the_other(self):
        shot = self.first_shot()
        I.add(self.ep, "proxy", shot, "proxy note")
        I.add(self.ep, "final", shot, "final note")
        self.assertEqual(I.clear(self.ep, "proxy"), 1)
        left = I.list_issues(self.ep)
        self.assertEqual([x["pass"] for x in left], ["final"])

    def test_clear_addressed_leaves_the_rest(self):
        items = self.three()
        # make the first one addressed by rebuilding its own shot
        line = next(x for x in items[0]["script"].splitlines() if x.startswith("sound:"))
        self.write_script(self.script_text().replace(line, line + " and a fridge hum", 1))
        self.assertTrue(E.build_episode(self.ep)["ok"])
        addressed = [x["id"] for x in I.list_issues(self.ep) if x["addressed"]]
        self.assertTrue(addressed)
        gone = I.clear(self.ep, "proxy", addressed_only=True)
        self.assertEqual(gone, len(addressed))
        left = {x["id"] for x in I.list_issues(self.ep)}
        self.assertEqual(left, {x["id"] for x in items} - set(addressed))

    def test_listing_one_pass(self):
        shot = self.first_shot()
        I.add(self.ep, "proxy", shot, "a")
        I.add(self.ep, "final", shot, "b")
        self.assertEqual(len(I.list_issues(self.ep)), 2)
        self.assertEqual(len(I.list_issues(self.ep, "proxy")), 1)


class TestExport(IssuesCase):
    def test_markdown_carries_the_note_and_what_produced_it(self):
        shot = self.first_shot()
        I.add(self.ep, "proxy", shot, "Ada enters from the wrong side")
        md = I.export_markdown(self.ep, "proxy")
        self.assertIn("# ks01: 1 issue (proxy pass)", md)
        self.assertIn("propose the smallest edit", md)          # the instruction line
        self.assertIn(f"## {shot}", md)
        self.assertIn("Ada enters from the wrong side", md)
        self.assertIn("**The script, as rendered:**", md)
        self.assertIn("**What the pipeline compiled from it:**", md)
        self.assertIn("**Reference pictures it used:**", md)
        self.assertEqual(md.count("```") % 2, 0)                # every fence closed

    def test_it_carries_no_machine_markers(self):
        """Nothing reads the markdown back, so it has nothing to parse."""
        I.add(self.ep, "proxy", self.first_shot(), "x")
        md = I.export_markdown(self.ep, "proxy")
        self.assertNotIn("<!--", md)
        self.assertNotIn("h3pipe:issue", md)

    def test_addressed_issues_are_left_out_unless_asked_for(self):
        shot = self.first_shot()
        item = I.add(self.ep, "proxy", shot, "the one note")
        line = next(x for x in item["script"].splitlines() if x.startswith("sound:"))
        self.write_script(self.script_text().replace(line, line + " and a fridge hum", 1))
        self.assertTrue(E.build_episode(self.ep)["ok"])
        self.assertIn("Nothing noted", I.export_markdown(self.ep, "proxy"))
        both = I.export_markdown(self.ep, "proxy", include_addressed=True)
        self.assertIn("the one note", both)
        self.assertIn("rebuilt or re-rendered since", both)

    def test_nothing_noted_still_makes_a_document(self):
        md = I.export_markdown(self.ep, "proxy")
        self.assertIn("0 issues", md)
        self.assertIn("*Nothing noted.*", md)

    def test_json_export_is_the_same_issues(self):
        shot = self.first_shot()
        I.add(self.ep, "proxy", shot, "x")
        j = I.export_json(self.ep, "proxy")
        self.assertEqual(j["pass"], "proxy")
        self.assertTrue(j["instruction"])
        self.assertEqual([x["shot"] for x in j["issues"]], [shot])
        json.dumps(j)                                           # serialisable


class TestCli(IssuesCase):
    def run_cli(self, *args: str):
        return subprocess.run([sys.executable, os.path.join(ROOT, "h3.py"), "issues",
                               self.ep, *args], capture_output=True, text=True, cwd=ROOT)

    def test_add_list_export_clear(self):
        shot = self.first_shot()
        out = self.run_cli("--proxy", "--add", shot, "the truck is on the wrong side")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("the truck is on the wrong side", out.stdout)

        out = self.run_cli("--proxy")
        self.assertIn(shot, out.stdout)
        self.assertIn("1 issue", out.stdout)

        out = self.run_cli("--proxy", "--export")
        self.assertIn("**The script, as rendered:**", out.stdout)

        out = self.run_cli("--clear")
        self.assertIn("1 cleared", out.stdout)
        self.assertFalse(os.path.exists(I.issues_path(self.ep)))

    def test_export_to_a_file_and_as_json(self):
        self.run_cli("--proxy", "--add", self.first_shot(), "x")
        dst = os.path.join(self._tmp.name, "issues.md")
        out = self.run_cli("--proxy", "--export", "-o", dst)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        with io.open(dst, encoding="utf-8") as fh:
            self.assertIn("propose the smallest edit", fh.read())
        out = self.run_cli("--proxy", "--export", "--json")
        json.loads(out.stdout)

    def test_add_from_a_file(self):
        import h3jobs as J
        shots = [s["id"] for s in J.load_shotlists(self.ep, "proxy")[0]["shots"][:2]]
        notes = os.path.join(self._tmp.name, "notes.txt")
        with io.open(notes, "w", encoding="utf-8") as fh:
            fh.write(f"# while watching\n\n{shots[0]}: too dark\n{shots[1]}: wrong side\n")
        out = self.run_cli("--proxy", "--add-from", notes)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("2 noted", out.stdout)
        self.assertEqual(len(I.list_issues(self.ep, "proxy")), 2)

    def test_a_bad_line_in_that_file(self):
        notes = os.path.join(self._tmp.name, "notes.txt")
        with io.open(notes, "w", encoding="utf-8") as fh:
            fh.write("this line names no shot\n")
        out = self.run_cli("--proxy", "--add-from", notes)
        self.assertEqual(out.returncode, 1)
        self.assertIn("expected", out.stdout)

    def test_resolve_by_id(self):
        shot = self.first_shot()
        item = I.add(self.ep, "proxy", shot, "x")
        out = self.run_cli("--resolve", item["id"])
        self.assertIn("1 resolved", out.stdout)
        self.assertEqual(I.list_issues(self.ep), [])

    def test_an_empty_notepad_says_so(self):
        out = self.run_cli("--proxy")
        self.assertEqual(out.returncode, 0)
        self.assertIn("no issues noted", out.stdout)

    def test_an_unknown_flag(self):
        out = self.run_cli("--bogus")
        self.assertEqual(out.returncode, 2)
        self.assertIn("--bogus", out.stdout)

    def test_a_shot_that_is_not_in_the_pass(self):
        out = self.run_cli("--proxy", "--add", "sh999", "x")
        self.assertEqual(out.returncode, 1)
        self.assertIn("sh999", out.stdout)


if __name__ == "__main__":
    unittest.main()
