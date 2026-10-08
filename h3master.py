#!/usr/bin/env python3
"""
h3master.py — an episode's master, in one action.

    python h3.py master <episode> [<episode> ...] [--check] [--wait] [--conform]
                        [--allow-gaps] [--prores] [--proxy] [--post [recipe|present]]
    python h3.py master <episode> --prores-only    # the .mov from the master already made

Every shot of the pass's cut (final by default) is upscaled by the series
config's recipe (`upscale.master`: each take through its own target's section,
its shot's own recipe over it), then the cut is assembled from the upscales at
the recipe's size and quality into `<episode>/master/`:
`<ep>_master_<WxH>.mp4` (and `.mov`, ProRes 422 HQ, with --prores), with
`<ep>_master.json` / `.md` saying what each shot is.

What it does with each shot of the cut (`plan_master`):

- `upscale`: no upscale, one made from an older take, or one that failed:
  queued by the recipe.
- `ok`: a fresh upscale made with the recipe's settings.
- `kept`: a fresh upscale marked Keep, or made with other settings (a
  different recipe, or before upscales recorded theirs). Left alone;
  `--conform` redoes the unmarked ones. Never a Keep.
- `queued`: an upscale already on its way.
- `gap`: nothing it can use: no usable pick (or the cut plays the other pass's
  take), the recipe covers no section for its target, or a kept upscale at
  another size than the master's.

With --post (the dialog's Post-process), each shot is also finished by the
series config's `post.master` (h3post: enhance, motion blur; its shot's own in
overrides.json's "post") and the master is assembled from the posts:

- `post`: its upscale is good but it has no post, one made from an older
  upscale, or one that failed: queued by the post recipe.
- an `upscale` row's post is queued right behind its upscale, so one run takes
  a shot all the way; `ok` / `kept` / `queued` then speak for both.
- a post made with other settings is `kept` like an upscale (--conform redoes it).

With --post present (the dialog's Post-process: Where present) nothing is
post-processed: each shot plays its fresh post where it has one (made from the
take menu or `h3.py post`, for the shots that need it) and its upscale
otherwise, and no post recipe is needed.

Assembling takes a while (every clip re-encoded at the master quality, then the
titles), so it says where it is as it goes, and only one run assembles an
episode's master at a time: <episode>/master/.assembling.json is held while one
does, and a second (another `h3.py master`, or the editor's Master) is refused.

The cut is never changed: `master` works from the picks as they are. Without
--wait, a run queues what needs it and a later run (or the editor) assembles
once they've landed. The assembly is strict: every clip from an upscale at the
master size, no gaps, unless --allow-gaps (a gap's clip is its take scaled up,
as the review cut does it, and the report says so).

Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from dataclasses import dataclass, field

import h3jobs as J
import h3takes as T
import h3upscale as U

STATUSES = ("upscale", "post", "ok", "kept", "queued", "gap")
# what Master does with posts: none, the fresh ones there are, or every shot by the recipe
POST_MODES = ("off", "present", "recipe")


def post_mode(post) -> str:
    """--post's value as a POST_MODES entry: True is the recipe, False none."""
    if post is True:
        return "recipe"
    if post in (False, None, ""):
        return "off"
    if post not in POST_MODES:
        raise MasterError(f"post {post!r}: it's {', '.join(POST_MODES)}")
    return post


class MasterError(ValueError):
    pass


class MasterBusy(MasterError):
    """A master of the episode is already being assembled (`lock`: who, since when)."""

    def __init__(self, message: str, lock: dict):
        super().__init__(message)
        self.lock = lock


@dataclass
class Row:
    shot: str
    take: T.Take | None
    status: str                          # STATUSES
    why: str = ""
    job: U.UpscaleJob | None = None      # an `upscale` row's planned job
    recipe: str = ""                     # the shot's recipe, in words
    post_job: object = None              # --post: its planned post (h3post.PostJob)
    post_kw: dict | None = None          # --post: its post recipe (h3post.recipe_for)

    def view(self) -> dict:
        rec = T.upscale_of(self.take) if self.take else None
        po = T.post_of(self.take, rec) if self.take and self.post_kw is not None else None
        return {"shot": self.shot, "take": self.take.take if self.take else None,
                "target": ((self.take.sidecar or {}).get("target") if self.take else None),
                "status": self.status, "why": self.why, "recipe": self.recipe,
                "upscale": ({"width": rec.get("width"), "height": rec.get("height"),
                             "status": rec.get("status"), "keep": bool(rec.get("keep"))}
                            if rec else None),
                "post": ({"status": po.get("status"), "fresh": bool(po.get("fresh")),
                          "recipe": po.get("recipe")} if po else None)}


