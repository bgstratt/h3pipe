"""
Phase 11 (docs/API.md "Phase 11: the graph handoff"): which workflow a target's
next render uses, copying a target's repo workflow into ComfyUI's saved
workflows to edit it on the canvas, and taking it back. The routes run through
h3pipe_api with test_render's FakeComfy, as test_api does; the client's
userdata write/delete go to the same fake.
"""
from __future__ import annotations

import copy
import io
import json
import os
import sys
import unittest
from contextlib import redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "comfy_nodes"))
import h3edit as E  # noqa: E402
import h3jobs as J  # noqa: E402
import h3pipe_api as A  # noqa: E402
import targets as TG  # noqa: E402
from test_api import ApiTest  # noqa: E402
from test_render import FakeComfy  # noqa: E402

H3 = "minimax_h3_ref2va"
WAN = "wan22_i2v"


def repo_graph(tid: str) -> dict:
    t = TG.load_target(tid)
    return json.load(io.open(t.binding.workflow, encoding="utf-8"))


class ClientTest(unittest.TestCase):
    """Comfy.list_userdata / put_userdata / delete_userdata."""

    def setUp(self):
        self.fake = FakeComfy()
        self.comfy = J.Comfy(self.fake.url)

    def tearDown(self):
        self.fake.close()

    def test_write_read_list_delete(self):
        self.assertEqual(self.comfy.list_userdata(), [])
        self.comfy.put_userdata("workflows/x.json", {"a": 1})
        self.assertEqual(self.comfy.userdata("workflows/x.json"), {"a": 1})
        self.assertEqual(self.comfy.list_userdata(), ["x.json"])
        self.assertTrue(self.comfy.delete_userdata("workflows/x.json"))
        self.assertIsNone(self.comfy.userdata("workflows/x.json"))
        self.assertFalse(self.comfy.delete_userdata("workflows/x.json"))

    def test_overwrite_false_is_409(self):
        self.comfy.put_userdata("workflows/x.json", {"a": 1})
        with self.assertRaises(RuntimeError) as cm:
            self.comfy.put_userdata("workflows/x.json", {"a": 2}, overwrite=False)
        self.assertIn("409", str(cm.exception))
        self.assertEqual(self.comfy.userdata("workflows/x.json"), {"a": 1})   # unchanged
        self.comfy.put_userdata("workflows/x.json", {"a": 2})
        self.assertEqual(self.comfy.userdata("workflows/x.json"), {"a": 2})

    def test_bytes_are_written_verbatim(self):
        raw = json.dumps({"b": 2}, indent=4).encode()
        self.comfy.put_userdata("workflows/y.json", raw)
        self.assertEqual(self.fake.userdata_raw["workflows/y.json"], raw)


class GraphSourceTest(unittest.TestCase):
    """h3jobs.target_graph_source: which graph is in force, and `differs`."""

    def setUp(self):
        self.fake = FakeComfy()
        self.t = TG.load_target(H3)
        self.env = mock.patch.dict(os.environ, {}, clear=False)
        self.env.start()
        os.environ.pop("H3_WORKFLOW", None)
        os.environ.pop("COMFYUI_PATH", None)

    def tearDown(self):
        self.env.stop()
        self.fake.close()

    def source(self, **kw):
        return J.target_graph_source(self.t, self.fake.url, **kw)

    def test_repo_when_comfy_has_none(self):
        g = self.source()
        self.assertEqual(g["source"], "repo")
        self.assertFalse(g["installed"])
        self.assertFalse(g["differs"])
        self.assertEqual(g["name"], self.t.binding.workflow_name)
        self.assertEqual(g["error"], "")

    def test_saved_copy_wins_and_matches(self):
        self.fake.userdata[f"workflows/{self.t.binding.workflow_name}"] = repo_graph(H3)
        g = self.source()
        self.assertEqual(g["source"], "comfy")
        self.assertTrue(g["installed"])
        self.assertFalse(g["differs"])                    # a verbatim copy is not an edit

    def test_edit_the_binding_patches_is_not_a_difference(self):
        """project_root, the model file, the sampler: a job overwrites them, so
        a canvas that once rendered an episode is not "edited here"."""
        saved = repo_graph(H3)
        for node in saved["nodes"]:
            if node["type"] == "H3ShotListLoader":
                node["widgets_values"][0] = r"C:\Shows\ep05"
            if node["type"] == "UNETLoader":
                node["widgets_values"][0] = "something_else.safetensors"
            if node["type"] == "KSamplerSelect":
                node["widgets_values"][0] = "dpmpp_2m"
        self.fake.userdata[f"workflows/{self.t.binding.workflow_name}"] = saved
        g = self.source()
        self.assertEqual(g["source"], "comfy")
        self.assertFalse(g["differs"])

    def test_real_edit_differs(self):
        saved = repo_graph(H3)
        for node in saved["nodes"]:
            if node["type"] == "UNETLoader":              # weight_dtype: no binding param
                node["widgets_values"][1] = "fp8_e4m3fn"
        self.fake.userdata[f"workflows/{self.t.binding.workflow_name}"] = saved
        self.assertTrue(self.source()["differs"])

    def test_removed_node_differs(self):
        saved = repo_graph(H3)
        saved["nodes"] = [n for n in saved["nodes"] if n["type"] != "H3SLAAttention"]
        self.fake.userdata[f"workflows/{self.t.binding.workflow_name}"] = saved
        self.assertTrue(self.source()["differs"])

    def test_env_wins_and_is_reported(self):
        p = os.path.join(self.fake.url.replace("http://", ""), "nope.json")
        os.environ["H3_WORKFLOW"] = p                     # set but missing: repo still wins
        g = self.source()
        self.assertEqual(g["source"], "repo")
        self.assertTrue(g["env_set"])
        self.assertEqual(g["env"], "H3_WORKFLOW")

    def test_env_file_wins(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "mine.json")
            json.dump(repo_graph(H3), io.open(p, "w", encoding="utf-8"))
            os.environ["H3_WORKFLOW"] = p
            self.fake.userdata[f"workflows/{self.t.binding.workflow_name}"] = repo_graph(H3)
            g = self.source()
            self.assertEqual(g["source"], "env")
            self.assertEqual(g["where"], p)
            self.assertTrue(g["installed"])               # a saved copy exists, it just loses

    def test_with_graph_returns_the_api_graph(self):
        g = self.source(with_graph=True)
        self.assertTrue(all("class_type" in v for v in g["graph"].values()))

    def test_offline_falls_back_to_the_repo(self):
        g = J.target_graph_source(self.t, "http://127.0.0.1:1")     # nothing listening
        self.assertEqual(g["source"], "repo")
        self.assertFalse(g["installed"])


