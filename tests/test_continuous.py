"""
`continuous:` (docs/CONTINUOUS.md): how a shot follows the previous one. The
parser and the IR -- a shot's own line, a `#` header's default for the shots
after its first, `none` breaking the chain, the old forms read as the new ones
with a warning -- and the first keyframe a `first` shot gets.
"""
from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import h3build  # noqa: E402
from h3core.story import ScriptError, parse_story  # noqa: E402


def story(body: str):
    text = "= e01  Test\n" + body
    return parse_story(text, {"ada"}, {"ada"})


def shots(st):
    return {sh.id: (sh, sq) for sq in st.sequences for sh in sq.shots}


SEQ = """# sq01  kitchen
continuous: first
## sh010
who: ada
Ada waits.
## sh020
who: ada
Ada waits more.
## sh030
continuous: none
who: ada
Ada leaves.
# sq02  street
## sh040
continuous: latent
who: ada
Ada walks.
## sh050
who: ada
Ada stops.
"""


class ParseTest(unittest.TestCase):
    def test_a_header_covers_the_shots_after_its_first(self):
        by = shots(story(SEQ))
        self.assertEqual({k: sh.continues(sq) for k, (sh, sq) in by.items()},
                         {"sh010": None, "sh020": "first", "sh030": None,
                          "sh040": "latent", "sh050": None})

    def test_first_makes_the_first_keyframe_continuity(self):
        by = shots(story(SEQ))
        sh, sq = by["sh020"]
        self.assertEqual(sh.keyframe("first", sq), "continuity")
        sh, sq = by["sh010"]
        self.assertIsNone(sh.keyframe("first", sq))
        sh, sq = by["sh040"]                      # latent isn't a keyframe
        self.assertIsNone(sh.keyframe("first", sq))

    def test_the_old_forms_read_as_the_new_ones_with_a_warning(self):
        st = story("""# sq01  kitchen
continuous: yes
## sh010
who: ada
Ada waits.
## sh020
first: continuity
who: ada
Ada waits more.
""")
        by = shots(st)
        self.assertEqual(st.sequences[0].continuous, "latent")
        sh, sq = by["sh020"]
        self.assertEqual(sh.continuous, "first")         # its own old line beats the header
        self.assertIsNone(sh.first)
        warnings = h3build.story_warnings(st, {})
        self.assertTrue(any("`continuous: yes`" in w and "continuous: latent" in w for w in warnings))
        self.assertTrue(any("`first: continuity` on shot sh020" in w for w in warnings))

    def test_overlap(self):
        st = story("""# sq01  kitchen
overlap: 22
## sh010
who: ada
Ada waits.
## sh020
continuous: latent
overlap: 39
who: ada
Ada waits more.
""")
        self.assertEqual(st.sequences[0].overlap, 22)
        self.assertEqual(shots(st)["sh020"][0].overlap, 39)

    def test_what_is_refused(self):
        for body, why in (("## sh010\ncontinuous: yes\nwho: ada\nAda.\n", "must be first"),
                          ("## sh010\ncontinuous: frame\nwho: ada\nAda.\n", "must be first"),
                          ("## sh010\noverlap: 0\nwho: ada\nAda.\n", "1 to 360"),
                          ("## sh010\nfirst: continue\nwho: ada\nAda.\n", "continuous: first")):
            with self.subTest(body=body), self.assertRaisesRegex(ScriptError, why):
                story("# sq01  kitchen\n" + body)

    def test_the_ir_round_trips(self):
        from h3core.ir import Episode
        st = story(SEQ)
        back = Episode.from_json(st.to_json())
        self.assertEqual({sh.id: sh.continues(sq) for sq in back.sequences for sh in sq.shots},
                         {sh.id: sh.continues(sq) for sq in st.sequences for sh in sq.shots})
        # an old IR's `"continuous": true` was the chaining `latent` names
        d = st.to_json()
        d["sequences"][1]["continuous"] = True
        self.assertEqual(Episode.from_json(d).sequences[1].continuous, "latent")


if __name__ == "__main__":
    unittest.main()
