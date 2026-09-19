"""
h3jobs planning rules, graph patching, and h3render end to end against a fake
ComfyUI (a stdlib HTTP server that plays the part of the save node).
"""
from __future__ import annotations

import json
import os
import random
import subprocess
import sys
import tempfile
import threading
import unittest
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import h3jobs as J  # noqa: E402
import h3takes as T  # noqa: E402

FIXTURE = os.path.join(HERE, "fixtures", "kitchen_sink")
WORKFLOW = os.path.join(ROOT, "targets", "video", "minimax_h3_ref2va", "workflow.json")
ENV = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")


def build_episode(root: str, refs: bool = True) -> None:
    for flags in ([], ["--proxy"]):
        subprocess.run([sys.executable, os.path.join(ROOT, "h3build.py"),
                        os.path.join(FIXTURE, "series.json"),
                        os.path.join(FIXTURE, "script.md"), "-o", root, *flags],
                       check=True, capture_output=True, env=ENV)
    if refs:
        stub_refs(root)


def stub_refs(root: str) -> None:
    """An empty file at every reference path the build asked for, so shots
    aren't blocked for missing refs (these tests are about takes)."""
    for name in ("refs_todo.json", "refs_todo_proxy.json"):
        p = os.path.join(root, name)
        if not os.path.isfile(p):
            continue
        for item in json.load(open(p, encoding="utf-8")):
            f = os.path.join(root, item["path"])
            os.makedirs(os.path.dirname(f), exist_ok=True)
            if not os.path.isfile(f):
                open(f, "wb").close()
    # the dialogue recording dub shots read isn't in refs_todo
    for name in ("shotlist.json", "shotlist_proxy.json"):
        p = os.path.join(root, "shotlist", name)
        track = (json.load(open(p, encoding="utf-8")).get("defaults", {}).get("master_track")
                 if os.path.isfile(p) else None)
        if track:
            f = os.path.join(root, track)
            os.makedirs(os.path.dirname(f), exist_ok=True)
            if not os.path.isfile(f):
                open(f, "wb").close()


def shot_ids(root: str, pass_: str = "final") -> list[str]:
    return [s["id"] for s in J.load_shotlist(root, pass_)["shots"]]


# ---------------------------------------------------------------------------
# planning
# ---------------------------------------------------------------------------

class PlanTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = cls._tmp.name
        build_episode(cls.root)
        cls.doc = J.load_shotlist(cls.root, "final")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        for sub in ("renders", "renders_proxy"):
            d = os.path.join(self.root, sub)
            if os.path.isdir(d):
                import shutil
                shutil.rmtree(d)

    def plan(self, i=0, ov=None, **req):
        sid = self.doc["shots"][i]["id"]
        return J.plan_job(self.root, "final", self.doc, i, J.RenderRequest(sid, **req), ov,
                          rng=random.Random(7))

    def finished_take(self, sid, n=None):
        t = T.reserve_take(self.root, "final", sid, {"status": "queued"}, take=n)
        T.update_sidecar(t.paths.sidecar, status="ok")
        open(t.paths.mp4, "wb").close()
        return t

    def test_first_render_uses_built_seed(self):
        j = self.plan()
        self.assertEqual((j.action, j.take, j.seed_source), ("render", 1, "stable"))
        self.assertEqual(j.seed, self.doc["shots"][0]["seed"])

    def test_skip_then_redo_gets_new_seed(self):
        self.finished_take("sh010")
        self.assertEqual(self.plan().action, "skip")
        j = self.plan(redo=True)
        self.assertEqual((j.action, j.take, j.seed_source), ("redo", 2, "new"))
        self.assertNotEqual(j.seed, self.doc["shots"][0]["seed"])
        self.assertLess(j.seed, 2 ** J.NEW_SEED_BITS)

    def test_seed_modes(self):
        self.finished_take("sh010")
        built = self.doc["shots"][0]["seed"]
        self.assertEqual(self.plan(redo=True, seed_mode="same").seed, built)
        self.assertEqual(self.plan(redo=True, seed=5).seed_source, "typed")
        self.assertEqual(self.plan(seed_mode="new").seed_source, "new")
        ov = T.set_override({}, "sh010", seed=99)
        j = self.plan(redo=True, ov=ov)
        self.assertEqual((j.seed, j.seed_source), (99, "override"))
        self.assertEqual(self.plan(redo=True, ov=ov, seed_mode="new").seed_source, "new")
        self.assertEqual(self.plan(redo=True, ov=ov, seed=5).seed, 5)

    def test_queued_take_is_busy_failed_is_retried_with_built_seed(self):
        T.reserve_take(self.root, "final", "sh010", {"status": "queued"})
        self.assertEqual(self.plan().action, "busy")
        T.update_sidecar(T.take_paths(self.root, "final", "sh010", 1).sidecar, status="failed")
        j = self.plan()
        self.assertEqual((j.action, j.take, j.seed_source), ("retry", 2, "stable"))

    def test_forced_take(self):
        self.finished_take("sh010")
        j = self.plan(take=1)
        self.assertEqual((j.action, j.take, j.forced), ("overwrite", 1, True))

    def test_overrides_and_request_precedence(self):
        ov = T.set_override({}, "sh010", "final", prompt="custom", base_hash="stale",
                            steps=11, model="ov_model.safetensors",
                            loras=[{"name": "a", "strength": 0.5}])
        j = self.plan(ov=ov)
        self.assertEqual((j.prompt, j.steps, j.model), ("custom", 11, "ov_model.safetensors"))
        self.assertEqual(j.loras, [{"name": "a", "strength": 0.5}])
        self.assertEqual(j.overridden, ["loras", "model", "prompt", "steps"])
        self.assertTrue(j.override_stale)
        j = self.plan(ov=ov, steps=3, loras=[])
        self.assertEqual((j.steps, j.loras), (3, []))
        T.set_override(ov, "sh010", "final", base_hash=J.story_hash(self.doc["shots"][0]))
        self.assertFalse(self.plan(ov=ov).override_stale)

    def test_lora_from_shotlist(self):
        # kitchen_sink sh040 has `lora: none`; others inherit the series LoRA
        idx = {s["id"]: i for i, s in enumerate(self.doc["shots"])}
        self.assertEqual(self.plan(idx["sh040"]).loras, [])
        self.assertEqual(self.plan(idx["sh010"]).loras,
                         [{"name": "series_turbo_8step.safetensors", "strength": 1.0}])
        self.assertEqual(self.plan(idx["sh210"]).loras,
                         [{"name": "seq_lora.safetensors", "strength": 1.0}])

    def test_parse_lora(self):
        self.assertEqual(J.parse_lora("none"), [])
        self.assertEqual(J.parse_lora("a.safetensors:0.6"), [{"name": "a.safetensors", "strength": 0.6}])
        self.assertEqual(J.parse_lora("C:/x/a.safetensors"),
                         [{"name": "C:/x/a.safetensors", "strength": 1.0}])

    def test_start_job_writes_frozen_shotlist_and_sidecar(self):
        ov = T.set_override({}, "sh010", "final", prompt=["one", "two"])
        j = self.plan(ov=ov, seed=42, note="hello")
        take = J.start_job(j)
        frozen = T.read_json(take.paths.shotlist)
        self.assertEqual(len(frozen["shots"]), 1)
        s = frozen["shots"][0]
        self.assertEqual((s["seed"], s["prompt"], s["id"]), (42, ["one", "two"], "sh010"))
        self.assertEqual(frozen["subjects"], self.doc["subjects"])
        sc = T.read_json(take.paths.sidecar)
        self.assertEqual((sc["status"], sc["seed"], sc["seed_source"], sc["note"]),
                         ("queued", 42, "typed", "hello"))
        self.assertEqual(sc["shot_hash"], J.story_hash(self.doc["shots"][0]))
        self.assertEqual([r["slot"] for r in sc["refs"]][-2:], ["Picture 1", "Picture 4"])
        self.assertEqual(sc["refs"][0]["sha1"], T.file_sha1(os.path.join(self.root, sc["refs"][0]["path"])))
        self.assertEqual(sc["missing_refs"], [])


# ---------------------------------------------------------------------------
# graph patching
# ---------------------------------------------------------------------------