class InstallTest(unittest.TestCase):
    """h3edit.install_workflow / revert_workflow."""

    def setUp(self):
        self.fake = FakeComfy()
        self.comfy = J.Comfy(self.fake.url)
        self.t = TG.load_target(WAN)

    def tearDown(self):
        self.fake.close()

    def test_install_writes_the_file_verbatim(self):
        r = E.install_workflow(self.t, self.comfy)
        name = self.t.binding.workflow_name
        self.assertEqual((r["name"], r["renders"]), (name, True))
        with open(self.t.binding.workflow, "rb") as fh:
            self.assertEqual(self.fake.userdata_raw[f"workflows/{name}"], fh.read())
        self.assertEqual(J.target_graph_source(self.t, self.fake.url)["source"], "comfy")

    def test_install_twice_needs_force(self):
        E.install_workflow(self.t, self.comfy)
        with self.assertRaises(RuntimeError) as cm:
            E.install_workflow(self.t, self.comfy)
        self.assertIn("409", str(cm.exception))
        E.install_workflow(self.t, self.comfy, overwrite=True)

    def test_scratch_name_does_not_render(self):
        r = E.install_workflow(self.t, self.comfy, "scratch.json")
        self.assertEqual((r["name"], r["renders"]), ("scratch.json", False))
        self.assertEqual(J.target_graph_source(self.t, self.fake.url)["source"], "repo")

    def test_bad_name(self):
        for name in ("x.txt", "../x.json", "sub/../../x.json"):
            with self.assertRaises(ValueError):
                E.install_workflow(self.t, self.comfy, name)
        # an empty name means "the one the binding looks up", as no name does
        self.assertEqual(E.install_workflow(self.t, self.comfy, "")["name"],
                         self.t.binding.workflow_name)

    def test_revert(self):
        E.install_workflow(self.t, self.comfy)
        self.assertEqual(E.revert_workflow(self.t, self.comfy)["deleted"], True)
        self.assertEqual(E.revert_workflow(self.t, self.comfy)["deleted"], False)
        self.assertEqual(J.target_graph_source(self.t, self.fake.url)["source"], "repo")

    def test_target_graphs_and_lookup(self):
        graphs = E.target_graphs(TG.list_targets("video"), self.fake.url)
        self.assertIn(H3, graphs)
        graph, where = E.graph_lookup(graphs)(self.t)
        self.assertTrue(where)
        self.assertTrue(all("class_type" in v for v in graph.values()))

    def test_target_graphs_lists_saved_workflows_once(self):
        """One /userdata listing, not one read per target, when ComfyUI has none
        of them saved — and the one it does have is still found."""
        E.install_workflow(self.t, self.comfy)
        reads = []
        orig = J.Comfy.userdata

        def counted(self_, path):
            reads.append(path)
            return orig(self_, path)

        with mock.patch.object(J.Comfy, "userdata", counted):
            graphs = E.target_graphs(TG.list_targets("video"), self.fake.url)
        self.assertEqual(reads, [f"workflows/{self.t.binding.workflow_name}"])
        self.assertEqual(graphs[WAN]["source"], "comfy")
        self.assertEqual(graphs[H3]["source"], "repo")


