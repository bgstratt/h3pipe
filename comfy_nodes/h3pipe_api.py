"""
h3pipe_api — the editor's HTTP routes (docs/API.md) as plain functions.

No aiohttp and no ComfyUI in here, so it runs (and is tested) with any Python:
every handler takes a Context plus the query dict and/or the JSON body and
returns (status, json-able value). h3pipe_routes.py is the aiohttp adapter that
ComfyUI loads. The work itself is the pipeline's (h3edit, h3jobs, h3refs, h3takes);
this layer checks input, keeps episodes inside the configured roots, turns
seeds into strings and back, and sends the live-update events.

The pipeline modules are found in $H3PIPE_HOME, else in the folder above this
one (comfy_nodes/ lives in the repo; ComfyUI loads it through a junction, so
the real path is used). If they can't be imported, IMPORT_ERROR says why and
the routes are not registered.
"""
from __future__ import annotations

import json
import os
import re
import sys

# ---------------------------------------------------------------------------
# the pipeline
# ---------------------------------------------------------------------------


def pipeline_home() -> str:
    env = os.environ.get("H3PIPE_HOME", "").strip()
    if env:
        return os.path.abspath(env)
    return os.path.dirname(os.path.dirname(os.path.realpath(__file__)))


HOME = pipeline_home()
IMPORT_ERROR: str | None = None
try:
    if HOME not in sys.path:
        # appended, not prepended: nothing in the repo may shadow ComfyUI's modules
        sys.path.append(HOME)
    import importlib.util
    # `targets` is a generic name: make sure it is the repo's package, not
    # something else on ComfyUI's path (HOME is appended, so it comes last).
    _spec = importlib.util.find_spec("targets")
    if _spec is None or not os.path.normcase(os.path.realpath(_spec.origin or "")).startswith(
            os.path.normcase(os.path.realpath(os.path.join(HOME, "targets")))):
        raise ImportError(f"another module called 'targets' shadows the pipeline's "
                          f"({getattr(_spec, 'origin', None)})")
    import h3edit as E  # noqa: E402
    import h3jobs as J  # noqa: E402
    import h3peaks as PK  # noqa: E402
    import h3promote as P  # noqa: E402
    import h3refs as R  # noqa: E402
    import h3takes as T  # noqa: E402
    import h3track as K  # noqa: E402
    import targets as TG  # noqa: E402
except Exception as exc:                                  # pragma: no cover
    E = J = K = P = PK = R = T = TG = None
    IMPORT_ERROR = (f"h3pipe: can't import the pipeline from {HOME} "
                    f"({exc.__class__.__name__}: {exc}); set H3PIPE_HOME to the repo")

DEFAULT_COMFY = "http://127.0.0.1:8188"
CONFIG_VERSION = 1
PASSES = ("final", "proxy")
DEFAULT_PASS = "proxy"
CONTENT_TYPES = {
    ".mp4": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime",
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".webp": "image/webp", ".wav": "audio/wav", ".mp3": "audio/mpeg",
    ".flac": "audio/flac", ".json": "application/json; charset=utf-8",
    ".md": "text/markdown; charset=utf-8", ".txt": "text/plain; charset=utf-8",
}


_UNSET = object()


class ApiError(Exception):
    """An answer other than 200: {"error": message, **data}."""

    def __init__(self, status: int, message: str, **data):
        super().__init__(message)
        self.status = status
        self.data = data


class Context:
    """What a handler needs from its host.

    user_dir   ComfyUI's user folder (config lives in <user_dir>/default/h3pipe/)
    comfy_url  this ComfyUI's own base URL
    emit       emit(event, data): a live-update event to every open editor
    comfy      an h3jobs.Comfy-like client (default: one for comfy_url, with
               client_id "h3pipe"); tests pass a fake
    env        environment for $H3PIPE_ROOTS (default os.environ)
    model_resolve  resolve(folder, name) -> a model file's path or None
               (default h3jobs.model_resolver(): ComfyUI's folder_paths);
               None checks model files by name only
    model_list list(folder, class_type, field) -> the files ComfyUI offers
               (default: folder_paths.get_filename_list, else /object_info)
    """

    def __init__(self, user_dir: str, comfy_url: str = DEFAULT_COMFY, emit=None,
                 comfy=None, env: dict | None = None, model_resolve=_UNSET, model_list=None):
        self.user_dir = user_dir
        self.comfy_url = (comfy_url or DEFAULT_COMFY).rstrip("/")
        self._emit = emit
        self._comfy = comfy
        self.env = os.environ if env is None else env
        if model_resolve is _UNSET:
            model_resolve = J.model_resolver() if J is not None else None
        self.model_resolve = model_resolve
        self._model_list = model_list
        self._model_cache = None

    @property
    def model_cache(self):
        """identify() results, in <user_dir>/default/h3pipe/modelid_cache.json."""
        if self._model_cache is None:
            self._model_cache = TG.modelid.user_cache(self.user_dir)
        return self._model_cache

    def model_list(self, folder: str | None, class_type: str | None, field: str | None) -> list:
        if self._model_list is not None:
            return list(self._model_list(folder, class_type, field) or [])
        if folder:
            try:
                import folder_paths                       # only inside ComfyUI
                return list(folder_paths.get_filename_list(folder))
            except Exception:
                pass
        if class_type and field:
            return list(self.comfy.choices(class_type, field) or [])
        return []

    def model_choices(self, folder: str | None, class_type: str | None,
                      field: str | None) -> list | None:
        """model_list, except that None means "can't tell" (no folder_paths
        and no answer from /object_info for that loader), so queue-time
        resolution leaves the param alone instead of calling it missing."""
        if self._model_list is not None:
            got = self._model_list(folder, class_type, field)
            return None if got is None else list(got)
        if folder:
            try:
                import folder_paths                       # only inside ComfyUI
                return list(folder_paths.get_filename_list(folder))
            except Exception:
                pass
        if class_type and field:
            try:
                got = self.comfy.choices(class_type, field, strict=True)
            except Exception:
                return None
            return None if got is None else list(got)
        return None

    @property
    def comfy(self):
        if self._comfy is None:
            self._comfy = J.Comfy(self.comfy_url, client_id="h3pipe")
        return self._comfy

    def emit(self, event: str, data: dict) -> None:
        if self._emit is not None:
            try:
                self._emit(event, data)
            except Exception:
                pass


def handler(fn):
    """Turn ApiError and stray exceptions into (status, {"error": ...})."""
    def wrapped(*args, **kw):
        try:
            return fn(*args, **kw)
        except ApiError as e:
            return e.status, dict({"error": str(e)}, **e.data)
        except FileNotFoundError as e:
            return 404, {"error": str(e)}
        except Exception as e:                            # the message is for a person
            if P is not None and isinstance(e, P.H.SourceError):
                return e.status, dict({"error": str(e)}, **e.data)
            return 500, {"error": f"{e.__class__.__name__}: {e}"}
    wrapped.__name__ = fn.__name__
    wrapped.__doc__ = fn.__doc__
    return wrapped


# ---------------------------------------------------------------------------
# seeds: strings on the wire (63-bit seeds don't survive a JS number)
# ---------------------------------------------------------------------------

