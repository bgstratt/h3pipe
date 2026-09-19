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
    import h3refs as R  # noqa: E402
    import h3takes as T  # noqa: E402
    import targets as TG  # noqa: E402
except Exception as exc:                                  # pragma: no cover
    E = J = R = T = TG = None
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


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class Context:
    """What a handler needs from its host.

    user_dir   ComfyUI's user folder (config lives in <user_dir>/default/h3pipe/)
    comfy_url  this ComfyUI's own base URL
    emit       emit(event, data): a live-update event to every open editor
    comfy      an h3jobs.Comfy-like client (default: one for comfy_url, with
               client_id "h3pipe"); tests pass a fake
    env        environment for $H3PIPE_ROOTS (default os.environ)
    """

    def __init__(self, user_dir: str, comfy_url: str = DEFAULT_COMFY, emit=None,
                 comfy=None, env: dict | None = None):
        self.user_dir = user_dir
        self.comfy_url = (comfy_url or DEFAULT_COMFY).rstrip("/")
        self._emit = emit
        self._comfy = comfy
        self.env = os.environ if env is None else env

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
            return e.status, {"error": str(e)}
        except FileNotFoundError as e:
            return 404, {"error": str(e)}
        except Exception as e:                            # the message is for a person
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
    rel = query.get("path")
    if not rel or not isinstance(rel, str):
        raise ApiError(400, "path is required")
    parts = rel.replace("\\", "/").split("/")
    if (rel.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", rel) or "\x00" in rel
            or (".." in parts and not _in_series_home(ctx, ep, parts))):
        raise ApiError(400, f"path must be relative to the episode and stay inside it: {rel!r}")
    full = os.path.normpath(os.path.join(ep, *[p for p in parts if p not in ("", ".")]))
    if not (_inside(_real(full), _real(ep)) or _in_series_home(ctx, ep, parts)):
        raise ApiError(403, f"{rel} leads outside the episode")
    if not os.path.isfile(full):
        raise ApiError(404, f"no file {rel} in {ep}")
    ext = os.path.splitext(full)[1].lower()
    return 200, {"path": full, "content_type": CONTENT_TYPES.get(ext, "application/octet-stream")}


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
    template = J.RenderRequest(
        shot_id="", redo=redo, seed=seed_in(body.get("seed")), seed_mode=seed_mode,
        model=_opt_str(body, "model") or None, loras=_opt_loras(body.get("loras")),
        steps=_opt_steps(body.get("steps")), prompt=_opt_prompt(body.get("prompt")),
        parent_take=check_take(body.get("parent_take"), "parent_take", nullable=True),
        note=_opt_str(body, "note") or "", allow_missing_refs=allow_missing)
    doc = J.load_shotlist(ep, pass_)                     # 404 before anything else
    target = J.shotlist_target(doc)
    b = target.binding
    try:
        base, _ = J.target_workflow(target, None, ctx.comfy_url)
        J.node_of(base, b.loader_class), J.node_of(base, b.saver_class)
    except Exception as e:
        raise ApiError(500, f"no usable {b.workflow_name}: {e}")
    result = E.queue_shots(ep, pass_, shots, template, ctx.comfy, base)
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
    cut = E.replace_cut(ep, pass_, clean)
    episode_event(ctx, ep)
    return 200, {"cut": cut}


OVERRIDE_FIELDS = ("prompt", "seed", "model", "loras", "steps", "note")


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

    built = E.pass_builds(ep, shot)
    passes = list(T.PASSES) if both else [pass_]
    if not built:
        raise ApiError(404, f"{shot} is in no built shotlist of this episode")
    missing = [ps for ps in passes if ps not in built]
    if pass_fields and missing:
        raise ApiError(409, f"{shot} has no {'/'.join(missing)} build: build the episode first")
    doc = J.load_shotlist(ep, next(iter(built)))
    target = J.shotlist_target(doc).id                  # overrides are keyed by target
    ov = T.load_overrides(ep)
    E.set_shot_override(ov, shot, built, passes, shot_fields, pass_fields, target)
    ov.setdefault("episode", doc.get("episode", ""))
    T.save_overrides(ep, ov)
    episode_event(ctx, ep)
    return 200, seeds_out({"override": E.override_view(ov, shot, built, target)})