class GraphTest(unittest.TestCase):
    def setUp(self):
        self.base = J.load_graph(WORKFLOW)

    def test_lora_chain(self):
        g = json.loads(json.dumps(self.base))
        J.apply_loras(g, [{"name": "a", "strength": 1.0}, {"name": "b", "strength": 0.6},
                          {"name": "c", "strength": 0.3}])
        loras = {k: v for k, v in g.items() if v["class_type"] == J.LORA}
        self.assertEqual(len(loras), 3)
        first = next(k for k, v in loras.items() if v["inputs"]["lora_name"] == "a")
        b = next(k for k, v in loras.items() if v["inputs"]["lora_name"] == "b")
        c = next(k for k, v in loras.items() if v["inputs"]["lora_name"] == "c")
        self.assertEqual(g[b]["inputs"]["model"], [first, 0])
        self.assertEqual(g[c]["inputs"]["model"], [b, 0])
        self.assertEqual(g[c]["inputs"]["strength_model"], 0.3)
        # whatever read the first LoRA now reads the last one, and nothing else does
        readers = [k for k, v in g.items() for x in v["inputs"].values() if x == [c, 0]]
        self.assertEqual(readers, ["3"])
        self.assertFalse([k for k, v in g.items() if k not in (b,)
                          for x in v["inputs"].values() if x == [first, 0]])

    def test_no_lora_is_strength_zero(self):
        g = json.loads(json.dumps(self.base))
        J.apply_loras(g, [])
        self.assertEqual(g["2"]["inputs"]["strength_model"], 0.0)

    def test_graph_for(self):
        with tempfile.TemporaryDirectory() as root:
            build_episode(root)
            doc = J.load_shotlist(root, "proxy")
            j = J.plan_job(root, "proxy", doc, 0, J.RenderRequest("sh010"), {})
            take = J.start_job(j)
            g = J.graph_for(self.base, j, take, review_copy=False)
            li, si = g["11"]["inputs"], g["42"]["inputs"]
            self.assertEqual(li["index"], 0)
            self.assertEqual(os.path.normpath(os.path.join(root, li["shotlist_file"])),
                             os.path.normpath(take.paths.shotlist))
            self.assertEqual(si["take"], 1)
            self.assertEqual(si["subfolder"], "renders_proxy")
            self.assertEqual(os.path.normpath(os.path.join(root, si["sidecar"])),
                             os.path.normpath(take.paths.sidecar))
            self.assertEqual(g["1"]["inputs"]["unet_name"], j.model)
            self.assertEqual(g["2"]["inputs"]["lora_name"], "proxy_turbo_4step.safetensors")
            self.assertFalse([v for v in g.values() if v["class_type"] == "SaveVideo"])

    def test_graph_leaves_lora_alone_when_none_named(self):
        with tempfile.TemporaryDirectory() as root:
            build_episode(root)
            doc = J.load_shotlist(root, "final")
            doc["defaults"]["lora"] = ""
            j = J.plan_job(root, "final", doc, 0, J.RenderRequest("sh010"), {})
            self.assertIsNone(j.loras)
            take = T.Take("sh010", 1, "final", T.take_paths(root, "final", "sh010", 1))
            g = J.graph_for(self.base, j, take)
            self.assertEqual(g["2"]["inputs"], self.base["2"]["inputs"])


# ---------------------------------------------------------------------------
# h3render end to end
# ---------------------------------------------------------------------------

def png_bytes(w: int, h: int, rgb=(128, 128, 128)) -> bytes:
    """A flat-colour RGB PNG, stdlib only."""
    import struct
    import zlib

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))
    row = b"\x00" + bytes(rgb) * w
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(row * h, 6)) + chunk(b"IEND", b""))


