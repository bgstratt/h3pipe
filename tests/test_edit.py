"""
Phase 1 exit check, end to end through h3.py against a fake ComfyUI:
redo gets a new seed; an overridden prompt renders and survives a rebuild;
the cut can use a non-latest take; a script edit marks takes script-stale.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
import h3edit as E  # noqa: E402
import h3jobs as J  # noqa: E402
import h3takes as T  # noqa: E402
from test_render import ENV, FIXTURE, WORKFLOW, FakeComfy, stub_refs  # noqa: E402


class EditFlowTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ep = os.path.join(self._tmp.name, "ks01")
        os.makedirs(self.ep)
        shutil.copy(os.path.join(FIXTURE, "series.json"), self.ep)
        shutil.copy(os.path.join(FIXTURE, "script.md"), os.path.join(self.ep, "ks01.md"))
        self.comfy = FakeComfy()

    def tearDown(self):
        self.comfy.close()
        self._tmp.cleanup()

    def h3(self, *args, ok=True) -> str:
        r = subprocess.run([sys.executable, os.path.join(ROOT, "h3.py"), *args],
                           capture_output=True, env=ENV, cwd=self._tmp.name)
        out = r.stdout.decode("utf-8") + r.stderr.decode("utf-8")
        if ok:
            self.assertEqual(r.returncode, 0, out)
        if args[:1] == ("build",):
            stub_refs(self.ep)
        return out

    def render(self, *args) -> str:
        return self.h3("render", self.ep, "--proxy", "--workflow", WORKFLOW,
                       "--comfy", self.comfy.url, *args)

    def status(self) -> dict:
        return {s["shot"]: s for s in E.episode_status(self.ep, "proxy")["shots"]}

    def test_phase1_exit_check(self):
        self.h3("build", self.ep)
        self.render()
        self.render("--only", "sh020", "--redo")
        st = self.status()["sh020"]
        self.assertEqual([t["take"] for t in st["takes"]], [1, 2])
        self.assertEqual(st["takes"][1]["seed_source"], "new")
        self.assertEqual(st["cut"]["take"], 2)                 # latest by default

        # override the prompt from a file; it must survive a rebuild
        pf = os.path.join(self._tmp.name, "p.txt")
        with open(pf, "w", encoding="utf-8") as fh:
            fh.write("A hand-tuned prompt.\n")
        out = self.h3("override", self.ep, "sh020", "--prompt-file", pf, "--proxy",
                      "--steps", "9")
        self.assertIn("steps  9", out)
        self.h3("build", self.ep)
        dumped = self.h3("override", self.ep, "sh020", "--dump-prompt", "--proxy")
        self.assertEqual(dumped.strip(), "A hand-tuned prompt.")
        self.render("--only", "sh020", "--redo")
        t3 = T.get_take(self.ep, "proxy", "sh020", 3)
        frozen = T.read_json(t3.paths.shotlist)["shots"][0]
        self.assertEqual((frozen["prompt"], frozen["steps"]), ("A hand-tuned prompt.", 9))
        self.assertEqual(t3.sidecar["overrides"], ["prompt", "steps"])

        # the cut uses an earlier take; assemble follows it
        self.h3("pick", self.ep, "sh020", "1", "--proxy")
        self.assertEqual(self.status()["sh020"]["cut"], {**self.status()["sh020"]["cut"],
                                                          "take": 1, "picked": True})
        cut = T.load_cut(self.ep)
        self.assertIn({"shot": "sh020", "take": 1}, cut["proxy"])
        asm = self.h3("assemble", self.ep, "--proxy", "--check", "--partial", ok=False)
        row = next(l for l in asm.splitlines() if " sh020 " in l)
        self.assertIn("t01", row)
        takes_out = self.h3("takes", self.ep, "--proxy", "--no-sweep")
        self.assertRegex(takes_out, r"t01 .*<- cut \(picked\)")

        # back to latest
        self.h3("pick", self.ep, "sh020", "latest", "--proxy")
        self.assertEqual(self.status()["sh020"]["cut"]["take"], 3)

        # a script edit makes that shot's takes (and its override) stale
        md = os.path.join(self.ep, "ks01.md")
        with open(md, encoding="utf-8") as fh:
            text = fh.read()
        text = text.replace("Bo sets the kettle down on the burner",
                            "Bo slams the kettle down on the burner")
        with open(md, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        self.h3("build", self.ep)
        st = self.status()
        self.assertTrue(all("script" in t["stale"] for t in st["sh020"]["takes"]))
        self.assertTrue(st["sh020"]["override"]["stale"])
        self.assertEqual(st["sh010"]["takes"][0]["stale"], [])
        self.assertIn("(STALE)", self.h3("takes", self.ep, "--proxy", "--no-sweep"))

    def test_pick_refuses_unusable_and_unknown(self):
        self.h3("build", self.ep)
        T.reserve_take(self.ep, "proxy", "sh010", {"status": "queued"})
        out = self.h3("pick", self.ep, "sh010", "1", "--proxy", ok=False)
        self.assertIn("queued", out)
        self.assertIn("no take 7", self.h3("pick", self.ep, "sh010", "7", "--proxy", ok=False))
        self.h3("pick", self.ep, "sh010", "1", "--proxy", "--force")
        self.assertFalse(os.path.isfile(os.path.join(self.ep, "overrides.json")))

    def test_fresh_override_is_not_stale_in_either_pass(self):
        # the proxy builds a different prompt (generated audio) from the final;
        # an override stamped against one must not read stale in the other
        self.h3("build", self.ep)
        pf = os.path.join(self._tmp.name, "p.txt")
        with open(pf, "w", encoding="utf-8") as fh:
            fh.write("tuned")
        out = self.h3("override", self.ep, "sh020", "--prompt-file", pf, "--both")
        self.assertNotIn("STALE", out)
        ov = T.load_overrides(self.ep)
        self.assertEqual(T.shot_override(ov, "sh020", "final")["prompt"], "tuned")
        self.assertEqual(T.shot_override(ov, "sh020", "proxy")["prompt"], "tuned")
        self.assertNotEqual(T.shot_override(ov, "sh020", "final")["base_hash"],
                            T.shot_override(ov, "sh020", "proxy")["base_hash"])
        for p in ("final", "proxy"):
            st = {s["shot"]: s for s in E.episode_status(self.ep, p)["shots"]}
            self.assertFalse(st["sh020"]["override"]["stale"], p)

    def test_override_clear(self):
        self.h3("build", self.ep)
        self.h3("override", self.ep, "sh010", "--seed", "5", "--lora", "a:0.5",
                "--lora", "b", "--both")
        ov = T.load_overrides(self.ep)
        self.assertEqual(T.shot_override(ov, "sh010", "proxy")["loras"],
                         [{"name": "a", "strength": 0.5}, {"name": "b", "strength": 1.0}])
        self.assertEqual(T.shot_override(ov, "sh010", "final")["seed"], 5)
        self.h3("override", self.ep, "sh010", "--clear", "loras", "--proxy")
        ov = T.load_overrides(self.ep)
        self.assertNotIn("loras", T.shot_override(ov, "sh010", "proxy"))
        self.assertIn("loras", T.shot_override(ov, "sh010", "final"))
        self.h3("override", self.ep, "sh010", "--clear")
        self.assertEqual(T.load_overrides(self.ep)["shots"], {})


class LibraryTest(unittest.TestCase):
    """The operations the CLI and the editor routes share (the routes' own
    tests, test_api.py, drive them end to end)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ep = os.path.join(self._tmp.name, "ks01")
        os.makedirs(self.ep)
        shutil.copy(os.path.join(FIXTURE, "series.json"), self.ep)
        shutil.copy(os.path.join(FIXTURE, "script.md"), os.path.join(self.ep, "ks01.md"))

    def tearDown(self):
        self._tmp.cleanup()

    def test_build_then_override_stamps_and_clears(self):
        r = E.build_episode(self.ep)
        self.assertTrue(r["ok"], r)
        built = E.pass_builds(self.ep, "sh020")
        self.assertEqual(set(built), {"final", "proxy"})
        ov = T.load_overrides(self.ep)
        E.set_shot_override(ov, "sh020", built, ["proxy"], {"seed": 7}, {"steps": 3})
        self.assertEqual(T.shot_override(ov, "sh020", "proxy"),
                         {"seed": 7, "steps": 3, "base_hash": J.story_hash(built["proxy"])})
        # clearing a pass field doesn't restamp; the last one takes the pass block with it
        E.set_shot_override(ov, "sh020", built, ["proxy"], None, {"steps": None})
        self.assertEqual(T.shot_override(ov, "sh020", "proxy"), {"seed": 7})
        view = E.override_view(ov, "sh020", built)
        self.assertEqual(view["final"], {"seed": 7, "stale": False})
        E.clear_shot_override(ov, "sh020")
        self.assertEqual(ov["shots"], {})

    def test_build_reports_a_script_error(self):
        with open(os.path.join(self.ep, "ks01.md"), "w", encoding="utf-8") as fh:
            fh.write("no header\n")
        r = E.build_episode(self.ep)
        self.assertFalse(r["ok"])
        self.assertIn("line 1", r["passes"]["final"]["error"])
        self.assertNotIn(chr(13), r["passes"]["final"]["error"])

    def test_run_tool_puts_the_pipeline_on_sys_path(self):
        # h3assemble imports h3takes; that must work even where `python
        # script.py` wouldn't add the script's folder (an embedded Python)
        rc, out, err = E.run_tool("h3assemble.py", ["-o", self.ep, "--check"],
                                  self._tmp.name, 60)
        self.assertNotIn("ModuleNotFoundError", err)
        self.assertIn("no shotlist", err)


if __name__ == "__main__":
    unittest.main()
