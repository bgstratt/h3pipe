#!/usr/bin/env python3
"""
h3takes.py — the on-disk contract for takes, overrides and the cut.

Everything that reads or writes a take, `overrides.json` or `cut.json` goes
through here: h3render, h3assemble, h3.py and (later) the ComfyUI routes. The
one exception is the H3SaveShot node, which runs inside ComfyUI where this file
is not importable; it follows the sidecar rules below by hand.

A take of shot `sh020` in pass `final` lives in <episode>/renders/sh020/:

    sh020_t03.json            sidecar: provenance + status (see SIDECAR below)
    sh020_t03.shotlist.json   frozen one-shot shotlist the loader read
    sh020_t03.mp4             the render
    sh020_t03.jpg             mid-frame thumbnail
    sh020_t03_strip.jpg       STRIP_FRAMES frames side by side, for hover scrub
    sh020_t03_h3.wav          H3's own mix (written by the node, as before)

Pass `proxy` uses renders_proxy/ instead. Takes made before sidecars existed
have only the mp4 (and wav); they list as status "ok" with no provenance.

SIDECAR — JSON object. Written by the queuer at queue time with status
"queued"; creating it is what reserves the take number (exclusive create, so
two queuers can't get the same number). H3SaveShot then sets:

    status      "ok" | "failed"
    finished    ISO-8601 local time
    frames      int, frames written
    mp4, thumb, strip   file names (not paths) of what it wrote, or null
    save_notes  the node's status string

and leaves every other field alone. Updates are atomic (write a temp file in
the same folder, then os.replace). A queued take whose ComfyUI job is gone
without reaching the saver is marked "failed" by whoever notices (see
`sweep_queued`).

Queuer fields: version, shot, take, pass, target, status, queued,
comfy_prompt_id, seed, seed_source, model, loras, steps, width, height,
length, shot_hash, overrides (names of the fields an override replaced),
parent_take, note, refs (list of {slot, path, sha1}).

Stdlib only.
"""
from __future__ import annotations

import datetime as _dt
import glob
import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass, field

SIDECAR_VERSION = 1
DEFAULT_TARGET = "minimax_h3_ref2va"
PASSES = ("final", "proxy")
STATUSES = ("queued", "ok", "failed")
STRIP_FRAMES = 8


# ---------------------------------------------------------------------------
# names and paths
# ---------------------------------------------------------------------------

def safe_id(shot_id: str) -> str:
    """The folder/file-safe form of a shot id. Same rule as the node and h3render."""
    return re.sub(r"[^A-Za-z0-9_.-]", "_", shot_id) or "shot"


def pass_subfolder(pass_: str) -> str:
    if pass_ not in PASSES:
        raise ValueError(f"pass must be one of {PASSES}, not {pass_!r}")
    return "renders_proxy" if pass_ == "proxy" else "renders"


def shot_dir(root: str, pass_: str, shot_id: str, folder: str | None = None) -> str:
    """`folder` overrides the pass's standard subfolder (h3render --subfolder)."""
    return os.path.join(root, folder or pass_subfolder(pass_), safe_id(shot_id))


def stem(shot_id: str, take: int) -> str:
    return f"{safe_id(shot_id)}_t{take:02d}"


@dataclass
class TakePaths:
    dir: str
    stem: str

    def _p(self, suffix: str) -> str:
        return os.path.join(self.dir, self.stem + suffix)

    @property
    def sidecar(self) -> str: return self._p(".json")
    @property
    def shotlist(self) -> str: return self._p(".shotlist.json")
    @property
    def mp4(self) -> str: return self._p(".mp4")
    @property
    def thumb(self) -> str: return self._p(".jpg")
    @property
    def strip(self) -> str: return self._p("_strip.jpg")
    @property
    def h3_wav(self) -> str: return self._p("_h3.wav")


def take_paths(root: str, pass_: str, shot_id: str, take: int,
               folder: str | None = None) -> TakePaths:
    return TakePaths(shot_dir(root, pass_, shot_id, folder), stem(shot_id, take))


# ---------------------------------------------------------------------------
# JSON files
# ---------------------------------------------------------------------------

def read_json(path: str, default=None):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default


