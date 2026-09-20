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
    python h3.py discard  Shows\\ep05 sh020 3 [--proxy]          # move a take to the trash
    python h3.py cut      Shows\\ep05 [--proxy] [--show]         # the cut: order, trims, locks
    python h3.py cut      Shows\\ep05 --move sh050 --before sh020
    python h3.py cut      Shows\\ep05 --order sh010,sh030,sh020  # these first, the rest after
    python h3.py cut      Shows\\ep05 --trim sh020 4 2           # frames off the head / tail
    python h3.py cut      Shows\\ep05 --lock sh020 | --unlock sh020
    python h3.py cut      Shows\\ep05 --reset order|trims|audio|all
    python h3.py cut      Shows\\ep05 --proxy --copy-from final [order|trims|audio|all]
    python h3.py cut      Shows\\ep05 --audio sh020 take sh020:1 --at 0.2 --gain 1.5
    python h3.py cut      Shows\\ep05 --audio sh030 file audio/line.wav | none | own
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
`discard` moves a take's files to renders[_proxy]/_trash/<shot>/ (nothing is
deleted, and nothing lists the trash); a cut entry that picked it goes back
to the latest usable take. A queued take must be cancelled first.
`cut` edits a pass's cut.json (the old one goes to _history/): reorder, trims
(in the cut's frames; at least one frame of the take stays), locks (a locked
shot refuses picks, moves, trims and audio sources without --force), reset to
script order, no trims or no audio sources, and copying the other pass's
order/trims/audio (trims converted when the passes' frame rates differ).
`--audio` says where one clip's sound comes from: another shot's take, a file
in the episode, or silence (`none`); `own` puts it back on its own take's
sound. `--from` starts that many seconds into the source, `--at` shifts it
against the picture and `--gain` scales it. The sound is cut or padded to the
clip, whose length never changes.
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
import re
import subprocess
import sys

import h3jobs as J
import h3peaks
import h3takes as T


# ---------------------------------------------------------------------------
# status: what the editor's shot bin shows
# ---------------------------------------------------------------------------

SCRIPT_SKIP = ("refs_todo", "readme", "notes", "align_report")


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
               "audio": (".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac", ".opus")}


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
    script_idx = {sid: i for i, sid in enumerate(shots)}
    resolved = T.resolve_cut(cut, pass_, list(shots))
    moved = out_of_order([script_idx.get(e.shot) for e in resolved])
    for order, e in enumerate(resolved):
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
                    "fps": (take_fps(chosen) if chosen_ok else None) or sfps,
                    # its place in this pass's cut, and in the script (None:
                    # an orphan); out of order when a move took it off the
                    # script's order
                    "order": order, "script_index": script_idx.get(e.shot),
                    "out_of_order": moved[order],
                    # where this clip's sound comes from (Phase 9d): the
                    # stored source, the file that will play, the badge
                    "audio": e.audio, **cut_audio(root, e, pass_)},
            # the shot's dialogue window on the recording (`track`), seconds
            **({"audio_in": shot["audio_in"], "audio_out": shot["audio_out"]}
               if shot and "audio_in" in shot and "audio_out" in shot else {}),
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
                # whose sound the cut plays for it (assemble's --audio auto)
                "audio": take_audio(root, t),
                "queued": (t.sidecar or {}).get("queued"),
                "comfy_prompt_id": (t.sidecar or {}).get("comfy_prompt_id"),
                "finished": (t.sidecar or {}).get("finished"),
                "save_notes": (t.sidecar or {}).get("save_notes", ""),
            } for t in takes],
        })
    d = doc0.get("defaults", {})
    track = episode_track(root)
    if track is not None:
        # how many shots the build gave a dialogue window (h3align's work)
        track["aligned"] = sum(1 for s in out if "audio_in" in s)
    return {"episode": doc0.get("episode", os.path.basename(root)),
            "title": doc0.get("title", ""), "pass": pass_,
            **episode_target_info(root, ov, J.shotlist_target(doc0).id),
            "fps": fps, "width": d.get("width"),
            "height": d.get("height"), "folder": folder or T.pass_subfolder(pass_),
            "track": track,
            "shots": out}