def seeds_out(obj):
    """A copy of `obj` with the value of every "seed" key as a string."""
    if isinstance(obj, dict):
        return {k: (str(v) if k == "seed" and isinstance(v, int) and not isinstance(v, bool)
                    else seeds_out(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [seeds_out(v) for v in obj]
    return obj


def seed_in(v, what: str = "seed") -> int | None:
    """A seed from a request: a string of digits (or, tolerated, an integer)."""
    if v is None:
        return None
    if isinstance(v, bool):
        raise ApiError(400, f"{what} must be a string of digits")
    if isinstance(v, int):
        n = v
    elif isinstance(v, str) and re.fullmatch(r"\s*\d+\s*", v):
        n = int(v)
    else:
        raise ApiError(400, f"{what} must be a string of digits, not {v!r}")
    if n < 0 or n >= 2 ** 64:
        raise ApiError(400, f"{what} must be between 0 and 2^64-1")
    return n


# ---------------------------------------------------------------------------
# config and roots
# ---------------------------------------------------------------------------

def config_path(ctx: Context) -> str:
    return os.path.join(ctx.user_dir, "default", "h3pipe", "config.json")


def load_roots(ctx: Context) -> list[str]:
    p = config_path(ctx)
    if os.path.isfile(p):
        try:
            with open(p, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as e:
            raise ApiError(500, f"{p} is unreadable: {e}")
        roots = data.get("roots") if isinstance(data, dict) else None
        return [r for r in (roots or []) if isinstance(r, str) and r]
    env = ctx.env.get("H3PIPE_ROOTS", "")
    return [r for r in env.split(os.pathsep) if r.strip()]


def config_json(ctx: Context) -> dict:
    return {"roots": load_roots(ctx), "comfy": ctx.comfy_url, "version": CONFIG_VERSION}


def _real(p: str) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(p)))


def _inside(path: str, root: str) -> bool:
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:                                    # another drive
        return False


def check_ep(ctx: Context, ep) -> str:
    """The episode folder `ep` (absolute, inside a configured root), or ApiError."""
    if not ep or not isinstance(ep, str):
        raise ApiError(400, "ep is required: the episode's absolute folder path")
    if not os.path.isabs(ep):
        raise ApiError(400, f"ep must be an absolute path, not {ep!r}")
    real = _real(ep)
    if not any(_inside(real, _real(r)) for r in load_roots(ctx)):
        raise ApiError(403, f"{ep} is not inside a configured root "
                            "(set them with PUT /h3pipe/config)")
    if not os.path.isdir(ep):
        raise ApiError(404, f"no episode folder at {ep}")
    return os.path.abspath(ep)


def check_pass(v, default: str = DEFAULT_PASS) -> str:
    if v is None or v == "":
        return default
    if v not in PASSES:
        raise ApiError(400, f"pass must be 'final' or 'proxy', not {v!r}")
    return v


def check_shot(v) -> str:
    if not v or not isinstance(v, str):
        raise ApiError(400, "shot is required")
    return v


def check_take(v, what: str = "take", nullable: bool = False) -> int | None:
    if v is None and nullable:
        return None
    if isinstance(v, bool) or not isinstance(v, int) or v < 1:
        if isinstance(v, str) and v.strip().isdigit() and int(v) >= 1:
            return int(v)
        raise ApiError(400, f"{what} must be a take number (1 or more)")
    return v


def body_dict(body) -> dict:
    if not isinstance(body, dict):
        raise ApiError(400, "the request body must be a JSON object")
    return body


def take_event(ctx: Context, ep: str, t, status: str | None = None) -> None:
    ctx.emit("h3pipe.take", {"ep": ep, "pass": t.pass_, "shot": t.shot, "take": t.take,
                             "status": status or t.status,
                             "thumb": E.rel(ep, t.paths.thumb)})


def episode_event(ctx: Context, ep: str) -> None:
    ctx.emit("h3pipe.episode", {"ep": ep})


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------

@handler
def get_config(ctx: Context, query: dict):
    return 200, config_json(ctx)


@handler
def put_config(ctx: Context, body):
    body = body_dict(body)
    roots = body.get("roots")
    if not isinstance(roots, list) or not all(isinstance(r, str) and r.strip() for r in roots):
        raise ApiError(400, "roots must be a list of folder paths")
    missing = [r for r in roots if not os.path.isdir(r)]
    if missing:
        raise ApiError(400, "no such folder: " + ", ".join(missing))
    clean = []
    for r in roots:
        a = os.path.abspath(r)
        if os.path.normcase(a) not in {os.path.normcase(c) for c in clean}:
            clean.append(a)
    T.write_json(config_path(ctx), {"roots": clean, "version": CONFIG_VERSION})
    return 200, config_json(ctx)


# ---------------------------------------------------------------------------
# episodes
# ---------------------------------------------------------------------------

@handler
def get_episodes(ctx: Context, query: dict):
    return 200, E.find_episodes(load_roots(ctx))


def sweep(ctx: Context, ep: str, pass_: str) -> list:
    """Close queued takes whose job is gone (see h3edit.sweep_takes). A ComfyUI
    that doesn't answer means no sweep this time, not an error."""
    try:
        changed = E.sweep_takes(ep, pass_, ctx.comfy)
    except FileNotFoundError:
        raise
    except Exception:
        return []
    for t in changed:
        take_event(ctx, ep, t)
    return changed


@handler
def get_episode(ctx: Context, query: dict):
    ep = check_ep(ctx, query.get("ep"))
    pass_ = check_pass(query.get("pass"))
    J.load_shotlist(ep, pass_)                           # 404 before touching ComfyUI
    sweep(ctx, ep, pass_)
    return 200, seeds_out(E.episode_status(ep, pass_))


@handler
def get_shot(ctx: Context, query: dict):
    ep = check_ep(ctx, query.get("ep"))
    pass_ = check_pass(query.get("pass"))
    shot = check_shot(query.get("shot"))
    try:
        detail = E.shot_detail(ep, pass_, shot)
    except KeyError as e:
        raise ApiError(404, e.args[0])
    return 200, seeds_out(detail)


@handler
def get_browse(ctx: Context, query: dict):
    """Folder picker. Not limited to the roots: it is how roots are chosen.
    Lists folder names only, never file contents."""
    try:
        return 200, E.browse(query.get("path") or None, query.get("files") or None)
    except ValueError as e:
        raise ApiError(400, str(e))
    except FileNotFoundError as e:
        raise ApiError(404, str(e))
    except PermissionError as e:
        raise ApiError(403, str(e))


@handler
def post_build(ctx: Context, body):
    body = body_dict(body)
    ep = check_ep(ctx, body.get("ep"))
    result = E.build_episode(ep)
    episode_event(ctx, ep)
    return 200, result


# ---------------------------------------------------------------------------
# files
# ---------------------------------------------------------------------------

@handler
def get_file(ctx: Context, query: dict):
    """(200, {"path", "content_type"}) for the adapter to stream."""
    ep = check_ep(ctx, query.get("ep"))
    full, rel = ep_file(ctx, ep, query.get("path"))
    ext = os.path.splitext(full)[1].lower()
    return 200, {"path": full, "content_type": CONTENT_TYPES.get(ext, "application/octet-stream")}


def ep_file(ctx: Context, ep: str, rel, escape_status: int = 403) -> tuple[str, str]:
    """(full path, the relative path with forward slashes) of an existing file
    inside the episode (or its parent-folder series config's folder). 400 for
    a path that isn't relative or climbs out, `escape_status` for one that
    leads outside through a link, 404 for no such file."""
    if not rel or not isinstance(rel, str):
        raise ApiError(400, "path is required")
    parts = rel.replace("\\", "/").split("/")
    if (rel.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", rel) or "\x00" in rel
            or (".." in parts and not _in_series_home(ctx, ep, parts))):
        raise ApiError(400, f"path must be relative to the episode and stay inside it: {rel!r}")
    full = os.path.normpath(os.path.join(ep, *[p for p in parts if p not in ("", ".")]))
    if not (_inside(_real(full), _real(ep)) or _in_series_home(ctx, ep, parts)):
        raise ApiError(escape_status, f"{rel} leads outside the episode")
    if not os.path.isfile(full):
        raise ApiError(404, f"no file {rel} in {ep}")
    return full, "/".join(p for p in parts if p not in ("", "."))


def _in_series_home(ctx: Context, ep: str, parts: list[str]) -> bool:
    """A path like ../refs/x.png is allowed when the episode's series config lives in its
    parent folder (a series folder, where series refs live) and the file is
    inside that folder and inside a configured root."""
    series_cfg = E.episode_series_config(ep)
    if not series_cfg:
        return False
    home = _real(os.path.dirname(os.path.abspath(series_cfg)))
    if home == _real(ep):
        return False
    full = _real(os.path.join(ep, *[p for p in parts if p not in ("", ".")]))
    return _inside(full, home) and any(_inside(full, _real(r)) for r in load_roots(ctx))


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------

def _opt_str(body: dict, key: str) -> str | None:
    v = body.get(key)
    if v is not None and not isinstance(v, str):
        raise ApiError(400, f"{key} must be a string or null")
    return v


def _opt_steps(v) -> int | None:
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, int) or v < 1:
        raise ApiError(400, "steps must be a whole number of 1 or more, or null")
    return v


def _opt_prompt(v):
    if v is None or isinstance(v, str):
        return v
    if isinstance(v, list) and all(isinstance(x, str) for x in v):
        return list(v)
    raise ApiError(400, "prompt must be text, a list of sections, or null")


def _opt_loras(v) -> list[dict] | None:
    if v is None:
        return None
    if not isinstance(v, list):
        raise ApiError(400, "loras must be a list of {name, strength}, or null")
    out = []
    for lo in v:
        if isinstance(lo, str):
            out += J.parse_lora(lo)
            continue
        if not isinstance(lo, dict) or not isinstance(lo.get("name"), str) or not lo["name"]:
            raise ApiError(400, "each LoRA needs a name: {\"name\": ..., \"strength\": 1.0}")
        s = lo.get("strength", 1.0)
        if isinstance(s, bool) or not isinstance(s, (int, float)):
            raise ApiError(400, f"LoRA {lo['name']}: strength must be a number")
        out.append({"name": lo["name"], "strength": float(s)})
    return out


def _opt_negative(v) -> str | None:
    """A negative prompt from a request: text ("" is an explicit empty one),
    or null for not set."""
    if v is None:
        return None
    if not isinstance(v, str):
        raise ApiError(400, "negative must be text or null")
    return v


def _opt_target(v) -> str | None:
    """A video target id from a request (null: not set), 400 if unknown."""
    if v is None or v == "":
        return None
    if not isinstance(v, str):
        raise ApiError(400, "target must be a video target id or null")
    try:
        return J.check_video_target(v)
    except Exception as e:
        raise ApiError(400, str(e))


@handler
def post_render(ctx: Context, body):
    body = body_dict(body)
    ep = check_ep(ctx, body.get("ep"))
    pass_ = check_pass(body.get("pass"))
    shots = body.get("shots")
    if shots is not None and (not isinstance(shots, list)
                              or not all(isinstance(s, str) and s for s in shots)):
        raise ApiError(400, "shots must be a list of shot ids (or null for every shot)")
    seed_mode = body.get("seed_mode") or "auto"
    if seed_mode not in ("auto", "new", "same"):
        raise ApiError(400, f"seed_mode must be auto, new or same, not {seed_mode!r}")
    redo = body.get("redo", False)
    if not isinstance(redo, bool):
        raise ApiError(400, "redo must be true or false")
    allow_missing = body.get("allow_missing_refs", False)
    if not isinstance(allow_missing, bool):
        raise ApiError(400, "allow_missing_refs must be true or false")
    allow_mismatch = body.get("allow_model_mismatch", False)
    if not isinstance(allow_mismatch, bool):
        raise ApiError(400, "allow_model_mismatch must be true or false")
    template = J.RenderRequest(
        shot_id="", redo=redo, seed=seed_in(body.get("seed")), seed_mode=seed_mode,
        model=_opt_str(body, "model") or None, loras=_opt_loras(body.get("loras")),
        steps=_opt_steps(body.get("steps")), prompt=_opt_prompt(body.get("prompt")),
        parent_take=check_take(body.get("parent_take"), "parent_take", nullable=True),
        note=_opt_str(body, "note") or "", allow_missing_refs=allow_missing,
        target=_opt_target(body.get("target")), allow_model_mismatch=allow_mismatch,
        negative=_opt_negative(body.get("negative")))
    J.load_shotlist(ep, pass_)                           # 404 before anything else
    workflows: dict = {}

    def base_for(target_id: str) -> dict:
        """The workflow of a job's target, resolved once per request; a target
        whose workflow can't be read fails every shot on it (reported per shot)."""
        if target_id not in workflows:
            t = TG.load_target(target_id, "video")
            b = t.binding
            try:
                base, _ = J.target_workflow(t, None, ctx.comfy_url)
                if b.loader_class:
                    J.node_of(base, b.loader_class)
                if not (b.saver.get("replace") and any(
                        v["class_type"] == b.saver["replace"]["class_type"] for v in base.values())):
                    J.node_of(base, b.saver_class)
                workflows[target_id] = base
            except Exception as e:
                workflows[target_id] = RuntimeError(f"no usable {b.workflow_name}: {e}")
        got = workflows[target_id]
        if isinstance(got, Exception):
            raise got
        return got

    result = E.queue_shots(ep, pass_, shots, template, ctx.comfy, base_for,
                           model_resolve=ctx.model_resolve, model_cache=ctx.model_cache,
                           model_list=ctx.model_choices)
    for q in result["queued"]:
        # "queued" even if the job has already finished: the saver sends its own event
        take_event(ctx, ep, T.get_take(ep, pass_, q["shot"], q["take"]), "queued")
    for err in result["errors"]:
        if err.get("take"):
            take_event(ctx, ep, T.get_take(ep, pass_, err["shot"], err["take"]))
    if result["queued"] or any(e.get("take") for e in result["errors"]):
        episode_event(ctx, ep)
    return 200, seeds_out(result)


@handler
def post_cancel(ctx: Context, body):
    body = body_dict(body)
    ep = check_ep(ctx, body.get("ep"))
    pass_ = check_pass(body.get("pass"))
    shot = check_shot(body.get("shot"))
    take = check_take(body.get("take"))
    try:
        t = E.cancel_take(ep, pass_, shot, take, ctx.comfy)
    except E.NotQueued as e:
        raise ApiError(409, str(e))
    except LookupError as e:
        raise ApiError(404, str(e))
    except (OSError, RuntimeError) as e:
        raise ApiError(500, f"couldn't reach ComfyUI to cancel: {e}")
    take_event(ctx, ep, t)
    sc = t.sidecar or {}
    return 200, {"shot": shot, "pass": pass_, "take": take, "status": t.status,
                 "save_notes": sc.get("save_notes", ""), "finished": sc.get("finished")}


@handler
def post_discard(ctx: Context, body):
    """Move a take to renders[_proxy]/_trash/<shot>/ (h3edit.discard_take);
    a cut entry that picked it goes back to the latest usable take."""
    body = body_dict(body)
    ep = check_ep(ctx, body.get("ep"))
    pass_ = check_pass(body.get("pass"))
    shot = check_shot(body.get("shot"))
    take = check_take(body.get("take"))
    try:
        res = E.discard_take(ep, pass_, shot, take)
    except T.StillQueued as e:
        raise ApiError(409, str(e))
    except LookupError as e:
        raise ApiError(404, str(e))
    episode_event(ctx, ep)
    return 200, res


# ---------------------------------------------------------------------------
# cut and overrides
# ---------------------------------------------------------------------------

@handler
def put_pick(ctx: Context, body):
    body = body_dict(body)
    ep = check_ep(ctx, body.get("ep"))
    pass_ = check_pass(body.get("pass"))
    shot = check_shot(body.get("shot"))
    take = check_take(body.get("take"), nullable=True)
    src = body.get("from_pass")
    if src is not None:
        src = check_pass(src)
    force = body.get("force", False)
    if not isinstance(force, bool):
        raise ApiError(400, "force must be true or false")
    try:
        cut = E.pick_take(ep, pass_, shot, take, from_pass=src, force=force)
    except E.NotUsable as e:
        raise ApiError(409, f"{e}; pick it anyway with \"force\": true")
    except E.Locked as e:
        raise ApiError(409, f"{e}: unlock it first, or pick anyway with \"force\": true",
                       locked=True)
    except KeyError as e:
        raise ApiError(404, e.args[0])
    except LookupError as e:
        raise ApiError(404, str(e))
    episode_event(ctx, ep)
    return 200, {"cut": cut}


def _cut_entry(raw) -> dict:
    if not isinstance(raw, dict) or not isinstance(raw.get("shot"), str) or not raw["shot"]:
        raise ApiError(400, "each cut entry needs a shot id")
    e = {"shot": raw["shot"]}
    if raw.get("take") is not None:
        e["take"] = check_take(raw["take"], f"{raw['shot']}: take")
    if raw.get("pass") is not None:
        e["pass"] = check_pass(raw["pass"])
    for k in ("trim_in", "trim_out"):
        v = raw.get(k)
        if v is None:
            continue
        if isinstance(v, bool) or not isinstance(v, int) or v < 0:
            raise ApiError(400, f"{raw['shot']}: {k} must be a whole number of frames >= 0")
        if v:
            e[k] = v
    if raw.get("locked") is not None:
        if not isinstance(raw["locked"], bool):
            raise ApiError(400, f"{raw['shot']}: locked must be true or false")
        if raw["locked"]:
            e["locked"] = True
    if raw.get("note") is not None:
        if not isinstance(raw["note"], str):
            raise ApiError(400, f"{raw['shot']}: note must be text")
        if raw["note"]:
            e["note"] = raw["note"]
    unknown = set(raw) - set(T.ENTRY_FIELDS)
    if unknown:
        raise ApiError(400, f"{raw['shot']}: unknown cut field(s) {', '.join(sorted(unknown))}")
    return e


@handler
def put_cut(ctx: Context, body):
    body = body_dict(body)
    ep = check_ep(ctx, body.get("ep"))
    pass_ = check_pass(body.get("pass"))
    entries = body.get("entries")
    if not isinstance(entries, list):
        raise ApiError(400, "entries must be a list of cut entries")
    clean = [_cut_entry(e) for e in entries]
    seen = set()
    for e in clean:
        if e["shot"] in seen:
            raise ApiError(400, f"{e['shot']} is in the cut twice")
        seen.add(e["shot"])
    try:
        cut = E.replace_cut(ep, pass_, clean)
    except E.CutError as e:
        raise ApiError(400, str(e))
    episode_event(ctx, ep)
    return 200, {"cut": cut}


def _cut_what(v) -> str:
    if v not in E.CUT_WHAT:
        raise ApiError(400, f"what must be one of {', '.join(E.CUT_WHAT)}, not {v!r}")
    return v


@handler
def post_cut_reset(ctx: Context, body):
    """Script order (picks, locks, notes kept) and/or no trims (h3edit.reset_cut)."""
    body = body_dict(body)
    ep = check_ep(ctx, body.get("ep"))
    pass_ = check_pass(body.get("pass"))
    what = _cut_what(body.get("what"))
    J.load_shotlist(ep, pass_)                           # 404: not built
    try:
        cut = E.reset_cut(ep, pass_, what)
    except E.CutError as e:
        raise ApiError(400, str(e))
    episode_event(ctx, ep)
    return 200, {"cut": cut}


@handler
def post_cut_copy(ctx: Context, body):
    """One pass's order and/or trims onto the other (h3edit.copy_cut)."""
    body = body_dict(body)
    ep = check_ep(ctx, body.get("ep"))
    for k in ("from", "to"):
        if body.get(k) not in PASSES:
            raise ApiError(400, f"{k} must be 'final' or 'proxy', not {body.get(k)!r}")
    src, dst = body["from"], body["to"]
    what = _cut_what(body.get("what"))
    if src == dst:
        raise ApiError(400, "from and to must be different passes")
    J.load_shotlist(ep, src)                             # 404: not built
    J.load_shotlist(ep, dst)
    try:
        cut = E.copy_cut(ep, src, dst, what)
    except E.CutError as e:
        raise ApiError(400, str(e))
    episode_event(ctx, ep)
    return 200, {"cut": cut}


def _num(query: dict, key: str, cast, lo=None):
    v = query.get(key)
    if v is None or v == "":
        return None
    try:
        n = cast(v)
    except (TypeError, ValueError):
        raise ApiError(400, f"{key} must be a number, not {v!r}")
    if cast is float and n != n:                          # NaN
        raise ApiError(400, f"{key} must be a number")
    if lo is not None and n < lo:
        raise ApiError(400, f"{key} must be {lo} or more")
    return n


@handler
def get_peaks(ctx: Context, query: dict):
    """A media file's waveform (h3peaks.peaks): max |amplitude| per bin, 0..255."""
    ep = check_ep(ctx, query.get("ep"))
    full, rel = ep_file(ctx, ep, query.get("path"), escape_status=400)
    bins = _num(query, "bins", int, 1)
    if bins is not None and bins > PK.MAX_BINS:
        raise ApiError(400, f"bins must be {PK.MAX_BINS} or fewer")
    start = _num(query, "start", float, 0)
    end = _num(query, "end", float, 0)
    try:
        return 200, PK.peaks(ep, full, rel, bins, start, end)
    except ValueError as e:
        raise ApiError(400, str(e))
    except PK.PeaksError as e:
        raise ApiError(500, str(e))


OVERRIDE_FIELDS = ("prompt", "seed", "model", "loras", "steps", "note", "target", "negative",
                   "model_low")


@handler
def put_override(ctx: Context, body):
    body = body_dict(body)
    ep = check_ep(ctx, body.get("ep"))
    pass_ = check_pass(body.get("pass"))
    shot = check_shot(body.get("shot"))
    both = body.get("both", False)
    if not isinstance(both, bool):
        raise ApiError(400, "both must be true or false")
    fields = body.get("fields")
    if not isinstance(fields, dict):
        raise ApiError(400, "fields must be an object, e.g. {\"seed\": \"1234\"}")
    unknown = set(fields) - set(OVERRIDE_FIELDS)
    if unknown:
        raise ApiError(400, f"unknown override field(s) {', '.join(sorted(unknown))}: "
                            f"one of {', '.join(OVERRIDE_FIELDS)}")
    shot_fields, pass_fields = {}, {}
    if "seed" in fields:
        shot_fields["seed"] = seed_in(fields["seed"])
    if "note" in fields:
        if fields["note"] is not None and not isinstance(fields["note"], str):
            raise ApiError(400, "note must be text or null")
        shot_fields["note"] = fields["note"] or None
    if "prompt" in fields:
        pass_fields["prompt"] = _opt_prompt(fields["prompt"])
    if "model" in fields:
        m = fields["model"]
        if m is not None and not isinstance(m, str):
            raise ApiError(400, "model must be a file name or null")
        pass_fields["model"] = m or None
    if "loras" in fields:
        pass_fields["loras"] = _opt_loras(fields["loras"])
    if "steps" in fields:
        pass_fields["steps"] = _opt_steps(fields["steps"])
    if "negative" in fields:
        # "" is an explicit empty negative; null clears the override
        n = fields["negative"]
        if n is not None and not isinstance(n, str):
            raise ApiError(400, "negative must be text or null")
        pass_fields["negative"] = n
    if "model_low" in fields:
        m = fields["model_low"]
        if m is not None and not isinstance(m, str):
            raise ApiError(400, "model_low must be a file name or null")
        pass_fields["model_low"] = m or None

    built_target = E.shot_built_target(ep, shot)
    if built_target is None:
        raise ApiError(404, f"{shot} is in no built shotlist of this episode")
    ov = T.load_overrides(ep)
    if "target" in fields:
        # shared by both passes; null clears it, and so does the built target
        # itself unless an episode target is set (then it pins the shot there)
        want = _opt_target(fields["target"])
        T.set_shot_target(ov, shot, E.keeps_shot_target(ov, want, built_target))
    # overrides are keyed by the target the shot renders on (after this change)
    target = J.effective_target(ov, shot, built_target, root=ep)
    built = E.pass_entries(ep, shot, target)
    passes = list(T.PASSES) if both else [pass_]
    missing = [ps for ps in passes if ps not in built]
    if pass_fields and missing:
        raise ApiError(409, f"{shot} has no {'/'.join(missing)} build"
                            + (f" that compiles for {target}" if target != built_target else "")
                            + ": build the episode first")
    E.set_shot_override(ov, shot, built, passes, shot_fields, pass_fields, target)
    if "episode" not in ov:
        ov["episode"] = next((J.load_shotlist(ep, ps).get("episode", "") for ps in T.PASSES
                              if os.path.isfile(os.path.join(ep, J.shotlist_rel(ps)))), "")
    T.save_overrides(ep, ov)
    episode_event(ctx, ep)
    return 200, seeds_out({"override": E.override_view(ov, shot, built, target),
                           "target": target, "built_target": built_target})


@handler
def delete_override(ctx: Context, query: dict):
    ep = check_ep(ctx, query.get("ep"))
    shot = check_shot(query.get("shot"))
    p = query.get("pass")
    pass_ = check_pass(p) if p else None
    built_target = E.shot_built_target(ep, shot) or T.DEFAULT_TARGET
    ov = T.load_overrides(ep)
    target = J.effective_target(ov, shot, built_target, root=ep)
    E.clear_shot_override(ov, shot, pass_, target)
    if pass_ is None:                                   # everything: the retarget too
        T.set_shot_target(ov, shot, None)
        # back to the script's target, else the episode's, else the build's
        target = J.effective_target(ov, shot, built_target, root=ep)
    T.save_overrides(ep, ov)
    episode_event(ctx, ep)
    built = E.pass_entries(ep, shot, target)
    return 200, seeds_out({"override": E.override_view(ov, shot, built, target),
                           "target": target, "built_target": built_target})


# ---------------------------------------------------------------------------
# targets (Phase 7)
# ---------------------------------------------------------------------------

@handler
def get_targets(ctx: Context, query: dict):
    """Every video, image and audio target: id, kind, label, presets, and the
    widgets its binding exposes (for pickers). `kind` narrows to one kind."""
    kind = query.get("kind") or None
    if kind is not None and kind not in TG.KINDS:
        raise ApiError(400, f"kind must be one of {', '.join(TG.KINDS)}, not {kind!r}")
    ready = query.get("ready")
    if ready not in (None, "", "0", "1", "true", "false"):
        raise ApiError(400, f"ready must be 1 or 0, not {ready!r}")
    targets = TG.list_targets(kind)
    out = [t.describe() for t in targets]
    if ready in ("1", "true"):
        rd = target_readiness(ctx)
        for d in out:
            d["readiness"] = rd.get(d["id"]) or {"status": "unknown", "missing": [],
                                                 "resolved": {}, "features_off": [],
                                                 "nodes_missing": []}
    return 200, {"targets": out, "default": dict(TG.DEFAULT_TARGETS)}


READY_TTL = 30.0                                          # seconds a readiness answer is kept
_READY: dict = {}
_READY_LOCK = None


def target_readiness(ctx: Context) -> dict:
    """Every target's readiness (h3edit.readiness) on this ComfyUI, from its
    /object_info and model folders, kept READY_TTL seconds per ComfyUI
    address. The handler runs off the event loop (h3pipe_routes); /object_info
    is one self-request, and headers are only read to find a family
    substitute (cached in the user folder)."""
    import threading
    import time
    global _READY_LOCK
    if _READY_LOCK is None:
        _READY_LOCK = threading.Lock()
    with _READY_LOCK:
        hit = _READY.get(ctx.comfy_url)
        if hit and time.monotonic() - hit[0] < READY_TTL:
            return hit[1]
        try:
            info, error = ctx.comfy.object_info(), ""
        except Exception as e:
            info, error = None, f"ComfyUI didn't answer /object_info: {e}"
        rd = E.readiness(TG.list_targets(), info, ctx.model_resolve, ctx.model_cache,
                         error=error)
        # an unanswered ComfyUI isn't worth remembering
        if info is not None:
            _READY[ctx.comfy_url] = (time.monotonic(), rd)
        return rd


@handler
def put_episode_target(ctx: Context, body):
    """The episode's video target (overrides.json's episode.target): the
    default of every shot the script gives no target. null clears it. Never
    writes series.json."""
    body = body_dict(body)
    ep = check_ep(ctx, body.get("ep"))
    if "target" not in body:
        raise ApiError(400, "target is required: a video target id, or null to clear it")
    want = _opt_target(body.get("target"))
    if not any(os.path.isfile(os.path.join(ep, J.shotlist_rel(ps))) for ps in T.PASSES):
        raise ApiError(404, f"{ep} has no build: build the episode first")
    info = E.set_episode_target(ep, want)
    episode_event(ctx, ep)
    return 200, {k: info[k] for k in ("target", "target_source", "series_target")}


MATCH_ORDER = {"name": 0, "fingerprint": 1, "other": 2}


@handler
def get_models(ctx: Context, query: dict):
    """The files ComfyUI offers for one model param of a target, each checked
    against the family the target wants: `match` "name" (named like it),
    "fingerprint" (its header says so) or "other" (anything else: unknown,
    unreadable, or another family, `mismatch` true). Matching files first, in
    ComfyUI's order. Headers are read only for files whose name doesn't
    match, and cached (<user dir>/default/h3pipe/modelid_cache.json). `ep`
    (optional) adds that series config's model_families."""
    tid = query.get("target")
    if not tid or not isinstance(tid, str):
        raise ApiError(400, "target is required (a target id from /h3pipe/targets)")
    try:
        t = TG.load_target(tid)
    except TG.TargetError as e:
        raise ApiError(400, str(e))
    param = query.get("param") or "model"
    spec = t.models.get(param)
    if spec is None:
        raise ApiError(400, f"{t.id} declares no model family for {param!r} "
                            f"(it does for: {', '.join(t.models) or 'nothing'})")
    extra = {}
    if query.get("ep"):
        ep = check_ep(ctx, query.get("ep"))
        try:
            extra = J.series_model_families(ep)
        except ValueError as e:
            raise ApiError(400, str(e))
    names = ctx.model_list(spec["folder"], spec["class_type"], spec["field"])
    files = []
    for n in names:
        c = TG.check_model(t, param, n, ctx.model_resolve, extra, ctx.model_cache) or {}
        found = c.get("found") or {}
        m = c.get("match")
        files.append({"name": n,
                      "match": m if m in ("name", "fingerprint") else "other",
                      "mismatch": bool(c.get("block")),
                      "family": spec["family"] if m == "name" else found.get("family"),
                      "label": c.get("label") if m == "name" else found.get("label") or "",
                      "confidence": "name" if m == "name" else found.get("confidence", "unknown"),
                      "detail": c.get("message") or "",
                      **({"base": found["base"]} if found.get("base") else {})})
    files.sort(key=lambda f: MATCH_ORDER[f["match"]])        # stable: ComfyUI's order within
    return 200, {"target": t.id, "param": param, "family": spec["family"],
                 "label": TG.modelid.family_label(spec["family"]),
                 "patterns": spec["patterns"] + [p for p in extra.get(spec["family"], [])
                                                 if p not in spec["patterns"]],
                 "folder": spec["folder"], "class_type": spec["class_type"],
                 "field": spec["field"], "fingerprint": ctx.model_resolve is not None,
                 "files": files}


# ---------------------------------------------------------------------------
# assemble
# ---------------------------------------------------------------------------

@handler
def post_assemble(ctx: Context, body):
    body = body_dict(body)
    ep = check_ep(ctx, body.get("ep"))
    pass_ = check_pass(body.get("pass"))
    partial = body.get("partial", True)
    if not isinstance(partial, bool):
        raise ApiError(400, "partial must be true or false")
    J.load_shotlist(ep, pass_)
    return 200, E.assemble_episode(ep, pass_, partial)


# ---------------------------------------------------------------------------
# references (Phase 5)
# ---------------------------------------------------------------------------

def _series(ep: str):
    try:
        return R.load_series(ep)
    except FileNotFoundError as e:
        raise ApiError(404, str(e))
    except ValueError as e:
        raise ApiError(500, f"series.json can't be read: {e}")


def _ref(s, ref_id):
    try:
        return R.find_ref(s, ref_id)
    except R.RefError as e:
        raise ApiError(400, str(e))
    except R.UnknownRef as e:
        raise ApiError(404, str(e))


def _view(ref, v, required: bool = False):
    if v is not None and not isinstance(v, str):
        raise ApiError(400, "view must be a view name or null")
    try:
        return R.check_view(ref, v, required=required)
    except R.RefError as e:
        raise ApiError(400, str(e))


def ref_event(ctx: Context, ep: str, ref_id: str, view, take, status: str) -> None:
    ctx.emit("h3pipe.ref", {"ep": ep, "ref": ref_id, "view": view, "take": take,
                            "status": status})


def sweep_refs(ctx: Context, s) -> list:
    """Close queued ref takes whose job is gone (h3refs.sweep). A ComfyUI that
    doesn't answer means no sweep this time, not an error."""
    try:
        changed = R.sweep(s, ctx.comfy)
    except Exception:
        return []
    for t in changed:
        ref_event(ctx, s.ep, t.ref, t.view, t.take, t.status)
    auto_pick_refs(ctx, s)
    return changed


def auto_pick_refs(ctx: Context, s) -> None:
    """Refs with no live file yet take their first finished candidate
    (h3refs.auto_pick); a stitch failure is left for an explicit pick to report."""
    picked = False
    for ref in R.series_refs(s) + R.keyframe_refs(s.ep):
        try:
            for res in R.auto_pick(s, ref):
                ref_event(ctx, s.ep, ref.id, res.take.view, res.take.take, "picked")
                picked = True
        except Exception:
            continue
    if picked:
        episode_event(ctx, s.ep)


def image_ready(ctx: Context):
    """ready(target) for the image-target defaults: its required files are
    among what ComfyUI offers (h3refs.target_ready)."""
    return R.target_ready(J.model_lister(ctx.comfy, ctx.model_choices))


@handler
def get_refs(ctx: Context, query: dict):
    ep = check_ep(ctx, query.get("ep"))
    s = _series(ep)
    sweep_refs(ctx, s)
    return 200, seeds_out(R.refs_listing(ep, ready=image_ready(ctx)))


@handler
def put_refs_defaults(ctx: Context, body):
    """The episode's ref-target choices (overrides.json's
    episode.refs_target / episode.keyframe_target / episode.voice_target):
    `target` for series refs, `keyframe_target` for shot keyframes,
    `voice_target` for voices (an audio target); null clears one, a key left
    out is kept. Never writes series.json. Returns the defaults as GET
    /h3pipe/refs gives them."""
    body = body_dict(body)
    ep = check_ep(ctx, body.get("ep"))
    s = _series(ep)
    fields = {}
    for k, kind in (("target", "image"), ("keyframe_target", "image"),
                    ("voice_target", "audio")):
        if k in body:
            v = body[k]
            if v is not None and (not isinstance(v, str)):
                raise ApiError(400, f"{k} must be an {kind} target id or null")
            fields[k] = v or None
    if not fields:
        raise ApiError(400, "give target, keyframe_target and/or voice_target (a target id "
                            "of that kind, or null to clear it)")
    try:
        R.set_image_defaults(ep, fields)
        d = R.image_defaults(s, image_ready(ctx))
    except R.RefError as e:
        raise ApiError(400, str(e))
    episode_event(ctx, ep)
    return 200, {"defaults": d}


@handler
def delete_refs_pick(ctx: Context, query: dict):
    """Unpick a ref (h3refs.clear_pick): its live file goes, every take stays,
    and auto-pick leaves it alone until something is picked. For a shot
    keyframe this is Clear: the shot no longer uses one. Returns the ref as
    GET /h3pipe/refs lists it."""
    ep = check_ep(ctx, query.get("ep"))
    s = _series(ep)
    ref = _ref(s, query.get("ref"))
    view = _view(ref, query.get("view") or None)
    try:
        res = R.clear_pick(s, ref, view)
    except R.RefError as e:
        raise ApiError(400, str(e))
    ref_event(ctx, ep, ref.id, view, res.was, "cleared")
    episode_event(ctx, ep)
    return 200, seeds_out(R.ref_json(s, ref, R.used_by(s, [ref]), ready=image_ready(ctx)))


@handler
def post_refs_generate(ctx: Context, body):
    body = body_dict(body)
    ep = check_ep(ctx, body.get("ep"))
    s = _series(ep)
    ref = _ref(s, body.get("ref"))
    view = _view(ref, body.get("view"))
    count = body.get("count", 1)
    if count is None:
        count = 1
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 16:
        raise ApiError(400, "count must be a whole number from 1 to 16")
    seed_mode = body.get("seed_mode") or "auto"
    if seed_mode not in ("auto", "new", "same"):
        raise ApiError(400, f"seed_mode must be auto, new or same, not {seed_mode!r}")
    prompt = body.get("prompt")
    if prompt is not None and not isinstance(prompt, str):
        raise ApiError(400, "prompt must be text or null")
    kind = R.ref_target_kind(ref)
    target = body.get("target")
    if target is not None and (not isinstance(target, str) or not target):
        raise ApiError(400, f"target must be an {kind} target id or null")
    if target:
        try:
            TG.load_target(target, kind)
        except TG.TargetError as e:
            raise ApiError(400, str(e))
    negative = body.get("negative")
    if negative is not None and not isinstance(negative, str):
        raise ApiError(400, "negative must be text or null")
    seconds = body.get("seconds")
    if seconds is not None:
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
            raise ApiError(400, "seconds must be a number of seconds, or null")
        if kind != "audio":
            raise ApiError(400, f"seconds is only for a voice ref, not {ref.id}")
    pass_ = check_pass(body.get("pass"), "final")
    req = R.GenRequest(ref=ref.id, view=view, count=count, seed_mode=seed_mode,
                       seed=seed_in(body.get("seed")), prompt=prompt or None,
                       model=_opt_str(body, "model") or None,
                       loras=_opt_loras(body.get("loras")),
                       steps=_opt_steps(body.get("steps")), note=_opt_str(body, "note") or "",
                       target=target or None, negative=negative, pass_=pass_,
                       seconds=seconds)
    why = R.can_generate(s, ref)
    if why:
        raise ApiError(400, why)
    try:
        base, _ = R.resolve_workflow(ctx.comfy_url)
    except Exception as e:
        raise ApiError(500, f"{R.REFS_WORKFLOW} can't be read: {e}")
    try:
        result = R.queue_generate(s, req, ctx.comfy, base, save_node=True,
                                  listing=J.model_lister(ctx.comfy, ctx.model_choices),
                                  resolve=ctx.model_resolve, cache=ctx.model_cache)
    except R.RefError as e:
        raise ApiError(400, str(e))
    except R.UnknownRef as e:
        raise ApiError(404, str(e))
    for q in result["queued"]:
        # "queued" even if the job has already finished: the saver sends its own event
        ref_event(ctx, ep, q["ref"], q["view"], q["take"], "queued")
    for err in result["errors"]:
        if err.get("take"):
            ref_event(ctx, ep, err["ref"], err["view"], err["take"], "failed")
    if result["queued"] or any(e.get("take") for e in result["errors"]):
        episode_event(ctx, ep)
    return 200, seeds_out(result)


@handler
def put_refs_pick(ctx: Context, body):
    """Make a candidate the live file. A voice whose character has no
    `voice_sample` yet is written to refs/voices/<id>.wav and the series
    config gains that line through the Phase 9a save path: the answer then
    carries `series_changed: true` and `path`."""
    body = body_dict(body)
    ep = check_ep(ctx, body.get("ep"))
    s = _series(ep)
    ref = _ref(s, body.get("ref"))
    view = _view(ref, body.get("view"), required=True)
    take = check_take(body.get("take"))
    force = body.get("force", False)
    if not isinstance(force, bool):
        raise ApiError(400, "force must be true or false")
    try:
        res = R.pick_take(s, ref, view, take, force=force)
    except R.NotUsable as e:
        raise ApiError(409, f"{e}; pick it anyway with \"force\": true")
    except R.UnknownRef as e:
        raise ApiError(404, str(e))
    except R.RefError as e:
        raise ApiError(400, str(e))
    except R.StitchError as e:
        ref_event(ctx, ep, ref.id, view, take, "picked")
        episode_event(ctx, ep)
        raise ApiError(500, f"picked, but the sheet could not be stitched: {e}")
    ref_event(ctx, ep, ref.id, view, take, "picked")
    episode_event(ctx, ep)
    if res.series_changed:
        s = _series(ep)                                   # series.json gained voice_sample
        ref = _ref(s, ref.id)
    out = seeds_out(R.ref_json(s, ref, R.used_by(s, [ref])))
    out["series_changed"] = res.series_changed
    return 200, out


MAX_UPLOAD = 64 * 1024 * 1024        # POST /h3pipe/refs/import as multipart


class Upload:
    """A file the adapter received in a multipart POST /h3pipe/refs/import
    and saved to a temporary file (it deletes it after the handler)."""

    def __init__(self, path: str, filename: str, size: int):
        self.path, self.filename, self.size = path, filename, size


def _pick_flag(v) -> bool:
    """`pick` from JSON (true/false/null) or a form field ("1", "true", "0", "")."""
    if v is None or v is False or v == "":
        return False
    if v is True:
        return True
    if isinstance(v, str) and v.strip().lower() in ("1", "true", "yes", "on"):
        return True
    if isinstance(v, str) and v.strip().lower() in ("0", "false", "no", "off"):
        return False
    raise ApiError(400, "pick must be true or false (a form sends \"1\" or \"0\")")


@handler
def post_refs_import(ctx: Context, body):
    """A file as a new take (source "imported"): `source_path`, a file on
    this machine (JSON), or `file`, an upload (multipart, which the adapter
    hands over as an Upload). `pick` picks it."""
    body = body_dict(body)
    ep = check_ep(ctx, body.get("ep"))
    s = _series(ep)
    ref = _ref(s, body.get("ref"))
    view = _view(ref, body.get("view") or None, required=True)
    pick = _pick_flag(body.get("pick"))
    up = body.get("file")
    if isinstance(up, Upload):
        if up.size > MAX_UPLOAD:
            raise ApiError(413, f"the file is over {MAX_UPLOAD // (1024 * 1024)} MB")
        src, name = up.path, up.filename or ""
        try:
            R.check_import_type(ref, name)
        except R.RefError as e:
            raise ApiError(400, str(e))
    elif up is not None:
        raise ApiError(400, "file must be sent as multipart/form-data")
    else:
        src, name = body.get("source_path"), None
        if not isinstance(src, str) or not src.strip():
            raise ApiError(400, "source_path is required: the file's absolute path on this "
                                "machine (or upload it as multipart `file`)")
        src = src.strip()
    try:
        t = R.import_take(s, ref, view, src, note=_opt_str(body, "note") or "",
                          original_name=name)
    except R.RefError as e:
        raise ApiError(400, str(e))
    ref_event(ctx, ep, ref.id, view, t.take, t.status)
    if pick:
        try:
            R.pick_take(s, ref, view, t.take)
        except R.StitchError as e:
            ref_event(ctx, ep, ref.id, view, t.take, "picked")
            episode_event(ctx, ep)
            raise ApiError(500, f"imported and picked, but the sheet could not be stitched: {e}")
        except (R.RefError, R.NotUsable) as e:
            episode_event(ctx, ep)
            raise ApiError(400, f"imported as take {t.take}, but not picked: {e}")
        ref_event(ctx, ep, ref.id, view, t.take, "picked")
        t = R.get_take(ref, view, t.take)
    episode_event(ctx, ep)
    return 200, seeds_out(R.take_json(ep, ref, t))


@handler
def post_refs_discard(ctx: Context, body):
    """Move a ref candidate to refs/_takes/_trash/ (h3refs.discard_take); if it
    was the pick, the ref (view) is cleared as DELETE /h3pipe/refs/pick does.
    Returns the ref as GET /h3pipe/refs lists it."""
    body = body_dict(body)
    ep = check_ep(ctx, body.get("ep"))
    s = _series(ep)
    ref = _ref(s, body.get("ref"))
    view = _view(ref, body.get("view") or None, required=True)
    take = check_take(body.get("take"))
    try:
        res = R.discard_take(s, ref, view, take)
    except T.StillQueued as e:
        raise ApiError(409, str(e))
    except R.UnknownRef as e:
        raise ApiError(404, str(e))
    except R.RefError as e:
        raise ApiError(400, str(e))
    ref_event(ctx, ep, ref.id, view, take, "discarded")
    if res.cleared is not None:
        ref_event(ctx, ep, ref.id, view, take, "cleared")
    episode_event(ctx, ep)
    return 200, seeds_out(R.ref_json(s, ref, R.used_by(s, [ref]), ready=image_ready(ctx)))


def _opt_image_target(body: dict, key: str) -> str | None:
    v = body.get(key)
    if v is None or v == "":
        return None
    if not isinstance(v, str):
        raise ApiError(400, f"{key} must be an image target id or null")
    try:
        TG.load_target(v, "image")
    except TG.TargetError as e:
        raise ApiError(400, str(e))
    return v


@handler
def post_refs_generate_missing(ctx: Context, body):
    """Every missing ref at once (h3refs.generate_missing): a candidate for
    each series ref this pass uses with no live file (not cleared, nothing
    queued or waiting), and every needed keyframe filled as `h3.py keyframe
    --missing` does. Returns {queued, picked, skipped, errors}; `dry_run`
    writes and queues nothing."""
    body = body_dict(body)
    ep = check_ep(ctx, body.get("ep"))
    pass_ = check_pass(body.get("pass"))
    kinds = body.get("kinds")
    if kinds is None:
        kinds = list(R.MISSING_KINDS)
    if (not isinstance(kinds, list) or not kinds
            or not all(k in R.MISSING_KINDS for k in kinds)):
        raise ApiError(400, "kinds must be a list of \"series\" and/or \"keyframe\"")
    target = _opt_image_target(body, "target")
    keyframe_target = _opt_image_target(body, "keyframe_target")
    dry_run = body.get("dry_run", False)
    if not isinstance(dry_run, bool):
        raise ApiError(400, "dry_run must be true or false")
    s = _series(ep)
    J.load_shotlist(ep, pass_)                           # 404 without a build
    base = None
    if not dry_run:
        try:
            base, _ = R.resolve_workflow(ctx.comfy_url)
        except Exception as e:
            raise ApiError(500, f"{R.REFS_WORKFLOW} can't be read: {e}")
    listing = J.model_lister(ctx.comfy, ctx.model_choices)
    result = R.generate_missing(s, None if dry_run else ctx.comfy, pass_, kinds, target,
                                keyframe_target, dry_run, base, listing=listing,
                                resolve=ctx.model_resolve, cache=ctx.model_cache)
    if not dry_run:
        for q in result["queued"]:
            ref_event(ctx, ep, q["ref"], q["view"], q["take"], "queued")
        for p in result["picked"]:
            ref_event(ctx, ep, p["ref"], None, p["take"], "ok")
            ref_event(ctx, ep, p["ref"], None, p["take"], "picked")
        for err in result["errors"]:
            if err.get("take"):
                ref_event(ctx, ep, err["ref"], err.get("view"), err["take"], "failed")
        if result["queued"] or result["picked"] or any(e.get("take") for e in result["errors"]):
            episode_event(ctx, ep)
    return 200, seeds_out(result)


@handler
def post_refs_keyframe(ctx: Context, body):
    """A shot's first (or last) keyframe from a frame of another shot's take:
    by default the previous (next) shot in the pass's cut, the take its cut
    entry uses, its last (first) frame (h3refs.keyframe_from_take)."""
    body = body_dict(body)
    ep = check_ep(ctx, body.get("ep"))
    pass_ = check_pass(body.get("pass"))
    shot = check_shot(body.get("shot"))
    which = body.get("which") or "first"
    if which not in ("first", "last"):
        raise ApiError(400, f"which must be \"first\" or \"last\", not {which!r}")
    source_shot = body.get("source_shot")
    if source_shot is not None and (not isinstance(source_shot, str) or not source_shot):
        raise ApiError(400, "source_shot must be a shot id or null")
    source_take = check_take(body.get("source_take"), "source_take", nullable=True)
    frame = body.get("frame")
    if isinstance(frame, str) and re.fullmatch(r"\s*-?\d+\s*", frame):
        frame = int(frame)
    if frame is not None and frame not in ("first", "last") and (
            isinstance(frame, bool) or not isinstance(frame, int)):
        raise ApiError(400, "frame must be a frame number, \"first\", \"last\" or null")
    pick = body.get("pick")
    if pick is not None and not isinstance(pick, bool):
        raise ApiError(400, "pick must be true, false or null")
    note = _opt_str(body, "note") or ""
    s = _series(ep)
    try:
        res = R.keyframe_from_take(s, shot, which, source_shot, source_take, frame, pass_,
                                   pick=pick, note=note)
    except R.RefError as e:
        raise ApiError(400, str(e))
    except R.UnknownRef as e:
        raise ApiError(404, str(e))
    except R.NotUsable as e:
        raise ApiError(409, str(e))
    except R.FfmpegMissing as e:
        raise ApiError(500, str(e))
    ref_event(ctx, ep, res.ref.id, None, res.take.take, res.take.status)
    if res.picked:
        ref_event(ctx, ep, res.ref.id, None, res.take.take, "picked")
    episode_event(ctx, ep)
    return 200, seeds_out(R.ref_json(s, res.ref, R.used_by(s, [res.ref])))


def _span(body: dict, key: str):
    """One end of a span in seconds, or None (`start` then means 0, `end` the
    end of the take)."""
    v = body.get(key)
    if v is None:
        return None
    if isinstance(v, str):
        try:
            v = float(v)
        except ValueError:
            raise ApiError(400, f"{key} must be a number of seconds") from None
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ApiError(400, f"{key} must be a number of seconds")
    return float(v)


@handler
def post_refs_voice_from_take(ctx: Context, body):
    """A line the model already spoke becomes a voice sample: cut
    `start`..`end` seconds out of a shot take's sound into a new candidate of
    the voice ref (h3refs.voice_from_take, source "from_take"). No model
    runs. Picked when the voice has no live file yet (`pick` forces or
    forbids it); picking a voice whose character has no `voice_sample` also
    writes the series config."""
    body = body_dict(body)
    ep = check_ep(ctx, body.get("ep"))
    s = _series(ep)
    ref = _ref(s, body.get("ref"))
    shot = check_shot(body.get("shot"))
    take = check_take(body.get("take"))
    pass_ = check_pass(body.get("pass"))
    start = _span(body, "start") or 0.0
    end = _span(body, "end")
    pick = body.get("pick")
    if pick is not None and not isinstance(pick, bool):
        raise ApiError(400, "pick must be true, false or null")
    try:
        res = R.voice_from_take(s, ref, shot, take, pass_, start, end, pick=pick,
                                note=_opt_str(body, "note") or "")
    except R.RefError as e:
        raise ApiError(400, str(e))
    except R.UnknownRef as e:
        raise ApiError(404, str(e))
    except R.NotUsable as e:
        raise ApiError(409, str(e))
    except R.FfmpegMissing as e:
        raise ApiError(500, str(e))
    ref_event(ctx, ep, ref.id, None, res.take.take, res.take.status)
    if res.picked:
        ref_event(ctx, ep, ref.id, None, res.take.take, "picked")
        s = _series(ep)                                   # a pick may have written series.json
        ref = _ref(s, ref.id)
    episode_event(ctx, ep)
    out = seeds_out(R.ref_json(s, ref, R.used_by(s, [ref])))
    out.update(picked=res.picked, take=res.take.take, source=res.source)
    return 200, out


REF_OVERRIDE_FIELDS = ("prompt", "seed", "model", "loras", "steps", "note", "target")


def _ref_override_json(s, ref, view) -> dict:
    o = R.override_view(s, ref, view, R.load_overrides(ref.home))
    return dict(o["values"], stale=o["stale"])


@handler
def put_refs_override(ctx: Context, body):
    body = body_dict(body)
    ep = check_ep(ctx, body.get("ep"))
    s = _series(ep)
    ref = _ref(s, body.get("ref"))
    view = _view(ref, body.get("view"))
    fields = body.get("fields")
    if not isinstance(fields, dict):
        raise ApiError(400, "fields must be an object, e.g. {\"seed\": \"1234\"}")
    unknown = set(fields) - set(REF_OVERRIDE_FIELDS)
    if unknown:
        raise ApiError(400, f"unknown override field(s) {', '.join(sorted(unknown))}: "
                            f"one of {', '.join(REF_OVERRIDE_FIELDS)}")
    clean = {}
    if "seed" in fields:
        clean["seed"] = seed_in(fields["seed"])
    if "note" in fields:
        if fields["note"] is not None and not isinstance(fields["note"], str):
            raise ApiError(400, "note must be text or null")
        clean["note"] = fields["note"] or None
    if "prompt" in fields:
        if fields["prompt"] is not None and not isinstance(fields["prompt"], str):
            raise ApiError(400, "prompt must be text or null")
        clean["prompt"] = fields["prompt"] or None
    if "model" in fields:
        m = fields["model"]
        if m is not None and not isinstance(m, str):
            raise ApiError(400, "model must be a file name or null")
        clean["model"] = m or None
    if "loras" in fields:
        clean["loras"] = _opt_loras(fields["loras"])
    if "steps" in fields:
        clean["steps"] = _opt_steps(fields["steps"])
    if "target" in fields:
        t = fields["target"]
        if t is not None and not isinstance(t, str):
            raise ApiError(400, "target must be an image target id or null")
        clean["target"] = t or None
    ov = R.load_overrides(ref.home)
    try:
        R.set_ref_override(ov, ref, view, clean,
                           R.prompt_hash(R.built_prompt(s, ref, view)))
    except R.RefError as e:
        raise ApiError(400, str(e))
    R.save_overrides(ref.home, ov)
    episode_event(ctx, ep)
    return 200, seeds_out({"override": _ref_override_json(s, ref, view)})


@handler
def delete_refs_override(ctx: Context, query: dict):
    ep = check_ep(ctx, query.get("ep"))
    s = _series(ep)
    ref = _ref(s, query.get("ref"))
    view = _view(ref, query.get("view") or None)
    ov = R.load_overrides(ref.home)
    R.clear_ref_override(ov, ref.id, view)
    R.save_overrides(ref.home, ov)
    episode_event(ctx, ep)
    return 200, seeds_out({"override": _ref_override_json(s, ref, view)})


# ---------------------------------------------------------------------------
# the script and the series config (Phase 9a)
# ---------------------------------------------------------------------------

def _file(v) -> str:
    if v not in P.H.FILES:
        raise ApiError(400, f"file must be 'script' or 'series', not {v!r}")
    return v


def _flag(v) -> bool:
    return v is True or (isinstance(v, str) and v.lower() in ("1", "true", "yes"))


@handler
def get_source(ctx: Context, query: dict):
    ep = check_ep(ctx, query.get("ep"))
    src = P.H.read_source(ep, _file(query.get("file")))
    return 200, P.H.source_json(src, hash_only=_flag(query.get("hash_only")))


@handler
def post_source_check(ctx: Context, body):
    body = body_dict(body)
    ep = check_ep(ctx, body.get("ep"))
    file = _file(body.get("file"))
    text = body.get("text")
    if not isinstance(text, str):
        raise ApiError(400, "text must be the file's text")
    return 200, P.H.check_text(ep, file, text)


@handler
def put_source(ctx: Context, body):
    """Save the script or the series config (409 when it changed on disk
    since `base_hash`), then rebuild unless `rebuild` is false."""
    body = body_dict(body)
    ep = check_ep(ctx, body.get("ep"))
    file = _file(body.get("file"))
    base_hash = body.get("base_hash")
    if not isinstance(base_hash, str) or not base_hash:
        raise ApiError(400, "base_hash is required: the hash the text was read at")
    rebuild = body.get("rebuild", True)
    if not isinstance(rebuild, bool):
        raise ApiError(400, "rebuild must be true or false")
    saved = P.H.save_source(ep, file, body.get("text"), base_hash)
    build = E.build_episode(ep) if rebuild else None
    episode_event(ctx, ep)
    return 200, {"hash": saved["hash"], "check": saved["check"], "build": build}


@handler
def get_promote(ctx: Context, query: dict):
    ep = check_ep(ctx, query.get("ep"))
    return 200, seeds_out(P.plan(ep, query.get("shot") or None))


@handler
def post_promote(ctx: Context, body):
    body = body_dict(body)
    ep = check_ep(ctx, body.get("ep"))
    items = body.get("items")
    if items != "all" and not (isinstance(items, list)
                               and all(isinstance(i, str) for i in items)):
        raise ApiError(400, "items must be a list of item ids from the plan, or \"all\"")
    hashes = body.get("hashes")
    if not isinstance(hashes, dict):
        raise ApiError(400, "hashes is required: {\"script\", \"series\"} from the plan")
    shot = body.get("shot") or None
    if shot is not None and not isinstance(shot, str):
        raise ApiError(400, "shot must be a shot id")
    result = P.apply(ep, items, hashes, shot)
    refs = result.pop("refs", [])
    if result["promoted"]:
        episode_event(ctx, ep)
        for ref_id in refs:
            ref_event(ctx, ep, ref_id, None, None, "promoted")
    return 200, seeds_out(result)


# ---------------------------------------------------------------------------
# the dialogue recording: attaching one, and aligning to it (Phase 9c A)
# ---------------------------------------------------------------------------

@handler
def get_align_ready(ctx: Context, query: dict):
    """What h3align needs (ffmpeg, numpy, a Whisper), checked against the
    Python that would run it, with the pip line for whatever is missing."""
    return 200, K.align_ready()


@handler
def post_track(ctx: Context, body):
    """Attach a dialogue recording: `source_path` (a file on this machine) or
    `file` (multipart, as /refs/import). It lands in <ep>/audio/, the series
    config's `audio.track` and `audio.mode` are written through the Phase 9a
    save path, and the episode is rebuilt. `track: null` clears it."""
    body = body_dict(body)
    ep = check_ep(ctx, body.get("ep"))
    pass_ = check_pass(body.get("pass"))
    up = body.get("file")
    src = body.get("source_path")
    if isinstance(up, Upload):
        if up.size > MAX_UPLOAD:
            raise ApiError(413, f"the file is over {MAX_UPLOAD // (1024 * 1024)} MB")
        res = K.attach_track(ep, up.path, up.filename or "")
    elif up is not None:
        raise ApiError(400, "file must be sent as multipart/form-data")
    elif isinstance(src, str) and src.strip():
        res = K.attach_track(ep, src.strip())
    elif src is not None:
        raise ApiError(400, "source_path must be the recording's absolute path on this "
                            "machine (or upload it as multipart `file`)")
    elif "track" in body and body.get("track") is None:
        res = K.clear_track(ep)
    else:
        raise ApiError(400, "send source_path (or a multipart `file`) to attach a "
                            "recording, or track: null to clear it")
    build = E.build_episode(ep)
    episode_event(ctx, ep)
    return 200, {"track": E.track_info(ep, pass_), "hash": res["hash"], "build": build,
                 "path": res["path"], "copied": res["copied"],
                 "reformatted": res["reformatted"]}


@handler
def post_align(ctx: Context, body):
    """Run h3align on the episode (a subprocess), sending `h3pipe.align` as it
    goes. 409 with `missing` when a dependency isn't installed; `dry_run`
    writes nothing."""
    body = body_dict(body)
    ep = check_ep(ctx, body.get("ep"))
    pass_ = check_pass(body.get("pass"))
    track = body.get("track")
    if track is not None and (not isinstance(track, str) or not track.strip()):
        raise ApiError(400, "track must be the recording's path (relative to the episode)")
    if isinstance(track, str):
        track = ep_file(ctx, ep, track.strip(), escape_status=400)[0]
    model = body.get("model")
    if model is not None and (not isinstance(model, str) or not model.strip()):
        raise ApiError(400, "model must be a Whisper model name, e.g. medium.en")
    snap = body.get("snap", True)
    dry = body.get("dry_run", False)
    for name, v in (("snap", snap), ("dry_run", dry)):
        if not isinstance(v, bool):
            raise ApiError(400, f"{name} must be true or false")

    def progress(ev):
        ctx.emit("h3pipe.align", {"ep": ep, "stage": ev["stage"], "pct": ev["pct"],
                                  "text": ev["text"]})

    res = K.align(ep, track=track, model=(model or "").strip() or None, snap=snap,
                  dry_run=dry, progress=progress)
    build = None if dry else E.build_episode(ep)
    if not dry:
        episode_event(ctx, ep)
    return 200, {"ok": bool(res.get("ok")), "dry_run": dry, "report": res.get("report", ""),
                 "report_path": res.get("report_path"), "changes": res.get("changes", []),
                 "notes": res.get("notes", []), "recording": res.get("recording"),
                 "duration": res.get("duration"), "words": res.get("words"),
                 "script_hash": res.get("script_hash"), "series_hash": res.get("series_hash"),
                 "track": E.track_info(ep, pass_), "build": build, "log": res.get("log", "")}


# (method, path, handler, what it takes: "query", "body" (JSON) or "form" (JSON, or
# multipart/form-data whose file field arrives as an Upload))
ROUTES = [
    ("GET", "/h3pipe/config", get_config, "query"),
    ("PUT", "/h3pipe/config", put_config, "body"),
    ("GET", "/h3pipe/episodes", get_episodes, "query"),
    ("GET", "/h3pipe/browse", get_browse, "query"),
    ("GET", "/h3pipe/episode", get_episode, "query"),
    ("GET", "/h3pipe/shot", get_shot, "query"),
    ("POST", "/h3pipe/build", post_build, "body"),
    ("GET", "/h3pipe/file", get_file, "query"),
    ("POST", "/h3pipe/render", post_render, "body"),
    ("POST", "/h3pipe/cancel", post_cancel, "body"),
    ("POST", "/h3pipe/discard", post_discard, "body"),
    ("PUT", "/h3pipe/pick", put_pick, "body"),
    ("PUT", "/h3pipe/cut", put_cut, "body"),
    ("POST", "/h3pipe/cut/reset", post_cut_reset, "body"),
    ("POST", "/h3pipe/cut/copy", post_cut_copy, "body"),
    ("GET", "/h3pipe/peaks", get_peaks, "query"),
    ("PUT", "/h3pipe/override", put_override, "body"),
    ("DELETE", "/h3pipe/override", delete_override, "query"),
    ("PUT", "/h3pipe/episode-target", put_episode_target, "body"),
    ("POST", "/h3pipe/assemble", post_assemble, "body"),
    ("GET", "/h3pipe/targets", get_targets, "query"),
    ("GET", "/h3pipe/models", get_models, "query"),
    ("GET", "/h3pipe/refs", get_refs, "query"),
    ("POST", "/h3pipe/refs/generate", post_refs_generate, "body"),
    ("PUT", "/h3pipe/refs/pick", put_refs_pick, "body"),
    ("DELETE", "/h3pipe/refs/pick", delete_refs_pick, "query"),
    ("PUT", "/h3pipe/refs/defaults", put_refs_defaults, "body"),
    ("POST", "/h3pipe/refs/import", post_refs_import, "form"),
    ("POST", "/h3pipe/refs/discard", post_refs_discard, "body"),
    ("POST", "/h3pipe/refs/generate-missing", post_refs_generate_missing, "body"),
    ("POST", "/h3pipe/refs/keyframe", post_refs_keyframe, "body"),
    ("POST", "/h3pipe/refs/voice-from-take", post_refs_voice_from_take, "body"),
    ("PUT", "/h3pipe/refs/override", put_refs_override, "body"),
    ("DELETE", "/h3pipe/refs/override", delete_refs_override, "query"),
    ("GET", "/h3pipe/source", get_source, "query"),
    ("POST", "/h3pipe/source/check", post_source_check, "body"),
    ("PUT", "/h3pipe/source", put_source, "body"),
    ("GET", "/h3pipe/promote", get_promote, "query"),
    ("POST", "/h3pipe/promote", post_promote, "body"),
    ("GET", "/h3pipe/align/ready", get_align_ready, "query"),
    ("POST", "/h3pipe/track", post_track, "form"),
    ("POST", "/h3pipe/align", post_align, "body"),
]
