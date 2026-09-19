#!/usr/bin/env python3
"""
h3refs.py — references (refs) as takes, driven by the series config.

A ref is a conditioning input a render reads that the pipeline generates or
you supply (docs/API.md, "References"). Refs are listed from the series config, not
from refs_todo, so a character nobody uses yet can still be generated:

    subject:<id>            a character (four views, stitched into its sheet),
                            prop or vehicle: the series config's `sheet` path
    location:<id>           the location's `plate`
    voice:<id>              a subject's `voice_sample` (imported takes only)
    shot:<shot>:first|last  a shot's first / last keyframe, at
                            refs/shots/<shot>/<first|last>.png (storage, takes
                            and pick only: nothing generates keyframes yet)

Where things live. Series refs belong to the folder holding series.json (the
episode, or its parent when the series config is shared by a series); shot
keyframes belong to the episode. Call that folder the ref's home:

    <home>/<path series.json names>                 the live file renders read
    <home>/refs/_takes/<key>/<key>[_<view>]_tNN.png  a candidate (take)
    <home>/refs/_takes/<key>/<key>[_<view>]_tNN.json its sidecar
    <home>/refs/_picks.json                         which take is live
    <home>/refs/_overrides.json                     prompt/seed/model/... tweaks

The ref key is the id with ':' replaced by '__'. `ref_file` is the one helper
that turns a ref path into a file; everything here goes through it. Paths
handed to callers (the API) are relative to the episode, with forward slashes.

A take's sidecar follows h3takes' rules (exclusive-create reservation, atomic
updates, statuses queued/ok/failed). Queuer fields: version, ref, view, take,
ep, status, queued, comfy_prompt_id, source ("generated" | "imported"), seed,
seed_source, prompt, model, loras, steps, cfg, width, height, overrides, note.
The saver (the H3SaveRefTake node, or kreagen's fallback) sets status,
finished, image, width, height and save_notes.

Picking copies the take into the live path; a character's pick is per view,
and picking the view that completes the set stitches the sheet with mksheet
(run as a subprocess with this Python: mksheet needs PIL, the pipeline is
stdlib only).

The prompt wording for every ref lives in the image target
(targets/image/krea2/prompt.py), re-exported here: h3build writes the refs_todo
prompts with it (through the video target's ref requests), and kreagen and the
editor generate with it. The model stack and graph are that target's too.

Stdlib only.
"""
from __future__ import annotations

import glob
import hashlib
import os
import random
import re
import shutil
import struct
import sys
import uuid
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import h3edit as E  # noqa: E402
import h3jobs as J  # noqa: E402
import h3takes as T  # noqa: E402

# ---------------------------------------------------------------------------
# wording and the image model: the krea2 image target (targets/image/krea2)
# ---------------------------------------------------------------------------
#
# The wording of every reference prompt lives in targets/image/krea2/prompt.py,
# the model stack's names in its target.json, and the graph code in its
# graph.py. They are re-exported here under their old names (kreagen and the
# tests read them from h3refs).

import targets as TG  # noqa: E402
from targets import voice_prompt  # noqa: E402,F401
from targets.image.krea2.graph import (  # noqa: E402,F401
    CFG, CLIP, CLIPTYPE, LORA, LORA_C, LORA_M, OBJECT_SIZE, PLATE_SIZE, PREFIX,
    REFS_WORKFLOW, SAMPLER, SAVER, SCHED, STEPS, UNET, VAE, VIEW_SIZE, _one, _splice_lora,
    build_graph, patch_workflow, take_graph)
from targets.image.krea2.prompt import (  # noqa: E402,F401
    VIEW_DESC, VIEW_TAGS, VIEW_TMPL, VIEWS, object_prompt, plate_prompt, sheet_prompt,
    view_prompt)

IMAGE_TARGET = TG.load_target(TG.DEFAULT_IMAGE_TARGET, "image")
NEW_SEED_BITS = 53         # as h3jobs: a browser can hold these as numbers


def seed_for(key: str) -> int:
    """A ref's stable seed (48 bits): a character's id, else the ref's path."""
    return int(hashlib.sha1(key.encode()).hexdigest()[:12], 16)


def parse_size(target: str, default=(1024, 1024)) -> tuple[int, int]:
    m = re.search(r"(\d+)\s*[x×]\s*(\d+)", target or "")
    return (int(m.group(1)), int(m.group(2))) if m else default


# ---------------------------------------------------------------------------
# errors (the routes map them to 400 / 404 / 409 / 500)
# ---------------------------------------------------------------------------

class RefError(ValueError):
    """A request that makes no sense for this ref (400)."""


class UnknownRef(LookupError):
    """No such ref, view or take (404)."""


class NotUsable(Exception):
    """A pick of a take that isn't finished, or whose file is gone (409)."""


class StitchError(RuntimeError):
    """The views were picked but the sheet couldn't be stitched (500)."""


# ---------------------------------------------------------------------------
# the series: the series config and where its paths resolve
# ---------------------------------------------------------------------------

def episode_series_config(ep: str) -> str | None:
    """series.json in the episode folder, else in its parent (h3edit's rule)."""
    return E.episode_series_config(ep)


@dataclass
class Series:
    ep: str                 # the episode folder (absolute)
    config_file: str
    home: str               # the folder holding series.json: series ref paths start here
    series_cfg: dict

    @property
    def look(self) -> str:
        return (self.series_cfg.get("style") or {}).get("look", "")


def load_series(ep: str) -> Series:
    """The episode's series config, filtered as h3build reads it. FileNotFoundError if
    there is none; ValueError if it can't be read."""
    from h3core.series_config import load_series_config
    ep = os.path.abspath(ep)
    path = episode_series_config(ep)
    if not path:
        raise FileNotFoundError(f"no series.json in {ep} or its parent folder")
    return Series(ep, path, os.path.dirname(os.path.abspath(path)), load_series_config(path))


def ref_file(home: str, path: str) -> str:
    """THE rule for where a ref path points: relative to the ref's home (the
    series config's folder for series refs, the episode for shot keyframes), unless
    absolute. Every file this module reads or writes goes through it."""
    return os.path.normpath(path if os.path.isabs(path) else os.path.join(home, path))


