"""
A shot left out of the cut (cut.json `"out": true`): it stays in the script
with its takes, its place and its pick, and assemble, Play all's neighbours,
the cut's upscales and Master skip it. Set by PUT /h3pipe/cut (the editor),
`h3.py cut --out / --in`; a locked entry refuses; copying the other pass's
order copies it; reset keeps it.
"""

from __future__ import annotations

import contextlib
import io
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from test_phase9b import Base  # noqa: E402  (first: it sets the import paths)
import h3edit as E  # noqa: E402
import h3pipe_api as A  # noqa: E402
import h3takes as T  # noqa: E402
import h3upscale as U  # noqa: E402


class CutOutTest(Base):
    def cli(self, *argv, code=0, pass_="proxy"):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            got = E.COMMANDS["cut"](self.ep, (["--proxy"] if pass_ == "proxy" else []) + list(argv))
        self.assertEqual(got, code, buf.getvalue())
        return buf.getvalue()

    def entry(self, shot, pass_="proxy"):
        return next((e for e in T.load_cut(self.ep).get(pass_, []) if e["shot"] == shot), None)

    def test_out_and_back_in(self):
        a, b, c = self.order[:3]
        out = self.cli("--out", b)
        self.assertIn("LEFT OUT", out)
        self.assertEqual(self.entry(b), {"shot": b, "out": True})
        data, shots = self.status()
        self.assertTrue(shots[b]["cut"]["out"])
        self.assertFalse(shots[a]["cut"]["out"])
        self.assertEqual([s["shot"] for s in data["shots"]][:3], [a, b, c])   # its place kept
        # neighbours skip it; its own neighbours still answer
        self.assertEqual(E.cut_neighbour(self.ep, "proxy", c, -1).shot, a)
        self.assertEqual(E.cut_neighbour(self.ep, "proxy", b, -1).shot, a)
        self.cli("--in", b)
        self.assertEqual(self.entry(b), {"shot": b})
        _, shots = self.status()
        self.assertFalse(shots[b]["cut"]["out"])

    def test_locked_refuses(self):
        b = self.order[1]
        self.cli("--lock", b)
        self.cli("--out", b, code=1)
        self.cli("--out", b, "--force")
        self.assertEqual(self.entry(b), {"shot": b, "locked": True, "out": True})

    def test_route(self):
        b = self.order[1]
        self.put_cut([{"shot": b, "out": True}])
        self.assertTrue(self.entry(b)["out"])
        self.put_cut([{"shot": b, "out": "yes"}], status=400)
        self.put_cut([{"shot": b, "out": False}])
        self.assertNotIn("out", self.entry(b))

    def test_reset_keeps_it_and_copy_carries_it(self):
        a, b = self.order[:2]
        E.set_cut_entry(self.ep, "proxy", b, out=True)
        E.move_shot(self.ep, "proxy", b, a, after=False)
        E.reset_cut(self.ep, "proxy", "all")
        self.assertTrue(self.entry(b)["out"])                                # kept, like picks
        E.copy_cut(self.ep, "proxy", "final", "order")
        self.assertTrue(self.entry(b, "final")["out"])
        E.copy_cut(self.ep, "proxy", "final", "trims")                       # trims don't carry it
        E.set_cut_entry(self.ep, "proxy", b, out=False)
        E.copy_cut(self.ep, "proxy", "final", "trims")
        self.assertTrue(self.entry(b, "final")["out"])

    def test_upscales_and_master_skip_it(self):
        a, b = self.order[:2]
        self.render(a, pass_="final")
        self.render(b, pass_="final")
        E.set_cut_entry(self.ep, "final", b, out=True)
        shots = [s for s, _, _ in U.cut_takes(self.ep)]
        self.assertIn(a, shots)
        self.assertNotIn(b, shots)
        self.assertIn(b, [s for s, _, _ in U.cut_takes(self.ep, {b})])      # named: included
        self.assertIn(b, [s for s, _, _ in U.cut_takes(self.ep, include_out=True)])
        # the whole cut's upscale leaves it alone
        self.comfy.nodes |= set(U.PIXEL_NODES)
        self.comfy.info["UpscaleModelLoader"] = {"input": {"required": {
            "model_name": [["RealESRGAN_x2.pth"], {}]}}}
        res = self.ok(A.post_upscale(self.ctx, {"ep": self.ep, "shots": None, "method": "pixel"}))
        self.assertNotIn(b, [q["shot"] for q in res["queued"]])
        # its pick's latent survives a prune (it may come back)
        t = T.get_take(self.ep, "final", b, 1)
        with open(t.paths.latent, "wb") as fh:
            fh.write(b"latent")
        gone = [p for p, _, _ in U.prune_latents(self.ep, dry_run=True)]
        self.assertNotIn(t.paths.latent, gone)


if __name__ == "__main__":
    unittest.main()
