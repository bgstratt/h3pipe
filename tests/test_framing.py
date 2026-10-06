"""
Shot sizes (h3core/framing.py): the names `size:` takes, their families, and
the framing the H3 Ref2VA prompt opens a shot with.

The three original sizes (close, medium, wide) must word exactly as before, so
a rebuilt episode's prompts don't change for them; the in-between sizes add
what the frame holds ("from the chest up").
"""
from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from h3core import framing  # noqa: E402
from h3core.story import ScriptError, parse_script  # noqa: E402
from targets.video.minimax_h3_ref2va.compile import RECIPE  # noqa: E402
from test_h3_prompt import SERIES  # noqa: E402
from targets.video.minimax_h3_ref2va.prompt import build_prompt  # noqa: E402


def opening(size: str, subjects=("ada",)) -> str:
    """The [Shot 1] line's framing sentence(s), up to the action."""
    shot = {"_subjects": list(subjects), "_policy": "generate", "_audio_ref": False,
            "_retention": "", "dialogue": [], "action": "ACTION.", "size": size,
            "camera": "", "sound": "", "music": "", "extras": "", "text": "",
            "plate": "kitchen"}
    desc = build_prompt(shot, {"location_key": "kitchen"}, SERIES, panels=1)[3]
    line = next(ln for ln in desc.split("\n") if ln.startswith("[Shot 1]"))
    return line[len("[Shot 1] "):line.index("ACTION.")].strip()


class NamesTest(unittest.TestCase):
    def test_any_spelling_of_a_size(self):
        for name in ("MCU", "mcu", "medium close-up", "Medium-Close-Up", "medium close"):
            self.assertEqual(framing.canonical(name), "mcu", name)
        for name, want in (("ECU", "ecu"), ("xcu", "ecu"), ("cowboy", "cowboy"),
                           ("American shot", "cowboy"), ("MWS", "mws"), ("medium wide", "mws"),
                           ("full shot", "fs"), ("FS", "fs"), ("long shot", "ws"), ("LS", "ws"),
                           ("EWS", "ews"), ("extreme wide shot", "ews"), ("ELS", "ews")):
            self.assertEqual(framing.canonical(name), want, name)

    def test_the_original_three_keep_their_meaning(self):
        for names, want in ((("close", "cu"), "cu"), (("medium", "ms"), "ms"),
                            (("wide", "ws"), "ws")):
            for n in names:
                self.assertEqual(framing.canonical(n), want)

    def test_families(self):
        self.assertEqual({c for c in framing.SIZES if framing.family(c) == "close"},
                         {"ecu", "cu", "mcu"})
        self.assertEqual({c for c in framing.SIZES if framing.family(c) == "medium"},
                         {"ms", "cowboy", "mws"})
        self.assertEqual({c for c in framing.SIZES if framing.family(c) == "wide"},
                         {"fs", "ws", "ews"})
        self.assertEqual(framing.family("not a size"), "medium")    # an old, hand-edited list

    def test_extreme_alone_is_not_a_size(self):
        self.assertIsNone(framing.canonical("extreme"))               # close or wide?

    def test_face_sizes_match_any_spelling(self):
        self.assertTrue(framing.in_sizes("medium close-up", RECIPE["face_sizes"]))
        self.assertTrue(framing.in_sizes("close", RECIPE["face_sizes"]))
        self.assertFalse(framing.in_sizes("cowboy", RECIPE["face_sizes"]))


class ParserTest(unittest.TestCase):
    SCRIPT = "= ep01  Test\n\n# sq01  kitchen\n\n## sh010\nwho: ada\nsize: {size}\ndur: 3.04\n\nAda turns.\n"

    def test_a_new_size_parses_and_is_kept_as_written(self):
        ep = parse_script(self.SCRIPT.format(size="Medium Close-Up"), {"ada"}, {"ada"})
        self.assertEqual(ep["sequences"][0]["shots"][0]["size"], "medium close-up")

    def test_an_unknown_size_names_every_size(self):
        with self.assertRaises(ScriptError) as cm:
            parse_script(self.SCRIPT.format(size="extreme"), {"ada"}, {"ada"})
        self.assertIn("medium close-up (mcu)", str(cm.exception))
        self.assertIn("extreme wide shot (ews", str(cm.exception))


class OpeningTest(unittest.TestCase):
    def test_the_original_three_word_as_before(self):
        self.assertTrue(opening("close").startswith(
            "A close-up frames <Subject 1>, filling most of the frame. Behind them,"))
        self.assertEqual(opening("medium"), "A medium shot frames <Subject 1> in <Subject 2>, a "
                         "cramped diner kitchen, showing the part of the space directly behind them.")
        self.assertEqual(opening("wide"),
                         "A wide shot takes in <Subject 2>, a cramped diner kitchen, with <Subject 1> in it.")

    def test_the_in_between_sizes_say_what_the_frame_holds(self):
        self.assertTrue(opening("mcu").startswith(
            "A medium close-up frames <Subject 1> from the chest up, filling most of the frame."))
        self.assertTrue(opening("ecu").startswith(
            "An extreme close-up frames <Subject 1>, a single detail filling the frame."))
        self.assertTrue(opening("cowboy").startswith(
            "A cowboy shot frames <Subject 1> from mid-thigh up in <Subject 2>"))
        self.assertTrue(opening("mws").startswith(
            "A medium-wide shot frames <Subject 1> from the knees up in <Subject 2>"))
        self.assertEqual(opening("fs"), "A full shot takes in <Subject 2>, a cramped diner "
                         "kitchen, with <Subject 1> in it from head to toe.")
        self.assertEqual(opening("ews"), "An extreme wide shot takes in <Subject 2>, a cramped "
                         "diner kitchen, with <Subject 1> in it, small within the vastness of "
                         "the setting.")

    def test_close_family_keeps_the_room_behind_the_face(self):
        for size in ("ecu", "mcu"):
            self.assertIn("never a plain studio backdrop", opening(size), size)


if __name__ == "__main__":
    unittest.main()
