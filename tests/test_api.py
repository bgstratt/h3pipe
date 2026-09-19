"""
The editor API (docs/API.md) through comfy_nodes/h3pipe_api.py, the handler
layer the aiohttp routes wrap. No aiohttp and no ComfyUI: the kitchen_sink
episode is built in a temp dir and ComfyUI is test_render's FakeComfy.
"""
from __future__ import annotations

import json
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
sys.path.insert(0, os.path.join(ROOT, "comfy_nodes"))
import h3pipe_api as A  # noqa: E402
import h3edit as E  # noqa: E402
import h3jobs as J  # noqa: E402
import h3takes as T  # noqa: E402
from test_render import stub_refs, ENV, FIXTURE, FakeComfy  # noqa: E402

HAVE_FF = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
_BUILT = None                                            # one build, copied per test


def built_episode() -> str:
    """A built kitchen_sink episode (both passes), made once per run."""
    global _BUILT
    if _BUILT is None or not os.path.isdir(_BUILT):   # another module may have cleaned up
        tmp = tempfile.mkdtemp(prefix="h3api_")
        ep = os.path.join(tmp, "ks01")
        os.makedirs(ep)
        shutil.copy(os.path.join(FIXTURE, "series.json"), ep)
        shutil.copy(os.path.join(FIXTURE, "script.md"), os.path.join(ep, "ks01.md"))
        r = E.build_episode(ep)
        assert r["ok"], r
        stub_refs(ep)
        _BUILT = ep
    return _BUILT


def tearDownModule():
    global _BUILT
    if _BUILT:
        shutil.rmtree(os.path.dirname(_BUILT), ignore_errors=True)
        _BUILT = None


class ApiTest(unittest.TestCase):
    build = True

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.shows = os.path.join(self.tmp, "Shows")
        self.ep = os.path.join(self.shows, "ks01")
        if self.build:
            shutil.copytree(built_episode(), self.ep)
        else:
            os.makedirs(self.ep)
            shutil.copy(os.path.join(FIXTURE, "series.json"), self.ep)
            shutil.copy(os.path.join(FIXTURE, "script.md"), os.path.join(self.ep, "ks01.md"))
        self.user = os.path.join(self.tmp, "user")
        self.comfy = FakeComfy()
        self.events: list[tuple[str, dict]] = []
        self.ctx = A.Context(self.user, self.comfy.url,
                             emit=lambda e, d: self.events.append((e, d)),
                             comfy=J.Comfy(self.comfy.url, client_id="h3pipe"), env={})
        self.ok(A.put_config(self.ctx, {"roots": [self.shows]}))

    def tearDown(self):
        self.comfy.close()
        self._tmp.cleanup()

    # -- helpers -------------------------------------------------------------

    def ok(self, res, status=200):
        code, data = res
        self.assertEqual(code, status, data)
        json.dumps(data)                                  # always serialisable
        return data

    def err(self, res, status):
        code, data = res
        self.assertEqual(code, status, data)
        self.assertIsInstance(data.get("error"), str)
        self.assertTrue(data["error"])
        return data["error"]

    def render(self, *shots, pass_="proxy", **kw):
        body = {"ep": self.ep, "pass": pass_, "shots": list(shots) or None}
        body.update(kw)
        return self.ok(A.post_render(self.ctx, body))

    def status(self, pass_="proxy"):
        data = self.ok(A.get_episode(self.ctx, {"ep": self.ep, "pass": pass_}))
        return data, {s["shot"]: s for s in data["shots"]}

    def events_of(self, name):
        return [d for e, d in self.events if e == name]


