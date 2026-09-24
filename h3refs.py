#!/usr/bin/env python3
"""
h3refs.py — references (refs) as takes, driven by the series config.

A ref is a conditioning input a render reads that the pipeline generates or
you supply (docs/API.md, "References"). Refs are listed from the series config, not
from refs_todo, so a character nobody uses yet can still be generated:

    subject:<id>            a character (four views, stitched into its sheet),
                            prop or vehicle: the series config's `sheet` path.
                            A wardrobe variant (`of:`) is listed like any other
                            subject -- its own sheet -- and has no voice of its
                            own: it shares the subject's it is a variant of
    location:<id>           the location's `plate`
    voice:<id>              a character's voice sample: the series config's
                            `voice_sample`, else refs/voices/<id>.wav once one
                            is picked. Imported, generated on an audio target
                            (targets/audio/), or cut out of a take's sound
                            (voice_from_take)
    shot:<shot>:first|last  a shot's first / last keyframe, at
                            refs/shots/<shot>/<first|last>.png: imported, or
                            cut out of another shot's take (keyframe_from_take:
                            the previous shot's last frame, for continuity)

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
ep, status, queued, comfy_prompt_id, source ("generated" | "imported" |
"frame"), seed, seed_source, prompt, model, loras, steps, cfg, width, height,
overrides, note; a "frame" take adds source_shot, source_take, source_pass,
source_frame, source_frames, source_mp4 and source_sha1.
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

import copy
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
from h3core import series_config as SC  # noqa: E402

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
    view_edit_prompt, view_prompt)

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
        # A voice ref for every character, not only those the series config
        # already gives a `voice_sample`: an audio target generates one
        # (targets/audio/), and picking a candidate for a character with no
        # sample writes VOICE_DIR/<id>.wav and the series config's line
        # (pick_take). A prop or a vehicle never speaks.
        #
        # A variant (`of:`) has its own sheet -- the wardrobe is what it is for --
        # but never its own voice: it is the same character, and it inherited the
        # base's `voice` and `voice_sample` when the series config loaded. A
        # second voice ref would let one character be picked two voices.
        if e.get("of"):
            continue
        if e.get("voice_sample") or e.get("kind", "character") == "character":
            out.append(Ref(f"voice:{sid}", "series", "voice", e.get("name", sid),
                           e.get("voice_sample") or "", s.home, e, sid))
    return out


VOICE_DIR = "refs/voices"               # where a picked voice with no `voice_sample` goes


def voice_sample_path(ref: Ref) -> str:
    """Where a voice ref's live file belongs: the series config's
    `voice_sample`, else refs/voices/<subject>.wav (which picking it writes
    into the series config). Relative to the ref's home, forward slashes."""
    return ref.path or f"{VOICE_DIR}/{T.safe_id(ref.subject or ref.id)}.wav"


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


def keyframe_refs(ep: str, needs: dict | None = None) -> list[Ref]:
    """The shot keyframes to list: every one a shot needs or its script asks
    for (keyframe_needs; `needs` when already worked out), and every one
    that exists (a live file or any take), in script order."""
    found: dict[tuple[str, str], Ref] = {}
    ids = built_shot_ids(ep)
    for (sid, end) in (keyframe_needs(ep) if needs is None else needs):
        found[(sid, end)] = keyframe_ref(ep, sid, end)
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


# ---------------------------------------------------------------------------
# keyframes as needed refs (docs/API.md "Phase 8.5")
# ---------------------------------------------------------------------------
#
# Which shots need a keyframe is decided from each shot's video target (the
# one its next render uses: h3jobs.target_choice) and its script lines:
#
#   capabilities.keyframes   the ends a target reads (optional ones)
#   requires_first           the first is required (Wan 14B I2V)
#   first: / last:           continuity | generate | import | none | <path>
#                            (the shot's line, else its sequence's)
#
# A keyframe is listed when the target reads it or the script asks for one
# (anything but `none`); `none` turns an optional one off. `method` is the
# script's (a path is "import", with `import_path`), else "continuity" for a
# first frame when the shot has a previous shot in its sequence, else
# "generate".

KEYFRAME_METHODS = ("continuity", "generate", "import", "none")


_STORY: dict = {}               # (path, size, mtime) -> the parsed story IR


def episode_story(ep: str):
    """The story IR of a built episode (shotlist/shots.json), or None. Cached
    per (path, size, mtime): a refs listing asks for it once per voice ref."""
    path = os.path.join(ep, "shotlist", "shots.json")
    try:
        st = os.stat(path)
        key = (os.path.normcase(os.path.abspath(path)), st.st_size, st.st_mtime_ns)
    except OSError:
        return None
    if key in _STORY:
        return _STORY[key]
    try:
        from h3core import ir
        with open(path, encoding="utf-8") as fh:
            story = ir.Episode.loads(fh.read())
    except (OSError, ValueError, KeyError):
        return None
    if len(_STORY) > 8:
        _STORY.clear()
    _STORY[key] = story
    return story


def shot_ir(ep: str, shot: str):
    """(the shot's IR, its sequence's IR), or (None, None) without shots.json."""
    story = episode_story(ep)
    for sq in (story.sequences if story else []):
        for sh in sq.shots:
            if sh.id == shot:
                return sh, sq
    return None, None


def keyframe_needs(ep: str) -> dict[tuple[str, str], dict]:
    """{(shot, which): {"need", "method", "import_path"?, "script", "target",
    "reads"}} for every keyframe a shot needs or its script asks for, in
    script order. `need` is "required" (the target can't render without it)
    or "optional"; `target` the video target that will read it (its next
    render's); `reads` whether that target reads it at all (a script can ask
    for a keyframe a target ignores); `script` the script's line or None."""
    story = episode_story(ep)
    if story is None:
        return {}
    built: dict[str, str] = {}
    for ps in T.PASSES:
        try:
            docs = J.load_shotlists(ep, ps)
        except (FileNotFoundError, ValueError):
            continue
        for doc in docs:
            tid = J.shotlist_target(doc).id
            for sh in doc.get("shots", []):
                built.setdefault(sh["id"], tid)
    ov = T.load_overrides(ep)
    out: dict[tuple[str, str], dict] = {}
    for sq in story.sequences:
        prev = None
        for sh in sq.shots:
            if sh.id not in built:
                prev = sh.id
                continue
            tid = J.effective_target(ov, sh.id, built[sh.id], root=ep)
            try:
                caps = TG.load_target(tid, "video").capabilities()
            except Exception:
                caps = {"keyframes": [], "requires_first": False}
            for end in KEYFRAME_ENDS:
                script = sh.keyframe(end, sq)
                reads = end in (caps.get("keyframes") or [])
                required = end == "first" and bool(caps.get("requires_first"))
                if script == "none" and not required:
                    continue
                if not (reads or script):
                    continue
                if script in KEYFRAME_METHODS and script != "none":
                    method = script
                elif script and script != "none":
                    method = "import"
                else:
                    method = "continuity" if (end == "first" and prev) else "generate"
                d = {"need": "required" if required else "optional", "method": method,
                     "script": script, "target": tid, "reads": reads}
                if method == "import" and script not in KEYFRAME_METHODS:
                    d["import_path"] = script
                out[(sh.id, end)] = d
            prev = sh.id
    return out


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


# P8: a reserved pseudo-view for a character's whole sheet, so a ready-made
# 4-panel sheet can be supplied the way every other reference is -- as a take,
# with a sidecar recording where it came from, a _trash/ instead of deletion and
# candidates to compare. Its takes are `<key>_sheet_tNN.png` (take_base) and its
# pick lives beside the views in _picks.json, but it is NOT one of them: nothing
# generates a sheet (that is four views stitched), so `allow_sheet` is off by
# default and the generate paths never accept it.
#
# A sheet copied into the folder by hand stays entirely valid and needs none of
# this: the pipeline reads the live file off disk. It simply has no take behind
# it, which `live_from` reports as None.
SHEET_VIEW = "sheet"


def check_view(ref: Ref, view, required: bool = False,
               allow_sheet: bool = False) -> str | None:
    """A view for this ref: one of VIEW_TAGS for a character, None otherwise.
    `allow_sheet` also takes SHEET_VIEW (a supplied whole sheet); see there."""
    if view in (None, ""):
        if required and ref.has_views:
            raise RefError(f"{ref.id} is a character: give a view "
                           f"({', '.join(VIEW_TAGS)}"
                           f"{' or ' + SHEET_VIEW if allow_sheet else ''})")
        return None
    if not ref.has_views:
        raise RefError(f"{ref.id} has no views (only characters do)")
    if view == SHEET_VIEW:
        if allow_sheet:
            return view
        raise RefError(f"{SHEET_VIEW!r} is a supplied sheet, not a view to generate: "
                       f"a sheet is the four views stitched")
    if view not in VIEW_TAGS:
        raise RefError(f"view must be one of {', '.join(VIEW_TAGS)}"
                       f"{', or ' + SHEET_VIEW if allow_sheet else ''}, not {view!r}")
    return view


# ---------------------------------------------------------------------------
# prompts, sizes and seeds as the series config gives them
# ---------------------------------------------------------------------------

def ref_shot(ref: Ref) -> tuple[str, str]:
    """(shot id, "first" | "last") of a keyframe ref."""
    m = re.fullmatch(r"shot:(.+):(first|last)", ref.id)
    if not m:
        raise RefError(f"{ref.id} is not a shot keyframe")
    return m.group(1), m.group(2)


def image_defaults(s: Series, ready=None) -> dict:
    """The episode's ref-target defaults (GET /h3pipe/refs `defaults`):
    {"target", "target_source", "keyframe_target", "keyframe_target_source",
    "voice_target", "voice_target_source"}, each source "editor"
    (overrides.json's episode.refs_target / episode.keyframe_target /
    episode.voice_target, PUT /h3pipe/refs/defaults), "series" (the series
    config's `refs` block) or "default" (krea2; flux2_klein_edit for
    keyframes when `ready(target)` says it can render here, else the refs
    target; ltx2_voice for voices). RefError for a name that isn't a target
    of the right kind."""
    ov = T.load_overrides(s.ep)
    try:
        block = TG.refs_block(s.series_cfg)
        known = {t.id for t in TG.list_targets("image")}
        out = {}
        ed = T.episode_field(ov, "refs_target")
        if ed and ed in known:
            out.update(target=ed, target_source="editor")
        elif block.get("target"):
            out.update(target=block["target"], target_source="series")
        else:
            out.update(target=TG.DEFAULT_IMAGE_TARGET, target_source="default")
        ek = T.episode_field(ov, "keyframe_target")
        if ek and ek in known:
            out.update(keyframe_target=ek, keyframe_target_source="editor")
        elif block.get("keyframe_target"):
            out.update(keyframe_target=block["keyframe_target"],
                       keyframe_target_source="series")
        else:
            t = TG.load_target(TG.DEFAULT_KEYFRAME_TARGET, "image")
            kt = t.id if (ready is None or ready(t)) else out["target"]
            out.update(keyframe_target=kt, keyframe_target_source="default")
        heard = {t.id for t in TG.list_targets("audio")}
        ev = T.episode_field(ov, "voice_target")
        if ev and ev in heard:
            out.update(voice_target=ev, voice_target_source="editor")
        elif block.get("voice_target"):
            out.update(voice_target=block["voice_target"], voice_target_source="series")
        else:
            out.update(voice_target=TG.DEFAULT_AUDIO_TARGET, voice_target_source="default")
        return out
    except (TG.TargetError, ValueError) as e:
        raise RefError(str(e)) from None


def ref_target_kind(ref: Ref) -> str:
    """The kind of target that generates this ref: "audio" for a voice,
    "image" for everything else."""
    return "audio" if ref.kind == "voice" else "image"


def image_target_for(s: Series, ref: Ref, requested: str | None = None,
                     ov_data: dict | None = None, ready=None, defaults: dict | None = None):
    """The target a generate of `ref` uses: the request's `target`, else the
    ref's override (`target` in refs/_overrides.json), else the episode's
    defaults (image_defaults: the editor's episode choice, the series
    config's `refs` block, the built-in rule): `voice_target` for a voice
    (an audio target), `keyframe_target` for a shot keyframe, `target` for
    anything else. RefError for a name that isn't a target of that kind."""
    kind = ref_target_kind(ref)
    key = {"voice": "voice_target", "keyframe": "keyframe_target"}.get(ref.kind, "target")
    try:
        if requested:
            return TG.load_target(requested, kind)
        ov = ref_override(ov_data if ov_data is not None else load_overrides(ref.home), ref.id)
        if ov.get("target"):
            return TG.load_target(ov["target"], kind)
        d = defaults if defaults is not None else image_defaults(s, ready)
        return TG.load_target(d[key], kind)
    except (TG.TargetError, ValueError) as e:
        raise RefError(str(e)) from None


DEFAULT_FIELDS = (("target", "refs_target", "image"),
                  ("keyframe_target", "keyframe_target", "image"),
                  ("voice_target", "voice_target", "audio"))


def set_image_defaults(ep: str, fields: dict) -> dict:
    """Set (or with None clear) the episode's ref-target choices in
    overrides.json (episode.refs_target from `target`, episode.keyframe_target
    from `keyframe_target`, episode.voice_target from `voice_target`).
    RefError for a name that isn't a target of that kind. series.json is
    never written."""
    ov = T.load_overrides(ep)
    name = ""
    for ps in T.PASSES:
        if os.path.isfile(os.path.join(ep, J.shotlist_rel(ps))):
            name = J.load_shotlist(ep, ps).get("episode", "")
            break
    for key, field_, kind in DEFAULT_FIELDS:
        if key not in fields:
            continue
        v = fields[key]
        if v:
            try:
                TG.load_target(v, kind)
            except TG.TargetError as e:
                raise RefError(str(e)) from None
        T.set_episode_field(ov, field_, v or None, name or os.path.basename(os.path.normpath(ep)))
    T.save_overrides(ep, ov)
    return ov


FACE_SIZES = ("close", "cu")


def picked_view_file(s: Series, subject: str, view: str) -> str | None:
    """The file of a character view's picked take, or None."""
    ref = next((r for r in series_refs(s) if r.id == f"subject:{subject}"), None)
    if ref is None:
        return None
    n = picked_take(load_picks(ref.home), ref.id, view)
    if n is None:
        return None
    t = next((t for t in list_takes(ref, view) if t.take == n), None)
    return t.paths.image if t is not None and os.path.isfile(t.paths.image) else None


