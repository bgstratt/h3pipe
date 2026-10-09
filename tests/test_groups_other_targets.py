"""
Group subjects (`members: N`) on the targets other than H3 Ref2VA: their
reference sheets send a group's picture whole (never one panel of a 4-panel
strip, which would cut a row of four people to one), and their prompts call it
N different people, each once. A supplied single portrait (`sheet_panels: 1`)
is sent whole the same way.
"""
from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from targets.video.ltx2 import compile as LTX  # noqa: E402
from targets.video.ltx2.prompt import _subject_sentence  # noqa: E402
from targets.video.ltx2_ingredients import compile as ING  # noqa: E402
from targets.video.ltx2_ingredients.prompt import panel_text  # noqa: E402
from targets.video.wan22_vace import compile as VACE  # noqa: E402

LADIES = {"kind": "character", "name": "the ladies", "members": 4, "sheet_panels": 1,
          "design": "four women in ball gowns", "sheet": "refs/ladies.png"}
ADA = {"kind": "character", "name": "Ada", "design": "a tall woman", "sheet": "refs/ada.png"}


class PromptTest(unittest.TestCase):
    def test_a_group_is_n_people_each_once(self):
        self.assertEqual(_subject_sentence("the ladies", "four women in ball gowns", LADIES),
                         "the ladies are 4 different people, each appearing once: four women "
                         "in ball gowns")
        self.assertEqual(_subject_sentence("Ada", "a tall woman", ADA), "Ada is a tall woman")

    def test_the_ingredients_panel_names_the_group(self):
        text = panel_text({"subject": "ladies", "kind": "character"},
                          {"subjects": {"ladies": LADIES}})
        self.assertIn("a group of 4 different people, each appearing once", text)


class SheetTest(unittest.TestCase):
    def test_ltx2_gives_a_whole_picture_no_view(self):
        if not LTX.SHEET:
            self.skipTest("this ltx2 recipe has no reference sheet")
        doc = {"subjects": {"ladies": {"kind": "character", "sheet": "refs/ladies.png",
                                       "sheet_panels": 1},
                            "ada": {"kind": "character", "sheet": "refs/ada.png"}}}
        panels = LTX.sheet_panels(doc, {"subjects": ["ada", "ladies"], "size": "wide"})
        by = {p["subject"]: p for p in panels}
        self.assertNotIn("view", by["ladies"])
        self.assertIn("view", by["ada"])

    def test_the_sheet_crops_only_a_panel_with_a_view(self):
        job = SimpleNamespace(root=ROOT, width=1280, height=720)
        panels = [{"subject": "ada", "kind": "character", "path": "a.png", "view": "body"},
                  {"subject": "ladies", "kind": "character", "path": "l.png"}]
        for spec in (ING.sheet_spec(job, panels), VACE.reference_spec(job, panels)):
            with self.subTest(spec=spec["panels"][0].get("crop")):
                self.assertIn("crop", spec["panels"][0])
                self.assertNotIn("crop", spec["panels"][1])


if __name__ == "__main__":
    unittest.main()