class ConfigAndRootsTest(ApiTest):
    build = False

    def test_config_roundtrip(self):
        cfg = self.ok(A.get_config(self.ctx, {}))
        self.assertEqual(cfg, {"roots": [os.path.abspath(self.shows)],
                               "comfy": self.comfy.url, "version": 1})
        self.assertTrue(os.path.isfile(os.path.join(self.user, "default", "h3pipe",
                                                    "config.json")))
        self.err(A.put_config(self.ctx, {"roots": [os.path.join(self.tmp, "nope")]}), 400)
        self.err(A.put_config(self.ctx, {"roots": "x"}), 400)
        self.err(A.put_config(self.ctx, ["x"]), 400)

    def test_roots_from_env_without_config_file(self):
        other = os.path.join(self.tmp, "Other")
        os.makedirs(other)
        ctx = A.Context(os.path.join(self.tmp, "fresh"), env={
            "H3PIPE_ROOTS": os.pathsep.join([self.shows, other])})
        self.assertEqual(self.ok(A.get_config(ctx, {}))["roots"], [self.shows, other])
        self.assertEqual(self.ok(A.get_config(A.Context(self.user + "2", env={}), {}))["roots"],
                         [])

    def test_ep_must_be_inside_a_root(self):
        outside = os.path.join(self.tmp, "Elsewhere", "ep01")
        os.makedirs(outside)
        self.err(A.get_episode(self.ctx, {"ep": outside}), 403)
        self.err(A.post_build(self.ctx, {"ep": outside}), 403)
        self.err(A.get_file(self.ctx, {"ep": outside, "path": "x"}), 403)
        # .. can't climb out of a root
        sneaky = os.path.join(self.shows, "..", "Elsewhere", "ep01")
        self.err(A.get_episode(self.ctx, {"ep": sneaky}), 403)
        # a root's sibling with the same prefix is not inside it
        os.makedirs(self.shows + "2")
        self.err(A.get_episode(self.ctx, {"ep": self.shows + "2"}), 403)
        self.err(A.get_episode(self.ctx, {}), 400)
        self.err(A.get_episode(self.ctx, {"ep": "Shows/ks01"}), 400)
        self.err(A.get_episode(self.ctx, {"ep": os.path.join(self.shows, "nope")}), 404)
        self.err(A.get_episode(self.ctx, {"ep": self.ep, "pass": "draft"}), 400)
        # inside, but never built
        self.err(A.get_episode(self.ctx, {"ep": self.ep}), 404)

    def test_build_ok_and_script_error(self):
        data = self.ok(A.post_build(self.ctx, {"ep": self.ep}))
        self.assertTrue(data["ok"], data)
        self.assertEqual(set(data["passes"]), {"final", "proxy"})
        for p in data["passes"].values():
            self.assertTrue(p["ok"])
            self.assertEqual(p["error"], "")
            self.assertIn("shotlist", p["report"])
        self.assertTrue(os.path.isfile(os.path.join(self.ep, "shotlist", "shotlist_proxy.json")))
        self.assertEqual(self.events_of("h3pipe.episode"), [{"ep": self.ep}])

        shutil.copy(os.path.join(HERE, "fixtures", "errors", "bad_size.md"),
                    os.path.join(self.ep, "ks01.md"))
        data = self.ok(A.post_build(self.ctx, {"ep": self.ep}))
        self.assertFalse(data["ok"])
        self.assertFalse(data["passes"]["final"]["ok"])
        self.assertIn("line 4", data["passes"]["final"]["error"])
        self.assertIn("extreme", data["passes"]["final"]["error"])

    def test_build_without_script(self):
        os.remove(os.path.join(self.ep, "ks01.md"))
        data = self.ok(A.post_build(self.ctx, {"ep": self.ep}))
        self.assertFalse(data["ok"])
        self.assertIn("script", data["error"])