def variant_reference_images(s: Series, ref: Ref, view: str | None,
                             target=None, limit: int | None = None) -> list[dict]:
    """The reference image an edit target reads to generate one view of a
    wardrobe variant: the SAME view of the subject it is a variant of
    (docs/PLAN.md, Phase 10b). The towel's back panel edits the base's back
    panel; generating it cold is what makes the face drift.

    The base's picked take of that view, else that panel cut out of its live
    4-panel sheet (`crop`), else nothing. Same shape as the keyframe parts
    (`_reference_parts`), so staging, the prompt and the sidecar record read it
    the way they already do. [] when the ref is not a variant, the target reads
    no references, or the base has no picture yet."""
    if limit is None:
        limit = target.capabilities().get("max_refs", 0) if target is not None else 0
    base_id = ref.entry.get("of")
    if not limit or not view or ref.kind != "character" or not base_id:
        return []
    base = (s.series_cfg.get("subjects") or {}).get(base_id) or {}
    d = {"role": "subject", "subject": base_id, "name": base.get("name", base_id),
         "kind": "character", "view": view}
    picked = picked_view_file(s, base_id, view)
    if picked:
        d["path"] = picked
    elif base.get("sheet") and os.path.isfile(ref_file(s.home, base["sheet"])):
        d["path"] = ref_file(s.home, base["sheet"])
        d["crop"] = {"panels": len(VIEW_TAGS), "index": VIEW_TAGS.index(view)}
    else:
        return []
    return [d]


COMPOSITE_FIGURES = 4       # at most this many figures in a composed reference


def reference_images(s: Series, sh, sq, target=None, limit: int | None = None,
                     compose: bool | None = None) -> list[dict]:
    """The reference images an edit target reads for a keyframe of shot `sh`
    (ir.Shot, `sq` its ir.Sequence), in order: its characters (script
    order), then its props and vehicles, then the location plate, each only
    when its file is on disk, at most `limit` (default the target's
    max_refs). A character gives one view: the face on a single-character
    close-up, else the three-quarter body; its picked take of that view,
    else that panel cut out of its live 4-panel sheet (`crop`). Each is
    {"role": "subject" | "plate", "subject" | "location", "name", "kind",
    "view"?, "path" (absolute), "crop"?: {"panels", "index"}}.

    A single-reference target (max_refs 1: Kontext; `compose` None means
    "when the target's max_refs is 1") with more than one of these gets ONE
    composed reference instead: {"role": "composite", "kind": "composite",
    "name", "parts": [the figures (at most COMPOSITE_FIGURES), then the
    plate]}, with no `path` until stage_references composes it (the
    figures pasted over the plate, comfy_nodes/h3_refsheet.py)."""
    if limit is None:
        limit = target.capabilities().get("max_refs", 0) if target is not None else 0
    if not limit:
        return []
    if compose is None:
        compose = (limit == 1 and target is not None
                   and target.capabilities().get("max_refs") == 1)
    out = _reference_parts(s, sh, sq)
    if compose and limit == 1 and len(out) > 1:
        figures = [r for r in out if r["role"] == "subject"][:COMPOSITE_FIGURES]
        plate = [r for r in out if r["role"] == "plate"]
        name = _and_names([r["name"] for r in figures])
        if plate:
            name = f"{name} over {plate[0]['name']}" if figures else plate[0]["name"]
        return [{"role": "composite", "kind": "composite", "name": name,
                 "parts": figures + plate}]
    return out[:limit]


def _and_names(names: list[str]) -> str:
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def _reference_parts(s: Series, sh, sq) -> list[dict]:
    """Every reference image a keyframe of `sh` could read (reference_images'
    order and rules, before `limit`)."""
    book = s.series_cfg["subjects"]
    subjects = list(sh.cast) + [p for p in sh.props if p not in sh.cast]
    chars = [x for x in subjects if (book.get(x) or {}).get("kind", "character") == "character"]
    ordered = chars + [x for x in subjects if x not in chars]
    face = len(chars) == 1 and len(subjects) == 1 and sh.size in FACE_SIZES
    view = "04_face" if face else "01_threequarter"
    out = []
    for sid in ordered:
        e = book.get(sid)
        if not e:
            continue
        kind = e.get("kind", "character")
        d = {"role": "subject", "subject": sid, "name": e.get("name", sid), "kind": kind}
        if kind == "character":
            p = picked_view_file(s, sid, view)
            if p:
                d.update(view=view, path=p)
            elif e.get("sheet") and os.path.isfile(ref_file(s.home, e["sheet"])):
                d.update(view=view, path=ref_file(s.home, e["sheet"]),
                         crop={"panels": len(VIEW_TAGS), "index": VIEW_TAGS.index(view)})
            else:
                continue
        else:
            if not (e.get("sheet") and os.path.isfile(ref_file(s.home, e["sheet"]))):
                continue
            d["path"] = ref_file(s.home, e["sheet"])
        out.append(d)
    loc_key = sh.plate or sq.location
    loc = s.series_cfg["locations"].get(loc_key) or {}
    if loc.get("plate") and os.path.isfile(ref_file(s.home, loc["plate"])):
        out.append({"role": "plate", "location": loc_key, "name": loc.get("name", loc_key),
                    "kind": "plate", "path": ref_file(s.home, loc["plate"])})
    return out


def keyframe_prompt(s: Series, ref: Ref, target=None, refs: list[dict] | None = None
                    ) -> str | None:
    """A keyframe's prompt from its shot's IR (targets/image/common.py, or the
    target's own keyframe_prompt): None without shotlist/shots.json. An edit
    target's reference images are named in it (`refs`, default
    reference_images)."""
    shot, which = ref_shot(ref)
    sh, sq = shot_ir(s.ep, shot)
    if sh is None:
        return None
    target = target or image_target_for(s, ref)
    if refs is None:
        refs = reference_images(s, sh, sq, target)
    fn = getattr(target.module, "keyframe_prompt", None)
    if fn is None:
        from targets.image.common import keyframe_prompt as fn
    return fn(sh, sq, s.series_cfg, which, refs, target.recipe.get("ref_word") or "image")


def built_prompt(s: Series, ref: Ref, view: str | None = None,
                 view_size: tuple[int, int] = VIEW_SIZE, target=None,
                 refs: list[dict] | None = None) -> str | None:
    """The prompt the series config gives this ref (and view): what a generate uses
    unless overridden. None when the series config lacks what it needs. A character
    with no view gets its hand-made-sheet description (as in refs_todo). A
    shot keyframe's is written from its shot (keyframe_prompt; `target` the
    image target that makes it, default image_target_for's)."""
    e = ref.entry
    if ref.kind == "keyframe":
        try:
            return keyframe_prompt(s, ref, target)
        except RefError:
            return None
    if ref.kind == "voice":
        return voice_brief(s, ref, target)[0]
    if ref.kind == "location":
        return plate_prompt(s.look, e["description"]) if e.get("description") else None
    if not e.get("design"):
        return None
    if ref.kind == "character":
        if view is None:
            # a variant's hand-made-sheet description says where to start from
            return sheet_prompt(e["design"], s.look,
                                base=s.series_cfg["subjects"].get(e.get("of")))
        if refs:
            # it is generated as an edit of that reference, so the brief is
            # what to change rather than what to draw
            return view_edit_prompt(view, e["design"], s.look, *view_size,
                                    base_name=refs[0].get("name", "the same character"))
        return view_prompt(view, e["design"], s.look, *view_size)
    return object_prompt(e["design"], s.look)


def voice_brief(s: Series, ref: Ref, target=None, seconds: float | None = None
                ) -> tuple[str, str, str]:
    """(the brief a voice generate uses, the line it asks for, where the line
    came from: "script" | "neutral") for a voice ref, from the audio target
    (targets/audio/common.py's wording unless it writes its own). Without an
    audio target the old refs_todo note is returned with no line: that is
    what a person recording one reads."""
    e = ref.entry
    name = e.get("name", ref.subject)
    voice = e.get("voice", "as written in series.json")
    try:
        target = target or image_target_for(s, ref)
    except RefError:
        target = None
    if target is None or target.kind != "audio":
        return voice_prompt(name, voice), "", "none"
    line, source = target.voice_line(episode_story(s.ep), ref.subject or "")
    if seconds is None:
        seconds = target.seconds_range()[0]
    return (target.voice_prompt(name, e.get("voice", ""), e.get("design", ""), line, seconds),
            line, source)


def gen_size(ref: Ref, view_size: tuple[int, int] | None = None, target=None,
             s: Series | None = None, pass_: str = "final") -> tuple[int, int]:
    """The size a generate makes: the image target's (template view_size /
    plate_size / object_size; krea2's by default), a character view at
    `view_size` when given; a shot keyframe at its shot's render size for
    `pass_` (keyframe_size)."""
    if ref.kind == "keyframe":
        return keyframe_size(s, ref, target, pass_)[:2]
    tpl = (target.spec.get("template") or {}) if target is not None else {}
    if ref.kind == "character":
        return tuple(view_size or tpl.get("view_size") or VIEW_SIZE)
    if ref.kind == "location":
        return tuple(tpl.get("plate_size") or PLATE_SIZE)
    return tuple(tpl.get("object_size") or OBJECT_SIZE)


def scaled_size(target, w: int, h: int) -> tuple[int, int]:
    """(w, h) for an image target: scaled up, keeping the aspect, to at least
    its template's `min_pixels`, on its `size_multiple` grid (a keyframe at a
    proxy's 640x352 is far below what an image model draws well; the video
    targets scale a keyframe to the frame). Unchanged without min_pixels."""
    import math
    tpl = (target.spec.get("template") or {}) if target is not None else {}
    m = int(tpl.get("size_multiple") or 1)
    k = 1.0
    if tpl.get("min_pixels") and w * h < int(tpl["min_pixels"]):
        k = math.sqrt(int(tpl["min_pixels"]) / float(w * h))
    return max(m, int(round(w * k / m)) * m), max(m, int(round(h * k / m)) * m)


def keyframe_size(s: Series | None, ref: Ref, target=None,
                  pass_: str = "final") -> tuple[int, int, int, int, str]:
    """(width, height to generate, the shot's render width, height, its video
    target) for a keyframe: the render size of the shot on the target its
    next render uses, in `pass_` (the keyframe file serves both passes; the
    final's is the larger), scaled for the image target (scaled_size).
    RefError when the shot isn't in that pass's build."""
    shot, _ = ref_shot(ref)
    ep = s.ep if s is not None else ref.home
    try:
        doc, idx = J.find_shot(ep, pass_, shot)
    except (KeyError, FileNotFoundError):
        raise RefError(f"{shot} is not in the {pass_} build: build the episode") from None
    job = J.plan_job(ep, pass_, doc, idx, J.RenderRequest(shot), T.load_overrides(ep))
    w, h = int(job.width or 1024), int(job.height or 576)
    gw, gh = scaled_size(target, w, h)
    return gw, gh, w, h, job.target


def stable_seed(ref: Ref) -> int:
    """kreagen's seeds: a character's four views share seed_for(<id>); anything
    else uses seed_for(<the path the series config names>).

    A wardrobe variant (`of:`) borrows the seed of the subject it is a variant
    of. On a text-to-image target that is the only thing carrying identity
    across the two generations: same seed, same model, a prompt that differs
    only in the wardrobe sentence. With its own id it drew a different person
    who happened to be described similarly. It is not identity — an edit target
    or the H3 still target is (docs/PLAN.md, Phase 10b) — but it is the
    difference between the same character in new clothes and a new character
    in the clothes."""
    if ref.kind == "character":
        return seed_for(ref.entry.get("of") or ref.subject)
    return seed_for(ref.path or ref.id)


def can_generate(s: Series, ref: Ref) -> str | None:
    """None if a generate can run; else why not, for a person."""
    if ref.kind == "voice":
        try:
            t = image_target_for(s, ref)
        except RefError as e:
            return str(e)
        if t.kind != "audio":
            return "no audio target generates voices: import a recording"
        return None
    if ref.kind == "keyframe":
        shot, _ = ref_shot(ref)
        if shot_ir(s.ep, shot)[0] is None:
            return (f"{shot} is not in shotlist/shots.json: build the episode to generate "
                    f"its keyframe (or use another shot's frame, or import an image)")
        return None
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


def trash_dir(ref: Ref) -> str:
    """Where a ref's discarded takes go: refs/_takes/_trash/<key>/."""
    return os.path.join(ref.home, "refs", "_takes", T.TRASH, ref.key)


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
                       image=f"{base}_t{k:02d}{ext}"),
        # a discarded take's number is not given out again
        taken=lambda k: T.in_trash(trash_dir(ref), f"{base}_t{k:02d}"))
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


def close_take(take: RefTake, data: bytes | None = None, notes: str = "",
               ext: str | None = None) -> str:
    """Close a take from outside the save node: write `data` as its file (if
    given) and set ok, or failed when there is none. For kreagen's SaveImage
    fallback and for a job whose saver didn't update the sidecar. `ext` (an
    extension including the dot) renames the take's file: ComfyUI's own
    SaveAudio writes flac, not the wav H3SaveRefAudio would have made."""
    if ext and os.path.splitext(take.paths.image)[1].lower() != ext.lower():
        take.paths.image = os.path.splitext(take.paths.image)[0] + ext
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
OVERRIDE_FIELDS = ("prompt", "seed", "model", "loras", "steps", "note", "target")


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
    if fields.get("target") is not None:
        kind = ref_target_kind(ref)                       # audio for a voice, else image
        if view:
            raise RefError(f"an {kind} target override is per ref, not per view")
        try:
            TG.load_target(fields["target"], kind)
        except TG.TargetError as e:
            raise RefError(str(e)) from None
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


def view_own_fields(data: dict, ref_id: str, view: str | None) -> list[str]:
    """P9: the override fields set on a character's VIEW itself. `ref_override`
    merges the character's fields over the view's, so `fields` alone can't say
    which of them a person set here -- and "revert this view" only drops these."""
    if not view:
        return []
    block = ((data.get("refs", {}).get(ref_id) or {}).get("views") or {}).get(view) or {}
    return sorted(k for k in OVERRIDE_FIELDS if block.get(k) is not None)


def override_view(s: Series, ref: Ref, view: str | None, data: dict,
                  view_size=VIEW_SIZE) -> dict:
    """{fields: [...], stale: bool} for the listing; `values` has the fields.
    `own` is the subset set on this view rather than inherited from the
    character (empty for a ref that has no views)."""
    eff = ref_override(data, ref.id, view)
    stale = False
    if eff.get("base_hash") and "prompt" in eff:
        stale = eff["base_hash"] != prompt_hash(built_prompt(s, ref, view, view_size))
    values = {k: v for k, v in eff.items() if k != "base_hash"}
    return {"fields": sorted(values), "stale": stale, "values": values,
            "own": view_own_fields(data, ref.id, view)}


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
    series_changed: bool = False     # a voice: series.json gained `voice_sample`