def ep_rel(ep: str, path: str | None) -> str | None:
    """`path` relative to the episode, forward slashes (URL-ready). A ref under
    a parent-folder series config comes out as ../refs/..."""
    if not path:
        return None
    try:
        return os.path.relpath(path, ep).replace(os.sep, "/")
    except ValueError:                                    # another drive
        return path.replace(os.sep, "/")


# ---------------------------------------------------------------------------
# refs
# ---------------------------------------------------------------------------

@dataclass
class Ref:
    id: str
    scope: str              # series | shot
    kind: str               # character | prop | vehicle | ... | location | voice | keyframe
    name: str
    path: str               # as the series config names it (relative to `home`); "" if none
    home: str
    entry: dict = field(default_factory=dict)
    subject: str | None = None

    @property
    def key(self) -> str:
        return ref_key(self.id)

    @property
    def file(self) -> str | None:
        return ref_file(self.home, self.path) if self.path else None

    @property
    def has_views(self) -> bool:
        return self.kind == "character"

    @property
    def views(self) -> list[str | None]:
        return list(VIEW_TAGS) if self.has_views else [None]

    @property
    def is_audio(self) -> bool:
        return self.kind == "voice"


def ref_key(ref_id: str) -> str:
    """The folder/file-safe key of a ref id: ':' becomes '__'."""
    return T.safe_id(ref_id.replace(":", "__"))


def series_refs(s: Series) -> list[Ref]:
    """Every ref the series config names, subjects first (series config order), then locations,
    then voices."""
    out = []
    book = s.series_cfg["subjects"]
    for sid, e in book.items():
        kind = e.get("kind", "character")
        out.append(Ref(f"subject:{sid}", "series", kind, e.get("name", sid),
                       e.get("sheet") or "", s.home, e, sid))
    for lid, e in s.series_cfg["locations"].items():
        out.append(Ref(f"location:{lid}", "series", "location", e.get("name", lid),
                       e.get("plate") or "", s.home, e))
    for sid, e in book.items():
        if e.get("voice_sample"):
            out.append(Ref(f"voice:{sid}", "series", "voice", e.get("name", sid),
                           e["voice_sample"], s.home, e, sid))
    return out


KEYFRAME_ENDS = ("first", "last")


def keyframe_ref(ep: str, shot: str, end: str) -> Ref:
    return Ref(f"shot:{shot}:{end}", "shot", "keyframe", f"{shot} {end} frame",
               f"refs/shots/{T.safe_id(shot)}/{end}.png", ep)


def built_shot_ids(ep: str) -> list[str]:
    ids: list[str] = []
    for ps in T.PASSES:
        try:
            docs = J.load_shotlists(ep, ps)
        except (FileNotFoundError, ValueError):
            continue
        for doc in docs:
            ids += [sh["id"] for sh in doc.get("shots", []) if sh["id"] not in ids]
    return ids


def keyframe_refs(ep: str) -> list[Ref]:
    """The shot keyframes that exist: a live file or any take. (Every shot can
    have two; listing them all would bury the series refs.)"""
    found: dict[tuple[str, str], Ref] = {}
    ids = built_shot_ids(ep)
    by_safe = {T.safe_id(sid): sid for sid in ids}
    for sid in ids:
        for end in KEYFRAME_ENDS:
            r = keyframe_ref(ep, sid, end)
            if os.path.isfile(r.file) or glob.glob(os.path.join(takes_dir(r), "*.json")):
                found[(sid, end)] = r
    # takes for a shot no longer built still list, so nothing is lost
    for d in glob.glob(os.path.join(ep, "refs", "_takes", "shot__*")):
        m = re.fullmatch(r"shot__(.+)__(first|last)", os.path.basename(d))
        if m and glob.glob(os.path.join(d, "*.json")):
            sid = by_safe.get(m.group(1), m.group(1))
            found.setdefault((sid, m.group(2)), keyframe_ref(ep, sid, m.group(2)))
    return [found[k] for k in sorted(found, key=lambda k: (ids.index(k[0])
                                                          if k[0] in ids else 1 << 30,
                                                          k[0], k[1]))]


def find_ref(s: Series, ref_id) -> Ref:
    """The ref with this id, or UnknownRef. Keyframes need the shot in a build."""
    if not isinstance(ref_id, str) or not ref_id:
        raise RefError("ref is required, e.g. \"subject:dean\" or \"location:kitchen\"")
    m = re.fullmatch(r"shot:(.+):(first|last)", ref_id)
    if m:
        if m.group(1) not in built_shot_ids(s.ep):
            raise UnknownRef(f"{m.group(1)} is in no built shotlist of this episode")
        return keyframe_ref(s.ep, m.group(1), m.group(2))
    if ref_id.startswith("shot:"):
        raise RefError(f"a shot ref is shot:<shot>:first or shot:<shot>:last, not {ref_id!r}")
    for r in series_refs(s):
        if r.id == ref_id:
            return r
    if not re.match(r"(subject|location|voice):", ref_id):
        raise RefError(f"unknown kind of ref {ref_id!r}: subject:<id>, location:<id>, "
                       f"voice:<id> or shot:<shot>:first|last")
    raise UnknownRef(f"{ref_id} is not in {os.path.basename(s.config_file)}")


def check_view(ref: Ref, view, required: bool = False) -> str | None:
    """A view for this ref: one of VIEW_TAGS for a character, None otherwise."""
    if view in (None, ""):
        if required and ref.has_views:
            raise RefError(f"{ref.id} is a character: give a view "
                           f"({', '.join(VIEW_TAGS)})")
        return None
    if not ref.has_views:
        raise RefError(f"{ref.id} has no views (only characters do)")
    if view not in VIEW_TAGS:
        raise RefError(f"view must be one of {', '.join(VIEW_TAGS)}, not {view!r}")
    return view


# ---------------------------------------------------------------------------
# prompts, sizes and seeds as the series config gives them
# ---------------------------------------------------------------------------