class ReadTest(ApiTest):
    def test_episodes(self):
        eps = self.ok(A.get_episodes(self.ctx, {}))
        self.assertEqual(len(eps), 1)
        e = eps[0]
        self.assertEqual((e["ep"], e["name"], e["script"]), (self.ep, "ks01", "ks01.md"))
        self.assertEqual(e["built"], {"final": True, "proxy": True})
        self.assertGreater(e["shots"], 0)

    def test_file(self):
        q = {"ep": self.ep, "path": "shotlist/shotlist_proxy.json"}
        data = self.ok(A.get_file(self.ctx, q))
        self.assertEqual(os.path.normcase(data["path"]),
                         os.path.normcase(os.path.join(self.ep, "shotlist", "shotlist_proxy.json")))
        self.assertTrue(data["content_type"].startswith("application/json"))
        self.assertTrue(os.path.isfile(data["path"]))
        with open(os.path.join(self.shows, "secret.txt"), "w") as fh:
            fh.write("x")
        for bad in ("../secret.txt", "shotlist/../../secret.txt", "shotlist\\..\\..\\secret.txt",
                    os.path.join(self.shows, "secret.txt"), "/etc/passwd", "C:secret.txt"):
            self.err(A.get_file(self.ctx, {"ep": self.ep, "path": bad}), 400)
        self.err(A.get_file(self.ctx, {"ep": self.ep, "path": "renders/none.mp4"}), 404)
        self.err(A.get_file(self.ctx, {"ep": self.ep, "path": "shotlist"}), 404)
        self.err(A.get_file(self.ctx, {"ep": self.ep}), 400)

    def test_file_after_render(self):
        (q,) = self.render("sh010")["queued"]
        data, shots = self.status()
        mp4 = shots["sh010"]["takes"][0]["mp4"]
        self.assertEqual(mp4, "renders_proxy/sh010/sh010_t01.mp4")
        f = self.ok(A.get_file(self.ctx, {"ep": self.ep, "path": mp4}))
        self.assertEqual(f["content_type"], "video/mp4")

    def test_episode_status_seeds_are_strings(self):
        self.render("sh010", "sh020")
        data, shots = self.status()
        self.assertEqual(data["pass"], "proxy")
        self.assertEqual(data["folder"], "renders_proxy")
        doc = J.load_shotlist(self.ep, "proxy")
        self.assertEqual([s["shot"] for s in data["shots"]], [s["id"] for s in doc["shots"]])
        built = {s["id"]: s["seed"] for s in doc["shots"]}
        t = shots["sh010"]["takes"][0]
        self.assertEqual((t["status"], t["seed"], t["seed_source"]),
                         ("ok", str(built["sh010"]), "stable"))
        self.assertGreater(built["sh010"], 2 ** 53)       # why they are strings
        self.assertEqual(shots["sh030"]["takes"], [])

    def test_shot_detail(self):
        self.render("sh010")
        d = self.ok(A.get_shot(self.ctx, {"ep": self.ep, "pass": "proxy", "shot": "sh010"}))
        self.assertIsInstance(d["built"]["seed"], str)
        self.assertIsInstance(d["effective"]["seed"], str)
        self.assertEqual(d["effective"]["seed"], d["built"]["seed"])
        self.assertIsInstance(d["built_prompt"], str)
        (t,) = d["takes"]
        self.assertEqual(t["sidecar"]["seed"], d["built"]["seed"])
        self.assertEqual(t["files"]["mp4"], "renders_proxy/sh010/sh010_t01.mp4")
        self.assertEqual(t["files"]["shotlist"], "renders_proxy/sh010/sh010_t01.shotlist.json")
        self.err(A.get_shot(self.ctx, {"ep": self.ep, "pass": "proxy", "shot": "sh999"}), 404)
        self.err(A.get_shot(self.ctx, {"ep": self.ep, "pass": "proxy"}), 400)


