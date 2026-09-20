"""
The generated documentation stays in step with the code.

  - INSTALL.md's model list is what tools/make_models_md.py emits today, so a
    target that gains, loses or renames a model file cannot leave the install
    instructions pointing at the old one (`python tools/make_models_md.py`).
  - The generated block is honest: every file it lists is one some target
    names, with the folder and tier that target gives it, and a URL only where
    target.json records one.
  - prompts/ is what tools/make_prompts.py emits from docs/AUTHORING.md today,
    so an edit to the authoring guide cannot leave the script-writing skill
    describing the old format (`python tools/make_prompts.py`). The committed
    copies are the skill's text (SKILL.md) and the same guide as assistant
    instructions; build/skill/ is gitignored and rebuilt from the same source.
"""
from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
import make_models_md as M  # noqa: E402
import make_prompts as P  # noqa: E402
import targets as TG  # noqa: E402


class TestScriptSkill(unittest.TestCase):
    """The skill and the assistant instructions are docs/AUTHORING.md."""

    def generated(self) -> dict:
        """What `python tools/make_prompts.py --no-bundle` would write now."""
        text = P.body()
        return {
            "prompts/SKILL.md": P.skill_text(text),
            "prompts/h3-script.instructions.md":
                "# h3pipe episode script — assistant instructions\n\nPaste this into Cursor rules, Copilot instructions, a custom GPT, or any\nsystem prompt. Generated from docs/AUTHORING.md by tools/make_prompts.py.\n\n" f"{P.PREAMBLE}\n\n{text}",
        }

    def test_up_to_date(self):
        for rel, want in self.generated().items():
            with self.subTest(file=rel):
                path = os.path.join(ROOT, *rel.split("/"))
                self.assertTrue(os.path.isfile(path), path)
                with open(path, encoding="utf-8") as fh:
                    got = fh.read()
                self.assertEqual(
                    got.replace("\r\n", "\n"), want.replace("\r\n", "\n"),
                    f"{rel} is out of date -- run `python tools/make_prompts.py` "
                    f"and commit the result")


class TestModelsBlock(unittest.TestCase):
    def setUp(self):
        with open(M.INSTALL, encoding="utf-8") as fh:
            self.text = fh.read()

    def test_install_md_has_the_markers(self):
        self.assertIn(M.BEGIN, self.text)
        self.assertIn(M.END, self.text)
        self.assertLess(self.text.index(M.BEGIN), self.text.index(M.END))

    def test_up_to_date(self):
        """Regenerate and compare, the way `--check` does."""
        self.assertEqual(
            M.splice(self.text, M.block()), self.text,
            "INSTALL.md's model list is out of date — run "
            "`python tools/make_models_md.py` and commit the result")

    def test_every_named_file_is_listed(self):
        """Every model file any target names has a row, in its folder."""
        block = M.block()
        for t in TG.list_targets():
            models = t.models
            for name, param in t.named_files().items():
                if param not in models:
                    continue
                with self.subTest(target=t.id, file=name):
                    self.assertIn(f"`{name}`", block)

    def test_no_invented_urls(self):
        """A row's link is a URL some target.json records, and a file with no
        record says so instead."""
        recorded = set()
        for t in TG.list_targets():
            for rec in t.downloads.values():
                if rec.get("url"):
                    recorded.add(rec["url"])
        for r in M.collect():
            with self.subTest(file=r["file"]):
                if r["url"]:
                    self.assertIn(r["url"], recorded)
                else:
                    self.assertIn("**none recorded**", M.link(r))
                    self.assertTrue(r["source"])

    def test_minimum_set_is_the_default_targets(self):
        self.assertEqual(M.MINIMUM, (TG.DEFAULT_VIDEO_TARGET, TG.DEFAULT_IMAGE_TARGET))
        minimum = [r for r in M.collect()
                   if any(u["target"] in M.MINIMUM for u in r["uses"])]
        self.assertTrue(minimum)
        for r in minimum:
            with self.subTest(file=r["file"]):
                self.assertIn(r["tier"], TG.TIERS)
                # the minimum tables name only the two default targets, even
                # for a file others share (krea2's VAE is also Wan 2.2's)
                cell = M.targets_cell(r, M.MINIMUM)
                self.assertTrue(cell)
                named = {b.strip().split(" ")[0].strip("`") for b in cell.split(",")}
                self.assertTrue(named <= set(M.MINIMUM), cell)


if __name__ == "__main__":
    unittest.main()
