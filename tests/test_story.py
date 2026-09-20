"""Tests for the story IR (h3core): the parser, line spans, JSON round trip, and
that shots.json stays free of H3 vocabulary."""
from __future__ import annotations

import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import h3build  # noqa: E402
from h3core import ir  # noqa: E402
from h3core.series_config import (character_ids, load_series_config,  # noqa: E402
                                  series_info, subject_ids, variant_of)
from h3core.story import ScriptError, parse_script, parse_story  # noqa: E402
from test_golden import fixtures  # noqa: E402

# Words that only mean something to H3 (or to a compiled prompt). None may
# appear in the IR's structure or in any value the parser derived.
H3_WORDS = ["frame", "<Picture", "<Subject", "<Audio", "retention", "fully_copy",
            "partially_copy", "panel", "17k", "length", "snap", "prompt"]
# Text copied verbatim from the script. Authors may write "snaps his fingers" or
# "out of frame" there; that is the story, not H3 leaking in.
PROSE = ("action", "camera", "sound", "music", "extras", "text")
# Shot keys the parser stores as written, where compile treats "" as unset.
RAW = {"camera", "sound", "music", "extras", "text", "plate", "policy", "retention",
       "model", "lora"}


def load_story(series_cfg_path: str, script_path: str) -> tuple[ir.Episode, dict, str]:
    series_cfg = load_series_config(series_cfg_path)
    with open(script_path, encoding="utf-8") as fh:
        text = fh.read()
    story = parse_story(text, subject_ids(series_cfg), character_ids(series_cfg),
                        series_info(series_cfg), variant_of(series_cfg))
    return story, series_cfg, text


def without_prose(doc: dict) -> dict:
    doc = json.loads(json.dumps(doc))
    for sq in doc["sequences"]:
        for sh in sq["shots"]:
            for k in PROSE:
                sh[k] = None
            for d in sh["dialogue"]:
                d["line"] = d["delivery"] = ""
    return doc


class FixtureTest(unittest.TestCase):
    """Every golden fixture, including the local real episodes when present."""

    def test_round_trip(self):
        for name, (series_cfg, script, _g) in fixtures().items():
            with self.subTest(fixture=name):
                story, _, _ = load_story(series_cfg, script)
                self.assertEqual(ir.Episode.from_json(story.to_json()), story)
                self.assertEqual(ir.Episode.loads(story.dumps()), story)
                self.assertEqual(json.loads(story.dumps()), story.to_json())

    def test_no_h3_vocabulary(self):
        for name, (series_cfg, script, gdir) in fixtures().items():
            with self.subTest(fixture=name):
                golden = os.path.join(gdir, "shots.json")
                self.assertTrue(os.path.isfile(golden), golden)
                with open(golden, encoding="utf-8") as fh:
                    doc = json.load(fh)
                story, _, _ = load_story(series_cfg, script)
                self.assertEqual(doc, story.to_json())
                flat = json.dumps(without_prose(doc), ensure_ascii=False).lower()
                for word in H3_WORDS:
                    self.assertNotIn(word.lower(), flat, f"{name}: '{word}' in shots.json")

    def test_ir_carries_everything_compile_reads(self):
        """The dict compile gets back from the IR is the parser's dict, give or
        take what compile provably ignores."""
        keep_seq = {"id", "location_key", "continuous", "shots", "model", "lora", "steps",
                    "profile", "target"}
        for name, (series_cfg_path, script, _g) in fixtures().items():
            with self.subTest(fixture=name):
                story, series_cfg, text = load_story(series_cfg_path, script)
                want = parse_script(text, subject_ids(series_cfg),
                                   character_ids(series_cfg), variant_of(series_cfg))
                for sq in want["sequences"]:
                    for k in set(sq) - keep_seq:
                        del sq[k]
                    for k in [k for k in ("model", "lora") if not sq.get(k)]:
                        sq.pop(k, None)
                    for sh in sq["shots"]:
                        for k in [k for k, v in sh.items() if v == "" and k in RAW]:
                            del sh[k]          # an empty value means "not set"
                        sh["plate"] = sh.get("plate") or sq["location_key"]
                        if "audio_in" in sh or sh.get("duration_auto"):
                            sh.pop("duration", None)
                        if "retention" in sh:
                            sh["retention"] = (sh["retention"].strip().lower()
                                               if sh["retention"].strip().lower()
                                               in h3build.RETENTIONS else sh["retention"])
                        if "steps" in sh:
                            sh["steps"] = int(sh["steps"])
                    if "steps" in sq:
                        sq["steps"] = int(sq["steps"])
                self.assertEqual(h3build.legacy_episode(story), want)