class SweepTest(ApiTest):
    def test_history_error_marks_take_failed_with_its_message(self):
        self.comfy.mode = "error"
        (q,) = self.render("sh010")["queued"]
        self.assertEqual(T.get_take(self.ep, "proxy", "sh010", 1).status, "queued")
        self.events.clear()
        _, shots = self.status()
        t = shots["sh010"]["takes"][0]
        self.assertEqual(t["status"], "failed")
        self.assertEqual(t["save_notes"], "boom")
        self.assertEqual(self.events_of("h3pipe.take"),
                         [{"ep": self.ep, "pass": "proxy", "shot": "sh010", "take": 1,
                           "status": "failed", "thumb": None}])

    def test_pending_job_is_left_alone(self):
        self.comfy.mode = "hold"
        self.render("sh010")
        _, shots = self.status()
        self.assertEqual(shots["sh010"]["takes"][0]["status"], "queued")
        self.comfy.running, self.comfy.pending = self.comfy.pending, []
        _, shots = self.status()
        self.assertEqual(shots["sh010"]["takes"][0]["status"], "queued")

    def test_job_gone_without_history_is_swept(self):
        T.reserve_take(self.ep, "proxy", "sh010",
                       {"status": "queued", "comfy_prompt_id": "gone",
                        "queued": "2026-01-01T00:00:00+00:00"})
        _, shots = self.status()
        t = shots["sh010"]["takes"][0]
        self.assertEqual(t["status"], "failed")
        self.assertIn("left the ComfyUI queue", t["save_notes"])

    def test_finished_job_the_saver_did_not_close(self):
        self.comfy.mode = "oldnode"
        self.render("sh010")
        _, shots = self.status()
        t = shots["sh010"]["takes"][0]
        self.assertEqual(t["status"], "ok")
        self.assertIn("h3jobs", t["save_notes"])

    def test_no_comfy_means_no_sweep(self):
        T.reserve_take(self.ep, "proxy", "sh010",
                       {"status": "queued", "comfy_prompt_id": "gone",
                        "queued": "2026-01-01T00:00:00+00:00"})
        ctx = A.Context(self.user, "http://127.0.0.1:9", env={})
        data = self.ok(A.get_episode(ctx, {"ep": self.ep, "pass": "proxy"}))
        sh = next(s for s in data["shots"] if s["shot"] == "sh010")
        self.assertEqual(sh["takes"][0]["status"], "queued")