def cut_audio(root: str, e: T.CutEntry, pass_: str) -> dict:
    """{"audio_file", "audio_why"} for one cut entry: the file its audio
    source will actually play (relative to the episode; null for `none`, for
    no source, and for a source that isn't there) and the badge's text
    (h3takes.audio_label, with " (missing)" when the file is gone)."""
    if not e.audio:
        return {"audio_file": None, "audio_why": None}
    f = T.audio_source_file(root, e.audio, pass_)
    why = T.audio_label(e.audio, pass_)
    if f is None and e.audio["source"] != "none":
        why += " (missing)"
    return {"audio_file": rel(root, f) if f else None, "audio_why": why}


def take_audio(root: str, t: T.Take) -> str | None:
    """The file whose sound the cut plays for a take, relative to the
    episode: its mp4 if that has an audio stream, else its _h3.wav, else None
    (h3peaks.clip_audio, the rule of h3assemble's --audio auto)."""
    p = h3peaks.clip_audio(t.paths.mp4, t.paths.h3_wav)
    return rel(root, p) if p else None


def words_cache(recording: str) -> str:
    """Where h3align caches a recording's word timings."""
    return recording + ".words.json"


def has_words(recording: str) -> bool:
    """Whether a usable transcript sits beside the recording (h3align's rule:
    the cache is newer than the recording, else it transcribes again)."""
    cache = words_cache(recording)
    try:
        return os.path.getmtime(cache) >= os.path.getmtime(recording)
    except OSError:
        return False


def episode_track(root: str) -> dict | None:
    """The series config's recorded dialogue (`audio.track`, relative to the
    episode) as {"path" (relative to the episode, forward slashes; absolute
    when it is on another drive), "duration" (seconds), "rate" (Hz), "exists",
    "words" (a cached transcript sits beside it)}, or None when the series
    config names none. `aligned` (how many shots carry a window) is added by
    episode_status, which knows the shots."""
    cfg = episode_series_config(root)
    try:
        b = (T.read_json(cfg) or {}) if cfg else {}
    except ValueError:
        return None
    audio = b.get("audio") if isinstance(b, dict) else None
    track = audio.get("track") if isinstance(audio, dict) else None
    if not isinstance(track, str) or not track.strip():
        return None
    full = os.path.normpath(track if os.path.isabs(track) else os.path.join(root, track))
    try:
        path = os.path.relpath(full, root).replace(os.sep, "/")
    except ValueError:                                    # another drive
        path = full
    if not os.path.isfile(full):
        return {"path": path, "duration": None, "rate": None, "exists": False,
                "words": False}
    info = h3peaks.media_info(full)
    dur = info["duration"]
    return {"path": path, "duration": round(dur, 6) if dur is not None else None,
            "rate": info["rate"], "exists": True, "words": has_words(full)}


def track_info(root: str, pass_: str = "proxy") -> dict | None:
    """episode_track with `aligned` (how many of the pass's shots carry a
    dialogue window), for the routes that change the recording without asking
    for the whole episode status."""
    track = episode_track(root)
    if track is not None:
        track["aligned"] = len(dialogue_windows(root, pass_, pass_fps(root, pass_)))
    return track


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
    # a variant (`of:`) shares the subject's voice, and so its voice ref: there
    # is no voice:<variant> to point at (h3refs.series_refs)
    from h3core.series_config import variant_of
    variants = variant_of(cfg)
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
            rid = (f"voice:{variants.get(r['subject'], r['subject'])}"
                   if r.get("subject") else None)
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


class Locked(Exception):
    """A pick (or a CLI move or trim) of a shot whose cut entry is locked."""


class CutError(ValueError):
    """A cut edit that can't be made: a bad trim, an unknown shot (400)."""


def pick_take(root: str, pass_: str, shot_id: str, take: int | None,
              from_pass: str | None = None, force: bool = False) -> dict:
    """Point `pass_`'s cut at take `take` of `shot_id` (None: latest usable),
    from `from_pass` (default the same pass; the other one makes a placeholder).
    Writes cut.json and returns it. Raises KeyError for a shot in neither the
    script nor the cut, LookupError for a take that doesn't exist, NotUsable
    for a take that can't be cut in and Locked for a shot whose cut entry is
    locked (unless `force`, for either)."""
    src = from_pass or pass_
    doc = J.load_shotlist(root, pass_)
    order = [d["shots"][i]["id"] for d, i in J.episode_shots(root, pass_)]
    if not force:
        e = next((e for e in T.resolve_cut(T.load_cut(root), pass_, order)
                  if e.shot == shot_id), None)
        if e is not None and e.locked:
            raise Locked(f"{shot_id} is locked in the {pass_} cut")
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
    """Replace one pass's list in cut.json (reorder, trims, locks, audio
    sources). Entries are cut.json entries; shots not in the script are allowed
    (they become orphans). CutError for trims that aren't whole frames >= 0, or
    that leave less than one frame of the take the entry uses (when its frame
    count is known), and for an audio source that can't be played (check_audio).
    The old cut.json goes to _history/ (write_cut)."""
    T.pass_subfolder(pass_)                               # validates the pass
    entries = [dict(e) for e in entries]
    check_trims(root, pass_, entries)
    check_audio(root, pass_, entries)
    return write_cut(root, pass_, entries)


