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

The prompt wording for every ref lives here, and only here: h3build writes the
refs_todo prompts with it, and kreagen and the editor generate with it.

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

# ---------------------------------------------------------------------------
# wording: the one source for every reference prompt
# ---------------------------------------------------------------------------

# A character sheet is generated as four square views and stitched (a 4096x1024
# canvas is far outside any image model's training distribution). The views,
# in sheet order, left to right:
VIEWS = [
    ("01_threequarter", "a three-quarter view of the full figure from head to feet, "
                        "turned slightly toward the viewer's left, standing straight "
                        "with arms relaxed at the sides"),
    ("02_side",         "a direct side profile of the full figure from head to feet, "
                        "facing the viewer's right, standing straight with arms "
                        "relaxed at the sides"),
    ("03_back",         "the full figure seen from directly behind, head to feet, "
                        "standing straight with arms relaxed at the sides"),
    ("04_face",         "a head-and-shoulders close-up, facing the viewer, "
                        "neutral expression"),
]
VIEW_TAGS = [tag for tag, _ in VIEWS]
VIEW_DESC = dict(VIEWS)

VIEW_TMPL = ("A single character reference view on a plain flat neutral background, "
             "no scene and no props, the whole figure inside the frame with margin "
             "on every side: {view}. {design}. Drawn as {look}. Output {w}x{h}.")


def view_prompt(view: str, design: str, look: str, w: int, h: int) -> str:
    """The prompt for one view of a character sheet (`view` is a VIEWS tag)."""
    return VIEW_TMPL.format(view=VIEW_DESC[view], design=design, look=look, w=w, h=h)


def sheet_prompt(design: str, look: str) -> str:
    """The whole 4-panel sheet, described for someone making it by hand
    (refs_todo). Generating uses view_prompt four times instead."""
    return (f"A character model sheet on a plain flat background: FOUR panels side "
            f"by side in a single horizontal strip, left to right — three-quarter "
            f"body, side profile full body, back view full body, and a "
            f"head-and-shoulders facial close-up. The SAME character in all four. "
            f"{design}. Drawn as {look}. "
            f"Output 4096x1024 or larger.")


def object_prompt(design: str, look: str) -> str:
    """A prop or vehicle reference."""
    return (f"A single clean three-quarter view of one object on a plain flat "
            f"background, no scene around it. {design}. Drawn as "
            f"{look}. Output 1024x1024 or larger.")


def plate_prompt(look: str, description: str) -> str:
    """A location's background plate."""
    return (f"A background plate drawn as {look}. An empty establishing "
            f"view of {description}. No characters, no props, no figures in frame — "
            f"the environment only. Wide framing that shows the layout of the space.")


def voice_prompt(name: str, voice: str) -> str:
    """What a voice sample should be (nothing generates voices yet)."""
    return (f"A 5-15 second clean recording of {name} speaking. "
            f"Voice: {voice}.")


# ---------------------------------------------------------------------------
# the image model stack (krea2 turbo by default) and its graph
# ---------------------------------------------------------------------------

REFS_WORKFLOW = "krea2_refs_t2i.json"
SAVER = "H3SaveRefTake"

UNET = "krea2_turbo_fp8_scaled.safetensors"
CLIP = "qwen3vl_4b_fp8_scaled.safetensors"
CLIPTYPE = "krea2"
VAE = "wan_2.1_vae.safetensors"
# No style LoRA by default: references have to match whatever look series.json
# asks for, and a realism LoRA fights a storybook one (and vice versa).
LORA = ""
LORA_M = 1.0
LORA_C = 1.0
STEPS = 12
CFG = 1.0
SAMPLER = "euler"
SCHED = "beta"
PREFIX = "h3refs/tmp"      # SaveImage prefix, when the graph keeps SaveImage

VIEW_SIZE = (1024, 1024)
PLATE_SIZE = (1344, 768)
OBJECT_SIZE = (1024, 1024)
NEW_SEED_BITS = 53         # as h3jobs: a browser can hold these as numbers


def seed_for(key: str) -> int:
    """A ref's stable seed (48 bits): a character's id, else the ref's path."""
    return int(hashlib.sha1(key.encode()).hexdigest()[:12], 16)