def set_voice_sample(s: Series, subject: str, rel: str) -> bool:
    """Write `subjects.<subject>.voice_sample` into the series config through
    the Phase 9a save path (a _history copy, then an atomic write keeping the
    file's line endings and BOM). The JSON is rewritten as a promote writes
    it (h3promote.dump_series: 2-space indent, key order kept). False when
    the line was already that. RefError when the file can't be read as JSON
    or names no such subject."""
    import json

    import h3promote as P
    import h3source as H
    try:
        src = H.read_source(s.ep, "series")
    except H.SourceError as e:
        raise RefError(str(e)) from None
    try:
        raw = json.loads(src.text)
    except ValueError as e:
        raise RefError(f"{os.path.basename(src.path)} is not valid JSON ({e}): fix it "
                       f"before picking a voice") from None
    book = raw.get("subjects")
    if not isinstance(book, dict) or subject not in book \
            or not isinstance(book[subject], dict):
        raise RefError(f"{os.path.basename(src.path)} has no subject {subject!r} to give a "
                       f"voice_sample to")
    # series_refs lists no voice for a variant, so this is only reachable by
    # asking for one by id. Writing it would split one character's voice in two.
    if book[subject].get("of"):
        raise RefError(f"{subject!r} is a variant of {book[subject]['of']!r} and shares its "
                       f"voice: give the voice to {book[subject]['of']!r} instead")
    if book[subject].get("voice_sample") == rel:
        return False
    book[subject]["voice_sample"] = rel
    H.write_source(src, P.dump_series(raw, src.text))
    return True


def pick_take(s: Series, ref: Ref, view: str | None, take: int,
              force: bool = False, panel_height: int = 1024,
              mksheet: str | None = None) -> PickResult:
    """Make `take` the live one: copy it into place (a character's view is
    recorded, and the sheet stitched once all four views are picked) and write
    _picks.json. Raises UnknownRef, NotUsable (unless `force` for a finished
    take whose status isn't ok), RefError, StitchError (after the pick is
    saved).

    A voice whose character has no `voice_sample` yet is copied to
    refs/voices/<id>.wav and the series config gains that line, through the
    Phase 9a save path (set_voice_sample); the result says `series_changed`."""
    view = check_view(ref, view, required=True, allow_sheet=True)
    t = get_take(ref, view, take)
    if not os.path.isfile(t.paths.image):
        raise NotUsable(f"{ref.id}{' ' + view if view else ''} t{take:02d} has no file")
    if t.status != "ok" and not force:
        raise NotUsable(f"{ref.id}{' ' + view if view else ''} t{take:02d} is {t.status}")
    if ref.is_audio and not ref.path:
        return _pick_new_voice(s, ref, t)
    if not ref.path:
        raise RefError(f"{os.path.basename(s.config_file) if ref.scope == 'series' else 'the episode'}"
                       f" names no file for {ref.id}")
    picks = load_picks(ref.home)
    block = picks["refs"].setdefault(ref.id, {})
    block.pop("cleared", None)                          # a pick ends a clear
    res = PickResult(t)
    if view == SHEET_VIEW:
        # a supplied sheet IS the live file: copied as it is, never stitched.
        # The views keep their own picks, and picking all four of them later
        # stitches over this (the row says which one the live file came from).
        _atomic_copy(t.paths.image, ref.file)
        block.setdefault("views", {})[SHEET_VIEW] = {
            "take": take, "sha1": T.file_sha1(ref.file), "picked": T.now()}
        save_picks(ref.home, picks)
        res.live = ref.file
        return res
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


def _pick_new_voice(s: Series, ref: Ref, t: RefTake) -> PickResult:
    """Pick a voice candidate for a character the series config gives no
    `voice_sample`: the wav goes to refs/voices/<id>.wav under the ref's home
    (keeping the candidate's own extension), _picks.json records the pick,
    and the series config gains the line."""
    rel = voice_sample_path(ref)
    ext = os.path.splitext(t.paths.image)[1].lower()
    if ext and os.path.splitext(rel)[1].lower() != ext:
        rel = os.path.splitext(rel)[0] + ext
    live = ref_file(ref.home, rel)
    _atomic_copy(t.paths.image, live)
    picks = load_picks(ref.home)
    block = picks["refs"].setdefault(ref.id, {})
    block.pop("cleared", None)
    block.update(take=t.take, sha1=T.file_sha1(live), picked=T.now())
    save_picks(ref.home, picks)
    changed = set_voice_sample(s, ref.subject or "", rel)
    # the Ref was built from the series config as it was: keep it truthful
    ref.path = rel
    ref.entry["voice_sample"] = rel
    return PickResult(t, live=live, series_changed=changed)


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
        picks = load_picks(ref.home)
        if picked_take(picks, ref.id, v) is not None or is_cleared(picks, ref.id, v):
            continue
        ok = [t for t in list_takes(ref, v)
              if t.status == "ok" and os.path.isfile(t.paths.image)]
        if ok:
            out.append(pick_take(s, ref, v, ok[0].take, mksheet=mksheet))
    return out


def live_from(picks: dict, ref: Ref) -> str | None:
    """Which route wrote a character's live sheet, judged from the file itself:
    SHEET_VIEW (a sheet supplied as a take), "views" (stitched from the four
    picks), or None. None covers both "no file" and "a file this tool didn't
    write" -- a sheet copied into the folder by hand, which is a perfectly good
    way to supply one and stays untouched by everything here.

    The file's own sha1 decides, not the newest timestamp, so a hand edit or a
    re-copy outside the editor is reported honestly rather than assumed."""
    if not ref.has_views or not ref.file or not os.path.isfile(ref.file):
        return None
    now = T.file_sha1(ref.file)
    block = picks.get("refs", {}).get(ref.id) or {}
    if ((block.get("views") or {}).get(SHEET_VIEW) or {}).get("sha1") == now:
        return SHEET_VIEW
    if block.get("sha1") == now:
        return "views"
    return None


def is_cleared(picks: dict, ref_id: str, view: str | None = None) -> bool:
    """True when the ref (or the view) was cleared and not picked since: its
    "no file" is the user's choice, so auto-pick leaves it alone."""
    block = picks.get("refs", {}).get(ref_id) or {}
    if block.get("cleared"):
        return True
    return bool(view and ((block.get("views") or {}).get(view) or {}).get("cleared"))


@dataclass
class ClearResult:
    ref: Ref
    view: str | None
    removed: list[str]               # live files deleted
    was: int | None                  # the take that was picked (a view's, or the ref's)


def clear_pick(s: Series, ref: Ref, view: str | None = None) -> ClearResult:
    """Unpick a ref: its live file is removed (every take stays) and
    _picks.json records the clear, so auto-pick doesn't put a take back
    until someone picks one. For a shot keyframe this is "Clear": the shot
    no longer uses a keyframe (a required one blocks it again). A
    character's view: that view is unpicked and the stitched sheet (which
    showed it) removed; a character with no view: every view and the sheet.
    SHEET_VIEW: the supplied sheet is forgotten, and its file removed only if
    it is still the live one (a stitch that replaced it is left alone).
    A voice: its live sample is removed and the clear recorded; the series
    config's `voice_sample` line is left alone (the editor never unwrites
    it), so the ref still lists and a later pick puts a file back.
    RefError for a ref with no file named; UnknownRef never (a ref with
    nothing picked is cleared all the same)."""
    view = check_view(ref, view, allow_sheet=True)
    if not ref.path:
        raise RefError(f"{ref.id} names no file to clear")
    picks = load_picks(ref.home)
    block = picks["refs"].setdefault(ref.id, {})
    was = picked_take(picks, ref.id, view)
    stamp = T.now()
    removed = []
    # clearing the supplied sheet must not delete a stitch that replaced it: the
    # live file only goes if it is still the file this pick wrote
    keep_live = (view == SHEET_VIEW and ref.file and os.path.isfile(ref.file)
                 and live_from(picks, ref) != SHEET_VIEW)
    if ref.file and os.path.isfile(ref.file) and not keep_live:
        os.remove(ref.file)
        removed.append(ref.file)
    if ref.has_views:
        views = block.setdefault("views", {})
        # a whole-ref clear takes the supplied sheet with it (its file is gone)
        for v in ([view] if view else list(VIEW_TAGS) + [SHEET_VIEW]):
            if v in views or view:
                views[v] = {"cleared": stamp}
        if view != SHEET_VIEW:
            block.pop("sha1", None)
            block.pop("stitched", None)
        if not view:
            block["cleared"] = stamp
    else:
        for k in ("take", "sha1", "picked"):
            block.pop(k, None)
        block["cleared"] = stamp
    save_picks(ref.home, picks)
    return ClearResult(ref, view, removed, was)


@dataclass
class DiscardResult:
    take: RefTake
    moved: list[str]                 # where the files went
    cleared: ClearResult | None      # it was the pick: the ref (the view) was cleared


def discard_take(s: Series, ref: Ref, view: str | None, take: int) -> DiscardResult:
    """Move a candidate (its sidecar and its image or audio) to
    refs/_takes/_trash/<key>/, names kept: nothing lists it, its number is
    not given out again, and it can be put back by hand. If it is the ref's
    (the view's) pick, the ref is cleared as clear_pick does. Raises
    UnknownRef (no such take), T.StillQueued (queued: let it finish first)."""
    view = check_view(ref, view, required=True, allow_sheet=True)
    t = get_take(ref, view, take)
    if t.status == "queued":
        raise T.StillQueued(f"{ref.id}{' ' + view if view else ''} t{take:02d} is queued: "
                            f"let it finish first")
    cleared = None
    if picked_take(load_picks(ref.home), ref.id, view) == take and (ref.path or not ref.is_audio):
        cleared = clear_pick(s, ref, view)
    files = set(T.stem_files(takes_dir(ref), f"{take_base(ref, view)}_t{take:02d}"))
    for p in (t.paths.sidecar, t.paths.image):
        if os.path.isfile(p):
            files.add(os.path.abspath(p))
    moved = T.discard_files(sorted(files), trash_dir(ref))
    return DiscardResult(t, moved, cleared)


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
# a voice sample out of a video take (the no-model path)
# ---------------------------------------------------------------------------
#
# A line the model already spoke in a take is the best voice reference there
# is: it is already the character. `voice_from_take` cuts `start..end` seconds
# out of that take's sound (h3peaks.clip_audio picks the file, as the cut
# does: the mp4 when it carries sound, else the take's _h3.wav) and adds the
# clip as a voice-ref candidate with source "from_take".

MIN_CLIP = 0.2                      # a shorter span is a click, not a voice
MAX_CLIP = 30.0


@dataclass
class VoiceClipResult:
    ref: Ref
    take: RefTake
    picked: bool
    source: dict                     # shot, take, pass, start, end, file