def write_json(path: str, data) -> None:
    """Atomic: a reader never sees a half-written file."""
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp_", suffix=".json", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def now() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def content_hash(obj) -> str:
    """sha1 of a JSON value in canonical form (sorted keys, no whitespace)."""
    blob = json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


def file_sha1(path: str) -> str | None:
    try:
        h = hashlib.sha1()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except FileNotFoundError:
        return None


# ---------------------------------------------------------------------------
# takes
# ---------------------------------------------------------------------------

@dataclass
class Take:
    shot: str
    take: int
    pass_: str
    paths: TakePaths
    sidecar: dict | None = None

    @property
    def status(self) -> str:
        if self.sidecar is not None:
            return self.sidecar.get("status", "queued")
        return "ok" if os.path.isfile(self.paths.mp4) else "failed"

    @property
    def has_video(self) -> bool:
        return os.path.isfile(self.paths.mp4)

    @property
    def usable(self) -> bool:
        """What assemble may cut in: finished and the mp4 is really there."""
        return self.status == "ok" and self.has_video


def take_numbers(root: str, pass_: str, shot_id: str, folder: str | None = None) -> list[int]:
    """Every take number with an mp4 or a sidecar, ascending."""
    d = shot_dir(root, pass_, shot_id, folder)
    s = re.escape(safe_id(shot_id))
    nums = set()
    for p in glob.glob(os.path.join(d, f"{safe_id(shot_id)}_t*")):
        m = re.fullmatch(rf"{s}_t(\d+)\.(?:json|mp4)", os.path.basename(p))
        if m:
            nums.add(int(m.group(1)))
    return sorted(nums)


def read_sidecar(path: str) -> dict | None:
    """A sidecar, or None if there is none. One caught mid-reservation (claimed
    but not yet written) or otherwise unparsable reads as queued."""
    try:
        data = read_json(path)
    except ValueError:
        return {"status": "queued", "unreadable": True}
    if data is not None and not isinstance(data, dict):
        return {"status": "queued", "unreadable": True}
    return data


def list_takes(root: str, pass_: str, shot_id: str, folder: str | None = None) -> list[Take]:
    out = []
    for n in take_numbers(root, pass_, shot_id, folder):
        tp = take_paths(root, pass_, shot_id, n, folder)
        out.append(Take(shot_id, n, pass_, tp, read_sidecar(tp.sidecar)))
    return out


def get_take(root: str, pass_: str, shot_id: str, take: int,
             folder: str | None = None) -> Take | None:
    tp = take_paths(root, pass_, shot_id, take, folder)
    if not (os.path.isfile(tp.sidecar) or os.path.isfile(tp.mp4)):
        return None
    return Take(shot_id, take, pass_, tp, read_sidecar(tp.sidecar))


def latest_usable(takes: list[Take]) -> Take | None:
    ok = [t for t in takes if t.usable]
    return ok[-1] if ok else None


def reserve_take(root: str, pass_: str, shot_id: str, sidecar: dict,
                 take: int | None = None, folder: str | None = None) -> Take:
    """Create the sidecar for the next free take number (or `take`) and return it.

    Exclusive create, so concurrent queuers each get their own number. With an
    explicit `take` the sidecar is overwritten: that is `--take N`, a deliberate
    re-render into an existing number.
    """
    d = shot_dir(root, pass_, shot_id, folder)
    os.makedirs(d, exist_ok=True)
    if take is not None:
        tp = take_paths(root, pass_, shot_id, take, folder)
        data = dict(sidecar, version=SIDECAR_VERSION, shot=shot_id, take=take,
                    **{"pass": pass_})
        write_json(tp.sidecar, data)
        return Take(shot_id, take, pass_, tp, data)
    nums = take_numbers(root, pass_, shot_id, folder)
    n, data = claim_number(
        (nums[-1] + 1) if nums else 1,
        lambda k: take_paths(root, pass_, shot_id, k, folder).sidecar,
        lambda k: dict(sidecar, version=SIDECAR_VERSION, shot=shot_id, take=k,
                       **{"pass": pass_}),
        # a legacy take (mp4, no sidecar) owns its number
        taken=lambda k: os.path.isfile(take_paths(root, pass_, shot_id, k, folder).mp4))
    return Take(shot_id, n, pass_, take_paths(root, pass_, shot_id, n, folder), data)


