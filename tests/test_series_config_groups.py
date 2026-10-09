"""
Group subjects (`members: N`): loading checks them and gives their picture
`sheet_panels: 1`; the refs treat it as one whole wide picture with no views.
"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from h3core.series_config import members, series_config_from  # noqa: E402


def cfg(**ladies):
    e = {"kind": "character", "name": "the ladies", "design": "four women in gowns",
         "members": 4}
    e.update(ladies)
    return {"series": {"id": "s", "title": "S"}, "style": {"look": "photoreal"},
            "subjects": {"ladies": e, "ada": {"name": "Ada", "design": "a tall woman"}},
            "locations": {}}


class LoadTest(unittest.TestCase):
    def test_a_group_gets_one_whole_picture(self):
        c = series_config_from(cfg())
        self.assertEqual(c["subjects"]["ladies"]["sheet_panels"], 1)
        self.assertEqual(members(c["subjects"]["ladies"]), 4)
        self.assertNotIn("sheet_panels", c["subjects"]["ada"])
        self.assertEqual(members(c["subjects"]["ada"]), 0)

    def test_bad_counts_are_refused(self):
        for n in (1, 0, "4", 4.0, True, 13):
            with self.subTest(n=n), self.assertRaisesRegex(ValueError, "members"):
                series_config_from(cfg(members=n))

    def test_only_a_character_can_be_a_group(self):
        with self.assertRaisesRegex(ValueError, "only a character"):
            series_config_from(cfg(kind="prop"))

    def test_a_group_is_never_a_model_sheet(self):
        with self.assertRaisesRegex(ValueError, "never a model sheet"):
            series_config_from(cfg(sheet_panels=4))

    def test_a_variant_of_a_group_is_a_group(self):
        c = cfg(sheet="refs/ladies/ladies.png")
        c["subjects"]["ladies_cloaks"] = {"of": "ladies", "design": "the same four in cloaks"}
        c = series_config_from(c)
        self.assertEqual(members(c["subjects"]["ladies_cloaks"]), 4)
        self.assertEqual(c["subjects"]["ladies_cloaks"]["sheet_panels"], 1)


class RefTest(unittest.TestCase):
    def test_the_ref_is_one_wide_picture(self):
        import h3refs
        e = series_config_from(cfg())["subjects"]["ladies"]
        r = h3refs.Ref("subject:ladies", "series", "character", "the ladies", "", ROOT, e,
                       "ladies")
        self.assertFalse(r.has_views)
        self.assertEqual(r.views, [None])
        w, h = h3refs.gen_size(r)
        self.assertGreater(w, h)

    def test_the_wording_asks_for_n_people_in_a_row(self):
        from targets.image.krea2.prompt import group_prompt
        p = group_prompt("four women in gowns", "photoreal", 4)
        self.assertIn("a group of 4 different people", p)
        self.assertIn("nobody overlapping", p)


if __name__ == "__main__":
    unittest.main()