def built_prompt(s: Series, ref: Ref, view: str | None = None,
                 view_size: tuple[int, int] = VIEW_SIZE) -> str | None:
    """The prompt the series config gives this ref (and view): what a generate uses
    unless overridden. None when the series config lacks what it needs. A character
    with no view gets its hand-made-sheet description (as in refs_todo)."""
    e = ref.entry
    if ref.kind == "keyframe":
        return None
    if ref.kind == "voice":
        return voice_prompt(e.get("name", ref.subject), e.get("voice", "as written in series.json"))
    if ref.kind == "location":
        return plate_prompt(s.look, e["description"]) if e.get("description") else None
    if not e.get("design"):
        return None
    if ref.kind == "character":
        if view is None:
            return sheet_prompt(e["design"], s.look)
        return view_prompt(view, e["design"], s.look, *view_size)
    return object_prompt(e["design"], s.look)


def gen_size(ref: Ref, view_size: tuple[int, int] = VIEW_SIZE) -> tuple[int, int]:
    if ref.kind == "character":
        return view_size
    if ref.kind == "location":
        return PLATE_SIZE
    return OBJECT_SIZE


def stable_seed(ref: Ref) -> int:
    """kreagen's seeds: a character's four views share seed_for(<id>); anything
    else uses seed_for(<the path the series config names>)."""
    if ref.kind == "character":
        return seed_for(ref.subject)
    return seed_for(ref.path or ref.id)


def can_generate(s: Series, ref: Ref) -> str | None:
    """None if a generate can run; else why not, for a person."""
    if ref.kind == "voice":
        return "nothing generates voices yet: import a recording"
    if ref.kind == "keyframe":
        return "nothing generates keyframes yet: import an image"
    if built_prompt(s, ref, VIEW_TAGS[0] if ref.has_views else None) is None:
        what = "description" if ref.kind == "location" else "design"
        return f"no {what} in {os.path.basename(s.config_file)} for {ref.id}"
    return None


# ---------------------------------------------------------------------------
# takes
# ---------------------------------------------------------------------------

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")
AUDIO_EXTS = (".wav", ".mp3", ".flac", ".ogg", ".m4a")


@dataclass
class RefPaths:
    sidecar: str
    image: str               # the take's file (an image, or audio for a voice)


@dataclass
class RefTake:
    ref: str
    view: str | None
    take: int
    paths: RefPaths
    sidecar: dict | None = None

    @property
    def status(self) -> str:
        return (self.sidecar or {}).get("status", "queued")

    @property
    def usable(self) -> bool:
        return self.status == "ok" and os.path.isfile(self.paths.image)


def takes_dir(ref: Ref) -> str:
    return os.path.join(ref.home, "refs", "_takes", ref.key)


def take_base(ref: Ref, view: str | None) -> str:
    return f"{ref.key}_{view}" if view else ref.key


def _take_from(ref: Ref, view: str | None, n: int, data: dict | None) -> RefTake:
    d, base = takes_dir(ref), take_base(ref, view)
    name = (data or {}).get("image") or f"{base}_t{n:02d}.png"
    return RefTake(ref.id, view, n, RefPaths(os.path.join(d, f"{base}_t{n:02d}.json"),
                                             os.path.join(d, name)), data)


def take_numbers(ref: Ref, view: str | None) -> list[int]:
    """Every take number with a sidecar or a file, ascending."""
    d, base = takes_dir(ref), take_base(ref, view)
    nums = set()
    for p in glob.glob(os.path.join(d, f"{base}_t*")):
        m = re.fullmatch(rf"{re.escape(base)}_t(\d+)\.[A-Za-z0-9]+", os.path.basename(p))
        if m:
            nums.add(int(m.group(1)))
    return sorted(nums)


def list_takes(ref: Ref, view: str | None = None) -> list[RefTake]:
    out = []
    for n in take_numbers(ref, view):
        t = _take_from(ref, view, n, None)
        out.append(_take_from(ref, view, n, T.read_sidecar(t.paths.sidecar)))
    return out


def get_take(ref: Ref, view: str | None, take: int) -> RefTake:
    t = next((t for t in list_takes(ref, view) if t.take == take), None)
    if t is None:
        raise UnknownRef(f"{ref.id}{' ' + view if view else ''} has no take {take}")
    return t


def reserve_take(ref: Ref, view: str | None, sidecar: dict,
                 ext: str = ".png") -> RefTake:
    """Claim the next take number for (ref, view) by creating its sidecar."""
    d, base = takes_dir(ref), take_base(ref, view)
    os.makedirs(d, exist_ok=True)
    nums = take_numbers(ref, view)
    n, data = T.claim_number(
        (nums[-1] + 1) if nums else 1,
        lambda k: os.path.join(d, f"{base}_t{k:02d}.json"),
        lambda k: dict(sidecar, version=T.SIDECAR_VERSION, ref=ref.id, view=view, take=k,
                       image=f"{base}_t{k:02d}{ext}"))
    return _take_from(ref, view, n, data)


def mark_queued(take: RefTake, prompt_id: str) -> None:
    take.sidecar = T.update_sidecar(take.paths.sidecar, comfy_prompt_id=prompt_id)


def mark_failed(take: RefTake, why: str) -> None:
    take.sidecar = T.update_sidecar(take.paths.sidecar, status="failed",
                                    finished=T.now(), save_notes=why)


def image_size(path: str) -> tuple[int, int] | None:
    """(width, height) of a PNG or JPEG from its header; None otherwise."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(32)
            if head[:8] == b"\x89PNG\r\n\x1a\n" and head[12:16] == b"IHDR":
                return struct.unpack(">II", head[16:24])
            if head[:2] == b"\xff\xd8":
                fh.seek(2)
                while True:
                    b = fh.read(1)
                    while b and b != b"\xff":
                        b = fh.read(1)
                    while b == b"\xff":
                        b = fh.read(1)
                    if not b:
                        return None
                    if b[0] in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                                0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                        fh.read(3)
                        h, w = struct.unpack(">HH", fh.read(4))
                        return w, h
                    seg = struct.unpack(">H", fh.read(2))[0]
                    fh.seek(seg - 2, 1)
    except (OSError, struct.error):
        return None
    return None


def close_take(take: RefTake, data: bytes | None = None, notes: str = "") -> str:
    """Close a take from outside the save node: write `data` as its image (if
    given) and set ok, or failed when there is no image. For kreagen's
    SaveImage fallback and for a job whose saver didn't update the sidecar."""
    if data is not None:
        _atomic_write(take.paths.image, data)
    if os.path.isfile(take.paths.image):
        wh = image_size(take.paths.image) or (None, None)
        take.sidecar = T.update_sidecar(
            take.paths.sidecar, status="ok", finished=T.now(),
            image=os.path.basename(take.paths.image), width=wh[0], height=wh[1],
            save_notes=notes)
        return "ok"
    mark_failed(take, notes or "ComfyUI finished but no image was saved")
    return "failed"