@dataclass
class Plan:
    root: str
    pass_: str
    recipe: dict
    size: tuple
    rows: list = field(default_factory=list)
    post: dict | None = None             # --post: the series config's post.master
    post_mode: str = "off"               # POST_MODES

    def of(self, status: str) -> list:
        return [r for r in self.rows if r.status == status]

    @property
    def ready(self) -> bool:
        """Every shot has its upscale: nothing to queue, nothing on its way, no gap."""
        return all(r.status in ("ok", "kept") for r in self.rows)

    def view(self) -> dict:
        return {"pass": self.pass_, "post": self.post_mode != "off", "post_mode": self.post_mode,
                "size": list(self.size), "fit": self.recipe.get("fit") or "crop",
                "quality": self.recipe.get("quality") or "review",
                "counts": {s: len(self.of(s)) for s in STATUSES}, "ready": self.ready,
                "rows": [r.view() for r in self.rows]}


def plan_master(root: str, pass_: str = "final", conform: bool = False,
                post=False) -> Plan:
    """What mastering the pass's cut would do, shot by shot (see the module's
    docstring). `post`: a POST_MODES entry, or True for "recipe". MasterError
    when the series has no recipe, or it names no size, or `post` is "recipe"
    and it has no post recipe."""
    mode = post_mode(post)
    recipe = U.master_recipe(root)
    if not recipe:
        raise MasterError("the series config has no upscale.master recipe (docs/AUTHORING.md, Masters)")
    try:
        size = U.parse_deliver(recipe.get("deliver"))
    except U.UpscaleError as e:
        raise MasterError(f"upscale.master: {e}")
    if not size:
        raise MasterError("upscale.master needs a deliver size (1080p, 1440p, 4k or WxH): "
                          "a master is one size")
    shots = U.shot_recipes(root)
    plan = Plan(root, pass_, recipe, size)
    plan.post_mode = mode
    post_shots: dict = {}
    if mode == "recipe":
        import h3post as P
        plan.post = P.master_recipe(root)
        if not plan.post:
            raise MasterError("the series config has no post.master recipe "
                              "(docs/POST_PROCESSING.md): master without Post-process, or add one")
        post_shots = P.shot_recipes(root)
    for shot, t, why in U.cut_takes(root, None, None, pass_=pass_):
        if t is None:
            plan.rows.append(Row(shot, None, "gap", why))
            continue
        target = (t.sidecar or {}).get("target") or T.DEFAULT_TARGET
        key, section = U.recipe_section(recipe, target)
        if key is None:
            plan.rows.append(Row(shot, t, "gap", f"upscale.master has no section for {target}"))
            continue
        text = U.describe_recipe({**(recipe.get("finish") or {}), **section, **(shots.get(shot) or {})})
        rec = T.upscale_of(t)
        row = Row(shot, t, "upscale", "", recipe=text)
        if rec and rec.get("status") == "queued":
            row.status, row.why = "queued", "its upscale is on its way"
        elif rec and rec.get("status") == "ok" and rec.get("fresh"):
            made = (rec.get("width"), rec.get("height"))
            if U.kept(t, rec):
                row.status, row.why = "kept", "marked Keep"
            else:
                match = U.recipe_status(root, t, recipe, shots, rec)
                if match == "same":
                    row.status = "ok"
                elif not conform:
                    row.status = "kept"
                    row.why = ("made before upscales recorded their settings" if match == "unknown"
                               else "made with other settings than the recipe's")
                else:
                    row.why = "--conform: made with other settings than the recipe's"
            if row.status in ("ok", "kept") and tuple(made) != tuple(size):
                row.status = "gap"
                row.why = (f"its upscale is {made[0]}x{made[1]}, the master {size[0]}x{size[1]}"
                           + (": unkeep it, or redo it" if U.kept(t, rec) else ": --conform redoes it"))
        elif rec and rec.get("status") == "ok":
            row.why = "its upscale was made from an older take"
        elif rec and rec.get("status") == "failed":
            row.why = "its last upscale failed: trying again"
        else:
            row.why = "not upscaled yet"
        if row.status == "upscale":
            try:
                job = U.plan_upscale(root, t, redo=True, **U.recipe_for(root, t, recipe, shots))
            except U.UpscaleError as e:
                row.status, row.why = "gap", str(e)
            else:
                if job.action == "error":
                    row.status, row.why = "gap", job.why
                else:
                    row.job = job
        if mode == "recipe" and row.status != "gap":
            plan_row_post(plan, row, post_shots, conform)
        elif mode == "present" and row.take is not None:
            note_present_post(row)
        plan.rows.append(row)
    if not plan.rows:
        raise MasterError(f"the {pass_} cut is empty")
    return plan