class RenderTest(ApiTest):
    def test_queue_skip_redo(self):
        data = self.render("sh010", "sh020", note="first")
        self.assertEqual([q["shot"] for q in data["queued"]], ["sh010", "sh020"])
        doc = J.load_shotlist(self.ep, "proxy")
        seeds = {s["id"]: s["seed"] for s in doc["shots"]}
        for q in data["queued"]:
            self.assertEqual((q["take"], q["seed"], q["seed_source"]),
                             (1, str(seeds[q["shot"]]), "stable"))
            self.assertTrue(q["prompt_id"])
        self.assertEqual((data["skipped"], data["errors"]), ([], []))
        # the queued take is on disk as the CLI writes it, then closed by the "saver"
        t = T.get_take(self.ep, "proxy", "sh010", 1)
        self.assertEqual(t.sidecar["comfy_prompt_id"], data["queued"][0]["prompt_id"])
        self.assertEqual(t.sidecar["note"], "first")
        self.assertTrue(os.path.isfile(t.paths.shotlist))
        g = self.comfy.graphs[0]
        si = next(v["inputs"] for v in g.values() if v["class_type"] == J.SAVER)
        self.assertEqual(si["subfolder"], "renders_proxy")
        self.assertEqual([d["status"] for d in self.events_of("h3pipe.take")],
                         ["queued", "queued"])
        self.assertEqual(self.events_of("h3pipe.episode"), [{"ep": self.ep}])

        data = self.render("sh010")
        self.assertEqual(data["queued"], [])
        self.assertEqual(data["skipped"], [{"shot": "sh010", "take": 1,
                                            "reason": "has a usable take (pass redo: true)"}])

        data = self.render("sh010", redo=True, seed="123", parent_take=1,
                           loras=[{"name": "a.safetensors", "strength": 0.5}], steps=6)
        (q,) = data["queued"]
        self.assertEqual((q["take"], q["seed"], q["seed_source"]), (2, "123", "typed"))
        sc = T.get_take(self.ep, "proxy", "sh010", 2).sidecar
        self.assertEqual((sc["seed"], sc["parent_take"], sc["steps"]), (123, 1, 6))
        self.assertEqual(sc["loras"], [{"name": "a.safetensors", "strength": 0.5}])

        big = str(2 ** 63 - 25)
        (q,) = self.render("sh010", redo=True, seed=big)["queued"]
        self.assertEqual(q["seed"], big)
        self.assertEqual(T.get_take(self.ep, "proxy", "sh010", 3).sidecar["seed"], int(big))

        data = self.render("sh010", redo=True, seed_mode="new")
        self.assertEqual(data["queued"][0]["seed_source"], "new")

    def test_busy_take_is_skipped(self):
        self.comfy.mode = "hold"
        self.render("sh010")
        data = self.render("sh010")
        self.assertEqual(data["skipped"][0]["reason"], "t01 is still queued")

    def test_errors(self):
        data = self.render("sh999", "sh010")
        self.assertEqual(data["errors"][0]["shot"], "sh999")
        self.assertIn("not in", data["errors"][0]["error"])
        self.assertEqual([q["shot"] for q in data["queued"]], ["sh010"])

        self.comfy.mode = "reject"
        self.events.clear()
        data = self.render("sh020")
        (e,) = data["errors"]
        self.assertEqual((e["shot"], e["take"]), ("sh020", 1))
        self.assertIn("bad input", e["error"])
        t = T.get_take(self.ep, "proxy", "sh020", 1)
        self.assertEqual(t.status, "failed")
        self.assertIn("bad input", t.sidecar["save_notes"])
        self.assertEqual(self.events_of("h3pipe.take")[0]["status"], "failed")

    def test_bad_input(self):
        for body in ({"seed": "12x"}, {"seed": -1}, {"seed_mode": "odd"}, {"shots": "sh010"},
                     {"steps": 0}, {"loras": [{"strength": 1}]}, {"redo": "yes"},
                     {"prompt": 5}, {"pass": "draft"}):
            b = {"ep": self.ep, "pass": "proxy", "shots": ["sh010"]}
            b.update(body)
            self.err(A.post_render(self.ctx, b), 400)
        self.err(A.post_render(self.ctx, "nope"), 400)
        self.assertEqual(self.comfy.graphs, [])

    def test_all_shots_when_shots_is_null(self):
        data = self.render()
        self.assertEqual(len(data["queued"]), len(J.load_shotlist(self.ep, "proxy")["shots"]))


class CancelTest(ApiTest):
    def cancel(self, take=1, status=200):
        res = A.post_cancel(self.ctx, {"ep": self.ep, "pass": "proxy", "shot": "sh010",
                                       "take": take})
        return self.ok(res) if status == 200 else self.err(res, status)

    def test_pending_job_is_deleted(self):
        self.comfy.mode = "hold"
        (q,) = self.render("sh010")["queued"]
        data = self.cancel()
        self.assertEqual((data["status"], data["save_notes"]), ("failed", "cancelled"))
        self.assertEqual(self.comfy.posts, [("/queue", {"delete": [q["prompt_id"]]})])
        self.assertEqual(T.get_take(self.ep, "proxy", "sh010", 1).status, "failed")
        self.assertEqual(self.events_of("h3pipe.take")[-1]["status"], "failed")

    def test_running_job_is_interrupted(self):
        self.comfy.mode = "hold"
        (q,) = self.render("sh010")["queued"]
        self.comfy.running, self.comfy.pending = self.comfy.pending, []
        self.cancel()
        self.assertEqual(self.comfy.posts, [("/interrupt", {"prompt_id": q["prompt_id"]})])

    def test_not_queued(self):
        self.render("sh010")
        self.assertIn("not queued", self.cancel(status=409))
        self.cancel(take=7, status=404)
        self.cancel(take="x", status=400)


