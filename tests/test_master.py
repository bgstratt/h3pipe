"""
h3master — an episode's master in one action.

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

    def test_auto_scale(self):
        # the smallest eighth that lands on the grid and covers the delivery, no stretch
        self.assertEqual(U.auto_scale(960, 544, (1920, 1080)), 2.0)      # 1920x1088
        self.assertEqual(U.auto_scale(1344, 768, (1920, 1080)), 1.5)     # 2016x1152
        self.assertEqual(U.auto_scale(1024, 576, (1920, 1080)), 2.0)     # 2048x1152
        self.assertEqual(U.auto_scale(1344, 768, (3840, 2160)), 3.0)     # 4032x2304
        self.assertEqual(U.auto_scale(1344, 768, None, default=2.0), 2.0)
        self.assertEqual(U.check_fields({"method": "latent", "scale": "auto"}), [])
        self.assertTrue(U.check_fields({"method": "pixel", "scale": "auto"}))
        self.assertTrue(U.check_fields({"scale": "auto", "then": "RealESRGAN_x2.pth"}))

    def test_auto_scale_in_the_master(self):
        rendered = self.render_all()
        self.set_recipe({**RECIPE, "targets": {"minimax_h3_*": {"method": "latent", "scale": "auto"},
                                               "*": {"method": "pixel"}}})
        rows = self.by_shot(M.plan_master(self.ep))
        row = rows[rendered[0]]
        sc = row.take.sidecar
        want = U.auto_scale(sc["width"], sc["height"], (1920, 1080))
        self.assertEqual((row.status, row.job.scale), ("upscale", want))
        self.assertIn("smallest scale", row.recipe)
        self.assertEqual(U.check_recipe(U.master_recipe(self.ep)), [])

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

        def fake_tool(script, args, cwd, timeout, progress=None):
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
        for want in ("--upscaled", "--size", "1920x1080", "--quality", "master", "--name",
                     "--progress"):
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
        # no _titles: the master is made without an intro or outro, and says so
        self.assertEqual(len(calls), 1)                 # h3assemble only
        self.assertIsNone(res["titles"])
        self.assertIn("No intro or outro.", md)
        # _titles in the show folder: the master gets them, in place (h3publish)
        titles = os.path.join(os.path.dirname(self.ep), "_titles")
        os.makedirs(titles, exist_ok=True)
        for f in ("INTRO.mp4", "OUTRO.mp4"):
            open(os.path.join(titles, f), "wb").close()
        published = []

        def with_titles(script, args, cwd, timeout, progress=None, rc=0, said="  -> titled\n"):
            if script == "h3assemble.py":
                return fake_tool(script, args, cwd, timeout)
            published.append(args)
            return rc, said, ""

        with mock.patch.object(h3edit, "run_tool", with_titles):
            res = M.assemble_master(plan, allow_gaps=True)
        self.assertTrue(res["ok"], res)
        master = os.path.join(self.ep, "master", name + ".mp4")
        self.assertEqual(len(published), 1)
        pub = published[0]
        self.assertEqual(pub[pub.index("--input") + 1], master)
        self.assertEqual(pub[pub.index("--out") + 1], master)
        self.assertEqual(pub[pub.index("--quality") + 1], "master")
        self.assertTrue(res["titles"]["intro"].endswith("_titles/INTRO.mp4"))
        self.assertIn("Titles: intro", open(res["report_md"], encoding="utf-8").read())
        # h3publish failing: not ok, the untitled master left where it is
        with mock.patch.object(h3edit, "run_tool",
                               lambda s, a, c, t, progress=None:
                               with_titles(s, a, c, t, None, 1, "  !! no font\n")):
            res = M.assemble_master(plan, allow_gaps=True)
        self.assertFalse(res["ok"])
        self.assertIn("titles failed: no font", res["error"])
        self.assertTrue(os.path.isfile(master))

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

    # -- one run at a time, its progress, how the titles laid the picture in ----

    def ready_plan(self):
        """A cut whose upscales have all landed (gaps allowed), and the master's name."""
        self.render_all()
        self.set_recipe(RECIPE)
        M.queue_master(M.plan_master(self.ep), self.ctx.comfy, self.ctx.comfy_url)
        return M.plan_master(self.ep), M.master_name(self.ep, (1920, 1080))

    def talking_tool(self, name, picture="re-encoded", why="the cut is hevc, not H.264"):
        """A run_tool stand-in: h3assemble writes the master and says where it
        is; h3publish says what it did with the picture."""
        def tool(script, args, cwd, timeout, progress=None):
            if script == "h3assemble.py":
                progress("h3assemble", {"stage": "probe", "done": 0, "total": 2, "text": "sh010"})
                progress("h3assemble", {"stage": "clips", "done": 1, "total": 2, "text": "sh010"})
                with open(os.path.join(self.ep, "renders", name + ".mp4"), "wb") as fh:
                    fh.write(b"master")
                return 0, "", ""
            progress("h3publish", {"stage": "titles", "done": None, "total": None, "text": "intro"})
            progress("h3publish", {"stage": "done", "picture": picture, "why": why})
            return 0, "", ""
        return tool

    def make_titles(self):
        titles = os.path.join(os.path.dirname(self.ep), "_titles")
        os.makedirs(titles, exist_ok=True)
        for f in ("INTRO.mp4", "OUTRO.mp4"):
            open(os.path.join(titles, f), "wb").close()

    def test_progress_and_how_the_picture_went_in(self):
        import h3edit
        plan, name = self.ready_plan()
        self.make_titles()
        heard = []
        with mock.patch.object(h3edit, "run_tool", self.talking_tool(name)):
            res = M.assemble_master(plan, allow_gaps=True, progress=heard.append)
        self.assertTrue(res["ok"], res)
        self.assertIn({"step": "assemble", "stage": "clips", "done": 1, "total": 2, "text": "sh010"},
                      heard)
        self.assertIn("titles", [(e["step"], e["stage"]) for e in heard if e["step"] == "titles"][0])
        self.assertNotIn("done", [e["stage"] for e in heard])   # the result, not a step
        self.assertEqual((res["titles"]["picture"], res["titles"]["why"]),
                         ("re-encoded", "the cut is hevc, not H.264"))
        md = open(res["report_md"], encoding="utf-8").read()
        self.assertIn("Picture: the whole cut re-encoded when the titles went on, because "
                      "the cut is hevc, not H.264.", md)
        rep = json.load(open(os.path.join(self.ep, "master", "ks01_master.json"), encoding="utf-8"))
        self.assertEqual(rep["titles"]["picture"], "re-encoded")
        with mock.patch.object(h3edit, "run_tool", self.talking_tool(name, "copied", "")):
            res = M.assemble_master(plan, allow_gaps=True)
        self.assertIn("Picture: the cut copied as assembled; only the title clips were encoded.",
                      open(res["report_md"], encoding="utf-8").read())
        self.assertIsNone(M.assembling(self.ep))                 # the lock went with the run

    def test_one_run_at_a_time(self):
        import h3edit
        import subprocess
        plan, name = self.ready_plan()
        self.assertIsNone(M.assembling(self.ep))
        lock = M.take_lock(self.ep, "h3.py master")
        held = M.assembling(self.ep)
        self.assertEqual((held["pid"], held["by"]), (os.getpid(), "h3.py master"))
        with self.assertRaisesRegex(M.MasterBusy, "already being assembled by h3.py master"):
            M.take_lock(self.ep, "the editor")

        def never(*a, **k):
            raise AssertionError("a second run must not start ffmpeg")

        with mock.patch.object(h3edit, "run_tool", never):
            with self.assertRaises(M.MasterBusy):
                M.assemble_master(plan, allow_gaps=True)
        M.release_lock(lock)
        self.assertIsNone(M.assembling(self.ep))
        # a lock left by a run that's gone is stale, and taken over
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        with open(M.lock_path(self.ep), "w", encoding="utf-8") as fh:
            json.dump({"pid": dead.pid, "host": __import__("socket").gethostname(),
                       "by": "h3.py master", "started": "2026-10-04T08:13:32-05:00"}, fh)
        self.assertIsNone(M.assembling(self.ep))
        M.release_lock(M.take_lock(self.ep, "the editor"))
        # another machine's can't be checked: held while it's fresh, stale after a day
        with open(M.lock_path(self.ep), "w", encoding="utf-8") as fh:
            json.dump({"pid": 1, "host": "elsewhere", "by": "h3.py master", "started": ""}, fh)
        self.assertIsNotNone(M.assembling(self.ep))
        old = os.path.getmtime(M.lock_path(self.ep)) - (M.LOCK_FOREIGN_HOURS + 1) * 3600
        os.utime(M.lock_path(self.ep), (old, old))
        self.assertIsNone(M.assembling(self.ep))
        M.release_lock(M.lock_path(self.ep))
        # a run that blows up still lets go
        with mock.patch.object(h3edit, "run_tool", never):
            with self.assertRaises(AssertionError):
                M.assemble_master(plan, allow_gaps=True)
        self.assertFalse(os.path.exists(M.lock_path(self.ep)))

    def test_route_job(self):
        import h3edit
        plan, name = self.ready_plan()
        self.make_titles()
        A._MASTER_JOBS.clear()
        self.addCleanup(A._MASTER_JOBS.clear)
        self.assertIsNone(self.ok(A.get_master_job(self.ctx, {"ep": self.ep}))["job"])
        body = {"ep": self.ep, "action": "assemble", "allow_gaps": True}
        with mock.patch.object(h3edit, "run_tool", self.talking_tool(name)):
            res = self.ok(A.post_master(self.ctx, body))
        self.assertEqual(res["output"], f"master/{name}.mp4")
        self.assertEqual(res["job"]["state"], "done")
        self.assertEqual(res["titles"]["picture"], "re-encoded")
        seen = self.events_of("h3pipe.master")
        self.assertEqual(seen[0]["state"], "running")
        self.assertIn(("assemble", "clips", 1, 2), [(e["step"], e["stage"], e["done"], e["total"])
                                                    for e in seen])
        self.assertEqual((seen[-1]["state"], seen[-1]["output"]), ("done", f"master/{name}.mp4"))
        # the dialog opened again afterwards: what was made, and how
        job = self.ok(A.get_master_job(self.ctx, {"ep": self.ep}))["job"]
        self.assertEqual((job["state"], job["report"], job["titles"]["picture"]),
                         ("done", "master/ks01_master.md", "re-encoded"))
        # `h3.py master` assembling it: the editor sees it running, and won't start another
        lock = M.take_lock(self.ep, "h3.py master")
        try:
            job = self.ok(A.get_master_job(self.ctx, {"ep": self.ep}))["job"]
            self.assertEqual((job["state"], job["elsewhere"], job["by"]),
                             ("running", True, "h3.py master"))
            code, data = A.post_master(self.ctx, body)
            self.assertEqual(code, 409, data)
            self.assertIn("already being assembled by h3.py master", data["error"])
            self.assertTrue(data["job"]["elsewhere"])
        finally:
            M.release_lock(lock)
        # the editor's own run still going (the request that started it hasn't
        # answered): a second press is refused, with the job to show
        A._MASTER_JOBS[A._master_key(self.ep)] = dict(job, state="running", elsewhere=False,
                                                      by="the editor", stage="clips", done=212,
                                                      total=323)
        code, data = A.post_master(self.ctx, body)
        self.assertEqual(code, 409, data)
        self.assertIn("already being assembled by the editor", data["error"])
        self.assertEqual(data["job"]["done"], 212)
        # not ready to assemble (still to upscale): refused before anything is
        # claimed or announced
        A._MASTER_JOBS.clear()
        before = len(self.events_of("h3pipe.master"))
        with mock.patch.object(M, "plan_master",
                               lambda *a, **k: M.Plan(self.ep, "final", RECIPE, (1920, 1080),
                                                      [M.Row("sh010", None, "upscale")])):
            self.err(A.post_master(self.ctx, body), 409)
        self.assertEqual(len(self.events_of("h3pipe.master")), before)
        self.assertIsNone(self.ok(A.get_master_job(self.ctx, {"ep": self.ep}))["job"])

    def test_printer(self):
        import io
        buf = io.StringIO()
        p = M.Printer(buf)
        for n in (0, 1, 2):
            p({"step": "assemble", "stage": "clips", "done": n, "total": 2, "text": f"sh0{n}0"})
        p({"step": "titles", "stage": "titles", "done": None, "total": None, "text": "intro"})
        p.finish()
        lines = buf.getvalue().splitlines()                    # not a terminal: a line per stage
        self.assertEqual(len(lines), 2)
        self.assertIn("assemble: writing the clips 0/2 sh000", lines[0])
        self.assertIn("titles: encoding the intro", lines[1])

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
