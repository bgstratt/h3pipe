#!/usr/bin/env python3
"""
h3edit.py — look at and steer an episode's takes: list them, pick one for the
cut, set per-shot overrides. The command-line face of what the editor does;
`episode_status` is what its routes will serve.

    python h3.py takes    Shows\\ep05 [--proxy] [--only sh020,sh030] [--no-sweep]
    python h3.py pick     Shows\\ep05 sh020 3 [--proxy] [--from proxy]
    python h3.py pick     Shows\\ep05 sh020 latest [--proxy]
    python h3.py override Shows\\ep05 sh020 --show [--proxy]
    python h3.py override Shows\\ep05 sh020 --seed 1234 --steps 10 [--proxy | --both]
    python h3.py override Shows\\ep05 sh020 --lora a.safetensors --lora b.safetensors:0.6
    python h3.py override Shows\\ep05 sh020 --prompt-file sh020.txt
    python h3.py override Shows\\ep05 sh020 --dump-prompt > sh020.txt
    python h3.py override Shows\\ep05 sh020 --clear prompt seed
    python h3.py override Shows\\ep05 sh020 --clear
    python h3.py override Shows\\ep05 sh020 --target ltx2      # retarget (--target none: undo)
    python h3.py override Shows\\ep05 --episode-target ltx2    # the episode's target (built: undo)
    python h3.py keyframe Shows\\ep05 sh020 [--from-prev | --from sh010[:3]] [--first | --last]
                                          [--frame N] [--proxy] [--pick | --no-pick]
    python h3.py targets  [Shows\\ep05] [--json]               # readiness of every target

`takes` shows every take with its status, why it is stale (script / ref /
preset / target), and which take the cut uses. `pick` writes cut.json; `latest` puts a
shot back on its newest usable take. `override` writes overrides.json: the
next render or redo of that shot uses it (see h3render.py). Prompt, model,
LoRAs and steps are per pass (final unless --proxy; --both sets both); seed
and note apply to both passes. They are written to the block of the target
the shot renders on now, so a retargeted shot's settings are its new
target's. `--target` retargets the shot (both passes): its IR is compiled for
that target at queue time, and a prompt override is ignored while it is.
`--target built` puts it back on its build's target: that clears the
retarget, or, while an episode target is set, pins the shot there.
`--episode-target` sets the default target of every shot the script gives
none (overrides.json's episode.target; series.json is never written).
`targets` shows which targets the running ComfyUI can render (readiness) and
what to download for the others.
`keyframe` cuts a frame out of another shot's take and adds it as the shot's
first (or last) keyframe: by default the previous shot's last frame, from the
take that shot's cut entry uses (continuity). It is picked when the shot has no
keyframe yet (h3refs.keyframe_from_take).

Stdlib only.
"""
from __future__ import annotations

import argparse
import copy
import os
import subprocess
import sys

import h3jobs as J
import h3takes as T


# ---------------------------------------------------------------------------
# status: what the editor's shot bin shows
# ---------------------------------------------------------------------------

SCRIPT_SKIP = ("refs_todo", "readme", "notes")


def episode_script(root: str) -> str | None:
    """The episode's script: <folder name>.md, else the only other .md there."""
    base = os.path.basename(os.path.normpath(root))
    md = os.path.join(root, f"{base}.md")
    if os.path.isfile(md):
        return md
    cands = [f for f in os.listdir(root) if f.lower().endswith(".md")
             and not f.lower().startswith(SCRIPT_SKIP)]
    return os.path.join(root, cands[0]) if len(cands) == 1 else None


def episode_series_config(root: str) -> str | None:
    """series.json in the episode folder, else in its parent (a series folder)."""
    for d in (root, os.path.dirname(os.path.normpath(root))):
        p = os.path.join(d, "series.json")
        if os.path.isfile(p):
            return p
    return None


def find_episodes(roots: list[str], depth: int = 2) -> list[dict]:
    """Every folder under `roots` (to `depth` levels) with a series config and a script."""
    out, seen = [], set()

    def visit(d: str, level: int):
        if level > depth or not os.path.isdir(d):
            return
        try:
            names = sorted(os.listdir(d))
        except OSError:
            return
        if "series.json" in names:
            script = episode_script(d)
            if script and os.path.normcase(d) not in seen:
                seen.add(os.path.normcase(d))
                out.append(episode_summary(d, script))
        for n in names:
            if n.startswith((".", "_")) or n in ("refs", "renders", "renders_proxy",
                                                 "shotlist", "views", "audio"):
                continue
            p = os.path.join(d, n)
            if os.path.isdir(p):
                visit(p, level + 1)

    for r in roots:
        visit(os.path.abspath(r), 0)
    return out


BROWSE_LIMIT = 1000
BROWSE_EXTS = {"image": (".png", ".jpg", ".jpeg", ".webp"),
               "audio": (".wav", ".mp3", ".flac", ".ogg", ".m4a")}


def browse(path: str | None, files: str | None = None) -> dict:
    """Folders for a folder picker: the subfolders of `path`, each flagged when
    it is an episode (a series config and a script) or holds a series config. No `path`: the
    starting points (drives on Windows, and the home folder)."""
    if not path:
        places = []
        if os.name == "nt":
            import string
            places += [f"{d}:\\" for d in string.ascii_uppercase if os.path.isdir(f"{d}:\\")]
        else:
            places.append("/")
        home = os.path.expanduser("~")
        return {"path": "", "parent": None,
                "dirs": [{"name": p, "path": p, "episode": False, "series_config": False}
                         for p in [home] + places]}
    path = os.path.abspath(path)
    if not os.path.isdir(path):
        raise FileNotFoundError(f"{path} is not a folder")
    if files is not None and files not in BROWSE_EXTS:
        raise ValueError(f"files must be one of {', '.join(BROWSE_EXTS)}")
    dirs, found = [], []
    try:
        names = sorted(os.listdir(path), key=str.lower)
    except PermissionError as e:
        raise PermissionError(f"can't read {path}: {e}") from None
    for n in names:
        if n.startswith((".", "$")) or n in ("System Volume Information", "__pycache__",
                                              "node_modules"):
            continue
        p = os.path.join(path, n)
        if not os.path.isdir(p):
            if files and n.lower().endswith(BROWSE_EXTS[files]) and len(found) < BROWSE_LIMIT:
                try:
                    found.append({"name": n, "path": p, "size": os.path.getsize(p)})
                except OSError:
                    pass
            continue
        has_series_cfg = os.path.isfile(os.path.join(p, "series.json"))
        episode = False
        if has_series_cfg:
            try:
                episode = episode_script(p) is not None
            except OSError:
                pass
        dirs.append({"name": n, "path": p, "episode": episode, "series_config": has_series_cfg})
        if len(dirs) >= BROWSE_LIMIT:
            break
    parent = os.path.dirname(path)
    return {"path": path, "parent": parent if parent != path else "",
            "episode": os.path.isfile(os.path.join(path, "series.json"))
            and episode_script(path) is not None,
            "dirs": dirs, "truncated": len(dirs) >= BROWSE_LIMIT,
            **({"files": found} if files else {})}


def episode_summary(root: str, script: str | None = None) -> dict:
    title = series = ""
    series_cfg = episode_series_config(root)
    if series_cfg:
        b = T.read_json(series_cfg) or {}
        series = (b.get("series") or {}).get("title", "")
    built = {p: os.path.isfile(os.path.join(root, J.shotlist_rel(p))) for p in T.PASSES}
    shots = 0
    for p in T.PASSES:
        if built[p]:
            docs = J.load_shotlists(root, p)
            title = docs[0].get("title", "")
            shots = sum(len(d.get("shots", [])) for d in docs)
            break
    return {"ep": os.path.abspath(root), "name": os.path.basename(os.path.normpath(root)),
            "series": series, "title": title, "built": built, "shots": shots,
            "script": os.path.basename(script or episode_script(root) or "")}


def episode_fps(root: str) -> float:
    series_cfg = episode_series_config(root)
    b = (T.read_json(series_cfg) or {}) if series_cfg else {}
    return float((b.get("series") or {}).get("fps", 24))


def rel(root: str, path: str) -> str | None:
    """`path` relative to the episode, with forward slashes (URL-ready), or
    None if there is no such file."""
    if not os.path.isfile(path):
        return None
    return os.path.relpath(path, root).replace(os.sep, "/")


def prompt_text(prompt) -> str:
    """A shotlist prompt (list of sections, or one string) as the loader joins it."""
    return "\n\n".join(prompt) if isinstance(prompt, list) else (prompt or "")