def plan_row_post(plan: Plan, row: Row, post_shots: dict, conform: bool) -> None:
    """--post: what the row's post is, on top of its upscale's status. An
    `upscale` row gets its post planned behind it (at the master's size)."""
    import h3post as P
    t = row.take
    row.post_kw = P.recipe_for(plan.root, row.shot, plan.post, post_shots)
    words = P.describe_recipe(row.post_kw)
    row.recipe = f"{row.recipe}; post: {words or 'none'}"
    quality = plan.recipe.get("quality") or "review"
    if row.status == "upscale":
        job = P.plan_post(plan.root, t, after_upscale=plan.size, quality=quality, **row.post_kw)
        if job.action == "post":
            row.post_job = job
        elif "nothing to do" not in job.why:
            row.status, row.why = "gap", f"post: {job.why}"
        return
    if row.status == "queued":
        return                               # its upscale first; the post is planned after
    rec = T.post_of(t)
    if rec and rec.get("status") == "queued":
        row.status, row.why = "queued", "its post is on its way"
        return
    job = P.plan_post(plan.root, t, quality=quality, **row.post_kw)
    if job.action == "error":
        if "nothing to do" in job.why:
            if rec and rec.get("fresh"):
                row.status = "gap"
                row.why = "it has a post, but its post recipe is none: delete the post"
            return                           # mastered from its upscale
        row.status, row.why = "gap", f"post: {job.why}"
        return
    if job.action == "skip":
        return                               # a fresh post with the recipe's settings
    if rec and rec.get("status") == "ok" and rec.get("fresh") and not conform:
        row.status, row.why = "kept", "its post was made with other settings than the recipe's"
        return
    row.status, row.post_job = "post", job
    row.why = ("--conform: its post was made with other settings" if rec and rec.get("fresh")
               else "its post is from an older upscale" if rec and rec.get("status") == "ok"
               else "its last post failed: trying again" if rec and rec.get("status") == "failed"
               else "not post-processed yet")


def note_present_post(row: Row) -> None:
    """--post present: say which file the shot will play, its post or its upscale."""
    row.post_kw = {}                     # the row's view shows its post
    rec = T.post_of(row.take)
    if rec and rec.get("status") == "ok" and rec.get("fresh"):
        row.recipe = f"{row.recipe}; its post"
    elif rec and rec.get("status") == "ok":
        row.recipe = f"{row.recipe}; its upscale (its post is stale)"
    elif rec and rec.get("status") == "queued":
        row.recipe = f"{row.recipe}; its upscale (a post is on its way: master again after)"
    else:
        row.recipe = f"{row.recipe}; its upscale"


ORDERS = ("cut", "target")


def load_key(job: U.UpscaleJob) -> tuple:
    """What ComfyUI loads to run an upscale: its target's model for a re-sample,
    else the upscale model or SeedVR2 model (whatever the take's target)."""
    if job.method == "latent":
        return ("latent", job.target.id, job.then_method, job.then_model)
    return (job.method, job.seedvr2_model if job.method == "seedvr2" else job.pixel_model)


def in_order(rows: list, order: str = "cut") -> list:
    """The `upscale` rows in the order they're queued. `cut`: the cut's, so what's
    finished is the cut up to a point (stop at the first shot that's off and
    everything before it is good). `target`: grouped by what ComfyUI loads
    (load_key), each group where its first shot is in the cut and in cut order
    within it, so each model loads once: for a batch left to run."""
    if order not in ORDERS:
        raise MasterError(f"order {order!r}: it's cut or target")
    if order == "cut":
        return list(rows)
    first: dict = {}
    for i, r in enumerate(rows):
        first.setdefault(load_key(r.job), i)
    return sorted(rows, key=lambda r: first[load_key(r.job)])


def queue_master(plan: Plan, comfy, comfy_url: str, order: str = "cut") -> tuple[list, list]:
    """Queue the plan's `upscale` rows on ComfyUI, in cut order or grouped by
    what they load (in_order), and with --post the posts: an upscale row's
    right behind its upscale in cut order (after all the upscales when grouped
    by target), then the `post` rows. Returns (queued rows, [(row, error)]).
    MasterError when this ComfyUI can't run them."""
    ups = in_order(plan.of("upscale"), order)
    posts = plan.of("post")
    if not ups and not posts:
        return [], []
    try:
        info = comfy.object_info()
    except Exception as e:
        raise MasterError(f"ComfyUI didn't answer: {e}")
    missing = U.not_ready([r.job for r in ups], info)
    pjobs = [r.post_job for r in ups + posts if r.post_job is not None]
    if pjobs:
        import h3post as P
        missing += [m for m in P.not_ready(pjobs, info) if m not in missing]
    if missing:
        raise MasterError("this ComfyUI can't run these: " + "; ".join(missing))
    bases: dict = {}
    queued, errors = [], []

    def queue_post(r: Row) -> bool:
        import h3post as P
        try:
            P.start(r.post_job)
            P.mark_queued(r.post_job, comfy.queue(P.post_graph(r.post_job)))
            return True
        except Exception as e:
            if os.path.isfile(r.take.paths.post_sidecar):
                P.mark_failed(r.post_job, str(e)[:800])
            errors.append((r, f"its post: {e}"))
            return False

    for r in ups:
        try:
            g = U.graph_of(r.job, bases, comfy_url)
            U.start(r.job)
            U.mark_queued(r.job, comfy.queue(g))
            r.status, r.why = "queued", "queued by this run"
            queued.append(r)
        except Exception as e:
            if os.path.isfile(r.job.take.paths.up_sidecar):
                U.mark_failed(r.job, str(e)[:800])
            errors.append((r, str(e)))
            continue
        if r.post_job is not None and order == "cut" and queue_post(r):
            r.why = "upscale and post queued by this run"
    for r in ([u for u in ups if u.status == "queued"] if order != "cut" else []):
        if r.post_job is not None and queue_post(r):
            r.why = "upscale and post queued by this run"
    for r in posts:
        if queue_post(r):
            r.status, r.why = "queued", "post queued by this run"
            queued.append(r)
    return queued, errors