def parse_size(target: str, default=(1024, 1024)) -> tuple[int, int]:
    m = re.search(r"(\d+)\s*[x×]\s*(\d+)", target or "")
    return (int(m.group(1)), int(m.group(2))) if m else default


def _one(g: dict, *ctypes: str) -> str:
    ids = [k for k, v in g.items() if v["class_type"] in ctypes]
    if len(ids) != 1:
        raise ValueError(f"the workflow needs exactly one {' / '.join(ctypes)} node "
                         f"(found {len(ids)})")
    return ids[0]


def _splice_lora(g: dict, ks: str, pos: str, name: str, sm: float, sc: float,
                 lid: str) -> None:
    """Insert a LoraLoader between the sampler's model / the prompt's clip and
    their sources, rewiring every text encoder that read the same clip."""
    ki = g[ks]["inputs"]
    model_src, clip_src = ki["model"], g[pos]["inputs"]["clip"]
    g[lid] = {"class_type": "LoraLoader",
              "inputs": {"model": model_src, "clip": clip_src, "lora_name": name,
                         "strength_model": sm, "strength_clip": sc}}
    ki["model"] = [lid, 0]
    for node in g.values():
        if node["class_type"] == "CLIPTextEncode" and node["inputs"].get("clip") == clip_src:
            node["inputs"]["clip"] = [lid, 1]


def patch_workflow(base: dict, prompt: str, negative: str, w: int, h: int, seed: int,
                   steps: int, cfg: float, prefix: str,
                   unet: str = "", lora: str = "", lora_m: float = 1.0,
                   lora_c: float = 1.0) -> dict:
    """Set this job's values on a loaded workflow, leaving its wiring alone.

    Positive and negative prompts are found by following the sampler's own
    links, because the two CLIPTextEncode nodes are otherwise identical.
    """
    g = copy.deepcopy(base)
    ks = _one(g, "KSampler", "KSamplerAdvanced")
    ki = g[ks]["inputs"]
    for key, val in (("seed", seed), ("noise_seed", seed), ("steps", steps),
                     ("cfg", cfg), ("denoise", 1.0)):
        if key in ki:
            ki[key] = val

    def text_node(slot: str) -> str | None:
        link = ki.get(slot)
        return link[0] if isinstance(link, list) else None

    pos, neg = text_node("positive"), text_node("negative")
    if pos and g[pos]["class_type"] == "CLIPTextEncode":
        g[pos]["inputs"]["text"] = prompt
    else:
        raise ValueError("the workflow's sampler has no CLIPTextEncode on `positive`")
    # The negative side is left exactly as the workflow wires it — a krea2 turbo
    # graph zeroes it, a guided model's graph carries its own text, and a NAG or
    # negpip setup is untouched. Only an explicit negative overrides that.
    if neg and negative and cfg > 1.0:
        g[neg] = {"class_type": "CLIPTextEncode",
                  "inputs": {"clip": g[pos]["inputs"]["clip"], "text": negative}}

    lat = _one(g, "EmptyLatentImage", "EmptySD3LatentImage")
    g[lat]["inputs"]["width"], g[lat]["inputs"]["height"] = w, h
    g[_one(g, "SaveImage")]["inputs"]["filename_prefix"] = prefix

    if unet:
        g[_one(g, "UNETLoader")]["inputs"]["unet_name"] = unet
    if lora:
        ids = [k for k, v in g.items() if v["class_type"] in ("LoraLoader",
                                                              "LoraLoaderModelOnly")]
        if len(ids) > 1:
            raise ValueError(f"--lora needs one LoraLoader in the workflow (found {len(ids)})")
        if ids:
            gi = g[ids[0]]["inputs"]
            gi["lora_name"] = lora
            if "strength_model" in gi:
                gi["strength_model"] = lora_m
            if "strength_clip" in gi:
                gi["strength_clip"] = lora_c
        else:
            # the workflow has no LoRA node: splice one in between the loaders
            # and everything that reads them, so --lora works on any graph
            _splice_lora(g, ks, pos, lora, lora_m, lora_c, "kreagen_lora")
    return g