def take_stale(root: str, pass_: str, doc: dict, shot: dict, sidecar: dict | None,
               target: str, cache: dict) -> list[str]:
    """Why a take is stale (h3jobs.stale_reasons), judged against the entry its
    own target would render now: the built entry, or the shot retargeted to
    that target. `target` is what the shot's next render uses; a take made on
    another target is also stale `target`."""
    if not sidecar:
        return ["unknown"]
    built_target = J.shotlist_target(doc).id
    tt = sidecar.get("target") or built_target
    cur = J.current_entry(root, pass_, doc, shot, tt, cache)
    out = J.stale_reasons(root, cur[0], cur[1], sidecar) if cur else []
    if tt != target:
        out.append("target")
    return out


def cut_take(root: str, e: T.CutEntry,
             takes: list[T.Take] | None = None) -> tuple[T.Take | None, int | None, bool]:
    """The take a cut entry uses: (take or None, its number or None, usable).
    A picked take (from the entry's pass: the other one for a placeholder), else
    the latest usable take. `takes` are the entry's shot's takes in the cut's
    own pass, when already listed."""
    if e.placeholder or takes is None:
        src = T.list_takes(root, e.pass_, e.shot)
    else:
        src = takes
    if e.take is not None:
        chosen = next((t for t in src if t.take == e.take), None)
        return chosen, e.take, bool(chosen and chosen.usable)
    chosen = T.latest_usable(src)
    return chosen, (chosen.take if chosen else None), chosen is not None


def cut_entries(root: str, pass_: str) -> list[T.CutEntry]:
    """One pass's cut, reconciled with the script (every target's shots)."""
    order = [d["shots"][i]["id"] for d, i in J.episode_shots(root, pass_)]
    return T.resolve_cut(T.load_cut(root), pass_, order)


def cut_neighbour(root: str, pass_: str, shot_id: str, step: int) -> T.CutEntry | None:
    """The shot `step` places from `shot_id` in the pass's cut (-1: the one
    before it, 1: the one after), skipping orphans as assemble does. None at
    either end; KeyError if the shot isn't in the cut."""
    entries = [e for e in cut_entries(root, pass_) if not e.orphan]
    idx = next((i for i, e in enumerate(entries) if e.shot == shot_id), None)
    if idx is None:
        raise KeyError(f"{shot_id} is not in the {pass_} cut")
    j = idx + step
    return entries[j] if 0 <= j < len(entries) else None


def take_frames(t: T.Take | None) -> int | None:
    """A take's real frame count: what the saver wrote in its sidecar
    (`frames`), else None (not rendered, or a saver from before it)."""
    n = ((t.sidecar or {}) if t else {}).get("frames")
    return int(n) if isinstance(n, (int, float)) and not isinstance(n, bool) and n > 0 else None


def take_fps(t: T.Take | None) -> float | None:
    """The frame rate a take was rendered at (its sidecar's `fps`: a Wan 14B
    take is 16 fps in a 24 fps episode), else None (a take from before
    sidecars recorded it: the episode's)."""
    n = ((t.sidecar or {}) if t else {}).get("fps")
    return float(n) if isinstance(n, (int, float)) and not isinstance(n, bool) and n > 0 else None


def shot_fps(doc: dict, shot: dict | None, fallback: float) -> float:
    """The frame rate a shot's build renders at (its target's)."""
    v = (shot or {}).get("fps", doc.get("defaults", {}).get("fps"))
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 \
        else fallback


def episode_status(root: str, pass_: str, folder: str | None = None) -> dict:
    """Every shot in cut order with its takes, the take the cut uses, and its
    override. Plain data, ready to serve as JSON. Shots come from every
    target's shotlist; each says the target its next render uses (`target`:
    overrides.json may retarget it) and the one the build compiled it for
    (`built_target`)."""
    docs = J.load_shotlists(root, pass_)
    doc0 = docs[0]
    fps = episode_fps(root)
    entries = J.episode_shots(root, pass_, docs)
    shots = {d["shots"][i]["id"]: (d, d["shots"][i]) for d, i in entries}
    ov = T.load_overrides(root)
    cut = T.load_cut(root)
    cache: dict = {}
    out = []
    for e in T.resolve_cut(cut, pass_, list(shots)):
        doc, shot = shots.get(e.shot, (doc0, None))
        built_target = J.shotlist_target(doc).id
        target, target_source = J.target_choice(ov, e.shot, built_target, root=root)
        takes = T.list_takes(root, pass_, e.shot, folder) if shot else []
        chosen, chosen_take, chosen_ok = cut_take(root, e, takes)
        o = T.shot_override(ov, e.shot, pass_, target)
        # what the next render reads: the retargeted entry when retargeted
        cur = J.current_entry(root, pass_, doc, shot, target, cache) if shot else None
        rdoc, rshot = cur if cur else (doc, shot)
        sfps = shot_fps(doc, shot, fps)
        out.append({
            "shot": e.shot,
            "orphan": e.orphan,
            "target": target,
            # request | override | script | episode | series | default
            "target_source": target_source,
            "built_target": built_target,
            "profile": shot.get("profile") if shot else None,
            "sequence": shot.get("sequence") if shot else None,
            "length": shot.get("length") if shot else None,
            # `dur: model`: the length is the build's estimate (the UI marks it ≈)
            **({"length_estimated": True} if shot and shot.get("length_estimated") else {}),
            # at the shot's own frame rate (its target's; the episode's for
            # every target that renders at it)
            "seconds": round(shot["length"] / sfps, 3) if shot else None,
            "size": shot.get("size") if shot else None,
            "subjects": shot.get("subjects", []) if shot else [],
            "audio_policy": shot.get("audio_policy") if shot else None,
            "missing_refs": [{k: r.get(k) for k in ("slot", "kind", "path", "subject",
                                                    "anyway", "why")
                              if r.get(k) is not None}
                             for r in J.missing_refs(root, rdoc, rshot)] if shot else [],
            **({"retarget_error": f"can't compile {e.shot} for {target} "
                                  f"(rebuild the episode)"}
               if shot and cur is None else {}),
            "cut": {"take": chosen_take, "picked": e.take is not None, "pass": e.pass_,
                    "placeholder": e.placeholder, "usable": chosen_ok,
                    "trim_in": e.trim_in, "trim_out": e.trim_out, "locked": e.locked,
                    "note": e.note, "in_cut_file": e.in_cut_file,
                    # the cut take's real length (its sidecar's saved frame
                    # count; a `dur: model` take's is the model's), or None
                    "frames": take_frames(chosen) if chosen_ok else None,
                    # the frame rate of those frames (the take's, else the
                    # shot's build): Wan 14B takes are 16 fps in a 24 fps cut
                    "fps": (take_fps(chosen) if chosen_ok else None) or sfps},
            "override": {"fields": sorted([k for k in o if k != "base_hash"]
                                          + (["target"] if T.shot_target(ov, e.shot) else [])),
                         "stale": bool(o.get("base_hash"))
                         and bool(rshot) and o["base_hash"] != J.story_hash(rshot)},
            "takes": [{
                "take": t.take, "status": t.status, "has_video": t.has_video,
                "seed": (t.sidecar or {}).get("seed"),
                "frames": take_frames(t),
                "fps": take_fps(t),
                "seed_source": (t.sidecar or {}).get("seed_source"),
                "target": (t.sidecar or {}).get("target"),
                "note": (t.sidecar or {}).get("note", ""),
                "overrides": (t.sidecar or {}).get("overrides", []),
                "stale": take_stale(root, pass_, doc, shot, t.sidecar, target, cache)
                if shot else [],
                "thumb": rel(root, t.paths.thumb),
                "strip": rel(root, t.paths.strip),
                "mp4": rel(root, t.paths.mp4),
                "queued": (t.sidecar or {}).get("queued"),
                "comfy_prompt_id": (t.sidecar or {}).get("comfy_prompt_id"),
                "finished": (t.sidecar or {}).get("finished"),
                "save_notes": (t.sidecar or {}).get("save_notes", ""),
            } for t in takes],
        })
    d = doc0.get("defaults", {})
    return {"episode": doc0.get("episode", os.path.basename(root)),
            "title": doc0.get("title", ""), "pass": pass_,
            **episode_target_info(root, ov, J.shotlist_target(doc0).id),
            "fps": fps, "width": d.get("width"),
            "height": d.get("height"), "folder": folder or T.pass_subfolder(pass_),
            "shots": out}