@handler
def delete_override(ctx: Context, query: dict):
    ep = check_ep(ctx, query.get("ep"))
    shot = check_shot(query.get("shot"))
    p = query.get("pass")
    pass_ = check_pass(p) if p else None
    built = E.pass_builds(ep, shot)
    target = (J.shotlist_target(J.load_shotlist(ep, next(iter(built)))).id if built
              else T.DEFAULT_TARGET)
    ov = T.load_overrides(ep)
    E.clear_shot_override(ov, shot, pass_, target)
    T.save_overrides(ep, ov)
    episode_event(ctx, ep)
    return 200, seeds_out({"override": E.override_view(ov, shot, built, target)})


# ---------------------------------------------------------------------------
# targets (Phase 7)
# ---------------------------------------------------------------------------

@handler
def get_targets(ctx: Context, query: dict):
    """Every video and image target: id, kind, label, presets, and the widgets
    its binding exposes (for pickers). `kind` narrows to one kind."""
    kind = query.get("kind") or None
    if kind is not None and kind not in TG.KINDS:
        raise ApiError(400, f"kind must be one of {', '.join(TG.KINDS)}, not {kind!r}")
    return 200, {"targets": [t.describe() for t in TG.list_targets(kind)],
                 "default": {"video": TG.DEFAULT_VIDEO_TARGET,
                             "image": TG.DEFAULT_IMAGE_TARGET}}


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


@handler
def get_refs(ctx: Context, query: dict):
    ep = check_ep(ctx, query.get("ep"))
    s = _series(ep)
    sweep_refs(ctx, s)
    return 200, seeds_out({"refs": R.list_refs(ep)})


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
    req = R.GenRequest(ref=ref.id, view=view, count=count, seed_mode=seed_mode,
                       seed=seed_in(body.get("seed")), prompt=prompt or None,
                       model=_opt_str(body, "model") or None,
                       loras=_opt_loras(body.get("loras")),
                       steps=_opt_steps(body.get("steps")), note=_opt_str(body, "note") or "")
    why = R.can_generate(s, ref)
    if why:
        raise ApiError(400, why)
    try:
        base, _ = R.resolve_workflow(ctx.comfy_url)
    except Exception as e:
        raise ApiError(500, f"{R.REFS_WORKFLOW} can't be read: {e}")
    try:
        result = R.queue_generate(s, req, ctx.comfy, base, save_node=True)
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
        R.pick_take(s, ref, view, take, force=force)
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
    return 200, seeds_out(R.ref_json(s, ref, R.used_by(s, [ref])))


@handler
def post_refs_import(ctx: Context, body):
    body = body_dict(body)
    ep = check_ep(ctx, body.get("ep"))
    s = _series(ep)
    ref = _ref(s, body.get("ref"))
    view = _view(ref, body.get("view"), required=True)
    src = body.get("source_path")
    if not isinstance(src, str) or not src.strip():
        raise ApiError(400, "source_path is required: the file's absolute path on this machine")
    try:
        t = R.import_take(s, ref, view, src.strip(), note=_opt_str(body, "note") or "")
    except R.RefError as e:
        raise ApiError(400, str(e))
    ref_event(ctx, ep, ref.id, view, t.take, t.status)
    episode_event(ctx, ep)
    return 200, seeds_out(R.take_json(ep, ref, t))


REF_OVERRIDE_FIELDS = ("prompt", "seed", "model", "loras", "steps", "note")


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


# (method, path, handler, what it takes: "query" or "body")
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
    ("PUT", "/h3pipe/pick", put_pick, "body"),
    ("PUT", "/h3pipe/cut", put_cut, "body"),
    ("PUT", "/h3pipe/override", put_override, "body"),
    ("DELETE", "/h3pipe/override", delete_override, "query"),
    ("POST", "/h3pipe/assemble", post_assemble, "body"),
    ("GET", "/h3pipe/targets", get_targets, "query"),
    ("GET", "/h3pipe/refs", get_refs, "query"),
    ("POST", "/h3pipe/refs/generate", post_refs_generate, "body"),
    ("PUT", "/h3pipe/refs/pick", put_refs_pick, "body"),
    ("POST", "/h3pipe/refs/import", post_refs_import, "body"),
    ("PUT", "/h3pipe/refs/override", put_refs_override, "body"),
    ("DELETE", "/h3pipe/refs/override", delete_refs_override, "query"),
]