# ---------------------------------------------------------------------------
# cut edits (Phase 9b): the timeline's reorder, trims, locks, reset and copy
# ---------------------------------------------------------------------------

def write_cut(root: str, pass_: str, entries: list[dict]) -> dict:
    """Write `entries` as pass `pass_`'s list in cut.json and return the cut.
    The file as it was goes to <ep>/_history/cut.json.<stamp> first (the
    newest h3source.HISTORY_KEEP are kept); an edit that changes nothing
    writes nothing."""
    import h3source                        # it imports this module: not at the top
    cut = T.load_cut(root)
    new = copy.deepcopy(cut)
    new[pass_] = [dict(e) for e in entries]
    if "episode" not in new:
        try:
            new["episode"] = J.load_shotlist(root, pass_).get("episode", "")
        except FileNotFoundError:
            new["episode"] = os.path.basename(os.path.normpath(root))
    if new == cut:
        return cut
    path = os.path.join(root, T.CUT_FILE)
    if os.path.isfile(path):
        with open(path, "rb") as fh:
            h3source.save_history_bytes(root, T.CUT_FILE, fh.read())
    T.save_cut(root, new)
    return new


def pass_fps(root: str, pass_: str) -> float:
    """The frame rate a pass's cut (and its trims) counts in: assemble's (the
    series config's `series.fps`, else that pass's shotlist's, else 24)."""
    import h3assemble
    try:
        doc = J.load_shotlist(root, pass_)
    except FileNotFoundError:
        doc = {}
    return h3assemble.episode_fps(root, doc)


def script_order(root: str, pass_: str) -> list[str]:
    """Every shot of the pass's build in script order (every target's)."""
    return [d["shots"][i]["id"] for d, i in J.episode_shots(root, pass_)]


def take_span(root: str, e: T.CutEntry, fps: float) -> int | None:
    """How many of the cut's frames (at `fps`) the take a cut entry uses has,
    when its frame count is known (its sidecar's `frames`), else None. A take
    at another rate (Wan 14B's 16 fps) is counted by its duration."""
    try:
        chosen, _, ok = cut_take(root, e)
    except ValueError:                                    # an unknown pass
        return None
    n = take_frames(chosen) if ok else None
    if n is None:
        return None
    tf = take_fps(chosen) or fps
    return n if abs(tf - fps) < 1e-3 else max(1, round(n * fps / tf))


def dialogue_windows(root: str, pass_: str, fps: float) -> dict[str, int]:
    """Shot id -> the frames its dialogue window keeps, rounded against the
    earliest window exactly as h3assemble does (shots without one left out)."""
    try:
        shots = [d["shots"][i] for d, i in J.episode_shots(root, pass_)]
    except FileNotFoundError:
        return {}
    wins = [s for s in shots if "audio_in" in s and "audio_out" in s]
    if not wins:
        return {}
    base = min(s["audio_in"] for s in wins)
    return {s["id"]: round((s["audio_out"] - base) * fps) - round((s["audio_in"] - base) * fps)
            for s in wins}


def _trim(v, what: str) -> int:
    if v is None:
        return 0
    if isinstance(v, bool) or not isinstance(v, int) or v < 0:
        raise CutError(f"{what} must be a whole number of frames >= 0, not {v!r}")
    return v