class FakeComfy:
    """Just enough of ComfyUI's API. Each /prompt runs 'instantly' and does what
    the save node does, according to `mode`: 'node' (writes mp4 and closes the
    sidecar), 'oldnode' (writes the mp4 only), 'error' (execution error),
    'hold' (accepted, and left pending in the queue) or 'reject' (node_errors).
    `running`/`pending` are what /queue reports; other POSTs land in `posts`.

    A reference-image graph (h3refs) is played too: H3SaveRefTake writes a
    flat PNG (its colour from the seed) beside the sidecar and closes it; a
    graph that kept SaveImage gets its PNG in the job's outputs, served by
    /view. `nodes` is what /object_info knows (drop "H3SaveRefTake" to play a
    ComfyUI whose node pack predates it)."""

    def __init__(self):
        self.mode = "node"
        self.nodes = {"H3SaveRefTake", "H3SaveShot", "H3ShotListLoader", "SaveImage"}
        self.info: dict[str, dict] = {}           # /object_info/<class> in full, when set
        self.predicted_frames = 199               # what a duration predictor "chooses"
        self.files: dict[str, bytes] = {}         # /view filename -> bytes
        self.userdata: dict[str, dict] = {}       # "workflows/x.json" -> saved workflow
        self.graphs: list[dict] = []
        self.history: dict[str, dict] = {}
        self.running: list[str] = []
        self.pending: list[str] = []
        self.posts: list[tuple[str, dict]] = []
        self.uploads: dict[str, bytes] = {}       # /upload/image: "sub/name" -> bytes
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, obj):
                body = json.dumps(obj).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path.startswith("/history/"):
                    pid = self.path.rsplit("/", 1)[1]
                    self._send({pid: fake.history[pid]} if pid in fake.history else {})
                elif self.path.startswith("/api/userdata/"):
                    from urllib.parse import unquote
                    key = unquote(self.path[len("/api/userdata/"):])
                    if key in fake.userdata:
                        self._send(fake.userdata[key])
                    else:
                        self.send_response(404)
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                elif self.path.startswith("/object_info/"):
                    from urllib.parse import unquote
                    ct = unquote(self.path[len("/object_info/"):])
                    self._send({ct: fake.info.get(ct, {"input": {}})}
                               if ct in fake.nodes or ct in fake.info else {})
                elif self.path.startswith("/view?"):
                    from urllib.parse import parse_qs, urlparse
                    name = parse_qs(urlparse(self.path).query)["filename"][0]
                    body = fake.files.get(name)
                    self.send_response(200 if body is not None else 404)
                    self.send_header("Content-Length", str(len(body or b"")))
                    self.end_headers()
                    self.wfile.write(body or b"")
                elif self.path == "/queue":
                    self._send({"queue_running": [[0, p] for p in fake.running],
                                "queue_pending": [[1, p] for p in fake.pending]})
                else:
                    self._send({})

            def do_POST(self):
                raw = self.rfile.read(int(self.headers["Content-Length"]))
                if self.path == "/upload/image":
                    # multipart: keep the fields and the file's bytes
                    import email.parser
                    import email.policy
                    msg = email.parser.BytesParser(policy=email.policy.default).parsebytes(
                        b"Content-Type: " + self.headers["Content-Type"].encode() + b"\r\n\r\n"
                        + raw)
                    form = {p.get_param("name", header="content-disposition"):
                            (p.get_filename(), p.get_payload(decode=True))
                            for p in msg.iter_parts()}
                    name = form["image"][0]
                    sub = form["subfolder"][1].decode()
                    fake.uploads[f"{sub}/{name}"] = form["image"][1]
                    self._send({"name": name, "subfolder": sub, "type": "input"})
                    return
                data = json.loads(raw)
                if self.path == "/prompt" and fake.mode == "reject":
                    self._send({"error": "invalid prompt",
                                "node_errors": {"11": {"errors": ["bad input"]}}})
                elif self.path == "/prompt":
                    self._send({"prompt_id": fake.run(data["prompt"])})
                else:
                    fake.posts.append((self.path, data))
                    self._send({})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def run(self, graph: dict) -> str:
        self.graphs.append(graph)
        pid = uuid.uuid4().hex
        if self.mode == "hold":
            self.pending.append(pid)
            return pid
        if self.mode == "error":
            self.history[pid] = {"status": {"status_str": "error", "completed": False,
                                            "messages": [["execution_error",
                                                          {"exception_message": "boom"}]]}}
            return pid
        if not any(v["class_type"] == J.SAVER for v in graph.values()):
            return self.run_image(graph, pid)
        si = next(v["inputs"] for v in graph.values() if v["class_type"] == J.SAVER)
        root = si["project_root"]
        loader = next((v["inputs"] for v in graph.values() if v["class_type"] == J.LOADER),
                      None)
        if loader is not None:
            shot = T.read_json(os.path.join(root, loader["shotlist_file"]))["shots"][0]
        else:
            # a loader-less target (ltx2): the saver names the shot, the latent its length
            lat = next(v["inputs"] for v in graph.values()
                       if v["class_type"] == "EmptyLTXVLatentVideo")
            shot = {"id": si["shot_id"], "length": lat["length"]}
            if isinstance(lat["length"], list):
                # linked to LTXVDurationPredictor (`dur: model`): the model's choice
                shot["length"] = self.predicted_frames
        stem = f"{T.safe_id(shot['id'])}_t{si['take']:02d}"
        d = os.path.join(root, si["subfolder"], T.safe_id(shot["id"]))
        open(os.path.join(d, stem + ".mp4"), "wb").close()
        if self.mode == "node":
            T.update_sidecar(os.path.join(root, si["sidecar"]), status="ok",
                             finished=T.now(), frames=shot["length"], mp4=stem + ".mp4",
                             thumb=None, strip=None, save_notes="fake")
        self.history[pid] = {"status": {"status_str": "success", "completed": True},
                             "outputs": {}}
        return pid

    def run_image(self, graph: dict, pid: str) -> str:
        """A reference-image job: what H3SaveRefTake (or SaveImage) does."""
        lat = next(v["inputs"] for v in graph.values()
                   if v["class_type"] in ("EmptyLatentImage", "EmptySD3LatentImage",
                                      "EmptyFlux2LatentImage"))
        seed = next(v["inputs"].get("seed", v["inputs"].get("noise_seed", 0))
                    for v in graph.values() if v["class_type"].startswith("KSampler"))
        png = png_bytes(lat["width"], lat["height"],
                        (seed % 256, (seed >> 8) % 256, (seed >> 16) % 256))
        outputs = {}
        for nid, v in graph.items():
            if v["class_type"] == "H3SaveRefTake":
                sc = v["inputs"]["sidecar"]
                data = T.read_json(sc) or {}
                name = data.get("image") or os.path.basename(sc)[:-5] + ".png"
                with open(os.path.join(os.path.dirname(sc), name), "wb") as fh:
                    fh.write(png)
                T.update_sidecar(sc, status="ok", finished=T.now(), image=name,
                                 width=lat["width"], height=lat["height"], save_notes="fake")
            elif v["class_type"] == "SaveImage":
                name = f"{v['inputs']['filename_prefix'].replace('/', '_')}_{pid[:8]}.png"
                self.files[name] = png
                outputs[nid] = {"images": [{"filename": name, "subfolder": "",
                                            "type": "output"}]}
        self.history[pid] = {"status": {"status_str": "success", "completed": True},
                             "outputs": outputs}
        return pid

    def close(self):
        self.server.shutdown()