def claim_number(first: int, sidecar_of, data_for, taken=None) -> tuple[int, dict]:
    """Reserve the first free number from `first` up by creating its sidecar.

    `sidecar_of(n)` is number n's sidecar path, `data_for(n)` its content, and
    `taken(n)` (optional) says a number is owned by something other than a
    sidecar. The claim is an exclusive create, so concurrent callers each get
    their own number; the content is then written atomically. Returns
    (number, content). Used for video takes and ref takes alike.
    """
    n = first
    while True:
        path = sidecar_of(n)
        if taken is not None and taken(n):
            n += 1
            continue
        try:
            open(path, "x").close()                # the claim
        except FileExistsError:
            n += 1
            continue
        data = data_for(n)
        write_json(path, data)                     # the content, atomically
        return n, data


def update_sidecar(path: str, **fields) -> dict:
    data = read_json(path, {}) or {}
    data.update(fields)
    write_json(path, data)
    return data


def _parse_time(s) -> _dt.datetime | None:
    try:
        return _dt.datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def sweep_queued(takes: list[Take], alive: set[str], as_of: str | None = None,
                 grace_s: float = 120.0) -> list[Take]:
    """Mark queued takes whose ComfyUI job is no longer pending or running as failed.

    `alive` is every prompt id ComfyUI still has in /queue (pending or running),
    fetched at `as_of` (default: now, which is only right if it was just
    fetched). The saver runs inside the job, so a job that has left the queue
    has already written its sidecar if it ever will. Two cases are left alone,
    because the snapshot can't speak for them:

    - a take queued after the snapshot was taken;
    - a take with no prompt id yet that was reserved less than `grace_s` ago
      (the queuer reserves first and queues a moment later).

    Returns the takes it changed.
    """
    snap = _parse_time(as_of) or _dt.datetime.now().astimezone()
    changed = []
    for t in takes:
        sc = t.sidecar
        if sc is None or sc.get("status") != "queued":
            continue
        if sc.get("comfy_prompt_id") in alive:
            continue
        queued = _parse_time(sc.get("queued"))
        if queued is not None and queued >= snap:
            continue
        if not sc.get("comfy_prompt_id") and (
                queued is None or (snap - queued).total_seconds() < grace_s):
            continue
        t.sidecar = update_sidecar(t.paths.sidecar, status="failed", finished=now(),
                                   save_notes="job left the ComfyUI queue without saving")
        changed.append(t)
    return changed


# ---------------------------------------------------------------------------
# overrides.json
# ---------------------------------------------------------------------------
#
# {"episode": "ep01",
#  "shots": {"sh020": {"minimax_h3_ref2va": {
#      "seed": null | int, "note": "",
#      "final": {"base_hash": "…", "prompt": null | "text" | ["section", …],
#                "model": null, "loras": null, "steps": null},
#      "proxy": {…same…}}}}}
#
# null (or absent) means "use the built value". `loras` is a list of
# {"name": str, "strength": float}; an empty list means "no LoRA at all".
#
# The prompt is per pass because the built prompt is: a proxy that generates
# its audio has different audio sections from a final that clones voices. Each
# pass's `base_hash` records the build of the shot its override was written
# against. Seed and note are shared: both passes render the same seeds on
# purpose, so the proxy stays a preview of the final.
#
# A shot may also carry a `target` beside its per-target blocks
# ({"sh020": {"target": "ltx2", ...}}): its renders then use that video target
# (the shot's IR is recompiled for it at queue time), both passes. See
# shot_target / set_shot_target.

OVERRIDES_FILE = "overrides.json"
PASS_FIELDS = ("prompt", "model", "loras", "steps", "base_hash", "negative", "model_low")
SHOT_FIELDS = ("seed", "note")


def load_overrides(root: str) -> dict:
    data = read_json(os.path.join(root, OVERRIDES_FILE)) or {}
    data.setdefault("shots", {})
    return data


def save_overrides(root: str, data: dict) -> None:
    write_json(os.path.join(root, OVERRIDES_FILE), data)