def episode_target_info(root: str, ov: dict | None = None, built: str | None = None) -> dict:
    """The episode's default video target now in force: {"target",
    "target_source": "editor" (overrides.json's episode target) | "series"
    (the series config's series.target) | "default", "series_target" (the
    series config's series.target, or None)}. `built` is the series
    target's shotlist's, when the series config names none."""
    ov = T.load_overrides(root) if ov is None else ov
    series = J.series_target(root)
    ep = T.episode_target(ov)
    if ep:
        return {"target": ep, "target_source": "editor", "series_target": series}
    if series:
        return {"target": series, "target_source": "series", "series_target": series}
    return {"target": J.TG.DEFAULT_VIDEO_TARGET if built is None else built,
            "target_source": "default", "series_target": None}


def set_episode_target(root: str, target: str | None) -> dict:
    """Set (or with None clear) the episode's video target in overrides.json;
    returns episode_target_info. ValueError (TargetError) for an id that
    isn't a video target. series.json is never written."""
    if target:
        J.check_video_target(target)
    ov = T.load_overrides(root)
    name = ""
    for ps in T.PASSES:
        if os.path.isfile(os.path.join(root, J.shotlist_rel(ps))):
            name = J.load_shotlist(root, ps).get("episode", "")
            break
    T.set_episode_target(ov, target or None, name or os.path.basename(os.path.normpath(root)))
    T.save_overrides(root, ov)
    return episode_target_info(root, ov)


def keeps_shot_target(ov: dict, want: str | None, built_target: str) -> str | None:
    """What a shot override's `target` should store for a request of `want`:
    None clears it. Without an episode target, the built target clears it
    too (the shot renders on it anyway). With one, the built target is kept
    as an explicit pin: the shot stays on its build's target while the rest
    of the episode follows the episode target."""
    if not want:
        return None
    if want == built_target and not T.episode_target(ov):
        return None
    return want


def _ep_path(root: str, p: str) -> tuple[str, str]:
    """(a ref path as the episode serves it: relative, forward slashes, maybe
    ../ beside a parent-folder series config; the file on disk)."""
    full = p if os.path.isabs(p) else os.path.join(root, p)
    try:
        r = os.path.relpath(full, root).replace(os.sep, "/")
    except ValueError:                                  # another drive
        r = full.replace(os.sep, "/")
    return r, full


def refs_used(root: str, job: J.Job) -> list[dict]:
    """The refs a shot's next render reads (its current target's ref slots),
    for the inspector: [{"id", "kind", "role", "path", "exists", "need",
    "thumb"}]. `role` is "subject", "plate", "first", "last", "voice",
    "recording" (a dub's dialogue track) or "reference_sheet" (the sheet or
    VACE reference a target composes at queue time: `path` is the latest
    take's kept copy, or null); `need` "required" or "optional"; `thumb` the
    file to show, relative to the episode (null when it isn't on disk)."""
    try:
        slots = J.ref_slots(job.doc, job.shot)
    except Exception:
        return []
    cfg = J.series_config(root) or {}
    plates = {}
    home = next((d for d in (root, os.path.dirname(os.path.normpath(root)))
                 if os.path.isfile(os.path.join(d, "series.json"))), root)
    for lid, loc in (cfg.get("locations") or {}).items():
        if isinstance(loc, dict) and loc.get("plate"):
            plates[os.path.normcase(os.path.normpath(os.path.join(home, loc["plate"])))] = lid
    out = []
    for r in slots:
        p = r.get("path") or ""
        rel_, full = _ep_path(root, p) if p else (None, "")
        exists = bool(p) and os.path.isfile(full)
        role, rid = None, None
        if r.get("role") in ("first", "last"):
            role, rid = r["role"], f"shot:{job.id}:{r['role']}"
        elif r.get("kind") == "audio":
            role = "voice" if r.get("subject") else "recording"
            rid = f"voice:{r['subject']}" if r.get("subject") else None
        elif r.get("subject"):
            role, rid = "subject", f"subject:{r['subject']}"
        elif r.get("location"):
            role, rid = "plate", f"location:{r['location']}"
        else:
            lid = plates.get(os.path.normcase(os.path.normpath(full))) if p else None
            role, rid = "plate", (f"location:{lid}" if lid else None)
        need = "optional" if r.get("optional") else "required"
        out.append({"id": rid, "kind": r.get("kind", "image"), "role": role, "path": rel_,
                    "exists": exists, "need": need, "thumb": rel_ if exists else None,
                    "slot": r.get("slot")})
    t = J.job_target(job)
    rec = t.recipe
    if rec.get("reference_sheet") or rec.get("reference_image"):
        suffix = ((rec.get("reference_sheet") or {}).get("take_suffix")
                  or (rec.get("reference_image") or {}).get("take_suffix") or "")
        latest = None
        for tk in reversed(T.list_takes(root, job.pass_, job.id, job.folder)):
            f = os.path.join(tk.paths.dir, tk.paths.stem + suffix) if suffix else ""
            if f and os.path.isfile(f):
                latest = f
                break
        rel_ = _ep_path(root, latest)[0] if latest else None
        out.append({"id": None, "kind": "image", "role": "reference_sheet", "path": rel_,
                    "exists": bool(latest), "need": "required", "thumb": rel_,
                    "slot": "reference sheet" if rec.get("reference_sheet") else "reference image"})
    return out


def reference_image(root: str, sidecar: dict | None) -> str | None:
    """The reference sheet (ltx2_ingredients) or reference image (wan22_vace)
    a take rendered with, kept beside it: its path relative to the episode,
    or None."""
    for r in (sidecar or {}).get("refs") or []:
        if r.get("role") in ("sheet", "reference") and r.get("path"):
            rel_, full = _ep_path(root, r["path"])
            return rel_ if os.path.isfile(full) else None
    return None


def shot_detail(root: str, pass_: str, shot_id: str, folder: str | None = None) -> dict:
    """Everything the inspector shows for one shot in one pass: the built entry,
    the prompt it builds to, the override and the prompt a render would use now,
    and each take's full sidecar. `target` is what the next render uses,
    `built_target` what the build compiled it for; `effective` is that
    render's settings (a retargeted shot's are its new target's)."""
    try:
        doc, idx = J.find_shot(root, pass_, shot_id)
    except KeyError:
        raise KeyError(f"{shot_id} is not in {J.shotlist_rel(pass_)}") from None
    shot = doc["shots"][idx]
    ov = T.load_overrides(root)
    job = J.plan_job(root, pass_, doc, idx, J.RenderRequest(shot_id), ov, folder)
    eff = T.shot_override(ov, shot_id, pass_, job.target)
    cache: dict = {}
    takes = []
    for t in T.list_takes(root, pass_, shot_id, folder):
        takes.append({"take": t.take, "status": t.status, "has_video": t.has_video,
                      "stale": take_stale(root, pass_, doc, shot, t.sidecar, job.target, cache),
                      "sidecar": t.sidecar,
                      # the reference sheet / VACE reference it rendered with, if kept
                      "reference_image": reference_image(root, t.sidecar),
                      "files": {k: rel(root, getattr(t.paths, k))
                                for k in ("mp4", "thumb", "strip", "shotlist", "h3_wav")
                                if os.path.isfile(getattr(t.paths, k))}})
    view = {k: v for k, v in eff.items() if k != "base_hash"}
    if T.shot_target(ov, shot_id):
        view["target"] = T.shot_target(ov, shot_id)
    return {
        "shot": shot_id, "pass": pass_, "index": idx, "target": job.target,
        "target_source": job.target_source,
        "built_target": job.built_target,
        "profile": shot.get("profile"), "built": shot,
        "built_prompt": prompt_text(shot.get("prompt")),
        "override": view,
        "override_stale": bool(eff.get("base_hash")) and eff["base_hash"] != J.story_hash(job.shot),
        "effective": {"prompt": prompt_text(job.prompt), "seed": job.seed,
                      "seed_source": job.seed_source, "model": job.model,
                      "loras": job.loras, "steps": job.steps, "target": job.target,
                      "width": job.width, "height": job.height, "length": job.frames,
                      # request | override | negative.txt | series | preset | none
                      "negative": (None if job.negative_source == "none"
                                   else J.job_values(job).get("negative") or ""),
                      "negative_source": job.negative_source,
                      **({"model_low": J.job_values(job).get("model_low")}
                         if J.job_target(job).binding.specs("model_low") else {}),
                      **({"error": job.error} if job.error else {}),
                      **({"notes": list(job.notes)} if job.notes else {})},
        "refs_used": refs_used(root, job),
        "takes": takes,
    }


