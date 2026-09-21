"""
What the build says when the series config or the script is wrong in a way no
target can fix.

Two kinds. A missing series-config field used to surface as a bare KeyError
attributed to the script (`error in ep01.md: 'name'`); it is a sentence about
series.json now. And a scene whose speakers all share one plate builds fine
but cuts badly, so it is a warning, printed by every build and `--check` and
carried into the editor's Script window by h3source.check_text.
"""
from __future__ import annotations

import copy
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import h3build as B  # noqa: E402
from h3core.series_config import series_config_from  # noqa: E402
from h3core.story import parse_story  # noqa: E402

CFG = {
    "series": {"id": "t", "title": "T", "fps": 24},
    "style": {"look": "a flat cartoon"},
    "subjects": {
        "ada": {"kind": "character", "name": "Ada", "design": "a tall woman",
                "sheet": "refs/ada/ada_sheet_4panel.png"},
        "bo": {"kind": "character", "name": "Bo", "design": "a stocky boy",
               "sheet": "refs/bo/bo_sheet_4panel.png"},
    },
    "audio": {"mode": "generate"},          # no recording, so dialogue is generated
    "locations": {
        "kitchen": {"description": "a cramped diner kitchen", "plate": "refs/_bg/k.png"},
        "kitchen_on_ada": {"description": "the kitchen from Bo's side",
                           "plate": "refs/_bg/k_ada.png"},
        "kitchen_on_bo": {"description": "the kitchen from Ada's side",
                          "plate": "refs/_bg/k_bo.png"},
    },
}


def cfg(**changes) -> dict:
    d = copy.deepcopy(CFG)
    for path, value in changes.items():
        keys = path.split("__")
        node = d
        for k in keys[:-1]:
            node = node[k]
        if value is None:
            node.pop(keys[-1], None)
        else:
            node[keys[-1]] = value
    return d


def story(text: str, c: dict):
    from h3core.series_config import character_ids, series_info, subject_ids, variant_of
    return parse_story(text, subject_ids(c), character_ids(c), series_info(c), variant_of(c))


class RequiredFieldsTest(unittest.TestCase):
    """A missing field names itself, and names series.json."""

    def bad(self, **changes) -> str:
        with self.assertRaises(ValueError) as cm:
            series_config_from(cfg(**changes))
        return str(cm.exception)

    def test_the_series_block(self):
        self.assertIn("needs a `series` block", self.bad(series=None))

    def test_the_look(self):
        self.assertIn("needs `style.look`", self.bad(style=None))
        self.assertIn("needs `style.look`", self.bad(style__look=""))

    def test_a_subjects_name(self):
        self.assertIn("subject 'ada' needs a `name`", self.bad(subjects__ada__name=None))

    def test_a_subjects_design(self):
        self.assertIn("subject 'ada' needs a `design`", self.bad(subjects__ada__design=None))

    def test_a_voice_only_character_needs_no_design(self):
        """A narrator who is never on screen is drawn by nobody."""
        c = cfg(subjects__ada__design=None)
        c["subjects"]["ada"]["voice"] = "warm, unhurried"
        self.assertEqual(series_config_from(c)["subjects"]["ada"].get("design"), None)

    def test_a_locations_description(self):
        self.assertIn("location 'kitchen' needs a `description`",
                      self.bad(locations__kitchen__description=None))

    def test_a_good_config_still_loads(self):
        self.assertEqual(series_config_from(cfg())["subjects"]["ada"]["name"], "Ada")