SCRIPT = """\
= ep09  Spans and Things

# sq01  street
Scene-setting prose.

## sh010
who: ada
size: WIDE
dur: 3.04
Ada walks down the street.
She stops.
// trailing comment, not part of the shot

## sh020
who: ada
with: kettle
audio: 1.00-2.50
dur: 9
policy: dub_keep_foley
retention: Partially_Copy
steps: 6
model: m.safetensors
camera: pushes in on Ada
ADA (V.O., into phone): Hello there.
BO (O.S.): Hi.
BO (grumbling): Yes.

# sq02  kitchen
continuous: yes
lora: l.safetensors
steps: eight

## sh030
plate: kitchen_window
dur: auto
pace: fast
retention: bogus
lora:
BO: One two.

## sh040
who: bo
retention: reference
Bo waits.
"""

SUBJECTS = {"ada", "bo", "kettle"}
CHARS = {"ada", "bo"}


class StoryTest(unittest.TestCase):
    def setUp(self):
        self.lines = SCRIPT.splitlines()
        self.ep = parse_story(SCRIPT, SUBJECTS, CHARS, {"fps": 24, "width": 1344, "height": 768})
        self.shots = {s.id: s for s in self.ep.shots()}

    def at(self, text: str) -> int:
        return self.lines.index(text) + 1

    def test_header(self):
        self.assertEqual((self.ep.id, self.ep.title), ("ep09", "Spans and Things"))
        self.assertEqual(self.ep.series, {"fps": 24, "width": 1344, "height": 768})
        self.assertEqual([s.id for s in self.ep.sequences], ["sq01", "sq02"])

    def test_line_spans(self):
        sq1, sq2 = self.ep.sequences
        self.assertEqual(sq1.source, {"line": self.at("# sq01  street")})
        self.assertEqual(sq2.source, {"line": self.at("# sq02  kitchen")})
        # a shot runs from its header to its last content line: the trailing
        # comment and blank lines belong to nobody
        self.assertEqual(self.shots["sh010"].source,
                         {"line": self.at("## sh010"), "end_line": self.at("She stops.")})
        self.assertEqual(self.shots["sh020"].source,
                         {"line": self.at("## sh020"),
                          "end_line": self.at("BO (grumbling): Yes.")})
        self.assertEqual(self.shots["sh030"].source,
                         {"line": self.at("## sh030"), "end_line": self.at("BO: One two.")})
        # the last shot ends on the file's last line
        self.assertEqual(self.shots["sh040"].source,
                         {"line": self.at("## sh040"), "end_line": len(self.lines)})
        for s in self.ep.shots():
            first, last = s.source["line"], s.source["end_line"]
            self.assertEqual(self.lines[first - 1], f"## {s.id}")
            self.assertLessEqual(first, last)

    def test_cast_props_plate_size(self):
        a, b, c, d = (self.shots[k] for k in ("sh010", "sh020", "sh030", "sh040"))
        self.assertEqual((a.cast, a.props, a.plate, a.size), (["ada"], [], "street", "wide"))
        # V.O. and O.S. speakers stay off the visible cast; on-screen ones join it
        self.assertEqual((b.cast, b.props), (["ada", "bo"], ["kettle"]))
        self.assertEqual(c.plate, "kitchen_window")
        self.assertEqual(c.cast, ["bo"])
        self.assertEqual(d.size, "medium")

    def test_dialogue(self):
        self.assertEqual(self.shots["sh020"].dialogue, [
            ir.Line("ada", "vo", "into phone", "Hello there."),
            ir.Line("bo", "os", "", "Hi."),
            ir.Line("bo", "on", "grumbling", "Yes."),
        ])

    def test_action_and_prose(self):
        a, b = self.shots["sh010"], self.shots["sh020"]
        self.assertEqual(a.action, "Ada walks down the street. She stops.")
        self.assertIsNone(a.camera)
        self.assertEqual(b.camera, "pushes in on Ada")
        self.assertEqual(b.action, "")

    def test_timing(self):
        self.assertEqual(self.shots["sh010"].timing, {"seconds": 3.04})
        # an audio window wins over dur:, as it does in compile
        self.assertEqual(self.shots["sh020"].timing, {"audio_in": 1.0, "audio_out": 2.5})
        self.assertEqual(self.shots["sh030"].timing, {"auto": True})
        self.assertEqual(self.shots["sh030"].pace, "fast")
        self.assertIsNone(self.shots["sh010"].pace)
        self.assertIsNone(self.shots["sh040"].timing)

    def test_audio_and_preserve(self):
        self.assertEqual(self.shots["sh020"].audio, "dub_keep_foley")
        self.assertIsNone(self.shots["sh010"].audio)
        self.assertEqual(self.shots["sh020"].preserve, "loose")
        self.assertEqual(self.shots["sh040"].preserve, "style")
        self.assertIsNone(self.shots["sh010"].preserve)
        # an unknown word is carried, not mapped, so the target can reject it
        self.assertIsNone(self.shots["sh030"].preserve)
        self.assertEqual(self.shots["sh030"].unparsed, {"preserve": "bogus"})

    def test_overrides_seed_key_continuous(self):
        sq1, sq2 = self.ep.sequences
        self.assertFalse(sq1.continuous)
        self.assertTrue(sq2.continuous)
        self.assertEqual(sq1.overrides, {"model": None, "lora": None, "steps": None})
        self.assertEqual(sq2.overrides, {"model": None, "lora": "l.safetensors", "steps": None})
        self.assertEqual(sq2.unparsed, {"steps": "eight"})
        self.assertEqual(self.shots["sh020"].overrides,
                         {"model": "m.safetensors", "lora": None, "steps": 6})
        # `lora:` with no value means "not set"
        self.assertEqual(self.shots["sh030"].overrides["lora"], None)
        self.assertEqual(self.shots["sh030"].seed_key, "ep09/sq02/sh030")

    def test_round_trip_and_json(self):
        doc = json.loads(self.ep.dumps())
        self.assertEqual(ir.Episode.from_json(doc), self.ep)
        self.assertNotIn("unparsed", doc["sequences"][0])
        self.assertIn("unparsed", doc["sequences"][1])

    def test_legacy_parser_unchanged(self):
        ep = parse_script(SCRIPT, SUBJECTS, CHARS)
        self.assertEqual(ep["sequences"][0]["shots"][1]["dialogue"][0],
                         {"who": "ada", "mode": "vo", "delivery": "into phone",
                          "line": "Hello there."})
        self.assertNotIn("_line", ep["sequences"][0]["shots"][0])
        self.assertNotIn("_end", ep["sequences"][0]["shots"][0])

    def test_errors_keep_their_line(self):
        with self.assertRaises(ScriptError) as cm:
            parse_story(SCRIPT + "\n## sh010\nAda again.\n", SUBJECTS, CHARS)
        self.assertIn(f"line {len(self.lines) + 2}:", str(cm.exception))

    def test_reexported_from_h3build(self):
        import h3core.speech
        import h3core.story
        for name in ("parse_script", "split_parenthetical", "ScriptError", "VO_TOKENS",
                     "OS_TOKENS"):
            self.assertIs(getattr(h3build, name), getattr(h3core.story, name))
        for name in ("syllables", "pacing", "speech_seconds", "forced_rate", "SPEECH_RATE"):
            self.assertIs(getattr(h3build, name), getattr(h3core.speech, name))
        self.assertIs(h3build.stable_seed, ir.stable_seed)
        self.assertEqual(ir.stable_seed("ep01", "sq01", "sh010"),
                         ir.stable_seed(*"ep01/sq01/sh010".split("/")))


if __name__ == "__main__":
    unittest.main()