def wait_master(plan: Plan, comfy, timeout: int = 3600) -> list:
    """Follow every queued row's upscale, and with --post its post, to its end
    (ComfyUI's history by its prompt id). Returns the rows whose upscale or
    post didn't finish ok."""
    stages = [("upscale", T.upscale_of)]
    if plan.post is not None:
        stages.append(("post", T.post_of))
    bad = []
    for r in plan.of("queued"):
        for name, of in stages:
            rec = of(r.take) or {}
            if rec.get("status") != "queued":
                continue
            pid = rec.get("comfy_prompt_id")
            t0 = time.time()
            try:
                if pid:
                    comfy.wait(pid, timeout)
                while (of(r.take) or {}).get("status") == "queued" and time.time() - t0 < 60:
                    time.sleep(0.5)                     # the saver's record lands just after
            except Exception as e:
                bad.append((r, f"its {name}: {e}"))
                break
            rec = of(r.take) or {}
            if rec.get("status") != "ok":
                bad.append((r, rec.get("save_notes") or f"its {name} is {rec.get('status')}"))
                break
    return bad


def fwd(root: str, path: str) -> str:
    """`path` relative to the episode, with forward slashes (the report's paths)."""
    return os.path.relpath(path, root).replace(os.sep, "/")


def master_dir(root: str) -> str:
    return os.path.join(root, "master")


def master_name(root: str, size: tuple) -> str:
    return f"{os.path.basename(os.path.normpath(root))}_master_{size[0]}x{size[1]}"


# While a master is being assembled, <episode>/master/.assembling.json says who
# by and since when, so a second run (the editor's button pressed again, or
# `h3.py master` beside the editor) is refused instead of redoing the same
# half hour of ffmpeg over the same files. A lock whose process is gone is stale.
LOCK_FILE = ".assembling.json"
LOCK_FOREIGN_HOURS = 24      # another machine's lock can't be checked: this old, it's stale


def pid_alive(pid: int) -> bool:
    """Whether process `pid` is still running (stdlib only)."""
    if pid == os.getpid():
        return True
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        h = k.OpenProcess(0x1000, False, pid)        # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return ctypes.get_last_error() == 5      # access denied: it's there
        code = ctypes.c_ulong()
        ok = k.GetExitCodeProcess(h, ctypes.byref(code))
        k.CloseHandle(h)
        return bool(ok) and code.value == 259        # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def lock_path(root: str) -> str:
    return os.path.join(master_dir(root), LOCK_FILE)


def assembling(root: str) -> dict | None:
    """The lock of a master of this episode being assembled right now
    ({"pid", "host", "by", "started"}), or None (no lock, or a stale one)."""
    import socket
    try:
        with open(lock_path(root), encoding="utf-8") as fh:
            lock = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(lock, dict):
        return None
    if lock.get("host") and lock["host"] != socket.gethostname():
        try:
            age = time.time() - os.path.getmtime(lock_path(root))
        except OSError:
            return None
        return lock if age < LOCK_FOREIGN_HOURS * 3600 else None
    pid = lock.get("pid")
    return lock if isinstance(pid, int) and pid_alive(pid) else None


def busy_message(lock: dict) -> str:
    started = str(lock.get("started") or "")
    at = started[11:16] if len(started) >= 16 else started or "a while ago"
    return (f"a master of this episode is already being assembled by {lock.get('by') or 'another run'} "
            f"(since {at}); wait for it to finish")


def take_lock(root: str, by: str) -> str:
    """Claim the episode's master lock for this process; MasterBusy when a live
    run holds it. A stale one is taken over. Returns the lock's path."""
    import socket
    os.makedirs(master_dir(root), exist_ok=True)
    path = lock_path(root)
    body = json.dumps({"pid": os.getpid(), "host": socket.gethostname(), "by": by,
                       "started": T.now()})
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            held = assembling(root)
            if held:
                raise MasterBusy(busy_message(held), held)
            try:
                os.remove(path)                      # stale: its run is gone
            except OSError:
                pass
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(body)
        return path
    held = assembling(root) or {}
    raise MasterBusy(busy_message(held), held)