def check_trims(root: str, pass_: str, entries: list[dict]) -> None:
    """CutError for a trim that isn't a whole number >= 0, or trims that
    leave less than one frame of the take an entry uses (when its frame count
    is known; a take not rendered yet can be trimmed ahead). A shot with a
    dialogue window counts from the window's frames, as h3assemble cuts the
    clip to its window before the trims."""
    fps, windows = None, None
    for raw in entries:
        sid = raw.get("shot")
        ti = _trim(raw.get("trim_in"), f"{sid}: trim_in")
        to = _trim(raw.get("trim_out"), f"{sid}: trim_out")
        if not (ti or to):
            continue
        fps = fps or pass_fps(root, pass_)
        if windows is None:
            windows = dialogue_windows(root, pass_, fps)
        src = raw.get("pass") or pass_
        e = T.CutEntry(shot=sid, pass_=src, take=raw.get("take"), placeholder=src != pass_)
        span = take_span(root, e, fps)
        keep = windows.get(sid)
        if keep:
            span = keep if span is None else min(keep, span)
        if span is not None and span - ti - to < 1:
            which = f"t{e.take:02d}" if isinstance(e.take, int) else "its take"
            raise CutError(f"{sid}: trim_in {ti} + trim_out {to} leave nothing of "
                           f"{which} ({span} frames): at least one frame must stay")


def _inside(path: str, root: str) -> bool:
    try:
        return os.path.commonpath([os.path.normcase(os.path.realpath(path)),
                                   os.path.normcase(os.path.realpath(root))]) \
            == os.path.normcase(os.path.realpath(root))
    except ValueError:                                    # another drive
        return False


def episode_file(root: str, path: str) -> str:
    """A media file an audio source names, as a full path. It must be relative
    to the episode and stay inside it — or inside the folder of a
    parent-folder series config, where the series' own audio lives (the rule
    /h3pipe/file and /h3pipe/peaks follow). CutError otherwise, and for a file
    that isn't there."""
    parts = [p for p in str(path).replace("\\", "/").split("/") if p not in ("", ".")]
    if (str(path).startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", str(path))
            or "\x00" in str(path) or not parts):
        raise CutError(f"{path!r}: an audio path is relative to the episode")
    full = os.path.normpath(os.path.join(root, *parts))
    cfg = episode_series_config(root)
    home = os.path.dirname(os.path.abspath(cfg)) if cfg else root
    if not (_inside(full, root) or _inside(full, home)):
        raise CutError(f"{path}: an audio path must stay inside the episode")
    if not os.path.isfile(full):
        raise CutError(f"no audio file {path} in the episode")
    return full


def check_audio(root: str, pass_: str, entries: list[dict]) -> None:
    """Check each entry's `audio` and rewrite it in its stored form (in
    place). CutError for a bad shape (h3takes.audio_spec: an unknown key, a
    negative `start`, a gain outside 0..4), a take that doesn't exist or has
    no sound, or a file outside the episode, missing, or with no audio
    stream."""
    for raw in entries:
        if "audio" not in raw:
            continue
        sid = raw.get("shot")
        try:
            spec = T.audio_spec(raw.get("audio"), pass_)
        except ValueError as e:
            raise CutError(f"{sid}: {e}")
        if spec is None:
            raw.pop("audio", None)
            continue
        if spec["source"] == "take":
            label = T.audio_label(spec, pass_)
            t = T.get_take(root, spec["pass"], spec["shot"], spec["take"])
            if t is None:
                raise CutError(f"{sid}: audio from {label}: there is no such take "
                               f"in the {spec['pass']} pass")
            if h3peaks.clip_audio(t.paths.mp4, t.paths.h3_wav) is None:
                raise CutError(f"{sid}: audio from {label}: that take has no sound")
        elif spec["source"] == "file":
            try:
                full = episode_file(root, spec["path"])
            except CutError as e:
                raise CutError(f"{sid}: {e}")
            if not h3peaks.has_audio(full):
                raise CutError(f"{sid}: {spec['path']} has no audio stream")
        raw["audio"] = spec


def out_of_order(script_index: list[int | None]) -> list[bool]:
    """Which cut entries aren't where script order would put them: those
    outside the longest run that is in script order (so moving one shot flags
    that shot). None (an orphan) is never flagged."""
    import bisect
    items = [(i, v) for i, v in enumerate(script_index) if v is not None]
    tails_v: list[int] = []
    tails_k: list[int] = []
    prev: dict[int, int | None] = {}
    for k, (_, v) in enumerate(items):
        j = bisect.bisect_left(tails_v, v)
        prev[k] = tails_k[j - 1] if j > 0 else None
        if j == len(tails_v):
            tails_v.append(v)
            tails_k.append(k)
        else:
            tails_v[j], tails_k[j] = v, k
    keep = set()
    k = tails_k[-1] if tails_k else None
    while k is not None:
        keep.add(items[k][0])
        k = prev[k]
    return [v is not None and i not in keep for i, v in enumerate(script_index)]