def build_graph(prompt: str, negative: str, w: int, h: int, seed: int,
                steps: int, cfg: float, prefix: str, lora_clip: bool,
                unet: str = "", lora: str = "", lora_m: float = LORA_M,
                lora_c: float = LORA_C) -> dict:
    """The built-in krea2 turbo graph, for when no workflow file is found."""
    lora = lora or LORA
    model_src = ["4", 0] if lora else ["1", 0]
    clip_src = (["4", 1] if lora_clip else ["2", 0]) if lora else ["2", 0]
    g = {
        "1": {"class_type": "UNETLoader",
              "inputs": {"unet_name": unet or UNET, "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader",
              "inputs": {"clip_name": CLIP, "type": CLIPTYPE, "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": VAE}},

        "5": {"class_type": "CLIPTextEncode",
              "inputs": {"clip": clip_src, "text": prompt}},
        "7": {"class_type": "EmptyLatentImage",
              "inputs": {"width": w, "height": h, "batch_size": 1}},
        "8": {"class_type": "KSampler",
              "inputs": {"model": model_src, "positive": ["5", 0], "negative": ["6", 0],
                         "latent_image": ["7", 0], "seed": seed, "steps": steps,
                         "cfg": cfg, "sampler_name": SAMPLER, "scheduler": SCHED,
                         "denoise": 1.0}},
        "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["3", 0]}},
        "10": {"class_type": "SaveImage",
               "inputs": {"images": ["9", 0], "filename_prefix": prefix}},
    }
    if lora:
        g["4"] = {"class_type": "LoraLoader",
                  "inputs": {"model": ["1", 0], "clip": ["2", 0], "lora_name": lora,
                             "strength_model": lora_m, "strength_clip": lora_c}}
    # At cfg 1.0 there is no guidance, so a negative prompt is inert and
    # ConditioningZeroOut is the cheap correct thing. Above 1.0 it bites.
    if cfg > 1.0 and negative:
        g["6"] = {"class_type": "CLIPTextEncode",
                  "inputs": {"clip": clip_src, "text": negative}}
    else:
        g["6"] = {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["5", 0]}}
    return g


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
            doc = J.load_shotlist(ep, ps)
        except (FileNotFoundError, ValueError):
            continue
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
    PREFIX) and the caller fetches the image (kreagen on an older node pack)."""
    loras = job.loras
    first = loras[0] if loras else None
    fname, fstr = (first["name"], float(first.get("strength", 1.0))) if first else ("", 1.0)
    if base is not None:
        g = patch_workflow(base, job.prompt, job.negative, job.width, job.height, job.seed,
                           job.steps, job.cfg, PREFIX, unet=job.model, lora=fname,
                           lora_m=fstr, lora_c=fstr)
    else:
        g = build_graph(job.prompt, job.negative, job.width, job.height, job.seed,
                        job.steps, job.cfg, PREFIX, lora_clip, unet=job.model, lora=fname,
                        lora_m=fstr, lora_c=fstr)
    if loras == []:
        # no LoRA at all: a workflow's own loader is kept but does nothing
        for v in g.values():
            if v["class_type"] in ("LoraLoader", "LoraLoaderModelOnly"):
                for k in ("strength_model", "strength_clip"):
                    if k in v["inputs"]:
                        v["inputs"][k] = 0.0
    if loras and len(loras) > 1:
        ks = _one(g, "KSampler", "KSamplerAdvanced")
        pos = g[ks]["inputs"]["positive"][0]
        for i, lo in enumerate(loras[1:], start=2):
            st = float(lo.get("strength", 1.0))
            _splice_lora(g, ks, pos, lo["name"], st, st, f"h3refs_lora{i}")
    if save_node:
        sid = _one(g, "SaveImage")
        g[sid] = {"class_type": SAVER,
                  "inputs": {"images": g[sid]["inputs"]["images"],
                             "sidecar": os.path.abspath(take.paths.sidecar)},
                  "_meta": {"title": "H3 Save Ref Take"}}
    return g


def resolve_workflow(comfy_url: str | None, explicit: str | None = None):
    """(krea2_refs_t2i graph or None, where) — None means the built-in graph."""
    return J.resolve_workflow(explicit, REFS_WORKFLOW, comfy_url, env="KREA_WORKFLOW",
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
            doc = J.load_shotlist(s.ep, ps)
        except FileNotFoundError:
            doc = None
        if doc is not None:
            for shot in doc.get("shots", []):
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