def sweep(root: str, pass_: str, comfy_url: str, folder: str | None = None) -> int:
    as_of = T.now()
    try:
        alive = J.Comfy(comfy_url).alive()
    except Exception:
        return -1
    return sum(len(T.sweep_queued(T.list_takes(root, pass_, d["shots"][i]["id"], folder),
                                  alive, as_of=as_of))
               for d, i in J.episode_shots(root, pass_))
# ---------------------------------------------------------------------------
# editor operations: shared by the commands below and the ComfyUI routes
# ---------------------------------------------------------------------------

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL_ENV = {"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}


class NotUsable(Exception):
    """A pick of a take the cut can't use (queued, failed, or no mp4)."""

    def __init__(self, take: T.Take):
        super().__init__(f"{take.shot} t{take.take:02d} is {take.status}"
                         + ("" if take.has_video else " with no mp4"))
        self.take = take


class NotQueued(Exception):
    """Cancel of a take that isn't queued any more."""


def pick_take(root: str, pass_: str, shot_id: str, take: int | None,
              from_pass: str | None = None, force: bool = False) -> dict:
    """Point `pass_`'s cut at take `take` of `shot_id` (None: latest usable),
    from `from_pass` (default the same pass; the other one makes a placeholder).
    Writes cut.json and returns it. Raises KeyError for a shot in neither the
    script nor the cut, LookupError for a take that doesn't exist, NotUsable
    for a take that can't be cut in (unless `force`)."""
    src = from_pass or pass_
    doc = J.load_shotlist(root, pass_)
    order = [d["shots"][i]["id"] for d, i in J.episode_shots(root, pass_)]
    if take is not None:
        t = T.get_take(root, src, shot_id, take)
        if t is None:
            raise LookupError(f"{shot_id} has no take {take} in {src}")
        if not t.usable and not force:
            raise NotUsable(t)
    cut = T.pick(T.load_cut(root), pass_, order, shot_id, take, from_pass=src)
    cut.setdefault("episode", doc.get("episode", ""))
    T.save_cut(root, cut)
    return cut


def replace_cut(root: str, pass_: str, entries: list[dict]) -> dict:
    """Replace one pass's list in cut.json (reorder, trims, locks). Entries are
    cut.json entries; shots not in the script are allowed (they become orphans)."""
    T.pass_subfolder(pass_)                               # validates the pass
    cut = T.load_cut(root)
    cut[pass_] = [dict(e) for e in entries]
    if "episode" not in cut:
        try:
            cut["episode"] = J.load_shotlist(root, pass_).get("episode", "")
        except FileNotFoundError:
            cut["episode"] = os.path.basename(os.path.normpath(root))
    T.save_cut(root, cut)
    return cut


def pass_builds(root: str, shot_id: str,
                have: dict[str, dict] | None = None) -> dict[str, dict]:
    """{pass: the shot as that pass builds it now}, for each pass that has it.
    `have` maps passes to shotlists already loaded."""
    built = {}
    for ps in T.PASSES:
        try:
            d = (have or {}).get(ps)
            s = next((s for s in d["shots"] if s["id"] == shot_id), None) if d else None
            if s is None:
                fd, fi = J.find_shot(root, ps, shot_id)
                s = fd["shots"][fi]
        except (FileNotFoundError, KeyError):
            continue
        built[ps] = s
    return built


def pass_entries(root: str, shot_id: str, target: str | None = None) -> dict[str, dict]:
    """{pass: the entry a render of the shot on `target` uses now} (the built
    entry, or the shot retargeted), for each pass that builds it. Override
    base_hashes are stamped against these."""
    out = {}
    for ps in T.PASSES:
        try:
            d, i = J.find_shot(root, ps, shot_id)
        except (FileNotFoundError, KeyError):
            continue
        cur = J.current_entry(root, ps, d, d["shots"][i], target)
        if cur is not None:
            out[ps] = cur[1]
    return out


def shot_built_target(root: str, shot_id: str) -> str | None:
    """The target the build compiled a shot for (None: in no build)."""
    for ps in T.PASSES:
        try:
            return J.shotlist_target(J.find_shot(root, ps, shot_id)[0]).id
        except (FileNotFoundError, KeyError):
            continue
    return None


def set_shot_override(ov: dict, shot_id: str, built: dict[str, dict], passes,
                      shot_fields: dict | None = None,
                      pass_fields: dict | None = None,
                      target: str = T.DEFAULT_TARGET) -> None:
    """Change one shot's override in `ov` (not saved). `shot_fields` (seed,
    note) are shared; `pass_fields` (prompt, model, loras, steps) go to each
    pass in `passes`, stamped with that pass's `base_hash` (the story hash of
    `built[pass]`) whenever a value is set. None clears a field."""
    if shot_fields:
        T.set_override(ov, shot_id, target=target, **shot_fields)
    if pass_fields:
        stamp = any(v is not None for v in pass_fields.values())
        for ps in passes:
            # written against the shot as this pass builds it now
            extra = {"base_hash": J.story_hash(built[ps])} if stamp else {}
            T.set_override(ov, shot_id, ps, target, **extra, **pass_fields)


def clear_shot_override(ov: dict, shot_id: str, pass_: str | None = None,
                        target: str = T.DEFAULT_TARGET) -> None:
    """Drop one pass's fields (prompt, model, loras, steps), or with no pass
    the shot's whole override: both passes, seed and note."""
    if pass_ is None:
        T.set_override(ov, shot_id, target=target, **{f: None for f in T.SHOT_FIELDS})
    for ps in (T.PASSES if pass_ is None else [pass_]):
        T.set_override(ov, shot_id, ps, target, **{f: None for f in T.PASS_FIELDS})


def override_view(ov: dict, shot_id: str, built: dict[str, dict],
                  target: str = T.DEFAULT_TARGET) -> dict:
    """{pass: effective override without base_hash, plus `stale`}, both passes.
    A retargeted shot's `target` (shared by both passes) is in each pass's
    view. `built` is what each pass renders now (pass_entries)."""
    out = {}
    for ps in T.PASSES:
        eff = T.shot_override(ov, shot_id, ps, target)
        view = {k: v for k, v in eff.items() if k != "base_hash"}
        if T.shot_target(ov, shot_id):
            view["target"] = T.shot_target(ov, shot_id)
        view["stale"] = (bool(eff.get("base_hash")) and ps in built
                         and eff["base_hash"] != J.story_hash(built[ps]))
        out[ps] = view
    return out


def sweep_takes(root: str, pass_: str, comfy, folder: str | None = None) -> list[T.Take]:
    """Close every queued take whose ComfyUI job is gone; return the takes changed.

    `comfy` is an h3jobs.Comfy (anything with alive() and history()). The
    snapshot time is taken before the queue is read, as sweep_queued needs. A
    take whose job has left the queue is looked up in /history: an execution
    error marks it failed with the exception message; a job that finished is
    closed from what is on disk (finish_job, for a saver that didn't); anything
    else goes to sweep_queued. A take whose job is still pending or running is
    left alone. Raises if ComfyUI can't be reached.
    """
    as_of = T.now()
    alive = comfy.alive()
    changed = []
    for d, i in J.episode_shots(root, pass_):
        s = d["shots"][i]
        rest = []
        for t in T.list_takes(root, pass_, s["id"], folder):
            sc = t.sidecar or {}
            pid = sc.get("comfy_prompt_id")
            if sc.get("status") != "queued" or not pid or pid in alive:
                rest.append(t)
                continue
            try:
                entry = comfy.history(pid)
            except Exception:
                entry = None
            err = J.execution_error(entry)
            if err is not None:
                J.mark_failed(t, err)
                changed.append(t)
            elif J.execution_done(entry):
                J.finish_job(t)
                changed.append(t)
            else:
                rest.append(t)
        changed += T.sweep_queued(rest, alive, as_of=as_of)
    return changed


def cancel_take(root: str, pass_: str, shot_id: str, take: int, comfy,
                folder: str | None = None) -> T.Take:
    """Cancel a queued take: delete its job from ComfyUI's queue if pending,
    interrupt it if running, and mark the take failed ("cancelled"). Raises
    LookupError for no such take, NotQueued if it isn't queued."""
    t = T.get_take(root, pass_, shot_id, take, folder)
    if t is None:
        raise LookupError(f"{shot_id} has no take {take} in {pass_}")
    if t.status != "queued":
        raise NotQueued(f"{shot_id} t{take:02d} is {t.status}, not queued")
    pid = (t.sidecar or {}).get("comfy_prompt_id")
    if pid:
        running, pending = comfy.queue_ids()
        if pid in pending:
            comfy.delete_queued([pid])
        elif pid in running:
            comfy.interrupt(pid)
    J.mark_failed(t, "cancelled")
    return t


def queue_shots(root: str, pass_: str, shot_ids: list[str] | None,
                template: J.RenderRequest, comfy, base,
                folder: str | None = None, model_resolve=J.DEFAULT,
                model_cache=J.DEFAULT, model_list=None) -> dict:
    """Plan and queue a take for each shot (every shot when `shot_ids` is
    None), as h3render does, without waiting for any of them.

    `base` is the workflow: one API graph for every shot (a single-target
    episode), or a function (target id -> API graph) for episodes whose shots
    render on different targets. A job's input images (keyframes) are
    uploaded to ComfyUI first (h3jobs.stage_inputs). Its model files are
    resolved to installed ones first (h3jobs.resolve_models: ComfyUI's
    lists, from `model_list(folder, class_type, field)` or `comfy`'s
    /object_info): a missing required file skips the shot, with
    `missing_files` saying what to download; a missing accelerator renders
    the base preset. Then they are checked (h3jobs.check_models, with
    `model_resolve` and `model_cache`): a file of another family skips the
    shot, with `model_mismatch` listing the checks, unless the template
    allows it.

    Returns {"queued": [{shot, take, prompt_id, seed, seed_source, target}],
    "skipped": [{shot, take, reason}], "errors": [{shot, error, take?}]}. A
    shot that fails to queue has its take marked failed; the others still queue.
    """
    docs = J.load_shotlists(root, pass_)
    ov = T.load_overrides(root)
    index = {d["shots"][i]["id"]: (d, i) for d, i in J.episode_shots(root, pass_, docs)}
    out = {"queued": [], "skipped": [], "errors": []}
    listing = J.model_lister(comfy, model_list)
    for sid in (shot_ids if shot_ids is not None else list(index)):
        if sid not in index:
            out["errors"].append({"shot": sid, "error": f"{sid} is not in "
                                  + J.shotlist_rel(pass_).replace(os.sep, "/")})
            continue
        req = copy.copy(template)
        req.shot_id = sid
        doc, i = index[sid]
        job = J.plan_job(root, pass_, doc, i, req, ov, folder)
        if job.action == "busy":
            out["skipped"].append({"shot": sid, "take": job.take,
                                   "reason": f"t{job.take:02d} is still queued"})
            continue
        if job.action == "skip":
            out["skipped"].append({"shot": sid, "take": job.take,
                                   "reason": "has a usable take (pass redo: true)"})
            continue
        if job.action == "blocked":
            out["skipped"].append({"shot": sid, "reason": job.blocked_reason(),
                                   "missing_refs": job.missing})
            continue
        if job.action == "error":
            out["errors"].append({"shot": sid, "error": job.error})
            continue
        J.resolve_models(job, listing, model_resolve, model_cache)
        if job.action == "missing_files":
            out["skipped"].append({"shot": sid, "target": job.target,
                                   "reason": "model files not installed: "
                                             + job.missing_files_note(),
                                   "missing_files": [
                                       {k: m.get(k) for k in ("param", "tier", "want", "family",
                                                              "folder", "url", "source")}
                                       for m in job.missing_files]})
            continue
        J.check_models(job, model_resolve, model_cache)
        if job.action == "mismatch":
            out["skipped"].append({"shot": sid, "reason": "model mismatch: " + job.mismatch_note()
                                   + " (pass allow_model_mismatch: true to render anyway)",
                                   "model_mismatch": [c for c in job.model_checks if c["block"]]})
            continue
        try:
            graph = base(job.target) if callable(base) else base
            J.stage_inputs(job, comfy if hasattr(comfy, "upload_input") else None)
            take = J.start_job(job)
        except Exception as e:
            out["errors"].append({"shot": sid, "error": str(e)[:800]})
            continue
        try:
            pid = comfy.queue(J.graph_for(graph, job, take))
            J.mark_queued(take, pid)
        except Exception as e:
            J.mark_failed(take, str(e)[:800])
            out["errors"].append({"shot": sid, "take": take.take, "error": str(e)[:800]})
            continue
        out["queued"].append({"shot": sid, "take": take.take, "prompt_id": pid,
                              "seed": job.seed, "seed_source": job.seed_source,
                              "target": job.target})
    return out


# ---------------------------------------------------------------------------
# readiness: can this ComfyUI render each target, and what is missing?
# ---------------------------------------------------------------------------

READY_STATUSES = ("ready", "degraded", "not_ready", "unknown")
_NODES: dict = {}


def target_nodes(t) -> dict[str, dict]:
    """{node class: {"tier", "feature"}} a target's renders need: its
    workflow's classes as a job's graph keeps them (the saver in place, then
    pruned to what the saver needs), its loader and saver, and target.json's
    `nodes`. From the repo's copy of the workflow; cached per target."""
    if t.id in _NODES:
        return _NODES[t.id]
    b = t.binding
    need: dict[str, dict] = {}
    try:
        g = J.load_graph(b.workflow) if b.workflow and os.path.isfile(b.workflow) else {}
        if g and b.saver.get("replace"):
            J.prepare_saver(g, b)
        # a job patches a value into every widget the binding names, cutting
        # whatever fed it (the LTX prompt enhancer): do the same before pruning
        for name in b.params:
            for spec in b.specs(name):
                if not (spec.get("class_type") and spec.get("field")):
                    continue
                try:
                    ids = J.select_nodes(g, spec)
                except ValueError:
                    continue
                for nid in ids:
                    if isinstance(g[nid]["inputs"].get(spec["field"]), list):
                        g[nid]["inputs"][spec["field"]] = ""
        if g and b.prune and b.saver_class:
            J.prune(g, J.node_of(g, b.saver_class))
        classes = {v["class_type"] for v in g.values()}
    except Exception:
        classes = set()
    if b.saver.get("replaces"):                          # krea2: the saver takes SaveImage's place
        classes.discard(b.saver["replaces"])
    for c in sorted(classes | {x for x in (b.loader_class, b.saver_class) if x}):
        need[c] = {"tier": "required", "feature": ""}
    need.update(t.nodes)
    _NODES[t.id] = need
    return need


def readiness(targets, object_info: dict | None, resolve=None, cache=None,
              extra: dict | None = None, series_cfg: dict | None = None,
              error: str = "") -> dict[str, dict]:
    """{target id: readiness} for each target (docs/API.md "Readiness"):

      {"status": "ready" | "degraded" | "not_ready" | "unknown",
       "missing": [{"param", "tier", "want", "family", "label", "folder", "url",
                    "source", "feature"?, "passes"}],
       "resolved": {param: {"want", "using", "how", "tier"}}  (the final pass),
       "by_pass": {pass: resolved},
       "features_off": [...], "nodes_missing": [...]}

    `object_info` is ComfyUI's /object_info (None: it didn't answer, so
    every target is "unknown", with `error`). Each pass's files (the
    target's presets, the series config's pass blocks over them when
    `series_cfg` is given) resolve against the loaders' choices
    (targets.resolve_models); a missing accelerator is judged through the
    pass's `base`. not_ready: a required file or node is missing;
    degraded: only accelerators or optional files (or optional nodes)."""
    out = {}
    for t in targets:
        if object_info is None:
            out[t.id] = {"status": "unknown", "missing": [], "resolved": {}, "by_pass": {},
                         "features_off": [], "nodes_missing": [],
                         "error": error or "ComfyUI didn't answer"}
            continue

        def listing(spec, info=object_info):
            ct, fld = spec.get("class_type"), spec.get("field")
            return J.choices_in(info, ct, fld, strict=True) if ct and fld else None

        missing: dict[tuple, dict] = {}
        blocked = False
        features_off: list[str] = []
        by_pass = {}
        for pass_ in t.presets:
            try:
                wanted = J.TG.wanted_files(t, pass_, series_cfg)
            except Exception:                           # a series config the preset can't read
                wanted = J.TG.wanted_files(t, pass_, None)
            r = J.TG.resolve_models(t, pass_, wanted, listing, resolve, extra, cache,
                                    J.TG.lora_slot(t, pass_, series_cfg))
            by_pass[pass_] = r["resolved"]
            blocked = blocked or bool(r["blocked"])
            for m in r["missing"]:
                key = (m["param"], m["want"])
                if key in missing:
                    missing[key]["passes"].append(pass_)
                else:
                    missing[key] = dict(m, passes=[pass_])
            for f in r["features_off"]:
                if f not in features_off:
                    features_off.append(f)
        nodes_missing = []
        for cls, spec in target_nodes(t).items():
            if cls in object_info:
                continue
            nodes_missing.append(cls)
            if spec["tier"] == "optional":
                if spec["feature"] and spec["feature"] not in features_off:
                    features_off.append(spec["feature"])
            else:
                blocked = True
        status = ("not_ready" if blocked
                  else "degraded" if missing or nodes_missing else "ready")
        out[t.id] = {"status": status, "missing": list(missing.values()),
                     "resolved": by_pass.get("final") or next(iter(by_pass.values()), {}),
                     "by_pass": by_pass, "features_off": features_off,
                     "nodes_missing": nodes_missing}
    return out


def ready_line(tid: str, r: dict) -> str:
    """One target's readiness, one line: status and what's missing."""
    n = len(r["missing"]) + len(r["nodes_missing"])
    what = []
    for tier in ("required", "accelerator", "optional"):
        k = sum(1 for m in r["missing"] if m["tier"] == tier)
        if k:
            what.append(f"{k} {tier}")
    if r["nodes_missing"]:
        what.append(f"{len(r['nodes_missing'])} node(s)")
    return f"  {tid:<20} {r['status']:<10}" + (f" missing {', '.join(what)}" if n else "")


def cmd_targets(root: str | None, argv: list[str]) -> int:
    """h3.py targets [<episode>] [--json] [--comfy URL]: each target's
    readiness on the running ComfyUI."""
    import json as _json
    ap = argparse.ArgumentParser(prog="h3.py targets",
                                 description="Which targets this ComfyUI can render, and what "
                                             "to download for the rest.")
    ap.add_argument("--json", action="store_true", help="print the readiness as JSON")
    ap.add_argument("--comfy", default="http://127.0.0.1:8188")
    ap.add_argument("--kind", choices=J.TG.KINDS)
    args = ap.parse_args(argv)
    series_cfg, extra, marks = None, {}, {}
    if root:
        series_cfg = J.series_config(root)
        try:
            extra = J.series_model_families(root)
        except ValueError as e:
            print(f"  ! series.json model_families ignored: {e}")
        info = episode_target_info(root)
        marks[info["target"]] = f"episode target ({info['target_source']})"
    comfy = J.Comfy(args.comfy)
    try:
        object_info, error = comfy.object_info(), ""
    except Exception as e:
        object_info, error = None, f"ComfyUI at {args.comfy} didn't answer: {e}"
    targets = J.TG.list_targets(args.kind)
    ready = readiness(targets, object_info, J.model_resolver(), J.TG.modelid.temp_cache(),
                      extra, series_cfg, error)
    if args.json:
        sys.stdout.write(_json.dumps({"comfy": args.comfy, "targets": ready}, indent=2) + "\n")
        return 0 if object_info is not None else 1
    print(f"\n  targets on {args.comfy}" + (f"  ·  {os.path.basename(root)}" if root else ""))
    if object_info is None:
        print(f"  !! {error}")
    for t in targets:
        r = ready[t.id]
        print(ready_line(t.id, r) + (f"   <- {marks[t.id]}" if t.id in marks else ""))
        for m in r["missing"]:
            passes = "" if len(m["passes"]) == len(t.presets) else f" ({'/'.join(m['passes'])})"
            what = f"feature off: {m['feature']}" if m["tier"] == "optional" and m.get("feature") \
                else ("falls back to the base preset" if m["tier"] == "accelerator" else "")
            print(f"      {m['tier']:<11} {m['param']:<13} {m['want']}{passes}"
                  + (f"  [{what}]" if what else ""))
            print(f"                  -> models/{m['folder']}/   "
                  + (m["url"] if m.get("url") else f"no URL: {m['source']}"))
        for c in r["nodes_missing"]:
            print(f"      node        {c}  (update ComfyUI, or install the node pack)")
        for param, v in sorted(r["resolved"].items()):
            if v["how"] == "family":
                print(f"      using       {param}: {v['using']} for {v['want']}")
    print()
    return 0 if object_info is not None else 1


# Runs a script with its own folder on sys.path. `python script.py` normally
# does that, but not under an embedded Python whose ._pth file fixes sys.path
# (ComfyUI's python_embeded), where PYTHONPATH is ignored as well.
RUN_SCRIPT = ("import os, runpy, sys; s = sys.argv[1]; sys.argv = sys.argv[1:]; "
              "sys.path.insert(0, os.path.dirname(os.path.abspath(s))); "
              "runpy.run_path(s, run_name='__main__')")


def run_tool(script: str, args: list[str], cwd: str, timeout: int) -> tuple[int, str, str]:
    """Run a pipeline script beside this file with this Python: (exit code,
    stdout, stderr)."""
    env = dict(os.environ, **TOOL_ENV)
    try:
        r = subprocess.run([sys.executable, "-c", RUN_SCRIPT, os.path.join(HERE, script), *args],
                           capture_output=True, cwd=cwd, env=env, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 1, "", f"{script} took longer than {timeout}s and was stopped"
    out, err = (b.decode("utf-8", "replace").replace("\r\n", "\n")
                for b in (r.stdout, r.stderr))
    return r.returncode, out, err


def build_episode(root: str, timeout: int = 600) -> dict:
    """h3build for the final pass, then the proxy pass, as `h3.py build` runs
    it. A script error is a result (ok: false, the message in the pass's
    `error`), not an exception."""
    series_cfg, script = episode_series_config(root), episode_script(root)
    if not series_cfg or not script:
        why = ("no series.json here or in the parent folder" if not series_cfg
               else "can't tell which .md in the folder is the script")
        return {"ok": False, "error": why, "passes": {}}
    passes = {}
    for ps in T.PASSES:
        rc, out, err = run_tool("h3build.py", [series_cfg, script, "-o", root]
                                + (["--proxy"] if ps == "proxy" else []), root, timeout)
        passes[ps] = {"ok": rc == 0, "report": out, "error": err.strip() if rc else ""}
    return {"ok": all(p["ok"] for p in passes.values()), "passes": passes}


def assemble_episode(root: str, pass_: str, partial: bool = True,
                     timeout: int = 3600) -> dict:
    """h3assemble for one pass, as `h3.py assemble` runs it. `output` is the
    cut's path relative to the episode (forward slashes), or None."""
    args = ["-o", root]
    if pass_ == "proxy":
        args += ["--shotlist", "shotlist/shotlist_proxy.json", "--subfolder", "renders_proxy"]
    if partial:
        args.append("--partial")
    rc, out, err = run_tool("h3assemble.py", args, root, timeout)
    output = None
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("-> ") and line.lower().endswith(".mp4"):
            output = rel(root, line[3:].strip())
    ok = rc == 0 and output is not None
    return {"ok": ok, "output": output, "report": out,
            "error": "" if ok else (err.strip() or "h3assemble wrote no cut")}


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------

def _pass(args) -> str:
    return "proxy" if args.proxy else "final"


def cmd_takes(root: str, argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="h3.py takes")
    ap.add_argument("--proxy", action="store_true")
    ap.add_argument("--only", help="comma-separated shot ids")
    ap.add_argument("--no-sweep", action="store_true",
                    help="don't ask ComfyUI which queued takes are still running")
    ap.add_argument("--comfy", default="http://127.0.0.1:8188")
    args = ap.parse_args(argv)
    pass_ = _pass(args)
    if not args.no_sweep:
        n = sweep(root, pass_, args.comfy)
        if n > 0:
            print(f"  marked {n} take(s) failed: queued, but ComfyUI no longer has the job")
    st = episode_status(root, pass_)
    only = {s.strip() for s in args.only.split(",")} if args.only else None
    picks = sum(1 for s in st["shots"] if s["cut"]["picked"])
    print(f"\n  {st['episode']}  ·  {pass_}  ·  {len(st['shots'])} shots"
          + (f", {picks} picked in cut.json" if picks else ""))
    counts = {"none": 0, "stale": 0}
    for s in st["shots"]:
        if only and s["shot"] not in only:
            continue
        head = s["shot"] + ("  (orphan: not in the script)" if s["orphan"] else "")
        ovf = s["override"]["fields"]
        if ovf:
            head += f"   override: {', '.join(ovf)}" + ("  (STALE)" if s["override"]["stale"] else "")
        print(f"\n  {head}")
        c = s["cut"]
        if not s["takes"] and not c["placeholder"]:
            counts["none"] += 1
            print("      (no takes)")
        for t in s["takes"]:
            mark = ""
            if not c["placeholder"] and t["take"] == c["take"]:
                mark = "<- cut" + (" (picked)" if c["picked"] else "")
            stale = [r for r in t["stale"] if r != "unknown"]
            if stale and t["status"] == "ok":
                counts["stale"] += 1
            seed = (f"seed {t['seed']} {t['seed_source'] or ''}".rstrip()
                    if t["seed"] is not None else "no sidecar")
            bits = [f"t{t['take']:02d}", f"{t['status']:<7}", f"{seed:<30}",
                    ("stale: " + ",".join(stale)) if stale else "", mark,
                    t["note"]]
            print("      " + "  ".join(b for b in bits if b))
        if c["placeholder"]:
            print(f"      <- cut uses {c['pass']} t{c['take']:02d} as a placeholder"
                  if c["take"] else f"      <- cut wants a {c['pass']} placeholder, none usable")
        elif c["picked"] and not c["usable"]:
            print(f"      ! cut.json picks t{c['take']:02d}, which is not usable")
    print(f"\n  {counts['none']} shot(s) with no takes, {counts['stale']} stale take(s)\n")
    return 0


def cmd_pick(root: str, argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="h3.py pick")
    ap.add_argument("shot")
    ap.add_argument("take", help="take number, or 'latest' to follow the newest usable take")
    ap.add_argument("--proxy", action="store_true", help="edit the proxy cut")
    ap.add_argument("--from", dest="src", choices=T.PASSES,
                    help="take comes from this pass (a placeholder)")
    ap.add_argument("--force", action="store_true", help="pick a take that isn't usable yet")
    args = ap.parse_args(argv)
    pass_ = _pass(args)
    src = args.src or pass_
    J.load_shotlist(root, pass_)            # no build: fail before parsing the take
    if args.take == "latest":
        take = None
    else:
        try:
            take = int(args.take.lstrip("tT"))
        except ValueError:
            print(f"  !! take must be a number or 'latest', not {args.take!r}")
            return 2
    try:
        pick_take(root, pass_, args.shot, take, from_pass=src, force=args.force)
    except NotUsable as e:
        print(f"  !! {e}; --force to pick it anyway")
        return 1
    except KeyError as e:
        print(f"  !! {e.args[0]}")
        return 1
    except LookupError as e:
        print(f"  !! {e}")
        return 1
    print(f"  {pass_} cut: {args.shot} -> "
          + (f"{src} t{take:02d}" if take is not None else "latest usable take"))
    return 0


def cmd_override(root: str, argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="h3.py override")
    ap.add_argument("shot", nargs="?", help="the shot (not needed with only --episode-target)")
    ap.add_argument("--episode-target", metavar="TARGET",
                    help="the episode's video target: every shot the script gives no target "
                         "renders on it ('built' clears it: back to the series config's)")
    p = ap.add_mutually_exclusive_group()
    p.add_argument("--proxy", action="store_true", help="model/LoRA/steps for the proxy pass")
    p.add_argument("--both", action="store_true", help="model/LoRA/steps for both passes")
    ap.add_argument("--seed", type=int)
    ap.add_argument("--steps", type=int)
    ap.add_argument("--model")
    ap.add_argument("--lora", action="append", metavar="NAME[:STRENGTH]",
                    help="repeat to stack; 'none' for no LoRA")
    ap.add_argument("--prompt-file", help="text file whose contents become the prompt")
    ap.add_argument("--note")
    ap.add_argument("--target", metavar="TARGET",
                    help="render this shot on another video target (both passes); "
                         "'built' goes back to the one the build chose")
    ap.add_argument("--clear", nargs="*", metavar="FIELD",
                    help="remove these fields (all of this shot's override if none named)")
    ap.add_argument("--show", action="store_true", help="print the effective override")
    ap.add_argument("--dump-prompt", action="store_true",
                    help="print the prompt the next render would use, for editing")
    args = ap.parse_args(argv)
    pass_ = _pass(args)
    if args.episode_target is not None:
        want = None if args.episode_target in ("built", "none", "series", "") \
            else args.episode_target
        try:
            info = set_episode_target(root, want)
        except Exception as e:
            print(f"  !! {e}")
            return 2
        print(f"  episode target: {info['target']} ({info['target_source']})"
              + (f"; the series config says {info['series_target']}"
                 if info["series_target"] and info["series_target"] != info["target"] else ""))
        if info["target_source"] == "editor":
            print(f"  shots the script gives no target render on {info['target']}; to make it "
                  f"permanent, put \"target\": \"{info['target']}\" in series.json's \"series\"")
        if not args.shot:
            return 0
    if not args.shot:
        ap.error("give a shot, or --episode-target")
    try:
        doc, idx = J.find_shot(root, pass_, args.shot)
    except KeyError:
        print(f"  !! {args.shot} is not in {J.shotlist_rel(pass_)}")
        return 1
    ov = T.load_overrides(root)
    built_target = J.shotlist_target(doc).id

    if args.target is not None:
        want = None if args.target in ("none", "") else args.target
        if want == "built":
            # with an episode target, 'built' pins the shot to its build's
            # target; without one it clears the retarget
            want = built_target
        if want:
            try:
                J.check_video_target(want)
            except Exception as e:
                print(f"  !! {e}")
                return 2
        T.set_shot_target(ov, args.shot, keeps_shot_target(ov, want, built_target))

    if args.dump_prompt:
        job = J.plan_job(root, pass_, doc, idx, J.RenderRequest(args.shot), ov)
        if job.error:
            print(f"  !! {job.error}")
            return 1
        pr = job.prompt
        sys.stdout.write(("\n\n".join(pr) if isinstance(pr, list) else pr) + "\n")
        return 0

    # the target the shot renders on now: its override block is the one written
    target = J.effective_target(ov, args.shot, built_target, root=root)
    # each pass's entry for that target: overrides are stamped against it
    built = pass_entries(root, args.shot, target)

    passes = list(T.PASSES) if args.both else [pass_]
    missing = [ps for ps in passes if ps not in built]
    if missing:
        print(f"  !! {args.shot} has no {'/'.join(missing)} build"
              + (f" that compiles for {target}" if target != built_target else "")
              + " — run h3.py build first")
        return 1
    user_fields = [f for f in T.SHOT_FIELDS + T.PASS_FIELDS if f != "base_hash"] + ["target"]
    changed = args.target is not None
    if args.clear is not None:
        # bare --clear drops the whole override (both passes unless --proxy);
        # named pass fields follow the usual pass flags
        fields = args.clear or user_fields
        clear_passes = T.PASSES if (not args.clear and not args.proxy) else passes
        for f in fields:
            if f == "target":
                T.set_shot_target(ov, args.shot, None)
            elif f in T.SHOT_FIELDS:
                T.set_override(ov, args.shot, target=target, **{f: None})
            elif f in user_fields:
                for ps in clear_passes:
                    T.set_override(ov, args.shot, ps, target, **{f: None})
            else:
                print(f"  !! unknown field {f!r}: one of {', '.join(user_fields)}")
                return 2
        changed = True
    shot_fields = {}
    if args.seed is not None:
        shot_fields["seed"] = args.seed
    if args.note is not None:
        shot_fields["note"] = args.note
    pass_fields = {}
    if args.prompt_file:
        with open(args.prompt_file, encoding="utf-8") as fh:
            pass_fields["prompt"] = fh.read().strip()
    if args.steps is not None:
        pass_fields["steps"] = args.steps
    if args.model:
        pass_fields["model"] = args.model
    if args.lora:
        pass_fields["loras"] = [l for spec in args.lora for l in J.parse_lora(spec)]
    if shot_fields or pass_fields:
        set_shot_override(ov, args.shot, built, passes, shot_fields, pass_fields, target)
        changed = True
    if changed:
        ov.setdefault("episode", doc.get("episode", ""))
        T.save_overrides(root, ov)

    target = J.effective_target(ov, args.shot, built_target, root=root)
    if target != built_target:
        print(f"  {args.shot} renders on {target} (built for {built_target}); "
              f"a prompt override is ignored while it is retargeted")
    for ps in T.PASSES:
        eff = T.shot_override(ov, args.shot, ps, target)
        if not {k for k in eff if k not in T.SHOT_FIELDS} and ps != pass_:
            continue
        stale = (eff.get("base_hash") and ps in built
                 and eff["base_hash"] != J.story_hash(built[ps]))
        print(f"  {args.shot} [{ps}]" + ("  (written against an older build: STALE)"
                                          if stale else ""))
        if not {k for k in eff if k != "base_hash"}:
            print("      no override")
        for k in ("seed", "steps", "model", "loras", "note", "prompt"):
            if k not in eff:
                continue
            v = eff[k]
            if k == "loras":
                v = ", ".join(f"{l['name']}@{l.get('strength', 1):g}" for l in v) or "none"
            elif k == "prompt" and not args.show:
                text = "\n\n".join(v) if isinstance(v, list) else v
                v = f"{len(text)} chars (--show to print)"
            elif k == "prompt":
                v = "\n" + ("\n\n".join(v) if isinstance(v, list) else v)
            print(f"      {k:<6} {v}")
    return 0


def _frame_arg(v: str):
    v = v.strip().lower()
    if v in ("first", "last"):
        return v
    try:
        return int(v)
    except ValueError:
        raise argparse.ArgumentTypeError(f"a frame number, first or last, not {v!r}") from None


def cmd_keyframe(root: str, argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        prog="h3.py keyframe",
        description="Make a shot's first (or last) keyframe from a frame of another "
                    "shot's take: by default the previous shot's last frame, from the "
                    "take the cut uses.")
    ap.add_argument("shot", nargs="?", help="the shot (not needed with --missing)")
    act = ap.add_mutually_exclusive_group()
    act.add_argument("--clear", action="store_true",
                     help="unpick the keyframe: its file goes (the takes stay), the shot "
                          "renders without one, and nothing re-picks it until you pick")
    act.add_argument("--generate", action="store_true",
                     help="make a still from the shot's description with the keyframe image "
                          "target (an edit target also gets the picked character views and "
                          "the plate)")
    act.add_argument("--missing", action="store_true",
                     help="fill every keyframe the episode needs (required ones, and optional "
                          "ones the script asks for) by its method: continuity, else generate")
    ap.add_argument("--target", metavar="IMAGE_TARGET",
                    help="with --generate/--missing: the image target (default: the ref's "
                         "override, the episode's, the series config's refs block, else "
                         "flux2_klein_edit when installed)")
    ap.add_argument("--dry-run", action="store_true",
                    help="with --generate/--missing: say what would be done, and the prompt")
    ap.add_argument("--comfy", default="http://127.0.0.1:8188")
    ap.add_argument("--timeout", type=int, default=900, help="seconds to wait per image")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--from-prev", action="store_true",
                     help="the neighbouring shot in cut order (the default): the previous "
                          "shot for --first, the next one for --last")
    src.add_argument("--from", dest="src", metavar="SHOT[:TAKE]",
                     help="this shot's take (default: the one its cut entry uses)")
    end = ap.add_mutually_exclusive_group()
    end.add_argument("--first", action="store_true", help="write the first keyframe (default)")
    end.add_argument("--last", action="store_true", help="write the last keyframe")
    ap.add_argument("--frame", type=_frame_arg, metavar="N",
                    help="frame number in the source take (0 = first, -1 = last; "
                         "default: last for --first, first for --last)")
    ap.add_argument("--proxy", action="store_true", help="the proxy cut and takes")
    pk = ap.add_mutually_exclusive_group()
    pk.add_argument("--pick", action="store_true",
                    help="make it the live keyframe even if one exists")
    pk.add_argument("--no-pick", action="store_true", help="only add it as a take")
    ap.add_argument("--note", default="")
    args = ap.parse_args(argv)
    import h3refs as R                                   # h3refs imports this module
    pass_ = _pass(args)
    which = "last" if args.last else "first"
    if not args.shot and not args.missing:
        ap.error("give a shot (or --missing)")
    if args.clear or args.generate or args.missing:
        return _keyframe_action(R, root, args, pass_, which)
    source_shot = source_take = None
    if args.src:
        source_shot, _, tk = args.src.partition(":")
        if tk:
            try:
                source_take = int(tk.lstrip("tT"))
            except ValueError:
                print(f"  !! --from takes SHOT or SHOT:TAKE, not {args.src!r}")
                return 2
    try:
        s = R.load_series(root)
        res = R.keyframe_from_take(s, args.shot, which, source_shot, source_take,
                                   args.frame, pass_,
                                   pick=True if args.pick else (False if args.no_pick else None),
                                   note=args.note)
    except (R.RefError, R.UnknownRef, R.NotUsable, R.FfmpegMissing,
            FileNotFoundError) as e:
        print(f"  !! {e}")
        return 1
    src_ = res.source
    print(f"  {res.ref.id} t{res.take.take:02d}: frame {src_['frame']} of {src_['frames']} "
          f"of {src_['shot']} {src_['pass']} t{src_['take']:02d}")
    print(f"      {R.ep_rel(root, res.take.paths.image)}")
    if res.picked:
        print(f"      picked: {R.ep_rel(root, res.ref.file)}")
    else:
        print(f"      not picked ({R.ep_rel(root, res.ref.file)} already exists; "
              f"--pick to replace it)")
    return 0


def _keyframe_action(R, root: str, args, pass_: str, which: str) -> int:
    """h3.py keyframe --clear / --generate / --missing."""
    try:
        s = R.load_series(root)
    except (FileNotFoundError, ValueError) as e:
        print(f"  !! {e}")
        return 1
    if args.clear:
        try:
            res = R.clear_pick(s, R.find_ref(s, f"shot:{args.shot}:{which}"))
        except (R.RefError, R.UnknownRef) as e:
            print(f"  !! {e}")
            return 1
        print(f"  {res.ref.id}: cleared"
              + (f" (was t{res.was:02d})" if res.was else "")
              + (f"; removed {R.ep_rel(root, res.removed[0])}" if res.removed else
                 "; there was no live file")
              + ". The takes stay; nothing re-picks it until you pick one.")
        need = R.keyframe_needs(root).get((args.shot, which))
        if need and need["need"] == "required":
            print(f"      {need['target']} needs it: {args.shot} is blocked until a "
                  f"{which} keyframe is picked")
        return 0
    comfy = J.Comfy(args.comfy, client_id="h3keyframe")
    listing = J.model_lister(comfy)                    # read-only: /object_info
    base = None
    if not args.dry_run:
        try:
            comfy.ping()
        except Exception as e:
            print(f"  !! can't reach ComfyUI at {args.comfy}: {e}")
            return 1
        try:
            base, _ = R.resolve_workflow(args.comfy)
        except Exception:
            base = None
    common = dict(comfy=comfy, pass_=pass_, base=base, listing=listing,
                  resolve=J.model_resolver(), cache=J.TG.modelid.temp_cache(),
                  dry_run=args.dry_run, target=args.target, timeout=args.timeout)
    if args.generate:
        todo = [(args.shot, which)]
    else:
        todo = [(sh, w) for sh, w, _n in R.missing_keyframes(s)]
        if args.shot:
            todo = [t for t in todo if t[0] == args.shot]
        if not todo:
            print("  no keyframe is missing (required, or asked for by the script)")
            return 0
    failed = 0
    for sh, w in todo:
        try:
            if args.generate:
                req = R.GenRequest(f"shot:{sh}:{w}", target=args.target, pass_=pass_)
                if args.dry_run:
                    (job,) = R.plan_generate(s, req, ready=R.target_ready(listing))
                    refs = ", ".join(f"{r.get('subject') or r.get('location')}"
                                     f"{' ' + r['view'] if r.get('view') else ''}"
                                     for r in job.references)
                    print(f"  shot:{sh}:{w}: {job.target.id} at {job.width}x{job.height}"
                          f" (renders {job.render_size[0]}x{job.render_size[1]} on "
                          f"{job.video_target}), seed {job.seed}"
                          + (f"\n      references: {refs}" if refs else "")
                          + f"\n      negative ({job.negative_source}): {job.negative!r}"
                          + f"\n{job.prompt}\n")
                    continue
                R.generate_and_wait(s, req, comfy, base, args.timeout, listing,
                                    common["resolve"], common["cache"],
                                    pick=True if args.pick else (False if args.no_pick else None))
                continue
            res = R.fill_keyframe(s, sh, w, **common)
            print(f"  shot:{sh}:{w}: {res.method}: {res.detail}")
        except (R.RefError, R.UnknownRef, R.NotUsable, R.FfmpegMissing, RuntimeError,
                ValueError) as e:
            print(f"  !! shot:{sh}:{w}: {e}")
            failed += 1
    return 1 if failed else 0


COMMANDS = {"takes": cmd_takes, "pick": cmd_pick, "override": cmd_override,
            "keyframe": cmd_keyframe}