def shot_override(data: dict, shot_id: str, pass_: str,
                  target: str = DEFAULT_TARGET) -> dict:
    """The effective override for one shot and pass: only the non-null fields."""
    block = data.get("shots", {}).get(shot_id, {}).get(target, {})
    out = {k: block[k] for k in SHOT_FIELDS if block.get(k) is not None}
    for k, v in (block.get(pass_) or {}).items():
        if k in PASS_FIELDS and v is not None:
            out[k] = v
    return out


def shot_target(data: dict, shot_id: str) -> str | None:
    """The video target overrides.json retargets a shot to (its shot-level
    `target`, shared by both passes), or None."""
    t = data.get("shots", {}).get(shot_id, {}).get("target")
    return t if isinstance(t, str) and t else None


def set_shot_target(data: dict, shot_id: str, target: str | None) -> dict:
    """Retarget a shot (None: back to the target the build compiled it for).
    It sits beside the per-target blocks: {"shots": {"sh020": {"target":
    "ltx2", "ltx2": {...}, "minimax_h3_ref2va": {...}}}}."""
    shots = data.setdefault("shots", {})
    if target:
        shots.setdefault(shot_id, {})["target"] = target
    elif shot_id in shots:
        shots[shot_id].pop("target", None)
        if not shots[shot_id]:
            del shots[shot_id]
    return data


def episode_target(data: dict) -> str | None:
    """The episode's video target set in the editor (overrides.json's
    top-level {"episode": {"target": "<id>"}}), or None. It is the default of
    every shot the script gives no target of its own (h3jobs.target_choice).
    An older file's "episode" is just the episode's name: no target."""
    ep = data.get("episode")
    t = ep.get("target") if isinstance(ep, dict) else None
    return t if isinstance(t, str) and t else None


def set_episode_target(data: dict, target: str | None, name: str = "") -> dict:
    """Set (or with None clear) the episode target. The "episode" key held
    the episode's name before; it is kept as "id" beside the target, and the
    plain name comes back when the target is cleared (and nothing else is
    set there)."""
    return set_episode_field(data, "target", target, name)


EPISODE_FIELDS = ("target", "refs_target", "keyframe_target")


def episode_field(data: dict, key: str) -> str | None:
    """One of the editor's episode-level choices in overrides.json's
    "episode" object: `target` (the video target), `refs_target` /
    `keyframe_target` (the image targets of series refs and of keyframes).
    None when unset (or "episode" is only the episode's name)."""
    ep = data.get("episode")
    v = ep.get(key) if isinstance(ep, dict) else None
    return v if isinstance(v, str) and v else None


def set_episode_field(data: dict, key: str, value: str | None, name: str = "") -> dict:
    """Set (or with None clear) one episode-level choice (EPISODE_FIELDS),
    keeping the episode's name as "id"; with nothing left set, "episode" is
    the plain name again."""
    ep = data.get("episode")
    ident = (ep.get("id") if isinstance(ep, dict) else ep) or name or ""
    fields = {k: ep[k] for k in EPISODE_FIELDS
              if isinstance(ep, dict) and isinstance(ep.get(k), str) and ep[k]}
    if value:
        fields[key] = value
    else:
        fields.pop(key, None)
    if fields:
        data["episode"] = dict({"id": ident} if ident else {},
                               **{k: fields[k] for k in EPISODE_FIELDS if k in fields})
    elif ident:
        data["episode"] = ident
    else:
        data.pop("episode", None)
    return data


def set_override(data: dict, shot_id: str, pass_: str | None = None,
                 target: str = DEFAULT_TARGET, **fields) -> dict:
    """Set fields on one shot's override. Pass-level fields need `pass_`.

    A value of None clears that field; an override block left empty is removed.
    """
    shots = data.setdefault("shots", {})
    block = shots.setdefault(shot_id, {}).setdefault(target, {})
    for k, v in fields.items():
        if k in SHOT_FIELDS:
            dst = block
        elif k in PASS_FIELDS:
            if pass_ not in PASSES:
                raise ValueError(f"{k} is per pass: give pass_='final' or 'proxy'")
            dst = block.setdefault(pass_, {})
        else:
            raise ValueError(f"unknown override field {k!r}")
        if v is None:
            dst.pop(k, None)
        else:
            dst[k] = v
    for p in PASSES:
        # a base_hash alone describes nothing
        if p in block and set(block[p]) <= {"base_hash"}:
            del block[p]
    if not block:
        del shots[shot_id][target]
    if not shots[shot_id]:
        del shots[shot_id]
    return data


