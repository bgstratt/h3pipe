"""
Phase 13e4 (docs/PLAN.md): h3master — an episode's master in one action.

The plan (each shot of the cut: upscale / ok / kept / queued / gap, by the
series recipe), queueing its upscales, what it keeps, the strict assembly into
<episode>/master/ with its report, the route and `h3.py master`.
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "comfy_nodes"))

import h3jobs as J  # noqa: E402
import h3master as M  # noqa: E402
import h3pipe_api as A  # noqa: E402
import h3takes as T  # noqa: E402
import h3upscale as U  # noqa: E402
from test_api import ApiTest  # noqa: E402
from test_phase13 import UPSCALER  # noqa: E402

RECIPE = {"deliver": "1080p", "fit": "crop", "quality": "master",
          "targets": {"minimax_h3_*": {"method": "latent"},
                      "*": {"method": "pixel", "pixel_model": "RealESRGAN_x2.pth"}}}


class MasterTest(ApiTest):
    def setUp(self):
        super().setUp()
        self.comfy.nodes |= set(U.UPSCALE_NODES) | set(U.PIXEL_NODES)
        self.comfy.info["MinimaxH3LatentUpscaler3D"] = UPSCALER
        self.comfy.info["UpscaleModelLoader"] = {"input": {"required": {
            "model_name": [["RealESRGAN_x2.pth", "4x-UltraSharp.pth"], {}]}}}

    def set_recipe(self, recipe):
        p = os.path.join(self.ep, "series.json")
        cfg = json.load(open(p, encoding="utf-8"))
        cfg.setdefault("upscale", {})["master"] = recipe
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh)

    def render_all(self) -> list[str]:
        """Every shot's final take; the shots that rendered."""
        self.render(pass_="final", allow_missing_refs=True)
        return [s for s in J.script_order(self.ep) if T.get_take(self.ep, "final", s, 1)]

    def by_shot(self, plan):
        return {r.shot: r for r in plan.rows}

    def test_needs_a_recipe_with_a_size(self):
        with self.assertRaises(M.MasterError):
            M.plan_master(self.ep)
        self.set_recipe({"targets": {"*": {"method": "pixel"}}})
        with self.assertRaisesRegex(M.MasterError, "deliver size"):
            M.plan_master(self.ep)
        self.err(A.post_master(self.ctx, {"ep": self.ep}), 409)

    def test_plan_queue_keep_and_conform(self):
        rendered = self.render_all()
        self.assertTrue(rendered)
        self.set_recipe(RECIPE)
        plan = M.plan_master(self.ep)
        rows = self.by_shot(plan)
        self.assertEqual({rows[s].status for s in rendered}, {"upscale"})
        unrendered = [r for r in plan.rows if r.shot not in rendered]
        self.assertTrue(all(r.status == "gap" for r in unrendered))
        self.assertFalse(plan.ready)
        # queue: each rendered shot by its target's section, at the master size
        queued, errors = M.queue_master(plan, self.ctx.comfy, self.ctx.comfy_url)
        self.assertEqual(([r.shot for r in queued], errors), (rendered, []))
        rec = T.upscale_of(T.get_take(self.ep, "final", rendered[0], 1))
        self.assertEqual((rec["status"], rec["width"], rec["height"], rec["quality"]),
                         ("ok", 1920, 1080, "master"))
        plan = M.plan_master(self.ep)
        self.assertEqual({self.by_shot(plan)[s].status for s in rendered}, {"ok"})
        self.assertEqual(M.queue_master(plan, self.ctx.comfy, self.ctx.comfy_url), ([], []))   # nothing to redo
        # the recipe changes: kept, not redone, unless --conform; a Keep stays even then
        a, b = rendered[0], rendered[-1]
        U.set_keep(T.get_take(self.ep, "final", a, 1), True)
        self.set_recipe({**RECIPE, "targets": {"*": {"method": "pixel", "pixel_model": "4x-UltraSharp.pth"}}})
        rows = self.by_shot(M.plan_master(self.ep))
        self.assertEqual((rows[a].status, rows[a].why), ("kept", "marked Keep"))
        self.assertEqual(rows[b].status, "kept")
        self.assertIn("other settings", rows[b].why)
        rows = self.by_shot(M.plan_master(self.ep, conform=True))
        self.assertEqual((rows[a].status, rows[b].status), ("kept", "upscale"))
        # a kept upscale at another size can't go in the master
        self.set_recipe({**RECIPE, "deliver": "4k"})
        rows = self.by_shot(M.plan_master(self.ep))
        self.assertEqual(rows[a].status, "gap")
        self.assertIn("1920x1080, the master 3840x2160", rows[a].why)
        # a shot whose target the recipe doesn't cover is a gap
        self.set_recipe({**RECIPE, "targets": {"wan22_*": {"method": "pixel"}}})
        self.assertTrue(all(r.status == "gap" for r in M.plan_master(self.ep).rows))

    def test_assemble_strict_and_report(self):
        rendered = self.render_all()
        self.set_recipe(RECIPE)
        plan = M.plan_master(self.ep)
        with self.assertRaisesRegex(M.MasterError, "still to upscale"):
            M.assemble_master(plan)
        M.queue_master(plan, self.ctx.comfy, self.ctx.comfy_url)
        plan = M.plan_master(self.ep)
        gaps = plan.of("gap")
        if gaps:
            with self.assertRaisesRegex(M.MasterError, "gap"):
                M.assemble_master(plan)
        name = M.master_name(self.ep, (1920, 1080))
        self.assertEqual(name, "ks01_master_1920x1080")
        calls = []

        def fake_tool(script, args, cwd, timeout):
            calls.append(args)
            with open(os.path.join(self.ep, "renders", name + ".mp4"), "wb") as fh:
                fh.write(b"master")
            with open(os.path.join(self.ep, "renders", name + "_shots.txt"), "w") as fh:
                fh.write("#\n")
            return 0, f"  -> renders/{name}.mp4\n", ""

        import h3edit
        with mock.patch.object(h3edit, "run_tool", fake_tool):
            res = M.assemble_master(plan, allow_gaps=True)
        self.assertTrue(res["ok"], res)
        args = calls[0]
        for want in ("--upscaled", "--size", "1920x1080", "--quality", "master", "--name"):
            self.assertIn(want, args)
        self.assertEqual("--partial" in args, bool(gaps))
        self.assertEqual(res["output"], os.path.join(self.ep, "master", name + ".mp4"))
        self.assertTrue(os.path.isfile(os.path.join(self.ep, "master", name + "_shots.txt")))
        rep = json.load(open(os.path.join(self.ep, "master", "ks01_master.json"), encoding="utf-8"))
        self.assertEqual((rep["size"], rep["quality"], rep["output"]),
                         ([1920, 1080], "master", f"master/{name}.mp4"))
        row = next(r for r in rep["rows"] if r["shot"] == rendered[0])
        self.assertEqual((row["status"], row["upscale"]["quality"]), ("ok", "master"))
        md = open(res["report_md"], encoding="utf-8").read()
        self.assertIn("| shot | take | target | status | recipe | note |", md)

    def test_route(self):
        rendered = self.render_all()
        self.set_recipe(RECIPE)
        res = self.ok(A.post_master(self.ctx, {"ep": self.ep}))
        self.assertEqual(res["plan"]["counts"]["upscale"], len(rendered))
        self.assertEqual(res["plan"]["size"], [1920, 1080])
        res = self.ok(A.post_master(self.ctx, {"ep": self.ep, "action": "queue"}))
        self.assertEqual(res["queued"], rendered)
        self.assertEqual(res["plan"]["counts"]["upscale"], 0)
        self.assertTrue(any(d["status"] == "queued" for d in self.events_of("h3pipe.upscale")))
        res = self.ok(A.post_master(self.ctx, {"ep": self.ep}))
        self.assertEqual(res["plan"]["counts"]["ok"], len(rendered))
        if res["plan"]["counts"]["gap"]:
            self.err(A.post_master(self.ctx, {"ep": self.ep, "action": "assemble"}), 409)
        self.err(A.post_master(self.ctx, {"ep": self.ep, "action": "burn"}), 400)
        self.err(A.post_master(self.ctx, {"ep": self.ep, "conform": "yes"}), 400)

    def test_queue_order(self):
        """In cut order by default; grouped by what ComfyUI loads on request, each
        group where its first shot is, cut order within it."""
        from types import SimpleNamespace as NS

        def row(shot, method, target, model=""):
            job = NS(method=method, target=NS(id=target), then_method="pixel", then_model="",
                     seedvr2_model=model, pixel_model=model)
            return M.Row(shot, None, "upscale", job=job)
        rows = [row("sh010", "latent", "minimax_h3_ref2va"), row("sh020", "latent", "ltx2"),
                row("sh030", "latent", "minimax_h3_ref2va"), row("sh040", "pixel", "wan22_i2v", "x2.pth"),
                row("sh050", "latent", "ltx2"), row("sh060", "pixel", "wan22_vace", "x2.pth"),
                row("sh070", "seedvr2", "wan22_i2v", "7b")]
        self.assertEqual([r.shot for r in M.in_order(rows)], [r.shot for r in rows])
        self.assertEqual([r.shot for r in M.in_order(rows, "target")],
                         ["sh010", "sh030", "sh020", "sh050", "sh040", "sh060", "sh070"])
        with self.assertRaises(M.MasterError):
            M.in_order(rows, "random")
        self.render_all()
        self.set_recipe(RECIPE)
        self.err(A.post_master(self.ctx, {"ep": self.ep, "action": "queue", "order": "random"}), 400)
        res = self.ok(A.post_master(self.ctx, {"ep": self.ep, "action": "queue", "order": "target"}))
        self.assertTrue(res["queued"])

    def test_cli_check_and_show_folders(self):
        self.render_all()
        self.set_recipe(RECIPE)
        self.assertEqual(M.episode_roots([self.shows]), [self.ep])            # a show folder
        self.assertEqual(M.episode_roots([self.ep]), [self.ep])
        with mock.patch("sys.stdout"):
            self.assertEqual(M.main([self.ep, "--check"]), 0)
        self.assertEqual({r.status for r in M.plan_master(self.ep).rows} - {"gap"}, {"upscale"})


if __name__ == "__main__":
    unittest.main()