class WorkflowLookupTest(unittest.TestCase):
    def test_order(self):
        comfy = FakeComfy()
        try:
            saved = J.load_graph(WORKFLOW)
            saved["2"]["inputs"]["lora_name"] = "from_comfy.safetensors"
            comfy.userdata["workflows/" + J.WORKFLOW_NAME] = saved   # an API graph is fine too
            g, where = J.resolve_workflow(None, J.WORKFLOW_NAME, comfy.url)
            self.assertEqual(g["2"]["inputs"]["lora_name"], "from_comfy.safetensors")
            self.assertIn("user workflows", where)
            # explicit beats ComfyUI
            g, where = J.resolve_workflow(WORKFLOW, J.WORKFLOW_NAME, comfy.url)
            self.assertEqual(where, WORKFLOW)
            # not saved in ComfyUI, or no ComfyUI at all: the repo copy
            comfy.userdata.clear()
            for url in (comfy.url, "http://127.0.0.1:9", None):
                g, where = J.resolve_workflow(None, J.WORKFLOW_NAME, url)
                self.assertEqual(os.path.normcase(where), os.path.normcase(WORKFLOW))
            self.assertEqual(J.resolve_workflow(None, "nope.json", None, required=False),
                             (None, ""))
            with self.assertRaises(FileNotFoundError):
                J.resolve_workflow(None, "nope.json", None)
        finally:
            comfy.close()

    def test_prefer_repo_beats_comfyui_path(self):
        with tempfile.TemporaryDirectory() as comfy_root:
            d = os.path.join(comfy_root, "user", "default", "workflows")
            os.makedirs(d)
            with open(os.path.join(d, J.WORKFLOW_NAME), "w", encoding="utf-8") as fh:
                fh.write(open(WORKFLOW, encoding="utf-8").read())
            old = os.environ.get("COMFYUI_PATH")
            os.environ["COMFYUI_PATH"] = comfy_root
            try:
                _, where = J.resolve_workflow(None, J.WORKFLOW_NAME)
                self.assertTrue(where.startswith(comfy_root), where)
                _, where = J.resolve_workflow(None, J.WORKFLOW_NAME, prefer_repo=True)
                self.assertFalse(where.startswith(comfy_root), where)
            finally:
                if old is None:
                    del os.environ["COMFYUI_PATH"]
                else:
                    os.environ["COMFYUI_PATH"] = old


class RenderCliTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        build_episode(self.root)
        self.comfy = FakeComfy()

    def tearDown(self):
        self.comfy.close()
        self._tmp.cleanup()

    def render(self, *args) -> subprocess.CompletedProcess:
        r = subprocess.run([sys.executable, os.path.join(ROOT, "h3render.py"), self.root,
                            "--workflow", WORKFLOW, "--comfy", self.comfy.url, *args],
                           capture_output=True, env=ENV, cwd=self.root)
        return r

    def takes(self, sid, pass_="proxy"):
        return T.list_takes(self.root, pass_, sid)

    def test_full_pass_then_redo_then_override(self):
        r = self.render("--proxy")
        self.assertEqual(r.returncode, 0, r.stdout.decode() + r.stderr.decode())
        ids = shot_ids(self.root, "proxy")
        self.assertEqual(len(self.comfy.graphs), len(ids))
        doc = J.load_shotlist(self.root, "proxy")
        for s in doc["shots"]:
            (t,) = self.takes(s["id"])
            self.assertTrue(t.usable)
            self.assertEqual((t.sidecar["seed"], t.sidecar["seed_source"]), (s["seed"], "stable"))
            self.assertTrue(os.path.isfile(t.paths.shotlist))

        # nothing left to do
        self.assertIn(b"nothing to do", self.render("--proxy").stdout)

        # redo one shot: new take, new seed, frozen shotlist carries it
        r = self.render("--proxy", "--only", "sh020", "--redo", "--note", "try again")
        self.assertEqual(r.returncode, 0, r.stdout.decode())
        t1, t2 = self.takes("sh020")
        self.assertEqual(t2.take, 2)
        self.assertEqual(t2.sidecar["seed_source"], "new")
        self.assertEqual(t2.sidecar["note"], "try again")
        self.assertEqual(T.read_json(t2.paths.shotlist)["shots"][0]["seed"], t2.sidecar["seed"])
        self.assertNotEqual(t2.sidecar["seed"], t1.sidecar["seed"])

        # an override (prompt + LoRA stack) applies on the next redo
        ov = T.set_override({}, "sh020", "proxy", prompt="hand-tuned prompt")
        T.set_override(ov, "sh020", "proxy", loras=[{"name": "a", "strength": 1.0},
                                                    {"name": "b", "strength": 0.4}])
        T.save_overrides(self.root, ov)
        r = self.render("--proxy", "--only", "sh020", "--redo", "--same-seed")
        self.assertEqual(r.returncode, 0, r.stdout.decode())
        t3 = self.takes("sh020")[-1]
        self.assertEqual(T.read_json(t3.paths.shotlist)["shots"][0]["prompt"], "hand-tuned prompt")
        self.assertEqual(t3.sidecar["overrides"], ["loras", "prompt"])
        self.assertEqual(t3.sidecar["seed_source"], "same")
        g = self.comfy.graphs[-1]
        self.assertEqual(sorted(v["inputs"]["lora_name"] for v in g.values()
                                if v["class_type"] == J.LORA), ["a", "b"])

    def test_old_save_node_is_closed_by_h3render(self):
        self.comfy.mode = "oldnode"
        r = self.render("--proxy", "--only", "sh010")
        self.assertEqual(r.returncode, 0, r.stdout.decode())
        (t,) = self.takes("sh010")
        self.assertEqual(t.status, "ok")
        self.assertIn("h3jobs", t.sidecar["save_notes"])

    def test_error_marks_take_failed_and_retry_reuses_built_seed(self):
        self.comfy.mode = "error"
        r = self.render("--proxy", "--only", "sh010")
        self.assertEqual(r.returncode, 1)
        (t,) = self.takes("sh010")
        self.assertEqual(t.status, "failed")
        self.assertIn("boom", t.sidecar["save_notes"])
        self.comfy.mode = "node"
        r = self.render("--proxy", "--only", "sh010")
        self.assertEqual(r.returncode, 0, r.stdout.decode())
        t1, t2 = self.takes("sh010")
        self.assertEqual((t2.take, t2.status, t2.sidecar["seed_source"]), (2, "ok", "stable"))

    def test_sweep_marks_abandoned_take_failed(self):
        T.reserve_take(self.root, "proxy", "sh010",
                       {"status": "queued", "comfy_prompt_id": "gone",
                        "queued": "2026-01-01T00:00:00+00:00"})
        r = self.render("--proxy", "--only", "sh010", "--list")
        self.assertIn(b"marked 1 take(s) failed", r.stdout)
        self.assertEqual(self.takes("sh010")[0].status, "failed")

    def test_list_and_dry_run_write_nothing(self):
        self.assertEqual(self.render("--proxy", "--list").returncode, 0)
        r = self.render("--proxy", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stdout.decode())
        self.assertFalse(os.path.isdir(os.path.join(self.root, "renders_proxy")))
        self.assertEqual(self.comfy.graphs, [])
        self.assertTrue(os.path.isfile(os.path.join(self.root, "h3render_graph.json")))


if __name__ == "__main__":
    unittest.main()