class OnScreenWithoutDesignTest(unittest.TestCase):
    """Legal in the series config, impossible in a shot."""

    def test_a_voice_only_character_on_screen_is_an_error(self):
        c = cfg(subjects__ada__design=None)
        c["subjects"]["ada"]["voice"] = "warm, unhurried"
        c = series_config_from(c)
        st = story("= ep01 T\n\n# sq01 kitchen\n\n## sh010\nwho: ada\ndur: 3\nShe waits.\n", c)
        with self.assertRaises(ValueError) as cm:
            B.check_story(st, c)
        self.assertIn("sh010", str(cm.exception))
        self.assertIn("no `design`", str(cm.exception))

    def test_off_screen_is_fine(self):
        c = cfg(subjects__ada__design=None)
        c["subjects"]["ada"]["voice"] = "warm, unhurried"
        c = series_config_from(c)
        st = story("= ep01 T\n\n# sq01 kitchen\n\n## sh010\nwho: bo\ndur: 3\nHe waits.\n"
                   "ADA (V.O.): Not yet.\n", c)
        B.check_story(st, c)                     # no raise


class SharedPlateWarningTest(unittest.TestCase):
    """Two speakers, one plate, one background: the cut reads as one camera."""

    def warn(self, text: str, c: dict | None = None) -> list[str]:
        c = series_config_from(c or cfg())
        return B.story_warnings(story(text, c), c)

    TWO_HANDER = ("= ep01 T\n\n# sq01 kitchen\n\n"
                  "## sh010\nwho: ada, bo\ndur: 3\nThey face each other.\n\n"
                  "## sh020\nwho: ada\ndur: 3\nShe looks at him.\nADA: Well.\n\n"
                  "## sh030\nwho: bo\ndur: 3\nHe looks back.\nBO: Well what.\n")

    def test_singles_on_one_plate_warn(self):
        w = self.warn(self.TWO_HANDER)
        self.assertEqual(len(w), 1)
        self.assertTrue(w[0].startswith("sequence sq01: "), w[0])
        self.assertIn("Ada and Bo", w[0])
        self.assertIn("kitchen", w[0])
        self.assertIn("`plate:`", w[0])

    def test_an_angle_each_is_quiet(self):
        text = self.TWO_HANDER.replace("who: ada\ndur: 3\nShe looks",
                                       "who: ada\nplate: kitchen_on_ada\ndur: 3\nShe looks")
        text = text.replace("who: bo\ndur: 3\nHe looks",
                            "who: bo\nplate: kitchen_on_bo\ndur: 3\nHe looks")
        self.assertEqual(self.warn(text), [])

    def test_one_person_alone_is_quiet(self):
        """A scene of one character in several shots is not a two-hander."""
        self.assertEqual(self.warn(
            "= ep01 T\n\n# sq01 kitchen\n\n"
            "## sh010\nwho: ada\ndur: 3\nShe wipes the counter.\n\n"
            "## sh020\nwho: ada\ndur: 3\nShe stops.\n"), [])

    def test_a_wardrobe_variant_is_the_same_person(self):
        """Dana in a towel and Dana in a coat taking turns on one plate is one
        character changing clothes, not two people talking."""
        c = cfg()
        c["subjects"]["ada_coat"] = {"of": "ada", "design": "Ada in a navy coat"}
        self.assertEqual(self.warn(
            "= ep01 T\n\n# sq01 kitchen\n\n"
            "## sh010\nwho: ada\ndur: 3\nShe wipes the counter.\n\n"
            "## sh020\nwho: ada_coat\ndur: 3\nShe comes back in a coat.\n", c), [])

    def test_two_shots_do_not_warn(self):
        """Only singles: a two-shot holds both people in one frame already."""
        self.assertEqual(self.warn(
            "= ep01 T\n\n# sq01 kitchen\n\n"
            "## sh010\nwho: ada, bo\ndur: 3\nThey talk.\n\n"
            "## sh020\nwho: bo, ada\ndur: 3\nThey keep talking.\n"), [])

    def test_the_warning_reaches_a_build(self):
        """compile_groups adds it, which is what --check prints and what the
        editor's Script window reads."""
        c = series_config_from(cfg())
        built = B.compile_groups(story(self.TWO_HANDER, c), c, "final")
        self.assertTrue(any(w.startswith("sequence sq01: ")
                            for w in built[0][2]["warnings"]), built[0][2]["warnings"])


if __name__ == "__main__":
    unittest.main()