def _entries_json(entries: list[T.CutEntry], pass_: str) -> list[dict]:
    return [T.cut_entry_to_json(e, pass_) for e in entries]


CUT_WHAT = ("order", "trims", "audio", "all")


def _what(what) -> tuple[bool, bool, bool]:
    if what not in CUT_WHAT:
        raise CutError(f"what must be one of {', '.join(CUT_WHAT)}, not {what!r}")
    return what in ("order", "all"), what in ("trims", "all"), what in ("audio", "all")


def reset_cut(root: str, pass_: str, what: str) -> dict:
    """Put a pass's cut back in script order (`order`; picks, locks and notes
    kept, orphans after the script's shots), clear its trims (`trims`) and/or
    its audio sources (`audio`) — a locked entry keeps both — or all of it
    (`all`). Returns the cut."""
    T.pass_subfolder(pass_)
    order, trims, audio = _what(what)
    idx = {sid: i for i, sid in enumerate(script_order(root, pass_))}
    entries = T.resolve_cut(T.load_cut(root), pass_, list(idx))
    if order:
        entries = (sorted((e for e in entries if not e.orphan), key=lambda e: idx[e.shot])
                   + [e for e in entries if e.orphan])
    for e in entries:
        if e.locked:
            continue
        if trims:
            e.trim_in = e.trim_out = 0
        if audio:
            e.audio = None
    return write_cut(root, pass_, _entries_json(entries, pass_))


def copy_cut(root: str, src: str, dst: str, what: str) -> dict:
    """Copy pass `src`'s order, trims and/or audio sources onto pass `dst`'s
    cut. Picks, locks and notes stay `dst`'s own. A shot only `dst` has stays
    after the shot it follows now. Trims are converted when the passes' frame
    rates differ (rounded to whole frames), and cut down (trim_out first) so
    that at least one frame of the take stays; audio sources are copied as
    they stand (a take source keeps the pass it names). A locked entry keeps
    its trims and its audio. Returns the cut."""
    T.pass_subfolder(src)
    T.pass_subfolder(dst)
    if src == dst:
        raise CutError("copy from the other pass: from and to are the same")
    order, trims, audio = _what(what)
    a = cut_entries(root, src)
    b = cut_entries(root, dst)
    if order:
        pos = {e.shot: i for i, e in enumerate(a)}
        out = sorted((e for e in b if e.shot in pos), key=lambda e: pos[e.shot])
        for i, e in enumerate(b):
            if e.shot in pos:
                continue
            at = 0
            if i > 0:
                at = next(j for j, x in enumerate(out) if x.shot == b[i - 1].shot) + 1
            out.insert(at, e)
        b = out
    if trims:
        fa, fb = pass_fps(root, src), pass_fps(root, dst)
        by = {e.shot: e for e in a}
        for e in b:
            s = by.get(e.shot)
            if s is None or e.locked:
                continue
            if abs(fa - fb) < 1e-3:
                ti, to = s.trim_in, s.trim_out
            else:
                ti, to = round(s.trim_in * fb / fa), round(s.trim_out * fb / fa)
            span = take_span(root, e, fb)
            if span is not None:
                over = ti + to - (span - 1)
                if over > 0:
                    less = min(to, over)
                    to, over = to - less, over - less
                    ti = max(0, ti - over)
            e.trim_in, e.trim_out = ti, to
    if audio:
        by = {e.shot: e for e in a}
        for e in b:
            s = by.get(e.shot)
            if s is None or e.locked:
                continue
            e.audio = copy.deepcopy(s.audio)
    return write_cut(root, dst, _entries_json(b, dst))


def _find(entries: list[T.CutEntry], shot_id: str, pass_: str) -> int:
    i = next((i for i, e in enumerate(entries) if e.shot == shot_id), None)
    if i is None:
        raise KeyError(f"{shot_id} is not in the {pass_} cut")
    return i