# ---------------------------------------------------------------------------
# cut.json
# ---------------------------------------------------------------------------
#
# {"episode": "ep01",
#  "final": [{"shot": "sh010", "take": 2},
#            {"shot": "sh020", "take": 3, "trim_in": 0, "trim_out": 0,
#             "locked": true, "note": ""},
#            {"shot": "sh030", "pass": "proxy", "take": 1}],
#  "proxy": []}
#
# An entry's `take` may be omitted or null: latest usable take. `pass` defaults
# to the list it is in; naming the other pass makes it a placeholder.

CUT_FILE = "cut.json"
ENTRY_FIELDS = ("shot", "take", "pass", "trim_in", "trim_out", "locked", "note")


def load_cut(root: str) -> dict:
    data = read_json(os.path.join(root, CUT_FILE)) or {}
    for p in PASSES:
        data.setdefault(p, [])
    return data


def save_cut(root: str, data: dict) -> None:
    write_json(os.path.join(root, CUT_FILE), data)


@dataclass
class CutEntry:
    shot: str
    pass_: str                      # the pass its take comes from
    take: int | None = None         # None: latest usable
    trim_in: int = 0                # frames
    trim_out: int = 0
    locked: bool = False
    note: str = ""
    in_cut_file: bool = True        # False: not listed, placed by script order
    orphan: bool = False            # listed, but no longer in the script
    placeholder: bool = False       # take comes from the other pass
    extra: dict = field(default_factory=dict)


def resolve_cut(data: dict, pass_: str, script_order: list[str]) -> list[CutEntry]:
    """The cut for one pass, reconciled with the script.

    Listed entries keep their order. A script shot that isn't listed is
    inserted right after the nearest earlier script shot that is present (or at
    the front). A listed shot that is no longer in the script stays where it is,
    flagged orphan; assemble skips orphans.
    """
    in_script = set(script_order)
    entries: list[CutEntry] = []
    seen: set[str] = set()
    for raw in data.get(pass_, []):
        sid = raw.get("shot")
        if not sid or sid in seen:
            continue
        seen.add(sid)
        src = raw.get("pass") or pass_
        entries.append(CutEntry(
            shot=sid, pass_=src, take=raw.get("take"),
            trim_in=int(raw.get("trim_in") or 0), trim_out=int(raw.get("trim_out") or 0),
            locked=bool(raw.get("locked")), note=raw.get("note") or "",
            orphan=sid not in in_script, placeholder=src != pass_,
            extra={k: v for k, v in raw.items() if k not in ENTRY_FIELDS}))
    for i, sid in enumerate(script_order):
        if sid in seen:
            continue
        pos = 0
        for prev in reversed(script_order[:i]):
            idx = next((j for j, e in enumerate(entries) if e.shot == prev), None)
            if idx is not None:
                pos = idx + 1
                break
        entries.insert(pos, CutEntry(shot=sid, pass_=pass_, in_cut_file=False))
        seen.add(sid)
    return entries


def cut_entry_to_json(e: CutEntry, list_pass: str) -> dict:
    out: dict = {"shot": e.shot}
    if e.pass_ != list_pass:
        out["pass"] = e.pass_
    if e.take is not None:
        out["take"] = e.take
    if e.trim_in:
        out["trim_in"] = e.trim_in
    if e.trim_out:
        out["trim_out"] = e.trim_out
    if e.locked:
        out["locked"] = True
    if e.note:
        out["note"] = e.note
    out.update(e.extra)
    return out


def pick(data: dict, pass_: str, script_order: list[str], shot_id: str,
         take: int | None, from_pass: str | None = None) -> dict:
    """Choose the take a shot uses in a pass's cut (None: back to latest).

    Materialises the whole resolved order into the file the first time, so the
    list on disk always shows the full cut once anyone has picked anything.
    """
    entries = resolve_cut(data, pass_, script_order)
    for e in entries:
        if e.shot == shot_id:
            e.take = take
            e.pass_ = from_pass or pass_
            break
    else:
        raise KeyError(f"{shot_id} is not in the script or the cut")
    data[pass_] = [cut_entry_to_json(e, pass_) for e in entries]
    return data
