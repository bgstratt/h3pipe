"""
Phase 9a (docs/API.md "Phase 9a: script and series config windows, promote"):
GET/PUT /h3pipe/source and POST /h3pipe/source/check (h3source: hashes, 409s,
_history/ copies, line endings and the BOM kept, in-memory checks with line
numbers), and promote (h3promote: the plan, what maps where and what is left,
applying it, the overrides it drops, and that every shot renders with exactly
the values it did before). The routes run through h3pipe_api with test_api's
built kitchen_sink episode; the aiohttp adapter is in test_routes.
"""
from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import os
import shutil
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "comfy_nodes"))
import h3edit as E  # noqa: E402
import h3jobs as J  # noqa: E402
import h3pipe_api as A  # noqa: E402
import h3promote as P  # noqa: E402
import h3refs as R  # noqa: E402
import h3source as H  # noqa: E402
import h3takes as T  # noqa: E402
import test_api  # noqa: E402
from test_api import ApiTest  # noqa: E402

H3 = "minimax_h3_ref2va"


def tearDownModule():
    test_api.tearDownModule()


def sha1(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha1(fh.read()).hexdigest()


def errd(test, res, status) -> dict:
    """An error answer's whole body (a 409 carries hash and text)."""
    code, data = res
    test.assertEqual(code, status, data)
    test.assertTrue(data.get("error"))
    json.dumps(data)
    return data


def read_bytes(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


class SourceTest(ApiTest):
    """Reading, checking and saving the two authored files."""

    @property
    def script(self):
        return os.path.join(self.ep, "ks01.md")

    @property
    def series(self):
        return os.path.join(self.ep, "series.json")

    def get(self, file="script", **q):
        return self.ok(A.get_source(self.ctx, dict({"ep": self.ep, "file": file}, **q)))

    def put(self, text, base_hash, file="script", rebuild=False, status=200):
        res = A.put_source(self.ctx, {"ep": self.ep, "file": file, "text": text,
                                      "base_hash": base_hash, "rebuild": rebuild})
        return self.ok(res, status) if status == 200 else errd(self, res, status)

    def test_read(self):
        got = self.get()
        with open(self.script, encoding="utf-8") as fh:
            self.assertEqual(got["text"], fh.read().replace("\r\n", "\n"))
        self.assertEqual((got["file"], got["path"]), ("script", "ks01.md"))
        self.assertEqual(got["hash"], sha1(self.script))
        # the shots and their spans are the parser's (shots.json's `source`)
        with open(os.path.join(self.ep, "shotlist", "shots.json"), encoding="utf-8") as fh:
            story = json.load(fh)
        want = [{"id": s["id"], "line": s["source"]["line"], "end_line": s["source"]["end_line"]}
                for sq in story["sequences"] for s in sq["shots"]]
        self.assertEqual(got["shots"], want)
        self.assertEqual(got["shots"][0], {"id": "sh010", "line": 10, "end_line": 18})
        series = self.get("series")
        self.assertEqual((series["path"], series["hash"]), ("series.json", sha1(self.series)))
        self.assertNotIn("shots", series)
        self.assertEqual(self.get(hash_only="1"),
                         {"file": "script", "hash": sha1(self.script),
                          "mtime": os.stat(self.script).st_mtime})
        self.err(A.get_source(self.ctx, {"ep": self.ep, "file": "notes"}), 400)

    def test_parent_series_config(self):
        shutil.move(self.series, os.path.join(self.shows, "series.json"))
        got = self.get("series")
        self.assertEqual(got["path"], "../series.json")
        self.assertEqual(got["hash"], sha1(os.path.join(self.shows, "series.json")))

    def test_script_without_parse_has_no_shots(self):
        with open(self.script, "a", encoding="utf-8") as fh:
            fh.write("\nsize: huge\n")
        self.assertEqual(self.get()["shots"], [])

    def test_check_writes_nothing(self):
        before = {p: (read_bytes(p), os.stat(p).st_mtime_ns) for p in (self.script, self.series)}
        text = self.get()["text"]
        ok = self.ok(A.post_source_check(self.ctx, {"ep": self.ep, "file": "script",
                                                    "text": text}))
        self.assertTrue(ok["ok"])
        self.assertEqual(ok["errors"], [])
        self.assertEqual(ok["shots"], self.get()["shots"])
        # the build's warnings, each on the line it is about
        w = {x["message"]: x for x in ok["warnings"]}
        self.assertEqual(w["sh040 overrides steps: 10"],
                         {"file": "script", "line": 51, "message": "sh040 overrides steps: 10"})
        self.assertEqual(text.split("\n")[50], "steps: 10")
        series_w = [x for x in ok["warnings"] if x["file"] == "series"]
        self.assertEqual(len(series_w), 1)
        self.assertEqual(series_w[0]["line"], 9)                    # "steps": 6
        # a parse error: the parser's 1-based line, into the text given
        bad = text.replace("size: ws", "size: huge")
        r = self.ok(A.post_source_check(self.ctx, {"ep": self.ep, "file": "script",
                                                   "text": bad}))
        self.assertFalse(r["ok"])
        self.assertEqual(r["errors"], [{"file": "script", "line": 12, "message":
                                        "size 'huge' must be one of ['close', 'cu', 'medium', "
                                        "'ms', 'wide', 'ws']"}])
        # a build error about a shot: the line it names
        r = self.ok(A.post_source_check(self.ctx, {
            "ep": self.ep, "file": "script",
            "text": text.replace("target: minimax_h3_ref2va", "target: nope")}))
        self.assertEqual(r["errors"][0]["line"], text.split("\n").index(
            "target: minimax_h3_ref2va") + 1)
        self.assertTrue(r["errors"][0]["message"].startswith("shot sh320: target 'nope'"))
        # the series config: JSON errors carry line and column
        stext = self.get("series")["text"]
        r = self.ok(A.post_source_check(self.ctx, {"ep": self.ep, "file": "series",
                                                   "text": stext.replace('"steps": 5',
                                                                         '"steps": 5,')}))
        self.assertEqual(r["errors"][0]["file"], "series")
        self.assertEqual((r["errors"][0]["line"], r["errors"][0]["col"]), (107, 17))
        self.assertEqual(r["shots"], [])
        # a loader error names the key's line when it can, null when it can't
        r = self.ok(A.post_source_check(self.ctx, {"ep": self.ep, "file": "series",
                                                   "text": stext.replace('"steps": 5',
                                                                         '"steps": 0')}))
        self.assertEqual((r["errors"][0]["file"], r["errors"][0]["line"]), ("series", 106))
        r = self.ok(A.post_source_check(self.ctx, {"ep": self.ep, "file": "series",
                                                   "text": stext.replace('"subjects"',
                                                                         '"subjectz"')}))
        self.assertEqual(r["errors"], [{"file": "series", "line": None,
                                        "message": "series.json has no `subjects` block"}])
        # a series config that breaks the script on disk: the script's line
        r = self.ok(A.post_source_check(self.ctx, {
            "ep": self.ep, "file": "series",
            "text": stext.replace('"kettle": {', '"kettle2": {')}))
        self.assertEqual(r["errors"][0]["file"], "script")
        self.assertEqual(r["errors"][0]["line"], text.split("\n").index("props: kettle") + 1)
        after = {p: (read_bytes(p), os.stat(p).st_mtime_ns) for p in (self.script, self.series)}
        self.assertEqual(after, before)
        self.assertFalse(os.path.exists(os.path.join(self.ep, H.HISTORY)))

    def test_save_conflict(self):
        got = self.get()
        with open(self.script, "a", encoding="utf-8") as fh:
            fh.write("\n// edited outside\n")
        r = self.put(got["text"] + "x", got["hash"], status=409)
        self.assertEqual(r["error"], "changed on disk")
        self.assertEqual(r["hash"], sha1(self.script))
        self.assertTrue(r["text"].endswith("// edited outside\n"))
        # overwrite = resend with the new base_hash
        r2 = self.put(got["text"], r["hash"])
        self.assertEqual(read_bytes(self.script).decode("utf-8"), got["text"])
        self.assertEqual(r2["hash"], sha1(self.script))

    def test_save_script_with_errors_and_history(self):
        old = read_bytes(self.script)
        got = self.get()
        bad = got["text"].replace("size: ws", "size: huge")
        r = self.put(bad, got["hash"])
        self.assertFalse(r["check"]["ok"])
        self.assertEqual(r["check"]["errors"][0]["line"], 12)
        self.assertIsNone(r["build"])
        self.assertEqual(read_bytes(self.script).decode("utf-8"), bad)       # saved anyway
        hist = H.history(self.ep, "ks01.md")
        self.assertEqual(len(hist), 1)
        self.assertEqual(read_bytes(hist[0]), old)
        self.assertRegex(os.path.basename(hist[0]), r"^ks01\.md\.\d{8}-\d{6}$")
        self.assertIn(("h3pipe.episode", {"ep": self.ep}), self.events)
        # a second save in the same second gets its own copy
        r = self.put(got["text"], r["hash"])
        self.assertEqual(len(H.history(self.ep, "ks01.md")), 2)
        # saving the same bytes writes nothing
        self.put(got["text"], r["hash"])
        self.assertEqual(len(H.history(self.ep, "ks01.md")), 2)

    def test_history_keeps_30(self):
        d = os.path.join(self.ep, H.HISTORY)
        os.makedirs(d)
        for i in range(35):
            with open(os.path.join(d, f"ks01.md.20260101-0000{i:02d}"), "w") as fh:
                fh.write(str(i))
        with open(os.path.join(d, "series.json.20260101-000000"), "w") as fh:
            fh.write("other file")
        got = self.get()
        self.put(got["text"] + "\n", got["hash"])
        hist = H.history(self.ep, "ks01.md")
        self.assertEqual(len(hist), 30)
        self.assertNotIn("ks01.md.20260101-000005", [os.path.basename(p) for p in hist])
        self.assertIn("ks01.md.20260101-000006", [os.path.basename(p) for p in hist])
        self.assertEqual(len(H.history(self.ep, "series.json")), 1)

    def test_bom_and_crlf_kept(self):
        raw = read_bytes(self.script).replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
        with open(self.script, "wb") as fh:
            fh.write(H.BOM + raw)
        got = self.get()
        self.assertFalse(got["text"].startswith("﻿"))
        self.assertNotIn("\r", got["text"])
        self.assertEqual(got["hash"], hashlib.sha1(H.BOM + raw).hexdigest())
        # the UI sends LF text (maybe with a BOM of its own): the file keeps its ways
        new = got["text"].replace("Ada slams", "Ada bangs")
        r = self.put("﻿" + new, got["hash"])
        data = read_bytes(self.script)
        self.assertTrue(data.startswith(H.BOM))
        self.assertFalse(data[3:].startswith(H.BOM))
        self.assertEqual(data, H.BOM + new.replace("\n", "\r\n").encode("utf-8"))
        self.assertEqual(r["hash"], hashlib.sha1(data).hexdigest())
        self.assertTrue(r["check"]["ok"])
        # an LF file without a BOM stays that way
        sgot = self.get("series")
        self.put(sgot["text"].replace("Kitchen Sink", "Kitchen Sync"), sgot["hash"], "series")
        sdata = read_bytes(self.series)
        self.assertNotIn(b"\r\n", sdata)
        self.assertFalse(sdata.startswith(H.BOM))
        self.assertIn(b"Kitchen Sync", sdata)

    def test_series_config_json_refused(self):
        got = self.get("series")
        before = read_bytes(self.series)
        r = self.put(got["text"].replace('"steps": 5', '"steps": 5,'), got["hash"], "series",
                     status=400)
        self.assertEqual((r["line"], r["col"]), (107, 17))
        self.assertIn("not valid JSON", r["error"])
        self.assertEqual(read_bytes(self.series), before)
        self.assertFalse(os.path.exists(os.path.join(self.ep, H.HISTORY)))
        # valid JSON that doesn't load is saved, with the check's errors
        r = self.put(got["text"].replace('"steps": 5', '"steps": 0'), got["hash"], "series")
        self.assertFalse(r["check"]["ok"])
        self.assertEqual(len(H.history(self.ep, "series.json")), 1)

    def test_save_rebuilds(self):
        got = self.get()
        r = self.put(got["text"].replace("steps: 10", "steps: 11"), got["hash"], rebuild=True)
        self.assertTrue(r["build"]["ok"], r["build"])
        self.assertEqual(J.find_shot(self.ep, "final", "sh040")[0]["shots"][
            J.find_shot(self.ep, "final", "sh040")[1]]["steps"], 11)

    def test_bad_requests(self):
        got = self.get()
        self.err(A.put_source(self.ctx, {"ep": self.ep, "file": "script", "text": "x"}), 400)
        self.err(A.put_source(self.ctx, {"ep": self.ep, "file": "script", "text": 3,
                                         "base_hash": got["hash"]}), 400)
        self.err(A.post_source_check(self.ctx, {"ep": self.ep, "file": "script"}), 400)
        self.err(A.get_source(self.ctx, {"ep": os.path.dirname(self.tmp), "file": "script"}),
                 403)


class PromoteTest(ApiTest):
    """Promote: the plan, the mapping, apply, and that nothing renders differently."""

    def setUp(self):
        super().setUp()
        self.script = os.path.join(self.ep, "ks01.md")
        self.series = os.path.join(self.ep, "series.json")

    # -- helpers -------------------------------------------------------------

    def set_ov(self, sid, passes, shot_fields=None, pass_fields=None, target=H3):
        ov = T.load_overrides(self.ep)
        E.set_shot_override(ov, sid, E.pass_entries(self.ep, sid, target), passes,
                            shot_fields, pass_fields, target)
        T.save_overrides(self.ep, ov)

    def render_values(self) -> dict:
        """What every shot's next render uses, per pass, through h3jobs.plan_job
        (the real thing, on disk): target, model, LoRAs, steps."""
        ov = T.load_overrides(self.ep)
        out = {}
        for ps in T.PASSES:
            for doc in J.load_shotlists(self.ep, ps):
                for i, s in enumerate(doc["shots"]):
                    job = J.plan_job(self.ep, ps, doc, i, J.RenderRequest(s["id"]), ov)
                    self.assertFalse(job.error, job.error)
                    out[(s["id"], ps)] = (job.target, job.model,
                                          P._loras(job.loras), job.steps)
        return out

    def plan(self, **q):
        return self.ok(A.get_promote(self.ctx, dict({"ep": self.ep}, **q)))

    def promote(self, items, hashes, status=200):
        res = A.post_promote(self.ctx, {"ep": self.ep, "items": items, "hashes": hashes})
        return self.ok(res) if status == 200 else errd(self, res, status)

    def overrides(self):
        ov = T.load_overrides(self.ep)
        return {k: v for k, v in ov.items() if k != "shots"}, ov.get("shots", {})

    def standard_overrides(self):
        """One of everything: promotable, one-pass, stacked, compiled text, seed."""
        self.set_ov("sh010", T.PASSES, {"seed": 5}, {"steps": 9, "model": "m.safetensors"})
        self.set_ov("sh020", ["final"], None, {"steps": 11})
        self.set_ov("sh030", T.PASSES, None,
                    {"loras": [{"name": "x.safetensors", "strength": 0.5}]})
        self.set_ov("sh050", T.PASSES, {"note": "hm"},
                    {"loras": [{"name": "a", "strength": 1.0}, {"name": "b", "strength": 1.0}],
                     "prompt": "hand written"})
        # a proxy-only steps override that the final pass already renders
        final_steps = E.pass_entries(self.ep, "sh110", H3)["final"]["steps"]
        self.set_ov("sh110", ["proxy"], None, {"steps": final_steps})
        # sh310's sequence (sq04) sets `profile: quick` (steps 5) on its header
        self.set_ov("sh310", T.PASSES, None, {"steps": 3})
        ov = T.load_overrides(self.ep)
        T.set_shot_target(ov, "sh060", "ltx2")
        T.set_episode_field(ov, "refs_target", "flux2_klein", "ks01")
        T.save_overrides(self.ep, ov)

    # -- the plan ------------------------------------------------------------

    def test_plan(self):
        self.standard_overrides()
        before = read_bytes(self.script), read_bytes(self.series), \
            read_bytes(os.path.join(self.ep, T.OVERRIDES_FILE))
        p = self.plan()
        items = {it["id"]: it for it in p["items"]}
        self.assertEqual(sorted(items), sorted([
            "shot:sh010:model", "shot:sh010:steps", "shot:sh030:loras", "shot:sh060:target",
            "shot:sh110:steps", "shot:sh310:steps", "episode:refs_target"]))
        self.assertEqual(items["shot:sh030:loras"]["value"],
                         [{"name": "x.safetensors", "strength": 0.5}])
        self.assertIn("`lora: x.safetensors:0.5`", items["shot:sh030:loras"]["summary"])
        self.assertEqual(items["shot:sh010:steps"]["dest"], "script")
        self.assertEqual(items["episode:refs_target"]["dest"], "series")
        text_after = self.apply_diff(p["diffs"]["script"])
        lines = text_after.split("\n")
        for it in p["items"]:
            if it["dest"] == "script":
                key = P.SHOT_LINE[it["field"]]
                self.assertTrue(lines[it["line"] - 1].startswith(f"{key}: "), it)
        left = {(x.get("shot"), x["field"], x.get("pass")): x["reason"] for x in p["left"]}
        self.assertIn("picking the take", left[("sh010", "seed", None)])
        self.assertIn("only the final pass overrides steps", left[("sh020", "steps", None)])
        self.assertIn("holds one LoRA", left[("sh050", "loras", None)])
        self.assertIn("no script form", left[("sh050", "prompt", "final")])
        self.assertIn("no place in the script", left[("sh050", "note", None)])
        self.assertEqual(p["hashes"], {"script": sha1(self.script), "series": sha1(self.series)})
        # the series diff says the file is reformatted (the fixture has compact arrays)
        self.assertTrue(p["diffs"]["series"].startswith("# series.json wasn't formatted"))
        self.assertIn('+    "target": "flux2_klein"', p["diffs"]["series"])
        # nothing was written
        self.assertEqual((read_bytes(self.script), read_bytes(self.series),
                          read_bytes(os.path.join(self.ep, T.OVERRIDES_FILE))), before)
        # one shot
        p1 = self.plan(shot="sh010")
        self.assertEqual([it["id"] for it in p1["items"]],
                         ["shot:sh010:model", "shot:sh010:steps"])
        self.assertEqual([x["field"] for x in p1["left"]], ["seed"])
        self.assertEqual(p1["diffs"]["series"], "")

    def apply_diff(self, diff: str) -> str:
        """The script with the plan applied, from the plan's own (private) text."""
        return P.make_plan(self.ep)["_texts"]["script"]

    def test_script_edits_are_line_level(self):
        """Replace the shot's own line; insert after its last `key:` line;
        never the sequence header's line; nothing else changes."""
        self.set_ov("sh040", T.PASSES, None, {"steps": 14})           # has `steps: 10`
        self.set_ov("sh110", T.PASSES, None, {"steps": 13})           # sq02 header: steps 12
        self.set_ov("sh020", T.PASSES, None, {"model": "new.safetensors"})
        p = P.make_plan(self.ep)
        old = read_bytes(self.script).decode("utf-8").split("\n")
        new = p["_texts"]["script"].split("\n")
        self.assertEqual(len(new), len(old) + 2)
        diff = [l for l in p["diffs"]["script"].split("\n")
                if l.startswith(("+", "-")) and not l.startswith(("+++", "---"))]
        self.assertEqual(diff, ["+model: new.safetensors", "-steps: 10", "+steps: 14",
                                "+steps: 13"])
        # sh110: inserted after its last key line, the header's `steps: 12` untouched
        i = new.index("steps: 13")
        self.assertEqual(new[i - 1], "sound: traffic hiss on wet asphalt")
        self.assertIn("steps: 12", new)
        self.assertEqual(new[new.index("steps: 12") - 1], "model: seq_model.safetensors")
        # sh020: after `extras:` (its last key line), before the action
        i = new.index("model: new.safetensors")
        self.assertTrue(new[i - 1].startswith("extras:"))

    def test_insert_right_after_header(self):
        text = "= ep01\n\n# sq01 kitchen\n## sh010\nAda waits.\nADA: Hi.\n"
        self.assertEqual(P.edit_script(text, {"sh010": (4, 6)}, {"sh010": [("steps", "7")]}),
                         "= ep01\n\n# sq01 kitchen\n## sh010\nsteps: 7\nAda waits.\nADA: Hi.\n")
        # a new line goes after the block's opening key lines, not below a
        # trailing `music:`; a trailing key that exists is still replaced
        text = "# sq01 kitchen\n## sh010\nwho: ada\nAda waits.\nADA: Hi.\nmusic: soft\n"
        self.assertEqual(
            P.edit_script(text, {"sh010": (2, 6)},
                          {"sh010": [("steps", "7"), ("music", "none")]}),
            "# sq01 kitchen\n## sh010\nwho: ada\nsteps: 7\nAda waits.\nADA: Hi.\nmusic: none\n")

    # -- apply -----------------------------------------------------------------

    def test_apply_renders_the_same(self):
        """The acceptance test: every shot's effective render values (target,
        model, LoRAs, steps, both passes, through plan_job) are identical
        before and after a promote of everything."""
        self.standard_overrides()
        before = self.render_values()
        p = self.plan()
        r = self.promote("all", p["hashes"])
        self.assertEqual(sorted(r["promoted"]), sorted(it["id"] for it in p["items"]))
        self.assertTrue(r["build"]["ok"], r["build"])
        self.assertEqual(r["hashes"], {"script": sha1(self.script), "series": sha1(self.series)})
        self.assertNotEqual(r["hashes"], p["hashes"])
        self.assertEqual(self.render_values(), before)
        # the promoted override fields are gone, the rest are still there
        ep, shots = self.overrides()
        self.assertEqual(ep["episode"], "ks01")                     # refs_target dropped
        self.assertNotIn("sh060", shots)                            # the retarget
        self.assertNotIn("sh030", shots)
        self.assertNotIn("sh310", shots)
        self.assertEqual(shots["sh010"], {H3: {"seed": 5}})
        self.assertEqual(set(shots["sh020"][H3]["final"]), {"base_hash", "steps"})
        self.assertEqual(set(shots["sh050"][H3]["final"]), {"base_hash", "prompt", "loras"})
        self.assertEqual(shots["sh050"][H3]["note"], "hm")
        self.assertNotIn("sh110", shots)
        # the files, with history copies
        text = read_bytes(self.script).decode("utf-8")
        self.assertIn("model: m.safetensors\nsteps: 9\n", text)
        self.assertIn("pace: slow\ntarget: ltx2\n", text)
        with open(self.series, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["refs"], {"target": "flux2_klein"})
        self.assertEqual(len(H.history(self.ep, "ks01.md")), 1)
        self.assertEqual(len(H.history(self.ep, "series.json")), 1)
        self.assertIn(("h3pipe.episode", {"ep": self.ep}), self.events)
        # and the rebuild put sh060 on ltx2 by the script
        self.assertIn("shotlist.ltx2.json", os.listdir(os.path.join(self.ep, "shotlist")))
        # nothing left to promote but what can't be
        p2 = self.plan()
        self.assertEqual(p2["items"], [])
        self.assertEqual({(x.get("shot"), x["field"]) for x in p2["left"]},
                         {(x.get("shot"), x["field"]) for x in r["left"]})

    def test_apply_some(self):
        self.standard_overrides()
        before = self.render_values()
        p = self.plan()
        r = self.promote(["shot:sh010:steps", "episode:refs_target"], p["hashes"])
        self.assertEqual(r["promoted"], ["shot:sh010:steps", "episode:refs_target"])
        _, shots = self.overrides()
        self.assertEqual(shots["sh010"][H3]["final"]["model"], "m.safetensors")
        self.assertNotIn("steps", shots["sh010"][H3]["final"])
        self.assertIn("sh030", shots)
        self.assertEqual(self.render_values(), before)
        # an id that isn't in the plan
        p = self.plan()
        self.promote(["shot:sh999:steps"], p["hashes"], status=400)

    def test_conflict(self):
        self.standard_overrides()
        p = self.plan()
        with open(self.series, "a", encoding="utf-8") as fh:
            fh.write("\n")
        r = self.promote("all", p["hashes"], status=409)
        self.assertEqual((r["error"], r["file"], r["hash"]),
                         ("changed on disk", "series", sha1(self.series)))
        self.assertIn("text", r)
        self.assertIn("sh010", T.load_overrides(self.ep)["shots"])
        self.assertFalse(os.path.exists(os.path.join(self.ep, H.HISTORY)))
        self.promote("all", {"script": sha1(self.script)}, status=409)
        self.err(A.post_promote(self.ctx, {"ep": self.ep, "items": "all"}), 400)
        self.err(A.post_promote(self.ctx, {"ep": self.ep, "items": 3, "hashes": {}}), 400)

    def test_check_catches_a_changed_render(self):
        """Every item is compiled before it is offered: one whose script line
        would render something else goes to `left` with what would change."""
        self.set_ov("sh010", T.PASSES, None, {"steps": 9})
        st = P.State(self.ep)
        before = P.render_values(st.story, st.cfg, st.ov)
        (item,), _ = P.shot_candidates(st, before, "sh010")
        self.assertEqual(P._check(st, [item], before), ([item], []))
        wrong = dict(item, _edit=("steps", "8"))
        keep, left = P._check(st, [wrong], before)
        self.assertEqual(keep, [])
        self.assertIn("sh010 would render differently (final: steps 9 → 8)", left[0]["reason"])
        unreadable = dict(item, _edit=("steps", "nine"))
        keep, left = P._check(st, [unreadable], before)
        self.assertEqual(keep, [])
        self.assertTrue(left[0]["reason"])

    def test_both_pass_values_must_agree(self):
        self.set_ov("sh010", ["final"], None, {"steps": 9})
        self.set_ov("sh010", ["proxy"], None, {"steps": 3})
        p = self.plan()
        self.assertEqual(p["items"], [])
        self.assertIn("differ (9 / 3)", p["left"][0]["reason"])

    def test_block_of_another_target(self):
        """An override written for the target the shot no longer renders on
        stays: a script line would give it to the other model."""
        self.set_ov("sh010", T.PASSES, None, {"steps": 9})
        ov = T.load_overrides(self.ep)
        T.set_shot_target(ov, "sh010", "ltx2")
        T.save_overrides(self.ep, ov)
        p = self.plan(shot="sh010")
        self.assertEqual([it["id"] for it in p["items"]], ["shot:sh010:target"])
        self.assertIn("written for minimax_h3_ref2va", p["left"][0]["reason"])

    def test_episode_target(self):
        ov = T.load_overrides(self.ep)
        T.set_episode_field(ov, "target", "ltx2", "ks01")
        T.save_overrides(self.ep, ov)
        p = self.plan()
        self.assertEqual(p["items"], [])
        self.assertIn("series.model, series.lora, series.steps, proxy.lora, proxy.steps",
                      p["left"][0]["reason"])
        # without pass-block render settings it moves to series.target
        with open(self.series, encoding="utf-8") as fh:
            cfg = json.load(fh)
        for block in ("series", "proxy"):
            for k in ("model", "lora", "steps"):
                cfg[block].pop(k, None)
        with open(self.series, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        self.assertTrue(E.build_episode(self.ep)["ok"])
        before = self.render_values()
        p = self.plan()
        self.assertEqual([it["id"] for it in p["items"]], ["episode:target"])
        self.assertFalse(p["diffs"]["series"].startswith("#"))       # already formatted
        r = self.promote(["episode:target"], p["hashes"])
        self.assertTrue(r["build"]["ok"])
        with open(self.series, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["series"]["target"], "ltx2")
        self.assertIsNone(T.episode_target(T.load_overrides(self.ep)))
        self.assertEqual(self.render_values(), before)

    def test_ref_prompt_to_design(self):
        s = R.load_series(self.ep)
        loc = R.find_ref(s, "location:kitchen")
        built = R.built_prompt(s, loc)
        old = s.series_cfg["locations"]["kitchen"]["description"]
        mine = built.replace(old, "a spotless diner kitchen at dawn")
        ov = R.load_overrides(s.home)
        R.set_ref_override(ov, loc, None, {"prompt": mine, "seed": 7}, R.prompt_hash(built))
        # one view of a character: its design is every view's
        ada = R.find_ref(s, "subject:ada")
        v0 = R.VIEW_TAGS[0]
        bp = R.built_prompt(s, ada, v0)
        R.set_ref_override(ov, ada, v0, {"prompt": bp.replace("red glasses", "blue glasses")},
                           R.prompt_hash(bp))
        # every view of another, the same change in each: its design moves
        bo = R.find_ref(s, "subject:bo")
        for v in R.VIEW_TAGS:
            bp = R.built_prompt(s, bo, v)
            R.set_ref_override(ov, bo, v, {"prompt": bp.replace("green bomber", "red bomber")},
                               R.prompt_hash(bp))
        # a prop whose override rewrites the wrapper, not just the design
        kettle = R.find_ref(s, "subject:kettle")
        R.set_ref_override(ov, kettle, None, {"prompt": "just a kettle"}, None)
        R.save_overrides(s.home, ov)

        p = self.plan()
        items = {it["id"]: it for it in p["items"]}
        self.assertEqual(sorted(items), ["ref:location:kitchen:prompt", "ref:subject:bo:prompt"])
        self.assertEqual(items["ref:location:kitchen:prompt"]["value"],
                         "a spotless diner kitchen at dawn")
        self.assertIn("sh010", items["ref:location:kitchen:prompt"]["summary"])
        left = {(x.get("ref"), x["field"]): x["reason"] for x in p["left"]}
        self.assertIn("describes all four views", left[("subject:ada", "prompt")])
        self.assertIn("more of the prompt", left[("subject:kettle", "prompt")])
        self.assertIn("picking the take", left[("location:kitchen", "seed")])

        r = self.promote(["ref:location:kitchen:prompt", "ref:subject:bo:prompt"],
                         p["hashes"])
        self.assertTrue(r["build"]["ok"])
        self.assertIn(("h3pipe.ref", {"ep": self.ep, "ref": "location:kitchen", "view": None,
                                      "take": None, "status": "promoted"}), self.events)
        s2 = R.load_series(self.ep)
        self.assertEqual(s2.series_cfg["locations"]["kitchen"]["description"],
                         "a spotless diner kitchen at dawn")
        self.assertIn("red bomber", s2.series_cfg["subjects"]["bo"]["design"])
        ov2 = R.load_overrides(s2.home)
        self.assertEqual(ov2["refs"]["location:kitchen"], {"seed": 7})
        self.assertNotIn("subject:bo", ov2["refs"])
        self.assertIn("subject:ada", ov2["refs"])
        # a generate now builds exactly the override's text
        self.assertEqual(R.built_prompt(s2, R.find_ref(s2, "location:kitchen")), mine)
        for v in R.VIEW_TAGS:
            self.assertIn("red bomber", R.built_prompt(s2, R.find_ref(s2, "subject:bo"), v))

    def test_cli(self):
        self.standard_overrides()
        before = self.render_values()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(P.cmd_promote(self.ep, ["sh010"]), 0)
        self.assertIn("shot:sh010:steps", out.getvalue())
        self.assertIn("nothing written", out.getvalue())
        self.assertIn("sh010", T.load_overrides(self.ep)["shots"])
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(P.cmd_promote(self.ep, ["--item", "nope"]), 2)
            self.assertEqual(P.cmd_promote(self.ep, ["sh010", "--all", "--dry-run"]), 0)
        self.assertIn("steps", T.load_overrides(self.ep)["shots"]["sh010"][H3]["final"])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(P.cmd_promote(self.ep, ["sh010", "--all"]), 0)
        self.assertIn("promoted 2", out.getvalue())
        self.assertEqual(T.load_overrides(self.ep)["shots"]["sh010"], {H3: {"seed": 5}})
        self.assertIn("sh030", T.load_overrides(self.ep)["shots"])       # other shots stay
        self.assertEqual(self.render_values(), before)

    def test_unbuildable_script(self):
        with open(self.script, "a", encoding="utf-8") as fh:
            fh.write("\nsize: huge\n")
        r = self.err(A.get_promote(self.ctx, {"ep": self.ep}), 400)
        self.assertIn("doesn't parse", r)


if __name__ == "__main__":
    unittest.main()