def move_shot(root: str, pass_: str, shot_id: str, anchor: str, after: bool,
              force: bool = False) -> dict:
    """Move `shot_id` just before (after) `anchor` in the pass's cut. Locked
    when the moved shot is locked (unless `force`); KeyError for a shot not
    in the cut."""
    if anchor == shot_id:
        raise CutError("move a shot before or after another shot, not itself")
    entries = cut_entries(root, pass_)
    e = entries.pop(_find(entries, shot_id, pass_))
    if e.locked and not force:
        raise Locked(f"{shot_id} is locked in the {pass_} cut: unlock it first")
    j = _find(entries, anchor, pass_)
    entries.insert(j + 1 if after else j, e)
    return write_cut(root, pass_, _entries_json(entries, pass_))


def reorder_cut(root: str, pass_: str, shots: list[str]) -> dict:
    """The listed shots first, in that order; every other entry after them in
    its current order."""
    entries = cut_entries(root, pass_)
    if len(set(shots)) != len(shots):
        raise CutError("a shot is listed twice")
    head = [entries[_find(entries, s, pass_)] for s in shots]
    rest = [e for e in entries if e.shot not in set(shots)]
    return write_cut(root, pass_, _entries_json(head + rest, pass_))


def set_cut_entry(root: str, pass_: str, shot_id: str, force: bool = False,
                  **fields) -> dict:
    """Change one entry's trim_in / trim_out / locked / note / audio. Trims
    and audio sources on a locked entry are Locked unless `force`; both are
    checked as PUT /h3pipe/cut checks them."""
    entries = cut_entries(root, pass_)
    e = entries[_find(entries, shot_id, pass_)]
    if ({"trim_in", "trim_out", "audio"} & set(fields)) and e.locked and not force:
        raise Locked(f"{shot_id} is locked in the {pass_} cut: unlock it first")
    for k, v in fields.items():
        setattr(e, k, v)
    out = _entries_json(entries, pass_)
    mine = [x for x in out if x["shot"] == shot_id]       # the same dicts as in `out`
    check_trims(root, pass_, mine)
    check_audio(root, pass_, mine)                        # rewrites `audio` in place
    return write_cut(root, pass_, out)


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


def take_files(root: str, t: T.Take) -> list[str]:
    """Every file of a take in its shot folder: the sidecar and whatever
    shares its stem (mp4, thumbnail, strip, frozen shotlist, H3 wav, kept
    reference sheet), plus any file the sidecar names there."""
    files = set(T.stem_files(t.paths.dir, t.paths.stem))
    sc = t.sidecar or {}
    named = [sc.get(k) for k in ("mp4", "thumb", "strip")]
    named += [r.get("path") for r in (sc.get("refs") or [])
              if isinstance(r, dict) and r.get("role") in ("sheet", "reference")]
    here = os.path.normcase(os.path.abspath(t.paths.dir))
    for n in named:
        if not isinstance(n, str) or not n:
            continue
        if os.path.isabs(n):
            full = n
        elif os.path.basename(n) == n:                    # a file name: beside the sidecar
            full = os.path.join(t.paths.dir, n)
        else:                                            # relative to the episode
            full = os.path.join(root, n)
        full = os.path.abspath(full)
        if os.path.normcase(os.path.dirname(full)) == here and os.path.isfile(full):
            files.add(full)
    return sorted(files)


def discard_take(root: str, pass_: str, shot_id: str, take: int,
                 folder: str | None = None) -> dict:
    """Move a take (its sidecar and every output: take_files) to
    <renders>/_trash/<shot>/, names kept, so nothing lists it and it can be
    put back by hand; its number is not given out again. A cut entry that
    picks it (in either pass's list, when its take comes from `pass_`) goes
    back to the latest usable take. Returns {"shot", "take", "pass", "moved":
    [the new paths, relative to the episode], "cut_changed"}. Raises
    LookupError for no such take, T.StillQueued for a queued one (cancel it
    first)."""
    t = T.get_take(root, pass_, shot_id, take, folder)
    if t is None:
        raise LookupError(f"{shot_id} has no take {take} in {pass_}")
    if t.status == "queued":
        raise T.StillQueued(f"{shot_id} {pass_} t{take:02d} is queued: cancel it first")
    moved = T.discard_files(take_files(root, t), T.trash_dir(root, pass_, shot_id, folder))
    cut = T.load_cut(root)
    changed = False
    for list_pass in T.PASSES:
        for e in cut.get(list_pass) or []:
            if (isinstance(e, dict) and e.get("shot") == shot_id and e.get("take") == take
                    and (e.get("pass") or list_pass) == pass_):
                del e["take"]
                changed = True
    if changed:
        T.save_cut(root, cut)
    return {"shot": shot_id, "take": take, "pass": pass_,
            "moved": [os.path.relpath(m, root).replace(os.sep, "/") for m in moved],
            "cut_changed": changed}


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
    except (NotUsable, Locked) as e:
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