def release_lock(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def existing_master(root: str) -> str | None:
    """The master already made for the recipe's size (<episode>/master/
    <ep>_master_<WxH>.mp4), else None (no recipe, no size, or not made yet)."""
    recipe = U.master_recipe(root)
    try:
        size = U.parse_deliver((recipe or {}).get("deliver"))
    except U.UpscaleError:
        return None
    if not size:
        return None
    mp4 = os.path.join(master_dir(root), master_name(root, size) + ".mp4")
    return mp4 if os.path.isfile(mp4) else None


def prores_only(root: str, by: str = "h3.py master") -> dict:
    """The ProRes 422 HQ .mov of the master already made, beside it (what
    --prores does at the end of an assembly, without assembling again), and the
    report's `prores` set. MasterError without a master; MasterBusy while one
    is being assembled. {"output", "mov"} (absolute paths)."""
    import h3assemble
    mp4 = existing_master(root)
    if mp4 is None:
        raise MasterError("there is no master of this episode at the recipe's size yet: "
                          "master it first (ProRes then comes from that master)")
    lock = take_lock(root, by)
    try:
        mov = mp4[:-4] + ".mov"
        why = h3assemble.prores_from(mp4, mov)
        if why:
            raise MasterError(f"the ProRes .mov couldn't be made: {why}")
    finally:
        release_lock(lock)
    stem = os.path.join(master_dir(root), os.path.basename(os.path.normpath(root)) + "_master")
    rep = T.read_json(stem + ".json")
    if isinstance(rep, dict):
        rep["prores"] = fwd(root, mov)
        T.write_json(stem + ".json", rep)
    md = stem + ".md"
    if os.path.isfile(md):
        with open(md, encoding="utf-8") as fh:
            text = fh.read()
        line = f"- `{fwd(root, mov)}` (ProRes 422 HQ)"
        if line not in text:
            mline = f"- `{fwd(root, mp4)}`"
            text = text.replace(mline + "\n", mline + "\n" + line + "\n", 1)
            with open(md, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
    return {"output": mp4, "mov": mov}


def assemble_master(plan: Plan, allow_gaps: bool = False, prores: bool = False,
                    timeout: int = 3600, progress=None, by: str = "h3.py master") -> dict:
    """The cut from its upscales at the master size into <episode>/master/, and
    the report beside it. Refused while anything is still to upscale or on its
    way, and with gaps unless `allow_gaps`; MasterBusy while another run is
    assembling this episode's master. `progress(event)` hears each step:
    {"step": "assemble" | "titles", "stage", "done", "total", "text"} (the
    stages are h3assemble's and h3publish's progress lines).
    {"ok", "output", "mov", "report", "error"}."""
    check_assemble(plan, allow_gaps)
    lock = take_lock(plan.root, by)
    try:
        return _assemble(plan, plan.of("gap"), prores, timeout, progress)
    finally:
        release_lock(lock)


def check_assemble(plan: Plan, allow_gaps: bool = False) -> None:
    """MasterError when the plan can't be assembled yet: shots still to upscale
    or on their way, or gaps without `allow_gaps`."""
    waiting = plan.of("upscale") + plan.of("post") + plan.of("queued")
    if waiting:
        what = "upscale or post-process" if plan.post is not None else "upscale"
        raise MasterError(f"{len(waiting)} shot(s) still to {what}: "
                          + ", ".join(r.shot for r in waiting[:10]))
    gaps = plan.of("gap")
    if gaps and not allow_gaps:
        raise MasterError(f"{len(gaps)} gap(s): " + "; ".join(f"{r.shot}: {r.why}" for r in gaps[:5])
                          + " (--allow-gaps masters them scaled up from their takes)")


def _tell(progress, step: str):
    """A run_tool progress listener that hands the tool's events on as `step`'s."""
    def hear(tool: str, ev: dict) -> None:
        if progress is not None:
            progress({"step": step, "stage": str(ev.get("stage") or ""),
                      "done": ev.get("done"), "total": ev.get("total"),
                      "text": str(ev.get("text") or "")})
    return hear


def _assemble(plan: Plan, gaps: list, prores: bool, timeout: int, progress) -> dict:
    import h3edit
    name = master_name(plan.root, plan.size)
    sub = "renders_proxy" if plan.pass_ == "proxy" else "renders"
    args = ["-o", plan.root, "--upscaled", *(["--post"] if plan.post_mode != "off" else []),
            "--size", f"{plan.size[0]}x{plan.size[1]}",
            "--quality", "master" if (plan.recipe.get("quality") == "master") else "review",
            "--name", name + ".mp4", "--progress"]
    if plan.pass_ == "proxy":
        args += ["--shotlist", "shotlist/shotlist_proxy.json", "--subfolder", sub]
    if gaps:
        args.append("--partial")
    # the .mov is made from the finished master: by h3assemble when nothing is
    # laid around the cut, else once, after the titles (add_titles) -- not
    # encoded from the untitled cut only to be made again
    retitle = prores and _has_titles(plan.root)
    if prores and not retitle:
        args += ["--intermediate", "prores"]
    rc, out, err = h3edit.run_tool("h3assemble.py", args, plan.root, timeout,
                                   progress=_tell(progress, "assemble"))
    made = os.path.join(plan.root, sub, name + ".mp4")
    if rc != 0 or not os.path.isfile(made):
        return {"ok": False, "output": None, "mov": None, "report": out,
                "error": err.strip() or out.strip()[-400:] or "h3assemble wrote no cut"}
    dst = master_dir(plan.root)
    os.makedirs(dst, exist_ok=True)
    old_mov = os.path.join(dst, name + ".mov")
    if (not prores or retitle) and os.path.isfile(old_mov):
        os.remove(old_mov)                  # an earlier master's: not this one any more
    moved = {}
    for ext in (".mp4", "_shots.txt", ".mov"):
        src = os.path.join(plan.root, sub, name + ext)
        if os.path.isfile(src):
            shutil.move(src, os.path.join(dst, name + ext))
            moved[ext] = os.path.join(dst, name + ext)
    mp4, mov = moved[".mp4"], moved.get(".mov")
    if retitle:
        mov = old_mov                       # add_titles makes it from the titled master
    titled, why = add_titles(plan, mp4, mov, timeout, progress)
    if mov and not os.path.isfile(mov):
        mov = None                          # the titles failed before it was made
    out += titled.get("report", "")
    rep = write_report(plan, mp4, mov, titled)
    if why:
        return {"ok": False, "output": mp4, "mov": mov, "report": out, "report_md": rep,
                "titles": None, "error": f"the master is assembled, but its titles failed: {why}"}
    return {"ok": True, "output": mp4, "mov": mov, "report": out,
            "report_md": rep, "titles": titled.get("titles"), "error": ""}


def _has_titles(root: str) -> bool:
    """An intro or an outro will be laid around the master. A series config
    h3publish can't read counts as none here; add_titles reports it."""
    import h3publish
    try:
        return any(h3publish.titles(root))
    except Exception:
        return False


def add_titles(plan: Plan, mp4: str, mov: str | None, timeout: int = 3600,
               progress=None) -> tuple[dict, str]:
    """The series intro and outro around the master, in place (h3publish), when
    the episode has them; the .mov made again from the titled master. Returns
    ({"titles": {"intro", "outro", "picture", "why"} or None, "report"}, why it
    failed or ""). `picture` is how h3publish laid the cut in: "copied" (only
    the title clips encoded), "re-encoded" (the whole cut, `why` says why) or
    None when it didn't say."""
    import h3assemble
    import h3edit
    import h3publish
    try:
        intro, outro = h3publish.titles(plan.root)
    except h3publish.PublishError as e:
        return {}, str(e)
    if not intro and not outro:
        return {"titles": None, "report": "\n  no _titles/INTRO.mp4 or OUTRO.mp4: "
                                          "the master has no intro or outro\n"}, ""
    quality = "master" if plan.recipe.get("quality") == "master" else "review"
    tell = _tell(progress, "titles")
    result: dict = {}

    def hear(tool: str, ev: dict) -> None:
        if ev.get("stage") == "done":
            result.update(picture=ev.get("picture"), why=ev.get("why") or "")
        else:
            tell(tool, ev)

    rc, out, err = h3edit.run_tool("h3publish.py", [plan.root, "--input", mp4, "--out", mp4,
                                                    "--quality", quality, "--progress"],
                                   plan.root, timeout, progress=hear)
    if rc != 0:
        why = next((ln.strip()[3:] for ln in out.splitlines() if ln.strip().startswith("!! ")),
                   err.strip()[-300:] or "h3publish failed")
        return {"report": "\n" + out}, why
    if mov:
        tell("h3master", {"stage": "prores", "text": os.path.basename(mov)})
        why = h3assemble.prores_from(mp4, mov)
        if why:
            return {"report": "\n" + out}, f"the ProRes .mov couldn't be made again: {why}"
    return {"titles": {"intro": fwd(plan.root, intro) if intro else None,
                       "outro": fwd(plan.root, outro) if outro else None,
                       "picture": result.get("picture"), "why": result.get("why", "")},
            "report": "\n" + out}, ""


def picture_line(titles: dict) -> str:
    """How the titles step laid the cut in, in words."""
    if titles.get("picture") == "copied":
        return "Picture: the cut copied as assembled; only the title clips were encoded."
    why = titles.get("why") or "h3publish didn't say why"
    return f"Picture: the whole cut re-encoded when the titles went on, because {why}."


def write_report(plan: Plan, mp4: str | None, mov: str | None, titled: dict | None = None) -> str:
    """<ep>_master.json and .md beside the master: each shot, its take, target,
    recipe and upscale, and anything kept or missing. Returns the .md's path."""
    root, dst = plan.root, master_dir(plan.root)
    os.makedirs(dst, exist_ok=True)
    stem = os.path.join(dst, os.path.basename(os.path.normpath(root)) + "_master")
    data = {"episode": os.path.basename(os.path.normpath(root)), "made": T.now(),
            "output": fwd(root, mp4) if mp4 else None, "prores": fwd(root, mov) if mov else None,
            "titles": (titled or {}).get("titles"), **plan.view()}
    for row, r in zip(plan.rows, data["rows"]):
        rec = T.upscale_of(row.take) if row.take else None
        if rec:
            r["upscale"].update(recipe=rec.get("recipe"), quality=rec.get("quality"),
                                finished=rec.get("finished"))
        if r.get("post"):
            r["post"]["finished"] = (T.post_of(row.take, rec) or {}).get("finished")
    T.write_json(stem + ".json", data)
    w, h = plan.size
    lines = [f"# {data['episode']} master", "",
             f"{w}x{h} ({data['fit']}), {data['quality']} quality, {plan.pass_} pass, "
             f"made {data['made']}"
             + (", every shot post-processed (post.master)" if plan.post_mode == "recipe"
                else ", each shot's post where it has one, else its upscale"
                if plan.post_mode == "present" else ""), ""]
    if mp4:
        lines += [f"- `{fwd(root, mp4)}`"] + ([f"- `{fwd(root, mov)}` (ProRes 422 HQ)"] if mov else []) + [""]
        t = data.get("titles")
        lines += [("Titles: " + ", ".join(f"{k} `{t[k]}`" for k in ("intro", "outro") if t.get(k))
                   + ", the episode's title drawn on") if t else "No intro or outro.", ""]
        if t and t.get("picture"):
            lines += [picture_line(t), ""]
    lines += ["| shot | take | target | status | recipe | note |", "|---|---|---|---|---|---|"]
    for r in data["rows"]:
        lines.append(f"| {r['shot']} | {r['take'] or ''} | {r['target'] or ''} | {r['status']} | "
                     f"{r['recipe']} | {r['why']} |")
    with open(stem + ".md", "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
    return stem + ".md"


def print_plan(plan: Plan) -> None:
    w, h = plan.size
    print(f"\n  {os.path.basename(os.path.normpath(plan.root))}: master {w}x{h} "
          f"({plan.recipe.get('fit') or 'crop'}, {plan.recipe.get('quality') or 'review'} quality), "
          f"{plan.pass_} cut, {len(plan.rows)} shots"
          + (", post-processed" if plan.post_mode == "recipe"
             else ", posts where present" if plan.post_mode == "present" else ""))
    mark = {"upscale": "..", "post": "..", "ok": "ok", "kept": "= ", "queued": "~~", "gap": "!!"}
    for r in plan.rows:
        tk = f"t{r.take.take:02d}" if r.take else "   "
        print(f"  {mark[r.status]} {r.shot:8} {tk}  {r.status:8} {r.recipe}"
              + (f"  ({r.why})" if r.why else ""))
    counts = ", ".join(f"{len(plan.of(s))} {s}" for s in STATUSES if plan.of(s))
    print(f"  {counts}")


def master_episode(root: str, args, comfy) -> int:
    """One episode of `h3.py master`: plan, queue, (wait), assemble."""
    try:
        plan = plan_master(root, args.pass_, args.conform, args.post)
    except MasterError as e:
        print(f"\n  !! {os.path.basename(root)}: {e}")
        return 1
    print_plan(plan)
    if args.check:
        return 0
    gaps = plan.of("gap")
    if gaps and not args.allow_gaps:
        print(f"  !! {len(gaps)} gap(s): not mastered (fix them, or --allow-gaps)")
        return 1
    # rounds: what's queued is waited for and the plan made again, so a post
    # that could only be planned once its upscale landed is queued next round
    for _ in range(3):
        if plan.of("upscale") or plan.of("post"):
            try:
                queued, errors = queue_master(plan, comfy, args.comfy, args.order)
            except MasterError as e:
                print(f"  !! {e}")
                return 1
            what = "shot(s) to upscale and post-process" if plan.post is not None else "upscale(s)"
            print(f"  queued {len(queued)} {what}"
                  + (" grouped by target: " + ", ".join(r.shot for r in queued)
                     if args.order == "target" and len(queued) > 1 else ""))
            for r, e in errors:
                print(f"  !! {r.shot}: {e}")
            if errors:
                return 1
        if not plan.of("queued"):
            break
        if not args.wait:
            print("  run it again once they've finished (or --wait) to assemble the master")
            return 0
        print(f"  waiting for {len(plan.of('queued'))} shot(s)...", flush=True)
        bad = wait_master(plan, comfy, args.timeout)
        for r, e in bad:
            print(f"  !! {r.shot}: {e}")
        if bad:
            return 1
        plan = plan_master(root, args.pass_, args.conform, args.post)
    printer = Printer()
    try:
        res = assemble_master(plan, args.allow_gaps, args.prores, args.timeout,
                              progress=printer)
    except MasterError as e:
        printer.finish()
        print(f"  !! {e}")
        return 1
    printer.finish()
    if not res["ok"]:
        print(f"  !! assembling failed: {res['error']}")
        return 1
    t = res.get("titles")
    print(f"  -> {res['output']}" + (f"\n  -> {res['mov']}" if res["mov"] else "")
          + ("\n  with the intro and outro" if t and t["intro"] and t["outro"]
             else f"\n  with the {'intro' if t['intro'] else 'outro'} only" if t
             else "\n  no intro or outro (no _titles/INTRO.mp4 or OUTRO.mp4)")
          + (f"\n  {picture_line(t)}" if t and t.get("picture") else "")
          + f"\n  report: {res['report_md']}")
    return 0


STAGE_WORDS = {"probe": "reading the clips", "clips": "writing the clips", "join": "joining",
               "prores": "the ProRes .mov", "verify": "checking", "titles": "encoding the",
               "reencode": "re-encoding the whole cut"}


def stage_words(ev: dict) -> str:
    """A progress event in words: "writing the clips 212/323 sh2590"."""
    words = STAGE_WORDS.get(ev.get("stage"), ev.get("stage") or "")
    if ev.get("stage") == "titles":
        return f"{words} {ev.get('text') or 'titles'}"
    n, total = ev.get("done"), ev.get("total")
    count = f" {n}/{total}" if isinstance(n, int) and isinstance(total, int) and total else \
        (f" {n}" if isinstance(n, int) and n else "")
    text = ev.get("text") if ev.get("stage") in ("probe", "clips") else ""
    return f"{words}{count}" + (f" {text}" if text else "")


class Printer:
    """`h3.py master`'s progress: one line per stage, the counts written over
    in place on a terminal."""

    def __init__(self, out=None):
        self.out = out or sys.stdout
        self.tty = hasattr(self.out, "isatty") and self.out.isatty()
        self.last = None
        self.t0 = time.time()

    def __call__(self, ev: dict) -> None:
        key = (ev.get("step"), ev.get("stage"))
        line = f"  [{int(time.time() - self.t0) // 60:>3}m] {ev.get('step')}: {stage_words(ev)}"
        if self.tty:
            end = "" if key == self.last or self.last is None else "\n"
            self.out.write(end + "\r" + line.ljust(78))
        elif key != self.last:
            self.out.write(line + "\n")
        self.out.flush()
        self.last = key

    def finish(self) -> None:
        """End the line a terminal's counts were being written over."""
        if self.tty and self.last is not None:
            self.out.write("\n")
            self.out.flush()
        self.last = None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="h3.py master", description=__doc__.split("\n\n")[0])
    ap.add_argument("episodes", nargs="+", help="episode folders (a show folder: every episode in it)")
    ap.add_argument("--proxy", dest="pass_", action="store_const", const="proxy", default="final",
                    help="the proxy cut (a check of the flow; masters are the final's)")
    ap.add_argument("--check", action="store_true", help="the plan only: queue and write nothing")
    ap.add_argument("--wait", action="store_true",
                    help="follow the queued upscales and assemble when they're done")
    ap.add_argument("--conform", action="store_true",
                    help="also redo upscales made with other settings than the recipe's (never a Keep)")
    ap.add_argument("--allow-gaps", action="store_true",
                    help="master a cut with gaps: their clips scaled up from their takes")
    ap.add_argument("--prores", action="store_true", help="also a ProRes 422 HQ .mov")
    ap.add_argument("--prores-only", action="store_true",
                    help="only the ProRes .mov, from the master already made (nothing "
                         "upscaled, posted or assembled)")
    ap.add_argument("--post", nargs="?", const="recipe", default="off",
                    choices=("recipe", "present"),
                    help="(after the episodes) recipe, or --post alone: each shot also "
                         "post-processed by the series config's post.master (h3post: enhance, "
                         "motion blur), the master made from the posts; present: each shot's "
                         "fresh post where it has one, its upscale otherwise, nothing queued")
    ap.add_argument("--order", choices=ORDERS, default="cut",
                    help="how the upscales are queued: cut (the default: in the cut's order, so "
                         "you can stop at the first bad one and keep everything before it) or "
                         "target (grouped by the model each loads, so each loads once: faster "
                         "for a batch left to run)")
    ap.add_argument("--comfy", default="http://127.0.0.1:8188")
    ap.add_argument("--timeout", type=int, default=3600)
    args = ap.parse_args(argv)
    roots = episode_roots(args.episodes)
    if not roots:
        print("  !! no episodes found")
        return 2
    comfy = J.Comfy(args.comfy)
    results = {}
    for root in roots:
        if args.prores_only:
            try:
                res = prores_only(root)
                print(f"  -> {res['mov']}")
                results[root] = 0
            except MasterError as e:
                print(f"  !! {os.path.basename(root)}: {e}")
                results[root] = 1
            continue
        results[root] = master_episode(root, args, comfy)
    if len(roots) > 1:
        print("\n  " + ", ".join(f"{os.path.basename(r)} {'ok' if rc == 0 else 'not done'}"
                                 for r, rc in results.items()))
    return 0 if all(rc == 0 for rc in results.values()) else 1


def episode_roots(paths: list) -> list:
    """Episode folders: each path that has a cut or a shotlist, or else the
    folders inside it that do (a show folder), sorted."""
    out = []
    for p in paths:
        p = os.path.abspath(p)
        if is_episode(p):
            out.append(p)
        elif os.path.isdir(p):
            out += sorted(os.path.join(p, d) for d in os.listdir(p)
                          if is_episode(os.path.join(p, d)))
    return list(dict.fromkeys(out))


def is_episode(p: str) -> bool:
    return os.path.isdir(os.path.join(p, "shotlist")) or os.path.isfile(os.path.join(p, T.CUT_FILE))


if __name__ == "__main__":
    sys.exit(main())