class CutTest(ApiTest):
    def pick(self, take, status=200, **kw):
        body = {"ep": self.ep, "pass": "proxy", "shot": "sh010", "take": take}
        body.update(kw)
        res = A.put_pick(self.ctx, body)
        return self.ok(res) if status == 200 else self.err(res, status)

    def test_pick(self):
        self.render("sh010")
        self.render("sh010", redo=True)
        cut = self.pick(1)["cut"]
        self.assertIn({"shot": "sh010", "take": 1}, cut["proxy"])
        self.assertEqual(cut["episode"], "ks01")
        _, shots = self.status()
        self.assertEqual((shots["sh010"]["cut"]["take"], shots["sh010"]["cut"]["picked"]),
                         (1, True))
        self.assertEqual(self.events_of("h3pipe.episode")[-1], {"ep": self.ep})
        cut = self.pick(None)["cut"]
        self.assertIn({"shot": "sh010"}, cut["proxy"])
        self.assertEqual(self.status()[1]["sh010"]["cut"]["take"], 2)

        self.comfy.mode = "error"
        self.render("sh010", redo=True)                  # t03 stays queued until swept
        self.assertIn("force", self.pick(3, status=409))
        self.assertIn({"shot": "sh010", "take": 3}, self.pick(3, force=True)["cut"]["proxy"])
        self.pick(9, status=404)
        self.pick(1, status=404, shot="sh999")
        self.pick(0, status=400)

    def test_placeholder_from_other_pass(self):
        self.render("sh010", pass_="proxy")
        cut = self.ok(A.put_pick(self.ctx, {"ep": self.ep, "pass": "final", "shot": "sh010",
                                            "take": 1, "from_pass": "proxy"}))["cut"]
        self.assertIn({"shot": "sh010", "pass": "proxy", "take": 1}, cut["final"])

    def test_cut_replace(self):
        order = [s["id"] for s in J.load_shotlist(self.ep, "proxy")["shots"]]
        entries = [{"shot": order[1], "trim_in": 3, "locked": True, "note": "tight"},
                   {"shot": order[0], "take": 2, "pass": "final"},
                   {"shot": "sh999", "trim_out": 0}]
        cut = self.ok(A.put_cut(self.ctx, {"ep": self.ep, "pass": "proxy",
                                           "entries": entries}))["cut"]
        self.assertEqual(cut["proxy"], [
            {"shot": order[1], "trim_in": 3, "locked": True, "note": "tight"},
            {"shot": order[0], "take": 2, "pass": "final"},
            {"shot": "sh999"}])
        self.assertEqual(T.load_cut(self.ep)["proxy"], cut["proxy"])
        data, shots = self.status()
        names = [s["shot"] for s in data["shots"]]
        self.assertLess(names.index(order[1]), names.index(order[0]))
        self.assertLess(names.index(order[0]), names.index("sh999"))
        self.assertEqual(len(names), len(order) + 1)
        self.assertTrue(shots["sh999"]["orphan"])
        self.assertEqual(self.events_of("h3pipe.episode")[-1], {"ep": self.ep})
        for bad in ([{"take": 1}], [{"shot": "a"}, {"shot": "a"}], [{"shot": "a", "trim_in": -1}],
                    [{"shot": "a", "colour": "red"}], "x"):
            self.err(A.put_cut(self.ctx, {"ep": self.ep, "pass": "proxy", "entries": bad}), 400)