def print_cut(root: str, pass_: str) -> None:
    st = episode_status(root, pass_)
    fps = pass_fps(root, pass_)
    print(f"\n  {st['episode']}  ·  {pass_} cut  ·  {len(st['shots'])} shots  ·  {fps:g} fps")
    for s in st["shots"]:
        c = s["cut"]
        take = f"{'t%02d' % c['take'] if c['take'] else '--':4}"
        trim = f"{c['trim_in']}/{c['trim_out']}" if c["trim_in"] or c["trim_out"] else "-"
        flags = [f for f, on in (("locked", c["locked"]), ("picked", c["picked"]),
                                 (f"placeholder({c['pass']})", c["placeholder"]),
                                 (f"audio: {c['audio_why']}", bool(c["audio_why"])),
                                 ("OUT OF ORDER", c["out_of_order"]),
                                 ("orphan", s["orphan"])) if on]
        print(f"  {c['order']:3}  {s['shot']:10} {take}  trim {trim:9}"
              + (f"  frames {c['frames']:<4}" if c["frames"] else "  frames --  ")
              + (f"  [{', '.join(flags)}]" if flags else "")
              + (f"  {c['note']}" if c["note"] else ""))
    print()


def audio_from_args(ap, words: list[str], pass_: str, at: float | None,
                    start: float | None, gain: float | None) -> dict | None:
    """`--audio SH take SHOT:TAKE[:PASS] | file PATH | none | own` (plus --at,
    --from and --gain) as an audio source, or None for `own`. Usage errors go
    through `ap` (exit 2)."""
    if len(words) < 2:
        ap.error("--audio takes a shot and then take SHOT:TAKE[:PASS], file PATH, "
                 "none or own")
    kind = words[1]
    knobs = {"offset": at, "start": start, "gain": gain}
    knobs = {k: v for k, v in knobs.items() if v is not None}
    if kind == "own":
        if len(words) > 2 or knobs:
            ap.error("--audio SH own clears the source: it takes nothing else")
        return None
    if kind == "none":
        if len(words) > 2:
            ap.error("--audio SH none takes nothing else")
        return {"source": "none"}
    if len(words) != 3:
        ap.error(f"--audio SH {kind} takes exactly one "
                 + ("SHOT:TAKE[:PASS]" if kind == "take" else "path"))
    if kind == "file":
        return dict(knobs, source="file", path=words[2])
    if kind != "take":
        ap.error(f"--audio: the source is take, file, none or own, not {kind!r}")
    bits = words[2].split(":")
    if len(bits) not in (2, 3) or not bits[0]:
        ap.error(f"--audio SH take: give SHOT:TAKE[:PASS], not {words[2]!r}")
    try:
        n = int(bits[1].lstrip("tT"))
    except ValueError:
        ap.error(f"--audio SH take: the take is a number, not {bits[1]!r}")
    if len(bits) == 3 and bits[2] not in T.PASSES:
        ap.error(f"--audio SH take: the pass is final or proxy, not {bits[2]!r}")
    return dict(knobs, source="take", shot=bits[0], take=n,
                **{"pass": bits[2] if len(bits) == 3 else pass_})


