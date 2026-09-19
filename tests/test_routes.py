"""
Smoke test for comfy_nodes/h3pipe_routes.py, the aiohttp adapter: it imports
against a stub `server.PromptServer` and `folder_paths`, and a few requests go
through a real aiohttp app (JSON in and out, bad bodies, the file route's
Range support and headers). Needs aiohttp, so the system Python skips it; run
it with ComfyUI's embedded Python:

    C:\\AI\\ComfyUI\\python_embeded\\python.exe tests\\test_routes.py -v
"""
from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

try:
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
except ImportError as exc:                               # pragma: no cover
    raise unittest.SkipTest(f"the routes need aiohttp: {exc}")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "comfy_nodes"))


class RoutesTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = self._tmp.name
        self.shows = os.path.join(tmp, "Shows")
        self.ep = os.path.join(self.shows, "ep01")
        os.makedirs(self.ep)
        with open(os.path.join(self.ep, "clip.mp4"), "wb") as fh:
            fh.write(bytes(range(256)) * 4)
        self.sent = []
        server = type(sys)("server")
        server.PromptServer = type("PromptServer", (), {})
        server.PromptServer.instance = mock.Mock(
            routes=web.RouteTableDef(), port=None,
            send_sync=lambda e, d: self.sent.append((e, d)))
        fp = type(sys)("folder_paths")
        fp.get_user_directory = lambda: os.path.join(tmp, "user")
        self.modules = mock.patch.dict(sys.modules, {"server": server, "folder_paths": fp})
        self.modules.start()
        sys.modules.pop("h3pipe_routes", None)
        import h3pipe_routes
        self.R = h3pipe_routes
        self.routes = server.PromptServer.instance.routes

    def tearDown(self):
        self.modules.stop()
        sys.modules.pop("h3pipe_routes", None)
        self._tmp.cleanup()

    def run_client(self, fn):
        async def main():
            app = web.Application()
            app.add_routes(self.routes)
            async with TestClient(TestServer(app)) as client:
                await fn(client)
        asyncio.run(main())

    def test_registered(self):
        got = {(r.method, r.path) for r in self.routes}
        self.assertEqual(got, {(m, p) for m, p, _, _ in self.R.A.ROUTES})
        self.assertEqual(self.R.base_url(mock.Mock(port=None)), "http://127.0.0.1:8188")
        self.assertEqual(self.R.base_url(mock.Mock(address="0.0.0.0", port=8190)),
                         "http://127.0.0.1:8190")
        self.assertEqual(self.R.base_url(mock.Mock(address="::1", port=8188)),
                         "http://[::1]:8188")

    def test_requests(self):
        async def fn(c):
            r = await c.put("/h3pipe/config", json={"roots": [self.shows]})
            self.assertEqual(r.status, 200, await r.text())
            self.assertEqual((await r.json())["roots"], [self.shows])
            self.assertTrue(os.path.isfile(os.path.join(self._tmp.name, "user", "default",
                                                        "h3pipe", "config.json")))
            r = await c.get("/h3pipe/config")
            self.assertEqual((await r.json())["comfy"], "http://127.0.0.1:8188")

            r = await c.put("/h3pipe/config", data=b"{nope",
                            headers={"Content-Type": "application/json"})
            self.assertEqual(r.status, 400)
            self.assertIn("JSON", (await r.json())["error"])

            r = await c.get("/h3pipe/episode", params={"ep": self._tmp.name})
            self.assertEqual(r.status, 403)
            r = await c.get("/h3pipe/episode", params={"ep": self.ep})
            self.assertEqual(r.status, 404)                # not built
            self.assertIn("error", await r.json())

            q = {"ep": self.ep, "path": "clip.mp4"}
            r = await c.get("/h3pipe/file", params=q)
            self.assertEqual(r.status, 200)
            self.assertEqual(r.headers["Content-Type"], "video/mp4")
            self.assertEqual(r.headers["Cache-Control"], "no-cache")
            self.assertEqual(len(await r.read()), 1024)
            r = await c.get("/h3pipe/file", params=q, headers={"Range": "bytes=256-511"})
            self.assertEqual(r.status, 206)
            self.assertEqual(await r.read(), bytes(range(256)))
            r = await c.get("/h3pipe/file", params={"ep": self.ep, "path": "../x"})
            self.assertEqual(r.status, 400)
            self.assertIn("error", await r.json())

            r = await c.get("/h3pipe/episodes")
            self.assertEqual(await r.json(), [])
        self.run_client(fn)

    def test_multipart_import(self):
        """POST /h3pipe/refs/import as multipart/form-data (Phase 8.6): the file
        is streamed to a temporary file, imported, and the temporary file goes."""
        from aiohttp import FormData
        from test_render import FIXTURE, png_bytes
        shutil.copy(os.path.join(FIXTURE, "series.json"), self.ep)
        uploads = []
        real_mkstemp = tempfile.mkstemp

        def mkstemp(*a, **kw):
            fd, p = real_mkstemp(*a, **kw)
            if os.path.basename(p).startswith("h3pipe_upload_"):
                uploads.append(p)
            return fd, p

        def form(name, data, **fields):
            f = FormData()
            for k, v in dict({"ep": self.ep, "ref": "location:kitchen"}, **fields).items():
                f.add_field(k, v)
            f.add_field("file", data, filename=name, content_type="application/octet-stream")
            return f

        async def fn(c):
            r = await c.put("/h3pipe/config", json={"roots": [self.shows]})
            self.assertEqual(r.status, 200)
            with mock.patch.object(self.R.tempfile, "mkstemp", mkstemp):
                r = await c.post("/h3pipe/refs/import",
                                 data=form("Kitchen.png", png_bytes(8, 8), pick="1"))
                self.assertEqual(r.status, 200, await r.text())
                t = await r.json()
                self.assertEqual((t["take"], t["original_name"]), (1, "Kitchen.png"))
                self.assertTrue(os.path.isfile(os.path.join(self.ep, "refs", "_bg",
                                                            "kitchen.png")))
                r = await c.post("/h3pipe/refs/import", data=form("notes.txt", b"hello"))
                self.assertEqual(r.status, 400)
                with mock.patch.object(self.R.A, "MAX_UPLOAD", 100):
                    r = await c.post("/h3pipe/refs/import",
                                     data=form("big.png", png_bytes(64, 64, (1, 2, 3))))
                    self.assertEqual(r.status, 413)
            # the JSON form still works on the same route
            r = await c.post("/h3pipe/refs/import", json={"ep": self.ep, "ref": "location:kitchen",
                                                          "source_path": "rel.png"})
            self.assertEqual(r.status, 400)
            self.assertIn("absolute", (await r.json())["error"])
        self.run_client(fn)
        self.assertEqual(len(uploads), 3)
        self.assertEqual([p for p in uploads if os.path.exists(p)], [])

    def test_source_and_promote(self):
        """Phase 9a through aiohttp: the source routes' JSON, a 409 with the
        file's hash and text, a JSON refusal with line/col, the promote plan
        and its 409 (test_phase9a has the rest)."""
        from test_render import FIXTURE
        shutil.copy(os.path.join(FIXTURE, "series.json"), self.ep)
        shutil.copy(os.path.join(FIXTURE, "script.md"), os.path.join(self.ep, "ep01.md"))

        async def fn(c):
            r = await c.put("/h3pipe/config", json={"roots": [self.shows]})
            self.assertEqual(r.status, 200)
            r = await c.get("/h3pipe/source", params={"ep": self.ep, "file": "script"})
            self.assertEqual(r.status, 200, await r.text())
            src = await r.json()
            self.assertEqual(src["path"], "ep01.md")
            self.assertEqual(src["shots"][0]["id"], "sh010")
            r = await c.get("/h3pipe/source", params={"ep": self.ep, "file": "script",
                                                      "hash_only": "1"})
            self.assertEqual(set(await r.json()), {"file", "hash", "mtime"})
            r = await c.post("/h3pipe/source/check", json={
                "ep": self.ep, "file": "script", "text": src["text"].replace("size: ws",
                                                                             "size: huge")})
            chk = await r.json()
            self.assertEqual((chk["ok"], chk["errors"][0]["line"]), (False, 12))
            r = await c.put("/h3pipe/source", json={"ep": self.ep, "file": "script",
                                                    "text": "x", "base_hash": "stale",
                                                    "rebuild": False})
            self.assertEqual(r.status, 409)
            body = await r.json()
            self.assertEqual((body["error"], body["hash"], body["text"]),
                             ("changed on disk", src["hash"], src["text"]))
            s = await (await c.get("/h3pipe/source",
                                   params={"ep": self.ep, "file": "series"})).json()
            r = await c.put("/h3pipe/source", json={"ep": self.ep, "file": "series",
                                                    "text": s["text"] + "}",
                                                    "base_hash": s["hash"], "rebuild": False})
            self.assertEqual(r.status, 400)
            self.assertIn("line", await r.json())
            r = await c.put("/h3pipe/source", json={"ep": self.ep, "file": "script",
                                                    "text": src["text"] + "\n",
                                                    "base_hash": src["hash"], "rebuild": False})
            self.assertEqual(r.status, 200, await r.text())
            saved = await r.json()
            self.assertIsNone(saved["build"])
            self.assertTrue(saved["check"]["ok"])
            r = await c.get("/h3pipe/promote", params={"ep": self.ep})
            self.assertEqual(r.status, 200, await r.text())
            plan = await r.json()
            self.assertEqual((plan["items"], plan["hashes"]["script"]), ([], saved["hash"]))
            r = await c.post("/h3pipe/promote", json={"ep": self.ep, "items": "all",
                                                      "hashes": {"script": src["hash"],
                                                                 "series": s["hash"]}})
            self.assertEqual(r.status, 409)
            self.assertEqual((await r.json())["file"], "script")
        self.run_client(fn)
        self.assertIn(("h3pipe.episode", {"ep": self.ep}), self.sent)

    def test_cut_edits_and_peaks(self):
        """Phase 9b through aiohttp: GET /h3pipe/peaks (a stdlib wav, the cache,
        a path that climbs out) and the cut routes' JSON and statuses
        (test_phase9b has the rest)."""
        from test_phase9b import steps_wav
        steps_wav(os.path.join(self.ep, "audio", "mix.wav"), [1000, 32767])

        async def fn(c):
            r = await c.put("/h3pipe/config", json={"roots": [self.shows]})
            self.assertEqual(r.status, 200)
            q = {"ep": self.ep, "path": "audio/mix.wav", "bins": "2"}
            for _ in range(2):                             # computed, then cached
                r = await c.get("/h3pipe/peaks", params=q)
                self.assertEqual(r.status, 200, await r.text())
                self.assertEqual(await r.json(), {"duration": 1.0, "bins": 2, "start": 0.0,
                                                  "end": 1.0, "peaks": [8, 255]})
            self.assertEqual(len(os.listdir(os.path.join(self.ep, "_cache", "peaks"))), 1)
            r = await c.get("/h3pipe/peaks", params=dict(q, path="../x.wav"))
            self.assertEqual(r.status, 400)
            r = await c.get("/h3pipe/peaks", params=dict(q, bins="many"))
            self.assertEqual(r.status, 400)
            r = await c.get("/h3pipe/peaks", params=dict(q, path="nope.wav"))
            self.assertEqual(r.status, 404)
            r = await c.post("/h3pipe/cut/reset", json={"ep": self.ep, "pass": "proxy",
                                                        "what": "order"})
            self.assertEqual(r.status, 404)                # not built
            r = await c.post("/h3pipe/cut/copy", json={"ep": self.ep, "from": "final",
                                                       "to": "final", "what": "all"})
            self.assertEqual(r.status, 400)
            self.assertIn("error", await r.json())
        self.run_client(fn)

    def test_models_through_folder_paths(self):
        """GET /h3pipe/models inside ComfyUI: the list and the paths come from
        folder_paths, the fingerprints are cached in the user folder."""
        from test_modelid import ltx_tensors, wan14_tensors, write_st
        models = os.path.join(self._tmp.name, "models", "diffusion_models")
        write_st(os.path.join(models, "renamed.safetensors"), ltx_tensors("2.5"))
        write_st(os.path.join(models, "wan_low.safetensors"), wan14_tensors())
        fp = sys.modules["folder_paths"]
        fp.get_filename_list = lambda folder: (sorted(os.listdir(models))
                                               if folder == "diffusion_models" else [])
        fp.get_full_path = lambda folder, name: (
            os.path.join(models, name) if folder == "diffusion_models"
            and os.path.isfile(os.path.join(models, name)) else None)

        async def fn(c):
            r = await c.get("/h3pipe/models", params={"target": "ltx2", "param": "model"})
            self.assertEqual(r.status, 200, await r.text())
            data = await r.json()
            self.assertEqual([(f["name"], f["match"], f["mismatch"]) for f in data["files"]],
                             [("renamed.safetensors", "fingerprint", False),
                              ("wan_low.safetensors", "other", True)])
            r = await c.get("/h3pipe/models", params={"target": "ltx2", "param": "loras"})
            self.assertEqual(r.status, 400)
        self.run_client(fn)
        self.assertTrue(os.path.isfile(os.path.join(self._tmp.name, "user", "default", "h3pipe",
                                                    "modelid_cache.json")))

    def test_no_pipeline_no_routes(self):
        server = sys.modules["server"]
        server.PromptServer.instance.routes = web.RouteTableDef()
        self.R.REGISTERED = False
        with mock.patch.object(self.R.A, "IMPORT_ERROR", "h3pipe: can't import"), \
                self.assertLogs("h3pipe", "WARNING"):
            self.assertFalse(self.R.register(server.PromptServer.instance))
        self.assertEqual(list(server.PromptServer.instance.routes), [])


if __name__ == "__main__":
    unittest.main()