def _atomic_write(path: str, data: bytes) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = os.path.join(os.path.dirname(os.path.abspath(path)),
                       f".tmp_{uuid.uuid4().hex}_{os.path.basename(path)}")
    try:
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _atomic_copy(src: str, dst: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    tmp = os.path.join(os.path.dirname(os.path.abspath(dst)),
                       f".tmp_{uuid.uuid4().hex}_{os.path.basename(dst)}")
    try:
        shutil.copyfile(src, tmp)
        os.replace(tmp, dst)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# overrides: <home>/refs/_overrides.json
# ---------------------------------------------------------------------------
#
# {"refs": {"location:kitchen": {"prompt": "…", "base_hash": "…", "seed": 12,
#                                "model": null, "loras": null, "steps": null, "note": ""},
#           "subject:dean": {"seed": 5, "views": {"02_side": {"prompt": "…",
#                                                             "base_hash": "…"}}}}}
#
# The same fields as a shot override. A view's fields beat the ref's. A
# character's prompt is per view (one text can't describe four views).
# `base_hash` is the hash of the built prompt the override was written
# against; when the series config changes that prompt, the override is stale.

OVERRIDES_FILE = os.path.join("refs", "_overrides.json")
PICKS_FILE = os.path.join("refs", "_picks.json")
OVERRIDE_FIELDS = ("prompt", "seed", "model", "loras", "steps", "note")


def load_overrides(home: str) -> dict:
    data = T.read_json(os.path.join(home, OVERRIDES_FILE)) or {}
    data.setdefault("refs", {})
    return data


def save_overrides(home: str, data: dict) -> None:
    T.write_json(os.path.join(home, OVERRIDES_FILE), data)


def prompt_hash(prompt) -> str | None:
    return T.content_hash(prompt) if prompt is not None else None


def ref_override(data: dict, ref_id: str, view: str | None = None) -> dict:
    """The effective override: the ref's non-null fields, then the view's."""
    block = data.get("refs", {}).get(ref_id, {})
    out = {k: v for k, v in block.items()
           if k in OVERRIDE_FIELDS + ("base_hash",) and v is not None}
    if view:
        vb = (block.get("views") or {}).get(view, {})
        if "prompt" in vb or "base_hash" in vb:
            out.pop("base_hash", None)
        out.update({k: v for k, v in vb.items()
                    if k in OVERRIDE_FIELDS + ("base_hash",) and v is not None})
    return out


def set_ref_override(data: dict, ref: Ref, view: str | None, fields: dict,
                     base_hash: str | None) -> dict:
    """Change fields at the ref's level (view None) or one view's. None clears a
    field. Setting a prompt stamps `base_hash`; clearing it drops the stamp."""
    unknown = set(fields) - set(OVERRIDE_FIELDS)
    if unknown:
        raise RefError(f"unknown override field(s) {', '.join(sorted(unknown))}: "
                       f"one of {', '.join(OVERRIDE_FIELDS)}")
    if ref.has_views and view is None and fields.get("prompt") is not None:
        raise RefError(f"{ref.id} is a character: a prompt override is per view")
    refs = data.setdefault("refs", {})
    block = refs.setdefault(ref.id, {})
    dst = block.setdefault("views", {}).setdefault(view, {}) if view else block
    for k, v in fields.items():
        if v is None:
            dst.pop(k, None)
        else:
            dst[k] = v
    if "prompt" in fields:
        if fields["prompt"] is None:
            dst.pop("base_hash", None)
        elif base_hash:
            dst["base_hash"] = base_hash
    _tidy(refs, ref.id)
    return data


def clear_ref_override(data: dict, ref_id: str, view: str | None = None) -> dict:
    """Drop one view's override, or with no view the ref's whole override."""
    refs = data.setdefault("refs", {})
    if ref_id not in refs:
        return data
    if view:
        (refs[ref_id].get("views") or {}).pop(view, None)
    else:
        del refs[ref_id]
    _tidy(refs, ref_id)
    return data


def _tidy(refs: dict, ref_id: str) -> None:
    block = refs.get(ref_id)
    if block is None:
        return
    views = block.get("views") or {}
    for v in [v for v, b in views.items() if not (set(b) - {"base_hash"})]:
        del views[v]
    if not views:
        block.pop("views", None)
    if not (set(block) - {"base_hash"}):
        del refs[ref_id]


def override_view(s: Series, ref: Ref, view: str | None, data: dict,
                  view_size=VIEW_SIZE) -> dict:
    """{fields: [...], stale: bool} for the listing; `values` has the fields."""
    eff = ref_override(data, ref.id, view)
    stale = False
    if eff.get("base_hash") and "prompt" in eff:
        stale = eff["base_hash"] != prompt_hash(built_prompt(s, ref, view, view_size))
    values = {k: v for k, v in eff.items() if k != "base_hash"}
    return {"fields": sorted(values), "stale": stale, "values": values}


# ---------------------------------------------------------------------------
# picks: <home>/refs/_picks.json
# ---------------------------------------------------------------------------
#
# {"version": 1,
#  "refs": {"location:kitchen": {"take": 3, "sha1": "…", "picked": "…"},
#           "subject:dean": {"views": {"01_threequarter": {"take": 2, "sha1": "…",
#                                                          "picked": "…"}, …},
#                            "sha1": "<the stitched sheet>", "stitched": "…"}}}
#
# sha1 is the live file's as the pick (or stitch) wrote it.

def load_picks(home: str) -> dict:
    data = T.read_json(os.path.join(home, PICKS_FILE)) or {}
    data.setdefault("version", 1)
    data.setdefault("refs", {})
    return data


def save_picks(home: str, data: dict) -> None:
    T.write_json(os.path.join(home, PICKS_FILE), data)


def picked_take(picks: dict, ref_id: str, view: str | None = None) -> int | None:
    block = picks.get("refs", {}).get(ref_id) or {}
    if view:
        block = (block.get("views") or {}).get(view) or {}
    return block.get("take")


@dataclass
class PickResult:
    take: RefTake
    live: str | None = None          # the file written, if any
    stitched: bool = False
    report: str = ""


def pick_take(s: Series, ref: Ref, view: str | None, take: int,
              force: bool = False, panel_height: int = 1024,
              mksheet: str | None = None) -> PickResult:
    """Make `take` the live one: copy it into place (a character's view is
    recorded, and the sheet stitched once all four views are picked) and write
    _picks.json. Raises UnknownRef, NotUsable (unless `force` for a finished
    take whose status isn't ok), RefError, StitchError (after the pick is
    saved)."""
    view = check_view(ref, view, required=True)
    t = get_take(ref, view, take)
    if not os.path.isfile(t.paths.image):
        raise NotUsable(f"{ref.id}{' ' + view if view else ''} t{take:02d} has no file")
    if t.status != "ok" and not force:
        raise NotUsable(f"{ref.id}{' ' + view if view else ''} t{take:02d} is {t.status}")
    if not ref.path:
        raise RefError(f"{os.path.basename(s.config_file) if ref.scope == 'series' else 'the episode'}"
                       f" names no file for {ref.id}")
    picks = load_picks(ref.home)
    block = picks["refs"].setdefault(ref.id, {})
    res = PickResult(t)
    if view:
        block.setdefault("views", {})[view] = {
            "take": take, "sha1": T.file_sha1(t.paths.image), "picked": T.now()}
        save_picks(ref.home, picks)
        chosen = block["views"]
        if all(v in chosen for v in VIEW_TAGS):
            files = []
            for v in VIEW_TAGS:
                vt = next((x for x in list_takes(ref, v) if x.take == chosen[v]["take"]), None)
                if vt is None or not os.path.isfile(vt.paths.image):
                    raise StitchError(f"picked, but {v} t{chosen[v]['take']:02d} has no "
                                      f"image to stitch")
                files.append(vt.paths.image)
            res.report = stitch_sheet(files, ref.file, panel_height, mksheet=mksheet)
            block["sha1"], block["stitched"] = T.file_sha1(ref.file), T.now()
            res.live, res.stitched = ref.file, True
            save_picks(ref.home, picks)
        return res
    _atomic_copy(t.paths.image, ref.file)
    block.update(take=take, sha1=T.file_sha1(ref.file), picked=T.now())
    save_picks(ref.home, picks)
    res.live = ref.file
    return res


def auto_pick(s: Series, ref: Ref, mksheet: str | None = None) -> list[PickResult]:
    """Put a ref that has NO live file yet on its first usable candidate: per
    view for a character, whose sheet is stitched once all four views have a
    pick. A ref whose live file exists is never touched (new candidates wait
    for an explicit pick), and nor is a view already picked. Voices aren't
    auto-picked (audio is only ever imported). This is what kreagen has always
    done for a missing file, so the editor and the CLI end up the same."""
    if ref.is_audio or not ref.path or os.path.isfile(ref.file):
        return []
    out = []
    for v in (VIEW_TAGS if ref.has_views else [None]):
        if picked_take(load_picks(ref.home), ref.id, v) is not None:
            continue
        ok = [t for t in list_takes(ref, v)
              if t.status == "ok" and os.path.isfile(t.paths.image)]
        if ok:
            out.append(pick_take(s, ref, v, ok[0].take, mksheet=mksheet))
    return out


def mksheet_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "mksheet.py")


