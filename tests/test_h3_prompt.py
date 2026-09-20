"""
The H3 Ref2VA prompt's headcount rule.

A reference image holding two figures gets drawn as two people, so each
subject's definition says its picture holds exactly one character. How many
characters are in the SHOT is a different claim, and it is made once, by name,
at the end of subject_definitions — saying it per subject gave every character
in a two-hander the sentence "Exactly one person appears in this shot", one
contradiction per subject, in exactly the shots where H3 settles a
contradiction by duplicating somebody.
"""
from __future__ import annotations

import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from targets.video.minimax_h3_ref2va.prompt import build_prompt  # noqa: E402

SERIES = {
    "style": {"look": "a flat vector cartoon"},
    "locations": {"kitchen": {"description": "a cramped diner kitchen"}},
    "subjects": {
        "ada": {"kind": "character", "name": "Ada", "design": "a tall woman",
                "voice": "dry"},
        "bo": {"kind": "character", "name": "Bo", "design": "a stocky boy"},
        "cy": {"kind": "character", "name": "Cy", "design": "a small girl"},
        "rex": {"name": "Rex", "design": "a scruffy terrier"},      # no kind: a character
        "kettle": {"kind": "prop", "name": "the kettle", "design": "a dented kettle"},
    },
}


def defs_of(subjects, **over) -> list[str]:
    """subject_definitions for a one-shot scene, as lines."""
    shot = {"_subjects": list(subjects), "_policy": "generate", "_audio_ref": False,
            "_retention": "", "dialogue": [], "action": "They stand there.",
            "size": "medium", "camera": "", "sound": "", "music": "", "extras": "",
            "text": "", "plate": "kitchen"}
    shot.update(over)
    out = build_prompt(shot, {"location_key": "kitchen"}, SERIES, panels=1)
    return out[0].split("\n")


def headcount(subjects, **over) -> str | None:
    """The one line that counts the shot's characters, if there is one."""
    lines = [ln for ln in defs_of(subjects, **over)
             if "in this shot" in ln or "each appear" in ln or "appears exactly once" in ln]
    assert len(lines) <= 1, lines          # the whole point: it is said once
    return lines[0] if lines else None


class HeadcountTest(unittest.TestCase):
    def test_one_character_is_named(self):
        self.assertEqual(headcount(["ada"]),
                         "Exactly one character appears in this shot: <Subject 1>.")

    def test_two_characters_are_counted_and_named(self):
        self.assertEqual(
            headcount(["ada", "bo"]),
            "Exactly two characters appear in this shot: <Subject 1> and <Subject 2>. "
            "They are different characters, and never duplicates of one another.")

    def test_three_characters(self):
        self.assertIn("Exactly three characters appear in this shot: <Subject 1>, "
                      "<Subject 2>, and <Subject 3>.", headcount(["ada", "bo", "cy"]))

    def test_props_are_not_characters(self):
        self.assertEqual(headcount(["ada", "kettle"]),
                         "Exactly one character appears in this shot: <Subject 1>.")

    def test_a_shot_of_props_alone_claims_no_headcount(self):
        self.assertIsNone(headcount(["kettle"]))

    def test_an_animal_with_no_kind_counts_as_a_character(self):
        """`kind` defaults to character so it can speak; the count says
        'characters' rather than 'people' for exactly this cast."""
        line = headcount(["cy", "rex"])
        self.assertIn("Exactly two characters", line)
        self.assertNotIn("people", line)

    def test_extras_drop_the_count_and_keep_the_once_each(self):
        line = headcount(["ada", "bo"], extras="two customers at the counter")
        self.assertTrue(line.startswith("<Subject 1> and <Subject 2> each appear exactly "
                                        "once in this shot."), line)
        self.assertIn("two customers at the counter", line)
        self.assertIn("never duplicates of them", line)
        self.assertNotIn("Exactly two characters", line)

    def test_extras_with_one_character_reads_singular(self):
        line = headcount(["ada"], extras="a crowd on the pavement")
        self.assertTrue(line.startswith("<Subject 1> appears exactly once in this shot."), line)
        self.assertIn("nothing like that character", line)

    def test_a_subject_with_no_picture_still_counts(self):
        """--render-anyway: an unreferenced character is in frame, in words."""
        line = headcount(["ada"], _unreferenced=["bo"])
        self.assertEqual(line, "Exactly two characters appear in this shot: <Subject 1> "
                               "and Bo. They are different characters, and never "
                               "duplicates of one another.")

    def test_the_per_subject_clause_only_describes_its_picture(self):
        lines = defs_of(["ada", "bo"])
        for ln in lines[1:3]:
            self.assertIn("Exactly one person appears in that image.", ln)
            self.assertNotIn("in this shot", ln)


class GoldenShapeTest(unittest.TestCase):
    """The rule holds in a real built episode, not just in hand-made dicts."""

    def test_no_built_shot_says_it_twice(self):
        with open(os.path.join(HERE, "golden", "kitchen_sink", "shotlist.json"),
                  encoding="utf-8") as fh:
            shots = json.load(fh)["shots"]
        self.assertTrue(shots)
        for sh in shots:
            defs = sh["prompt"][0]
            self.assertLessEqual(defs.count("in this shot"), 1, sh["id"])


if __name__ == "__main__":
    unittest.main()
