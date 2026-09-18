"""
h3pipe_routes — registers the editor API (docs/API.md) on ComfyUI's server.

A thin aiohttp adapter over h3pipe_api: parse the query or JSON body, run the
handler off the event loop, answer JSON (or stream a file for /h3pipe/file).
Events go out through PromptServer.send_sync, which is safe from a worker
thread. Imported by the package __init__ inside try/except, so nothing here can
stop the nodes loading.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os

from aiohttp import web

try:
    from . import h3pipe_api as A
except ImportError:                                       # loaded as a plain module
    import h3pipe_api as A

log = logging.getLogger("h3pipe")
REGISTERED = False


def base_url(server) -> str:
    """This ComfyUI's own address, for queueing on it and reading its workflows."""
    port = getattr(server, "port", None)
    if not port:
        return A.DEFAULT_COMFY
    addr = str(getattr(server, "address", "") or "")
    if addr in ("", "0.0.0.0", "::", "[::]"):
        addr = "127.0.0.1"
    if ":" in addr and not addr.startswith("["):
        addr = f"[{addr}]"
    return f"http://{addr}:{port}"


def user_dir() -> str:
    try:
        import folder_paths
        return folder_paths.get_user_directory()
    except Exception:
        return os.path.join(os.getcwd(), "user")


def make_context(server) -> "A.Context":
    def emit(event, data):
        server.send_sync(event, data)
    return A.Context(user_dir(), base_url(server), emit)


def _json(status: int, data) -> web.Response:
    return web.json_response(data, status=status,
                             dumps=lambda o: json.dumps(o, ensure_ascii=False))


def make_view(server, fn, takes: str, is_file: bool = False):
    async def view(request: web.Request) -> web.StreamResponse:
        if takes == "body":
            try:
                arg = await request.json()
            except (ValueError, UnicodeDecodeError):
                return _json(400, {"error": "the request body must be JSON"})
        else:
            arg = dict(request.query)
        ctx = make_context(server)
        loop = asyncio.get_running_loop()
        try:
            status, data = await loop.run_in_executor(None, fn, ctx, arg)
        except Exception as e:                            # handler() already catches; belt
            log.exception("h3pipe: %s failed", request.path)
            return _json(500, {"error": f"{e.__class__.__name__}: {e}"})
        if is_file and status == 200:
            return web.FileResponse(data["path"], headers={
                "Cache-Control": "no-cache", "Content-Type": data["content_type"]})
        return _json(status, data)
    view.__name__ = f"h3pipe_{fn.__name__}"
    return view


def register(server) -> bool:
    """Add every route to `server.routes`. False (and one log line) if the
    pipeline can't be imported."""
    global REGISTERED
    if REGISTERED:
        return True
    if A.IMPORT_ERROR:
        log.warning(A.IMPORT_ERROR)
        return False
    for method, path, fn, takes in A.ROUTES:
        server.routes.route(method, path)(make_view(server, fn, takes,
                                                    is_file=fn is A.get_file))
    REGISTERED = True
    return True


def register_on_prompt_server() -> bool:
    from server import PromptServer
    return register(PromptServer.instance)


register_on_prompt_server()