class RoutesTest(ApiTest):
    """POST / DELETE /h3pipe/workflow/install, and GET /h3pipe/targets' graph."""

    def setUp(self):
        super().setUp()
        A._GRAPHS.clear()
        A._READY.clear()

    def tearDown(self):
        A._GRAPHS.clear()
        A._READY.clear()
        super().tearDown()

    def graph_of(self, tid: str) -> dict:
        d = self.ok(A.get_targets(self.ctx, {}))
        return next(t["graph"] for t in d["targets"] if t["id"] == tid)

    def test_targets_carries_the_graph_block(self):
        g = self.graph_of(H3)
        self.assertEqual(g["source"], "repo")
        self.assertEqual(g["name"], TG.load_target(H3).binding.workflow_name)
        self.assertNotIn("graph", g)                      # the graph itself is never sent

    def test_install_then_revert(self):
        d = self.ok(A.post_workflow_install(self.ctx, {"target": H3}))
        self.assertTrue(d["renders"])
        self.assertEqual(d["graph"]["source"], "comfy")
        self.assertEqual(self.graph_of(H3)["source"], "comfy")     # the cache was dropped
        self.assertFalse(self.graph_of(H3)["differs"])
        d = self.ok(A.delete_workflow_install(self.ctx, {"target": H3}))
        self.assertTrue(d["deleted"])
        self.assertEqual(d["graph"]["source"], "repo")
        self.assertEqual(self.graph_of(H3)["source"], "repo")

    def test_install_twice_is_409(self):
        self.ok(A.post_workflow_install(self.ctx, {"target": H3}))
        self.err(A.post_workflow_install(self.ctx, {"target": H3}), 409)
        self.ok(A.post_workflow_install(self.ctx, {"target": H3, "overwrite": True}))

    def test_bad_requests(self):
        self.err(A.post_workflow_install(self.ctx, {}), 400)
        self.err(A.post_workflow_install(self.ctx, {"target": "nope"}), 400)
        self.err(A.post_workflow_install(self.ctx, {"target": H3, "name": "x.txt"}), 400)
        self.err(A.post_workflow_install(self.ctx, {"target": H3, "overwrite": "yes"}), 400)
        self.err(A.delete_workflow_install(self.ctx, {"target": "nope"}), 400)

    def test_revert_when_nothing_is_saved(self):
        d = self.ok(A.delete_workflow_install(self.ctx, {"target": H3}))
        self.assertFalse(d["deleted"])

    def test_readiness_judges_the_graph_in_force(self):
        """A saved graph with a node this ComfyUI doesn't have is what readiness
        reports on — not the repo's copy."""
        saved = repo_graph(H3)
        node = copy.deepcopy(next(n for n in saved["nodes"] if n["type"] == "H3SLAAttention"))
        node["id"] = 9999
        node["type"] = "SomeNodePackNobodyHas"
        node["inputs"] = []
        node["outputs"] = []
        saved["nodes"].append(node)
        self.comfy.userdata[f"workflows/{TG.load_target(H3).binding.workflow_name}"] = saved
        d = self.ok(A.get_targets(self.ctx, {"kind": "video", "ready": "1"}))
        h3 = next(t for t in d["targets"] if t["id"] == H3)
        self.assertEqual(h3["graph"]["source"], "comfy")
        self.assertIn("SomeNodePackNobodyHas", h3["readiness"]["nodes_missing"])
        self.assertEqual(h3["readiness"]["status"], "not_ready")


class CliTest(unittest.TestCase):
    """h3.py targets: the graph line, --install-workflow, --revert-workflow."""

    def setUp(self):
        self.fake = FakeComfy()
        E._NODES.clear()

    def tearDown(self):
        self.fake.close()

    def run_cmd(self, *argv) -> tuple[int, str]:
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = E.cmd_targets(None, ["--comfy", self.fake.url, *argv])
        return code, buf.getvalue()

    def test_graph_line_and_install_revert(self):
        code, out = self.run_cmd("--kind", "video")
        self.assertEqual(code, 0, out)
        self.assertIn("[repo copy]", out)
        code, out = self.run_cmd("--install-workflow", WAN)
        self.assertEqual(code, 0, out)
        self.assertIn("wrote user workflows/", out)
        self.assertIn("[saved in ComfyUI]", out)
        code, out = self.run_cmd("--install-workflow", WAN)
        self.assertEqual(code, 1)
        self.assertIn("--force", out)
        code, out = self.run_cmd("--revert-workflow", WAN)
        self.assertEqual(code, 0, out)
        self.assertIn("[repo copy]", out)

    def test_unknown_target(self):
        code, out = self.run_cmd("--install-workflow", "nope")
        self.assertEqual(code, 1)
        self.assertIn("no target called", out)

    def test_json_has_graphs(self):
        code, out = self.run_cmd("--kind", "video", "--json")
        d = json.loads(out)
        self.assertEqual(d["graphs"][WAN]["source"], "repo")
        self.assertNotIn("graph", d["graphs"][WAN])


if __name__ == "__main__":
    unittest.main()
