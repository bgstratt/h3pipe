"""h3publish: the series intro + the cut + the outro, the episode title drawn on.

The end-to-end tests build a tiny episode with testsrc clips of different sizes
and check the output's size, rate and length: a cut h3assemble encoded has its
picture copied frame for frame, any other cut is re-encoded. Skipped when
ffmpeg or ffprobe is not on PATH, or no font file is found.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import h3edit  # noqa: E402
import h3publish as P  # noqa: E402

HAVE_FF = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
FONT = next((f for f in ("C:/Windows/Fonts/segoescb.ttf", "C:/Windows/Fonts/arial.ttf",
                         "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                         "/Library/Fonts/Arial.ttf") if os.path.isfile(f)), None)


def make_clip(path: str, seconds: float, size: str, rate: int, sound: bool = True) -> None:
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc=size={size}:rate={rate}"]
    if sound:
        cmd += ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=32000"]
    cmd += ["-t", f"{seconds}", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
    cmd += ["-c:a", "aac"] if sound else []
    r = subprocess.run(cmd + [path], capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(r.stderr)


class Helpers(unittest.TestCase):
    def test_episode_title(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "ep07.md")
            with open(p, "w", encoding="utf-8") as f:
                f.write("\n= ep07  Cul-de-sac\n\n// = not this\n# sq01  street\n")
            self.assertEqual(P.episode_title(p), "Cul-de-sac")
            with open(p, "w", encoding="utf-8") as f:
                f.write("= ep07\n")
            self.assertEqual(P.episode_title(p), "")

    def test_config_defaults(self):
        c = P.publish_config({"publish": {"intro": "a.mp4", "subtitle": {"size": 0.1}}})
        self.assertEqual(c["intro"], "a.mp4")
        self.assertEqual(c["subtitle"]["size"], 0.1)
        self.assertEqual(c["subtitle"]["intro_in"], P.SUBTITLE["intro_in"])
        self.assertEqual(P.publish_config({})["subtitle"], P.SUBTITLE)

    def test_alpha(self):
        self.assertEqual(P.alpha_expr([4, 5]), "clip((t-4)/1,0,1)")
        self.assertEqual(P.alpha_expr([0, 1], [4.5, 5.5]),
                         "clip((t-0)/1,0,1)*(1-clip((t-4.5)/1,0,1))")
        self.assertEqual(P.alpha_expr([2, 2]), "gte(t,2)")

    def test_cut_tags(self):
        cut = {"sar": "N/A", "color_range": None, "color_primaries": None,
               "color_transfer": None, "color_space": None}
        self.assertEqual(P.cut_tags(cut), "setsar=0,setparams=range=unknown:color_primaries="
                                          "unknown:color_trc=unknown:colorspace=unknown")
        cut.update(sar="1:1", color_range="tv")
        self.assertTrue(P.cut_tags(cut).startswith("setsar=1,setparams=range=tv:"))

    def test_graph_inputs(self):
        cut = {"width": 960, "height": 544, "fps": 24, "duration": 10, "audio": False}
        clip = {"width": 1376, "height": 768, "fps": 24, "duration": 8, "audio": True}
        g = P.filter_graph(cut, clip, clip, P.SUBTITLE)
        self.assertIn("[3:a]", g)            # the silence stands in for the cut's sound
        self.assertIn("concat=n=3", g)
        g = P.filter_graph(cut, None, None, P.SUBTITLE)
        self.assertIn("concat=n=1", g)
        self.assertNotIn("drawtext", g)


@unittest.skipUnless(HAVE_FF and FONT, "needs ffmpeg, ffprobe and a font")
class EndToEnd(unittest.TestCase):
    def episode(self, show: str, assembled: bool) -> str:
        """ep07 with 2 s titles (tagged BT.709, like ComfyUI's) and a 3 s cut:
        encoded by h3assemble.conform, or a foreign ultrafast encode."""
        ep = os.path.join(show, "ep07")
        os.makedirs(os.path.join(ep, "renders"))
        os.makedirs(os.path.join(show, "_titles"))
        for name in ("INTRO", "OUTRO"):
            raw = os.path.join(show, "_titles", f"{name}_raw.mp4")
            make_clip(raw, 2, "1376x768", 24)
            r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", raw, "-c", "copy",
                                "-color_primaries", "bt709", "-color_trc", "bt709",
                                "-colorspace", "bt709", "-color_range", "tv",
                                os.path.join(show, "_titles", f"{name}.mp4")],
                               capture_output=True, text=True, timeout=120)
            self.assertEqual(r.returncode, 0, r.stderr)
        cut = os.path.join(ep, "renders", "ep07.mp4")
        if assembled:
            raw = os.path.join(show, "raw.mp4")
            make_clip(raw, 3, "320x176", 24, sound=False)
            P.h3assemble.conform(raw, cut, None, 24, 72, quality="review")
        else:
            make_clip(cut, 3, "320x176", 24, sound=False)
        with open(os.path.join(ep, "ep07.md"), "w", encoding="utf-8") as f:
            f.write("= ep07  Cul-de-sac\n")
        with open(os.path.join(ep, "series.json"), "w", encoding="utf-8") as f:
            json.dump({"series": {"id": "t", "title": "T"},
                       "publish": {"intro": "../_titles/INTRO.mp4",
                                   "outro": "../_titles/OUTRO.mp4",
                                   "subtitle": {"font": FONT, "intro_in": [0.5, 1],
                                                "outro_out": [1, 1.5]}}}, f)
        return ep

    def publish(self, ep: str) -> tuple[str, str]:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            out = P.publish(ep)
        self.assertEqual(out, os.path.join(ep, "publish", "ep07.mp4"))
        got = P.probe(out)
        self.assertEqual((got["width"], got["height"], got["fps"]), (320, 176, 24))
        self.assertEqual(got["frames"], 48 + 72 + 48)
        self.assertTrue(got["audio"])
        return out, buf.getvalue()

    def test_assembled_cut_is_copied(self):
        with tempfile.TemporaryDirectory() as show:
            ep = self.episode(show, assembled=True)
            out, said = self.publish(ep)
            self.assertIn("picture copied", said)
            self.assertNotIn("re-encoding the whole cut", said)
            cut = os.path.join(ep, "renders", "ep07.mp4")
            got, src = P.probe(out), P.probe(cut)
            self.assertEqual(P.frame_hashes(out, 48, 72, got), P.frame_hashes(cut, 0, 72, src))

    def test_editor_publish(self):
        # the route's helper: _titles in the show folder by convention, no
        # publish block needed; the output relative to the episode
        with tempfile.TemporaryDirectory() as show:
            ep = self.episode(show, assembled=True)
            cfg = os.path.join(ep, "series.json")
            with open(cfg, encoding="utf-8") as f:
                doc = json.load(f)
            doc["publish"] = {"subtitle": doc["publish"]["subtitle"]}
            with open(cfg, "w", encoding="utf-8") as f:
                json.dump(doc, f)
            res = h3edit.publish_episode(ep, "renders/ep07.mp4")
            self.assertTrue(res["ok"], res["report"])
            self.assertEqual(res["output"], "publish/ep07.mp4")
            self.assertEqual(P.probe(os.path.join(ep, res["output"]))["frames"], 48 + 72 + 48)
            shutil.rmtree(os.path.join(show, "_titles"))
            res = h3edit.publish_episode(ep, "renders/ep07.mp4")
            self.assertFalse(res["ok"])
            self.assertIn("no intro or outro", res["error"])

    def test_foreign_cut_is_reencoded(self):
        with tempfile.TemporaryDirectory() as show:
            _, said = self.publish(self.episode(show, assembled=False))
            self.assertIn("re-encoding the whole cut: the title clips can't be encoded", said)

    def run_with_progress(self, ep: str) -> tuple[int, list, str]:
        """h3publish as h3master runs it: through h3edit.run_tool, --progress."""
        events = []
        rc, out, err = h3edit.run_tool("h3publish.py", [ep, "--progress"], ep, 600,
                                       progress=lambda tool, ev: events.append((tool, ev)))
        self.assertEqual(rc, 0, out + err)
        self.assertNotIn("##h3publish", out)            # progress lines don't reach the log
        return rc, events, out

    def test_progress_says_the_picture_was_copied(self):
        with tempfile.TemporaryDirectory() as show:
            _, events, _ = self.run_with_progress(self.episode(show, assembled=True))
            self.assertTrue(all(tool == "h3publish" for tool, _ in events))
            stages = [ev["stage"] for _, ev in events]
            for want in ("titles", "join", "verify"):
                self.assertIn(want, stages)
            self.assertEqual(events[-1][1]["stage"], "done")
            self.assertEqual((events[-1][1]["picture"], events[-1][1]["why"]), ("copied", ""))

    def test_progress_says_the_picture_was_reencoded(self):
        with tempfile.TemporaryDirectory() as show:
            _, events, _ = self.run_with_progress(self.episode(show, assembled=False))
            done = events[-1][1]
            self.assertEqual(done["picture"], "re-encoded")
            self.assertIn("the title clips can't be encoded", done["why"])
            counted = [ev for _, ev in events if ev["stage"] == "reencode" and ev["total"]]
            self.assertTrue(counted)                     # ffmpeg's frame count, as it went
            self.assertEqual(counted[0]["total"], 48 + 72 + 48)

    def test_missing_clip(self):
        with tempfile.TemporaryDirectory() as ep:
            make_clip(os.path.join(ep, "cut.mp4"), 1, "320x176", 24)
            with open(os.path.join(ep, "x.md"), "w", encoding="utf-8") as f:
                f.write("= x  X\n")
            with open(os.path.join(ep, "series.json"), "w", encoding="utf-8") as f:
                json.dump({"series": {"id": "t", "title": "T"},
                           "publish": {"intro": "nope.mp4", "subtitle": {"font": FONT}}}, f)
            with self.assertRaises(P.PublishError):
                P.publish(ep, os.path.join(ep, "cut.mp4"))


if __name__ == "__main__":
    unittest.main()