def stitch_sheet(views: list[str], out: str, panel_height: int = 1024,
                 timeout: int = 300, mksheet: str | None = None) -> str:
    """Stitch four view images into `out` with mksheet.build, run as a
    subprocess of this Python (mksheet needs PIL; this module is stdlib only).
    Written to a temp file and moved into place. Returns mksheet's report."""
    os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
    tmp = os.path.join(os.path.dirname(os.path.abspath(out)),
                       f".tmp_{uuid.uuid4().hex}_{os.path.basename(out)}")
    try:
        rc, so, se = E.run_tool(os.path.abspath(mksheet or mksheet_path()),
                                     [*views, "-o", tmp, "--panel-height", str(panel_height)],
                                     os.path.dirname(os.path.abspath(out)), timeout)
        if not os.path.isfile(tmp):
            if "No module named 'PIL'" in se:
                raise StitchError(f"stitching a sheet needs PIL, and {sys.executable} can't "
                                  f"import it (pip install pillow)")
            raise StitchError("mksheet failed: " + (se.strip() or so.strip())[-600:])
        os.replace(tmp, out)
    finally:
        if os.path.isfile(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    return so


# ---------------------------------------------------------------------------
# import
# ---------------------------------------------------------------------------

def import_take(s: Series, ref: Ref, view: str | None, source_path: str,
                note: str = "") -> RefTake:
    """Add a file from disk as a new take (source "imported"), status ok."""
    view = check_view(ref, view, required=True)
    if not isinstance(source_path, str) or not source_path:
        raise RefError("source_path is required: a file on this machine")
    if not os.path.isabs(source_path):
        raise RefError(f"source_path must be absolute, not {source_path!r}")
    if not os.path.isfile(source_path):
        raise FileNotFoundError(f"no file at {source_path}")
    ext = os.path.splitext(source_path)[1].lower()
    allowed = AUDIO_EXTS if ref.is_audio else IMAGE_EXTS
    if ext not in allowed:
        raise RefError(f"{ref.id} takes {'audio' if ref.is_audio else 'an image'} "
                       f"({', '.join(allowed)}), not {ext or 'a file with no extension'}")
    t = reserve_take(ref, view, {"status": "queued", "queued": T.now(), "ep": s.ep,
                                 "source": "imported", "source_path": source_path,
                                 "comfy_prompt_id": None, "seed": None, "seed_source": None,
                                 "prompt": None, "model": None, "loras": None,
                                 "steps": None, "note": note}, ext=ext)
    try:
        _atomic_copy(source_path, t.paths.image)
    except BaseException as e:
        mark_failed(t, f"import failed: {e}")
        raise
    wh = (None, None) if ref.is_audio else (image_size(t.paths.image) or (None, None))
    t.sidecar = T.update_sidecar(t.paths.sidecar, status="ok", finished=T.now(),
                                 width=wh[0], height=wh[1],
                                 save_notes=f"imported from {source_path}")
    return t


# ---------------------------------------------------------------------------
# generating
# ---------------------------------------------------------------------------

@dataclass
class GenRequest:
    ref: str
    view: str | None = None
    count: int = 1
    seed_mode: str = "auto"          # auto | new | same
    seed: int | None = None          # typed
    prompt: str | None = None
    model: str | None = None
    loras: list[dict] | None = None
    steps: int | None = None
    cfg: float = CFG
    negative: str = ""
    note: str = ""
    view_size: tuple[int, int] = VIEW_SIZE


@dataclass
class GenJob:
    ref: Ref
    view: str | None
    candidate: int                   # 0-based, within one request
    prompt: str
    seed: int
    seed_source: str
    model: str                       # "" leaves the workflow's model alone
    loras: list[dict] | None         # None leaves the workflow's LoRA alone
    steps: int
    cfg: float
    negative: str
    width: int
    height: int
    note: str = ""
    overridden: list[str] = field(default_factory=list)
    override_stale: bool = False

    @property
    def name(self) -> str:
        return f"{self.ref.id}:{self.view}" if self.view else self.ref.id


def plan_generate(s: Series, req: GenRequest, overrides: dict | None = None,
                  rng: random.Random | None = None) -> list[GenJob]:
    """The jobs one generate request makes: `count` candidates, each one image,
    or four (one per view, sharing a seed) for a character with no view.

    Seeds: typed > pinned in the override (unless seed_mode "new") > "same"
    (the ref's stable seed) > "new" > auto: the stable seed while the ref (the
    views concerned) has no usable take, else a new one. Candidates after the
    first always get new seeds.
    """
    ref = find_ref(s, req.ref)
    why = can_generate(s, ref)
    if why:
        raise RefError(why)
    view = check_view(ref, req.view)
    views = [view] if (view or not ref.has_views) else list(VIEW_TAGS)
    if req.prompt is not None and ref.has_views and view is None:
        raise RefError(f"{ref.id} is a character: a prompt goes to one view at a time")
    if not isinstance(req.count, int) or isinstance(req.count, bool) or not 1 <= req.count <= 16:
        raise RefError("count must be a whole number from 1 to 16")
    if req.seed_mode not in ("auto", "new", "same"):
        raise RefError(f"seed_mode must be auto, new or same, not {req.seed_mode!r}")
    ov_data = overrides if overrides is not None else load_overrides(ref.home)
    rng = rng or random.SystemRandom()
    shared_ov = ref_override(ov_data, ref.id, view)       # view None: the ref's level
    has_usable = any(t.usable for v in views for t in list_takes(ref, v))
    w, h = gen_size(ref, req.view_size)

    jobs = []
    for c in range(req.count):
        overridden = []
        if c == 0 and req.seed is not None:
            seed, source = int(req.seed), "typed"
        elif c == 0 and "seed" in shared_ov and req.seed_mode != "new":
            seed, source = int(shared_ov["seed"]), "override"
            overridden.append("seed")
        elif c == 0 and req.seed_mode == "same":
            seed, source = stable_seed(ref), "same"
        elif c == 0 and req.seed_mode == "auto" and not has_usable:
            seed, source = stable_seed(ref), "stable"
        else:
            seed, source = rng.getrandbits(NEW_SEED_BITS), "new"
        for v in views:
            ov = ref_override(ov_data, ref.id, v)
            used = list(overridden)

            def pick(name, built, from_req):
                if from_req is not None:
                    return from_req
                if name in ov:
                    used.append(name)
                    return ov[name]
                return built

            base_prompt = built_prompt(s, ref, v, req.view_size)
            prompt = pick("prompt", base_prompt, req.prompt)
            stale = ("prompt" in used and bool(ov.get("base_hash"))
                     and ov["base_hash"] != prompt_hash(base_prompt))
            jobs.append(GenJob(
                ref=ref, view=v, candidate=c, prompt=prompt, seed=seed, seed_source=source,
                model=pick("model", "", req.model) or "",
                loras=pick("loras", None, req.loras),
                steps=int(pick("steps", STEPS, req.steps)), cfg=req.cfg,
                negative=req.negative, width=w, height=h,
                note=req.note or "", overridden=sorted(set(used)), override_stale=stale))
    return jobs


def start_gen(s: Series, job: GenJob) -> RefTake:
    """Reserve the take and write its `queued` sidecar. No ComfyUI yet."""
    return reserve_take(job.ref, job.view, {
        "status": "queued", "queued": T.now(), "ep": s.ep, "comfy_prompt_id": None,
        "source": "generated", "seed": job.seed, "seed_source": job.seed_source,
        "prompt": job.prompt, "model": job.model, "loras": job.loras, "steps": job.steps,
        "cfg": job.cfg, "width": job.width, "height": job.height,
        "overrides": job.overridden, "override_stale": job.override_stale,
        "note": job.note})


def graph_for(base: dict | None, job: GenJob, take: RefTake, save_node: bool = True,
              lora_clip: bool = True) -> dict:
    """The API graph for one take: `base` (a loaded krea2_refs_t2i workflow)
    patched with this job's values, or the built-in graph when `base` is None.
    With `save_node` the SaveImage becomes an H3SaveRefTake writing into the
    take (the sidecar path is absolute); without, SaveImage stays (prefix
    PREFIX) and the caller fetches the image (kreagen on an older node pack).
    The graph code is the image target's (targets/image/krea2/graph.py)."""
    return take_graph(base, job.prompt, job.negative, job.width, job.height, job.seed,
                      job.steps, job.cfg, job.model, job.loras,
                      take.paths.sidecar if save_node else None, lora_clip)


def resolve_workflow(comfy_url: str | None, explicit: str | None = None):
    """(krea2_refs_t2i graph or None, where) — None means the built-in graph."""
    b = IMAGE_TARGET.binding
    return J.resolve_workflow(explicit, b.workflow_name, comfy_url, env=b.env,
                              required=False)


def queue_generate(s: Series, req: GenRequest, comfy, base: dict | None,
                   save_node: bool = True, lora_clip: bool = True,
                   rng: random.Random | None = None) -> dict:
    """Plan, reserve and queue every take a request makes, without waiting.

    Returns {"queued": [{ref, view, take, prompt_id, seed, seed_source}],
    "errors": [{ref, view, take?, error}]}; a take that fails to queue is
    marked failed and the others still go. Raises RefError / UnknownRef for a
    request that can't be planned at all."""
    jobs = plan_generate(s, req, rng=rng)
    out = {"queued": [], "errors": []}
    for job in jobs:
        try:
            take = start_gen(s, job)
        except Exception as e:
            out["errors"].append({"ref": job.ref.id, "view": job.view, "error": str(e)[:800]})
            continue
        try:
            pid = comfy.queue(graph_for(base, job, take, save_node, lora_clip))
            mark_queued(take, pid)
        except Exception as e:
            mark_failed(take, str(e)[:800])
            out["errors"].append({"ref": job.ref.id, "view": job.view, "take": take.take,
                                  "error": str(e)[:800]})
            continue
        out["queued"].append({"ref": job.ref.id, "view": job.view, "take": take.take,
                              "prompt_id": pid, "seed": job.seed,
                              "seed_source": job.seed_source})
    return out


def finish_from_history(take: RefTake, entry: dict | None, comfy=None) -> str:
    """Close a take whose job has finished, from ComfyUI's /history entry: an
    execution error marks it failed; success with the sidecar still queued
    (SaveImage instead of H3SaveRefTake) fetches the image from the job's
    outputs when `comfy` can (`view(img)`), else closes it from disk."""
    sc = T.read_sidecar(take.paths.sidecar) or {}
    take.sidecar = sc
    if sc.get("status") != "queued":
        return sc.get("status", "?")
    err = J.execution_error(entry)
    if err is not None:
        mark_failed(take, err)
        return "failed"
    imgs = [i for o in ((entry or {}).get("outputs") or {}).values()
            for i in (o.get("images") or [])]
    if imgs and comfy is not None and hasattr(comfy, "view"):
        return close_take(take, comfy.view(imgs[0]),
                          "fetched from ComfyUI's output folder: the graph's SaveImage "
                          f"wrote {imgs[0].get('filename')}")
    return close_take(take, None, "closed by h3refs: the saver did not update the sidecar")


def wait_take(comfy, take: RefTake, pid: str, timeout: float, poll: float = 1.5) -> str:
    """Wait for a queued take (kreagen: the CLI must work without the editor).
    Done when the sidecar leaves `queued` (the save node closed it) or the job
    is in /history. Returns the take's final status."""
    import time
    t0 = time.time()
    while time.time() - t0 < timeout:
        sc = T.read_sidecar(take.paths.sidecar) or {}
        if sc.get("status") in ("ok", "failed"):
            take.sidecar = sc
            return sc["status"]
        try:
            entry = comfy.history(pid)
        except Exception:                                 # a blip: ask again
            entry = None
        if _finished(entry):
            return finish_from_history(take, entry, comfy)
        time.sleep(poll)
    mark_failed(take, f"no result after {timeout:.0f}s")
    return "failed"


def _finished(entry: dict | None) -> bool:
    """A /history entry for a job that is over, either way."""
    return entry is not None and (J.execution_done(entry)
                                  or J.execution_error(entry) is not None)


# ---------------------------------------------------------------------------
# sweeping: queued takes whose job is gone
# ---------------------------------------------------------------------------

def homes(s: Series) -> list[str]:
    """Every folder whose refs/ this episode's refs live under."""
    out = [s.home]
    if os.path.normcase(os.path.abspath(s.ep)) != os.path.normcase(s.home):
        out.append(s.ep)
    return out


def all_takes(s: Series) -> list[RefTake]:
    """Every ref take under this episode's homes (any ref, known or not)."""
    out = []
    for home in homes(s):
        for sc_path in glob.glob(os.path.join(home, "refs", "_takes", "*", "*_t*.json")):
            data = T.read_sidecar(sc_path)
            m = re.search(r"_t(\d+)\.json$", sc_path)
            if not m:
                continue
            name = (data or {}).get("image") or os.path.basename(sc_path)[:-5] + ".png"
            out.append(RefTake((data or {}).get("ref", ""), (data or {}).get("view"),
                               int(m.group(1)),
                               RefPaths(sc_path, os.path.join(os.path.dirname(sc_path), name)),
                               data))
    return out


def sweep(s: Series, comfy) -> list[RefTake]:
    """Close queued ref takes whose ComfyUI job is gone (h3edit.sweep_takes'
    rules). Raises if ComfyUI can't be reached."""
    as_of = T.now()
    alive = comfy.alive()
    changed, rest = [], []
    for t in all_takes(s):
        sc = t.sidecar or {}
        pid = sc.get("comfy_prompt_id")
        if sc.get("status") != "queued" or not pid or pid in alive:
            rest.append(t)
            continue
        try:
            entry = comfy.history(pid)
        except Exception:
            entry = None
        if _finished(entry):
            finish_from_history(t, entry, comfy)
            changed.append(t)
        else:
            rest.append(t)
    changed += T.sweep_queued(rest, alive, as_of=as_of)
    return changed


# ---------------------------------------------------------------------------
# usage: which shots read each ref
# ---------------------------------------------------------------------------

def _norm(p: str) -> str:
    return os.path.normcase(os.path.normpath(p)) if p else ""


def used_by(s: Series, refs: list[Ref]) -> dict[str, dict[str, list[str]]]:
    """{ref id: {pass: [shot ids]}} from each pass's built shotlist (the slots
    the loader fills, h3jobs.ref_slots), else that pass's refs_todo, else []."""
    by_path: dict[str, list[str]] = {}
    for r in refs:
        if r.path:
            by_path.setdefault(_norm(r.path), []).append(r.id)
    ids = {r.id for r in refs}
    out = {r.id: {p: [] for p in T.PASSES} for r in refs}

    def add(ref_id, pass_, shot):
        if ref_id in ids and shot not in out[ref_id][pass_]:
            out[ref_id][pass_].append(shot)

    for ps in T.PASSES:
        try:
            docs = J.load_shotlists(s.ep, ps)
        except FileNotFoundError:
            docs = None
        if docs is not None:
            for doc, shot in ((d, sh) for d in docs for sh in d.get("shots", [])):
                for slot in J.ref_slots(doc, shot):
                    if slot.get("subject") and slot["kind"] == "image":
                        add(f"subject:{slot['subject']}", ps, shot["id"])
                    elif slot.get("subject") and slot["kind"] == "audio":
                        add(f"voice:{slot['subject']}", ps, shot["id"])
                    else:
                        for rid in by_path.get(_norm(slot.get("path", "")), []):
                            add(rid, ps, shot["id"])
            continue
        todo = T.read_json(os.path.join(s.ep, f"refs_todo{'_proxy' if ps == 'proxy' else ''}.json"))
        for item in todo or []:
            for rid in by_path.get(_norm(item.get("path", "")), []):
                for sh in item.get("blocks_shots", []):
                    add(rid, ps, sh)
    return out


# ---------------------------------------------------------------------------
# the listing (GET /h3pipe/refs)
# ---------------------------------------------------------------------------

def take_json(ep: str, ref: Ref, t: RefTake) -> dict:
    sc = t.sidecar or {}
    have = os.path.isfile(t.paths.image)
    f = ep_rel(ep, t.paths.image) if have else None
    return {"take": t.take, "view": t.view, "status": t.status, "usable": t.usable,
            "seed": sc.get("seed"), "seed_source": sc.get("seed_source"),
            "image": None if ref.is_audio else f,
            "audio": f if ref.is_audio else None,
            "source": sc.get("source", "generated"), "note": sc.get("note", ""),
            "prompt": sc.get("prompt"), "model": sc.get("model"), "loras": sc.get("loras"),
            "steps": sc.get("steps"), "width": sc.get("width"), "height": sc.get("height"),
            "overrides": sc.get("overrides", []),
            "queued": sc.get("queued"), "finished": sc.get("finished"),
            "comfy_prompt_id": sc.get("comfy_prompt_id"),
            "save_notes": sc.get("save_notes", "")}


def effective(s: Series, ref: Ref, view: str | None, ov_data: dict,
              view_size=VIEW_SIZE) -> dict | None:
    """What a generate (seed_mode auto, nothing else set) would use now."""
    if can_generate(s, ref):
        return None
    try:
        (job, *_) = plan_generate(s, GenRequest(ref.id, view, view_size=view_size),
                                  ov_data, rng=random.Random(0))
    except (RefError, UnknownRef):
        return None
    return {"prompt": job.prompt, "seed": job.seed if job.seed_source != "new" else None,
            "seed_source": job.seed_source, "model": job.model, "loras": job.loras,
            "steps": job.steps, "width": job.width, "height": job.height}


def ref_json(s: Series, ref: Ref, usage: dict | None = None,
             ov_data: dict | None = None, picks: dict | None = None,
             view_size=VIEW_SIZE) -> dict:
    ov_data = ov_data if ov_data is not None else load_overrides(ref.home)
    picks = picks if picks is not None else load_picks(ref.home)
    live = ref.file
    exists = bool(live and os.path.isfile(live))
    o = override_view(s, ref, None, ov_data, view_size)
    out = {
        "id": ref.id, "key": ref.key, "scope": ref.scope, "kind": ref.kind,
        "name": ref.name, "subject": ref.subject,
        "path": ep_rel(s.ep, live),
        "exists": exists, "sha1": T.file_sha1(live) if exists else None,
        "used_by": (usage or {}).get(ref.id) or {p: [] for p in T.PASSES},
        # a character's own prompt is its hand-made-sheet description (as in
        # refs_todo); what each view generates with is in its `views` entry
        "prompt": built_prompt(s, ref, None, view_size),
        "can_generate": can_generate(s, ref) is None,
        "why_not": can_generate(s, ref),
        "override": {"fields": o["fields"], "stale": o["stale"], "values": o["values"]},
        "views": [], "takes": [], "picked": None, "effective": None,
    }
    if ref.has_views:
        for v in VIEW_TAGS:
            vo = override_view(s, ref, v, ov_data, view_size)
            eff = effective(s, ref, v, ov_data, view_size)
            out["views"].append({
                "view": v, "picked": picked_take(picks, ref.id, v),
                "prompt": eff["prompt"] if eff else built_prompt(s, ref, v, view_size),
                "override": {"fields": vo["fields"], "stale": vo["stale"],
                             "values": vo["values"]},
                "effective": eff,
                "takes": [take_json(s.ep, ref, t) for t in list_takes(ref, v)]})
    else:
        out["takes"] = [take_json(s.ep, ref, t) for t in list_takes(ref)]
        out["picked"] = picked_take(picks, ref.id)
        out["effective"] = effective(s, ref, None, ov_data, view_size)
        if out["effective"]:
            out["prompt"] = out["effective"]["prompt"]
    # flat aliases the editor reads (docs/API.md): the override's values, and
    # the series config's prompt before any override, for the prompt diff
    out["override_values"] = out["override"]["values"]
    out["built_prompt"] = built_prompt(s, ref, None, view_size)
    return out


def list_refs(ep: str, view_size=VIEW_SIZE) -> list[dict]:
    """Every ref of the episode's series config, then the shot keyframes that exist."""
    s = load_series(ep)
    refs = series_refs(s) + keyframe_refs(s.ep)
    usage = used_by(s, refs)
    cache: dict[str, tuple[dict, dict]] = {}
    out = []
    for r in refs:
        if r.home not in cache:
            cache[r.home] = (load_overrides(r.home), load_picks(r.home))
        ov, pk = cache[r.home]
        out.append(ref_json(s, r, usage, ov, pk, view_size))
    return out


def get_ref_json(ep: str, ref_id: str) -> dict:
    s = load_series(ep)
    ref = find_ref(s, ref_id)
    return ref_json(s, ref, used_by(s, [ref]))