def cmd_cut(root: str, argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        prog="h3.py cut",
        description="Show or edit a pass's cut (cut.json): order, trims, locks. Each edit "
                    "keeps a copy of the old cut.json in _history/.")
    ap.add_argument("--proxy", action="store_true", help="the proxy cut")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--show", action="store_true", help="list the cut (the default)")
    g.add_argument("--order", metavar="SH,SH,...",
                   help="these shots first, in this order; the rest follow in their "
                        "current order")
    g.add_argument("--move", metavar="SH", help="move a shot (with --before or --after)")
    g.add_argument("--trim", nargs=3, metavar=("SH", "IN", "OUT"),
                   help="trim frames from the head and tail of a shot's take")
    g.add_argument("--lock", metavar="SH", help="lock a shot (picks, moves and trims refuse)")
    g.add_argument("--unlock", metavar="SH")
    g.add_argument("--reset", choices=CUT_WHAT,
                   help="script order (picks, locks, notes kept), no trims and/or no "
                        "audio sources")
    g.add_argument("--copy-from", nargs="+", metavar=("PASS", "WHAT"),
                   help="copy the other pass's order, trims and/or audio sources "
                        "(default all); picks stay this pass's own")
    g.add_argument("--audio", nargs="+", metavar=("SH", "SOURCE"),
                   help="where a clip's sound comes from: 'SH take SHOT:TAKE[:PASS]', "
                        "'SH file PATH', 'SH none' (silence) or 'SH own' (its own take's "
                        "sound again)")
    ap.add_argument("--at", type=float, metavar="SECONDS", default=None,
                    help="--audio: shift the sound against the picture (positive = later)")
    ap.add_argument("--from", dest="from_s", type=float, metavar="SECONDS", default=None,
                    help="--audio: start this many seconds into the source")
    ap.add_argument("--gain", type=float, metavar="G", default=None,
                    help=f"--audio: a linear multiplier, 0 to {T.GAIN_MAX:g} (1 unchanged)")
    where = ap.add_mutually_exclusive_group()
    where.add_argument("--before", metavar="SH")
    where.add_argument("--after", metavar="SH")
    ap.add_argument("--force", action="store_true",
                    help="move, trim or re-sound a locked shot")
    args = ap.parse_args(argv)
    pass_ = _pass(args)
    J.load_shotlist(root, pass_)                     # no build: say so
    if (args.before or args.after) and not args.move:
        ap.error("--before / --after go with --move")
    try:
        if args.order:
            reorder_cut(root, pass_, [s.strip() for s in args.order.split(",") if s.strip()])
        elif args.move:
            if not (args.before or args.after):
                ap.error("--move needs --before SH or --after SH")
            move_shot(root, pass_, args.move, args.after or args.before, bool(args.after),
                      force=args.force)
        elif args.trim:
            sh, a, b = args.trim
            try:
                ti, to = int(a), int(b)
            except ValueError:
                raise CutError(f"trims are whole numbers of frames, not {a!r} {b!r}")
            set_cut_entry(root, pass_, sh, force=args.force, trim_in=ti, trim_out=to)
        elif args.audio:
            spec = audio_from_args(ap, args.audio, pass_, args.at, args.from_s, args.gain)
            set_cut_entry(root, pass_, args.audio[0], force=args.force, audio=spec)
        elif args.lock or args.unlock:
            set_cut_entry(root, pass_, args.lock or args.unlock, locked=bool(args.lock))
        elif args.reset:
            reset_cut(root, pass_, args.reset)
        elif args.copy_from:
            if len(args.copy_from) > 2:
                ap.error("--copy-from takes a pass and, optionally, order|trims|all")
            src = args.copy_from[0]
            if src not in T.PASSES:
                ap.error(f"--copy-from: the pass is final or proxy, not {src!r}")
            copy_cut(root, src, pass_, args.copy_from[1] if len(args.copy_from) > 1 else "all")
    except (CutError, Locked) as e:
        print(f"  !! {e}")
        return 1
    except KeyError as e:
        print(f"  !! {e.args[0]}")
        return 1
    print_cut(root, pass_)
    return 0


def cmd_discard(root: str, argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        prog="h3.py discard",
        description="Move a take to renders[_proxy]/_trash/<shot>/ (nothing is deleted); "
                    "a cut entry that picked it goes back to the latest usable take.")
    ap.add_argument("shot")
    ap.add_argument("take", help="take number (3 or t03)")
    ap.add_argument("--proxy", action="store_true", help="a proxy take")
    args = ap.parse_args(argv)
    pass_ = _pass(args)
    try:
        take = int(args.take.lstrip("tT"))
    except ValueError:
        print(f"  !! take must be a number, not {args.take!r}")
        return 2
    try:
        res = discard_take(root, pass_, args.shot, take)
    except (LookupError, T.StillQueued) as e:
        print(f"  !! {e}")
        return 1
    where = os.path.dirname(res["moved"][0]) if res["moved"] else "the trash"
    print(f"  {args.shot} {pass_} t{take:02d}: moved {len(res['moved'])} file(s) to {where}"
          + ("; the cut no longer picks it (latest usable take)" if res["cut_changed"] else ""))
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
                    refs = R.references_text(job.references)
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
            "keyframe": cmd_keyframe, "discard": cmd_discard, "cut": cmd_cut}