def voice_from_take(s: Series, ref: Ref, shot: str, take: int, pass_: str = "proxy",
                    start: float = 0.0, end: float | None = None,
                    pick: bool | None = None, note: str = "") -> VoiceClipResult:
    """Cut `start..end` seconds out of a video take's sound into a new
    candidate of voice ref `ref` (source "from_take"; the sidecar records the
    shot, take, pass and span). Picked when the voice has no live file yet
    (or always with pick=True; never with pick=False) — and picking a voice
    whose character has no `voice_sample` writes the series config
    (pick_take). Raises RefError, UnknownRef, NotUsable, FfmpegMissing."""
    if not ref.is_audio:
        raise RefError(f"{ref.id} is not a voice ref: a take's audio can only become a "
                       f"voice sample")
    if pass_ not in T.PASSES:
        raise RefError(f"pass must be final or proxy, not {pass_!r}")
    src = T.get_take(s.ep, pass_, shot, take)
    if src is None:
        raise UnknownRef(f"{shot} has no {pass_} take {take}")
    import h3peaks
    sound = h3peaks.clip_audio(src.paths.mp4, src.paths.h3_wav)
    if not sound:
        raise NotUsable(f"{shot} {pass_} t{take:02d} has no sound to take a voice from")
    dur = h3peaks.media_info(sound).get("duration")
    start = float(start or 0.0)
    if end is None:
        end = dur if dur else start + 5.0
    end = float(end)
    if start < 0:
        raise RefError("start must be 0 or more")
    if end - start < MIN_CLIP:
        raise RefError(f"the span must be at least {MIN_CLIP:g}s long "
                       f"(got {end - start:.2f}s)")
    if end - start > MAX_CLIP:
        raise RefError(f"the span must be at most {MAX_CLIP:g}s long "
                       f"(got {end - start:.2f}s)")
    if dur and start >= dur:
        raise RefError(f"{shot} {pass_} t{take:02d} is {dur:.2f}s long: the span starts "
                       f"after it ends")
    d = takes_dir(ref)
    os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, f".tmp_{uuid.uuid4().hex}.wav")
    try:
        extract_audio(sound, start, end, tmp)
        rel = ep_rel(s.ep, sound)
        t = reserve_take(ref, None, {
            "status": "queued", "queued": T.now(), "ep": s.ep, "source": "from_take",
            "source_shot": shot, "source_take": take, "source_pass": pass_,
            "source_start": start, "source_end": end, "source_file": rel,
            "source_sha1": T.file_sha1(sound), "seconds": round(end - start, 3),
            "comfy_prompt_id": None, "seed": None, "seed_source": None,
            "prompt": None, "model": None, "loras": None, "steps": None,
            "width": None, "height": None, "note": note}, ext=".wav")
        os.replace(tmp, t.paths.image)
    finally:
        if os.path.isfile(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    t.sidecar = T.update_sidecar(
        t.paths.sidecar, status="ok", finished=T.now(),
        save_notes=f"{start:.2f}-{end:.2f}s of {shot} {pass_} t{take:02d}")
    source = {"shot": shot, "take": take, "pass": pass_, "start": start, "end": end,
              "file": rel}
    picked = False
    live = ref.file
    if pick or (pick is None and not (live and os.path.isfile(live))
                and not is_cleared(load_picks(ref.home), ref.id)):
        pick_take(s, ref, None, t.take)
        picked = True
    return VoiceClipResult(ref, t, picked, source)


def extract_audio(src: str, start: float, end: float, out: str, timeout: int = 300) -> None:
    """`start..end` seconds of `src`'s sound as a 16-bit PCM wav at `out`
    (ffmpeg, a subprocess, as h3assemble runs it). Mono is left alone: what
    the file has is what the sample gets."""
    rc, log = _run([_ffmpeg("ffmpeg"), "-v", "error", "-nostdin", "-y",
                    "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", src,
                    "-vn", "-c:a", "pcm_s16le", out], timeout)
    if rc != 0 or not os.path.isfile(out):
        raise NotUsable(f"ffmpeg could not cut {start:.2f}-{end:.2f}s out of "
                        f"{os.path.basename(src)}: {log.strip()[-300:]}")


# ---------------------------------------------------------------------------
# import
# ---------------------------------------------------------------------------

def check_import_type(ref: Ref, name: str) -> str:
    """The extension a file called `name` is imported with (lower case), or
    RefError when this ref can't use that kind of file."""
    ext = os.path.splitext(name or "")[1].lower()
    allowed = AUDIO_EXTS if ref.is_audio else IMAGE_EXTS
    if ext not in allowed:
        raise RefError(f"{ref.id} takes {'audio' if ref.is_audio else 'an image'} "
                       f"({', '.join(allowed)}), not {ext or 'a file with no extension'}")
    return ext


def import_take(s: Series, ref: Ref, view: str | None, source_path: str,
                note: str = "", original_name: str | None = None) -> RefTake:
    """Add a file from disk as a new take (source "imported"), status ok.
    `original_name` is an upload's own file name (POST /h3pipe/refs/import
    as multipart): `source_path` is then the upload's temporary file, which
    is not recorded, and the type is judged by the original name."""
    view = check_view(ref, view, required=True, allow_sheet=True)
    if not isinstance(source_path, str) or not source_path:
        raise RefError("source_path is required: a file on this machine")
    if not os.path.isabs(source_path):
        raise RefError(f"source_path must be absolute, not {source_path!r}")
    if not os.path.isfile(source_path):
        raise FileNotFoundError(f"no file at {source_path}")
    ext = check_import_type(ref, original_name or source_path)
    origin = ({"source_path": None, "original_name": os.path.basename(original_name)}
              if original_name else {"source_path": source_path})
    t = reserve_take(ref, view, {"status": "queued", "queued": T.now(), "ep": s.ep,
                                 "source": "imported", **origin,
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
                                 save_notes=(f"uploaded as {os.path.basename(original_name)}"
                                             if original_name
                                             else f"imported from {source_path}"))
    return t


# ---------------------------------------------------------------------------
# keyframes from a video take's frame (continuity)
# ---------------------------------------------------------------------------
#
# A shot's first keyframe is usually the previous shot's last frame: the cut
# then runs on without a jump. `keyframe_from_take` cuts that frame out of the
# take the cut uses (ffmpeg, as a subprocess, like h3assemble) and adds it as a
# keyframe ref take with source "frame". The symmetric case, a shot's last
# keyframe from the next shot's first frame, is there for FL2V targets.

class FfmpegMissing(RuntimeError):
    """ffmpeg / ffprobe aren't on PATH (500)."""


def _ffmpeg(tool: str) -> str:
    exe = shutil.which(tool)
    if not exe:
        raise FfmpegMissing(f"cutting a frame out of a take needs {tool} on PATH "
                            f"(install ffmpeg: https://ffmpeg.org/download.html)")
    return exe


def _run(cmd: list[str], timeout: int) -> tuple[int, str]:
    import subprocess
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 1, f"{os.path.basename(cmd[0])} took longer than {timeout}s"
    return r.returncode, (r.stdout + b"\n" + r.stderr).decode("utf-8", "replace")


def video_frames(mp4: str, timeout: int = 300) -> int:
    """How many frames the video really has (decoded and counted, not the
    container's estimate)."""
    rc, out = _run([_ffmpeg("ffprobe"), "-v", "error", "-select_streams", "v:0",
                    "-count_frames", "-show_entries", "stream=nb_read_frames",
                    "-of", "csv=p=0", mp4], timeout)
    try:
        n = int(out.strip().splitlines()[0].strip().rstrip(","))
    except (ValueError, IndexError):
        raise RefError(f"can't count the frames of {os.path.basename(mp4)}: "
                       f"{out.strip()[-300:] or 'ffprobe said nothing'}") from None
    if rc != 0 or n < 1:
        raise RefError(f"{os.path.basename(mp4)} has no video frames")
    return n


def frame_index(frame, count: int) -> int:
    """A frame spec ("first", "last", or an index; negative counts from the
    end, -1 being the last) as an index into `count` frames."""
    if frame == "first":
        return 0
    if frame == "last":
        return count - 1
    if isinstance(frame, bool) or not isinstance(frame, int):
        raise RefError(f"frame must be \"first\", \"last\" or a frame number, not {frame!r}")
    i = frame + count if frame < 0 else frame
    if not 0 <= i < count:
        raise RefError(f"frame {frame} is outside the take ({count} frames: 0 to {count - 1})")
    return i


def extract_frame(mp4: str, index: int, out: str, timeout: int = 300) -> None:
    """Write frame `index` (0-based, counted in decode order) of `mp4` to `out`
    as a PNG. `select` counts decoded frames, so the frame is exact (seeking
    with -ss lands on timestamps)."""
    rc, msg = _run([_ffmpeg("ffmpeg"), "-y", "-v", "error", "-i", mp4,
                    "-vf", f"select=eq(n\\,{index})", "-fps_mode", "passthrough",
                    "-frames:v", "1", "-update", "1", "-f", "image2", "-c:v", "png", out],
                   timeout)
    if rc != 0 or not os.path.isfile(out) or os.path.getsize(out) == 0:
        raise RefError(f"ffmpeg couldn't cut frame {index} out of {os.path.basename(mp4)}: "
                       f"{msg.strip()[-400:] or 'no image written'}")


@dataclass
class KeyframeResult:
    ref: Ref
    take: RefTake
    picked: bool
    source: dict                     # shot, take, pass, frame, frames, mp4


def keyframe_source(s: Series, shot: str, which: str, pass_: str,
                    source_shot: str | None = None,
                    source_take: int | None = None) -> tuple[T.Take, str]:
    """The video take a keyframe is cut from, and its pass. By default the
    shot before (for `first`) or after (for `last`) in the pass's cut order,
    and the take that shot's cut entry uses (a pick, a placeholder from the
    other pass, else its latest usable take). Raises RefError (no neighbour),
    UnknownRef (no such shot or take), NotUsable (no usable take)."""
    if source_shot is None:
        try:
            e = E.cut_neighbour(s.ep, pass_, shot, -1 if which == "first" else 1)
        except KeyError as err:
            raise UnknownRef(err.args[0]) from None
        if e is None:
            raise RefError(f"{shot} is the {'first' if which == 'first' else 'last'} shot of "
                           f"the {pass_} cut: there is no "
                           f"{'previous' if which == 'first' else 'next'} shot to take a "
                           f"frame from")
        source_shot = e.shot
    if source_take is not None:
        t = T.get_take(s.ep, pass_, source_shot, source_take)
        if t is None:
            raise UnknownRef(f"{source_shot} has no {pass_} take {source_take}")
        if not t.usable:
            raise NotUsable(f"{source_shot} {pass_} t{source_take:02d} is {t.status}"
                            + ("" if t.has_video else " with no mp4"))
        return t, pass_
    entries = E.cut_entries(s.ep, pass_)
    e = next((x for x in entries if x.shot == source_shot), None)
    if e is None:
        if not T.list_takes(s.ep, pass_, source_shot):
            raise UnknownRef(f"{source_shot} is not in the {pass_} cut and has no takes")
        e = T.CutEntry(shot=source_shot, pass_=pass_)
    t, n, ok = E.cut_take(s.ep, e)
    if n is None:
        raise NotUsable(f"{source_shot} has no usable {e.pass_} take to take a frame from "
                        f"(render it first)")
    if not ok:
        why = "missing" if t is None else (t.status + ("" if t.has_video else " with no mp4"))
        raise NotUsable(f"the {pass_} cut uses {source_shot} {e.pass_} t{n:02d}, which is {why}")
    return t, e.pass_


def keyframe_from_take(s: Series, shot: str, which: str = "first",
                       source_shot: str | None = None, source_take: int | None = None,
                       frame=None, pass_: str = "proxy", pick: bool | None = None,
                       note: str = "") -> KeyframeResult:
    """Make `shot`'s `which` keyframe from a frame of a video take: by default
    the previous shot's last frame (`first`), or the next shot's first frame
    (`last`); see keyframe_source for which take. `frame` is "first", "last"
    or an index (negative from the end). The frame becomes a new take of
    shot:<shot>:<which> (source "frame"; the sidecar records the source shot,
    take, pass and frame index), picked when the keyframe has no live file yet
    (or always with pick=True; never with pick=False)."""
    if which not in KEYFRAME_ENDS:
        raise RefError(f"which must be first or last, not {which!r}")
    if pass_ not in T.PASSES:
        raise RefError(f"pass must be final or proxy, not {pass_!r}")
    ref = find_ref(s, f"shot:{shot}:{which}")
    src, src_pass = keyframe_source(s, shot, which, pass_, source_shot, source_take)
    if frame is None:
        frame = "last" if which == "first" else "first"
    count = video_frames(src.paths.mp4)
    idx = frame_index(frame, count)
    d = takes_dir(ref)
    os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, f".tmp_{uuid.uuid4().hex}.png")
    try:
        extract_frame(src.paths.mp4, idx, tmp)
        source = {"shot": src.shot, "take": src.take, "pass": src_pass, "frame": idx,
                  "frames": count, "mp4": ep_rel(s.ep, src.paths.mp4),
                  "sha1": T.file_sha1(src.paths.mp4)}
        t = reserve_take(ref, None, {
            "status": "queued", "queued": T.now(), "ep": s.ep, "source": "frame",
            "source_shot": src.shot, "source_take": src.take, "source_pass": src_pass,
            "source_frame": idx, "source_frames": count, "source_mp4": source["mp4"],
            "source_sha1": source["sha1"], "comfy_prompt_id": None, "seed": None,
            "seed_source": None, "prompt": None, "model": None, "loras": None,
            "steps": None, "note": note})
        os.replace(tmp, t.paths.image)
    finally:
        if os.path.isfile(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    wh = image_size(t.paths.image) or (None, None)
    t.sidecar = T.update_sidecar(
        t.paths.sidecar, status="ok", finished=T.now(), width=wh[0], height=wh[1],
        save_notes=f"frame {idx} of {count} of {src.shot} {src_pass} t{src.take:02d}")
    picked = False
    if pick or (pick is None and not os.path.isfile(ref.file)
                and not is_cleared(load_picks(ref.home), ref.id)):
        pick_take(s, ref, None, t.take)
        picked = True
    return KeyframeResult(ref, t, picked, source)


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
    cfg: float | None = None         # None: the image target's preset
    negative: str | None = None      # None: negative.txt, the series config, the preset
    note: str = ""
    view_size: tuple[int, int] | None = None   # a character view's size (None: the target's)
    target: str | None = None        # the image target (None: image_target_for)
    pass_: str = "final"             # a keyframe's size: this pass's render size
    negative_source: str = "request"  # what an explicit `negative` is (kreagen: "file")
    seconds: float | None = None     # a voice ref's length (None: the audio target's default)


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
    target: object = None            # the image target (targets.Target)
    negative_source: str = "preset"
    values: dict = field(default_factory=dict)   # other binding params (sampler, shift, ...)
    references: list = field(default_factory=list)   # an edit target's reference images
    render_size: tuple | None = None  # a keyframe: the shot's render size
    video_target: str = ""           # a keyframe: the video target that reads it
    notes: list = field(default_factory=list)
    resolved: dict = field(default_factory=dict)  # resolve_models' answer, per param
    inputs: dict = field(default_factory=dict)    # {"references": [names ComfyUI reads]}
    base: bool = False               # an accelerator is missing: the base preset renders
    # a voice ref (an audio target): how long, on the target's frame grid,
    # and the line it is asked to say
    seconds: float | None = None
    frames: int | None = None
    fps: float | None = None
    line: str = ""
    line_source: str = ""            # script | neutral | request

    @property
    def is_audio(self) -> bool:
        return self.target is not None and self.target.kind == "audio"

    @property
    def name(self) -> str:
        return f"{self.ref.id}:{self.view}" if self.view else self.ref.id

    @property
    def is_krea2(self) -> bool:
        return self.target is None or self.target.id == TG.DEFAULT_IMAGE_TARGET


# Binding params a job sets itself; any other the preset gives (sampler,
# shift, guidance, text encoders, VAE) is patched from `values`.
IMAGE_JOB_PARAMS = ("model", "loras", "steps", "seed", "cfg", "prompt", "negative",
                    "width", "height")
# an audio target's own: the job works out how many frames at what rate
AUDIO_JOB_PARAMS = ("length", "fps")


def _preset_values(target) -> dict:
    p = target.presets.get("final")
    if p is None:
        return {}
    skip = IMAGE_JOB_PARAMS + AUDIO_JOB_PARAMS
    return {k: v for k, v in p.extra.items()
            if k not in skip and target.binding.specs(k)}


def plan_generate(s: Series, req: GenRequest, overrides: dict | None = None,
                  rng: random.Random | None = None, ready=None,
                  defaults: dict | None = None) -> list[GenJob]:
    """The jobs one generate request makes: `count` candidates, each one image,
    or four (one per view, sharing a seed) for a character with no view.

    The image target is image_target_for's (the request's `target`, the ref's
    override, the series config's `refs` block; `ready(target)` decides
    whether the default keyframe target can render here). A shot keyframe is
    written from its shot (keyframe_prompt), at its shot's render size for
    `req.pass_`, and an edit target gets its reference images
    (reference_images).

    Seeds: typed > pinned in the override (unless seed_mode "new") > "same"
    (the ref's stable seed) > "new" > auto: the stable seed while the ref (the
    views concerned) has no usable take, else a new one. Candidates after the
    first always get new seeds.

    The negative: the request's (`negative`, its `negative_source`), else the
    episode's negative.txt, else the series config's `negative`, else the
    target preset's.
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
    if req.pass_ not in T.PASSES:
        raise RefError(f"pass must be final or proxy, not {req.pass_!r}")
    ov_data = overrides if overrides is not None else load_overrides(ref.home)
    target = image_target_for(s, ref, req.target, ov_data, ready, defaults)
    rng = rng or random.SystemRandom()
    shared_ov = ref_override(ov_data, ref.id, view)       # view None: the ref's level
    has_usable = any(t.usable for v in views for t in list_takes(ref, v))
    preset = target.presets.get("final")
    render_size, video_target, refs = None, "", []
    seconds = frames = fps = None
    line = line_source = ""
    if ref.kind == "voice":
        if target.kind != "audio":
            raise RefError(f"{target.id} is a {target.kind} target: a voice ref needs an "
                           f"audio one ({', '.join(t.id for t in TG.list_targets('audio'))})")
        want = req.seconds
        if want is not None:
            if isinstance(want, bool) or not isinstance(want, (int, float)) or want <= 0:
                raise RefError("seconds must be a positive number")
            lo, hi = target.seconds_range()[2], target.seconds_range()[1]
            if not lo <= float(want) <= hi:
                raise RefError(f"seconds must be between {lo:g} and {hi:g} on "
                               f"{target.short}, not {float(want):g}")
        seconds, frames, fps = target.snap_seconds(want)
        w = h = 0
    elif ref.kind == "keyframe":
        gw, gh, rw, rh, video_target = keyframe_size(s, ref, target, req.pass_)
        w, h, render_size = gw, gh, (rw, rh)
        shot, _ = ref_shot(ref)
        sh, sq = shot_ir(s.ep, shot)
        refs = reference_images(s, sh, sq, target)
    else:
        vs = req.view_size
        if vs is None and target.id == TG.DEFAULT_IMAGE_TARGET:
            vs = VIEW_SIZE
        w, h = gen_size(ref, vs, target)
    view_size = (w, h) if ref.has_views else (req.view_size or VIEW_SIZE)
    cfg = float(req.cfg if req.cfg is not None
                else (preset.extra.get("cfg", CFG) if preset is not None else CFG))
    default_steps = int(preset.steps) if (preset is not None and preset.steps) else STEPS
    if target.binding.specs("negative") or target.id == TG.DEFAULT_IMAGE_TARGET:
        negative, neg_source = J.negative_for(
            s.ep, preset.extra.get("negative") if preset is not None else "",
            requested=req.negative, series_cfg=s.series_cfg)
        if req.negative is not None:
            neg_source = req.negative_source or "request"
    else:
        negative, neg_source = "", "none"
    notes = []
    note = J.negative_note(cfg, neg_source) if negative and neg_source != "none" else ""
    if note:
        notes.append(note)
    if ref.kind == "keyframe" and render_size and render_size != (w, h):
        notes.append(f"generated at {w}x{h}: the shot renders at {render_size[0]}x"
                     f"{render_size[1]} ({video_target}), scaled up to {target.short}'s "
                     f"minimum size; the video target scales it to the frame")

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
            if "target" in ov and not req.target:
                used.append("target")

            def pick(name, built, from_req):
                if from_req is not None:
                    return from_req
                if name in ov:
                    used.append(name)
                    return ov[name]
                return built

            vrefs = refs
            if ref.kind == "keyframe":
                base_prompt = keyframe_prompt(s, ref, target, refs)
            elif ref.kind == "voice":
                base_prompt, line, line_source = voice_brief(s, ref, target, seconds)
            else:
                # a variant's view edits the base's SAME view, so its reference
                # -- and so its wording -- is per view, not per ref
                vrefs = variant_reference_images(s, ref, v, target) or refs
                base_prompt = built_prompt(s, ref, v, view_size, target, vrefs)
            prompt = pick("prompt", base_prompt, req.prompt)
            stale = ("prompt" in used and bool(ov.get("base_hash"))
                     and ov["base_hash"] != prompt_hash(base_prompt))
            if ref.kind == "voice" and prompt != base_prompt:
                line_source = "request" if req.prompt is not None else "override"
            jobs.append(GenJob(
                ref=ref, view=v, candidate=c, prompt=prompt, seed=seed, seed_source=source,
                model=pick("model", "", req.model) or "",
                loras=pick("loras", None, req.loras),
                steps=int(pick("steps", default_steps, req.steps)), cfg=cfg,
                negative=negative, width=w, height=h,
                note=req.note or "", overridden=sorted(set(used)), override_stale=stale,
                target=target, negative_source=neg_source, values=_preset_values(target),
                references=[dict(r) for r in vrefs], render_size=render_size,
                video_target=video_target, notes=list(notes),
                seconds=seconds, frames=frames, fps=fps, line=line,
                line_source=line_source))
    return jobs


def reference_record(s: Series, r: dict) -> dict:
    """A reference image as a ref take's sidecar records it: its path relative
    to the episode and its sha1 (a composite's parts too; a composite not
    composed, in a dry run, has path and sha1 null)."""
    out = {k: v for k, v in r.items() if k not in ("tmp", "parts")}
    path = r.get("path")
    out.update(path=ep_rel(s.ep, path) if path else None,
               sha1=T.file_sha1(path) if path else None)
    if r.get("parts"):
        out["parts"] = [reference_record(s, p) for p in r["parts"]]
    return out


def references_text(refs: list[dict]) -> str:
    """The reference images in one line, for the CLI's dry runs."""
    def one(r):
        if r.get("role") == "composite":
            return "one image composed of " + references_text(r.get("parts") or [])
        return (f"{r.get('subject') or r.get('location')}"
                f"{' ' + r['view'] if r.get('view') else ''}")
    return ", ".join(one(r) for r in refs)


def start_gen(s: Series, job: GenJob) -> RefTake:
    """Reserve the take and write its `queued` sidecar. No ComfyUI yet."""
    extra = {}
    if job.target is not None:
        extra["target"] = job.target.id
    if job.negative_source != "none":
        extra.update(negative=job.negative, negative_source=job.negative_source)
    if job.references:
        extra["references"] = [reference_record(s, r) for r in job.references]
    if job.inputs:
        extra["inputs"] = dict(job.inputs)
    if job.render_size:
        extra.update(render_width=job.render_size[0], render_height=job.render_size[1],
                     video_target=job.video_target)
    if job.values:
        extra["values"] = dict(job.values)
    if job.resolved:
        extra["resolved"] = copy.deepcopy(job.resolved)
    if job.base:
        extra["base"] = True
    if job.notes:
        extra["notes"] = list(job.notes)
    if job.is_audio:
        # a voice sample: no picture, a length and the line it was asked to say
        extra.update(seconds=job.seconds, frames=job.frames, fps=job.fps,
                     line=job.line, line_source=job.line_source,
                     width=None, height=None)
    return reserve_take(job.ref, job.view, {
        "status": "queued", "queued": T.now(), "ep": s.ep, "comfy_prompt_id": None,
        "source": "generated", "seed": job.seed, "seed_source": job.seed_source,
        "prompt": job.prompt, "model": job.model, "loras": job.loras, "steps": job.steps,
        "cfg": job.cfg, "width": job.width, "height": job.height,
        "overrides": job.overridden, "override_stale": job.override_stale,
        "note": job.note, **extra}, ext=".wav" if job.is_audio else ".png")


def image_graph(base: dict, job: GenJob, sidecar: str | None) -> dict:
    """The API graph of one take on an image target other than krea2, or on
    any audio target: `base` (the target's workflow) with its SaveImage /
    SaveAudio replaced by the binding's saver (H3SaveRefTake, H3SaveRefAudio)
    writing into the take, the binding's widgets set from the job (model,
    LoRAs, steps, seed, cfg, prompt, negative, the picture's size or the
    voice's length and frame rate, and the preset's other params), then the
    target's own graph code (an edit target's reference images, an audio
    target's reference-audio chain: job.inputs), then pruned to the saver."""
    t = job.target
    b = t.binding
    g = copy.deepcopy(base)
    J.prepare_saver(g, b)
    saver = J.node_of(g, b.saver_class)
    g[saver]["inputs"]["sidecar"] = os.path.abspath(sidecar) if sidecar else ""
    preset = t.presets.get("final")
    model = job.model or (preset.model if preset is not None else "")
    if model:
        J.patch_param(g, b, "model", model)
    specs = b.specs("loras")
    if job.loras:
        J.apply_loras(g, [dict(lo) for lo in job.loras], specs[0] if specs else None)
    elif specs and job.loras == []:
        J.remove_loras(g, specs[0])
    vals = {"steps": job.steps, "seed": job.seed, "cfg": job.cfg, "prompt": job.prompt,
            "width": job.width, "height": job.height}
    if job.is_audio:
        vals.update(width=None, height=None, length=job.frames, fps=job.fps)
    if job.negative_source != "none":
        vals["negative"] = job.negative
    vals.update(job.values)
    for name, v in vals.items():
        if v is not None and b.specs(name):
            J.patch_param(g, b, name, v)
    if t.supports("patch_graph"):
        t.patch_graph(g, job, dict(job.inputs))
    if b.prune:
        J.prune(g, saver)
    return g


def graph_for(base: dict | None, job: GenJob, take: RefTake, save_node: bool = True,
              lora_clip: bool = True) -> dict:
    """The API graph for one take. krea2: `base` (a loaded krea2_refs_t2i
    workflow) patched with this job's values, or the built-in graph when
    `base` is None; with `save_node` the SaveImage becomes an H3SaveRefTake
    writing into the take (the sidecar path is absolute); without, SaveImage
    stays (prefix PREFIX) and the caller fetches the image (kreagen on an
    older node pack). The graph code is the image target's
    (targets/image/krea2/graph.py). Any other image target, and every audio
    target: image_graph (its workflow is required)."""
    if not job.is_krea2:
        if base is None:
            raise ValueError(f"{job.target.id} needs its workflow ({job.target.binding.workflow_name})")
        return image_graph(base, job, take.paths.sidecar if save_node else None)
    g = take_graph(base, job.prompt, job.negative, job.width, job.height, job.seed,
                   job.steps, job.cfg, job.model, job.loras,
                   take.paths.sidecar if save_node else None, lora_clip)
    # a substitute text encoder / VAE (resolve_models) where the graph loads one
    for param, name in job.values.items():
        m = (job.target.models.get(param) if job.target is not None else None) or {}
        if m.get("class_type") and m.get("field"):
            for k, v in g.items():
                if v["class_type"] == m["class_type"] and m["field"] in v["inputs"]:
                    v["inputs"][m["field"]] = name
    return g


def resolve_workflow(comfy_url: str | None, explicit: str | None = None, target=None):
    """(the target's graph, or None, where). krea2 (the default):
    krea2_refs_t2i as saved in ComfyUI or the repo's, None meaning the
    built-in graph. Any other image target, and every audio target: its
    binding's workflow (required). An audio target's workflow is the repo's
    first: no saved canvas has an audio-only LTX graph, and a canvas of that
    name would be someone's experiment."""
    t = target or IMAGE_TARGET
    b = t.binding
    if t.kind == "audio":
        return J.target_workflow(t, explicit, comfy_url, required=True, prefer_repo=True)
    if t.id == TG.DEFAULT_IMAGE_TARGET:
        return J.resolve_workflow(explicit, b.workflow_name, comfy_url, env=b.env,
                                  required=False)
    return J.target_workflow(t, explicit, comfy_url, required=True)


def target_ready(listing):
    """ready(target) for image_target_for: True when every required model
    file of the target's final pass resolves against ComfyUI's lists
    (`listing`, h3jobs.model_lister's; None: can't tell, so ready)."""
    cache: dict = {}

    def ready(t) -> bool:
        if listing is None:
            return True
        if t.id not in cache:
            try:
                r = TG.resolve_models(t, "final", TG.wanted_files(t, "final"), listing)
                cache[t.id] = not r["blocked"]
            except Exception:
                cache[t.id] = True
        return cache[t.id]
    return ready


def resolve_job_models(job: GenJob, listing, resolve=None, cache=None,
                       extra: dict | None = None) -> list[dict]:
    """Resolve the files a job loads against what is installed
    (targets.resolve_models, as a video job does): a substitute of the same
    family takes a missing file's place (job.model / job.values), a missing
    accelerator switches to the preset's `base` (its model, steps and cfg),
    and a missing required file is returned (the job can't run). Records
    job.resolved and notes. `listing` None: nothing resolved."""
    t = job.target
    if listing is None or t is None:
        return []
    wanted = TG.wanted_files(t, "final")
    if job.model:
        wanted["model"] = job.model
    for k in t.models:
        if k in job.values and isinstance(job.values[k], str):
            wanted[k] = job.values[k]
    if job.loras is not None:
        wanted["loras"] = [dict(lo) for lo in job.loras]
    r = TG.resolve_models(t, "final", wanted, listing, resolve, extra, cache,
                          TG.lora_slot(t, "final"))
    job.resolved = r["resolved"]
    for param, using in r["files"].items():
        if param == "model":
            job.model = using
        else:
            job.values[param] = using
    if r["loras"] is not None:
        job.loras = r["loras"]
    if r["base"] is not None:
        job.base = True
        if r["base"].get("steps") is not None and "steps" not in job.overridden:
            job.steps = int(r["base"]["steps"])
        if r["base"].get("cfg") is not None:
            job.cfg = float(r["base"]["cfg"])
        job.values.update({k: v for k, v in r["values"].items()
                           if k not in ("steps", "cfg") and t.binding.specs(k)})
    job.notes.extend(r["notes"])
    return r["blocked"]


COMPOSITE_FIGURE_HEIGHT = 2 / 3     # a composed reference's figures, of the frame's height
COMPOSITES = "_composites"          # refs/_takes/<key>/_composites/<sha1>.png


def compose_composite(s: Series, job: GenJob) -> None:
    """Compose a job's single composite reference (reference_images): its
    figures side by side over the plate, bottom-aligned, about two thirds of
    the frame's height, at the job's size (comfy_nodes/h3_refsheet.py, run as
    a PIL subprocess of this Python). The file is kept, named by its sha1, in
    refs/_takes/<key>/_composites/, and becomes the reference's `path`.

    A Python without PIL (a CLI run outside ComfyUI) can't compose: the job
    falls back to the first part alone (the first character, else the plate),
    its prompt is rewritten for that one reference (unless overridden) and a
    note says so."""
    from targets.video.ltx2_ingredients import sheet as SH
    comp = job.references[0]
    parts = comp.get("parts") or []
    figures = [p for p in parts if p.get("role") != "plate"]
    plate = next((p for p in parts if p.get("role") == "plate"), None)
    d = os.path.join(takes_dir(job.ref), COMPOSITES)
    os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, f".tmp_{uuid.uuid4().hex}.png")
    spec = {"mode": "composite", "width": job.width, "height": job.height,
            "background": "white", "figure_height": COMPOSITE_FIGURE_HEIGHT,
            "plate": ({"path": os.path.abspath(plate["path"])} if plate else None),
            "figures": [{"path": os.path.abspath(f["path"]),
                         **({"crop": f["crop"]} if f.get("crop") else {})} for f in figures]}
    try:
        SH.compose(spec, tmp)
        out = os.path.join(d, f"{T.file_sha1(tmp)}.png")
        os.replace(tmp, out)
        comp["path"] = out
    except SH.SheetError as e:
        # a view cut from a sheet needs PIL too: an uncut figure, else the plate
        first = dict(next((f for f in figures if not f.get("crop")), None)
                     or plate or figures[0])
        was = job.ref.kind == "keyframe" and job.prompt == keyframe_prompt(
            s, job.ref, job.target, [comp])
        job.references = [first]
        job.notes.append(f"couldn't compose one reference from {len(parts)} ({e}); "
                         f"sent {first['name']} alone")
        if was:                                           # not a typed or override prompt
            job.prompt = keyframe_prompt(s, job.ref, job.target, job.references)
    finally:
        if os.path.isfile(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def stage_references(s: Series, job: GenJob, comfy=None) -> dict:
    """Upload a job's reference images to ComfyUI's input folder
    (h3pipe/<sha1>.png, through POST /upload/image) and record their names in
    job.inputs ({"references": [...]}). A character view cut out of a
    4-panel sheet (`crop`) is composed first (comfy_nodes/h3_refsheet.py,
    a PIL subprocess). Without `comfy` (a dry run) only the names are worked
    out."""
    if not job.references:
        job.inputs = {}
        return {}
    if job.references[0].get("role") == "composite":
        if comfy is None:
            job.inputs = {"references": [f"{J.INPUT_SUBFOLDER}/dry_run_composite.png"]}
            return job.inputs
        compose_composite(s, job)
    names = []
    for r in job.references:
        path = r["path"]
        if r.get("crop"):
            from targets.video.ltx2_ingredients import sheet as SH
            d = takes_dir(job.ref)
            os.makedirs(d, exist_ok=True)
            tmp = os.path.join(d, f".tmp_ref_{uuid.uuid4().hex}.png")
            if comfy is None:
                names.append(f"{J.INPUT_SUBFOLDER}/dry_run_{T.safe_id(r.get('subject') or 'ref')}"
                             f"_{r.get('view')}.png")
                continue
            try:
                SH.compose({"width": 1024, "height": 1024, "background": "white", "gap": 0,
                            "panels": [{"path": os.path.abspath(path), "crop": r["crop"],
                                        "fit": "cover"}]}, tmp)
                name = J.input_name(tmp)
                comfy.upload_input(tmp, name)
            finally:
                if os.path.isfile(tmp):
                    os.remove(tmp)
            names.append(name)
            continue
        name = J.input_name(path)
        if comfy is not None:
            comfy.upload_input(path, name)
        names.append(name)
    job.inputs = {"references": names}
    return job.inputs


def stage_voice_reference(s: Series, job: GenJob, comfy=None) -> dict:
    """Upload the voice sample an audio target copies (its speaker identity)
    to ComfyUI's input folder and record its name in job.inputs
    ({"reference_audio": "h3pipe/<sha1>.wav"}). Only for a target whose
    `capabilities.reference_audio` is true and a ref whose live file is on
    disk; otherwise nothing is staged and the target generates the voice
    from the wording alone. Without `comfy` (a dry run) only the name is
    worked out."""
    if not job.is_audio or not job.target.capabilities().get("reference_audio"):
        return job.inputs
    path = job.ref.file
    if not path or not os.path.isfile(path):
        return job.inputs
    name = J.input_name(path)
    if comfy is not None:
        comfy.upload_input(path, name)
    job.inputs = dict(job.inputs, reference_audio=name)
    job.references = [{"role": "voice", "subject": job.ref.subject,
                       "name": job.ref.name, "kind": "voice", "path": path}]
    return job.inputs


def queue_generate(s: Series, req: GenRequest, comfy, base=None,
                   save_node: bool = True, lora_clip: bool = True,
                   rng: random.Random | None = None, listing=None, resolve=None,
                   cache=None) -> dict:
    """Plan, reserve and queue every take a request makes, without waiting.

    `base` is krea2's workflow (a graph, or None for the built-in one), or a
    function (image target id -> graph); any other image target's workflow
    is resolved from `comfy` (its saved copy, else the repo's). `listing`
    (h3jobs.model_lister's) resolves each job's model files by family
    (resolve_job_models) and says whether the default keyframe target is
    ready; a job whose required files aren't installed is an error. Edit
    targets' reference images are uploaded first (stage_references).

    Returns {"queued": [{ref, view, take, prompt_id, seed, seed_source,
    target}], "errors": [{ref, view, take?, error}]}; a take that fails to
    queue is marked failed and the others still go. Raises RefError /
    UnknownRef for a request that can't be planned at all."""
    jobs = plan_generate(s, req, rng=rng, ready=target_ready(listing))
    out = {"queued": [], "errors": []}
    graphs: dict = {}

    def graph_base(t):
        if callable(base):
            return base(t.id)
        if t.id == TG.DEFAULT_IMAGE_TARGET:
            return base
        if t.id not in graphs:
            graphs[t.id], _ = resolve_workflow(getattr(comfy, "base", None), None, t)
        return graphs[t.id]

    extra = {}
    try:
        extra = TG.series_model_families(s.series_cfg)
    except ValueError:
        extra = {}
    for job in jobs:
        blocked = resolve_job_models(job, listing, resolve, cache, extra)
        if blocked:
            out["errors"].append({"ref": job.ref.id, "view": job.view, "target": job.target.id,
                                  "error": "model files not installed: "
                                           + "; ".join(TG.missing_message(m) for m in blocked),
                                  "missing_files": [
                                      {k: m.get(k) for k in ("param", "tier", "want", "family",
                                                             "folder", "url", "source")}
                                      for m in blocked]})
            continue
        try:
            g0 = graph_base(job.target)
            up = comfy if hasattr(comfy, "upload_input") else None
            stage_references(s, job, up)
            stage_voice_reference(s, job, up)
            take = start_gen(s, job)
        except Exception as e:
            out["errors"].append({"ref": job.ref.id, "view": job.view, "error": str(e)[:800]})
            continue
        try:
            pid = comfy.queue(graph_for(g0, job, take, save_node, lora_clip))
            mark_queued(take, pid)
        except Exception as e:
            mark_failed(take, str(e)[:800])
            out["errors"].append({"ref": job.ref.id, "view": job.view, "take": take.take,
                                  "error": str(e)[:800]})
            continue
        out["queued"].append({"ref": job.ref.id, "view": job.view, "take": take.take,
                              "prompt_id": pid, "seed": job.seed,
                              "seed_source": job.seed_source, "target": job.target.id})
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
    outs = list(((entry or {}).get("outputs") or {}).values())
    imgs = [i for o in outs for i in (o.get("images") or [])]
    auds = [a for o in outs for a in (o.get("audio") or [])]
    if imgs and comfy is not None and hasattr(comfy, "view"):
        return close_take(take, comfy.view(imgs[0]),
                          "fetched from ComfyUI's output folder: the graph's SaveImage "
                          f"wrote {imgs[0].get('filename')}")
    if auds and comfy is not None and hasattr(comfy, "view"):
        name = auds[0].get("filename") or ""
        return close_take(take, comfy.view(auds[0]),
                          "fetched from ComfyUI's output folder: the graph's SaveAudio "
                          f"wrote {name}",
                          ext=os.path.splitext(name)[1].lower() or None)
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
# filling needed keyframes (the CLI's "generate missing")
# ---------------------------------------------------------------------------

def generate_and_wait(s: Series, req: GenRequest, comfy, base=None, timeout: float = 900,
                      listing=None, resolve=None, cache=None, pick: bool | None = None,
                      log=print) -> list[RefTake]:
    """queue_generate, then wait for each take (wait_take), then pick the
    first finished one when the ref has no live file and wasn't cleared (the
    auto-pick rule; `pick` True always, False never). Returns the finished
    takes. RuntimeError when nothing could be queued."""
    ref = find_ref(s, req.ref)
    out = queue_generate(s, req, comfy, base, listing=listing, resolve=resolve, cache=cache)
    for e in out["errors"]:
        log(f"  !! {e['ref']}{' ' + e['view'] if e.get('view') else ''}: {e['error']}")
    if not out["queued"]:
        raise RuntimeError("nothing was queued")
    done = []
    for q in out["queued"]:
        t = get_take(ref, q["view"], q["take"])
        status = wait_take(comfy, t, q["prompt_id"], timeout)
        log(f"  .. {q['ref']}{' ' + q['view'] if q.get('view') else ''} t{q['take']:02d} "
            f"({q['target']}): {status}")
        if status == "ok":
            done.append(t)
    if done and (pick or (pick is None and not (ref.file and os.path.isfile(ref.file))
                          and not is_cleared(load_picks(ref.home), ref.id))):
        for t in done if ref.has_views else done[:1]:
            pick_take(s, ref, t.view, t.take)
            log(f"  -> picked t{t.take:02d}: {ep_rel(s.ep, ref.file)}")
    return done


@dataclass
class FillResult:
    ref: str
    method: str                      # what was done: continuity | generate | import | skip
    detail: str = ""
    take: int | None = None


@dataclass
class KeyframePlan:
    method: str                      # skip | import | continuity | generate
    detail: str = ""                 # skip: why; import: the script's path
    path: str | None = None          # import: the file
    source: T.Take | None = None     # continuity: the video take the frame comes from
    fallback: str = ""               # generate after continuity: why there is no frame


def keyframe_plan(s: Series, shot: str, which: str, pass_: str = "proxy",
                  need: dict | None = None) -> KeyframePlan:
    """How "generate missing" fills one keyframe (keyframe_needs' method):
    `continuity` when the neighbouring shot has a usable take in the pass's
    cut (keyframe_source), else `generate` (with `fallback` saying why); a
    script path is `import`; `none`, or `import` with no path, is `skip`.
    Shared by the CLI (fill_keyframe) and the route (generate_missing)."""
    if need is None:
        need = keyframe_needs(s.ep).get((shot, which)) or {"method": "generate"}
    method = need["method"]
    if method == "none":
        return KeyframePlan("skip", "the script says none")
    if method == "import" and need.get("import_path"):
        src = need["import_path"]
        full = src if os.path.isabs(src) else os.path.normpath(os.path.join(s.ep, src))
        return KeyframePlan("import", src, path=full)
    if method == "import":
        return KeyframePlan("skip", "the script says import: import one (the editor, or "
                                    "POST /h3pipe/refs/import)")
    if method == "continuity":
        try:
            src, _ = keyframe_source(s, shot, which, pass_)
            return KeyframePlan("continuity", source=src)
        except (NotUsable, RefError, UnknownRef) as e:
            return KeyframePlan("generate", fallback=str(e))
    return KeyframePlan("generate")


def import_keyframe(s: Series, ref: Ref, plan: KeyframePlan) -> RefTake:
    """Import the script's file for a keyframe (plan.method "import") and pick it."""
    t = import_take(s, ref, None, plan.path, note="from the script's line")
    pick_take(s, ref, None, t.take)
    return t


def fill_keyframe(s: Series, shot: str, which: str, comfy=None, pass_: str = "proxy",
                  base=None, listing=None, resolve=None, cache=None, dry_run: bool = False,
                  target: str | None = None, timeout: float = 900, log=print) -> FillResult:
    """Fill one needed keyframe by its method (keyframe_needs): `continuity`
    cuts the previous shot's last frame (keyframe_from_take; the pass's cut)
    and falls back to generating when that shot has no usable take; an image
    path in the script is imported; `generate` makes a still with the
    keyframe image target (generate_and_wait, the size of the shot in
    `pass_`). A dry run says what it would do, and the prompt."""
    ref = find_ref(s, f"shot:{shot}:{which}")
    plan = keyframe_plan(s, shot, which, pass_)
    if plan.method == "skip":
        return FillResult(ref.id, "skip", plan.detail)
    if plan.method == "import":
        if dry_run:
            return FillResult(ref.id, "import", f"would import {plan.path}")
        t = import_keyframe(s, ref, plan)
        return FillResult(ref.id, "import", f"imported {plan.detail}", t.take)
    if plan.method == "continuity":
        if dry_run:
            return FillResult(ref.id, "continuity",
                              f"would cut the {'last' if which == 'first' else 'first'} "
                              f"frame of {plan.source.shot} {pass_} t{plan.source.take:02d}")
        try:
            res = keyframe_from_take(s, shot, which, pass_=pass_)
            return FillResult(ref.id, "continuity",
                              f"frame {res.source['frame']} of {res.source['shot']} "
                              f"t{res.source['take']:02d}"
                              + ("" if res.picked else " (not picked)"), res.take.take)
        except (NotUsable, RefError, UnknownRef) as e:
            plan.fallback = str(e)
    if plan.fallback:
        log(f"  .. {ref.id}: no continuity ({plan.fallback}); generating a still instead")
    req = GenRequest(ref.id, target=target, pass_=pass_)
    if dry_run:
        (job,) = plan_generate(s, req, rng=random.Random(0), ready=target_ready(listing))
        refs = references_text(job.references)
        return FillResult(ref.id, "generate",
                          f"would generate with {job.target.id} at {job.width}x{job.height}"
                          + (f", references: {refs}" if refs else "") + f"\n{job.prompt}")
    done = generate_and_wait(s, req, comfy, base, timeout, listing, resolve, cache, log=log)
    return FillResult(ref.id, "generate", f"{len(done)} take(s)",
                      done[0].take if done else None)


def missing_keyframes(s: Series) -> list[tuple[str, str, dict]]:
    """The keyframes "generate missing" fills: every required one, and every
    optional one the script asks for, with no live file and not cleared, in
    script order: [(shot, which, need)]."""
    out = []
    for (shot, which), need in keyframe_needs(s.ep).items():
        if need["need"] != "required" and not (need["script"] and need["script"] != "none"):
            continue
        ref = keyframe_ref(s.ep, shot, which)
        if os.path.isfile(ref.file) or is_cleared(load_picks(ref.home), ref.id):
            continue
        out.append((shot, which, need))
    return out


MISSING_KINDS = ("series", "keyframe")


def _waiting(takes: list[RefTake]) -> str | None:
    """Why a ref with these takes needs no new candidate: one is queued, or
    one has finished and waits to be picked (auto-pick takes it)."""
    if any(t.status == "queued" for t in takes):
        return "a candidate is queued"
    if any(t.usable for t in takes):
        return "a finished candidate is waiting to be picked"
    return None


def missing_series(s: Series, pass_: str = "proxy") -> tuple[list[tuple[Ref, str | None]],
                                                             list[dict]]:
    """The series refs "generate missing" queues: every one this episode
    uses in `pass_` (used_by) with no live file, not cleared and with no
    candidate queued or waiting, as [(ref, view)] (view None: the ref, or a
    character's four views sharing a seed; else the views still missing),
    and [{"ref", "view"?, "reason"}] for missing ones it leaves alone."""
    refs = series_refs(s)
    usage = used_by(s, refs)
    todo, skipped = [], []
    for ref in refs:
        if not usage.get(ref.id, {}).get(pass_) or not ref.path or os.path.isfile(ref.file):
            continue
        why = can_generate(s, ref)
        if why:
            skipped.append({"ref": ref.id, "reason": why})
            continue
        picks = load_picks(ref.home)
        if is_cleared(picks, ref.id):
            skipped.append({"ref": ref.id, "reason": "cleared: pick a candidate to use one"})
            continue
        if not ref.has_views:
            why = _waiting(list_takes(ref))
            if why:
                skipped.append({"ref": ref.id, "reason": why})
            else:
                todo.append((ref, None))
            continue
        need = []
        for v in VIEW_TAGS:
            if picked_take(picks, ref.id, v) is not None:
                continue
            why = ("cleared: pick a candidate to use one" if is_cleared(picks, ref.id, v)
                   else _waiting(list_takes(ref, v)))
            if why:
                skipped.append({"ref": ref.id, "view": v, "reason": why})
            else:
                need.append(v)
        if len(need) == len(VIEW_TAGS):
            todo.append((ref, None))
        else:
            todo += [(ref, v) for v in need]
    return todo, skipped


def generate_missing(s: Series, comfy, pass_: str = "proxy", kinds=MISSING_KINDS,
                     target: str | None = None, keyframe_target: str | None = None,
                     dry_run: bool = False, base=None, listing=None, resolve=None,
                     cache=None, save_node: bool = True) -> dict:
    """POST /h3pipe/refs/generate-missing: fill every missing ref at once,
    without waiting. Series refs (missing_series) get one candidate each
    (queue_generate, the image target `target` or each ref's default);
    needed keyframes (missing_keyframes: required ones and ones the script
    asks for) are filled as `h3.py keyframe --missing` does (keyframe_plan):
    a frame of the neighbouring shot's take (continuity, picked), else a
    still queued with `keyframe_target`; a script path is imported and
    picked. A keyframe with a candidate queued or waiting is skipped.

    Returns {"queued": [{ref, view, take, prompt_id, seed, seed_source,
    target, method}], "picked": [{ref, take, method}], "skipped": [{ref,
    view?, reason}], "errors": [{ref, view?, error, ...}]}. A dry run returns
    the same with nothing written or queued: take and prompt_id null."""
    out = {"queued": [], "picked": [], "skipped": [], "errors": []}
    ready = target_ready(listing)

    def queue(ref: Ref, view, tid, method):
        req = GenRequest(ref.id, view, target=tid, pass_=pass_, note="generate missing")
        try:
            if dry_run:
                for job in plan_generate(s, req, rng=random.Random(0), ready=ready):
                    out["queued"].append({
                        "ref": ref.id, "view": job.view, "take": None, "prompt_id": None,
                        "seed": job.seed if job.seed_source != "new" else None,
                        "seed_source": job.seed_source, "target": job.target.id,
                        "method": method})
                return
            res = queue_generate(s, req, comfy, base, save_node=save_node, listing=listing,
                                 resolve=resolve, cache=cache)
        except (RefError, UnknownRef) as e:
            out["errors"].append({"ref": ref.id, "view": view, "error": str(e)})
            return
        out["queued"] += [dict(q, method=method) for q in res["queued"]]
        out["errors"] += res["errors"]

    if "series" in kinds:
        todo, skipped = missing_series(s, pass_)
        out["skipped"] += skipped
        for ref, view in todo:
            queue(ref, view, target, "generate")
    if "keyframe" in kinds:
        for shot, which, need in missing_keyframes(s):
            ref = keyframe_ref(s.ep, shot, which)
            why = _waiting(list_takes(ref))
            if why:
                out["skipped"].append({"ref": ref.id, "reason": why})
                continue
            plan = keyframe_plan(s, shot, which, pass_, need)
            try:
                if plan.method == "skip":
                    out["skipped"].append({"ref": ref.id, "reason": plan.detail})
                    continue
                if plan.method == "import":
                    t = None if dry_run else import_keyframe(s, ref, plan)
                    out["picked"].append({"ref": ref.id, "take": t.take if t else None,
                                          "method": "import"})
                    continue
                if plan.method == "continuity":
                    if dry_run:
                        out["picked"].append({"ref": ref.id, "take": None,
                                              "method": "continuity"})
                        continue
                    try:
                        # picked: the keyframe has no live file and isn't cleared
                        res = keyframe_from_take(s, shot, which, pass_=pass_)
                        out["picked"].append({"ref": ref.id, "take": res.take.take,
                                              "method": "continuity"})
                        continue
                    except (NotUsable, RefError, UnknownRef):
                        pass                              # no frame: a still instead
            except (RefError, UnknownRef, NotUsable, FfmpegMissing, StitchError,
                    OSError) as e:
                out["errors"].append({"ref": ref.id, "view": None, "error": str(e)[:800]})
                continue
            queue(ref, None, keyframe_target, "generate")
    return out


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
            if os.path.basename(os.path.dirname(sc_path)) == T.TRASH:
                continue                                  # discarded: never swept or listed
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
    # a variant has its own sheet but shares the subject's voice, so a shot of
    # the variant counts as a use of that voice (series_refs lists no voice for
    # a variant, and the shot would otherwise be dropped here)
    variants = SC.variant_of(s.series_cfg)

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
                        who = slot["subject"]
                        add(f"voice:{variants.get(who, who)}", ps, shot["id"])
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
            # what this take was generated FROM: [] when nothing was fed, which
            # is the only way to tell an edit from a text-to-image generate on a
            # target that does both (targets/image/qwen_image_21)
            "references": [{"id": r.get("id"), "name": r.get("name"),
                            "view": r.get("view"), "path": r.get("path")}
                           for r in (sc.get("references") or [])],
            "queued": sc.get("queued"), "finished": sc.get("finished"),
            "comfy_prompt_id": sc.get("comfy_prompt_id"),
            "save_notes": sc.get("save_notes", ""),
            # an upload's own file name (POST /h3pipe/refs/import, multipart)
            **({"original_name": sc["original_name"]} if sc.get("original_name") else {}),
            # a keyframe cut out of a video take (source "frame")
            **({"from": {"shot": sc.get("source_shot"), "take": sc.get("source_take"),
                         "pass": sc.get("source_pass"), "frame": sc.get("source_frame"),
                         "frames": sc.get("source_frames")}}
               if sc.get("source") == "frame" else {}),
            # a voice candidate: how long it was asked to be and what it says
            **({"seconds": sc.get("seconds"), "line": sc.get("line"),
                "line_source": sc.get("line_source")} if ref.is_audio else {}),
            # a voice sample cut out of a video take (source "from_take")
            **({"from": {"shot": sc.get("source_shot"), "take": sc.get("source_take"),
                         "pass": sc.get("source_pass"), "start": sc.get("source_start"),
                         "end": sc.get("source_end")}}
               if sc.get("source") == "from_take" else {})}


def effective(s: Series, ref: Ref, view: str | None, ov_data: dict,
              view_size=VIEW_SIZE, ready=None, defaults: dict | None = None) -> dict | None:
    """What a generate (seed_mode auto, nothing else set) would use now,
    `target` the image target."""
    if can_generate(s, ref):
        return None
    try:
        (job, *_) = plan_generate(s, GenRequest(ref.id, view, view_size=view_size),
                                  ov_data, rng=random.Random(0), ready=ready,
                                  defaults=defaults)
    except (RefError, UnknownRef):
        return None
    out = {"prompt": job.prompt, "seed": job.seed if job.seed_source != "new" else None,
           "seed_source": job.seed_source, "model": job.model, "loras": job.loras,
           "steps": job.steps, "width": job.width, "height": job.height,
           "target": job.target.id if job.target is not None else None}
    if job.is_audio:
        out.update(width=None, height=None, seconds=job.seconds, line=job.line,
                   line_source=job.line_source,
                   max_seconds=job.target.seconds_range()[1])
    return out


def edit_refs(s: Series, ref: Ref, target) -> list[dict]:
    """The reference images a generate of keyframe `ref` would feed an edit
    `target` now (reference_images, after its max_refs): [{"id", "role",
    "view"?, "path" (relative to the episode)}]."""
    shot, _ = ref_shot(ref)
    sh, sq = shot_ir(s.ep, shot)
    if sh is None:
        return []
    def one(r):
        if r["role"] == "composite":
            return {"id": None, "role": "composite", "path": None, "name": r["name"],
                    "parts": [one(p) for p in r["parts"]]}
        d = {"id": f"subject:{r['subject']}" if r["role"] == "subject"
             else f"location:{r['location']}", "role": r["role"],
             "path": ep_rel(s.ep, r["path"])}
        if r.get("view"):
            d["view"] = r["view"]
        if r.get("crop"):
            d["crop"] = dict(r["crop"])
        return d
    return [one(r) for r in reference_images(s, sh, sq, target)]


def ref_json(s: Series, ref: Ref, usage: dict | None = None,
             ov_data: dict | None = None, picks: dict | None = None,
             view_size=VIEW_SIZE, needs: dict | None = None, ready=None,
             defaults: dict | None = None, shared: dict | None = None) -> dict:
    ov_data = ov_data if ov_data is not None else load_overrides(ref.home)
    picks = picks if picks is not None else load_picks(ref.home)
    if defaults is None:
        try:
            defaults = image_defaults(s, ready)
        except RefError:
            defaults = None
    live = ref.file
    exists = bool(live and os.path.isfile(live))
    o = override_view(s, ref, None, ov_data, view_size)
    out = {
        "id": ref.id, "key": ref.key, "scope": ref.scope, "kind": ref.kind,
        "name": ref.name, "subject": ref.subject,
        # a wardrobe variant keeps the subject's name (it is the same character,
        # and that name goes into every prompt), so the listing says whose
        # variant it is rather than inventing a second name for them
        "of": ref.entry.get("of") or None,
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
            eff = effective(s, ref, v, ov_data, view_size, ready, defaults)
            out["views"].append({
                "view": v, "picked": picked_take(picks, ref.id, v),
                "cleared": is_cleared(picks, ref.id, v),
                "prompt": eff["prompt"] if eff else built_prompt(s, ref, v, view_size),
                # P9: the series config's wording for THIS view, before any
                # override -- what a prompt edit is diffed against and reverted to
                "built_prompt": built_prompt(s, ref, v, view_size),
                "override": {"fields": vo["fields"], "stale": vo["stale"],
                             "values": vo["values"], "own": vo["own"]},
                "effective": eff,
                "takes": [take_json(s.ep, ref, t) for t in list_takes(ref, v)]})
        # the image target the views generate with (they share one)
        first = next((v["effective"] for v in out["views"] if v["effective"]), None)
        out["effective"] = {"target": first["target"]} if first else None
        # P8: the ref's own takes are its supplied whole sheets (nothing else
        # can be a take of a character), and `live_from` says which route wrote
        # the file that is live now
        out["takes"] = [take_json(s.ep, ref, t) for t in list_takes(ref, SHEET_VIEW)]
        out["picked"] = picked_take(picks, ref.id, SHEET_VIEW)
        out["sheet_cleared"] = is_cleared(picks, ref.id, SHEET_VIEW)
    else:
        out["takes"] = [take_json(s.ep, ref, t) for t in list_takes(ref)]
        out["picked"] = picked_take(picks, ref.id)
        out["effective"] = effective(s, ref, None, ov_data, view_size, ready, defaults)
        if out["effective"]:
            out["prompt"] = out["effective"]["prompt"]
    out["cleared"] = is_cleared(picks, ref.id)
    out["live_from"] = live_from(picks, ref)
    # who else reads this live file, and whose pick wrote the one there now
    # (a pick or a clear here is a change every one of them sees)
    if shared is None:
        shared = shared_context(s.ep)
    out["shared_with"] = shared["shared"].get(_real_path(live), []) if live else []
    out["live_owner"] = shared["owner"].get(out["sha1"]) if out["sha1"] else None
    if ref.kind == "keyframe":
        shot, which = ref_shot(ref)
        need = (needs if needs is not None else keyframe_needs(s.ep)).get((shot, which))
        out.update(shot=shot, which=which,
                   need=need["need"] if need else None,
                   method=need["method"] if need else None,
                   target=need["target"] if need else None,
                   requested=bool(need and need["script"] and need["script"] != "none"),
                   script=need["script"] if need else None,
                   reads=need["reads"] if need else None)
        if need and need.get("import_path"):
            out["import_path"] = need["import_path"]
        tid = (out["effective"] or {}).get("target")
        if tid:
            t = TG.load_target(tid, "image")
            if t.capabilities()["mode"] == "edit":
                out["edit_refs"] = edit_refs(s, ref, t)
    # flat aliases the editor reads (docs/API.md): the override's values, and
    # the series config's prompt before any override, for the prompt diff
    out["override_values"] = out["override"]["values"]
    out["built_prompt"] = built_prompt(s, ref, None, view_size)
    return out


def refs_listing(ep: str, view_size=VIEW_SIZE, ready=None) -> dict:
    """GET /h3pipe/refs: {"refs": every ref of the episode's series config,
    then the shot keyframes it needs or has (keyframe_refs), "defaults": the
    episode's image-target defaults (image_defaults)}. `ready(target)` says
    whether an image target can render here (target_ready)."""
    s = load_series(ep)
    try:
        defaults = image_defaults(s, ready)
    except RefError:
        defaults = None
    needs = keyframe_needs(s.ep)
    refs = series_refs(s) + keyframe_refs(s.ep, needs)
    usage = used_by(s, refs)
    shared = shared_context(s.ep)
    cache: dict[str, tuple[dict, dict]] = {}
    out = []
    for r in refs:
        if r.home not in cache:
            cache[r.home] = (load_overrides(r.home), load_picks(r.home))
        ov, pk = cache[r.home]
        out.append(ref_json(s, r, usage, ov, pk, view_size, needs, ready, defaults, shared))
    return {"refs": out, "defaults": defaults}


def list_refs(ep: str, view_size=VIEW_SIZE, ready=None) -> list[dict]:
    """Every ref of the episode's series config, then the shot keyframes it
    needs or has (refs_listing's refs)."""
    return refs_listing(ep, view_size, ready)["refs"]


def get_ref_json(ep: str, ref_id: str) -> dict:
    s = load_series(ep)
    ref = find_ref(s, ref_id)
    return ref_json(s, ref, used_by(s, [ref]))


# ---------------------------------------------------------------------------
# P8: supplying files in bulk -- which slot does a file name mean?
# ---------------------------------------------------------------------------
#
# One implementation, in Python, called by both the editor (POST
# /h3pipe/refs/match) and the CLI (`h3.py supply`). The rules are worth stating
# once and being able to argue with, and two copies of them would drift.
#
# Matching is exact on a normalised name, never fuzzy: a file matches a slot
# when its name IS one of that slot's names. Anything that matches two slots is
# reported unmatched rather than guessed, because the cost of a wrong guess is a
# render with the wrong character in it.

# a trailing revision marker people add and don't mean: walker_sheet_v2.png
REV_RE = re.compile(r"_(?:v\d+|\d+|copy|final|new|edit|edited|fixed)$")
# the word in a view tag, without its sheet-order number (01_threequarter)
VIEW_WORDS = {v: v.split("_", 1)[1] if "_" in v else v for v in VIEW_TAGS}


def slugify(name: str) -> str:
    """A file name as matching sees it: no folders, no extension, lower case,
    every run of anything but letters and digits a single underscore."""
    stem = os.path.splitext(os.path.basename(str(name or "")))[0]
    return re.sub(r"[^a-z0-9]+", "_", stem.lower()).strip("_")


def _path_stem(ref: Ref, view: str | None = None) -> str | None:
    """The slug of the file the series config names for this ref -- the
    strongest signal there is, because it is the name the show already uses."""
    if view and view != SHEET_VIEW:
        return None
    return slugify(ref.path) if ref.path else None


def slot_names(ref: Ref, view: str | None) -> list[str]:
    """Every name that means this slot, best first."""
    out = []
    stem = _path_stem(ref, view)
    if stem:
        out.append(stem)
    rid = slugify(ref.subject or ref.id.split(":", 1)[-1])
    if ref.kind == "keyframe":
        shot, which = ref_shot(ref)
        out += [f"{slugify(shot)}_{which}", f"{which}_{slugify(shot)}"]
        return list(dict.fromkeys(out))
    if view and view != SHEET_VIEW:
        word = VIEW_WORDS[view]
        out += [f"{rid}_{view}", f"{rid}_{word}", f"{view}_{rid}", f"{word}_{rid}"]
        return list(dict.fromkeys(out))
    # the ref's own file: a character's sheet, a prop, a plate, a voice sample
    out.append(rid)
    if ref.has_views:
        out += [f"{rid}_sheet", f"{rid}_sheet_4panel", f"{rid}_4panel", f"sheet_{rid}"]
    elif ref.is_audio:
        out += [f"{rid}_voice", f"{rid}_sample", f"voice_{rid}"]
    elif ref.kind == "location":
        out += [f"{rid}_plate", f"bg_{rid}", f"plate_{rid}"]
    return list(dict.fromkeys(out))


def supply_slots(s: Series, refs: list[Ref] | None = None) -> list[dict]:
    """Every slot a supplied file could go into, with the names that mean it:
    each ref, each of a character's four views, and a character's whole sheet."""
    refs = refs if refs is not None else series_refs(s) + keyframe_refs(s.ep)
    out = []
    for ref in refs:
        if not ref.path and not ref.is_audio:
            continue                    # nothing to be live: nowhere to put a file
        views = [SHEET_VIEW, *VIEW_TAGS] if ref.has_views else [None]
        for v in views:
            out.append({"ref": ref, "view": v, "audio": ref.is_audio,
                        "names": slot_names(ref, v)})
    return out


def _slot_json(slot: dict) -> dict:
    ref = slot["ref"]
    return {"ref": ref.id, "view": slot["view"], "name": ref.name,
            "kind": ref.kind, "audio": bool(slot["audio"]),
            "path": ep_rel(ref.home, ref.file) if ref.file else None}


def match_files(s: Series, names: list[str], refs: list[Ref] | None = None) -> dict:
    """Which slot each supplied file name means (P8).

    {"matched": [{"file", "ref", "view", "name", "kind", "audio", "path",
                  "why"}],
     "unmatched": [{"file", "why"}]}

    A name matches a slot when its slug is one of the slot's names (see
    slot_names); the file's extension has to suit the slot (audio for a voice,
    an image for everything else). A trailing revision marker (`_v2`, `_final`)
    is ignored, and said so in `why`. Two slots matching the same name, or two
    files matching the same slot, leave both files unmatched with the reason:
    nothing is guessed and nothing is written here."""
    slots = supply_slots(s, refs)
    by_name: dict[str, list[dict]] = {}
    for slot in slots:
        for n in slot["names"]:
            by_name.setdefault(n, []).append(slot)

    matched, unmatched = [], []
    claimed: dict[tuple, list[str]] = {}
    for f in names or []:
        name = str(f)
        slug = slugify(name)
        if not slug:
            unmatched.append({"file": name, "why": "the file has no name to match on"})
            continue
        ext = os.path.splitext(name)[1].lower()
        audio, image = ext in AUDIO_EXTS, ext in IMAGE_EXTS
        if not (audio or image):
            unmatched.append({"file": name,
                              "why": f"{ext or 'a file with no extension'} is not a picture "
                                     f"({', '.join(IMAGE_EXTS)}) or a sound "
                                     f"({', '.join(AUDIO_EXTS)})"})
            continue
        trimmed, note = slug, ""
        if slug not in by_name:
            cut = REV_RE.sub("", slug)
            if cut != slug and cut in by_name:
                trimmed, note = cut, f", ignoring the trailing '{slug[len(cut) + 1:]}'"
        hits = by_name.get(trimmed) or []
        if not hits:
            unmatched.append({"file": name, "why": f"no ref or view is named '{slug}'"})
            continue
        fit = [h for h in hits if h["audio"] == audio]
        if not fit:
            kinds = "audio" if hits[0]["audio"] else "an image"
            unmatched.append({"file": name,
                              "why": f"{_slot_json(hits[0])['name']} takes {kinds}, "
                                     f"and this file isn't"})
            continue
        if len(fit) > 1:
            where = ", ".join(sorted(f"{h['ref']}{' ' + h['view'] if h['view'] else ''}"
                                     for h in fit))
            unmatched.append({"file": name, "why": f"'{trimmed}' could be {where}"})
            continue
        slot = fit[0]
        key = (slot["ref"].id, slot["view"])
        claimed.setdefault(key, []).append(name)
        matched.append(dict(_slot_json(slot), file=name,
                           why=f"'{trimmed}' is {_slot_json(slot)['name']}"
                               f"{' ' + slot['view'] if slot['view'] else ''}{note}"))

    # two files for one slot: neither wins, because which one went live would
    # come down to the order the browser handed them over
    clash = {k: v for k, v in claimed.items() if len(v) > 1}
    if clash:
        keep = []
        for m in matched:
            key = (m["ref"], m["view"])
            if key in clash:
                others = [x for x in clash[key] if x != m["file"]]
                unmatched.append({"file": m["file"],
                                  "why": f"so is {', '.join(others)}: rename one"})
            else:
                keep.append(m)
        matched = keep
    unmatched.sort(key=lambda x: str(x["file"]).lower())
    return {"matched": matched, "unmatched": unmatched}


def match_names(ep: str, names: list[str]) -> dict:
    """POST /h3pipe/refs/match: match_files for an episode on disk."""
    return match_files(load_series(ep), names)


def supply_files(ep: str, paths: list[str], ref_id: str | None = None,
                 view: str | None = None, pick: bool = True,
                 dry_run: bool = False) -> dict:
    """P8: put files that already exist into their ref slots.

    With `ref_id` (and `view` for a character), one file goes into that slot.
    Without it, every file is matched to a slot by name (match_files) and only
    the matches are taken; the rest are reported and left alone. Each file
    becomes an imported take, picked by default, so it goes live.

    {"matched": [...], "unmatched": [...], "supplied": [{file, ref, view, take,
     live}], "failed": [{file, ref, view, error}]}. `dry_run` fills matched and
    unmatched and writes nothing."""
    s = load_series(ep)
    files = []
    for p in paths:
        full = os.path.abspath(p)
        if os.path.isdir(full):
            for n in sorted(os.listdir(full), key=str.lower):
                f = os.path.join(full, n)
                if os.path.isfile(f) and os.path.splitext(n)[1].lower() in IMAGE_EXTS + AUDIO_EXTS:
                    files.append(f)
        elif os.path.isfile(full):
            files.append(full)
        else:
            raise FileNotFoundError(f"no file or folder at {p}")
    if not files:
        raise RefError("no image or audio files to supply")

    if ref_id:
        if len(files) != 1:
            raise RefError(f"--ref takes one file, not {len(files)}")
        ref = find_ref(s, ref_id)
        view = check_view(ref, view, required=True, allow_sheet=True)
        plan = [{"file": files[0], "ref": ref.id, "view": view, "name": ref.name,
                 "why": "named on the command line"}]
        out = {"matched": list(plan), "unmatched": []}
    else:
        out = match_files(s, files)
        plan = out["matched"]
    out["supplied"], out["failed"] = [], []
    if dry_run:
        return out

    for m in plan:
        ref = find_ref(s, m["ref"])
        try:
            t = import_take(s, ref, m["view"], m["file"],
                            original_name=os.path.basename(m["file"]))
            live = None
            if pick:
                live = pick_take(s, ref, m["view"], t.take).live
            out["supplied"].append({"file": m["file"], "ref": ref.id, "view": m["view"],
                                    "take": t.take, "live": live})
        except (RefError, NotUsable, StitchError, OSError) as e:
            out["failed"].append({"file": m["file"], "ref": ref.id, "view": m["view"],
                                  "error": str(e)})
    return out


def cmd_supply(root: str, argv: list[str]) -> int:
    """`python h3.py supply <episode> <file-or-folder>... [--ref id[:view]]
    [--no-pick] [--dry-run]` -- the CLI half of P8, and the way to fill a show
    whose pictures are already on disk."""
    paths, ref_id, view, pick, dry = [], None, None, True, False
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--ref" and i + 1 < len(argv):
            spec = argv[i + 1]
            # subject:ada:02_side -- the view is the part after the ref id
            bits = spec.split(":")
            if len(bits) > 2:
                ref_id, view = ":".join(bits[:2]), bits[2]
            else:
                ref_id = spec
            i += 2
            continue
        if a == "--view" and i + 1 < len(argv):
            view = argv[i + 1]
            i += 2
            continue
        if a in ("--no-pick", "--dry-run"):
            pick, dry = (pick and a != "--no-pick"), (dry or a == "--dry-run")
            i += 1
            continue
        if a.startswith("-"):
            print(f"  !! unknown flag {a}")
            return 2
        paths.append(a)
        i += 1
    if not paths:
        print("  !! usage: python h3.py supply <episode> <file-or-folder>... "
              "[--ref id[:view]] [--no-pick] [--dry-run]")
        return 2
    try:
        r = supply_files(root, paths, ref_id, view, pick, dry)
    except (RefError, UnknownRef, FileNotFoundError) as e:
        print(f"  !! {e}")
        return 1

    for m in r["matched"]:
        where = f"{m['ref']}{' ' + m['view'] if m['view'] else ''}"
        print(f"  {os.path.basename(m['file']):34} -> {where:30} {m['why']}")
    for u in r["unmatched"]:
        print(f"  {os.path.basename(u['file']):34} -- {u['why']}")
    if dry:
        print(f"\n  -- dry run: {len(r['matched'])} would be supplied, "
              f"{len(r['unmatched'])} left alone")
        return 0
    for sup in r["supplied"]:
        print(f"  -> {sup['ref']}{' ' + sup['view'] if sup['view'] else ''} "
              f"take {sup['take']}{' (live)' if sup['live'] else ''}")
    for f in r["failed"]:
        print(f"  !! {os.path.basename(f['file'])}: {f['error']}")
    print(f"\n  -- {len(r['supplied'])} supplied, {len(r['unmatched'])} unmatched, "
          f"{len(r['failed'])} failed")
    return 1 if r["failed"] else 0


# ---------------------------------------------------------------------------
# a live file shared with other episodes
# ---------------------------------------------------------------------------
#
# With the layout the shows use -- a series config per episode, pictures named
# `../refs/...` one level up -- one live file is read by every episode that
# names it, so in a real show nearly every ref is shared and saying so is not
# worth a word on its own. What is worth saying is who owns the file that is
# live NOW:
#
#   * Generating candidates is private to an episode: they go to its own
#     `refs/_takes/`, and auto_pick refuses to act when a live file exists.
#     Nothing about a regenerate reaches a neighbour.
#   * A pick, an upload or supply with `pick`, and a stitch of the fourth view
#     OVERWRITE the shared file. The other episodes' takes then go `stale: ref`.
#     Recoverable, as long as whoever owned the file still has that take.
#   * A clear, and a discard of the pick, DELETE it -- blocking every episode
#     that needs it until something is picked again.
#   * A file nobody's pick record matches (put there by hand, or replaced
#     outside the editor) has no candidate behind it anywhere. Overwriting that
#     one loses it for good, and that is the case worth stopping for.
#
# This layer only reports. The editor confirms and the CLI asks for --yes.

def sibling_episodes(ep: str) -> list[str]:
    """The other episode folders beside `ep`: a sibling with a series config of
    its own or sharing the one above, and a script."""
    parent = os.path.dirname(os.path.abspath(os.path.normpath(ep)))
    here = os.path.normcase(os.path.abspath(ep))
    out = []
    try:
        names = sorted(os.listdir(parent), key=str.lower)
    except OSError:
        return out
    for n in names:
        d = os.path.join(parent, n)
        if n.startswith((".", "_")) or not os.path.isdir(d):
            continue
        if os.path.normcase(os.path.abspath(d)) == here:
            continue
        if E.episode_series_config(d) and E.episode_script(d):
            out.append(d)
    return out


def ep_name(ep: str) -> str:
    return os.path.basename(os.path.normpath(os.path.abspath(ep)))


# Both of these are read for every episode beside this one on every listing, so
# each is memoised on the file it came from (its path, mtime and size): a
# neighbour's config hardly ever changes, its picks change on every pick, and a
# change to either is picked up on the next call. Measured on a 10-episode show
# with 131 refs each: 0.6s a listing cold, ~0.01s warm.
_PATHS: dict[str, tuple[tuple, set]] = {}
_SHA1S: dict[str, tuple[tuple, set]] = {}


def _stamp(path: str | None) -> tuple:
    if not path:
        return ()
    try:
        st = os.stat(path)
        return (os.path.normcase(os.path.abspath(path)), st.st_mtime_ns, st.st_size)
    except OSError:
        return (os.path.normcase(os.path.abspath(path)), 0, -1)


def _live_paths(ep: str) -> set[str]:
    """The absolute live file of every ref an episode names."""
    key = os.path.normcase(os.path.abspath(ep))
    sig = _stamp(E.episode_series_config(ep))
    hit = _PATHS.get(key)
    if hit and hit[0] == sig:
        return hit[1]
    try:
        s = load_series(ep)
        out = {_real_path(r.file) for r in series_refs(s) if r.file}
    except Exception:                    # a neighbour we can't read says nothing
        out = set()
    _PATHS[key] = (sig, out)
    return out


def _real_path(path: str) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def _recorded_sha1s(ep: str) -> set[str]:
    """Every sha1 an episode's picks recorded -- what it believes it wrote into
    a live file (a view's stitch and a supplied sheet included)."""
    cfg = E.episode_series_config(ep)
    home = os.path.dirname(os.path.abspath(cfg)) if cfg else ep
    key = os.path.normcase(os.path.abspath(ep))
    sig = _stamp(os.path.join(home, PICKS_FILE))
    hit = _SHA1S.get(key)
    if hit and hit[0] == sig:
        return hit[1]
    out = set()
    for block in (load_picks(home).get("refs") or {}).values():
        if not isinstance(block, dict):
            continue
        out.update(x for x in [block.get("sha1")] if x)
        for v in (block.get("views") or {}).values():
            if isinstance(v, dict) and v.get("sha1"):
                out.add(v["sha1"])
    _SHA1S[key] = (sig, out)
    return out


def shared_context(ep: str) -> dict:
    """Who else reads this episode's live files, computed ONCE for a listing:

    {"shared": {an absolute live path: [the other episodes reading it]},
     "owner":  {a sha1: the episode whose pick wrote it}}

    `owner` covers this episode and its siblings, so a ref's owner is a lookup
    on the sha1 the listing already computed -- no extra file reads per ref.
    Empty `shared` is the one-folder layout, where nothing is shared.

    Two episodes that picked byte-identical files record one sha1 between them,
    and this episode wins it. That is arbitrary and it does not matter: the file
    they would write over each other is the same file."""
    mine = _live_paths(ep)
    owner = {sha: ep_name(ep) for sha in _recorded_sha1s(ep)}
    shared: dict[str, list[str]] = {}
    if not mine:
        return {"shared": shared, "owner": owner}
    for sib in sibling_episodes(ep):
        name = ep_name(sib)
        both = mine & _live_paths(sib)      # one load of that config, not two
        if not both:
            continue
        for path in both:
            shared.setdefault(path, []).append(name)
        for sha in _recorded_sha1s(sib):
            owner.setdefault(sha, name)
    return {"shared": {k: sorted(set(v)) for k, v in shared.items()}, "owner": owner}