class OverrideTest(ApiTest):
    def put(self, fields, status=200, **kw):
        body = {"ep": self.ep, "pass": "proxy", "shot": "sh020", "fields": fields}
        body.update(kw)
        res = A.put_override(self.ctx, body)
        return self.ok(res) if status == 200 else self.err(res, status)

    def built(self, pass_):
        return next(s for s in J.load_shotlist(self.ep, pass_)["shots"] if s["id"] == "sh020")

    def block(self):
        return T.load_overrides(self.ep)["shots"]["sh020"][T.DEFAULT_TARGET]

    def test_set_and_clear(self):
        big = str(2 ** 63 - 7)
        data = self.put({"prompt": "calmer", "seed": big, "steps": 6, "note": "n"})["override"]
        self.assertEqual(data["proxy"], {"prompt": "calmer", "seed": big, "steps": 6,
                                         "note": "n", "stale": False})
        self.assertEqual(data["final"], {"seed": big, "note": "n", "stale": False})
        b = self.block()
        self.assertEqual(b["seed"], int(big))
        self.assertEqual(b["proxy"]["base_hash"], J.story_hash(self.built("proxy")))
        self.assertNotIn("final", b)
        self.assertEqual(self.events_of("h3pipe.episode")[-1], {"ep": self.ep})
        # a render now uses it
        (q,) = self.render("sh020")["queued"]
        self.assertEqual((q["seed"], q["seed_source"]), (big, "override"))

        # null clears one field; the others stay
        data = self.put({"seed": None, "steps": None})["override"]
        self.assertEqual(data["proxy"], {"prompt": "calmer", "note": "n", "stale": False})

        # DELETE one pass keeps the shared fields; DELETE all removes the shot
        data = self.ok(A.delete_override(self.ctx, {"ep": self.ep, "shot": "sh020",
                                                    "pass": "proxy"}))["override"]
        self.assertEqual(data["proxy"], {"note": "n", "stale": False})
        self.put({"model": "m.safetensors"})
        self.ok(A.delete_override(self.ctx, {"ep": self.ep, "shot": "sh020"}))
        self.assertNotIn("sh020", T.load_overrides(self.ep)["shots"])

    def test_both_passes_stamp_their_own_build(self):
        loras = [{"name": "a.safetensors", "strength": 0.6}]
        data = self.put({"loras": loras, "model": "m.safetensors"}, both=True)["override"]
        for ps in ("final", "proxy"):
            self.assertEqual(data[ps]["loras"], loras)
            self.assertEqual(self.block()[ps]["base_hash"], J.story_hash(self.built(ps)))
        self.assertNotEqual(self.block()["final"]["base_hash"],
                            self.block()["proxy"]["base_hash"])

    def test_same_file_as_the_cli(self):
        self.put({"steps": 10, "seed": "5", "loras": ["b.safetensors:0.4"]})
        via_api = T.read_json(os.path.join(self.ep, "overrides.json"))
        os.remove(os.path.join(self.ep, "overrides.json"))
        r = subprocess.run([sys.executable, os.path.join(ROOT, "h3.py"), "override", self.ep,
                            "sh020", "--proxy", "--steps", "10", "--seed", "5",
                            "--lora", "b.safetensors:0.4"], capture_output=True, env=ENV)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(T.read_json(os.path.join(self.ep, "overrides.json")), via_api)

    def test_stale_after_rebuild(self):
        self.put({"prompt": "x"})
        ov = T.load_overrides(self.ep)
        T.set_override(ov, "sh020", "proxy", base_hash="old")
        T.save_overrides(self.ep, ov)
        data = self.ok(A.delete_override(self.ctx, {"ep": self.ep, "shot": "sh020",
                                                    "pass": "final"}))["override"]
        self.assertTrue(data["proxy"]["stale"])
        _, shots = self.status()
        self.assertTrue(shots["sh020"]["override"]["stale"])

    def test_bad_input(self):
        self.put({"colour": "red"}, status=400)
        self.put({"seed": "abc"}, status=400)
        self.put({"steps": "6"}, status=400)
        self.put("x", status=400)
        self.put({"seed": "1"}, status=404, shot="sh999")


@unittest.skipUnless(HAVE_FF, "ffmpeg/ffprobe not on PATH")
class AssembleTest(ApiTest):
    def test_errors_are_results(self):
        # nothing rendered
        data = self.ok(A.post_assemble(self.ctx, {"ep": self.ep, "pass": "proxy"}))
        self.assertFalse(data["ok"])
        self.assertIsNone(data["output"])
        self.assertIn("nothing rendered", data["report"])
        # FakeComfy's mp4s are empty files: ffmpeg fails, and says so
        self.render("sh010")
        data = self.ok(A.post_assemble(self.ctx, {"ep": self.ep, "pass": "proxy",
                                                  "partial": True}))
        self.assertFalse(data["ok"])
        self.assertTrue(data["error"])
        self.err(A.post_assemble(self.ctx, {"ep": self.ep, "partial": "yes"}), 400)


class SeedTest(unittest.TestCase):
    def test_seeds_out(self):
        self.assertEqual(A.seeds_out({"seed": 2 ** 63 - 1, "x": [{"seed": 5, "take": 2}],
                                      "y": {"seed": None}, "z": True}),
                         {"seed": str(2 ** 63 - 1), "x": [{"seed": "5", "take": 2}],
                          "y": {"seed": None}, "z": True})

    def test_seed_in(self):
        self.assertEqual(A.seed_in("6430499148929255544"), 6430499148929255544)
        self.assertEqual(A.seed_in(12), 12)
        self.assertIsNone(A.seed_in(None))
        for bad in ("", "1.5", "-3", True, 1.0, "1e5", str(2 ** 64)):
            with self.assertRaises(A.ApiError):
                A.seed_in(bad)


if __name__ == "__main__":
    unittest.main()


class MissingRefsAndBrowseTest(ApiTest):
    def remove_plate(self, rel):
        os.remove(os.path.join(self.ep, rel))

    def test_missing_ref_blocks_unless_render_anyway(self):
        _, shots = self.status()
        self.assertEqual(shots["sh010"]["missing_refs"], [])
        plate = next(r for r in J.ref_slots(J.load_shotlist(self.ep, "proxy"),
                                            J.load_shotlist(self.ep, "proxy")["shots"][0])
                     if r["slot"] == "Picture 4")["path"]
        self.remove_plate(plate)
        _, shots = self.status()
        self.assertEqual([m["slot"] for m in shots["sh010"]["missing_refs"]], ["Picture 4"])
        res = self.render("sh010")
        self.assertEqual(res["queued"], [])
        self.assertIn("missing refs", res["skipped"][0]["reason"])
        self.assertEqual(self.comfy.graphs, [])
        res = self.render("sh010", allow_missing_refs=True)
        self.assertEqual(len(res["queued"]), 1)
        t = T.get_take(self.ep, "proxy", "sh010", res["queued"][0]["take"])
        self.assertEqual(T.read_json(t.paths.shotlist)["shots"][0]["missing_refs"], "blank")
        self.assertEqual(t.sidecar["missing_refs"], ["Picture 4"])
        self.err(A.post_render(self.ctx, {"ep": self.ep, "allow_missing_refs": "yes"}), 400)

    def test_browse(self):
        top = self.ok(A.get_browse(self.ctx, {}))
        self.assertIsNone(top["parent"])
        self.assertTrue(top["dirs"])
        res = self.ok(A.get_browse(self.ctx, {"path": self.shows}))
        (d,) = res["dirs"]
        self.assertEqual((d["name"], d["episode"], d["bible"]), ("ks01", True, True))
        self.assertEqual(os.path.normcase(res["parent"]), os.path.normcase(self.tmp))
        self.assertTrue(self.ok(A.get_browse(self.ctx, {"path": self.ep}))["episode"])
        self.err(A.get_browse(self.ctx, {"path": os.path.join(self.tmp, "nope")}), 404)
